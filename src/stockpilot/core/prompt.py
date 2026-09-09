"""AI 上下文构建与对话模板（个股诊断 / 信号解读 / 回测解读 / 新闻解读）。"""
from __future__ import annotations

from typing import Dict, List, Optional

from .models import KLine, NewsItem, Quote, TradeSignal
from .indicators import kline_summary, signals_text

SYSTEM_PROMPT = (
    "你是一名严谨的A股证券投资分析助手。要求：\n"
    "1) 结论明确、分点陈述，用简体中文；\n"
    "2) 给出支持证据的同时必须给出反向证据与主要风险；\n"
    "3) 涉及买卖参考时给出具体价位区间与仓位建议（百分比）；\n"
    "4) 不夸大确定性，不承诺收益；\n"
    "5) 回复末尾固定加一行：以上分析由AI生成，仅供参考，不构成投资建议。"
)

DISCLAIMER = "以上分析由AI生成，仅供参考，不构成投资建议。"


def _f(x, nd: int = 2, suffix: str = "") -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):.{nd}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


# ---------------------------------------------------------------- 上下文

def build_stock_context(quote: Quote, ind: Dict[str, object],
                        klines: Optional[List[KLine]] = None,
                        news: Optional[List[NewsItem]] = None) -> str:
    parts = [
        f"【{quote.name} {quote.code}】现价 {_f(quote.price)} "
        f"涨跌 {_f(quote.change_pct, 2, '%')} 振幅 {_f(quote.amplitude, 2, '%')}",
        f"今开 {_f(quote.open)} 昨收 {_f(quote.prev_close)} "
        f"最高 {_f(quote.high)} 最低 {_f(quote.low)}",
        f"换手率 {_f(quote.turnover_rate, 2, '%')} 量比 {_f(quote.volume_ratio)} "
        f"成交额 {_f(quote.amount, 0, '万')}",
        f"主力净流入 {_f(quote.main_inflow, 0, '万')}",
        f"PE {_f(quote.pe)} PB {_f(quote.pb)} "
        f"流通市值 {_f(quote.float_mv, 0, '亿')} 总市值 {_f(quote.total_mv, 0, '亿')}",
        "技术指标: " + "；".join(
            f"{k.upper()}={_f(ind.get(k))}"
            for k in ("ma5", "ma10", "ma20", "ma60", "dif", "dea", "macd_hist",
                      "rsi6", "k", "d", "j", "boll_up", "boll_mid", "boll_low")),
    ]
    forms = signals_text(ind)
    if forms:
        parts.append("形态: " + "；".join(forms))
    if klines:
        parts.append("近10日K线:\n" + kline_summary(klines, 10))
    if news:
        parts.append("近期消息:\n" + "\n".join(
            f"- {n.date} {n.title}" for n in news[:8]))
    return "\n".join(parts)


def build_signal_context(sig: TradeSignal,
                         klines: Optional[List[KLine]] = None,
                         news: Optional[List[NewsItem]] = None) -> str:
    side = "买入机会" if sig.side == "buy" else "卖出提醒"
    parts = [
        f"【策略信号】{sig.name}({sig.code}) 信号方向: {side}",
        f"来源策略: {sig.strategy}   时间: {sig.time}",
        f"现价(参考买入成本): {_f(sig.price)}",
    ]
    if sig.side == "buy":
        parts.append(f"系统建议止损: {_f(sig.stop_price)}  建议目标: {_f(sig.target_price)}")
    parts.append(f"风险评分: {sig.risk_score}/100（越低越稳）")
    parts.append("命中规则: " + "；".join(sig.hit_rules))
    if sig.risk_notes:
        parts.append("风险提示: " + "；".join(sig.risk_notes))
    if klines:
        parts.append("近10日K线:\n" + kline_summary(klines, 10))
    if news:
        parts.append("近期消息:\n" + "\n".join(
            f"- {n.date} {n.title}" for n in news[:6]))
    return "\n".join(parts)


def build_backtest_context(result) -> str:
    losses = sorted(result.trades, key=lambda t: t.pnl_pct)[:10]
    parts = [f"【回测报告】策略: {result.strategy}",
             result.summary_text(),
             "亏损最大的交易:"]
    for t in losses:
        parts.append(f"- {t.code} {t.name} {t.entry_date}→{t.exit_date} "
                     f"{t.entry_price}→{t.exit_price} {t.pnl_pct}% 持有{t.hold_days}天 原因:{t.reason}")
    return "\n".join(parts)


def build_screener_context(rows: List[dict], top: int = 15) -> str:
    lines = ["代码 名称 现价 涨跌幅% 换手率% 量比 PE 流通市值(亿)"]
    for r in rows[:top]:
        lines.append(
            f"{r.get('code')} {r.get('name')} {_f(r.get('price'))} "
            f"{_f(r.get('change_pct'))} {_f(r.get('turnover_rate'))} "
            f"{_f(r.get('volume_ratio'))} {_f(r.get('pe'))} {_f(r.get('float_mv'), 0)}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 模板

TEMPLATES: Dict[str, str] = {
    "个股诊断":
        "请对以下股票做全面诊断，从技术面、资金情绪、消息面三方面分析，"
        "给出操作参考（买入/持有/回避 + 建议买入区间与止损位）与风险清单。\n\n{context}\n\n{question}",
    "信号解读":
        "以下是量化策略产生的交易信号。请评估该信号质量（A/B/C 三档），"
        "核对止损位是否合理，给出分批买入与仓位上限建议，并必须列出至少两条反向证据。\n\n{context}\n\n{question}",
    "回测解读":
        "以下是策略回测报告。请判断该策略是否值得用于实盘监听，"
        "指出可能的缺陷（过拟合、样本不足、单边行情依赖、费用敏感等）并给出改进建议。\n\n{context}\n\n{question}",
    "选股解读":
        "以下是条件选股结果列表。请逐只给出一句点评，并按关注度排序，"
        "指出最值得深入研究的2~3只及原因。\n\n{context}\n\n{question}",
    "新闻解读":
        "请解读以下消息对相关股票的可能影响（方向、力度、持续性）以及应对思路。\n\n{context}\n\n{question}",
    "自由问答":
        "请基于以下背景回答用户问题。\n\n{context}\n\n{question}",
}


def build_messages(template_key: str, context: str,
                   question: str = "") -> List[dict]:
    tpl = TEMPLATES.get(template_key, TEMPLATES["自由问答"])
    q = question.strip() or "（无补充问题，请直接按模板要求分析）"
    user = tpl.format(context=context, question=q)
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user}]
