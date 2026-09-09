"""v7.2：错误呈现规范（用户要求：异常弹窗报错，不进页面布局）单测。"""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_show_error_throttles_duplicates(qapp):
    """弹窗节流：同键 10 秒内只弹一次（后台每轮扫描都失败时不轰炸）。"""
    from stockpilot.ui import err
    err._last_popup.clear()
    pops = []
    from PySide6.QtWidgets import QMessageBox
    orig = QMessageBox.warning
    QMessageBox.warning = staticmethod(lambda *a, **k: pops.append(a))
    try:
        err.show_error(None, "测试", "同一错误 A")
        err.show_error(None, "测试", "同一错误 A")   # 节流内 → 不弹
        err.show_error(None, "测试", "不同错误 B")   # 新键 → 弹
    finally:
        QMessageBox.warning = orig
    assert len(pops) == 2
    assert pops[0][1] == "测试"


def test_fail_hint_truncates(qapp):
    """状态区短文案：超长错误自动截断（不撑破单行 hint）。"""
    from stockpilot.ui import err
    lbl = QLabel()
    err.fail_hint(lbl, "x" * 200, max_len=20)
    assert len(lbl.text()) <= 21 and lbl.text().endswith("…")
    err.fail_hint(lbl, "短文案")
    assert lbl.text() == "短文案"


def test_shorten():
    """弹窗正文压缩：去换行+截断。"""
    from stockpilot.ui import err
    assert err.shorten("line1" + chr(10) + "line2") == "line1 line2"
    assert len(err.shorten("y" * 300)) <= 61


def test_detail_error_not_in_layout(qapp, monkeypatch, tmp_path):
    """核心验收：行情加载失败时——价格位是占位符（—），长错误不在主布局。"""
    import stockpilot.core.session_store as ss
    monkeypatch.setattr(ss, "data_dir", lambda: tmp_path)
    import stockpilot.ui.pages.detail as dmod
    monkeypatch.setattr(dmod, "submit", lambda *a, **k: None)
    from PySide6.QtWidgets import QMessageBox
    pops = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: pops.append(a[2])))
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    from stockpilot.ui.pages.detail import DetailDialog
    ctx = AppContext(Config())
    ctx.monitor.market_fetch = lambda max_count=2000: []
    d = DetailDialog(ctx, "600519", "t")
    long_err = ("请求失败 https://push2.eastmoney.com/api/... HTTPSConnectionPool "
                "Max retries exceeded" + "x" * 300)
    d._on_error(long_err)
    price_text = d.price_label.text()
    # 主布局价格位：短（占位符），绝不包含长错误
    assert "请求失败" not in price_text and "HTTPSConnectionPool" not in price_text
    assert len(price_text) < 40, f"价格位被错误文本撑破: {price_text[:60]}"
    # 错误进了弹窗
    assert len(pops) == 1 and "HTTPSConnectionPool" in pops[0]


def test_backtest_error_popup_not_progress(qapp, monkeypatch, tmp_path):
    """回测失败：进度区短文案，错误全文走弹窗。"""
    import stockpilot.core.session_store as ss
    monkeypatch.setattr(ss, "data_dir", lambda: tmp_path)
    from PySide6.QtWidgets import QMessageBox
    pops = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: pops.append(a[2])))
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    from stockpilot.ui.pages.backtest import BacktestPage
    ctx = AppContext(Config())
    ctx.monitor.market_fetch = lambda max_count=2000: []
    bp = BacktestPage(ctx)
    bp._on_err("K线获取失败 600519: 全部源失败 " + "z" * 200)
    # 进度区：≤20 字短状态
    assert "详见弹窗" in bp.progress.text() or len(bp.progress.text()) <= 21
    assert "全部源失败" not in bp.progress.text()
    assert len(pops) == 1
