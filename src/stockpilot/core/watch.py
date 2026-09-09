"""持仓盯盘（v6.0，融合 PanWatch 盘中监测 Agent 的规则引擎思想）。

借鉴 TNT-Likely/PanWatch（906★）的可借鉴点：
1. **规则先行、AI 可选**：盯盘核心是确定性规则（异动/风控线/技术位），
   AI 解读是可选增强（PanWatch 全 AI 判定成本高，桌面工具先规则后 AI）；
2. **ATR 自适应异动阈值**：max(固定阈值, 1.5×ATR%)——高波动股 ±5% 是
   常态不误报，低波动股 ±3% 已是异动不漏报；
3. **通知节流**：同一股票同一告警类型短时间内不重复打扰（默认 30 分钟）。

纯函数：输入（持仓+行情+指标）→ 输出告警列表。UI 层消费并可推送/托盘/AI 解读。
持仓信息来自用户已有数据：cost（成本价）/qty/date/strategy（关联策略的止损止盈参数）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional



# ---------------------------------------------------------------- 数据结构
@dataclass
class WatchAlert:
    code: str
    name: str
    kind: str          # 告警类型（见 KIND_TEXT）
    level: str         # danger / warning / info / chance
    title: str         # 一句话标题
    detail: str        # 详情（含数据）
    action: str        # 建议动作
    price: float = 0.0
    change_pct: float = 0.0


KIND_TEXT = {
    "stop_loss": "逼近止损",
    "stop_loss_hit": "跌破止损",
    "take_profit": "达到止盈",
    "drawdown": "亏损扩大",
    "surge": "盘中异动",
    "vol_spike": "量能异动",
    "ma_break": "跌破MA20",
    "rsi_overbought": "RSI超买",
    "rsi_oversold": "RSI超卖",
    "daily_profit": "当日大盈",
}

_LEVEL_ORDER = {"danger": 0, "warning": 1, "chance": 2, "info": 3}


# ---------------------------------------------------------------- 规则引擎
def check_position(code: str, name: str, pos: dict, quote, ind: Dict[str, object],
                   config: Optional[dict] = None) -> List[WatchAlert]:
    """对一只持仓跑全部盯盘规则。返回告警列表（可能为空）。

    pos: {cost, qty, date, strategy}（用户持仓数据）
    quote: Quote（实时行情）
    ind: indicators.analyze 输出
    config: 盯盘配置 {
        stop_loss_pct / take_profit_pct: 无关联策略时的默认风控线（%）
        surge_pct: 固定异动阈值（%），ATR 自适应可抬升
        vol_ratio_spike: 量比异动阈值
        drawdown_warn_pct: 浮亏告警阈值（%）
        daily_profit_pct: 当日浮盈报喜阈值（%）
    }
    """
    cfg = {
        "stop_loss_pct": 8.0,
        "take_profit_pct": 15.0,
        "surge_pct": 3.0,
        "vol_ratio_spike": 2.5,
        "drawdown_warn_pct": 5.0,
        "daily_profit_pct": 4.0,
    }
    cfg.update(config or {})
    out: List[WatchAlert] = []
    price = quote.price if quote is not None else None
    if not price or not pos:
        return out
    cost = float(pos.get("cost") or 0)
    if cost <= 0:
        return out

    pnl_pct = (price - cost) / cost * 100
    chg = quote.change_pct or 0.0

    # ---- 规则1：止损线（用户成本为锚；优先用关联策略参数）----
    sl = _strategy_risk(pos, "stop_loss_pct", cfg["stop_loss_pct"])
    tp = _strategy_risk(pos, "take_profit_pct", cfg["take_profit_pct"])
    if price <= cost * (1 - sl / 100):
        out.append(WatchAlert(
            code=code, name=name, kind="stop_loss_hit", level="danger",
            title=f"已跌破止损线（-{sl:.0f}%）",
            detail=f"成本 {cost:.2f}，现价 {price:.2f}，浮亏 {pnl_pct:.1f}%",
            action="按纪律止损或立即人工决策", price=price, change_pct=chg))
    elif price <= cost * (1 - sl / 100 * 0.8):
        out.append(WatchAlert(
            code=code, name=name, kind="stop_loss", level="warning",
            title=f"逼近止损线（余 {sl * 0.2:.1f}% 空间）",
            detail=f"成本 {cost:.2f}，现价 {price:.2f}，浮亏 {pnl_pct:.1f}%",
            action="关注反弹，做好止损预案", price=price, change_pct=chg))

    # ---- 规则2：止盈线 ----
    if price >= cost * (1 + tp / 100):
        out.append(WatchAlert(
            code=code, name=name, kind="take_profit", level="chance",
            title=f"达到止盈线（+{tp:.0f}%）",
            detail=f"成本 {cost:.2f}，现价 {price:.2f}，浮盈 {pnl_pct:.1f}%",
            action="考虑分批止盈/移动止盈", price=price, change_pct=chg))

    # ---- 规则3：浮亏扩大（无风控线时仍要提醒重套）----
    if pnl_pct <= -cfg["drawdown_warn_pct"] and not any(
            a.kind == "stop_loss_hit" for a in out):
        out.append(WatchAlert(
            code=code, name=name, kind="drawdown", level="warning",
            title=f"浮亏扩大至 {pnl_pct:.1f}%",
            detail=f"成本 {cost:.2f}，现价 {price:.2f}",
            action="审视持仓逻辑是否已变化", price=price, change_pct=chg))

    # ---- 规则4：盘中异动（ATR 自适应：PanWatch 同款）----
    # ATR% 由 indicators.analyze 预计算（ind["atr_pct"]）——盯盘层不重复算K线
    atr = ind.get("atr_pct")
    threshold = max(cfg["surge_pct"], (atr * 1.5) if isinstance(atr, (int, float)) and atr else 0)
    if abs(chg) >= threshold:
        direct = "拉升" if chg > 0 else "急跌"
        out.append(WatchAlert(
            code=code, name=name, kind="surge",
            level="info" if chg > 0 else "warning",
            title=f"盘中{direct} {chg:+.1f}%",
            detail=f"现价 {price:.2f}，异动阈值 {threshold:.1f}%"
                   + (f"（ATR 自适应）" if atr else "（固定）"),
            action=("急涨注意分批止盈" if chg > 0 else "急跌注意风险与支撑位"),
            price=price, change_pct=chg))

    # ---- 规则5：量能异动 ----
    vr = quote.volume_ratio if quote is not None else None
    if isinstance(vr, (int, float)) and vr >= cfg["vol_ratio_spike"]:
        out.append(WatchAlert(
            code=code, name=name, kind="vol_spike", level="info",
            title=f"放量（量比 {vr:.1f}）",
            detail=f"现价 {price:.2f}，涨跌 {chg:+.1f}%",
            action="放量上涨持有观察；放量下跌警惕出货",
            price=price, change_pct=chg))

    # ---- 规则6：跌破 MA20（趋势护栏）----
    ma20 = ind.get("ma20")
    if isinstance(ma20, (int, float)) and ma20 and price < ma20 * 0.99 \
            and (ind.get("prev_above_ma20") is not False):
        out.append(WatchAlert(
            code=code, name=name, kind="ma_break", level="warning",
            title=f"跌破MA20（{ma20:.2f}）",
            detail=f"现价 {price:.2f}，MA20 为趋势护栏",
            action="短线趋势转弱，评估减仓",
            price=price, change_pct=chg))

    # ---- 规则7：RSI 超买超卖 ----
    rsi = ind.get("rsi6")
    if isinstance(rsi, (int, float)):
        if rsi >= 80:
            out.append(WatchAlert(
                code=code, name=name, kind="rsi_overbought", level="info",
                title=f"RSI6={rsi:.0f} 严重超买",
                detail=f"现价 {price:.2f}，短线回调风险",
                action="持仓者可部分止盈", price=price, change_pct=chg))
        elif rsi <= 20:
            out.append(WatchAlert(
                code=code, name=name, kind="rsi_oversold", level="chance",
                title=f"RSI6={rsi:.0f} 严重超卖",
                detail=f"现价 {price:.2f}，存在超卖反弹机会",
                action="勿恐慌割肉，等反弹再决策", price=price, change_pct=chg))

    # ---- 规则8：当日大盈报喜 ----
    if chg >= cfg["daily_profit_pct"]:
        out.append(WatchAlert(
            code=code, name=name, kind="daily_profit", level="chance",
            title=f"今日大涨 {chg:+.1f}%",
            detail=f"现价 {price:.2f}，持仓浮盈 {pnl_pct:+.1f}%",
            action="可考虑移动止盈保护利润", price=price, change_pct=chg))

    out.sort(key=lambda a: _LEVEL_ORDER.get(a.level, 9))
    return out


def _strategy_risk(pos: dict, key: str, default: float) -> float:
    """优先用持仓关联策略的止损/止盈参数（同一口径：监听卖出同款）。"""
    from . import strategy as stg
    sname = pos.get("strategy") or ""
    s = next((x for x in stg.builtin_strategies() if x.name == sname), None)
    if s:
        v = getattr(s, key, None)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return float(default)


# ---------------------------------------------------------------- 节流器（PanWatch 同款思想）
class AlertThrottle:
    """同股同类型告警节流：minutes 内不重复（防止盘中每轮扫描刷屏）。"""

    def __init__(self, minutes: int = 30):
        from datetime import datetime, timedelta
        self.minutes = minutes
        self._last: Dict[str, object] = {}
        self._dt = datetime
        self._td = timedelta

    def _key(self, a: WatchAlert) -> str:
        return f"{a.code}|{a.kind}"

    def filter(self, alerts: List[WatchAlert], now=None) -> List[WatchAlert]:
        """过滤掉节流期内已提醒过的；保留的记录时间戳。"""
        now = now or self._dt.now()
        fresh = []
        for a in alerts:
            k = self._key(a)
            last = self._last.get(k)
            if last is not None and now - last < self._td(minutes=self.minutes):
                continue
            fresh.append(a)
            self._last[k] = now
        return fresh

    def clear(self, code: Optional[str] = None) -> None:
        """清空（全部或单股）——用户手工处理持仓后立即恢复告警资格。"""
        if code is None:
            self._last.clear()
        else:
            self._last = {k: v for k, v in self._last.items()
                          if not k.startswith(f"{code}|")}


def alerts_summary(alerts: List[WatchAlert]) -> str:
    """告警列表 → 一行摘要（推送/托盘用）。"""
    if not alerts:
        return "持仓盯盘：暂无告警"
    parts = [f"{a.name} {a.title}" for a in alerts[:5]]
    more = f" 等{len(alerts)}条" if len(alerts) > 5 else ""
    return "持仓盯盘：" + "；".join(parts) + more
