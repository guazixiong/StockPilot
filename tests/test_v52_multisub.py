"""v5.2：副图多指标同时显示（用户报告"怎么只能看一个"）。

旧：QComboBox 单选 MACD/KDJ/RSI/无。
新：QToolButton+QMenu 可勾选多选 → 堆叠绘制（各指标均分副图区、
独立坐标互不压缩、动态扩容高度 20%/28%/36%）。
"""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def chart(qapp):
    from stockpilot.ui.kline_chart import CandleChart
    from stockpilot.core.models import KLine
    c = CandleChart()
    c.resize(900, 500)
    c.set_data([KLine(date=f"2026-07-{(i % 28) + 1:02d}", open=10.0 + i * 0.1,
                      close=10.5 + i * 0.1, high=11.0 + i * 0.1,
                      low=9.5 + i * 0.1, volume=1000.0, amount=10000.0,
                      change_pct=1.0, turnover=2.0) for i in range(60)])
    return c


def test_sub_modes_multi_select(chart):
    """set_sub_modes：多选保序去重、去'无'。"""
    chart.set_sub_modes(["RSI", "MACD", "MACD", "无", "KDJ"])
    assert chart.sub_modes == ["RSI", "MACD", "KDJ"]
    chart.set_sub_modes([])
    assert chart.sub_modes == []
    chart.set_sub_modes(["不存在"])
    assert chart.sub_modes == []


def test_legacy_set_sub_mode_compat(chart):
    """旧单选接口兼容：诊断/外部仍可能传字符串。"""
    chart.set_sub_mode("KDJ")
    assert chart.sub_modes == ["KDJ"]
    chart.set_sub_mode("无")
    assert chart.sub_modes == []


@pytest.mark.parametrize("modes", (
    [], ["MACD"], ["MACD", "KDJ"], ["MACD", "KDJ", "RSI"]))
def test_stacked_render_no_crash(chart, modes):
    """1/2/3 指标堆叠渲染到底（paintEvent 真实执行）。"""
    chart.set_sub_modes(modes)
    img = chart.grab()
    assert not img.isNull()


def test_detail_dialog_multi_sub_sync(qapp, monkeypatch, tmp_path):
    """详情窗：勾选菜单 → 三周期图表同步 + 按钮文案聚合。"""
    import stockpilot.core.session_store as ss
    monkeypatch.setattr(ss, "data_dir", lambda: tmp_path)
    import stockpilot.ui.pages.detail as dmod
    monkeypatch.setattr(dmod, "submit",
                        lambda fn, *a, on_done=None, on_err=None, **kw: None)
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    from stockpilot.ui.pages.detail import DetailDialog
    ctx = AppContext(Config())
    ctx.monitor.market_fetch = lambda max_count=2000: []
    d = DetailDialog(ctx, "600519", "t")
    # 勾选两个
    d._sub_actions["KDJ"].setChecked(True)
    assert d.charts["day"].sub_modes == ["MACD", "KDJ"]
    assert d.charts["week"].sub_modes == ["MACD", "KDJ"]
    assert d.charts["month"].sub_modes == ["MACD", "KDJ"]
    assert d.sub_btn.text() == "副图: MACD/KDJ"
    # 逐项取消：取消最后一个时自动勾回（防呆，不允许点选路径全空）
    d._sub_actions["KDJ"].setChecked(False)   # 剩 MACD
    assert d.charts["day"].sub_modes == ["MACD"]
    d._sub_actions["MACD"].setChecked(False)  # 最后一个：自动勾回
    assert d.charts["day"].sub_modes == ["MACD"], "取消最后一项应自动勾回"
    # 全选 / 菜单「清空」= 纯K线（合法态）
    d._set_all_subs(True)
    assert d.charts["month"].sub_modes == ["MACD", "KDJ", "RSI"]
    d._set_all_subs(False)
    assert d.charts["day"].sub_modes == []
    assert d.sub_btn.text() == "副图: 无"
    assert d.charts["day"].sub_modes == []
