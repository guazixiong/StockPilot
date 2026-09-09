"""demo（StockPilot-UI-V5-Fusion-Terminal）风格共享组件库。

对应 demo 中的：
- pageHead      → .simple-head（eyebrow 小字 + 大标题 + 副标题 + ● REALTIME DATA 徽章）
- Panel         → .panel（panel-head 标题行 + 内容区）
- StatCard      → .quote / .data-card（label + 大数字 + 说明）
- IndexQuote    → .overview-card / .ticker（指数卡：名称/大数/涨跌）
- Chip/Badge    → .tab 徽章、.badge
- SegTabs       → .chart-tabs / .quick-tabs / .sector-tabs（分段选项卡）

所有组件只做视觉；数据填充由各页面自行完成（底层功能不动）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                                QSizePolicy, QVBoxLayout, QWidget)

from . import theme as T


# ---------------------------------------------------------------- Panel
def panel(title: str = "", sub: str = "", more: str = "",
          ai: bool = False) -> QFrame:
    """demo .panel：渐变底 + 发光描边 + 12px 圆角。

    title/sub → panel-head 左侧；more → panel-head 右侧「更多 ›」。
    返回 QFrame，head 下已铺好内容区 QVBoxLayout（self.body_lay）。
    """
    f = QFrame()
    f.setProperty("card", True)
    if ai:
        f.setProperty("aiPanel", True)
    v = QVBoxLayout(f)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)
    if title or more:
        head = QFrame()
        head.setProperty("panelHead", True)
        head.setFixedHeight(40)
        h = QHBoxLayout(head)
        h.setContentsMargins(14, 0, 14, 0)
        t = QLabel(title)
        t.setProperty("panelTitle", True)
        h.addWidget(t)
        if sub:
            s = QLabel(sub)
            s.setProperty("panelSub", True)
            h.addSpacing(8)
            h.addWidget(s)
        h.addStretch(1)
        if more:
            m = QLabel(more)
            m.setProperty("panelMore", True)
            h.addWidget(m)
        v.addWidget(head)
    body = QWidget()
    body.setProperty("panelBody", True)
    body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
    f.body_lay = QVBoxLayout(body)
    f.body_lay.setContentsMargins(0, 0, 0, 0)
    v.addWidget(body, 1)
    return f


# ---------------------------------------------------------------- PageHead
def page_head(title: str, sub: str, badge: str = "● REALTIME DATA") -> QFrame:
    """demo .simple-head：eyebrow + 大标题 + 副标题 + 右侧徽章。"""
    f = QFrame()
    f.setProperty("pageHead", True)
    h = QHBoxLayout(f)
    h.setContentsMargins(2, 4, 2, 10)
    left = QVBoxLayout()
    left.setSpacing(3)
    eyebrow = QLabel("STOCKPILOT / FUSION TERMINAL")
    eyebrow.setProperty("eyebrow", True)
    t = QLabel(title)
    t.setProperty("pageTitle", True)
    s = QLabel(sub)
    s.setProperty("pageSub", True)
    left.addWidget(eyebrow)
    left.addWidget(t)
    left.addWidget(s)
    h.addLayout(left)
    h.addStretch(1)
    if badge:
        b = QLabel(badge)
        b.setProperty("liveBadge", True)
        b.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        h.addWidget(b, 0, Qt.AlignBottom)
    return f


# ---------------------------------------------------------------- StatCard
def stat_card(name: str, value: str = "—") -> QFrame:
    """demo .quote/.data-card：label + 大数字（value_label 可着色）。"""
    f = QFrame()
    f.setProperty("card", True)
    f.setProperty("stat", True)
    v = QVBoxLayout(f)
    v.setContentsMargins(15, 12, 15, 12)
    v.setSpacing(2)
    n = QLabel(name)
    n.setProperty("statName", True)
    val = QLabel(value)
    val.setProperty("statValue", True)
    v.addWidget(n)
    v.addWidget(val)
    f.value_label = val
    f.name_label = n
    return f


# ---------------------------------------------------------------- IndexQuote
def index_quote(name: str) -> QFrame:
    """demo .overview-card / .ticker：指数卡（名称/大数/涨跌小字 + sparkline）。"""
    f = QFrame()
    f.setProperty("card", True)
    f.setProperty("quote", True)
    v = QVBoxLayout(f)
    v.setContentsMargins(13, 10, 13, 9)
    v.setSpacing(2)
    n = QLabel(name)
    n.setProperty("statName", True)
    px = QLabel("—")
    px.setProperty("quoteValue", True)
    ch = QLabel("—")
    ch.setProperty("quoteChg", True)
    spark = SparkLine()
    spark.setFixedHeight(30)
    v.addWidget(n)
    v.addWidget(px)
    v.addWidget(ch)
    v.addWidget(spark)
    f.name_label, f.value_label, f.chg_label = n, px, ch
    f.spark = spark
    return f


def fill_index_quote(card: QFrame, q) -> None:
    """按 Quote 填充 index_quote 卡（涨跌着色 + sparkline 单点占位）。"""
    if q is None or q.price is None:
        return
    chg = q.change_pct or 0
    color = T.UP if chg > 0 else (T.DOWN if chg < 0 else T.FLAT)
    card.value_label.setText(f"{q.price:,.2f}")
    card.value_label.setStyleSheet(
        f"color:{color}; font-size:14pt; font-weight:800;"
        "background:transparent;")
    txt = f"{chg:+.2f}%"
    if q.change is not None:
        txt = f"{q.change:+.2f}　{txt}"
    card.chg_label.setText(txt)
    card.chg_label.setStyleSheet(f"color:{color}; font-weight:700;"
                                 "background:transparent;")


# ---------------------------------------------------------------- SparkLine
class SparkLine(QFrame):
    """迷你走势线（demo .ticker svg 的自绘等价物）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._vals: list = []
        self.setMinimumHeight(24)
        self.setStyleSheet("background:transparent;")

    def set_data(self, vals: list) -> None:
        self._vals = [v for v in (vals or []) if v is not None]
        self.update()

    def paintEvent(self, ev) -> None:  # noqa: N802
        from PySide6.QtGui import QColor, QPainter, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if len(self._vals) < 2:
            p.setPen(QColor(T.HINT))
            p.drawLine(2, h - 3, w - 2, h - 3)
            p.end()
            return
        vals = self._vals
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1.0
        pad = 2
        step = (w - pad * 2) / (len(vals) - 1)
        up = vals[-1] >= vals[0]
        color = QColor(T.UP if up else T.DOWN)
        pts = [(pad + step * i, pad + (hi - v) / span * (h - pad * 2 - 1))
               for i, v in enumerate(vals)]
        p.setPen(QPen(color, 1.6))
        for i in range(1, len(pts)):
            p.drawLine(int(pts[i - 1][0]), int(pts[i - 1][1]),
                       int(pts[i][0]), int(pts[i][1]))
        # 渐隐填充
        fill = QColor(color)
        fill.setAlpha(28)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        from PySide6.QtGui import QPolygonF
        from PySide6.QtCore import QPointF
        poly = QPolygonF([QPointF(x, y) for x, y in pts])
        poly.append(QPointF(pts[-1][0], h))
        poly.append(QPointF(pts[0][0], h))
        p.drawPolygon(poly)
        p.end()


# ---------------------------------------------------------------- SegTabs
class SegTabs(QFrame):
    """demo .chart-tabs / .quick-tabs：分段选项卡（active 有底部渐变亮块）。

    changed = Signal(str)  # 当前选中文字
    """

    changed = Signal(str)

    def __init__(self, items: list, active: int = 0, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:transparent;")
        self._items: list = []
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        for i, text in enumerate(items):
            b = QPushButton(text)
            b.setProperty("seg", True)
            b.setCursor(Qt.PointingHandCursor)
            b.setCheckable(True)
            b.setChecked(i == active)
            b.clicked.connect(lambda _=False, idx=i: self.select(idx))
            h.addWidget(b)
            self._items.append(b)

    def select(self, idx: int) -> None:
        if not (0 <= idx < len(self._items)):
            return
        for i, b in enumerate(self._items):
            b.setChecked(i == idx)
        self.changed.emit(self._items[idx].text())

    def current_text(self) -> str:
        for b in self._items:
            if b.isChecked():
                return b.text()
        return self._items[0].text() if self._items else ""


# ---------------------------------------------------------------- Chip
def chip(text: str, color: str = T.SUB, filled: bool = False) -> QLabel:
    """demo 概念徽章：胶囊小标签。"""
    lbl = QLabel(text)
    if filled:
        lbl.setStyleSheet(
            f"background:{color}; color:#fff; border-radius:9px;"
            "padding:2px 10px; font-size:8pt; font-weight:bold;")
    else:
        lbl.setStyleSheet(
            f"background:#102542; color:{color}; border-radius:9px;"
            f"padding:2px 10px; font-size:8pt; border:1px solid {T.BORDER};")
    lbl.setProperty("chip", True)
    return lbl
