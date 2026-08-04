"""通过 RQData 同步 A 股 30 分钟历史行情到本地 PostgreSQL。"""

import asyncio
import os
from datetime import date, timedelta

import asyncpg
import rqdatac as rq


def vendor_code(code: str) -> str:
    if code.startswith(("8", "920")):
        return f"{code}.XBJ"
    return f"{code}.XSHG" if code.startswith(("5", "6", "9")) else f"{code}.XSHE"


def _credentials() -> tuple[str, str]:
    username = os.getenv("RQDATA_USERNAME", "").strip()
    password = os.getenv("RQDATA_PASSWORD", "").strip()
    if not username or not password:
        raise RuntimeError("缺少 RQDATA_USERNAME 或 RQDATA_PASSWORD")
    return username, password


def fetch_thirty_minute(code: str, start_date: date, end_date: date) -> list[tuple]:
    username, password = _credentials()
    rq.init(username, password)
    frame = rq.get_price(
        vendor_code(code), start_date=start_date, end_date=end_date,
        frequency="30m", fields=["open", "high", "low", "close", "volume", "total_turnover"],
        adjust_type="none", skip_suspended=True, expect_df=True,
    )
    if frame is None or frame.empty:
        return []
    rows = []
    for index, item in frame.iterrows():
        trade_time = index[-1] if isinstance(index, tuple) else index
        rows.append((
            code, trade_time.to_pydatetime(), 30,
            float(item["open"]), float(item["high"]), float(item["low"]), float(item["close"]),
            float(item["volume"] or 0), float(item["total_turnover"] or 0),
        ))
    return rows


async def sync(connection: asyncpg.Connection, stock_codes: list[str], start_date: date, end_date: date) -> dict:
    written = 0
    failures = []
    cached = []
    for code in stock_codes:
        try:
            bounds = await connection.fetchrow(
                """SELECT min(trade_time)::date first_date,max(trade_time)::date last_date
                   FROM minute_quotes WHERE stock_code=$1 AND interval_minutes=30""", code,
            )
            ranges = []
            first_date, last_date = bounds["first_date"], bounds["last_date"]
            if first_date is None:
                ranges.append((start_date, end_date))
            else:
                if start_date < first_date:
                    ranges.append((start_date, first_date - timedelta(days=1)))
                if last_date < end_date:
                    ranges.append((last_date + timedelta(days=1), end_date))
            if not ranges:
                cached.append(code)
                continue
            for range_start, range_end in ranges:
                rows = await asyncio.to_thread(fetch_thirty_minute, code, range_start, range_end)
                for offset in range(0, len(rows), 5000):
                    await connection.executemany("""INSERT INTO minute_quotes(stock_code,trade_time,interval_minutes,open,high,low,close,volume,amount,source)
                      VALUES($1,$2::timestamp,$3,$4,$5,$6,$7,$8,$9,'rqdata')
                      ON CONFLICT(stock_code,trade_time,interval_minutes) DO UPDATE SET
                      open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
                      volume=excluded.volume,amount=excluded.amount,updated_at=now()""", rows[offset:offset + 5000])
                written += len(rows)
        except Exception as exc:
            failures.append({"code": code, "error": str(exc)})
    return {"written": written, "cached": cached, "failures": failures, "interval_minutes": 30, "source": "RQData"}


async def sync_exact_range(connection: asyncpg.Connection, code: str, start_date: date, end_date: date) -> int:
    """无视整体边界，精确同步一个日期切片，供按日期广度优先的回填任务使用。"""
    rows = await asyncio.to_thread(fetch_thirty_minute, code, start_date, end_date)
    for offset in range(0, len(rows), 5000):
        await connection.executemany("""INSERT INTO minute_quotes(stock_code,trade_time,interval_minutes,open,high,low,close,volume,amount,source)
          VALUES($1,$2::timestamp,$3,$4,$5,$6,$7,$8,$9,'rqdata')
          ON CONFLICT(stock_code,trade_time,interval_minutes) DO UPDATE SET
          open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
          volume=excluded.volume,amount=excluded.amount,source='rqdata',updated_at=now()""", rows[offset:offset + 5000])
    return len(rows)
