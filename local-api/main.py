import asyncio
import os
import re
from contextlib import asynccontextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo

import asyncpg
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pypinyin import Style, lazy_pinyin
from refresh_industries import refresh as refresh_industry_metrics
from refresh_leaders import refresh as refresh_leader_metrics
from refresh_tags import refresh as refresh_stock_tags, refresh_one as refresh_one_stock_tags

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


def search_keys(name: str) -> tuple[str, str]:
    full = "".join(lazy_pinyin(name)).lower()
    initials = "".join(lazy_pinyin(name, style=Style.FIRST_LETTER)).lower()
    clean = lambda value: re.sub(r"[^a-z0-9]", "", value)
    return clean(full), clean(initials)


async def init_db():
    global pool
    pool = await asyncpg.create_pool(DB, min_size=2, max_size=12)
    async with pool.acquire() as connection:
        sql = open("schema.sql").read()
        for statement in (s.strip() for s in sql.split(";") if s.strip()):
            await connection.execute(statement)
        missing = await connection.fetch(
            """SELECT code,name FROM stocks
               WHERE name_pinyin IS NULL OR name_initials IS NULL"""
        )
        if missing:
            await connection.executemany(
                """UPDATE stocks SET name_pinyin=$2,name_initials=$3
                   WHERE code=$1""",
                [(row["code"], *search_keys(row["name"])) for row in missing],
            )


async def persist_snapshot(items: list[dict]):
    shanghai = ZoneInfo("Asia/Shanghai")
    valid = [
        item
        for item in items
        if str(item.get("f12", "")).isdigit()
        and len(str(item.get("f12"))) == 6
    ]
    async with pool.acquire() as connection, connection.transaction():
        await connection.executemany(
            """
            INSERT INTO stocks(
              code,name,market,industry_name,name_pinyin,name_initials,updated_at
            )
            VALUES($1,$2,$3,$4,$5,$6,now())
            ON CONFLICT(code) DO UPDATE SET
              name=excluded.name,market=excluded.market,
              name_pinyin=excluded.name_pinyin,
              name_initials=excluded.name_initials,
              industry_name=CASE WHEN stocks.industry_source='sw2021'
                THEN stocks.industry_name
                ELSE coalesce(nullif(excluded.industry_name,''),stocks.industry_name) END,
              updated_at=now()
            """,
            [
                (
                    str(item["f12"]),
                    str(item.get("f14") or item["f12"]),
                    market(str(item["f12"])),
                    str(item.get("f100") or ""),
                    *search_keys(str(item.get("f14") or item["f12"])),
                )
                for item in valid
            ],
        )
        quotes = [
            (
                str(item["f12"]),
                datetime.fromtimestamp(number(item.get("f124")), shanghai).date(),
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
            if number(item.get("f2")) > 0 and number(item.get("f124")) > 0
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

class IndustryImportPayload(BaseModel):
    items: list[dict]


@asynccontextmanager
async def lifespan(app):
    await init_db()
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(refresh_tags_job, "cron", hour=17, minute=10)
    scheduler.add_job(refresh_industries_job, "cron", hour=17, minute=15)
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


async def refresh_industries_job():
    async with pool.acquire() as connection:
        await refresh_industry_metrics(connection)


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


@app.post("/api/import/sw-industries")
async def import_sw_industries(payload: IndustryImportPayload):
    values = []
    for item in payload.items:
        code = str(item.get("code", "")).strip()
        if len(code) == 6 and code.isdigit() and item.get("l2_name"):
            values.append((
                code,item.get("l1_code"),item.get("l1_name"),
                item.get("l2_code"),item.get("l2_name"),
            ))
    async with pool.acquire() as connection:
        await connection.executemany(
            """UPDATE stocks SET sw_l1_code=$2::varchar,sw_l1_name=$3::varchar,
               sw_l2_code=$4::varchar,sw_l2_name=$5::varchar,
               industry_name=$5::text,industry_source='sw2021',
               updated_at=now() WHERE code=$1""",
            values,
        )
        stats = await connection.fetchrow(
            """SELECT count(*) FILTER(WHERE sw_l2_name IS NOT NULL) l2,
               count(DISTINCT sw_l2_name) industries,count(*) total FROM stocks"""
        )
    return {"written": len(values),"l2_covered": stats["l2"],
            "industries": stats["industries"],"total": stats["total"]}


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
            SELECT s.code,s.name,s.market,s.industry_name,
              coalesce(q.close,0) price,coalesce(q.change_pct,0) change,
              coalesce(q.volume,0) volume,coalesce(q.amount,0) amount,
              coalesce(t.tags,ARRAY[]::text[]) tags,
              coalesce(t.tag_keys,ARRAY[]::text[]) tag_keys,
              ir.phase AS industry_phase,ir.risk_level AS industry_risk,
              ir.risk_score AS industry_risk_score
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
            LEFT JOIN LATERAL (
              SELECT phase,risk_level,risk_score
              FROM industry_daily_metrics
              WHERE industry_name=s.industry_name
              ORDER BY trade_date DESC LIMIT 1
            ) ir ON true
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
    pinyin_query = re.sub(r"[^a-z0-9]", "", q.lower())
    # 精确或小范围搜索时先刷新行情与系统标签，避免列表仍展示旧快照，
    # 而用户点击个股后才看到新交易日的数据。限制为 10 只，防止宽泛
    # 关键词触发大量外部行情请求。
    async with pool.acquire() as connection:
        candidates = await connection.fetch(
            """
            SELECT s.code
            FROM stocks s
            WHERE s.code LIKE $1 OR s.name LIKE $1
              OR ($2 <> '' AND (
                s.name_pinyin LIKE '%' || $2 || '%'
                OR s.name_initials LIKE '%' || $2 || '%'
              ))
            ORDER BY s.code
            LIMIT 11
            """,
            f"%{q}%",
            pinyin_query,
        )
    if 0 < len(candidates) <= 10:
        await asyncio.gather(
            *(history(row["code"]) for row in candidates),
            return_exceptions=True,
        )
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            f"""
            SELECT s.code,s.name,s.market,s.industry_name,
              coalesce(d.close,0) price,coalesce(d.change_pct,0) change,
              coalesce(d.volume,0) volume,coalesce(d.amount,0) amount,
              coalesce(t.tags,ARRAY[]::text[]) tags,
              coalesce(t.tag_keys,ARRAY[]::text[]) tag_keys,
              ir.phase AS industry_phase,ir.risk_level AS industry_risk,
              ir.risk_score AS industry_risk_score
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
            LEFT JOIN LATERAL (
              SELECT phase,risk_level,risk_score
              FROM industry_daily_metrics
              WHERE industry_name=s.industry_name
              ORDER BY trade_date DESC LIMIT 1
            ) ir ON true
            WHERE (
              s.code LIKE $1 OR s.name LIKE $1
              OR ($3 <> '' AND (
                s.name_pinyin LIKE '%' || $3 || '%'
                OR s.name_initials LIKE '%' || $3 || '%'
              )) OR EXISTS (
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
            pinyin_query,
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
    refreshed_tags = []
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
        await refresh_one_stock_tags(connection, code)
        refreshed_tags = await connection.fetch(
            """
            SELECT tag_name FROM stock_tags
            WHERE stock_code=$1
            ORDER BY category,tag_name
            """,
            code,
        )
    return {
        "source": "本地PostgreSQL + 腾讯证券",
        "rows": parsed,
        "tags": [row["tag_name"] for row in refreshed_tags],
    }


@app.get("/api/industries")
async def industries(days: int = 90):
    days = min(90, max(10, days))
    async with pool.acquire() as connection:
        latest = await connection.fetch(
            """
            SELECT DISTINCT ON (industry_name)
              industry_name,trade_date,member_count,avg_change_pct,
              industry_index,return_20d,amount,amount_ratio,above_ma20_pct,
              limit_up_count,up_count,down_count,rotation_score,phase,
              phase_days,risk_score,risk_level,risk_reasons
            FROM industry_daily_metrics
            ORDER BY industry_name,trade_date DESC
            """
        )
        history = await connection.fetch(
            """
            SELECT industry_name,trade_date,industry_index,rotation_score,
              phase,risk_score,risk_level,above_ma20_pct
            FROM industry_daily_metrics
            WHERE trade_date >= (
              SELECT max(trade_date) FROM industry_daily_metrics
            ) - ($1 * interval '1 day')
            ORDER BY industry_name,trade_date
            """,
            days * 2,
        )
    histories: dict[str, list[dict]] = {}
    for row in history:
        name = row["industry_name"]
        histories.setdefault(name, []).append(
            {
                "date": row["trade_date"].isoformat(),
                "index": number(row["industry_index"]),
                "score": number(row["rotation_score"]),
                "phase": row["phase"],
                "risk": number(row["risk_score"]),
                "riskLevel": row["risk_level"],
                "breadth": number(row["above_ma20_pct"]),
            }
        )
    result = []
    for row in latest:
        item = dict(row)
        name = item.pop("industry_name")
        item["name"] = name
        item["date"] = item.pop("trade_date").isoformat()
        item["history"] = histories.get(name, [])[-days:]
        result.append(item)
    result.sort(key=lambda item: number(item["rotation_score"]), reverse=True)
    return {"source": "本地PostgreSQL", "days": days, "industries": result}


@app.post("/api/industries/refresh")
async def refresh_industries():
    async with pool.acquire() as connection:
        industry_count, row_count = await refresh_industry_metrics(connection)
    return {"industries": industry_count, "rows": row_count}


@app.get("/api/industry-leaders")
async def industry_leaders(industry: str = ""):
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT a.*,s.name
            FROM industry_leader_analysis a JOIN stocks s ON s.code=a.stock_code
            WHERE ($1='' OR a.industry_name=$1)
            ORDER BY a.industry_name,a.correlation_90d DESC
            """,
            industry,
        )
    return {"source": "策略PDF + 本地PostgreSQL", "leaders": [dict(row) for row in rows]}


@app.post("/api/industry-leaders/refresh")
async def refresh_industry_leaders():
    async with pool.acquire() as connection:
        count = await refresh_leader_metrics(connection)
    return {"leaders": count}


@app.post("/api/sync/daily")
async def manual_sync():
    return {
        "started": False,
        "message": "请在Mac宿主机执行 local-api/sync_host.py",
    }
