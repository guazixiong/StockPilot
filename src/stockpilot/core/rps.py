"""RPS（欧奈尔相对强度）横截面排名引擎。

借鉴 Sequoia-X RpsBreakoutStrategy：全市场每只股票近 N 日涨幅 →
百分位排名 = RPS；RPS≥阈值 且 现价接近 N 日高点 → 强度突破入选。
"""
from __future__ import annotations

from typing import Dict, List

from .models import KLine


def rps_breakout(market: Dict[str, List[KLine]], period: int = 120,
                 rps_min: float = 90.0, close_to_high: float = 0.90
                 ) -> List[Dict[str, float]]:
    """market: {code: klines}。返回按 RPS 降序的入选列表。

    每项: {code, rps, pct_change, close, roll_high}
    规则（与 Sequoia 等价）：
      - pct_change = close[-1]/close[-1-period] - 1
      - rps = pct_change 在全市场的百分位（0~100）
      - close >= period 日滚动最高价 × close_to_high
    """
    rows: List[Dict[str, float]] = []
    for code, kls in market.items():
        if len(kls) < period + 1:
            continue
        base = kls[-1 - period].close
        if not base:
            continue
        pct = (kls[-1].close / base - 1) * 100
        highs = [k.high for k in kls[-period:]]
        roll_high = max(highs) if highs else kls[-1].close
        rows.append({"code": code, "pct_change": round(pct, 2),
                     "close": kls[-1].close, "roll_high": roll_high})
    if not rows:
        return []
    # 百分位排名（0~100）
    sorted_rows = sorted(rows, key=lambda r: r["pct_change"])
    n = len(sorted_rows)
    for i, r in enumerate(sorted_rows):
        r["rps"] = round((i + 1) / n * 100, 1)
    # 过滤：RPS 达标 + 接近区间高点
    out = [r for r in sorted_rows
           if r["rps"] >= rps_min and r["roll_high"] > 0
           and r["close"] >= r["roll_high"] * close_to_high]
    out.sort(key=lambda r: r["rps"], reverse=True)
    return out
