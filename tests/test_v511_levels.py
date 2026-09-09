"""v5.1.1：Tab 切换压力/支撑位丢失修复的防回归。

用户报告：个股 月K → 分时 → 月K，切回后压力位全部消失。
根因：_on_tab_change 的缓存快速路径只 set_data(klines) 不带 levels，
而 KLineChart.set_data 内 `self.levels = levels or {}` 会清空。
"""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def detail(qapp, monkeypatch, tmp_path):
    import stockpilot.core.session_store as ss
    monkeypatch.setattr(ss, "data_dir", lambda: tmp_path)
    import stockpilot.ui.pages.detail as dmod
    monkeypatch.setattr(dmod, "submit",
                        lambda fn, *a, on_done=None, on_err=None, **kw: None)
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    from stockpilot.ui.pages.detail import DetailDialog
    from stockpilot.core.models import KLine
    ctx = AppContext(Config())
    ctx.monitor.market_fetch = lambda max_count=2000: []
    d = DetailDialog(ctx, "600519", "t")
    d.klines["month"] = [KLine(date=f"{2023 + m // 12}-{m % 12 + 1:02d}-15",
                               open=10.0 + m % 12, close=11.0 + m % 12,
                               high=12.0 + m % 12, low=9.0 + m % 12,
                               volume=100.0, amount=1000.0, change_pct=2.0,
                               turnover=3.0) for m in range(36)]   # ≥30 根才有筹码
    d.quote = type("Q", (), {"price": 11.5})()
    return d


def test_tab_roundtrip_keeps_levels(detail):
    """月K → 分时 → 月K：levels 不丢且内容一致（回归闸）。"""
    d = detail
    d._on_kline_loaded(("month", d.klines["month"]))   # 首次加载路径
    lv_first = dict(d.charts["month"].levels)
    assert lv_first, "前置：首次加载应有压力/支撑位"
    d.tabs.setCurrentIndex(0)        # 分时
    d.tabs.setCurrentIndex(3)        # 切回月K（缓存路径——修复点）
    lv_back = d.charts["month"].levels
    assert lv_back, "切回后 levels 被清空（v5.1.1 修的 bug）"
    assert set(lv_back) == set(lv_first)


def test_tab_roundtrip_keeps_cyq(detail):
    """筹码曲线随 levels 一同保留（同一次 set_data 参数）。"""
    d = detail
    d._on_kline_loaded(("month", d.klines["month"]))
    cyq_first = d.charts["month"].cyq
    d.tabs.setCurrentIndex(0)
    d.tabs.setCurrentIndex(3)
    assert d.charts["month"].cyq is not None, "切回后筹码曲线丢失"
    assert cyq_first is not None
