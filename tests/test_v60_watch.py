"""v6.0：持仓盯盘（融合 PanWatch）单测——规则引擎 8 路径+节流+ATR 自适应。"""
from stockpilot.core import indicators, watch as w
from stockpilot.core.models import KLine, Quote


def _kls(n=80, base=100.0, drift=0.002):
    out, px = [], base
    for i in range(n):
        px *= (1 + drift)
        out.append(KLine(date=f"d{i}", open=px * 0.995, close=px,
                         high=px * 1.01, low=px * 0.985, volume=200000,
                         amount=200000 * px, change_pct=drift * 100,
                         turnover=2.0))
    return out


def _q(price=100.0, chg=0.0, vr=1.0):
    return Quote.from_mapping("600519", {
        "price": price, "change_pct": chg, "volume_ratio": vr,
        "name": "测试股"})


def _ind(kls=None, **over):
    ind = indicators.analyze(kls or _kls())
    ind.update(over)
    return ind


def test_atr_pct_basic():
    """ATR%：单调缓涨序列应有小正值；样本不足返回 None。"""
    assert indicators.atr_pct(_kls(80)) > 0
    assert indicators.atr_pct(_kls(5)) is None


def test_stop_loss_hit_and_near():
    """跌破止损= danger；逼近止损= warning（两级）。"""
    ind = _ind()
    # 成本 100，止损 8% → 破位价 92
    pos = {"cost": 100.0, "qty": 100, "strategy": ""}
    alerts = w.check_position("600519", "t", pos, _q(price=91.0), ind)
    kinds = [a.kind for a in alerts]
    assert "stop_loss_hit" in kinds and \
        next(a for a in alerts if a.kind == "stop_loss_hit").level == "danger"
    # 逼近（价 94 > 92 但 < 93.6=余20%空间线）
    alerts2 = w.check_position("600519", "t", pos, _q(price=93.0), ind)
    assert any(a.kind == "stop_loss" and a.level == "warning" for a in alerts2)
    assert not any(a.kind == "stop_loss_hit" for a in alerts2)


def test_take_profit_and_daily_profit():
    """止盈线=chance；当日大涨报喜。"""
    ind = _ind()
    pos = {"cost": 80.0, "qty": 100, "strategy": ""}
    alerts = w.check_position("600519", "t", pos, _q(price=100.0, chg=5.0), ind)
    kinds = [a.kind for a in alerts]
    assert "take_profit" in kinds
    assert "daily_profit" in kinds


def test_strategy_params_override_default():
    """关联策略的止损参数优先于默认（与监听卖出同口径）。"""
    ind = _ind()
    # 关联"海龟20日突破"（stop_loss_pct=6）→ 成本 100 破位价 94
    pos = {"cost": 100.0, "qty": 100, "strategy": "海龟20日突破"}
    alerts = w.check_position("600519", "t", pos, _q(price=93.5), ind)
    hit = next((a for a in alerts if a.kind == "stop_loss_hit"), None)
    assert hit is not None
    assert "6%" in hit.title or "-6" in hit.title


def test_surge_atr_adaptive():
    """异动阈值 ATR 自适应：ATR 大时 3% 固定阈值被 1.5×ATR 抬升。"""
    ind = _ind(atr_pct=4.0)   # 高波动股：1.5×4=6% 阈值
    pos = {"cost": 100.0, "qty": 100}
    # 涨 4%：低于自适应阈值（6%）不报
    alerts = w.check_position("600519", "t", pos, _q(price=104.0, chg=4.0), ind)
    assert not any(a.kind == "surge" for a in alerts)
    # 涨 7%：超阈值报异动
    alerts2 = w.check_position("600519", "t", pos, _q(price=107.0, chg=7.0), ind)
    a = next(x for x in alerts2 if x.kind == "surge")
    assert "拉升" in a.title


def test_vol_spike_and_rsi():
    """量比异动 + RSI 超买卖。"""
    ind = _ind()
    pos = {"cost": 100.0, "qty": 100}
    alerts = w.check_position("600519", "t", pos, _q(price=100.0, vr=3.2), ind)
    assert any(a.kind == "vol_spike" for a in alerts)
    ind2 = _ind(rsi6=85)
    alerts2 = w.check_position("600519", "t", pos, _q(price=100.0), ind2)
    assert any(a.kind == "rsi_overbought" for a in alerts2)
    ind3 = _ind(rsi6=15)
    alerts3 = w.check_position("600519", "t", pos, _q(price=100.0), ind3)
    a3 = next(x for x in alerts3 if x.kind == "rsi_oversold")
    assert a3.level == "chance"


def test_ma_break_requires_prev_above():
    """跌破 MA20 告警要求昨日未破（防持续阴跌刷屏）。"""
    ind = _ind(ma20=105.0)
    pos = {"cost": 100.0, "qty": 100}
    # 昨日在上方 → 今日破位报
    ind["prev_above_ma20"] = True
    alerts = w.check_position("600519", "t", pos, _q(price=102.0), ind)
    assert any(a.kind == "ma_break" for a in alerts)
    # 昨日已破 → 不再报
    ind["prev_above_ma20"] = False
    alerts2 = w.check_position("600519", "t", pos, _q(price=101.0), ind)
    assert not any(a.kind == "ma_break" for a in alerts2)


def test_no_cost_no_alerts():
    """无成本/零成本持仓不产生告警（防御）。"""
    ind = _ind()
    assert w.check_position("600519", "t", {"cost": 0}, _q(), ind) == []
    assert w.check_position("600519", "t", {}, _q(), ind) == []
    assert w.check_position("600519", "t", {"cost": 100}, None, ind) == []


def test_throttle_same_kind_dedup():
    """节流：同股同类型 30 分钟内只提醒一次；不同类型互不影响。"""
    from datetime import datetime, timedelta
    a1 = [w.WatchAlert("600519", "t", "surge", "info", "x", "d", "act"),
          w.WatchAlert("600519", "t", "vol_spike", "info", "y", "d", "act")]
    th = w.AlertThrottle(minutes=30)
    t0 = datetime(2026, 9, 6, 10, 0)
    f1 = th.filter(a1, now=t0)
    assert len(f1) == 2
    f2 = th.filter(a1, now=t0 + timedelta(minutes=5))
    assert f2 == []                       # 节流期内
    f3 = th.filter(a1, now=t0 + timedelta(minutes=31))
    assert len(f3) == 2                   # 过窗恢复


def test_throttle_clear_by_code():
    """用户处理后单股清空节流（立即恢复告警资格）。"""
    from datetime import datetime, timedelta
    a = [w.WatchAlert("600519", "t", "surge", "info", "x", "d", "act")]
    th = w.AlertThrottle(minutes=30)
    t0 = datetime(2026, 9, 6, 10, 0)
    assert th.filter(a, now=t0)
    assert not th.filter(a, now=t0 + timedelta(minutes=1))
    th.clear("600519")
    assert th.filter(a, now=t0 + timedelta(minutes=1))


def test_alerts_summary():
    """摘要：多条拼一行，>5 条收口。"""
    mk = lambda i: w.WatchAlert(f"60000{i}", f"股{i}", "surge", "info",
                               f"标题{i}", "d", "act")
    assert "暂无告警" in w.alerts_summary([])
    assert "股0" in w.alerts_summary([mk(i) for i in range(3)])
    s = w.alerts_summary([mk(i) for i in range(8)])
    assert "等8条" in s
