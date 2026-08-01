from statistics import mean


def avg(values):
    return mean(values) if values else 0.0


def consecutive_pullback(rows):
    days = 0
    for row in reversed(rows[-5:]):
        if float(row["change_pct"]) <= 0:
            days += 1
        else:
            break
    return days


async def refresh_one(connection, code):
    rows = await connection.fetch(
        """SELECT trade_date,open,high,low,close,change_pct,volume
           FROM daily_quotes WHERE stock_code=$1 ORDER BY trade_date DESC LIMIT 90""", code)
    rows = list(reversed(rows))
    if len(rows) < 20:
        return False
    closes = [float(r["close"]) for r in rows]
    volumes = [float(r["volume"]) for r in rows]
    latest = rows[-1]
    close, change = closes[-1], float(latest["change_pct"])
    ma5, ma10, ma20 = avg(closes[-5:]), avg(closes[-10:]), avg(closes[-20:])
    vr5 = volumes[-1] / max(1, avg(volumes[-5:]))
    vr20 = volumes[-1] / max(1, avg(volumes[-20:]))
    true_ranges = []
    for index, row in enumerate(rows[-15:]):
        previous = float(rows[-16 + index]["close"]) if len(rows) >= 16 else float(row["close"])
        true_ranges.append(max(float(row["high"])-float(row["low"]), abs(float(row["high"])-previous), abs(float(row["low"])-previous)))
    atr14 = avg(true_ranges[-14:])
    dd20 = (close / max(closes[-20:]) - 1) * 100
    dd60 = (close / max(closes[-60:]) - 1) * 100
    pullback = consecutive_pullback(rows)
    stock = await connection.fetchrow("SELECT industry_name FROM stocks WHERE code=$1", code)
    industry = await connection.fetchrow(
        """SELECT rotation_score,risk_level FROM industry_daily_metrics
           WHERE industry_name=$1 ORDER BY trade_date DESC LIMIT 1""",
        stock["industry_name"] if stock else "")
    rank = await connection.fetchval(
        """SELECT rank FROM (SELECT industry_name,rank() OVER(ORDER BY rotation_score DESC) rank
           FROM industry_daily_metrics WHERE trade_date=(SELECT max(trade_date) FROM industry_daily_metrics)) x
           WHERE industry_name=$1""", stock["industry_name"] if stock else "")
    rotation = float(industry["rotation_score"]) if industry else 0
    risk = industry["risk_level"] if industry else "中"
    recent20 = rows[-20:]
    active = any(float(r["change_pct"]) > 9.8 for r in recent20)
    active = active or sum(1 for r in recent20 if float(r["change_pct"]) > 4 and float(r["volume"]) > avg(volumes[-20:])) >= 2
    bull = ma5 >= ma10 >= ma20 and close >= ma20
    turn = close > ma5 and close > float(rows[-2]["high"]) and 1.05 <= vr5 <= 1.8
    shrinking_pullback = 2 <= pullback <= 5 and vr20 <= .8 and close >= ma20
    blockers, buy_signals, sell_signals = [], [], []
    if risk == "高": blockers.append("行业轮动高风险")
    if close < ma20 and vr20 >= 1: blockers.append("放量跌破MA20")
    if change <= -9.8: blockers.append("当日跌停")
    if bull: buy_signals.append("均线多头且站上MA20")
    if shrinking_pullback: buy_signals.append(f"缩量回调{pullback}日")
    if turn: buy_signals.append("量价拐点确认")
    if active: buy_signals.append("近20日活跃核心候选")
    score = (20 if rank and rank <= 3 else 10 if rank and rank <= 10 else 0)
    score += 20 if bull else 8 if close >= ma20 else 0
    score += 20 if shrinking_pullback else 0
    score += 15 if turn else 0
    score += 15 if active else 0
    score += 10 if risk == "低" else 5 if risk == "中" else 0
    model = "无"
    if shrinking_pullback: model = "主线缩量回调"
    elif active and close > ma5: model = "活跃股二次参与"
    elif -30 <= dd60 <= -15 and close > ma5: model = "核心股深跌修复"
    if blockers: buy_level = "禁买"
    elif score >= 75 and turn: buy_level = "买入确认"
    elif score >= 65: buy_level = "候选"
    elif score >= 50: buy_level = "观察"
    else: buy_level = "弱信号"
    sell_score = 0
    if close < ma10 and vr20 >= 1: sell_score += 25; sell_signals.append("放量跌破MA10")
    if close < ma20: sell_score += 25; sell_signals.append("跌破MA20")
    if close < ma20 and vr20 >= 1: sell_score += 25; sell_signals.append("放量破位")
    if risk == "高": sell_score += 20; sell_signals.append("行业轮动高风险")
    if change <= -9.8: sell_score += 40; sell_signals.append("当日跌停")
    sell_level = "退出" if sell_score >= 60 else "减仓" if sell_score >= 35 else "预警" if sell_score >= 20 else "持有"
    defense = max(ma20, min(float(r["low"]) for r in rows[-5:]))
    await connection.execute(
        """INSERT INTO stock_discipline_signals(stock_code,trade_date,close,ma5,ma10,ma20,atr14,
        volume_ratio_5,volume_ratio_20,drawdown_20d,drawdown_60d,pullback_days,industry_rank,
        industry_rotation_score,industry_risk_level,buy_score,sell_score,buy_level,sell_level,buy_model,
        buy_signals,sell_signals,blockers,defense_price,stop_atr_price,calculated_at)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,now())
        ON CONFLICT(stock_code,trade_date) DO UPDATE SET close=excluded.close,ma5=excluded.ma5,ma10=excluded.ma10,
        ma20=excluded.ma20,atr14=excluded.atr14,volume_ratio_5=excluded.volume_ratio_5,
        volume_ratio_20=excluded.volume_ratio_20,drawdown_20d=excluded.drawdown_20d,
        drawdown_60d=excluded.drawdown_60d,pullback_days=excluded.pullback_days,industry_rank=excluded.industry_rank,
        industry_rotation_score=excluded.industry_rotation_score,industry_risk_level=excluded.industry_risk_level,
        buy_score=excluded.buy_score,sell_score=excluded.sell_score,buy_level=excluded.buy_level,
        sell_level=excluded.sell_level,buy_model=excluded.buy_model,buy_signals=excluded.buy_signals,
        sell_signals=excluded.sell_signals,blockers=excluded.blockers,defense_price=excluded.defense_price,
        stop_atr_price=excluded.stop_atr_price,calculated_at=now()""",
        code, latest["trade_date"], close, ma5, ma10, ma20, atr14, vr5, vr20, dd20, dd60,
        pullback, rank, rotation, risk, score, sell_score, buy_level, sell_level, model,
        buy_signals, sell_signals, blockers, defense, close-2*atr14)
    return True


async def refresh(connection):
    codes = await connection.fetch("SELECT code FROM stocks ORDER BY code")
    count = 0
    for row in codes:
        try:
            count += int(await refresh_one(connection, row["code"]))
        except Exception as exc:
            print(f"discipline signal failed {row['code']}: {exc}")
    return count
