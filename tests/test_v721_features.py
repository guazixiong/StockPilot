"""v7.2.1 功能迭代测试：
1. AI 多股对比（CompareDialog + build_compare_context + AI对比模板）
2. crashguard 闪退捕获（crash.log 写入/线程钩子/幂等安装）
3. 设置页运行日志查看器（crash.log / app.log 可查 + 清空）
"""
import importlib
import os
import sys
import threading

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton  # noqa: E402

import stockpilot.crashguard as cg  # noqa: E402


def _rx(btn, sig):
    r = btn.receivers(__import__("PySide6.QtCore",
                                 fromlist=["SIGNAL"]).SIGNAL(sig))
    return r if isinstance(r, int) else len(r)


def _ctx(qapp):
    """同 test_v441 模式：真实 Config（conftest 已切到临时 CWD），
    隔离网络扫描、行情与 K 线接口（端到端不真连外网）。"""
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    ctx = AppContext(Config())
    ctx.monitor.market_fetch = lambda max_count=2000: []
    ctx.get_quotes = lambda codes: {}
    ctx.get_kline = lambda code, period="day", limit=120: []
    return ctx


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- AI 对比

def test_compare_template_exists():
    from stockpilot.core.prompt import TEMPLATES, build_messages
    assert "AI对比" in TEMPLATES
    msgs = build_messages("AI对比", "ctx", "q")
    assert "对比总表" in msgs[1]["content"]
    assert "组合建议" in msgs[1]["content"]


def test_build_compare_context_same_fields():
    """同口径：每只股票输出相同字段块；行情缺失时降级提示而非崩。"""
    from stockpilot.core.models import Quote
    from stockpilot.core.prompt import build_compare_context
    q = Quote(code="600519", name="贵州茅台", price=1500.0, change_pct=1.2,
              turnover_rate=0.8, volume_ratio=1.5, amount=30000.0,
              pe=28.0, pb=9.0, float_mv=18000.0)
    text = build_compare_context([
        {"code": "600519", "name": "贵州茅台", "quote": q,
         "ind": {"ma5": 1490.0, "rsi6": 55}},
        {"code": "000001", "name": "平安银行", "quote": None, "ind": {}},
    ])
    assert "600519" in text and "MA5=1490.00" in text
    assert "共 2 只" in text
    assert "数据不可用" in text            # 缺行情股有降级说明


def test_compare_dialog_flow(qapp):
    """预填自选 → 勾选 → 计数/OK 启用 → 端到端 _run → ask_ai 发出。"""
    from stockpilot.ui.compare_dialog import CompareDialog
    ctx = _ctx(qapp)
    captured = []
    dlg = CompareDialog(
        ctx, [{"code": "600519", "name": "a"}, {"code": "300750", "name": "b"}])
    dlg.ask_ai.connect(lambda c, t: captured.append((c, t)))
    assert dlg.table.rowCount() == 2
    assert not dlg.ok_btn.isEnabled(), "初始未勾选，OK 应禁用"
    # 手动添加第三只（默认勾上）
    dlg.code_edit.setText("601318")
    dlg._add_code()
    assert dlg.table.rowCount() == 3
    # 勾选前两只
    for r in range(2):
        cb = dlg.table.cellWidget(r, 0).findChild(QCheckBox)
        cb.setChecked(True)
    assert len(dlg._checked_codes()) == 3
    assert dlg.ok_btn.isEnabled(), "勾选≥2 后应可启动"
    assert dlg.count_label.text().startswith("已选 3")
    # 端到端：_run → 后台取数（stub 空行情）→ _on_ready → ask_ai
    dlg._run()
    def _poll():
        if captured or dlg.result() == QDialog_ACCEPTED:
            qapp.quit()
    from PySide6.QtCore import QTimer
    QTimer(qapp, interval=50, timeout=_poll)
    QTimer.singleShot(5000, qapp.quit)
    qapp.exec()
    assert captured, "ask_ai 应已发出（行情 stub 为空也不会卡死）"
    body, title = captured[0]
    assert "多股对比" in body and title.startswith("AI对比")


QDialog_ACCEPTED = 3  # QDialog.Accepted


def test_market_compare_button_wired(qapp):
    """行情页「AI 对比」按钮必须有接收者（死按钮闸）且有 ask_ai 信号。"""
    from stockpilot.ui.pages.market import MarketPage
    mp = MarketPage(_ctx(qapp))
    assert hasattr(mp, "ask_ai") and hasattr(mp, "open_compare")
    btns = [b for b in mp.findChildren(QPushButton) if b.text() == "AI 对比"]
    assert len(btns) == 1
    assert _rx(btns[0], "clicked()") >= 1


def test_compare_dialog_upper_limit(qapp):
    """MAX 上限保护：勾选超过上限的股票计数带提示、OK 禁用。"""
    from stockpilot.ui.compare_dialog import CompareDialog
    ctx = _ctx(qapp)
    watch = [{"code": f"60000{i}", "name": f"s{i}"} for i in range(9)]
    dlg = CompareDialog(ctx, watch)
    for r in range(dlg.table.rowCount()):
        cb = dlg.table.cellWidget(r, 0).findChild(QCheckBox)
        cb.setChecked(True)
    n = len(dlg._checked_codes())
    assert n > dlg.MAX
    assert not dlg.ok_btn.isEnabled(), "超过上限 OK 应禁用"
    assert f"上限 {dlg.MAX}" in dlg.count_label.text()


# ---------------------------------------------------------------- 闪退捕获

def test_crashguard_write_crash(tmp_path, monkeypatch):
    monkeypatch.setattr("stockpilot.core.storage.data_dir", lambda: tmp_path)
    importlib.reload(cg)
    try:
        raise ValueError("闪退测试")
    except ValueError as e:
        p = cg.write_crash("未捕获异常(主线程)", exc=e)
    assert p is not None and p.exists()
    text = p.read_text(encoding="utf-8")
    assert "ValueError" in text and "闪退测试" in text
    assert "pid=" in text and "线程=" in text and "类型=" in text


def test_crashguard_install_hooks(tmp_path, monkeypatch):
    """install() 幂等，且装上后 sys/threading excepthook 均被替换，
    后台线程异常能落盘（qInstallMessageHandler 也被接管）。"""
    monkeypatch.setattr("stockpilot.core.storage.data_dir", lambda: tmp_path)
    importlib.reload(cg)
    cg.install()
    cg.install()                       # 幂等
    assert sys.excepthook is cg._py_hook
    assert threading.excepthook is cg._thread_hook
    # 后台线程异常 → crash.log（threading.excepthook 真实链路）
    err = []

    def boom():
        err.append(1 / 0)

    t = threading.Thread(target=boom)
    try:
        t.start()
        t.join(3)
    finally:
        pass
    crash = (tmp_path / "logs" / "crash.log")
    assert crash.exists(), "线程异常应写入 crash.log"
    assert "ZeroDivisionError" in crash.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 设置页日志查看器

def test_settings_log_viewer(qapp):
    """设置页运行日志查看器：crash/app 两个 tab 可读 + 清空可用。"""
    from stockpilot.ui.pages.settings import SettingsPage
    sp = SettingsPage(_ctx(qapp))
    assert hasattr(sp, "log_view_crash") and hasattr(sp, "log_view_app")
    assert sp.log_tabs.count() == 2
    assert sp._group_box_map.get("运行日志") is not None
    # crash.log 不存在时给占位文案而非空/崩
    assert "暂无崩溃记录" in sp.log_view_crash.toPlainText()
    sp._clear_crash_log()
    assert "暂无崩溃记录" in sp.log_view_crash.toPlainText()
