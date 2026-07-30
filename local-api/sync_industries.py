"""从新浪财经免费公开接口同步全部A股行业分类到本地PostgreSQL。"""

import asyncio
import json
import os
import re

import asyncpg
import httpx

DATABASE_URL = os.environ["DATABASE_URL"]
SECTOR_URL = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
MEMBER_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)


async def get_with_retry(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    error = None
    for attempt in range(4):
        try:
            response = await client.get(url, **kwargs)
            response.raise_for_status()
            return response
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            error = exc
            await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"免费行业接口连续失败：{error}")


async def fetch_industries() -> list[tuple[str, str]]:
    timeout = httpx.Timeout(30)
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
    assignments: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        response = await get_with_retry(client, SECTOR_URL)
        text = response.content.decode("gb18030", "replace")
        match = re.search(r"=\s*(\{.*\})\s*;?\s*$", text, re.S)
        if not match:
            raise RuntimeError("新浪行业列表格式异常")
        sectors = json.loads(match.group(1))
        for order, value in enumerate(sectors.values(), 1):
            parts = value.split(",")
            node, industry = parts[0].strip(), parts[1].strip()
            expected = int(float(parts[2])) if len(parts) > 2 else 0
            pages = max(1, (expected + 79) // 80)
            for page in range(1, pages + 1):
                result = await get_with_retry(
                    client, MEMBER_URL,
                    params={
                        "page": page,
                        "num": 80,
                        "sort": "symbol",
                        "asc": 1,
                        "node": node,
                        "symbol": "",
                        "_s_r_a": "page",
                    },
                )
                members = result.json()
                for item in members:
                    code = str(item.get("code", ""))
                    if len(code) == 6 and code.isdigit():
                        assignments.setdefault(code, industry)
            print(f"\r行业 {order}/{len(sectors)}，已识别 {len(assignments)} 只", end="", flush=True)
    print()
    return list(assignments.items())


async def main():
    rows = await fetch_industries()
    connection = await asyncpg.connect(DATABASE_URL)
    try:
        await connection.executemany(
            """
            UPDATE stocks SET industry_name=$2,updated_at=now()
            WHERE code=$1
            """,
            rows,
        )
        covered = await connection.fetchval(
            "SELECT count(*) FROM stocks WHERE industry_name IS NOT NULL AND industry_name<>''"
        )
    finally:
        await connection.close()
    print(f"行业标签同步完成：接口返回 {len(rows)} 只，本地已覆盖 {covered} 只")


if __name__ == "__main__":
    asyncio.run(main())
