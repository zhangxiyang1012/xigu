"""根据本地日线批量刷新全部股票的决策辅助标签。"""

import asyncio
import math
import os
from collections import defaultdict

import asyncpg

DATABASE_URL = os.environ["DATABASE_URL"]


def tolerant_streak(rows: list[asyncpg.Record]) -> tuple[str, int]:
    prices = [float(row["close"]) for row in rows]
    best = ("平", 0)
    for days in range(1, min(30, len(prices) - 1) + 1):
        part = prices[-days - 1 :]
        direction = "上涨" if part[-1] >= part[0] else "下跌"
        opposite = sum(
            1
            for previous, current in zip(part, part[1:])
            if (direction == "上涨" and current < previous)
            or (direction == "下跌" and current > previous)
        )
        if opposite <= 1:
            best = (direction, days)
    return best


def strict_streak(rows: list[asyncpg.Record]) -> tuple[str, int]:
    if not rows:
        return "平", 0
    latest_change = float(rows[-1]["change_pct"])
    direction = "上涨" if latest_change > 0 else "下跌" if latest_change < 0 else "平"
    days = 0
    for row in reversed(rows):
        change = float(row["change_pct"])
        if ((direction == "上涨" and change > 0)
                or (direction == "下跌" and change < 0)):
            days += 1
        else:
            break
    return direction, days


def tolerant_trend_label(rows: list[asyncpg.Record]) -> tuple[str, int, str]:
    direction, days = tolerant_streak(rows)
    if not rows:
        return direction, days, ""
    current_direction, current_days = strict_streak(rows)
    segment_start = len(rows) - current_days
    first_change = float(rows[segment_start]["change_pct"])
    limit_direction = "涨停" if first_change > 9.8 else "跌停" if first_change < -9.8 else ""
    if limit_direction:
        limit_days = 0
        for row in rows[segment_start:]:
            change = float(row["change_pct"])
            if ((limit_direction == "涨停" and change > 9.8)
                    or (limit_direction == "跌停" and change < -9.8)):
                limit_days += 1
            else:
                break
        previous_direction, previous_days = strict_streak(rows[:segment_start])
        reversed_trend = (
            (limit_direction == "跌停" and previous_direction == "上涨")
            or (limit_direction == "涨停" and previous_direction == "下跌")
        )
        if reversed_trend and previous_days >= 2:
            continuation = (
                f"，转{'跌' if limit_direction == '跌停' else '涨'}"
                f"第{current_days}天"
                if current_days > limit_days else ""
            )
            return (
                previous_direction,
                current_days,
                f"容错连{'涨' if previous_direction == '上涨' else '跌'}"
                f"{previous_days}天，{limit_direction}第{limit_days}天"
                f"{continuation}",
            )
    return (
        direction,
        days,
        f"容错连{'涨' if direction == '上涨' else '跌'}{days}天",
    )


def build_tags(rows: list[asyncpg.Record]) -> list[tuple]:
    if not rows:
        return [("no_history", "暂无行情", "数据", "neutral", None)]

    closes = [float(row["close"]) for row in rows]
    volumes = [float(row["volume"]) for row in rows]
    latest = rows[-1]
    close = closes[-1]
    change = float(latest["change_pct"])
    ma5 = sum(closes[-5:]) / min(5, len(closes))
    ma10 = sum(closes[-10:]) / min(10, len(closes))
    ma20 = sum(closes[-20:]) / min(20, len(closes))
    recent20 = closes[-20:]
    avg_volume = sum(volumes[-20:]) / min(20, len(volumes))
    returns = [
        closes[index] / closes[index - 1] - 1
        for index in range(max(1, len(closes) - 20), len(closes))
        if closes[index - 1]
    ]
    volatility = (
        math.sqrt(sum((value - sum(returns) / len(returns)) ** 2 for value in returns) / len(returns))
        * math.sqrt(252)
        * 100
        if returns
        else 0
    )
    tags: list[tuple] = []

    if ma5 > ma10 > ma20:
        tags.append(("bull_ma", "多头排列", "趋势", "up", ma5))
    elif ma5 < ma10 < ma20:
        tags.append(("bear_ma", "空头排列", "趋势", "down", ma5))
    tags.append(
        ("above_ma20" if close >= ma20 else "below_ma20",
         "站上20日线" if close >= ma20 else "跌破20日线",
         "趋势", "up" if close >= ma20 else "down", ma20)
    )
    if close >= max(recent20):
        tags.append(("high_20d", "近20日新高", "位置", "up", close))
    if close <= min(recent20):
        tags.append(("low_20d", "近20日新低", "位置", "down", close))
    if avg_volume and volumes[-1] >= avg_volume * 1.5:
        tags.append(
            ("volume_up" if change >= 0 else "volume_down",
             "放量上涨" if change >= 0 else "放量下跌",
             "量价", "up" if change >= 0 else "down", volumes[-1] / avg_volume)
        )
    elif avg_volume and volumes[-1] <= avg_volume * 0.6:
        tags.append(("low_volume", "明显缩量", "量价", "neutral", volumes[-1] / avg_volume))
    if volatility >= 45:
        tags.append(("high_volatility", "高波动", "波动", "neutral", volatility))
    elif volatility <= 18:
        tags.append(("low_volatility", "低波动", "波动", "neutral", volatility))
    if change > 9.8:
        tags.append(("limit_up", "当日涨停", "行情", "up", change))
    elif change < -9.8:
        tags.append(("limit_down", "当日跌停", "行情", "down", change))

    direction, days, trend_label = tolerant_trend_label(rows)
    if days >= 2:
        tags.append(
            ("tolerant_rise" if direction == "上涨" else "tolerant_fall",
             trend_label,
             "趋势", "up" if direction == "上涨" else "down", days)
        )
    if not tags:
        tags.append(("sideways", "震荡整理", "趋势", "neutral", None))
    return tags


async def refresh_one(connection: asyncpg.Connection, code: str) -> int:
    rows = await connection.fetch(
        """
        SELECT trade_date,close,change_pct,volume
        FROM daily_quotes WHERE stock_code=$1
        ORDER BY trade_date DESC LIMIT 60
        """,
        code,
    )
    history = list(reversed(rows))
    as_of = history[-1]["trade_date"] if history else None
    values = [
        (code, key, name, category, direction, value, as_of)
        for key, name, category, direction, value in build_tags(history)
    ]
    await connection.execute(
        "DELETE FROM stock_tags WHERE stock_code=$1 AND source='system'", code
    )
    await connection.executemany(
        """
        INSERT INTO stock_tags(
          stock_code,tag_key,tag_name,category,direction,value,as_of,source
        ) VALUES($1,$2,$3,$4,$5,$6,$7,'system')
        """,
        values,
    )
    return len(values)


async def refresh(connection: asyncpg.Connection) -> tuple[int, int]:
    rows = await connection.fetch(
        """
        SELECT stock_code,trade_date,close,change_pct,volume
        FROM (
          SELECT stock_code,trade_date,close,change_pct,volume,
            row_number() OVER (PARTITION BY stock_code ORDER BY trade_date DESC) AS rn
          FROM daily_quotes
        ) recent
        WHERE rn <= 60
        ORDER BY stock_code,trade_date
        """
    )
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["stock_code"].strip()].append(row)
    stocks = await connection.fetch("SELECT code FROM stocks ORDER BY code")
    values = []
    for stock in stocks:
        code = stock["code"].strip()
        history = grouped.get(code, [])
        as_of = history[-1]["trade_date"] if history else None
        for key, name, category, direction, value in build_tags(history):
            values.append((code, key, name, category, direction, value, as_of))

    async with connection.transaction():
        await connection.execute("DELETE FROM stock_tags WHERE source='system'")
        await connection.executemany(
            """
            INSERT INTO stock_tags(
              stock_code,tag_key,tag_name,category,direction,value,as_of,source
            ) VALUES($1,$2,$3,$4,$5,$6,$7,'system')
            """,
            values,
        )
    return len(stocks), len(values)


async def main():
    connection = await asyncpg.connect(DATABASE_URL)
    stock_count, tag_count = await refresh(connection)
    await connection.close()
    print(f"标签刷新完成：{stock_count} 只股票，{tag_count} 个标签", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
