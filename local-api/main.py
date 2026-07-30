import asyncio
import os
from contextlib import asynccontextmanager
from datetime import date

import asyncpg
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from refresh_tags import refresh as refresh_stock_tags

DB = os.environ["DATABASE_URL"]
pool: asyncpg.Pool


def market(code: str) -> str:
    if code.startswith("68"):
        return "科创板"
    if code.startswith("30"):
        return "创业板"
    if code.startswith("6"):
        return "沪市"
    if code.startswith(("8", "4", "9")):
        return "北交所"
    return "深市"


def number(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def init_db():
    global pool
    pool = await asyncpg.create_pool(DB, min_size=2, max_size=12)
    async with pool.acquire() as connection:
        sql = open("schema.sql").read()
        for statement in (s.strip() for s in sql.split(";") if s.strip()):
            await connection.execute(statement)


async def persist_snapshot(items: list[dict]):
    today = date.today()
    valid = [
        item
        for item in items
        if str(item.get("f12", "")).isdigit()
        and len(str(item.get("f12"))) == 6
    ]
    async with pool.acquire() as connection, connection.transaction():
        await connection.executemany(
            """
            INSERT INTO stocks(code,name,market,updated_at)
            VALUES($1,$2,$3,now())
            ON CONFLICT(code) DO UPDATE SET
              name=excluded.name,market=excluded.market,updated_at=now()
            """,
            [
                (
                    str(item["f12"]),
                    str(item.get("f14") or item["f12"]),
                    market(str(item["f12"])),
                )
                for item in valid
            ],
        )
        quotes = [
            (
                str(item["f12"]),
                today,
                number(item.get("f17"), number(item.get("f2"))),
                number(item.get("f15"), number(item.get("f2"))),
                number(item.get("f16"), number(item.get("f2"))),
                number(item.get("f2")),
                number(item.get("f3")),
                int(number(item.get("f5"))),
                number(item.get("f6")),
                number(item.get("f8")),
            )
            for item in valid
            if number(item.get("f2")) > 0
        ]
        if quotes:
            await connection.executemany(
                """
                INSERT INTO daily_quotes(
                  stock_code,trade_date,open,high,low,close,
                  change_pct,volume,amount,turnover
                ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                ON CONFLICT(stock_code,trade_date) DO UPDATE SET
                  open=excluded.open,high=excluded.high,low=excluded.low,
                  close=excluded.close,change_pct=excluded.change_pct,
                  volume=excluded.volume,amount=excluded.amount,
                  turnover=excluded.turnover,updated_at=now()
                """,
                quotes,
            )


class SnapshotPayload(BaseModel):
    items: list[dict]


class HistoryPayload(BaseModel):
    code: str
    rows: list[dict]


@asynccontextmanager
async def lifespan(app):
    await init_db()
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(refresh_tags_job, "cron", hour=17, minute=10)
    scheduler.start()
    yield
    scheduler.shutdown()
    await pool.close()


app = FastAPI(title="析股本地行情API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


async def refresh_tags_job():
    async with pool.acquire() as connection:
        await refresh_stock_tags(connection)


@app.get("/health")
async def health():
    async with pool.acquire() as connection:
        stocks_count = await connection.fetchval("SELECT count(*) FROM stocks")
        quotes_count = await connection.fetchval("SELECT count(*) FROM daily_quotes")
    return {"ok": True, "stocks": stocks_count, "quotes": quotes_count}


@app.post("/api/import/snapshot")
async def import_snapshot(payload: SnapshotPayload):
    await persist_snapshot(payload.items)
    return {"written": len(payload.items)}


@app.get("/api/backfill/pending")
async def backfill_pending(min_days: int = 200, range_days: int = 365):
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT s.code,count(q.trade_date) AS days
            FROM stocks s
            LEFT JOIN daily_quotes q ON q.stock_code=s.code
              AND q.trade_date >= current_date - ($2 * interval '1 day')
            GROUP BY s.code
            HAVING count(q.trade_date) < $1
            ORDER BY s.code
            """,
            max(1, min_days),
            max(1, range_days),
        )
    return {"stocks": [dict(row) for row in rows]}


@app.post("/api/import/history")
async def import_history(payload: HistoryPayload):
    code = payload.code.strip()
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(400, "股票代码无效")
    values = [
        (
            code,
            date.fromisoformat(row["date"]),
            number(row.get("open")),
            number(row.get("high")),
            number(row.get("low")),
            number(row.get("close")),
            number(row.get("change")),
            int(number(row.get("volume"))),
            number(row.get("amount")),
            number(row.get("turnover")),
        )
        for row in payload.rows
        if row.get("date")
    ]
    if values:
        async with pool.acquire() as connection, connection.transaction():
            await connection.executemany(
                """
                INSERT INTO daily_quotes(
                  stock_code,trade_date,open,high,low,close,
                  change_pct,volume,amount,turnover,source
                ) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'eastmoney')
                ON CONFLICT(stock_code,trade_date) DO UPDATE SET
                  open=excluded.open,high=excluded.high,low=excluded.low,
                  close=excluded.close,change_pct=excluded.change_pct,
                  volume=excluded.volume,amount=excluded.amount,
                  turnover=excluded.turnover,updated_at=now(),source='eastmoney'
                """,
                values,
            )
    return {"written": len(values)}


@app.get("/api/stocks")
async def stocks(
    page: int = 1,
    tags: str = "",
    sort: str = "desc",
):
    page = max(1, page)
    selected_tags = [tag for tag in tags.split(",") if tag]
    order = "change ASC,s.code" if sort == "asc" else "change DESC,s.code"
    async with pool.acquire() as connection:
        total = await connection.fetchval(
            """
            SELECT count(*) FROM stocks s
            WHERE cardinality($1::text[]) = 0 OR (
              SELECT count(DISTINCT tag_key) FROM stock_tags
              WHERE stock_code=s.code AND tag_key=ANY($1::text[])
            ) = cardinality($1::text[])
            """,
            selected_tags,
        )
        rows = await connection.fetch(
            f"""
            SELECT s.code,s.name,s.market,
              coalesce(q.close,0) price,coalesce(q.change_pct,0) change,
              coalesce(q.volume,0) volume,coalesce(q.amount,0) amount,
              coalesce(t.tags,ARRAY[]::text[]) tags,
              coalesce(t.tag_keys,ARRAY[]::text[]) tag_keys
            FROM stocks s
            LEFT JOIN LATERAL (
              SELECT close,change_pct,volume,amount
              FROM daily_quotes
              WHERE stock_code=s.code
              ORDER BY trade_date DESC LIMIT 1
            ) q ON true
            LEFT JOIN LATERAL (
              SELECT array_agg(tag_name ORDER BY category,tag_name) tags,
                array_agg(tag_key ORDER BY category,tag_name) tag_keys
              FROM stock_tags WHERE stock_code=s.code
            ) t ON true
            WHERE cardinality($1::text[]) = 0 OR (
              SELECT count(DISTINCT tag_key) FROM stock_tags
              WHERE stock_code=s.code AND tag_key=ANY($1::text[])
            ) = cardinality($1::text[])
            ORDER BY {order}
            LIMIT 100 OFFSET $2
            """,
            selected_tags,
            (page - 1) * 100,
        )
    return {
        "source": "本地PostgreSQL",
        "page": page,
        "pageSize": 100,
        "total": total,
        "stocks": [dict(row) for row in rows],
    }


@app.get("/api/search")
async def search(q: str, tags: str = "", sort: str = "desc"):
    selected_tags = [tag for tag in tags.split(",") if tag]
    order = "change ASC,s.code" if sort == "asc" else "change DESC,s.code"
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            f"""
            SELECT s.code,s.name,s.market,
              coalesce(d.close,0) price,coalesce(d.change_pct,0) change,
              coalesce(d.volume,0) volume,coalesce(d.amount,0) amount,
              coalesce(t.tags,ARRAY[]::text[]) tags,
              coalesce(t.tag_keys,ARRAY[]::text[]) tag_keys
            FROM stocks s
            LEFT JOIN LATERAL (
              SELECT close,change_pct,volume,amount FROM daily_quotes
              WHERE stock_code=s.code ORDER BY trade_date DESC LIMIT 1
            ) d ON true
            LEFT JOIN LATERAL (
              SELECT array_agg(tag_name ORDER BY category,tag_name) tags,
                array_agg(tag_key ORDER BY category,tag_name) tag_keys
              FROM stock_tags WHERE stock_code=s.code
            ) t ON true
            WHERE (
              s.code LIKE $1 OR s.name LIKE $1 OR EXISTS (
                SELECT 1 FROM stock_tags
                WHERE stock_code=s.code AND tag_name LIKE $1
              )
            ) AND (
              cardinality($2::text[]) = 0 OR (
                SELECT count(DISTINCT tag_key) FROM stock_tags
                WHERE stock_code=s.code AND tag_key=ANY($2::text[])
              ) = cardinality($2::text[])
            )
            ORDER BY {order} LIMIT 100
            """,
            f"%{q}%",
            selected_tags,
        )
    return {"stocks": [dict(row) for row in rows]}


@app.get("/api/tags")
async def tags():
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT tag_key AS key,
              CASE
                WHEN tag_key='tolerant_rise' THEN '容错连涨'
                WHEN tag_key='tolerant_fall' THEN '容错连跌'
                ELSE min(tag_name)
              END AS name,
              min(category) AS category,min(direction) AS direction,
              count(DISTINCT stock_code) AS stock_count
            FROM stock_tags
            GROUP BY tag_key
            ORDER BY stock_count DESC,tag_key
            """
        )
    return {"tags": [dict(row) for row in rows]}


@app.post("/api/tags/refresh")
async def refresh_tags():
    async with pool.acquire() as connection:
        stock_count, tag_count = await refresh_stock_tags(connection)
    return {"stocks": stock_count, "tags": tag_count}


@app.get("/api/history")
async def history(code: str):
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(400, "股票代码无效")
    prefix = "sh" if code.startswith("6") else "bj" if code.startswith(("4", "8", "9")) else "sz"
    symbol = f"{prefix}{code}"
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={symbol},day,,,500,qfq"
    )
    async with httpx.AsyncClient(
        timeout=20,
        headers={"Referer": "https://gu.qq.com/", "User-Agent": "Mozilla/5.0"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json().get("data", {}).get(symbol, {})
    source = data.get("qfqday") or data.get("day") or []
    parsed = []
    for index, values in enumerate(source):
        close = float(values[2])
        previous = float(source[index - 1][2]) if index else close
        volume = int(float(values[5]))
        parsed.append(
            {
                "date": values[0],
                "open": float(values[1]),
                "close": close,
                "high": float(values[3]),
                "low": float(values[4]),
                "volume": volume,
                "amount": close * volume * 100,
                "change": (close / previous - 1) * 100 if previous else 0,
                "turnover": 0,
            }
        )
    if not parsed:
        raise HTTPException(502, "历史行情暂时不可用")
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            """
            INSERT INTO stocks(code,name,market,updated_at)
            VALUES($1,$2,$3,now())
            ON CONFLICT(code) DO UPDATE SET updated_at=now()
            """,
            code,
            code,
            market(code),
        )
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
              updated_at=now()
            """,
            [
                (
                    code,
                    date.fromisoformat(row["date"]),
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["change"],
                    row["volume"],
                    row["amount"],
                    row["turnover"],
                )
                for row in parsed
            ],
        )
    return {"source": "本地PostgreSQL + 腾讯证券", "rows": parsed}


@app.get("/api/industries")
async def industries():
    return {"source": "本地PostgreSQL", "industries": []}


@app.post("/api/sync/daily")
async def manual_sync():
    return {
        "started": False,
        "message": "请在Mac宿主机执行 local-api/sync_host.py",
    }
