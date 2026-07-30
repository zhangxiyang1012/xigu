"""由个股日线聚合生成90日行业轮动节奏与风险指标。"""

import asyncio
import os
from collections import defaultdict

import asyncpg

DATABASE_URL = os.environ["DATABASE_URL"]


def percentile(values: list[float], value: float) -> float:
    if len(values) <= 1:
        return 50.0
    return sum(candidate <= value for candidate in values) / len(values) * 100


def tolerant_phase(points: list[dict], index: int) -> tuple[str, int, str]:
    best_direction, best_days = "平", 0
    prices = [point["industry_index"] for point in points[: index + 1]]
    for days in range(1, min(12, len(prices) - 1) + 1):
        part = prices[-days - 1 :]
        direction = "上涨" if part[-1] >= part[0] else "下跌"
        opposite = sum(
            (direction == "上涨" and current < previous)
            or (direction == "下跌" and current > previous)
            for previous, current in zip(part, part[1:])
        )
        if opposite <= 1:
            best_direction, best_days = direction, days

    if len(prices) >= 6 and abs(prices[-1] / prices[-6] - 1) < 0.01:
        phase = "震荡"
    elif best_direction == "上涨":
        phase = "启动" if best_days <= 3 else "上升" if best_days <= 7 else "过热"
    elif best_direction == "下跌":
        phase = "退潮" if best_days <= 3 else "下跌"
    else:
        phase = "震荡"
    return phase, best_days, best_direction


async def refresh(connection: asyncpg.Connection) -> tuple[int, int]:
    rows = await connection.fetch(
        """
        WITH quotes AS (
          SELECT s.industry_name,q.stock_code,q.trade_date,q.close,
            q.change_pct,q.amount,
            avg(q.close) OVER (
              PARTITION BY q.stock_code ORDER BY q.trade_date
              ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
            ) AS ma20,
            count(*) OVER (
              PARTITION BY q.stock_code ORDER BY q.trade_date
              ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
            ) AS ma20_days
          FROM daily_quotes q
          JOIN stocks s ON s.code=q.stock_code
          WHERE s.industry_name IS NOT NULL
            AND s.industry_name <> ''
            AND q.trade_date >= current_date - interval '220 days'
        )
        SELECT industry_name,trade_date,count(*) AS member_count,
          avg(change_pct)::float AS avg_change_pct,
          sum(amount)::float AS amount,
          100.0 * avg(
            CASE WHEN ma20_days >= 20 AND close >= ma20 THEN 1.0 ELSE 0.0 END
          ) AS above_ma20_pct,
          count(*) FILTER (WHERE change_pct >= 9.8) AS limit_up_count,
          count(*) FILTER (WHERE change_pct > 0) AS up_count,
          count(*) FILTER (WHERE change_pct < 0) AS down_count,
          max(change_pct)::float AS leader_change
        FROM quotes
        WHERE trade_date >= current_date - interval '170 days'
        GROUP BY industry_name,trade_date
        ORDER BY industry_name,trade_date
        """
    )
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["industry_name"]].append(dict(row))

    for points in grouped.values():
        industry_index = 100.0
        for index, point in enumerate(points):
            industry_index *= 1 + point["avg_change_pct"] / 100
            point["industry_index"] = industry_index
            anchor = points[max(0, index - 20)]["industry_index"]
            point["return_20d"] = (industry_index / anchor - 1) * 100 if anchor else 0
            previous_amounts = [item["amount"] for item in points[max(0, index - 5) : index]]
            baseline = sum(previous_amounts) / len(previous_amounts) if previous_amounts else point["amount"]
            point["amount_ratio"] = point["amount"] / baseline if baseline else 1

    by_date: dict[object, list[dict]] = defaultdict(list)
    for points in grouped.values():
        for point in points:
            by_date[point["trade_date"]].append(point)
    for day_points in by_date.values():
        returns = [point["return_20d"] for point in day_points]
        breadths = [point["above_ma20_pct"] for point in day_points]
        amounts = [point["amount_ratio"] for point in day_points]
        strong = [point["limit_up_count"] / max(1, point["member_count"]) for point in day_points]
        leaders = [point["leader_change"] for point in day_points]
        for point in day_points:
            point["rotation_score"] = (
                0.30 * percentile(returns, point["return_20d"])
                + 0.25 * percentile(breadths, point["above_ma20_pct"])
                + 0.20 * percentile(amounts, point["amount_ratio"])
                + 0.15 * percentile(
                    strong, point["limit_up_count"] / max(1, point["member_count"])
                )
                + 0.10 * percentile(leaders, point["leader_change"])
            )

    values = []
    for industry_name, points in grouped.items():
        for index, point in enumerate(points):
            phase, phase_days, direction = tolerant_phase(points, index)
            recent = points[max(0, index - 5) : index + 1]
            risk_score = 10.0
            reasons = []
            if point["above_ma20_pct"] < 40:
                risk_score += 25
                reasons.append("多数成分股跌破20日线")
            if direction == "下跌":
                risk_score += 20
                reasons.append(f"处于{phase_days}日下行节奏")
            if phase == "过热":
                risk_score += 25
                reasons.append(f"连续上行已进入第{phase_days}日")
            if len(recent) >= 4 and all(
                recent[offset]["rotation_score"] > recent[offset + 1]["rotation_score"]
                for offset in range(len(recent) - 4, len(recent) - 1)
            ):
                risk_score += 20
                reasons.append("轮动强度连续回落")
            if (
                len(recent) >= 5
                and point["industry_index"] >= recent[0]["industry_index"]
                and point["above_ma20_pct"] + 5 < recent[0]["above_ma20_pct"]
            ):
                risk_score += 20
                reasons.append("指数与成分股广度背离")
            if point["amount_ratio"] > 1.3 and abs(point["avg_change_pct"]) < 0.3:
                risk_score += 15
                reasons.append("放量但价格推进不足")
            if point["rotation_score"] < 35:
                risk_score += 15
                reasons.append("行业轮动排名靠后")
            risk_score = min(100.0, risk_score)
            risk_level = "高" if risk_score >= 65 else "中" if risk_score >= 35 else "低"
            values.append(
                (
                    industry_name,
                    point["trade_date"],
                    point["member_count"],
                    point["avg_change_pct"],
                    point["industry_index"],
                    point["return_20d"],
                    point["amount"],
                    point["amount_ratio"],
                    point["above_ma20_pct"],
                    point["limit_up_count"],
                    point["up_count"],
                    point["down_count"],
                    point["rotation_score"],
                    phase,
                    phase_days,
                    risk_score,
                    risk_level,
                    reasons or ["暂无明显退潮信号"],
                )
            )

    async with connection.transaction():
        await connection.execute("TRUNCATE industry_daily_metrics")
        await connection.executemany(
            """
            INSERT INTO industry_daily_metrics(
              industry_name,trade_date,member_count,avg_change_pct,
              industry_index,return_20d,amount,amount_ratio,above_ma20_pct,
              limit_up_count,up_count,down_count,rotation_score,phase,
              phase_days,risk_score,risk_level,risk_reasons
            ) VALUES(
              $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18
            )
            """,
            values,
        )
    return len(grouped), len(values)


async def main():
    connection = await asyncpg.connect(DATABASE_URL)
    industry_count, row_count = await refresh(connection)
    await connection.close()
    print(f"行业轮动刷新完成：{industry_count} 个行业，{row_count} 条日度指标")


if __name__ == "__main__":
    asyncio.run(main())
