"""持仓与交易记录的纯计算（可单测，无 Qt 依赖）。"""
from __future__ import annotations

from typing import Dict, List, Optional


def position_metrics(pos: Dict, quote) -> Dict:
    """单条持仓的实时指标。pos: {name, cost, qty, date}; quote 可为 None。"""
    cost = float(pos.get("cost") or 0)
    qty = float(pos.get("qty") or 0)
    price = quote.price if quote and quote.price is not None else None
    prev_close = quote.prev_close if quote and quote.prev_close is not None else None
    market_value = price * qty if price is not None else None
    pnl = (price - cost) * qty if price is not None else None
    pnl_pct = (price / cost - 1) * 100 if price and cost > 0 else None
    today_pnl = (price - prev_close) * qty \
        if price is not None and prev_close is not None else None
    today_pnl_pct = (price / prev_close - 1) * 100 \
        if price and prev_close else None
    return {
        "price": price, "market_value": market_value, "pnl": pnl,
        "pnl_pct": pnl_pct, "today_pnl": today_pnl,
        "today_pnl_pct": today_pnl_pct,
    }


def portfolio_summary(rows: List[Dict]) -> Dict:
    """持仓汇总。rows 为 position_metrics 输出列表。"""
    mv = sum(r["market_value"] or 0 for r in rows)
    pnl = sum(r["pnl"] or 0 for r in rows)
    today = sum(r["today_pnl"] or 0 for r in rows)
    cost = 0.0
    return {"market_value": mv, "pnl": pnl, "today_pnl": today}


def journal_stats(records: List[Dict]) -> Dict:
    """交易流水统计。record: {side: buy/sell, price, qty, fee, amount?}。"""
    buy = sell = fee = 0.0
    for r in records:
        price = float(r.get("price") or 0)
        qty = float(r.get("qty") or 0)
        f = float(r.get("fee") or 0)
        amount = float(r.get("amount")) if r.get("amount") is not None \
            else price * qty
        fee += f
        if str(r.get("side")) == "buy":
            buy += amount
        else:
            sell += amount
    return {"buy": buy, "sell": sell, "fee": fee, "net": buy - sell + fee}
