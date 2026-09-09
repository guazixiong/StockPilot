"""v4.4.1：按钮"点击不可用"防回归（运行时审计闸）。

用户再次报告死按钮。v4.2 的静态审计（±40 行窗口内找 clicked.connect）有
盲区，本轮用**运行时审计**双闸：
1. receivers() 闸：offscreen 构建全部页面+对话框，每个 QAbstractButton
   （排除 QTabBar 内置箭头/合法的"保存读值"复选框）必须有信号接收者；
2. 反馈即时性闸：所有"提交后台任务"的入口槽，submit 之后必须立即改变
   状态文字（不许等首条 on_progress 从 Worker 回传——回测页曾因此空白
   15~30s，用户以为点击无效）。
"""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import (QApplication, QAbstractButton)  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


_STUB_PAGES = ("home", "market", "boards", "monitor", "backtest",
                "positions", "ai", "news", "screener", "limitup", "detail")


def _stub_submit():
    """替换各页面模块的 submit（from-import 早绑定：必须改模块属性，
    改 workers.submit 打不到已加载页面的引用——v4.4.1 修的真实教训）。"""
    import importlib
    import stockpilot.ui.pages as pages
    for mod_name in _STUB_PAGES:
        try:
            mod = importlib.import_module(
                f"stockpilot.ui.pages.{mod_name}")
        except ImportError:
            continue
        if not getattr(mod, "_submit_stubbed", False):
            mod.submit = (lambda fn, *a, on_done=None, on_err=None,
                          on_progress=None, with_progress=False,
                          pool=None, **kw: None)
            mod._submit_stubbed = True


def _ctx(qapp):
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    ctx = AppContext(Config())
    # 隔离真实网络扫描与后台任务（解释器关闭后崩溃的历史教训）：
    # 本文件只测"点击后状态文字立即变化"，任务本身不需要真跑。
    ctx.monitor.market_fetch = lambda max_count=2000: []
    ctx.opportunity_scan = lambda rows, on_progress=None: []
    _stub_submit()
    return ctx


def _rx(btn, sig):
    r = btn.receivers(__import__("PySide6.QtCore",
                                 fromlist=["SIGNAL"]).SIGNAL(sig))
    return r if isinstance(r, int) else len(r)


ALL_PAGES = ("HomePage", "MarketPage", "BoardsPage", "MonitorPage",
             "BacktestPage", "PositionsPage", "AiPage", "NewsPage",
             "SettingsPage", "LimitUpPage", "ScreenerPage")


def test_runtime_all_buttons_have_receivers(qapp):
    """运行时闸：全页面（含详情窗/悬浮窗）按钮必须有信号接收者。"""
    ctx = _ctx(qapp)
    from stockpilot.ui.pages import (ai, backtest, boards, detail, home,
                                     limitup, market, monitor, news,
                                     positions, screener, settings)
    mods = {"HomePage": home, "MarketPage": market, "BoardsPage": boards,
            "MonitorPage": monitor, "BacktestPage": backtest,
            "PositionsPage": positions, "AiPage": ai, "NewsPage": news,
            "SettingsPage": settings, "LimitUpPage": limitup,
            "ScreenerPage": screener}
    widgets = [(n, getattr(m, n)(ctx)) for n, m in mods.items()]
    widgets.append(("DetailDialog", detail.DetailDialog(ctx, "600519", "t")))

    dead = []
    for name, wdg in widgets:
        for btn in wdg.findChildren(QAbstractButton):
            # 排除：Qt 内置（QTabBar 滚动箭头）、合法"保存读值"复选框、
            # 菜单型按钮（InstantPopup：点击弹菜单，动作才需要接收者——
            # v5.2 副图多选按钮属此类）
            parent = btn.parentWidget()
            if parent is not None and parent.__class__.__name__ == "QTabBar":
                continue
            if btn.__class__.__name__ in ("QCheckBox", "QRadioButton"):
                continue    # 这两类"读值"模式合法；真正要拦的是零反馈按钮
            from PySide6.QtWidgets import QToolButton as _TB
            if isinstance(btn, _TB) and btn.menu() is not None:
                assert btn.menu().actions(), f"空菜单按钮: {btn.text()!r}"
                continue    # 有菜单=有交互，菜单动作由动作级审计覆盖
            if btn.isCheckable():
                n = _rx(btn, "toggled(bool)") or _rx(btn, "clicked()")
            else:
                n = _rx(btn, "clicked()") + _rx(btn, "clicked(bool)")
            if n == 0:
                dead.append(f"{name}: {btn.text()[:20]!r}")
    assert not dead, "运行时死按钮（有创建无接收者）: " + "; ".join(dead)


def test_runtime_enabled_buttons(qapp):
    """非内置控件的项目按钮不应处于永久禁用（除非任务进行中由代码管理）。"""
    ctx = _ctx(qapp)
    from stockpilot.ui.pages.backtest import BacktestPage
    bp = BacktestPage(ctx)
    # 初始态：两个主按钮都应可点（v4.2 曾有漏 connect 后又永久禁用的组合）
    assert bp.run_btn.isEnabled(), "开始回测初始禁用（点击自然无效）"
    assert bp.validate_btn.isEnabled(), "验证报告按钮初始禁用"


def test_backtest_run_immediate_feedback(qapp, monkeypatch):
    """反馈即时性闸：回测 run() 在 submit 后必须立即更新进度文字。

    历史事故：进度首条反馈等 Worker 拉完第一只股票 K 线才回传（全市场
    Top30 约 15~30s 空白），用户看到'点击没反应'。"""
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    ctx = _ctx(qapp)
    from stockpilot.ui.pages.backtest import BacktestPage
    bp = BacktestPage(ctx)
    before = bp.progress.text()
    # 用自选股范围：往配置里塞一只股票避免"范围为空"弹窗（弹窗也算反馈，
    # 但我们要测的是 submit 成功后的即时文字）
    ctx.cfg.add_watch("600519", "贵州茅台")
    bp.rb_watch.setChecked(True)
    bp.run()
    after = bp.progress.text()
    assert after != before and after.strip(), (
        f"回测启动后进度无变化（{before!r}→{after!r}）——死按钮观感的根因")


def test_screener_radar_immediate_feedback(qapp, monkeypatch):
    """选股器雷达启动同样必须有即时反馈（同闸第二个入口）。"""
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    ctx = _ctx(qapp)
    from stockpilot.ui.pages.screener import ScreenerPage
    sc = ScreenerPage(ctx)
    sc._set_all_strategies(True)          # 预勾选策略避免弹窗
    before = sc.radar_status.text()
    sc.run_radar()
    after = sc.radar_status.text()
    assert after != before and after.strip(), (
        f"雷达启动无即时反馈（{before!r}→{after!r}）")
    sc._radar_running = False              # 复位，防后台任务尾噪声
