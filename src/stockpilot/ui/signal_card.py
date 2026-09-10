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

        # v7.2.6 分辨率适配：三个 130px 标签位至少需要 ~390px 宽，宽不足时
        # 逐级退化：中档画『损/现/目』紧凑三段；极窄（<130px）只画现价
        # 线 + 现价数字，避免标签互叠（1920×1080 双列流下 11~147px 价位
        # 条的实拍异常；具体数值 tooltip 与详情页仍完整可见）。
        compact = w < 400
        tiny = w < 130
        if tiny:
            p.setFont(QFont("Microsoft YaHei UI", 6))
            p.setPen(QColor(TEXT))
            p.drawText(QRectF(0, bar_y + bar_h, w, 14),
                       Qt.AlignHCenter | Qt.AlignVCenter, f"现 {self.price:.2f}")
        elif compact:
            f_small = QFont("Microsoft YaHei UI", 6)
            p.setFont(f_small)
            label_w = w / 3
            p.setPen(QColor(DOWN))
            p.drawText(QRectF(0, bar_y + bar_h, label_w, 14),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       f"损{self.stop:.2f}")
            p.setPen(QColor(TEXT))
            p.drawText(QRectF(label_w, bar_y + bar_h, label_w, 14),
                       Qt.AlignHCenter | Qt.AlignVCenter,
                       f"现{self.price:.2f}")
            p.setPen(QColor(UP))
            p.drawText(QRectF(label_w * 2, bar_y + bar_h, label_w, 14),
                       Qt.AlignRight | Qt.AlignVCenter,
                       f"目{self.target:.2f}")
        else:
            p.setFont(QFont("Microsoft YaHei UI", 7))
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
    """一条交易信号的机会卡片（v7.2.4 紧凑版：双行布局）。

    行1：徽章 + 名称代码 + 方向 · 时间/追踪（右对齐）
    行2：现价 + 盈亏比 | 价位条 | 迷你走势 | 风险/评级 + 操作按钮
    命中规则收进 tooltip（不再占一行）；风险提示并入行1尾。
    单卡高度从 ~127px 压到 ~78px，配合两列流单位面积可看 3 倍机会。
    """

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
        root.setContentsMargins(10, 5, 10, 5)
        root.setSpacing(3)

        # 行1：徽章 + 名称代码 + 方向 ·（右侧）追踪 + 风险提示 + 时间
        top = QHBoxLayout()
        top.setSpacing(6)
        badge = QLabel(self.sig.strategy)
        badge.setStyleSheet(
            f"background:{ACCENT}; color:#fff; border-radius:7px;"
            "padding:1px 8px; font-size:8pt; font-weight:bold;")
        name = QLabel(
            f"<b style='font-size:10pt'>{self.sig.name}</b> "
            f"<span style='color:{SUB};font-size:8pt'>{self.sig.code}</span>")
        name.setStyleSheet("background:transparent;")
        side_txt = "买" if self.sig.side == "buy" else "卖"
        side = QLabel(side_txt)
        side.setToolTip("买入机会" if self.sig.side == "buy" else "卖出提醒")
        side.setStyleSheet(
            f"color:{'#fff'};background:{UP if self.sig.side == 'buy' else DOWN};"
            "border-radius:6px;padding:0 6px;font-size:8pt;font-weight:bold;")
        self.time_label = QLabel(self.sig.time)
        self.time_label.setProperty("hint", True)
        self.track_label = QLabel("")
        self.track_label.setProperty("hint", True)
        self.note_label = QLabel("")
        self.note_label.setToolTip("")
        self.note_label.setProperty("hint", True)
        top.addWidget(badge)
        top.addWidget(name)
        top.addWidget(side)
        top.addStretch(1)
        top.addWidget(self.track_label)
        top.addWidget(self.note_label)
        top.addWidget(self.time_label)
        root.addLayout(top)

        # 行2：现价+盈亏比 | 价位条（伸展）| 迷你走势 | 风险评级 + 按钮
        mid = QHBoxLayout()
        mid.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(0)
        self.price_label = QLabel("—")
        self.price_label.setStyleSheet(
            f"font-size:11pt; font-weight:bold; color:{TEXT};"
            "background:transparent;")
        self.rr_label = QLabel("—")
        self.rr_label.setProperty("sub", True)
        left.addWidget(self.price_label)
        left.addWidget(self.rr_label)
        mid.addLayout(left)
        self.range_bar = PriceRangeBar()
        mid.addWidget(self.range_bar, 2)
        self.spark = Sparkline()
        self.spark.setFixedSize(60, 30)
        mid.addWidget(self.spark)
        # 风险/评级合并为一列
        stat = QVBoxLayout()
        stat.setSpacing(0)
        self.risk_label = QLabel("—")
        self.risk_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.risk_label.setStyleSheet(
            "font-size:8pt; background:transparent;")
        self.grade_label = QLabel("—")
        self.grade_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.grade_label.setStyleSheet(
            "font-size:10pt; font-weight:bold; background:transparent;")
        stat.addWidget(self.risk_label)
        stat.addWidget(self.grade_label)
        mid.addLayout(stat)
        for text, cb, prop in (("详情", self._open, "secondary"),
                               ("AI", self._ai, "ai"),
                               ("+自选", self._watch, "secondary")):
            b = QPushButton(text)
            b.setProperty(prop, True)
            b.setFixedHeight(22)
            b.setFixedWidth(44)
            b.clicked.connect(cb)
            mid.addWidget(b)
        root.addLayout(mid)

        # v7.2.6 分辨率适配：卡宽不足时折叠行2 次要元素给价位条让位。
        self._compact = False
        self._update_tip()
        self._apply_compact()

    def _update_tip(self) -> None:
        """卡片 tooltip：规则/风险/（紧凑时）盈亏比。"""
        s = self.sig
        rules = "、".join(s.hit_rules or [])
        notes = "；".join(s.risk_notes or [])
        tip = (f"{s.name}（{s.code}）{s.strategy}\n"
               f"命中规则：{rules or '—'}\n风险提示：{notes or '—'}")
        if self._compact:
            tip += self._rr_tip()
        self.setToolTip(tip)

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
        self.risk_label.setText(f"风险 {risk:.0f}")
        band = UP if risk >= 60 else (WARNING if risk >= 35 else DOWN)
        self.risk_label.setStyleSheet(
            f"color:{band};font-weight:bold;font-size:8pt;"
            "background:transparent;")
        self.grade_label.setText(f"{s.grade or '—'}级")
        self.grade_label.setStyleSheet(
            f"color:{GRADE_COLOR.get(s.grade, FLAT)}; font-size:10pt;"
            "font-weight:bold;background:transparent;")
        self.spark.set_data(s.sparkline)
        self.note_label.setText("⚠" if s.risk_notes else "")
        self.note_label.setToolTip(
            "；".join(s.risk_notes or []) or "")

    # ------------------------------------------------------------ 交互
    def _apply_compact(self) -> None:
        """v7.2.6：卡宽不足时逐级折叠行2 次要元素给价位条让位。

        <470px：隐藏迷你走势 + 盈亏比收进 tooltip（按钮组的 44px 最小宽
        与现价/风险列都是硬需求，价位条是行2 唯一可让位的弹性元素）。
        1920×1080 双列流下价位条曾从 396px 被压到 11px 的实拍异常即此。"""
        compact = 0 < self.width() < 470
        if compact != self._compact:
            self._compact = compact
            self.spark.setVisible(not compact)
            self.rr_label.setVisible(not compact)
            self._update_tip()
        # 价位条保底宽：低于此只画色条不画标签（paintEvent 紧凑模式）
        self.range_bar.setMinimumWidth(60 if compact else 0)

    def _rr_tip(self) -> str:
        s = self.sig
        if s.price and s.stop_price and s.target_price and s.price > s.stop_price:
            rr = (s.target_price - s.price) / (s.price - s.stop_price)
            return f"\n盈亏比 {rr:.1f}"
        return ""

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._apply_compact()

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
    """机会卡片滚动容器（按 op_score 降序）。

    v7.2.4：两列网格流（宽屏横向利用）——单列时一屏只能看 1~2 张 127px
    的卡，两列 + 紧凑卡后同屏可见数量提升约 3 倍。窄面板自动回退单列。
    """

    open_stock = Signal(str, str)
    ask_ai = Signal(object)
    add_watch = Signal(str, str)

    _COLS = 2          # 视口够宽时两列（阈值随 _MIN_COL_W）
    _MIN_COL_W = 390   # v7.2.6：280→390。行2 固定元素（按钮组+现价/风险列）
    # 约 330px，280px 列宽下价位条只剩 11px 且三标签互叠（1920×1080
    # 用户实拍异常根因）。390 = 330 固定 + 60 价位条保底：2560 屏两列
    # 559px 全元素；1920 屏两列 ~396px 走势/盈亏比自动折叠（等比例
    # 适配），仍保双列流一屏多卡的 v7.2.4 设计初衷。

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self._inner = QWidget()
        self._lay = QVBoxLayout(self._inner)
        self._lay.setContentsMargins(4, 4, 8, 4)
        self._lay.setSpacing(6)
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

    def _columns(self) -> int:
        return self._COLS if self.viewport().width() >= (
                self._MIN_COL_W * self._COLS + 8) else 1

    def set_signals(self, signals: List[TradeSignal]) -> None:
        self._sigs = list(signals or [])
        self._cols_rendered = self._columns()
        self._clear()
        lay = self._lay
        if not self._sigs:
            lay.addWidget(self._empty, 1)
            return
        ordered = sorted(self._sigs, key=lambda s: s.op_score, reverse=True)
        cols = self._cols_rendered
        rows = [ordered[i::cols] for i in range(cols)]   # 纵向优先排：左列顶级机会
        grid = QHBoxLayout()
        grid.setSpacing(6)
        for col_sigs in rows:
            col = QVBoxLayout()
            col.setSpacing(6)
            for sig in col_sigs:
                card = SignalCard(sig)
                card.open_stock.connect(self.open_stock)
                card.ask_ai.connect(self.ask_ai)
                card.add_watch.connect(self.add_watch)
                col.addWidget(card)
            col.addStretch(1)
            grid.addLayout(col, 1)
        lay.addLayout(grid)
        lay.addStretch(1)

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        # 列数随面板宽度自适应：暂存信号重建布局
        if getattr(self, "_sigs", None) and self._columns() != getattr(
                self, "_cols_rendered", None):
            self.set_signals(self._sigs)

    def set_tracking(self, mapping: Dict[str, float]) -> None:
        for w in self._inner.findChildren(SignalCard):
            if w.sig.code in mapping:
                w.set_tracking(mapping[w.sig.code])

    def set_tracking_fn(self, fn) -> None:
        """fn(sig) -> float|None，逐卡计算发出以来涨跌。"""
        for w in self._inner.findChildren(SignalCard):
            w.apply_tracking_fn(fn)

    def _clear(self) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None and w is not self._empty:
                w.deleteLater()
            elif item.layout() is not None:
                # 网格行布局：删卡（子布局的 widget 归属卡自身父链）
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget() is not None:
                        sub.widget().deleteLater()
                    elif sub.layout() is not None:
                        while sub.layout().count():
                            s2 = sub.layout().takeAt(0)
                            if s2.widget() is not None:
                                s2.widget().deleteLater()
        if self._empty.parent() is not None:
            self._empty.setParent(None)
