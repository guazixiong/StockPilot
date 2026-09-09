"""市场状态机 Regime Engine（v6.2，融合用户自研 investment-system v4.11.0）。

来源：用户自己的交易系统（D:/repo/money/investment-system-v4.11.0.zip，
engine/regime_engine_v4.0.js）——纯规则确定性机会闸门，与本项目
"不自动下单、纪律化"理念完全一致。本文件把其核心移植为 Python 纯函数：

1. **ETF 广度状态机**：6 只 ETF（医药/证券/标普500/日经225/AI/新能源）
   当日上涨占比 → BULL(≥60%)/RANGE/BEAR(≤30%)，**连续 3 日确认**才切换
   ——防假突破（用户系统原版语义）；
2. **状态驱动仓位**：BULL 90% / RANGE 70% / BEAR 40% 目标总仓位；
3. **分层买入信号**：用户配置的双价格区间（首笔试探 + 回落补仓）+
   追价上限（doNotChaseAbove）+ 趋势门控（价≥MA5≥MA10 且 价≥MA20）；
4. **卖出优先级**：硬止损 > 趋势破位 > 弱势减仓 > 计划减仓 > 止盈
   （止盈减半仓 50% 锁定利润——用户原版口径 -10%/+15%/50%）。

状态机为纯函数式：classify() 无副作用，确认计数由调用方传入/传回
（桌面单进程存 config，与用户系统存 JSON state 同思路）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------- Regime
DEFAULT_UNIVERSE = [
    ("512010", "医药ETF", "防守"),
    ("512880", "证券ETF", "周期进攻"),
    ("513650", "标普500ETF", "跨境对冲"),
    ("513880", "日经225ETF", "跨境对冲"),
    ("515070", "人工智能ETF", "科技进攻"),
    ("516160", "新能源ETF", "成长进攻"),
]
POSITION_SIZING = {"BULL": 0.90, "RANGE": 0.70, "BEAR": 0.40}
# 状态下各 ETF 相对总资金权重（用户 v4.11.0 原版表）
ALLOCATION = {
    "BULL": {"515070": 0.16, "516160": 0.10, "512880": 0.10,
             "513650": 0.32, "513880": 0.32},
    "RANGE": {"512010": 0.17, "515070": 0.17, "512880": 0.10,
              "513650": 0.28, "513880": 0.28},
    "BEAR": {"512010": 0.30, "512880": 0.15,
             "513650": 0.275, "513880": 0.275},
}
BULL_THRESHOLD = 0.60
BEAR_THRESHOLD = 0.30
CONFIRM_DAYS = 3


@dataclass
class RegimeState:
    """状态机的持久化部分（存 config["regime"]）。"""
    confirmed: str = "RANGE"
    pending: Optional[str] = None
    pending_days: int = 0
    last_date: str = ""      # 末日日期（防同日重复推进）


@dataclass
class RegimeResult:
    state: str               # 确认后的状态 BULL/RANGE/BEAR
    raw_state: str           # 今日原始判定
    score: float             # 上涨占比
    advancing: int = 0
    total: int = 0
    valid: bool = False
    target_position: float = 0.0   # 该状态目标总仓位
    changed: bool = False          # 今日是否发生状态切换


def classify_regime(quotes: List[dict], st: RegimeState,
                    today: str = "") -> RegimeResult:
    """每日一次推进状态机。quotes: [{code, change_pct}]（ETF 全日线）。

    与用户 JS 原版逐行对齐：
    - universe 不全 → 返回当前确认态（valid=False，不推进确认计数）；
    - 上涨占比 score ≥0.60 → BULL 原始态；≤0.30 → BEAR；其余 RANGE；
    - 原始态与确认态相同 → 清空 pending；
    - 原始态与 pending 相同 → pending_days+1；否则重置为 1；
    - pending_days ≥ 3 → 切换确认态。
    today 为空时不做同日防重（兼容手动调用）。
    """
    codes = {c for c, _, _ in DEFAULT_UNIVERSE}
    usable = [r for r in quotes
              if r.get("code") in codes and isinstance(r.get("change_pct"), (int, float))]
    if len(usable) < len(codes):
        return RegimeResult(state=st.confirmed, raw_state=st.confirmed,
                            score=0.0, valid=False,
                            target_position=POSITION_SIZING.get(st.confirmed, 0.70))
    # 同日防重：状态机一天只推进一次
    if today and st.last_date == today:
        return RegimeResult(state=st.confirmed, raw_state=st.pending or st.confirmed,
                            score=0.0, valid=True,
                            target_position=POSITION_SIZING.get(st.confirmed, 0.70))
    advancing = sum(1 for r in usable if r["change_pct"] > 0)
    score = advancing / len(usable)
    raw = "BULL" if score >= BULL_THRESHOLD else \
          "BEAR" if score <= BEAR_THRESHOLD else "RANGE"
    prev_confirmed = st.confirmed
    if raw == st.confirmed:
        st.pending, st.pending_days = None, 0
    elif raw == st.pending:
        st.pending_days += 1
    else:
        st.pending, st.pending_days = raw, 1
    if st.pending is not None and st.pending_days >= CONFIRM_DAYS:
        st.confirmed, st.pending, st.pending_days = st.pending, None, 0
    if today:
        st.last_date = today
    return RegimeResult(
        state=st.confirmed, raw_state=raw, score=score,
        advancing=advancing, total=len(usable), valid=True,
        target_position=POSITION_SIZING.get(st.confirmed, 0.70),
        changed=st.confirmed != prev_confirmed)


def target_weights(state: str, capital: float) -> Dict[str, float]:
    """该状态下各 ETF 目标金额 = 总资金 × 权重× 仓位率。"""
    expo = POSITION_SIZING.get(state, 0.70)
    alloc = ALLOCATION.get(state, ALLOCATION["RANGE"])
    return {code: round(capital * w * expo, 0) for code, w in alloc.items()}


# ---------------------------------------------------------------- 分层买入计划
@dataclass
class BuyPlan:
    code: str
    name: str
    target_amount: float
    first_range: tuple        # (低, 高) 首笔试探区间
    add_range: tuple          # 回落补仓区间
    no_chase_above: float     # 追价上限


@dataclass
class PlanSignal:
    level: str                # BUY / PROBE_BUY / RISK / WATCH / BLOCKED
    title: str
    reason: str
    code: str = ""
    name: str = ""


def classify_plan(plan: BuyPlan, price: Optional[float],
                  trend_ok: bool) -> PlanSignal:
    """分层信号分类器（用户 v4.3 语义）。

    trend_ok: 趋势门控（现价≥MA5≥MA10 且 现价≥MA20）。
    - 无行情 → BLOCKED；超追价上限 → WATCH（不追高）；
    - 首笔区间 + 趋势通过 → BUY（原版为 PROBE_BUY；本桌面版直接出
      BUY——无低本金模式开关，目标金额本身就是用户配置）；
    - 补仓区间 + 趋势通过 → BUY（回落补仓）；
    - 区间内但趋势不通过 → WATCH（附拦截原因）。
    """
    if price is None or price <= 0:
        return PlanSignal("BLOCKED", "行情缺失", "BLOCKED",
                          plan.code, plan.name)
    if price > plan.no_chase_above:
        return PlanSignal(
            "WATCH", f"现价 {price:.3f} 超追价上限 {plan.no_chase_above:.3f}",
            "追价拦截（不追高）", plan.code, plan.name)
    lo1, hi1 = plan.first_range
    lo2, hi2 = plan.add_range
    if lo1 <= price <= hi1:
        if trend_ok:
            return PlanSignal(
                "BUY", f"现价 {price:.3f} 进入首笔区间 {lo1}~{hi1}",
                "首笔建仓（趋势确认通过）", plan.code, plan.name)
        return PlanSignal("WATCH",
                          f"在首笔区间 {lo1}~{hi1} 但趋势门控未通过",
                          "趋势拦截", plan.code, plan.name)
    if lo2 <= price <= hi2:
        if trend_ok:
            return PlanSignal(
                "BUY", f"现价 {price:.3f} 进入回落补仓区间 {lo2}~{hi2}",
                "回落补仓（趋势确认通过）", plan.code, plan.name)
        return PlanSignal("WATCH",
                          f"在补仓区间 {lo2}~{hi2} 但趋势门控未通过",
                          "趋势拦截", plan.code, plan.name)
    return PlanSignal("WATCH",
                      f"现价 {price:.3f} 未进入任何买入区间",
                      "区间外观察", plan.code, plan.name)


def trend_gate(ind: Dict[str, object], price: Optional[float]) -> bool:
    """趋势确认门控（用户原版三条件：价≥MA5、MA5≥MA10、价≥MA20）。"""
    ma5, ma10, ma20 = (ind.get(k) for k in ("ma5", "ma10", "ma20"))
    if not price or not all(isinstance(v, (int, float)) and v for v in (ma5, ma10, ma20)):
        return False
    return price >= ma5 >= ma10 and price >= ma20


# ---------------------------------------------------------------- 卖出优先级
SELL_PRIORITY = ["hard_stop", "trend_exit", "weakness_exit",
                "planned_reduce", "take_profit"]


def sell_decision(cost: float, price: float, ind: Dict[str, object],
                  hard_stop_pct: float = -10.0,
                  take_profit_pct: float = 15.0,
                  take_profit_ratio: float = 0.5) -> PlanSignal:
    """卖出判定（用户优先级链：风险永远优先于止盈）。

    1. hard_stop：现价 ≤ 成本×(1-10%) → 全卖（SELL_ALL）
    2. trend_exit：跌破 MA20 → 全卖
    3. take_profit：现价 ≥ 成本×(1+15%) → **减半仓**（锁利润，留一半）
    其余 → 无信号（Hold 不打扰）。
    """
    if cost <= 0 or price <= 0:
        return PlanSignal("HOLD", "", "无效成本/价格")
    if price <= cost * (1 + hard_stop_pct / 100):
        return PlanSignal("RISK",
                          f"硬止损：现价 {price:.2f} ≤ 成本 {cost:.2f}×{1 + hard_stop_pct / 100:.2f}",
                          "hard_stop 全部卖出（风险优先级最高）")
    ma20 = ind.get("ma20")
    if isinstance(ma20, (int, float)) and ma20 and price < ma20:
        return PlanSignal("RISK",
                          f"趋势破位：现价 {price:.2f} 跌破 MA20 {ma20:.2f}",
                          "trend_exit 全部卖出")
    if price >= cost * (1 + take_profit_pct / 100):
        return PlanSignal(
            "TAKE_PROFIT",
            f"止盈：现价 {price:.2f} ≥ 成本×{1 + take_profit_pct / 100:.2f}",
            f"take_profit 减仓 {take_profit_ratio * 100:.0f}% 锁定利润")
    return PlanSignal("HOLD", "", "")


# ---------------------------------------------------------------- 买入计划（持久化）
# v6.3.1 修正：不预置任何具体计划（学习的是"双区间+追价上限+趋势门控"的思路，
# 不是照搬某人的标的清单）。用户通过 Regime Tab 的 JSON 导入建立自己的计划，
# 或在 data/config.json 的 monitor.buy_plans 里手工维护（结构与 classify_plan 一致）。
DEFAULT_BUY_PLANS: List[BuyPlan] = []


def load_buy_plans(monitor_cfg: dict) -> List[BuyPlan]:
    """从 config["monitor"]["buy_plans"] 读取（用户自己的计划）。"""
    saved = monitor_cfg.get("buy_plans")
    if not saved:
        return list(DEFAULT_BUY_PLANS)
    out = []
    for d in saved:
        try:
            out.append(BuyPlan(
                code=str(d["code"]), name=str(d.get("name") or d["code"]),
                target_amount=float(d.get("target_amount") or 1000),
                first_range=tuple(d["firstBuyRange"]),
                add_range=tuple(d["addBuyRange"]),
                no_chase_above=float(d["doNotChaseAbove"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def save_buy_plans(monitor_cfg: dict, plans: List[dict]) -> None:
    """保存用户编辑的计划（BuyPlan.to_dict 形态）。"""
    monitor_cfg["buy_plans"] = plans


# ---------------------------------------------------------------- 告警限流（用户 v4.4 口径）
class DigestThrottle:
    """用户系统的告警路由（alert_router）Python 版：

    - 同一标的+同一级别+同一理由：30 分钟内不重复（可调）；
    - 全局：单次摘要 ≤8 条 / 每小时 ≤6 次推送 / 每日 ≤30 次推送；
    - 优先级排序：RISK(1) < BUY(2) < PROBE_BUY(3) < 其它(9)。
    跨进程持久化由调用方负责（桌面场景存 config.monitor.alert_state）。
    """

    def __init__(self, max_per_hour: int = 6, max_per_day: int = 30,
                 max_per_digest: int = 8, dedup_minutes: int = 30):
        self.max_per_hour = max_per_hour
        self.max_per_day = max_per_day
        self.max_per_digest = max_per_digest
        self.dedup_minutes = dedup_minutes
        self._items: Dict[str, object] = {}   # key -> last_sent datetime
        self._hour_ts: List = []              # 推送时间戳（1h 滑窗）
        self._day_sent = 0
        self._day = ""

    @staticmethod
    def _priority(level: str) -> int:
        return {"RISK": 1, "BUY": 2, "PROBE_BUY": 3}.get(level, 9)

    def route(self, items: List[dict], now=None) -> List[dict]:
        """items: [{key, level, ...}]（key=标的+级别+理由 拼接）。
        返回本轮应推送的摘要条目（按优先级排序、限量）。"""
        from datetime import datetime, timedelta
        now = now or datetime.now()
        day = now.strftime("%Y-%m-%d")
        if self._day != day:
            self._day = day
            self._day_sent = 0
        self._hour_ts = [t for t in self._hour_ts
                         if now - t < timedelta(hours=1)]
        # 限流闸：日/时/同条
        if self._day_sent >= self.max_per_day or                 len(self._hour_ts) >= self.max_per_hour:
            return []
        dedup = timedelta(minutes=self.dedup_minutes)
        fresh = []
        for it in sorted(items, key=lambda x: self._priority(x.get("level", ""))):
            key = it.get("key") or f"{it.get('code')}|{it.get('level')}|{it.get('reason')}"
            last = self._items.get(key)
            if last is not None and now - last < dedup:
                continue
            fresh.append(it)
            self._items[key] = now
            if len(fresh) >= self.max_per_digest:
                break
        if fresh:
            self._day_sent += 1
            self._hour_ts.append(now)
        return fresh
