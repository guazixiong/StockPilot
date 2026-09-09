"""机会卡片：把一条交易信号表达成"一眼能看懂的机会"。

结构：策略徽章+名称 / 现价+价位区间条(止损-现价-目标) / 迷你走势+风险+评级，
底部命中规则 chips 与操作按钮。OpportunityFlow 为卡片滚动容器。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from ..core.models import TradeSignal
from .theme import (ACCENT, DOWN, FLAT, HINT, PANEL2, SUB, SUCCESS,
                    TEXT, UP, WARNING)

BORDER_HEX = "#173351"
GRADE_COLOR = {"A": WARNING, "B": ACCENT, "C": FLAT}


def _chip(text: str, color: str = SUB) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"background:{PANEL2}; color:{color}; border:1px solid {BORDER_HEX};"
        "border-radius:8px; padding:2px 8px; font-size:8pt;")
    return lbl


class Sparkline(QWidget):
    """迷你走势：最近 30 日收盘折线，涨跌着色。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(88, 44)
        self.values: List[float] = []

    def set_data(self, values: List[float]) -> None:
        self.values = [v for v in (values or []) if v is not None]
        self.update()

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#0A1A30"))
        if len(self.values) < 2:
            p.setPen(QColor(HINT))
            p.drawText(self.rect(), Qt.AlignCenter, "无走势")
            p.end()
            return
        vals = self.values
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1.0
        w, h = self.width(), self.height()
        pad = 3
        step = (w - pad * 2) / (len(vals) - 1)

        def px(i: int) -> float:
            return pad + step * i

        def py(v: float) -> float:
            return pad + (hi - v) / span * (h - pad * 2)

        color = QColor(UP) if vals[-1] >= vals[0] else QColor(DOWN)
        p.setPen(QPen(color, 1.4))
        for i in range(1, len(vals)):
            p.drawLine(int(px(i - 1)), int(py(vals[i - 1])),
                       int(px(i)), int(py(vals[i])))
        p.end()


class PriceRangeBar(QWidget):
    """价位区间条：止损—现价—目标 在价格轴上的位置可视化。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(34)
        self.stop = self.price = self.target = None

    def set_levels(self, stop, price, target) -> None:
        self.stop, self.price, self.target = stop, price, target
        self.update()

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), Qt.transparent)
        w, h = self.width(), self.height()
        bar_y, bar_h = h // 2 - 4, 6
        p.setFont(QFont("Microsoft YaHei UI", 7))

        if not (self.stop and self.price and self.target
                and self.target > self.stop):
            p.setPen(QColor(HINT))
            p.drawText(self.rect(), Qt.AlignCenter, "价位区间不可用")
            p.end()
            return

        grad = QLinearGradient(0, 0, w, 0)
        grad.setColorAt(0, QColor(DOWN))
        grad.setColorAt(1, QColor(UP))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(0, bar_y, w, bar_h), 3, 3)

        span = self.target - self.stop
        px = lambda v: max(2, min(w - 2, (v - self.stop) / span * w))  # noqa: E731

        p.setPen(QColor(DOWN))
        p.drawText(QRectF(0, bar_y + bar_h, 130, 14),
                   Qt.AlignLeft | Qt.AlignVCenter, f"止损 {self.stop:.2f}")
        p.setPen(QColor(TEXT))
        p.drawText(QRectF(w / 2 - 65, bar_y + bar_h, 130, 14),
                   Qt.AlignHCenter | Qt.AlignVCenter, f"现价 {self.price:.2f}")
        p.setPen(QColor(UP))
        p.drawText(QRectF(w - 130, bar_y + bar_h, 130, 14),
                   Qt.AlignRight | Qt.AlignVCenter, f"目标 {self.target:.2f}")

        x = px(self.price)
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawLine(int(x), bar_y - 4, int(x), bar_y + bar_h + 4)
        p.end()


class SignalCard(QFrame):
    """一条交易信号的机会卡片。"""

    open_stock = Signal(str, str)
    ask_ai = Signal(object)
    add_watch = Signal(str, str)

    def __init__(self, sig: TradeSignal, parent=None):
        super().__init__(parent)
        self.sig = sig
        self.setProperty("card", True)
        self._build()
        self._fill()

    # ------------------------------------------------------------ UI
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 6, 10, 6)
        root.setSpacing(3)

        # 行1：徽章 + 名称 + 方向 + 时间
        top = QHBoxLayout()
        badge = QLabel(self.sig.strategy)
        badge.setStyleSheet(
            f"background:{ACCENT}; color:#fff; border-radius:8px;"
            "padding:2px 10px; font-size:8.5pt; font-weight:bold;")
        name = QLabel(f"<b style='font-size:11.5pt'>{self.sig.name}</b> "
                      f"<span style='color:{SUB}'>{self.sig.code}</span>")
        name.setStyleSheet("background:transparent;")
        side_txt = "买入机会" if self.sig.side == "buy" else "卖出提醒"
        side = QLabel(side_txt)
        side.setStyleSheet(
            f"color:{UP if self.sig.side == 'buy' else DOWN};"
            "font-weight:bold; background:transparent;")
        self.time_label = QLabel(self.sig.time)
        self.time_label.setProperty("hint", True)
        self.track_label = QLabel("")
        self.track_label.setProperty("hint", True)
        top.addWidget(badge)
        top.addWidget(name)
        top.addWidget(side)
        top.addStretch(1)
        top.addWidget(self.track_label)
        top.addWidget(self.time_label)
        root.addLayout(top)

        # 行2：左价位条 / 右走势+风险+评级
        mid = QHBoxLayout()
        mid.setSpacing(18)
        left = QVBoxLayout()
        price_row = QHBoxLayout()
        self.price_label = QLabel("—")
        self.price_label.setStyleSheet(
            f"font-size:13pt; font-weight:bold; color:{TEXT};"
            "background:transparent;")
        self.rr_label = QLabel("—")
        self.rr_label.setProperty("sub", True)
        price_row.addWidget(self.price_label)
        price_row.addWidget(self.rr_label, 1)
        left.addLayout(price_row)
        self.range_bar = PriceRangeBar()
        left.addWidget(self.range_bar)
        mid.addLayout(left, 3)

        right = QHBoxLayout()
        right.setSpacing(16)
        self.spark = Sparkline()
        self.spark.setFixedSize(72, 36)
        right.addWidget(self.spark)
        stat = QVBoxLayout()
        stat.setSpacing(1)
        risk_name = QLabel("风险")
        risk_name.setProperty("statName", True)
        self.risk_label = QLabel("—")
        self.risk_label.setAlignment(
            Qt.AlignRight | Qt.AlignVCenter)
        self.grade_label = QLabel("—")
        self.grade_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.grade_label.setStyleSheet(
            "font-size:12pt; font-weight:bold; background:transparent;"
            "border:1px solid #1E3B5E; border-radius:9px; padding:1px 8px;")
        stat.addWidget(risk_name)
        stat.addWidget(self.risk_label)
        stat.addWidget(self.grade_label)
        right.addLayout(stat)
        mid.addLayout(right, 1)
        root.addLayout(mid)

        # 行3：命中规则 chips + 风险提示
        chips = QHBoxLayout()
        chips.setSpacing(6)
        for rule in (self.sig.hit_rules or [])[:3]:
            chips.addWidget(_chip(rule))
        if self.sig.risk_notes:
            note = QLabel("⚠ " + self.sig.risk_notes[0])
            note.setProperty("hint", True)
            chips.addWidget(note, 1)
        else:
            chips.addStretch(1)
        root.addLayout(chips)

        # 操作按钮内联到 chips 行尾（紧凑化：省一整行）
        for text, cb, prop in (("详情", self._open, "secondary"),
                               ("AI", self._ai, "ai"),
                               ("+自选", self._watch, "secondary")):
            b = QPushButton(text)
            b.setProperty(prop, True)
            b.setFixedHeight(22)
            b.setFixedWidth(52 if len(text) == 2 else 60)
            b.clicked.connect(cb)
            chips.addWidget(b)

    def _fill(self) -> None:
        s = self.sig
        if s.price:
            up = (s.side == "buy")
            self.price_label.setText(f"{s.price:.2f}")
            self.price_label.setStyleSheet(
                f"font-size:13pt;font-weight:bold;color:{UP if up else DOWN};"
                "background:transparent;")
        else:
            self.price_label.setText("—")
        self.range_bar.set_levels(s.stop_price, s.price, s.target_price)
        if s.price and s.stop_price and s.target_price and s.price > s.stop_price:
            rr = (s.target_price - s.price) / (s.price - s.stop_price)
            good = rr >= 2
            self.rr_label.setText(f"盈亏比 {rr:.1f}")
            self.rr_label.setStyleSheet(
                f"color:{SUCCESS if good else WARNING};"
                "font-weight:bold;background:transparent;")
        else:
            self.rr_label.setText("")
        risk = s.risk_score or 0
        self.risk_label.setText(f"{risk:.0f}/100")
        band = UP if risk >= 60 else (WARNING if risk >= 35 else DOWN)
        self.risk_label.setStyleSheet(
            f"color:{band};font-weight:bold;background:transparent;")
        self.grade_label.setText(s.grade or "—")
        self.grade_label.setStyleSheet(
            f"color:{GRADE_COLOR.get(s.grade, FLAT)}; font-weight:bold;"
            "background:transparent;")
        self.spark.set_data(s.sparkline)

    # ------------------------------------------------------------ 交互
    def _open(self) -> None:
        self.open_stock.emit(self.sig.code, self.sig.name)

    def _ai(self) -> None:
        self.ask_ai.emit(self.sig)

    def _watch(self) -> None:
        self.add_watch.emit(self.sig.code, self.sig.name)

    def set_tracking(self, pct: Optional[float]) -> None:
        """发出以来的累计涨跌幅。"""
        if pct is None:
            self.track_label.setText("")
            return
        color = UP if pct >= 0 else DOWN
        self.track_label.setText(
            f"<span style='color:{color}; font-weight:bold'>"
            f"发出以来 {pct:+.2f}%</span>")

    def apply_tracking_fn(self, fn) -> None:
        try:
            self.set_tracking(fn(self.sig))
        except Exception:  # noqa: BLE001
            self.set_tracking(None)
class OpportunityFlow(QScrollArea):
    """机会卡片滚动容器（按 op_score 降序）。"""

    open_stock = Signal(str, str)
    ask_ai = Signal(object)
    add_watch = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self._inner = QWidget()
        self._lay = QVBoxLayout(self._inner)
        self._lay.setContentsMargins(4, 4, 8, 4)
        self._lay.setSpacing(8)
        self._lay.addStretch(1)
        self.setWidget(self._inner)
        self._empty = QLabel(
            "<div style='color:#5D7A9C;line-height:2.0;text-align:center'>"
            "<span style='font-size:22pt'>◎</span><br>"
            "暂无机会信号<br>"
            "<span style='font-size:8.5pt'>点击「立即扫描」或「开始找机会」</span></div>")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setTextFormat(Qt.RichText)
        self.set_signals([])

    def set_signals(self, signals: List[TradeSignal]) -> None:
        self._clear()
        lay = self._lay
        if not signals:
            lay.addWidget(self._empty, 1)
            return
        for sig in sorted(signals, key=lambda s: s.op_score, reverse=True):
            card = SignalCard(sig)
            card.open_stock.connect(self.open_stock)
            card.ask_ai.connect(self.ask_ai)
            card.add_watch.connect(self.add_watch)
            lay.addWidget(card)
        lay.addStretch(1)

    def set_tracking(self, mapping: Dict[str, float]) -> None:
        for i in range(self._lay.count()):
            w = self._lay.itemAt(i).widget()
            if isinstance(w, SignalCard) and w.sig.code in mapping:
                w.set_tracking(mapping[w.sig.code])

    def set_tracking_fn(self, fn) -> None:
        """fn(sig) -> float|None，逐卡计算发出以来涨跌。"""
        for i in range(self._lay.count()):
            w = self._lay.itemAt(i).widget()
            if isinstance(w, SignalCard):
                w.apply_tracking_fn(fn)

    def _clear(self) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None and w is not self._empty:
                w.deleteLater()
        if self._empty.parent() is not None:
            self._empty.setParent(None)
