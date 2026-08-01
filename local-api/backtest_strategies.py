"""三年纪律策略回测：严格按历史时点计算，信号次日开盘成交。"""

import asyncio
import json
import os
from datetime import date, timedelta
from statistics import mean, median

import asyncpg

from refresh_industries import refresh as refresh_industries

DATABASE_URL = os.environ["DATABASE_URL"]
HORIZONS = {"1月": 21, "3月": 63, "半年": 126, "1年": 252, "2年": 504, "3年": 756}
BUY_COST = 0.0008   # 佣金0.03% + 滑点0.05%
SELL_COST = 0.0013  # 佣金0.03% + 印花税0.05% + 滑点0.05%


def avg(values):
    return mean(values) if values else 0.0


def metrics(rows, index, industry):
    part = rows[max(0, index - 89): index + 1]
    if len(part) < 20:
        return None
    closes = [float(row["close"]) for row in part]
    volumes = [float(row["volume"]) for row in part]
    close = closes[-1]
    ma5, ma10, ma20 = avg(closes[-5:]), avg(closes[-10:]), avg(closes[-20:])
    vr5 = volumes[-1] / max(1, avg(volumes[-5:]))
    vr20 = volumes[-1] / max(1, avg(volumes[-20:]))
    true_ranges = []
    for local_index, row in enumerate(part[-15:]):
        absolute_index = len(part) - min(15, len(part)) + local_index
        previous = float(part[max(0, absolute_index - 1)]["close"])
        true_ranges.append(max(float(row["high"]) - float(row["low"]), abs(float(row["high"]) - previous), abs(float(row["low"]) - previous)))
    atr14 = avg(true_ranges[-14:])
    pullback = 0
    for row in reversed(part[-5:]):
        if float(row["change_pct"]) <= 0: pullback += 1
        else: break
    active = any(float(row["change_pct"]) > 9.8 for row in part[-20:])
    active = active or sum(float(row["change_pct"]) > 4 and float(row["volume"]) > avg(volumes[-20:]) for row in part[-20:]) >= 2
    bull = ma5 >= ma10 >= ma20 and close >= ma20
    turn = close > ma5 and close > float(part[-2]["high"]) and 1.05 <= vr5 <= 1.8
    shrinking = 2 <= pullback <= 5 and vr20 <= .8 and close >= ma20
    risk = industry["risk_level"] if industry else "中"
    rank = industry["rank"] if industry else None
    blockers, buy_rules, sell_rules = [], [], []
    if risk == "高": blockers.append("B2 行业轮动高风险")
    if close < ma20 and vr20 >= 1: blockers.append("B0 放量跌破MA20")
    if float(part[-1]["change_pct"]) <= -9.8: blockers.append("B0 当日跌停")
    if avg([float(row["amount"]) for row in part[-20:]]) < 100_000_000: blockers.append("B0 近20日成交额低于1亿元")
    if bull: buy_rules.append("B3 均线多头且站上MA20")
    if shrinking: buy_rules.append(f"B4-A 缩量回调{pullback}日")
    if turn: buy_rules.append("B4 量价拐点确认")
    if active: buy_rules.append("B4-C 近20日活跃核心候选")
    score = 20 if rank and rank <= 3 else 10 if rank and rank <= 10 else 0
    score += 20 if bull else 8 if close >= ma20 else 0
    score += 20 if shrinking else 0
    score += 15 if turn else 0
    score += 15 if active else 0
    score += 10 if risk == "低" else 5 if risk == "中" else 0
    model = "主线缩量回调" if shrinking else "活跃股二次参与" if active and close > ma5 else "核心股深跌修复" if -30 <= (close / max(closes[-60:]) - 1) * 100 <= -15 and close > ma5 else "无"
    sell_score = 0
    if close < ma10 and vr20 >= 1: sell_score += 25; sell_rules.append("S1 放量跌破MA10")
    if close < ma20: sell_score += 25; sell_rules.append("S1 跌破MA20")
    if close < ma20 and vr20 >= 1: sell_score += 25; sell_rules.append("S0 放量破位")
    if risk == "高": sell_score += 20; sell_rules.append("S2 行业轮动高风险")
    if float(part[-1]["change_pct"]) <= -9.8: sell_score += 40; sell_rules.append("S0 当日跌停")
    atr_stop = max(0.0, close - 1.5 * atr14)
    support = max((value for value in (ma20, min(float(row["low"]) for row in part[-5:])) if 0 < value < close), default=0.0)
    return {"close": close, "ma5": ma5, "score": score, "turn": turn, "blockers": blockers,
            "buy_rules": buy_rules, "sell_score": sell_score, "sell_rules": sell_rules,
            "model": model, "defense": max(support, atr_stop)}


def forward_returns(rows, execution_index, side):
    base = float(rows[execution_index]["open"]) * (1 + BUY_COST if side == "buy" else 1 - SELL_COST)
    values = {}
    for label, days in HORIZONS.items():
        target = execution_index + days
        if target >= len(rows):
            values[label] = None
        else:
            future = float(rows[target]["close"]) * (1 - SELL_COST)
            raw = (future / base - 1) * 100
            values[label] = round(raw if side == "buy" else -raw, 4)
    return values


async def run(connection, stock_codes=None):
    end_date = await connection.fetchval("SELECT max(trade_date) FROM daily_quotes")
    start_date = end_date - timedelta(days=1095)
    industry_range = await connection.fetchrow("SELECT min(trade_date) first_date,max(trade_date) last_date FROM industry_daily_metrics")
    if not industry_range or not industry_range["first_date"] or industry_range["first_date"] > start_date or industry_range["last_date"] < end_date:
        await refresh_industries(connection, history_days=1210, output_days=1110)
    industry_rows = await connection.fetch("""SELECT industry_name,trade_date,risk_level,
      rank() OVER(PARTITION BY trade_date ORDER BY rotation_score DESC) rank
      FROM industry_daily_metrics WHERE trade_date >= $1""", start_date - timedelta(days=90))
    industry = {(row["industry_name"], row["trade_date"]): dict(row) for row in industry_rows}
    run_id = await connection.fetchval("""INSERT INTO strategy_backtest_runs(start_date,end_date,parameters)
      VALUES($1,$2,$3::jsonb) RETURNING id""", start_date, end_date, json.dumps({
        "entry": "买入确认后下一交易日开盘", "exit": "减仓/退出/硬止损/时间止损后下一交易日开盘",
        "horizons": HORIZONS, "buy_cost": BUY_COST, "sell_cost": SELL_COST,
        "scope": "selected" if stock_codes else "all", "stock_codes": stock_codes or [],
        "unavailable_rules": ["上市不足120日（上市日期缺失）", "市场系统性风险（尚无历史市场环境表）"]
      }, ensure_ascii=False))
    if stock_codes:
        stocks = await connection.fetch("SELECT code,name,industry_name FROM stocks WHERE code::text=ANY($1::text[]) ORDER BY code", stock_codes)
    else:
        stocks = await connection.fetch("SELECT code,name,industry_name FROM stocks ORDER BY code")
    events, trades = [], []
    try:
        for stock_no, stock in enumerate(stocks, 1):
            rows = list(await connection.fetch("""SELECT trade_date,open,high,low,close,change_pct,volume,amount
              FROM daily_quotes WHERE stock_code=$1 AND trade_date >= $2 ORDER BY trade_date""",
              stock["code"], start_date - timedelta(days=150)))
            holding = None
            for index in range(20, len(rows) - 1):
                if rows[index]["trade_date"] < start_date: continue
                m = metrics(rows, index, industry.get((stock["industry_name"], rows[index]["trade_date"])))
                if not m: continue
                if holding is None and m["score"] >= 75 and m["turn"] and not m["blockers"]:
                    execution_index = index + 1
                    price = float(rows[execution_index]["open"]) * (1 + BUY_COST)
                    holding = {"signal_index": index, "buy_index": execution_index, "price": price,
                               "defense": m["defense"], "strategy": m["model"], "rules": m["buy_rules"]}
                    events.append((run_id, stock["code"], "buy", rows[index]["trade_date"], rows[execution_index]["trade_date"], price,
                                   "买入确认", m["model"], m["buy_rules"], json.dumps(forward_returns(rows, execution_index, "buy"), ensure_ascii=False)))
                    continue
                if holding is not None:
                    held = index - holding["buy_index"]
                    pnl = (m["close"] / holding["price"] - 1) * 100
                    rules = list(m["sell_rules"])
                    if m["close"] < holding["defense"]: rules.append("S0 跌破买入时预设防守位")
                    if held >= 10 and pnl <= 0: rules.append("S4 持有10日仍未走强")
                    if pnl >= 8 and m["close"] < m["ma5"]: rules.append("S3 盈利后跌破MA5移动止盈")
                    actionable = m["sell_score"] >= 35 or any(rule.startswith(("S0", "S3", "S4")) for rule in rules)
                    if actionable:
                        execution_index = index + 1
                        price = float(rows[execution_index]["open"]) * (1 - SELL_COST)
                        level = "退出" if m["sell_score"] >= 60 or any(rule.startswith("S0") for rule in rules) else "减仓"
                        strategy = "；".join(dict.fromkeys(rule.split(" ", 1)[0] for rule in rules)) or "纪律卖出"
                        events.append((run_id, stock["code"], "sell", rows[index]["trade_date"], rows[execution_index]["trade_date"], price,
                                       level, strategy, rules, json.dumps(forward_returns(rows, execution_index, "sell"), ensure_ascii=False)))
                        trades.append((run_id, stock["code"], rows[holding["signal_index"]]["trade_date"], rows[holding["buy_index"]]["trade_date"],
                                       holding["price"], holding["strategy"], holding["rules"], rows[index]["trade_date"], rows[execution_index]["trade_date"],
                                       price, strategy, rules, held, (price / holding["price"] - 1) * 100, "closed"))
                        holding = None
            if holding is not None:
                last = rows[-1]
                trades.append((run_id, stock["code"], rows[holding["signal_index"]]["trade_date"], rows[holding["buy_index"]]["trade_date"],
                               holding["price"], holding["strategy"], holding["rules"], None, None, None, None, [], len(rows)-1-holding["buy_index"],
                               (float(last["close"]) * (1-SELL_COST) / holding["price"] - 1) * 100, "open"))
            if stock_no % 500 == 0: print(f"已回测 {stock_no}/{len(stocks)} 只股票")

        for offset in range(0, len(events), 2000):
            await connection.executemany("""INSERT INTO strategy_backtest_events(run_id,stock_code,side,signal_date,execution_date,
              execution_price,signal_level,strategy_name,matched_rules,forward_returns) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)""", events[offset:offset+2000])
        for offset in range(0, len(trades), 2000):
            await connection.executemany("""INSERT INTO strategy_backtest_trades(run_id,stock_code,buy_signal_date,buy_date,buy_price,buy_strategy,
              buy_rules,sell_signal_date,sell_date,sell_price,sell_strategy,sell_rules,holding_days,return_pct,status)
              VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)""", trades[offset:offset+2000])
        summaries = []
        for side in ("buy", "sell"):
            selected = [event for event in events if event[2] == side]
            for label, days in HORIZONS.items():
                values = [json.loads(event[9])[label] for event in selected if json.loads(event[9]).get(label) is not None]
                summaries.append((run_id, side, label, days, len(values), avg(values), median(values) if values else 0,
                                  100 * sum(value > 0 for value in values) / max(1, len(values)), max(values, default=0), min(values, default=0)))
        await connection.executemany("""INSERT INTO strategy_backtest_summaries(run_id,side,horizon,trading_days,sample_count,
          avg_return_pct,median_return_pct,win_rate_pct,best_return_pct,worst_return_pct) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""", summaries)
        await connection.execute("""UPDATE strategy_backtest_runs SET status='complete',finished_at=now(),stock_count=$2,event_count=$3,trade_count=$4 WHERE id=$1""",
                                 run_id, len(stocks), len(events), len(trades))
        return run_id, len(events), len(trades)
    except Exception as exc:
        await connection.execute("UPDATE strategy_backtest_runs SET status='failed',finished_at=now(),error=$2 WHERE id=$1", run_id, str(exc))
        raise


async def main():
    connection = await asyncpg.connect(DATABASE_URL, command_timeout=900)
    result = await run(connection)
    await connection.close()
    print(f"回测完成：run={result[0]} events={result[1]} trades={result[2]}")


if __name__ == "__main__":
    asyncio.run(main())
