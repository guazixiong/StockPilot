"""v7.2.7 回归闸：缺陷导出 2026-09-10 的四个缺陷。

①③（致命，同根因）：K线图 mouseMoveEvent 调 self._py(lv) 但 _py 只是
   paintEvent 内局部闭包 → 每次 mouseMove 抛 AttributeError → crashguard
   弹窗刷屏（缺陷①"点 OK 后依旧重复弹"）+ 个股 K 线页弹窗（缺陷③）。
   修复：换算提为实例方法，状态（_py_top/_py_h）存实例由 paintEvent 刷新。
②（致命）：heat_widgets.py paintEvent 用 QRectF 但模块没导入 →
   热度版块一渲染就崩。修复：QtCore 导入行补 QRectF。
④（严重）：分辨率适配，v7.2.6 已修（见 test_v726_resolution.py 5 闸），
   本文件仅冒烟最小尺寸不回归。
另：crashguard 同签名弹窗 30 秒冷却（缺陷①的重复弹窗面）。
"""
import math

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent          # noqa: E402
from PySide6.QtWidgets import QApplication     # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _klines(n=40):
    from stockpilot.core.models import KLine
    out, price = [], 10.0
    for i in range(n):
        o = price
        price = max(1.0, price + (i % 7 - 3) * 0.05)
        out.append(KLine(date=f"d{i}", open=o, high=max(o, price) + 0.1,
                         low=min(o, price) - 0.1, close=price,
                         volume=1000 + i))
    return out


def _move(w, x, y):
    return QMouseEvent(QEvent.MouseMove, QPointF(x, y), QPointF(x, y),
                       Qt.NoButton, Qt.NoButton, Qt.NoModifier)


class TestKlinePy:
    """缺陷①③：CandleChart._py 实例化。"""

    def test_py_is_instance_method(self):
        from stockpilot.ui.kline_chart import CandleChart
        assert callable(getattr(CandleChart, "_py", None)), \
            "_py 必须是类级方法（曾为 paintEvent 闭包导致 mouseMove 抛错）"

    def test_mousemove_before_first_paint_safe(self, qapp):
        """首帧前（_py_h=0）悬停：NaN 不命中也不抛。"""
        from stockpilot.ui.kline_chart import CandleChart
        c = CandleChart()
        c.resize(800, 500)
        c.set_data(_klines(), levels={"压力": 11.0})
        c.mouseMoveEvent(_move(c, 400, 100))
        assert math.isnan(c._py(10.0))

    def test_mousemove_after_paint_hits_level(self, qapp):
        """绘制后沿价格区扫过：不抛且能命中标注线。"""
        from stockpilot.ui.kline_chart import CandleChart
        c = CandleChart()
        c.resize(800, 500)
        c.set_data(_klines(), levels={"压力位": 11.5, "支撑位": 8.5})
        c.grab()                                   # 强制 paintEvent 写状态
        assert c._py_h > 0
        hit = None
        for step in range(20):                     # 修复前首步即抛
            y = c._py_top + c._py_h * step / 19
            c.mouseMoveEvent(_move(c, 400, y))
            hit = hit or c._level_hit
        assert hit is not None, "扫过价格区应命中至少一条标注线"

    def test_py_maps_price_into_plot(self, qapp):
        from stockpilot.ui.kline_chart import CandleChart
        c = CandleChart()
        c.resize(800, 500)
        c.set_data(_klines(), levels={"压力": 11.0})
        c.grab()
        mid = (c._py_hi + c._py_lo) / 2
        y = c._py(mid)
        assert c._py_top <= y <= c._py_top + c._py_h, \
            f"中间价应落在绘图区内: {y} ∉ [{c._py_top}, {c._py_top + c._py_h}]"


class TestHeatQRectF:
    """缺陷②：heat_widgets QRectF 可用。"""

    def test_modlevel_qrectf(self):
        import stockpilot.ui.heat_widgets as hw
        assert hasattr(hw, "QRectF"), "QRectF 必须模块级可用"

    def test_flowbar_renders(self, qapp):
        from stockpilot.ui.heat_widgets import FlowBar
        f = FlowBar("t")
        f.resize(300, 74)
        f.animate_to(3.5, 8.0)
        f.grab()                                    # 修复前 NameError

    def test_hbar_and_heattile_render(self, qapp):
        from stockpilot.core.models import Board
        from stockpilot.ui.heat_widgets import HBar, HeatTile
        b = HBar("t")
        b.resize(300, 30)
        b.animate_to(0.7, "123 亿 +2.5%")
        b.grab()
        t = HeatTile(Board(name="电力", change_pct=2.5, amount_yi=450,
                           count=30, leader_code="600000",
                           leader_name="X", leader_pct=5.1))
        t.resize(120, 76)
        t.grab()


class TestCrashDlgCooldown:
    """缺陷①的重复弹窗面：同签名 30 秒冷却。"""

    def test_same_signature_pops_once(self, qapp, monkeypatch, tmp_path):
        import stockpilot.crashguard as cg
        from PySide6.QtWidgets import QMessageBox

        # 落盘也走钩子——必须沙箱化 data_dir，否则写进真实 crash.log
        # 污染其他用例（v721 日志查看器预期"暂无崩溃记录"）
        monkeypatch.setattr("stockpilot.core.storage.data_dir",
                            lambda: tmp_path)
        popped = []
        monkeypatch.setattr(QMessageBox, "critical",
                            staticmethod(lambda p, t, b, *a, **k: popped.append(b)))
        cg._last_dlg = None
        try:
            for _ in range(5):
                cg._py_hook(AttributeError,
                            AttributeError("no attribute _py"), None)
            assert len(popped) == 1, f"5 连同签名应只弹 1 次, 实 {len(popped)}"
            cg._py_hook(ValueError, ValueError("different"), None)
            assert len(popped) == 2, "换签名应穿透冷却再弹"
        finally:
            cg._last_dlg = None


class TestResolutionSmoke:
    """缺陷④：v7.2.6 已修，此处防最小尺寸回归。"""

    def test_min_window_size(self, qapp):
        from stockpilot.ui.app import MainWindow
        w = MainWindow()
        assert w.minimumWidth() <= 1200 and w.minimumHeight() <= 700
