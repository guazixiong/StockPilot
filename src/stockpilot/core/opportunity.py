"""机会评分：把"这是不是一个值得做的机会"量化为 0~100 分 + A/B/C 评级。

纯函数，监听与策略选股共用；因子口径见设计文档 §21.1。
v4.0：四因子权重可配置（risk/trend/volume/rr）+ 流动性惩罚门槛。
v4.3：新增第五因子 money（主力资金净流入占成交额比，Quote.main_inflow）。
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

DEFAULT_WEIGHTS = {"risk": 0.5, "trend": 0.12, "volume": 0.08, "rr": 0.10,
                   "money": 0.06}


def opportunity_score(sig, ind: Dict[str, object], quote,
                      weights: Optional[Dict[str, float]] = None,
                      min_amount_wan: float = 0.0) -> Tuple[float, str]:
    """sig: TradeSignal（需 price/stop/target/risk_score）；ind: indicators 上下文。

    weights: 五因子权重（risk/trend/volume/rr/money），默认 DEFAULT_WEIGHTS，
    各因子按"与基准权重的比例"缩放贡献；
    min_amount_wan: 流动性门槛（成交额万元），低于此值线性扣分（至多20）。
    """
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        for k in w:
            try:
                w[k] = float(weights.get(k, w[k]))
            except (TypeError, ValueError):
                pass
    score = 60.0
    price, stop, target = sig.price, sig.stop_price, sig.target_price

    # 1) 风险反向：风险分越高越扣
    score -= w["risk"] * (sig.risk_score or 0)

    # 2) 趋势强度（trend 权重相对基准 0.12 缩放）
    t_scale = w["trend"] / 0.12
    if ind.get("ma_bull"):
        score += 12 * t_scale
    ma20 = ind.get("ma20")
    if isinstance(ma20, (int, float)) and price and price > ma20:
        score += 6 * t_scale

    # 3) 量能：温和放量最佳（1.0~2.5 倍均量）；过度放量惩罚
    vr = quote.volume_ratio if quote is not None else None
    if vr is None:
        vr = ind.get("vol_vs_ma5")
    v_scale = w["volume"] / 0.08
    if isinstance(vr, (int, float)):
        if 1.0 <= vr <= 2.5:
            score += 8 * v_scale
        elif vr > 4:
            score -= 5 * v_scale

    # 4) 潜在盈亏比：rr = (目标-现价)/(现价-止损)（rr 权重相对基准 0.10 缩放）
    if price and stop and target and price > stop:
        rr = (target - price) / (price - stop)
        score += min(rr * 8, 15) * (w["rr"] / 0.10)

    # 5) 主力资金（v4.3）：净流入占当日成交额比例 x 强度加分（±8 封顶）
    m_scale = w["money"] / 0.06
    inflow = quote.main_inflow if quote is not None else None
    amount = quote.amount if quote is not None else None
    if isinstance(inflow, (int, float)) and isinstance(amount, (int, float)) \
            and amount > 0:
        ratio = inflow / amount          # 万元/万元，无单位
        if ratio > 0:
            score += min(ratio * 40, 8) * m_scale
        else:
            score += max(ratio * 40, -8) * m_scale

    # 6) 流动性惩罚：成交额不足门槛线性扣（默认门槛 5000 万）
    if min_amount_wan > 0 and amount is not None and amount < min_amount_wan:
        score -= min((min_amount_wan - amount) / min_amount_wan * 20, 20)

    score = max(0.0, min(100.0, round(score, 1)))
    grade = "A" if score >= 75 else ("B" if score >= 60 else "C")
    return score, grade
