"""筹码分布（Cyq），基于日 K 近似换手率衰减模型。

经典简化算法：从 N 日前起，每日按换手率将历史筹码向当日价格迁移；
得到每根K线的成本分布 → 获利盘比例 / 平均成本 / 90% 成本区间 / 集中度。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .models import KLine

PRICE_BUCKETS = 60          # 价格分桶数
DECAY_DEFAULT = 2.0          # 衰减系数（每日迁移比例上限的平滑）


def cyq_distribution(klines: List[KLine], lookback: int = 120,
                     buckets: int = PRICE_BUCKETS) -> Dict[str, Optional[float]]:
    """返回当前筹码分布摘要（None 表示数据不足）。

    keys: profit_ratio(获利盘%), avg_cost, cost_low(90%分位), cost_high,
          concentration(90%区间宽度/价格%), near_current(现价±5%筹码占比%)
    """
    if len(klines) < 30:
        return {k: None for k in ("profit_ratio", "avg_cost", "cost_low",
                                  "cost_high", "concentration", "near_current")}
    data = klines[-lookback:]
    lo = min(k.low for k in data)
    hi = max(k.high for k in data)
    if hi <= lo:
        return {k: None for k in ("profit_ratio", "avg_cost", "cost_low",
                                  "cost_high", "concentration", "near_current")}
    width = (hi - lo) / buckets

    # 成本分布直方图
    dist = [0.0] * buckets
    # 初始：均匀分布太假，用首日收盘附近集中
    for i in range(buckets):
        dist[i] = 1.0
    total = float(buckets)

    for k in data:
        turnover = (k.turnover / 100.0) if k.turnover else None
        if turnover is None:
            # 无换手率时用经验近似：量/5日均量 → 粗略 0.5%~5%
            turnover = 0.02
        turnover = min(max(turnover, 0.001), 0.95)
        # 新筹码集中在当日均价附近（3 个桶）
        avg_px = ((k.high + k.low + k.close) / 3 - lo) / width
        center = int(avg_px)
        add_per_bucket = turnover * total / 3
        for b in (center - 1, center, center + 1):
            if 0 <= b < buckets:
                dist[b] += add_per_bucket
                total += add_per_bucket
        # 旧筹码按换手衰减
        keep = 1.0 - turnover
        dist = [v * keep for v in dist]
        total = sum(dist)

    if total <= 0:
        return {k: None for k in ("profit_ratio", "avg_cost", "cost_low",
                                  "cost_high", "concentration", "near_current")}

    cur = data[-1].close
    cur_bucket = int((cur - lo) / width)
    # 获利盘：成本低于现价的筹码占比
    profit = sum(dist[:cur_bucket]) / total * 100
    # 平均成本
    avg_cost = sum((lo + (i + 0.5) * width) * dist[i]
                   for i in range(buckets)) / total
    # 90% 成本区间（去掉两端各 5%）
    acc, lo_i = 0.0, 0
    for i, v in enumerate(dist):
        acc += v / total
        if acc >= 0.05:
            lo_i = i
            break
    acc, hi_i = 0.0, buckets - 1
    for i in range(buckets - 1, -1, -1):
        acc += dist[i] / total
        if acc >= 0.05:
            hi_i = i
            break
    cost_low = lo + lo_i * width
    cost_high = lo + (hi_i + 1) * width
    span = cost_high - cost_low
    concentration = (span / cur * 100) if cur > 0 else None  # 越小越集中
    # 现价 ±5% 筹码占比（支撑/压力密集程度）
    near = sum(dist[i] for i in range(buckets)
               if abs(lo + (i + 0.5) * width - cur) <= cur * 0.05) / total * 100

    return {
        "profit_ratio": round(profit, 2),
        "avg_cost": round(avg_cost, 2),
        "cost_low": round(cost_low, 2),
        "cost_high": round(cost_high, 2),
        "concentration": round(concentration, 2) if concentration else None,
        "near_current": round(near, 2),
    }


def cyq_curve(klines: List[KLine], lookback: int = 120,
              buckets: int = PRICE_BUCKETS):
    """返回 (prices, weights) 用于绘制筹码分布横图；None 表示数据不足。"""
    if len(klines) < 30:
        return None
    data = klines[-lookback:]
    lo = min(k.low for k in data)
    hi = max(k.high for k in data)
    if hi <= lo:
        return None
    width = (hi - lo) / buckets
    dist = [1.0] * buckets
    total = float(buckets)
    for k in data:
        turnover = (k.turnover / 100.0) if k.turnover else 0.02
        turnover = min(max(turnover, 0.001), 0.95)
        center = int((((k.high + k.low + k.close) / 3) - lo) / width)
        add_per = turnover * total / 3
        for b in (center - 1, center, center + 1):
            if 0 <= b < buckets:
                dist[b] += add_per
                total += add_per
        keep = 1.0 - turnover
        dist = [v * keep for v in dist]
        total = sum(dist)
    prices = [lo + (i + 0.5) * width for i in range(buckets)]
    weights = [v / total if total else 0 for v in dist]
    return prices, weights
