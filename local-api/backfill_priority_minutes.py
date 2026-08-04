"""为截图优先股票池按统一窗口补齐 RQData 30 分钟行情，可重复执行和断点续传。"""

import asyncio
import os
from datetime import date
from pathlib import Path

import asyncpg
import rqdatac as rq

from minute_data import sync_exact_range, vendor_code


TARGET_DAYS = int(os.getenv("PRIORITY_TARGET_DAYS", "548"))
RESERVE_BYTES = int(os.getenv("RQDATA_QUOTA_RESERVE_BYTES", str(3 * 1024 * 1024)))
SLICE_DAYS = int(os.getenv("PRIORITY_SLICE_TRADING_DAYS", "20"))
POOL_FILE = Path(__file__).with_name("priority_stocks_20260805.txt")


def codes() -> list[str]:
    values = {
        line.strip() for line in POOL_FILE.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if len(values) != 253:
        raise RuntimeError(f"合并优先池应为253只，实际{len(values)}只")
    return sorted(values)


def rq_window() -> list[date]:
    rq.init(os.environ["RQDATA_USERNAME"], os.environ["RQDATA_PASSWORD"])
    end = date.today()
    dates = list(rq.get_trading_dates("2020-01-01", end))
    if len(dates) < TARGET_DAYS:
        raise RuntimeError("RQData交易日历不足")
    return dates[-TARGET_DAYS:]


def rq_metadata(code: str) -> tuple[str, str, str, date | None]:
    instrument = rq.instruments(vendor_code(code))
    market = "北交所" if code.startswith(("8", "920")) else ("沪市" if code.startswith(("5", "6", "9")) else "深市")
    if instrument is None:
        raise RuntimeError(f"RQData不支持证券 {code}")
    listed_date = instrument.listed_date
    if isinstance(listed_date, str):
        listed_date = date.fromisoformat(listed_date)
    return code, instrument.symbol or code, market, listed_date


async def main() -> None:
    stock_codes = codes()
    trading_dates = await asyncio.to_thread(rq_window)
    start_date, end_date = trading_dates[0], trading_dates[-1]
    connection = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await connection.execute("""CREATE TABLE IF NOT EXISTS backtest_priority_stocks (
          stock_code char(6) PRIMARY KEY REFERENCES stocks(code) ON DELETE CASCADE,
          batch_name varchar(48) NOT NULL,target_trading_days integer NOT NULL DEFAULT 580,
          source varchar(32) NOT NULL DEFAULT 'wechat_screenshot',created_at timestamptz NOT NULL DEFAULT now())""")
        await connection.execute("""CREATE TABLE IF NOT EXISTS minute_backfill_coverage (
          batch_name varchar(48) NOT NULL,stock_code char(6) NOT NULL REFERENCES stocks(code) ON DELETE CASCADE,
          range_start date NOT NULL,range_end date NOT NULL,status varchar(16) NOT NULL,
          rows_written integer NOT NULL DEFAULT 0,completed_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY(batch_name,stock_code,range_start,range_end))""")
        existing = {row["code"].strip() for row in await connection.fetch("SELECT code FROM stocks WHERE code=ANY($1::char(6)[])", stock_codes)}
        missing = [code for code in stock_codes if code not in existing]
        if missing:
            metadata = [await asyncio.to_thread(rq_metadata, code) for code in missing]
            await connection.executemany("""INSERT INTO stocks(code,name,market,listed_date)
              VALUES($1,$2,$3,$4) ON CONFLICT(code) DO UPDATE SET name=excluded.name,
              market=excluded.market,listed_date=coalesce(stocks.listed_date,excluded.listed_date),updated_at=now()""", metadata)
            print(f"stock_metadata_added={len(metadata)} codes={','.join(missing)}", flush=True)
        await connection.executemany("""INSERT INTO backtest_priority_stocks(stock_code,batch_name,target_trading_days)
          VALUES($1,'priority_merged_20260805',$2) ON CONFLICT(stock_code) DO UPDATE SET
          batch_name=excluded.batch_name,target_trading_days=excluded.target_trading_days""",
          [(code, TARGET_DAYS) for code in stock_codes])

        slices = []
        for right in range(len(trading_dates), 0, -SLICE_DAYS):
            left = max(0, right - SLICE_DAYS)
            slices.append((trading_dates[left], trading_dates[right - 1]))
        print(f"priority=253 target={TARGET_DAYS} window={start_date}..{end_date} slices={len(slices)} mode=breadth_first", flush=True)
        total_written = 0
        stop = False
        for slice_index, (range_start, range_end) in enumerate(slices, 1):
            completed = {row["stock_code"].strip() for row in await connection.fetch("""SELECT stock_code
              FROM minute_backfill_coverage WHERE batch_name='priority_merged_20260805'
              AND range_start=$1 AND range_end=$2 AND status IN ('ok','cached','not_listed')""", range_start, range_end)}
            for code_index, code in enumerate(stock_codes, 1):
                if code in completed:
                    continue
                listed_date = await connection.fetchval("SELECT listed_date FROM stocks WHERE code=$1", code)
                existing = await connection.fetchval("""SELECT count(*) FROM minute_quotes WHERE stock_code=$1
                  AND interval_minutes=30 AND trade_time::date BETWEEN $2 AND $3""", code, range_start, range_end)
                status, written = "cached", 0
                if listed_date and listed_date > range_end:
                    status = "not_listed"
                elif not existing:
                    quota = await asyncio.to_thread(rq.user.get_quota)
                    remaining = int(quota["bytes_limit"] - quota["bytes_used"])
                    if remaining <= RESERVE_BYTES:
                        print(f"quota_guard remaining={remaining}; stop at slice={slice_index} stock={code_index}", flush=True)
                        stop = True
                        break
                    written = await sync_exact_range(connection, code, range_start, range_end)
                    status = "ok"
                    total_written += written
                await connection.execute("""INSERT INTO minute_backfill_coverage
                  (batch_name,stock_code,range_start,range_end,status,rows_written)
                  VALUES('priority_merged_20260805',$1,$2,$3,$4,$5)
                  ON CONFLICT(batch_name,stock_code,range_start,range_end) DO UPDATE SET
                  status=excluded.status,rows_written=excluded.rows_written,completed_at=now()""",
                  code, range_start, range_end, status, written)
            print(f"slice[{slice_index}/{len(slices)}] {range_start}..{range_end} completed", flush=True)
            if stop:
                break
        covered = await connection.fetchval("""SELECT count(*) FROM (
          SELECT p.stock_code FROM backtest_priority_stocks p JOIN minute_quotes q ON q.stock_code=p.stock_code
          AND q.interval_minutes=30 AND q.trade_time::date BETWEEN $1 AND $2
          WHERE p.batch_name='priority_merged_20260805' GROUP BY p.stock_code
          HAVING count(DISTINCT q.trade_time::date) >= $3) x""", start_date, end_date, TARGET_DAYS)
        print(f"done written={total_written} fully_covered={covered}/253", flush=True)
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
