"""回填数据库中全部A股指定天数的前复权日线。"""

import asyncio
import os
from datetime import date, timedelta

import asyncpg
import httpx

DATABASE_URL = os.environ["DATABASE_URL"]
BACKFILL_DAYS = int(os.getenv("BACKFILL_DAYS", "1095"))
START_DATE = date.today() - timedelta(days=BACKFILL_DAYS)
KLINE_LIMIT = min(5000, int(BACKFILL_DAYS * 0.75) + 100)
CONCURRENCY = int(os.getenv("BACKFILL_CONCURRENCY", "12"))
LIMIT = int(os.getenv("BACKFILL_LIMIT", "0"))


def symbol_for(code: str) -> str:
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("4", "8", "9")):
        return f"bj{code}"
    return f"sz{code}"


async def fetch_rows(client: httpx.AsyncClient, code: str) -> list[tuple]:
    symbol = symbol_for(code)

    async def fetch_page(start: str = "", end: str = "", count: int = 640) -> list:
        url = (
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={symbol},day,{start},{end},{count},qfq"
        )
        last_error = None
        for attempt in range(3):
            try:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json().get("data", {}).get(symbol, {})
                return payload.get("qfqday") or payload.get("day") or []
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"{code}: {last_error}")

    # 腾讯单次最多约 640 条：先取最新一页，再按日期补齐更早的区间。
    source = await fetch_page(count=min(KLINE_LIMIT, 640))
    if source:
        earliest = date.fromisoformat(source[0][0])
        if earliest > START_DATE:
            older = await fetch_page(
                start=START_DATE.isoformat(),
                end=(earliest - timedelta(days=1)).isoformat(),
                count=640,
            )
            source = older + source

    result = []
    previous_close = None
    seen_dates: set[date] = set()
    for values in source:
        trade_date = date.fromisoformat(values[0])
        close = float(values[2])
        if trade_date in seen_dates:
            previous_close = close
            continue
        seen_dates.add(trade_date)
        if trade_date < START_DATE:
            previous_close = close
            continue
        volume = int(float(values[5]))
        result.append(
            (
                code,
                trade_date,
                float(values[1]),
                float(values[3]),
                float(values[4]),
                close,
                (close / previous_close - 1) * 100 if previous_close else 0,
                volume,
                close * volume * 100,
                0.0,
            )
        )
        previous_close = close
    return result


async def store_rows(pool: asyncpg.Pool, rows: list[tuple]) -> int:
    if not rows:
        return 0
    async with pool.acquire() as connection, connection.transaction():
        await connection.executemany(
            """
            INSERT INTO daily_quotes(
              stock_code,trade_date,open,high,low,close,
              change_pct,volume,amount,turnover,source
            ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'tencent')
            ON CONFLICT(stock_code,trade_date) DO UPDATE SET
              open=excluded.open,high=excluded.high,low=excluded.low,
              close=excluded.close,change_pct=excluded.change_pct,
              volume=excluded.volume,amount=excluded.amount,
              turnover=excluded.turnover,updated_at=now(),source='tencent'
            WHERE daily_quotes.source <> 'eastmoney'
            """,
            rows,
        )
    return len(rows)


async def main():
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=16)
    async with pool.acquire() as connection:
        codes = [
            row["code"].strip()
            for row in await connection.fetch("SELECT code FROM stocks ORDER BY code")
        ]
        run_id = await connection.fetchval(
            """
            INSERT INTO sync_runs(sync_type,trade_date,status)
            VALUES($1,current_date,'running') RETURNING id
            """,
            f"backfill_{BACKFILL_DAYS}d",
        )
    if LIMIT > 0:
        codes = codes[:LIMIT]

    semaphore = asyncio.Semaphore(CONCURRENCY)
    completed = 0
    written = 0
    failed: list[str] = []
    lock = asyncio.Lock()

    async with httpx.AsyncClient(
        timeout=25,
        limits=httpx.Limits(
            max_connections=CONCURRENCY + 4,
            max_keepalive_connections=CONCURRENCY,
        ),
        headers={"Referer": "https://gu.qq.com/", "User-Agent": "Mozilla/5.0"},
    ) as client:

        async def process(code: str):
            nonlocal completed, written
            async with semaphore:
                try:
                    rows = await fetch_rows(client, code)
                    count = await store_rows(pool, rows)
                    async with lock:
                        written += count
                except Exception as exc:
                    async with lock:
                        failed.append(str(exc))
                finally:
                    async with lock:
                        completed += 1
                        if completed % 100 == 0 or completed == len(codes):
                            print(
                                f"进度 {completed}/{len(codes)}，"
                                f"写入 {written} 行，失败 {len(failed)} 只",
                                flush=True,
                            )

        await asyncio.gather(*(process(code) for code in codes))

    status = "success" if not failed else "partial"
    error = "\n".join(failed[:100]) if failed else None
    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE sync_runs SET status=$1,rows_written=$2,
              finished_at=now(),error=$3 WHERE id=$4
            """,
            status,
            written,
            error,
            run_id,
        )
    await pool.close()
    print(
        f"完成：{len(codes)} 只股票，写入 {written} 行，失败 {len(failed)} 只",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
