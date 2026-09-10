"""v7.2.2 按钮点击异常修复回归闸（正/逆向 SOP 补层）。

根因（排查结论）：
R1 监听页定时器重复连接 —— _apply_timer 每次「保存设置」都 connect 一次
   timer/eod_timer/_save_scan_cfg，保存 N 次后一个周期触发 N 次自动扫描
   （点击按钮后行为异常：重复弹窗/重复扫描/重复推送的直接根因）。
R2 PySide6 disconnect 防重连写法失效 —— 对未连接信号 disconnect 只发
   RuntimeWarning 不抛异常（except RuntimeError 拦不住），
   monitor.py / screener.py 的先断后连实际从未生效。
R3 requests 默认 trust_env=True 跟系统代理走 —— 用户开着死代理时全部行情
   请求 ProxyError（用户日志实锤），点击刷新类按钮全部"失败"。

本文件闸：
- G1 重复 apply_settings / _load_plans 后 receivers 恒为 1（R1+R2）
- G2 compare 对话框全按钮真实点击不崩（v5.2.2 闸只覆盖页面不含对话框）
- G3 HttpClient/OpenAIClient/notify 会话 trust_env=False（R3）
"""
import pytest

pytest.importorskip("PySide6")
import os  # noqa: E402
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import (QAbstractButton, QApplication,  # noqa: E402
                               QToolButton)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _sig(obj, name):
    from PySide6.QtCore import SIGNAL
    return SIGNAL(name)


def _n_receivers(obj, sig_name):
    r = obj.receivers(_sig(obj, sig_name))
    return r if isinstance(r, int) else len(r)


def _ctx(qapp, monkeypatch):
    import tempfile
    import stockpilot.core.session_store as ss
    monkeypatch.setattr(ss, "data_dir", lambda: tempfile.mkdtemp())
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    ctx = AppContext(Config())
    ctx.monitor.market_fetch = lambda max_count=2000: []
    ctx.opportunity_scan = lambda rows, on_progress=None: []
    ctx.get_quotes = lambda codes: {}
    ctx.get_kline = lambda code, period="day", limit=120: []
    return ctx


# ---------------------------------------------------------------- G1 重复连接

def test_monitor_no_duplicate_signal_connections(qapp, monkeypatch):
    """保存设置 N 次 → 监听页全部周期信号 receivers 必须恒为 1（R1+R2）。"""
    import warnings
    ctx = _ctx(qapp, monkeypatch)
    from stockpilot.ui.pages.monitor import MonitorPage
    mp = MonitorPage(ctx)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)   # disconnect 失败=爆
        for _ in range(5):
            mp.apply_settings()

    checks = {
        "自动扫描 timer": (mp.timer, "timeout()"),
        "盘后日报 timer": (mp.eod_timer, "timeout()"),
        "自动监听开关": (mp.auto_check, "toggled(bool)"),
        "间隔选择器": (mp.interval_spin, "valueChanged(int)"),
    }
    bad = {k: _n_receivers(o, s) for k, (o, s) in checks.items()
           if _n_receivers(o, s) != 1}
    assert not bad, f"重复连接未防住: {bad}（保存5次后应为1）"


def test_screener_no_duplicate_signal_connections(qapp, monkeypatch):
    """方案下拉重复 _load_plans 后 currentIndexChanged 只连一次（R2）。"""
    import warnings
    ctx = _ctx(qapp, monkeypatch)
    from stockpilot.ui.pages.screener import ScreenerPage
    sp = ScreenerPage(ctx)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        for _ in range(5):
            sp._load_plans()
    n = _n_receivers(sp.plan_combo, "currentIndexChanged(int)")
    assert n == 1, f"方案下拉重复连接: {n}（应为1）"


# ---------------------------------------------------------------- G2 对话框点击

def test_compare_dialog_buttons_click_no_crash(qapp, monkeypatch):
    """CompareDialog 全按钮真实点击不崩（v5.2.2 页面闸的对话框补层）。"""
    from PySide6.QtWidgets import QDialog, QCheckBox
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    import stockpilot.ui.compare_dialog as cd
    monkeypatch.setattr(cd, "submit", lambda *a, **k: None)
    ctx = _ctx(qapp, monkeypatch)
    dlg = cd.CompareDialog(
        ctx, [{"code": "600519", "name": "贵州茅台"},
              {"code": "300750", "name": "宁德时代"}])
    crashed = []
    for btn in dlg.findChildren(QAbstractButton):
        if not btn.isEnabled():
            continue
        try:
            btn.click()
        except Exception as exc:  # noqa: BLE001
            crashed.append(f"{btn.text()[:20]!r}: {type(exc).__name__} {exc}")
    # 勾选后再点一轮（OK 启用后的路径）
    for r in range(dlg.table.rowCount()):
        cb = dlg.table.cellWidget(r, 0).findChild(QCheckBox)
        if cb:
            cb.setChecked(True)
    for btn in dlg.findChildren(QAbstractButton):
        if not btn.isEnabled():
            continue
        try:
            btn.click()
        except Exception as exc:  # noqa: BLE001
            crashed.append(f"勾选后 {btn.text()[:20]!r}: {type(exc).__name__} {exc}")
    assert not crashed, "CompareDialog 点击崩溃: " + "; ".join(crashed)


# ---------------------------------------------------------------- G3 代理隔离

def test_http_sessions_ignore_system_proxy():
    """行情/AI/推送三类 HTTP 会话都必须 trust_env=False（R3：死系统代理
    会让全部请求 ProxyError——用户日志实锤的按钮"点击失败"根因）。"""
    from stockpilot.core.providers.base import HttpClient
    c = HttpClient()
    assert c.session.trust_env is False, "HttpClient 必须无视系统代理"

    from stockpilot.core.ai.client import AiConfig, OpenAIClient
    ai = OpenAIClient(AiConfig(base_url="http://localhost:1/v1",
                               model="m", api_key="k"))
    assert ai.session.trust_env is False, "OpenAIClient 必须无视系统代理"

    # notify._post 用独立会话：源码级断言（不发真请求）
    import inspect
    from stockpilot.core import notify as n
    src = inspect.getsource(n._post)
    assert "trust_env = False" in src, "notify._post 必须无视系统代理"


def test_explicit_proxy_still_applied():
    """用户显式配置代理时仍生效（trust_env=False 不影响显式 proxies）。"""
    from stockpilot.core.providers.base import HttpClient
    c = HttpClient(proxy="http://127.0.0.1:7890")
    assert c.session.trust_env is False
    assert c.session.proxies == {"http": "http://127.0.0.1:7890",
                                 "https": "http://127.0.0.1:7890"}
