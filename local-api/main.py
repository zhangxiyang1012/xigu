import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
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
from refresh_discipline_signals import refresh as refresh_discipline, refresh_one as refresh_one_discipline

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


async def refresh_latest_quotes(codes: list[str]) -> int:
    """批量刷新指定股票的最新行情，并基于最新交易日重算标签。"""
    if not codes:
        return 0
    symbols = [
        ("sh" if code.startswith("6") else
         "bj" if code.startswith(("4", "8", "9")) else "sz") + code
        for code in codes
    ]
    items: list[dict] = []
    shanghai = ZoneInfo("Asia/Shanghai")
    async with httpx.AsyncClient(
        timeout=20,
        headers={"Referer": "https://gu.qq.com/", "User-Agent": "Mozilla/5.0"},
    ) as client:
        for start in range(0, len(symbols), 80):
            response = await client.get(
                f"https://qt.gtimg.cn/q={','.join(symbols[start:start + 80])}"
            )
            response.raise_for_status()
            for line in response.content.decode("gb18030", errors="replace").splitlines():
                match = re.match(r'v_[^=]+="(.*)";', line.strip())
                if not match:
                    continue
                values = match.group(1).split("~")
                if len(values) < 39 or not values[2].isdigit() or not values[30]:
                    continue
                try:
                    traded_at = datetime.strptime(values[30], "%Y%m%d%H%M%S").replace(
                        tzinfo=shanghai
                    )
                    amount_parts = values[35].split("/")
                    items.append({
                        "f12": values[2],
                        "f14": values[1],
                        "f2": float(values[3]),
                        "f3": float(values[32]),
                        "f5": int(float(values[36])),
                        "f6": float(amount_parts[2]) if len(amount_parts) > 2 else 0,
                        "f8": float(values[38]),
                        "f17": float(values[5]),
                        "f15": float(values[33]),
                        "f16": float(values[34]),
                        "f124": int(traded_at.timestamp()),
                    })
                except (ValueError, IndexError):
                    continue
    if not items:
        return 0
    await persist_snapshot(items)
    refreshed_codes = {str(item["f12"]) for item in items}
    async with pool.acquire() as connection, connection.transaction():
        for code in refreshed_codes:
            await refresh_one_stock_tags(connection, code)
    return len(refreshed_codes)


class SnapshotPayload(BaseModel):
    items: list[dict]


class HistoryPayload(BaseModel):
    code: str
    rows: list[dict]

class IndustryImportPayload(BaseModel):
    items: list[dict]

class PositionPayload(BaseModel):
    code: str
    quantity: float | None = None
    cost_price: float | None = None
    note: str | None = None


class BacktestPayload(BaseModel):
    stock_codes: list[str]
    execution_mode: str = "daily_next_open"


@asynccontextmanager
async def lifespan(app):
    await init_db()
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(refresh_tags_job, "cron", hour=17, minute=10)
    scheduler.add_job(refresh_industries_job, "cron", hour=17, minute=15)
    scheduler.add_job(refresh_discipline_job, "cron", hour=17, minute=20)
    scheduler.start()
    yield
    scheduler.shutdown()
    await pool.close()


app = FastAPI(title="析股本地行情API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


async def refresh_tags_job():
    async with pool.acquire() as connection:
        await refresh_stock_tags(connection)


async def refresh_industries_job():
    async with pool.acquire() as connection:
        await refresh_industry_metrics(connection)


async def refresh_discipline_job():
    async with pool.acquire() as connection:
        await refresh_discipline(connection)


def eastmoney_codes(code: str) -> tuple[str, str]:
    exchange = "SH" if code.startswith("6") else "BJ" if code.startswith(("4", "8", "9")) else "SZ"
    return f"{exchange}{code}", f"{code}.{exchange}"


async def sync_fundamentals(code: str):
    survey_code, secu_code = eastmoney_codes(code)
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Mozilla/5.0"}) as client:
        survey_req, finance_req, concept_req = await asyncio.gather(
            client.get("https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax", params={"code": survey_code}),
            client.get("https://datacenter.eastmoney.com/securities/api/data/v1/get", params={"reportName":"RPT_F10_FINANCE_MAINFINADATA","columns":"ALL","filter":f'(SECUCODE="{secu_code}")',"pageNumber":1,"pageSize":1,"sortTypes":-1,"sortColumns":"REPORT_DATE"}),
            client.get("https://datacenter-web.eastmoney.com/api/data/v1/get", params={"reportName":"RPT_F10_CORETHEME_BOARDTYPE","columns":"ALL","filter":f'(SECUCODE="{secu_code}")',"pageNumber":1,"pageSize":50}),
        )
    survey_req.raise_for_status(); finance_req.raise_for_status(); concept_req.raise_for_status()
    base = survey_req.json().get("jbzl") or {}
    finance_rows = ((finance_req.json().get("result") or {}).get("data") or [])
    financial = finance_rows[0] if finance_rows else {}
    concept_rows = ((concept_req.json().get("result") or {}).get("data") or [])
    concepts = [{"name":x.get("BOARD_NAME"),"reason":x.get("SELECTED_BOARD_REASON")}
                for x in concept_rows if x.get("BOARD_NAME") and x.get("SELECTED_BOARD_REASON")][:12]
    report_date = date.fromisoformat(financial["REPORT_DATE"][:10]) if financial.get("REPORT_DATE") else None
    async with pool.acquire() as connection:
        await connection.execute("""INSERT INTO stock_fundamentals(stock_code,company_name,main_business,company_intro,
          concepts,report_date,report_name,revenue,revenue_yoy,net_profit,net_profit_yoy,gross_margin,roe,total_shares,free_shares,updated_at)
          VALUES($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,now())
          ON CONFLICT(stock_code) DO UPDATE SET company_name=excluded.company_name,main_business=excluded.main_business,
          company_intro=excluded.company_intro,concepts=excluded.concepts,report_date=excluded.report_date,
          report_name=excluded.report_name,revenue=excluded.revenue,revenue_yoy=excluded.revenue_yoy,
          net_profit=excluded.net_profit,net_profit_yoy=excluded.net_profit_yoy,gross_margin=excluded.gross_margin,
          roe=excluded.roe,total_shares=excluded.total_shares,free_shares=excluded.free_shares,updated_at=now()""",
          code,base.get("gsmc"),base.get("jyfw"),base.get("gsjj"),json.dumps(concepts,ensure_ascii=False),
          report_date,financial.get("REPORT_DATE_NAME"),financial.get("TOTALOPERATEREVE"),
          financial.get("TOTALOPERATEREVETZ"),financial.get("PARENTNETPROFIT"),
          financial.get("PARENTNETPROFITTZ"),financial.get("XSMLL"),financial.get("ROEJQ"),
          financial.get("TOTAL_SHARE"),financial.get("A_FREE_SHARE"))


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
    page_codes = [row["code"] for row in rows]
    if page_codes:
        try:
            await refresh_latest_quotes(page_codes)
        except Exception:
            # 免费行情短暂不可用时保留数据库中的最近有效结果。
            pass
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
                WHERE s.code=ANY($1::text[]) AND (
                  cardinality($2::text[]) = 0 OR (
                    SELECT count(DISTINCT tag_key) FROM stock_tags
                    WHERE stock_code=s.code AND tag_key=ANY($2::text[])
                  ) = cardinality($2::text[])
                )
                ORDER BY {order}
                """,
                page_codes,
                selected_tags,
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
        await refresh_one_discipline(connection, code)
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


@app.post("/api/discipline-signals/refresh")
async def refresh_discipline_signals():
    async with pool.acquire() as connection:
        count = await refresh_discipline(connection)
    return {"stocks": count}


@app.get("/api/discipline-rules")
async def discipline_rules():
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """SELECT rule_key,rule_name,side,category,priority,description,parameters
               FROM discipline_rules WHERE enabled ORDER BY priority DESC,rule_key""")
    return {"rules": [dict(row) for row in rows]}


async def backtest_performance(connection, run):
    """股票等权汇总；单只股票内部将历次交易收益复利连接。"""
    rows = await connection.fetch("""SELECT stock_code,buy_date,coalesce(sell_date,buy_date) exit_date,
      return_pct,status FROM strategy_backtest_trades WHERE run_id=$1 ORDER BY stock_code,buy_date,id""", run["id"])
    stock_factors, closed_returns, ordered_returns = {}, [], []
    for row in rows:
        value = number(row["return_pct"])
        stock_factors[row["stock_code"]] = stock_factors.get(row["stock_code"], 1.0) * (1 + value / 100)
        ordered_returns.append((row["exit_date"], value))
        if row["status"] == "closed": closed_returns.append(value)
    stock_count = max(1, int(run["stock_count"] or 0))
    strategy_return = 100 * ((sum(stock_factors.values()) + stock_count - len(stock_factors)) / stock_count - 1)
    elapsed_days = max(1, (run["end_date"] - run["start_date"]).days)
    annualized = ((1 + strategy_return / 100) ** (365 / elapsed_days) - 1) * 100 if strategy_return > -100 else -100
    equity = peak = 1.0
    max_drawdown = 0.0
    for _, value in sorted(ordered_returns, key=lambda item: item[0]):
        equity *= 1 + value / 100
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1)
    parameters = run["parameters"] if isinstance(run["parameters"], dict) else json.loads(run["parameters"] or "{}")
    selected = parameters.get("stock_codes") or []
    if selected:
        benchmark_rows = await connection.fetch("""SELECT stock_code,
          (array_agg(close ORDER BY trade_date))[1] first_close,
          (array_agg(close ORDER BY trade_date DESC))[1] last_close
          FROM daily_quotes WHERE stock_code::text=ANY($1::text[]) AND trade_date BETWEEN $2 AND $3
          GROUP BY stock_code""", selected, run["start_date"], run["end_date"])
    else:
        benchmark_rows = await connection.fetch("""SELECT stock_code,
          (array_agg(close ORDER BY trade_date))[1] first_close,
          (array_agg(close ORDER BY trade_date DESC))[1] last_close
          FROM daily_quotes WHERE trade_date BETWEEN $1 AND $2 GROUP BY stock_code""", run["start_date"], run["end_date"])
    benchmark_values = [100 * (number(row["last_close"]) / number(row["first_close"]) - 1)
                        for row in benchmark_rows if number(row["first_close"]) > 0]
    benchmark_return = sum(benchmark_values) / len(benchmark_values) if benchmark_values else 0.0
    initial_assets = 1_000_000.0
    ending_assets = initial_assets * (1 + strategy_return / 100)
    return {"strategy_return_pct": round(strategy_return, 4), "annualized_return_pct": round(annualized, 4),
            "benchmark_return_pct": round(benchmark_return, 4), "excess_return_pct": round(strategy_return - benchmark_return, 4),
            "trade_sequence_max_drawdown_pct": round(100 * max_drawdown, 4),
            "initial_assets": round(initial_assets, 2), "ending_assets": round(ending_assets, 2),
            "total_profit": round(ending_assets - initial_assets, 2), "net_deposit": 0.0,
            "realized_profit_trades": sum(value > 0 for value in closed_returns),
            "calculation": "券商账户口径：累计收益率=(期末总资产-期初总资产-净入金)÷期初总资产；回测无入出金，初始资金100万元；开放仓按期末价计价"}


@app.get("/api/backtest/latest")
async def latest_backtest():
    async with pool.acquire() as connection:
        run = await connection.fetchrow("SELECT * FROM strategy_backtest_runs ORDER BY id DESC LIMIT 1")
        if not run: return {"run": None, "summaries": [], "trades": {}, "strategies": [], "events": []}
        summaries = await connection.fetch("""SELECT side,horizon,trading_days,sample_count,avg_return_pct,
          median_return_pct,win_rate_pct,best_return_pct,worst_return_pct FROM strategy_backtest_summaries
          WHERE run_id=$1 ORDER BY side,trading_days""", run["id"])
        trade_stats = await connection.fetchrow("""SELECT count(*) FILTER(WHERE status='closed') closed_count,
          count(*) FILTER(WHERE status='open') open_count,avg(return_pct) FILTER(WHERE status='closed') avg_return_pct,
          percentile_cont(.5) WITHIN GROUP(ORDER BY return_pct) FILTER(WHERE status='closed') median_return_pct,
          100.0*avg(CASE WHEN return_pct>0 THEN 1 ELSE 0 END) FILTER(WHERE status='closed') win_rate_pct,
          avg(holding_days) FILTER(WHERE status='closed') avg_holding_days,
          max(return_pct) FILTER(WHERE status='closed') best_return_pct,
          min(return_pct) FILTER(WHERE status='closed') worst_return_pct
          FROM strategy_backtest_trades WHERE run_id=$1""", run["id"])
        strategies = await connection.fetch("""SELECT 'buy' side,buy_strategy strategy,count(*) samples,
          avg(return_pct) avg_return_pct,100.0*avg(CASE WHEN return_pct>0 THEN 1 ELSE 0 END) win_rate_pct
          FROM strategy_backtest_trades WHERE run_id=$1 AND status='closed' GROUP BY buy_strategy
          UNION ALL SELECT 'sell',coalesce(sell_strategy,'未卖出'),count(*),avg(return_pct),
          100.0*avg(CASE WHEN return_pct>0 THEN 1 ELSE 0 END)
          FROM strategy_backtest_trades WHERE run_id=$1 AND status='closed' GROUP BY sell_strategy
          ORDER BY side,samples DESC""", run["id"])
        events = await connection.fetch("""SELECT e.side,e.signal_date,e.execution_date,e.signal_level,
          e.strategy_name,e.matched_rules,e.execution_price,s.code,s.name,s.industry_name
          FROM strategy_backtest_events e JOIN stocks s ON s.code=e.stock_code WHERE e.run_id=$1
          ORDER BY e.signal_date DESC,e.id DESC LIMIT 100""", run["id"])
        performance = await backtest_performance(connection, run)
    run_dict = dict(run)
    if isinstance(run_dict.get("parameters"), str): run_dict["parameters"] = json.loads(run_dict["parameters"])
    return {"run": run_dict, "summaries": [dict(row) for row in summaries], "trades": dict(trade_stats), "performance": performance,
            "strategies": [dict(row) for row in strategies], "events": [dict(row) for row in events]}


@app.post("/api/backtest/run")
async def start_backtest(payload: BacktestPayload):
    from backtest_strategies import run
    from minute_data import sync as sync_minute_data
    stock_codes = list(dict.fromkeys(code.strip() for code in payload.stock_codes if re.fullmatch(r"\d{6}", code.strip())))
    if not stock_codes:
        raise HTTPException(400, "请至少选择一只股票")
    if len(stock_codes) > 300:
        raise HTTPException(400, "单次最多回测300只股票")
    if payload.execution_mode not in {"daily_next_open", "intraday_30m", "multi_timeframe"}:
        raise HTTPException(400, "不支持的成交模式")
    if payload.execution_mode == "intraday_30m" and len(stock_codes) > 50:
        raise HTTPException(400, "30分钟回测单次最多选择50只股票")
    async with pool.acquire() as connection:
        valid_count = await connection.fetchval("SELECT count(*) FROM stocks WHERE code::text=ANY($1::text[])", stock_codes)
        if valid_count != len(stock_codes):
            raise HTTPException(400, "选择中包含无效股票代码")
        sync_result = None
        if payload.execution_mode == "intraday_30m":
            end_date = await connection.fetchval("SELECT max(trade_date) FROM daily_quotes")
            try:
                sync_result = await sync_minute_data(connection, stock_codes, end_date - timedelta(days=1095), end_date)
            except RuntimeError as exc:
                raise HTTPException(503, f"30分钟行情源暂不可用：{exc}") from exc
            if sync_result["failures"]:
                raise HTTPException(503, {"message": "30分钟行情同步失败", "failures": sync_result["failures"]})
        if payload.execution_mode == "multi_timeframe":
            covered = await connection.fetchval("""SELECT count(DISTINCT stock_code) FROM minute_quotes
              WHERE interval_minutes=30 AND stock_code::text=ANY($1::text[])""", stock_codes)
            if covered != len(stock_codes):
                raise HTTPException(400, f"所选股票中仅{covered}/{len(stock_codes)}只有30分钟数据，请先完成回填")
        run_id, events, trades = await run(connection, stock_codes, payload.execution_mode)
        completed_run = await connection.fetchrow("SELECT * FROM strategy_backtest_runs WHERE id=$1", run_id)
        performance = await backtest_performance(connection, completed_run)
    return {"run_id": run_id, "events": events, "trades": trades, "performance": performance, "minute_sync": sync_result}


@app.get("/api/position-model")
async def position_model():
    from position_model import market_regime, target_position
    async with pool.acquire() as connection:
        market = await connection.fetchrow("""SELECT avg(above_ma20_pct) breadth,
          100.0*avg(CASE WHEN risk_level='高' THEN 1 ELSE 0 END) high_risk_share,max(trade_date) trade_date
          FROM industry_daily_metrics WHERE trade_date=(SELECT max(trade_date) FROM industry_daily_metrics)""")
        rows = await connection.fetch("""SELECT p.stock_code code,s.name,s.industry_name,d.close,d.defense_price,
          d.buy_level,d.industry_rank,d.industry_risk_level FROM portfolio_positions p JOIN stocks s ON s.code=p.stock_code
          LEFT JOIN LATERAL(SELECT * FROM stock_discipline_signals WHERE stock_code=p.stock_code ORDER BY trade_date DESC LIMIT 1)d ON true
          ORDER BY p.created_at""")
    breadth, high_risk = number(market["breadth"] if market else 0), number(market["high_risk_share"] if market else 0)
    regime, factor = market_regime(breadth, high_risk)
    positions = []
    for row in rows:
        item = dict(row)
        item.update(target_position(close=number(row["close"]), defense=number(row["defense_price"]),
          buy_level=row["buy_level"] or "", industry_rank=row["industry_rank"],
          industry_risk=row["industry_risk_level"] or "中", market_factor=factor))
        positions.append(item)
    return {"market": {"regime": regime, "factor": factor, "breadth": breadth,
            "high_risk_share": high_risk, "trade_date": market["trade_date"] if market else None}, "positions": positions,
            "limits": {"risk_per_trade_pct": .8, "stock_max_pct": 15, "industry_max_pct": 35}}


@app.get("/api/radar")
async def radar(side: str = "all", level: str = "", industry: str = "", q: str = "", only_watch: bool = False, limit: int = 100):
    limit = min(300, max(1, limit))
    pinyin_query = re.sub(r"[^a-z0-9]", "", q.lower())
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """SELECT DISTINCT ON (d.stock_code) d.stock_code AS code,s.name,s.market,s.industry_name,
              d.trade_date,d.close AS price,q.change_pct AS change,d.ma5,d.ma10,d.ma20,d.atr14,
              d.volume_ratio_5,d.volume_ratio_20,d.drawdown_20d,d.drawdown_60d,d.pullback_days,
              d.industry_rank,d.industry_rotation_score,d.industry_risk_level,d.buy_score,d.sell_score,
              d.buy_level,d.sell_level,d.buy_model,d.buy_signals,d.sell_signals,d.blockers,
              d.defense_price,d.stop_atr_price
            FROM stock_discipline_signals d JOIN stocks s ON s.code=d.stock_code
            LEFT JOIN daily_quotes q ON q.stock_code=d.stock_code AND q.trade_date=d.trade_date
            WHERE ($1='' OR s.industry_name=$1)
              AND (NOT $2 OR EXISTS(SELECT 1 FROM watchlist w WHERE w.stock_code=d.stock_code))
              AND ($3='' OR s.code LIKE $3 OR s.name LIKE $3
                OR ($4<>'' AND (s.name_pinyin LIKE '%'||$4||'%' OR s.name_initials LIKE '%'||$4||'%'))
                OR EXISTS(SELECT 1 FROM stock_tags t WHERE t.stock_code=d.stock_code AND t.tag_name LIKE $3))
            ORDER BY d.stock_code,d.trade_date DESC""", industry, only_watch, f"%{q}%", pinyin_query)
    items = [dict(row) for row in rows]
    # 顶部统计必须基于完整命中集合。若先按某一侧排序并 LIMIT，选择
    # “卖出纪律”时前 200 条通常全是退出信号，会把买入/减仓错误显示为 0。
    summary = {
        "buy_confirmed": sum(item["buy_level"] == "买入确认" for item in items),
        "candidates": sum(item["buy_level"] == "候选" for item in items),
        "reduce": sum(item["sell_level"] == "减仓" for item in items),
        "exit": sum(item["sell_level"] == "退出" for item in items),
    }
    # 统计卡是全量摘要；level 只负责筛选下方列表，不能反向改变其他统计数。
    if level:
        items = [item for item in items if item["buy_level"] == level or item["sell_level"] == level]
    if side == "buy": items.sort(key=lambda x: number(x["buy_score"]), reverse=True)
    elif side == "sell": items.sort(key=lambda x: number(x["sell_score"]), reverse=True)
    else: items.sort(key=lambda x: max(number(x["buy_score"]), number(x["sell_score"])), reverse=True)
    items = items[:limit]
    return {"side": side, "summary": summary, "signals": items}


@app.get("/api/watchlist")
async def get_watchlist():
    async with pool.acquire() as connection:
        rows = await connection.fetch("SELECT stock_code AS code,created_at FROM watchlist ORDER BY created_at DESC")
    return {"stocks": [dict(row) for row in rows]}


@app.post("/api/watchlist/{code}")
async def add_watchlist(code: str):
    if len(code) != 6 or not code.isdigit(): raise HTTPException(400, "股票代码无效")
    async with pool.acquire() as connection:
        result = await connection.execute("INSERT INTO watchlist(stock_code) VALUES($1) ON CONFLICT DO NOTHING", code)
    return {"code": code, "watched": True, "result": result}


@app.delete("/api/watchlist/{code}")
async def delete_watchlist(code: str):
    async with pool.acquire() as connection:
        await connection.execute("DELETE FROM watchlist WHERE stock_code=$1", code)
    return {"code": code, "watched": False}


@app.get("/api/portfolio")
async def get_portfolio():
    async with pool.acquire() as connection:
        rows = await connection.fetch("""SELECT p.stock_code AS code,s.name,s.market,s.industry_name,
          p.quantity,p.cost_price,p.note,d.trade_date,d.close AS price,q.change_pct AS change,
          d.buy_score,d.sell_score,d.buy_level,d.sell_level,d.buy_model,d.buy_signals,d.sell_signals,
          d.blockers,d.defense_price,d.stop_atr_price
        FROM portfolio_positions p JOIN stocks s ON s.code=p.stock_code
        LEFT JOIN LATERAL (SELECT * FROM stock_discipline_signals WHERE stock_code=p.stock_code ORDER BY trade_date DESC LIMIT 1) d ON true
        LEFT JOIN daily_quotes q ON q.stock_code=d.stock_code AND q.trade_date=d.trade_date
        ORDER BY greatest(coalesce(d.sell_score,0),coalesce(d.buy_score,0)) DESC,p.created_at DESC""")
    return {"positions": [dict(row) for row in rows]}


@app.get("/api/stock-analysis")
async def stock_analysis(code: str):
    if len(code) != 6 or not code.isdigit(): raise HTTPException(400, "股票代码无效")
    async with pool.acquire() as connection:
        cached_at = await connection.fetchval("SELECT updated_at FROM stock_fundamentals WHERE stock_code=$1", code)
    if not cached_at or cached_at < datetime.now(cached_at.tzinfo) - timedelta(days=7):
        try: await sync_fundamentals(code)
        except Exception as exc: print(f"fundamentals unavailable {code}: {exc}")
    async with pool.acquire() as connection:
        fundamental = await connection.fetchrow("""SELECT company_name,main_business,company_intro,concepts,
          report_date,report_name,revenue,revenue_yoy,net_profit,net_profit_yoy,gross_margin,roe,total_shares,
          free_shares,source,updated_at FROM stock_fundamentals WHERE stock_code=$1""", code)
        technical = await connection.fetchrow("""SELECT trade_date,close,ma5,ma10,ma20,atr14,volume_ratio_5,
          volume_ratio_20,drawdown_20d,drawdown_60d,pullback_days,industry_rank,industry_rotation_score,
          industry_risk_level,buy_score,sell_score,buy_level,sell_level,buy_model,buy_signals,sell_signals,
          blockers,defense_price,stop_atr_price FROM stock_discipline_signals WHERE stock_code=$1
          ORDER BY trade_date DESC LIMIT 1""", code)
        tags = await connection.fetch("SELECT tag_name,direction,category FROM stock_tags WHERE stock_code=$1 ORDER BY category,tag_name", code)
    f = dict(fundamental) if fundamental else None
    if f:
        latest_price = number(technical["close"]) if technical else 0
        f["total_market_cap"] = number(f.get("total_shares")) * latest_price
        f["free_market_cap"] = number(f.get("free_shares")) * latest_price
        rev_yoy, profit_yoy, profit = number(f.get("revenue_yoy")), number(f.get("net_profit_yoy")), number(f.get("net_profit"))
        f["performance_support"] = "营收与利润同步增长" if rev_yoy > 0 and profit_yoy > 0 and profit > 0 else "收入改善，利润仍需验证" if rev_yoy > 0 else "业绩支撑偏弱，关注后续报告"
    return {"fundamental": f, "technical": dict(technical) if technical else None, "tags": [dict(x) for x in tags]}


@app.post("/api/portfolio")
async def add_portfolio(payload: PositionPayload):
    if len(payload.code) != 6 or not payload.code.isdigit(): raise HTTPException(400, "股票代码无效")
    async with pool.acquire() as connection:
        await connection.execute("""INSERT INTO portfolio_positions(stock_code,quantity,cost_price,note)
          VALUES($1,$2,$3,$4) ON CONFLICT(stock_code) DO UPDATE SET quantity=coalesce(excluded.quantity,portfolio_positions.quantity),
          cost_price=coalesce(excluded.cost_price,portfolio_positions.cost_price),note=coalesce(excluded.note,portfolio_positions.note),updated_at=now()""",
          payload.code,payload.quantity,payload.cost_price,payload.note)
    return {"code": payload.code, "added": True}


@app.delete("/api/portfolio/{code}")
async def delete_portfolio(code: str):
    async with pool.acquire() as connection:
        await connection.execute("DELETE FROM portfolio_positions WHERE stock_code=$1", code)
    return {"code": code, "removed": True}


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
