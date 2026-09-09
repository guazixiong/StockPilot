"""v6.2：Regime 状态机+买入计划+纪律卖出（融合用户 investment-system v4.11.0）单测。

用户系统核心语义逐条对齐：6ETF 广度状态机 3 日确认 / 双区间分层买入 /
追价拦截 / 趋势门控 / 卖出优先级 / 止盈减半仓 / 告警限流（时6/日30/条8/同条30min）。
"""
from datetime import datetime, timedelta

from stockpilot.core import regime as rg


def _rows(changes):
    """6 只 ETF 的行情行（changes 按宇宙顺序）。"""
    codes = [c for c, _, _ in rg.DEFAULT_UNIVERSE]
    return [{"code": c, "change_pct": ch} for c, ch in zip(codes, changes)]


# ---------------------------------------------------------------- 状态机
def test_regime_classify_and_confirm():
    """BULL 需 3 日连续确认才切换（用户原版语义）。"""
    st = rg.RegimeState(confirmed="RANGE")
    up_all = _rows([2.0] * 6)                      # 100% 上涨 → BULL 原始态
    r1 = rg.classify_regime(up_all, st, today="2026-09-01")
    assert r1.raw_state == "BULL" and r1.state == "RANGE" and not r1.changed
    rg.classify_regime(up_all, st, today="2026-09-02")
    r3 = rg.classify_regime(up_all, st, today="2026-09-03")
    assert r3.state == "BULL" and r3.changed and r3.target_position == 0.90


def test_regime_reset_pending_on_flicker():
    """第 2 天反转则 pending 重置（防假突破的另一半语义）。"""
    st = rg.RegimeState(confirmed="RANGE")
    rg.classify_regime(_rows([2.0] * 6), st, today="09-01")   # pending BULL 1
    rg.classify_regime(_rows([2.0] * 6), st, today="09-02")   # pending BULL 2
    r = rg.classify_regime(_rows([-2.0] * 6), st, today="09-03")  # 反转 → pending BEAR 1
    assert r.state == "RANGE" and st.pending == "BEAR" and st.pending_days == 1


def test_regime_bear_threshold_and_sizing():
    """BEAR ≤30% 上涨占比触发 + 目标仓位 40%。"""
    st = rg.RegimeState(confirmed="RANGE")
    down = _rows([-1.0] * 5 + [0.5])              # 1/6 上涨 = 16.7% ≤30%
    for i, day in enumerate(["09-01", "09-02", "09-03"]):
        r = rg.classify_regime(down, st, today=day)
    assert r.state == "BEAR" and r.target_position == 0.40


def test_regime_same_day_no_advance():
    """同日多次调用只推进一次（桌面定时器防抖）。"""
    st = rg.RegimeState(confirmed="RANGE")
    rg.classify_regime(_rows([2.0] * 6), st, today="2026-09-07")
    pd = st.pending_days
    rg.classify_regime(_rows([2.0] * 6), st, today="2026-09-07")
    assert st.pending_days == pd


def test_regime_incomplete_universe():
    """宇宙不齐 → 保持当前状态 valid=False。"""
    st = rg.RegimeState(confirmed="BULL")
    r = rg.classify_regime([{"code": "512010", "change_pct": 1.0}], st)
    assert r.state == "BULL" and not r.valid


def test_target_weights_sum():
    """各状态权重×仓位 = 总资金合理分配（BULL 加总 ≈ 90%）。"""
    w = rg.target_weights("BULL", 10000)
    assert abs(sum(w.values()) - 9000) < 1


# ---------------------------------------------------------------- 买入计划
def test_classify_plan_buy_and_no_chase():
    """首笔区间→BUY；超追价上限→WATCH 不追高（用户铁律）。"""
    pl = rg.BuyPlan("512880", "证券ETF", 1000, (1.08, 1.09),
                    (1.05, 1.07), 1.11)
    assert rg.classify_plan(pl, 1.085, True).level == "BUY"
    assert rg.classify_plan(pl, 1.06, True).level == "BUY"     # 补仓区间
    w = rg.classify_plan(pl, 1.20, True)
    assert w.level == "WATCH" and "追价拦截" in w.reason


def test_classify_plan_trend_blocked():
    """区间内但趋势门控未通过 → WATCH 附拦截原因。"""
    pl = rg.BuyPlan("512880", "证券ETF", 1000, (1.08, 1.09),
                    (1.05, 1.07), 1.11)
    sig = rg.classify_plan(pl, 1.085, False)
    assert sig.level == "WATCH" and "趋势" in sig.reason


def test_trend_gate():
    """趋势门控三条件：价≥MA5≥MA10 且 价≥MA20。"""
    ok = {"ma5": 10.0, "ma10": 9.8, "ma20": 9.5}
    assert rg.trend_gate(ok, 10.1)
    assert not rg.trend_gate(ok, 9.9)          # 价 < MA5
    assert not rg.trend_gate(dict(ok, ma10=10.2), 10.1)  # MA5 < MA10
    assert not rg.trend_gate(ok, 9.4)          # 价 < MA20
    assert not rg.trend_gate({}, None)          # 缺数据


def test_no_preset_plans():
    """v6.3.1 修正：不预置任何具体计划（学习思路不搬标的清单）——空配置零计划。"""
    plans = rg.load_buy_plans({})
    assert plans == []
    # 用户自配后正确解析（load 路径）
    cfg = {"buy_plans": [{
        "code": "159915", "name": "创业板ETF", "target_amount": 3000,
        "firstBuyRange": [2.10, 2.13], "addBuyRange": [2.05, 2.08],
        "doNotChaseAbove": 2.18}]}
    plans2 = rg.load_buy_plans(cfg)
    assert len(plans2) == 1 and plans2[0].no_chase_above == 2.18


# ---------------------------------------------------------------- 卖出优先级
def test_sell_priority_chain():
    """硬止损 > 趋势破位 > 止盈；止盈是减半仓不是全卖。"""
    # 硬止损（最高优先）
    s1 = rg.sell_decision(10.0, 8.9, {"ma20": 5.0})   # 跌破-10% 且破MA20
    assert s1.level == "RISK" and "hard_stop" in s1.reason
    # 趋势破位
    s2 = rg.sell_decision(10.0, 9.5, {"ma20": 9.6})
    assert s2.level == "RISK" and "trend_exit" in s2.reason
    # 止盈（减半仓）
    s3 = rg.sell_decision(10.0, 11.6, {"ma20": 9.0})
    assert s3.level == "TAKE_PROFIT" and "50%" in s3.reason
    # 持有
    s4 = rg.sell_decision(10.0, 10.5, {"ma20": 9.5})
    assert s4.level == "HOLD"


# ---------------------------------------------------------------- 告警限流
def test_digest_throttle_limits():
    """用户 v4.4 口径：优先级排序 / 同条30min去重 / 单次≤8条。"""
    th = rg.DigestThrottle()
    items = [{"key": f"k{i}", "level": "RISK" if i % 2 else "BUY"}
             for i in range(20)]
    out = th.route(items)
    assert len(out) == 8                              # 单次最多 8 条
    assert out[0]["key"] == "k1"                      # RISK 优先
    # 已入选的 8 条 30 分钟内去重（原版只记录入选 key）
    again = th.route(items)
    assert not any(o["key"] in {x["key"] for x in out} for o in again)
    assert again                                    # 未入选的下次仍可入选（原版语义）


def test_digest_throttle_hour_cap():
    """每小时 ≤6 次推送。"""
    th = rg.DigestThrottle(max_per_hour=2)
    t0 = datetime(2026, 9, 7, 10, 0)
    one = [{"key": "a", "level": "BUY"}]
    assert th.route(one, now=t0)
    assert th.route([{"key": "b", "level": "BUY"}], now=t0 + timedelta(minutes=20))
    # 时窗内第 3 次 → 拒绝
    assert th.route([{"key": "c", "level": "BUY"}], now=t0 + timedelta(minutes=40)) == []
    # 下一小时恢复
    assert th.route([{"key": "d", "level": "BUY"}], now=t0 + timedelta(minutes=61))
