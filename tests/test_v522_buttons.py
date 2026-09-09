"""v5.2.2：全按钮"真实点击"审计（第三层闸：点击不崩）。

历史死按钮三层闸演进：
- v4.2 静态审计（connect 存在）
- v4.4.1 运行时 receivers 审计（有接收者+反馈即时性）
- v5.2.2 真实点击审计（本文件）：全部按钮 .click() 不抛异常——
  本轮真凶 = MarketPage 调 window().statusBar()（QWidget 页面无此 API），
  异常被 Qt 吞掉 → 用户看到"点击没反应"。
"""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import (QAbstractButton, QApplication, QDialog,  # noqa: E402
                               QFileDialog, QMessageBox, QToolButton)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def silent_dialogs(qapp, monkeypatch):
    """弹窗/文件选择全拦截（点击安全：不弹原生对话框阻塞）。"""
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.No))
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    # 自定义对话框的 exec/exec_ 同样拦截（StrategyDialog/编辑器等模态窗口
    # 在审计中会真实弹出阻塞——点击审计要验证"不崩"，不是"能弹窗"）
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    monkeypatch.setattr(QDialog, "exec_", lambda self: 0)
    # QInputDialog.getText 同为原生模态（保存方案等入口）——审计中取消
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("", False)))
    monkeypatch.setattr(QInputDialog, "getItem",
                        staticmethod(lambda *a, **k: ("", False)))
    monkeypatch.setattr(QInputDialog, "getInt",
                        staticmethod(lambda *a, **k: (0, False)))


def _ctx(qapp, monkeypatch):
    import stockpilot.core.session_store as ss
    import tempfile
    monkeypatch.setattr(ss, "data_dir", lambda: tempfile.mkdtemp())
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    ctx = AppContext(Config())
    ctx.monitor.market_fetch = lambda max_count=2000: []
    ctx.opportunity_scan = lambda rows, on_progress=None: []
    import stockpilot.ui.pages.detail as dmod
    monkeypatch.setattr(dmod, "submit", lambda *a, **k: None)
    return ctx


def _click_all(qapp, monkeypatch, ctx, mod_name, cls):
    """构建页面并对全部可交互按钮 .click()——任何未捕获异常=死钮。"""
    import importlib
    mod = importlib.import_module(f"stockpilot.ui.pages.{mod_name}")
    monkeypatch.setattr(mod, "submit", lambda *a, **k: None)
    w = cls(ctx)
    crashed = []
    for btn in w.findChildren(QAbstractButton):
        if not btn.isEnabled():
            continue
        parent = btn.parentWidget()
        if parent is not None and parent.__class__.__name__ == "QTabBar":
            continue
        if isinstance(btn, QToolButton) and btn.menu() is not None:
            continue
        try:
            btn.click()
        except Exception as exc:  # noqa: BLE001
            crashed.append(f"{btn.text()[:20]!r}: {type(exc).__name__} {exc}")
    assert not crashed, f"[{mod_name}] 点击崩溃: " + "; ".join(crashed)


@pytest.mark.parametrize("page", [
    ("home", "HomePage"), ("market", "MarketPage"), ("boards", "BoardsPage"),
    ("monitor", "MonitorPage"), ("backtest", "BacktestPage"),
    ("positions", "PositionsPage"), ("ai", "AiPage"), ("news", "NewsPage"),
    ("settings", "SettingsPage"), ("limitup", "LimitUpPage"),
    ("screener", "ScreenerPage"),
])
def test_all_buttons_click_no_crash(qapp, silent_dialogs, monkeypatch, page):
    """11 页全部按钮真实点击不崩（v5.2.2 修的 statusBar 事故回归闸）。"""
    mod_name, cls_name = page
    ctx = _ctx(qapp, monkeypatch)
    import importlib
    cls = getattr(importlib.import_module(
        f"stockpilot.ui.pages.{mod_name}"), cls_name)
    _click_all(qapp, monkeypatch, ctx, mod_name, cls)


def test_no_statusbar_on_widget_pages():
    """源码级闸：QWidget 页面禁止再调 window().statusBar()（QMainWindow 专属）。"""
    import pathlib
    ui_dir = pathlib.Path(__file__).resolve().parents[1] / "src" / "stockpilot" / "ui"
    offenders = []
    for f in ui_dir.glob("pages/*.py"):
        lines = f.read_text(encoding="utf-8").splitlines()
        notify_def = next((i for i, ln in enumerate(lines, 1)
                           if "def _notify" in ln), None)
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if ".statusBar()" in line and not stripped.startswith("#"):
                # 合法窗口：_notify 方法体（def 行起 14 行内）
                if notify_def is not None and 0 <= i - notify_def <= 14:
                    continue
                offenders.append(f"{f.name}:{i}")
    assert not offenders, "页面直接调 statusBar()（QWidget 无此 API）: " + ", ".join(offenders)
