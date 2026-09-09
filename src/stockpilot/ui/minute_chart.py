"""分时图：价格线 + 均价线 + 昨收基准 + 分时量（横轴固定 9:30~15:00）。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..core.models import MinuteSeries

from .theme import CHART_BG, CHART_GRID, MA5, SUB, UP as _UP, DOWN as _DOWN

BG = QColor(CHART_BG)
GRID = QColor(CHART_GRID)
TEXT = QColor(SUB)
UP = QColor(_UP)
DOWN = QColor(_DOWN)
AVG = QColor(MA5)


class MinuteChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.series: Optional[MinuteSeries] = None
        self._empty = "暂无分时数据（非交易时段或接口不可用）"
        self.setMinimumHeight(300)

    def set_series(self, series: Optional[MinuteSeries]) -> None:
        self.series = series
        self.update()

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), BG)
        w, h = self.width(), self.height()
        left, right_margin, top, bottom = 8, 58, 22, 18
        plot_w = max(w - left - right_margin, 10)
        total_h = max(h - top - bottom, 10)
        price_h = int(total_h * 0.76)
        vol_top = top + price_h + 10
        vol_h = max(total_h - price_h - 10, 8)
        p.setFont(QFont("Microsoft YaHei UI", 7))

        if not self.series or not self.series.points:
            p.setPen(TEXT)
            p.drawText(self.rect(), Qt.AlignCenter, self._empty)
            p.end()
            return

        pts = self.series.points
        n = len(pts)
        prev = self.series.prev_close
        if prev is None:
            prev = next((x.price for x in pts if x.price), 0.0) or 1.0
        prices = [x.price for x in pts if x.price is not None]
        avgs = [x.avg for x in pts if x.avg is not None]
        hi = max(prices + avgs + [prev]) if prices else prev
        lo = min(prices + avgs + [prev]) if prices else prev
        # 对称区间（分时图惯例）
        dev = max(abs(hi - prev), abs(prev - lo), prev * 0.001)
        hi, lo = prev + dev, prev - dev
        span = (hi - lo) or 1.0

        def py(v: float) -> float:
            return top + (hi - v) / span * price_h

        def px(i: int) -> float:  # 时间均匀铺满全天 242 分钟
            return left + plot_w * (i / max(n - 1, 1))

        # 网格 + 价格标签
        for i in range(5):
            y = top + price_h * i / 4
            p.setPen(QPen(GRID, 1))
            p.drawLine(left, int(y), left + plot_w, int(y))
            p.setPen(TEXT)
            p.drawText(QRectF(left + plot_w + 4, y - 7, right_margin - 8, 14),
                       Qt.AlignLeft | Qt.AlignVCenter, f"{hi - span * i / 4:.2f}")

        # 昨收基准
        p.setPen(QPen(QColor("#3A4A63"), 1, Qt.DashLine))
        p.drawLine(left, int(py(prev)), left + plot_w, int(py(prev)))

        # 分时量
        max_vol = max((x.volume for x in pts), default=1) or 1
        step = plot_w / max(n, 1)
        for i, x in enumerate(pts):
            v = x.volume / max_vol * vol_h
            color = UP if x.price is not None and x.price >= prev else DOWN
            p.fillRect(QRectF(px(i) - step / 2, vol_top + vol_h - v,
                              max(step, 1.2), v), color)

        # 均价线
        p.setPen(QPen(AVG, 1))
        pts_avg = [(px(i), py(x.avg)) for i, x in enumerate(pts) if x.avg]
        for a, b in zip(pts_avg, pts_avg[1:]):
            p.drawLine(int(a[0]), int(a[1]), int(b[0]), int(b[1]))

        # 价格线
        last = pts[-1].price or prev
        line_color = UP if last >= prev else DOWN
        p.setPen(QPen(line_color, 1.5))
        pts_price = [(px(i), py(x.price)) for i, x in enumerate(pts)
                     if x.price is not None]
        for a, b in zip(pts_price, pts_price[1:]):
            p.drawLine(int(a[0]), int(a[1]), int(b[0]), int(b[1]))

        # 时间轴（固定 9:30 / 11:30 / 14:00 / 15:00 位置）
        p.setPen(TEXT)
        for frac, label in ((0.0, "09:30"), (0.5, "11:30/13:00"),
                            (0.75, "14:00"), (1.0, "15:00")):
            x = left + plot_w * frac
            p.drawText(QRectF(x - 36, h - bottom + 2, 72, 14),
                       Qt.AlignCenter, label)

        # 顶部信息
        chg = (last - prev) / prev * 100 if prev else 0
        color = UP if chg >= 0 else DOWN
        p.setPen(QColor("#D9E8FF"))
        p.drawText(QRectF(left, 2, w - right_margin - left, 16),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   f"{self.series.date}  现价 {last:.2f}")
        p.setPen(color)
        p.drawText(QRectF(left + 170, 2, 200, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, f"{chg:+.2f}%")
        p.setPen(TEXT)
        p.drawText(QRectF(w - right_margin - 200, 2, 196, 16),
                   Qt.AlignRight | Qt.AlignVCenter, f"昨收 {prev:.2f}")
        p.end()
