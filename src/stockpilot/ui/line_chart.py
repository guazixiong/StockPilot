"""通用折线图（回测收益曲线等）：基线 + 渐变填充 + 悬停数值。"""
from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QLinearGradient, QPen, QBrush
from PySide6.QtWidgets import QWidget

from .theme import CHART_BG, CHART_GRID, SUB

BG = QColor(CHART_BG)
GRID = QColor(CHART_GRID)
TEXT = QColor(SUB)


class LineChart(QWidget):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.title = title
        self.points: List[Tuple[str, float]] = []
        self.base: Optional[float] = None
        from .theme import ACCENT
        self.color = ACCENT
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        self.hover: Optional[int] = None

    def set_series(self, points: List[Tuple[str, float]],
                   base: Optional[float] = None,
                   color: str = None) -> None:
        if color is None:
            from .theme import ACCENT
            color = ACCENT
        self.points = points or []
        self.base = base
        self.color = color
        self.hover = None
        self.update()

    def clear(self) -> None:
        self.set_series([])

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self.points:
            left, right = 8, self.width() - 8
            plot_w = max(right - left, 1)
            i = int((ev.position().x() - left) / plot_w * len(self.points))
            self.hover = i if 0 <= i < len(self.points) else None
            self.update()
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev) -> None:  # noqa: N802
        self.hover = None
        self.update()
        super().leaveEvent(ev)

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), BG)
        w, h = self.width(), self.height()
        top, bottom, left, right = 22, 18, 8, 52
        plot_h = max(h - top - bottom, 4)
        plot_w = max(w - left - right, 4)
        p.setFont(QFont("Microsoft YaHei UI", 7))

        if not self.points:
            p.setPen(TEXT)
            p.drawText(self.rect(), Qt.AlignCenter, self.title or "暂无数据")
            p.end()
            return

        values = [v for _, v in self.points]
        lo, hi = min(values), max(values)
        if self.base is not None:
            lo, hi = min(lo, self.base), max(hi, self.base)
        if hi <= lo:
            hi = lo * 1.0001 + 1e-6
        span = hi - lo

        def py(v: float) -> float:
            return top + (hi - v) / span * plot_h

        # 基线
        if self.base is not None:
            p.setPen(QPen(GRID, 1, Qt.DashLine))
            p.drawLine(left, int(py(self.base)), left + plot_w, int(py(self.base)))

        # 网格 + 右侧标签
        p.setPen(QPen(GRID, 1))
        for i in range(3):
            y = top + plot_h * i / 2
            p.drawLine(left, int(y), left + plot_w, int(y))
            p.setPen(TEXT)
            p.drawText(QRectF(left + plot_w + 4, y - 7, right - 8, 14),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       f"{hi - span * i / 2:,.2f}")

        n = len(self.points)
        step = plot_w / max(n - 1, 1)
        pts = [(left + step * i, py(v)) for i, (_, v) in enumerate(self.points)]

        # 渐变填充
        path_color = QColor(self.color)
        fill = QLinearGradient(0, top, 0, top + plot_h)
        fill.setColorAt(0, QColor(self.color[0:7] + "40")
                        if len(self.color) == 7 else path_color)
        fill.setColorAt(1, QColor("#00000000"))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(fill))
        poly_pts = pts + [(pts[-1][0], top + plot_h), (pts[0][0], top + plot_h)]
        from PySide6.QtGui import QPolygonF
        p.drawPolygon(QPolygonF(poly_pts))

        # 折线
        p.setPen(QPen(path_color, 1.6))
        for a, b in zip(pts, pts[1:]):
            p.drawLine(int(a[0]), int(a[1]), int(b[0]), int(b[1]))

        # 标题 / 悬停
        p.setPen(TEXT)
        p.drawText(QRectF(left, 2, w - right - left, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, self.title)
        idx = self.hover if self.hover is not None else n - 1
        label, value = self.points[idx]
        if self.hover is not None:
            p.setPen(QPen(QColor("#3A4A63"), 1, Qt.DashLine))
            p.drawLine(int(pts[idx][0]), top, int(pts[idx][0]), top + plot_h)
        p.setPen(QColor("#D9E8FF"))
        p.drawText(QRectF(w - right - 240, 2, 236, 16),
                   Qt.AlignRight | Qt.AlignVCenter, f"{label}  {value:,.2f}")
        p.end()
