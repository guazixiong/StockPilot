"""v6.3：盘中独立调度器+本地即时通知（用户批评"不能即时监控通知"的修复）。"""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ---------------------------------------------------------------- 交易时段门禁
def test_trading_hours_gates():
    """用户 sh 口径：9:30-11:30 / 13:00-15:00 盘中；午休/收盘/盘前不跑。"""
    from datetime import datetime
    from stockpilot.ui.scheduler import is_trading_hours
    assert is_trading_hours(datetime(2026, 9, 7, 9, 30))
    assert is_trading_hours(datetime(2026, 9, 7, 11, 30))
    assert is_trading_hours(datetime(2026, 9, 7, 13, 0))
    assert is_trading_hours(datetime(2026, 9, 7, 14, 59))
    assert not is_trading_hours(datetime(2026, 9, 7, 9, 29))    # 盘前
    assert not is_trading_hours(datetime(2026, 9, 7, 11, 31))   # 午休
    assert not is_trading_hours(datetime(2026, 9, 7, 12, 30))   # 午休
    assert not is_trading_hours(datetime(2026, 9, 7, 15, 0))    # 收盘
    assert not is_trading_hours(datetime(2026, 9, 7, 22, 0))    # 夜间


def test_scheduler_reentry_lock(qapp, monkeypatch):
    """flock 等价物：in_flight 时 tick 不叠新一轮。"""
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    from stockpilot.ui.scheduler import MonitorScheduler
    ctx = AppContext(Config())
    ctx.monitor.market_fetch = lambda max_count=2000: []
    import stockpilot.ui.scheduler as sch_mod
    # v7.2.3：改走 monkeypatch（此前裸赋值污染模块属性到整个测试会话，
    # 掩盖了生产环境 scheduler.submit 不存在的崩溃——crash.log 实锤）
    monkeypatch.setattr(sch_mod, "submit", lambda *a, **k: None,
                        raising=False)
    monkeypatch.setattr(sch_mod, "is_trading_hours", lambda dt=None: True)
    s = MonitorScheduler(ctx)
    s._in_flight = True
    s._tick()
    assert s._in_flight            # 未被覆盖（没有叠新扫描）
    s._in_flight = False
    s._tick()
    assert s._in_flight            # tick 发起扫描后置锁


def test_scheduler_pool_includes_plans(qapp):
    """监控池 = 持仓 ∪ 计划池（用户语义：holdings 必监控、计划未持仓也盯）。"""
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    ctx = AppContext(Config())
    ctx.monitor.market_fetch = lambda max_count=2000: []
    from stockpilot.ui.scheduler import MonitorScheduler
    ctx.cfg.save_position("510210", {"code": "510210", "name": "上证ETF",
                                     "cost": 0.943, "qty": 1800, "date": "",
                                     "strategy": ""})
    s = MonitorScheduler(ctx)
    # _scan_all 内部拼池——检查代码路径（pool = positions + plans）
    import inspect
    src = inspect.getsource(MonitorScheduler._scan_all)
    assert "positions" in src and "plans" in src
    assert "load_buy_plans" in src
    ctx.cfg.positions.pop("510210", None)
    ctx.cfg.save()


# ---------------------------------------------------------------- 本地通知
def test_local_notify_not_requires_webhook():
    """本地即时通知不依赖 webhook 配置（默认开）——修复'零通知'问题。"""
    from stockpilot.core import notify
    from stockpilot.core.watch import WatchAlert
    a = WatchAlert("600519", "贵州茅台", "stop_loss_hit", "danger",
                   "跌破止损", "detail", "止损")
    # 完全空配置 → 本地通知仍生效
    assert notify.should_notify_local({}, [a]) is True
    # 显式关闭则不通知
    assert notify.should_notify_local({"local": {"enabled": False}}, [a]) is False
    # 无告警不通知
    assert notify.should_notify_local({}, []) is False


def test_local_alert_text_priority():
    """弹窗文本：danger 置顶 + 最多 4 条 + 汇总计数。"""
    from stockpilot.core import notify
    from stockpilot.core.watch import WatchAlert
    alerts = [WatchAlert("A", "股A", "vol_spike", "info", "放量", "", ""),
              WatchAlert("B", "股B", "stop_loss_hit", "danger", "破止损", "", ""),
              WatchAlert("C", "股C", "take_profit", "chance", "止盈", "", "")]
    text = notify.local_alert_text(alerts)
    assert "破止损" in text.split(chr(10))[1]      # danger 排最前
    assert "股B" in text
    many = [WatchAlert(f"C{i}", f"股{i}", "x", "info", f"t{i}", "", "")
            for i in range(8)]
    t2 = notify.local_alert_text(many)
    assert "等 8 条" in t2


def test_scheduler_local_chain_e2e(qapp, monkeypatch):
    """端到端：调度器产新告警 → on_alerts 回调被调用（主窗接托盘/声音）。"""
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    from stockpilot.ui.scheduler import MonitorScheduler
    ctx = AppContext(Config())
    ctx.monitor.market_fetch = lambda max_count=2000: []
    s = MonitorScheduler(ctx)
    got = []
    s.on_alerts = lambda fresh: got.append(len(fresh))
    from stockpilot.core.watch import WatchAlert
    s._on_done([WatchAlert("B", "股B", "纪律·hard_stop", "danger", "硬止损", "", "")])
    assert got == [1]
    # 同条 30 分钟去重 → 二轮不再通知
    s._on_done([WatchAlert("B", "股B", "纪律·hard_stop", "danger", "硬止损", "", "")])
    assert got == [1]


# ---------------------------------------------------------------- v7.2.3 回归
def test_scheduler_tick_production_path(qapp):
    """v7.2.3 回归闸：生产路径（未 monkeypatch 模块属性）交易时段 _tick
    不得抛 AttributeError。

    事故：_tick 曾调用 `stockpilot.ui.scheduler.submit` —— 该属性只在测试
    打桩时被塞进模块，生产环境每个交易时段 tick 必炸
    （crash.log 2026-09-10 'module has no attribute submit'）。
    修复后 submit 来自 workers 模块（与页面同一条后台投递链）。
    """
    import tempfile
    import stockpilot.core.session_store as ss
    ss.data_dir = lambda: tempfile.mkdtemp()
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    import stockpilot.ui.scheduler as sch_mod
    from stockpilot.ui.scheduler import MonitorScheduler
    assert not hasattr(sch_mod, "submit"), "scheduler 模块不应自带 submit"
    ctx = AppContext(Config())
    ctx.monitor.market_fetch = lambda max_count=2000: []
    ctx.get_quotes = lambda codes: {}
    ctx.kline_for_scan = lambda code, limit=800: []
    s = MonitorScheduler(ctx)
    orig_gate = sch_mod.is_trading_hours
    try:
        monkeypatch_gate = lambda dt=None: True   # 绕过时段门禁
        sch_mod.is_trading_hours = monkeypatch_gate
        s._tick()        # 不打桩 submit —— 必须走真实 workers.submit
        assert s._in_flight is True, "tick 应发起扫描并置锁"
    finally:
        sch_mod.is_trading_hours = orig_gate
        s.stop()
