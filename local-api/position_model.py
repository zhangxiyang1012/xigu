"""可解释的动态仓位建议模型。"""


def market_regime(breadth: float, high_risk_share: float) -> tuple[str, float]:
    if breadth >= 55 and high_risk_share < 25:
        return "正常", 1.0
    if breadth >= 40 and high_risk_share < 40:
        return "谨慎", 0.6
    return "系统风险", 0.3


def target_position(*, close: float, defense: float, buy_level: str, industry_rank: int | None,
                    industry_risk: str, market_factor: float, portfolio_drawdown: float = 0) -> dict:
    stop_distance = max(0.02, (close - defense) / close) if close > 0 and 0 < defense < close else 1.0
    risk_weight = min(0.15, 0.008 / stop_distance)
    industry_factor = 0.0 if industry_risk == "高" else 1.2 if industry_rank and industry_rank <= 3 and industry_risk == "低" else 1.0 if industry_rank and industry_rank <= 10 else 0.5
    signal_factor = {"买入确认": 1.0, "候选": 0.5, "观察": 0.25}.get(buy_level, 0.0)
    drawdown_factor = 0.3 if portfolio_drawdown >= 12 else 0.6 if portfolio_drawdown >= 8 else 0.8 if portfolio_drawdown >= 5 else 1.0
    target = min(0.15, risk_weight * market_factor * industry_factor * signal_factor * drawdown_factor)
    return {"target_weight_pct": round(target * 100, 2), "risk_weight_pct": round(risk_weight * 100, 2),
            "stop_distance_pct": round(stop_distance * 100, 2), "industry_factor": industry_factor,
            "signal_factor": signal_factor, "market_factor": market_factor, "drawdown_factor": drawdown_factor}
