"""板块热力页的动画组件（QVariantAnimation 驱动，无轮询定时器）。

AnimatedNumber：数字滚动；FlowBar：资金流入/流出双向条；
HBar：水平条形排行；HeatTile：行业热力块（颜色=涨跌幅，渐入动画）。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (QEasingCurve, QVariantAnimation, Qt, QRectF,
                            Signal)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                               QLabel, QVBoxLayout, QWidget)

from .theme import DOWN, FLAT, HINT, SUB, TEXT, UP


class AnimatedNumber(QLabel):
    """数字滚动动画：animate_to(目标值) 从当前值平滑滚到目标。"""

    def __init__(self, parent=None, ndigits: int = 1, prefix: str = "",
                 suffix: str = ""):
        super().__init__(parent)
        self._value = 0.0
        self._target = 0.0
        self._nd = ndigits
        self._prefix = prefix
        self._suffix = suffix
        self.setStyleSheet(f"font-size:15pt; font-weight:bold; color:{TEXT};"
                           "background:transparent;")
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(900)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_tick)
        self._render()

    def animate_to(self, target: float, color: Optional[str] = None,
                   prefix: Optional[str] = None, suffix: Optional[str] = None):
        if prefix is not None:
            self._prefix = prefix
        if suffix is not None:
            self._suffix = suffix
        if color:
            self.setStyleSheet(
                f"font-size:15pt; font-weight:bold; color:{color};"
                "background:transparent;")
        self._target = float(target)
        self._anim.stop()
        # start/end 必须同型（float）——混 int/float 会导致 QVariantAnimation 不插值
        self._anim.setStartValue(float(self._value))
        self._anim.setEndValue(self._target)
        self._anim.start()
        # 兜底：动画由事件循环驱动，极端环境下可能不 tick——用 QTimer 确保终值落地
        from PySide6.QtCore import QTimer
        QTimer.singleShot(self._anim.duration() + 50, self._ensure_final)

    def _ensure_final(self) -> None:
        """动画结束后确保显示终值（防御 QVariantAnimation 静默失效）。"""
        if self._value != self._target:
            self._value = self._target
            self._render()

    def _on_tick(self, v):
        self._value = float(v)
        self._render()

    def _render(self):
        self.setText(f"{self._prefix}{self._value:+.{self._nd}f}{self._suffix}"
                     if self._prefix or self._suffix
                     else f"{self._value:.{self._nd}f}")


class FlowBar(QWidget):
    """资金流向双向条：中心轴向右红(流入)/向左绿(流出)，条从 0 生长。"""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(74)
        self._title = title
        self._value = 0.0       # 亿元，正=流入 红，负=流出 绿
        self._ratio = 0.0       # 0~1 当前条宽比例
        self._max_abs = 1.0
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        head = QHBoxLayout()
        name = QLabel(title)
        name.setStyleSheet(f"color:{SUB}; font-weight:bold;"
                           "background:transparent;")
        self.number = AnimatedNumber(ndigits=1, suffix=" 亿")
        head.addWidget(name)
        head.addStretch(1)
        head.addWidget(self.number)
        v.addLayout(head)
        v.addStretch(1)
        self._grow = QVariantAnimation(self)
        self._grow.setDuration(900)
        self._grow.setEasingCurve(QEasingCurve.OutCubic)
        self._grow.valueChanged.connect(lambda r: setattr(self, "_ratio", r))
        self._grow.valueChanged.connect(lambda *_: self.update())

    def animate_to(self, value_yi: float, max_abs: float) -> None:
        self._value = value_yi
        self._max_abs = max(abs(max_abs), 0.001)
        color = UP if value_yi >= 0 else DOWN
        self.number.animate_to(value_yi, color=color, prefix="主力")
        self._grow.stop()
        self._grow.setStartValue(0.0)
        self._grow.setEndValue(abs(value_yi) / self._max_abs)
        self._grow.start()

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        bar_h = 16
        y = h - bar_h - 6
        mid = w // 2
        # 背景轨道
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1a2130"))
        p.drawRoundedRect(QRectF(2, y, w - 4, bar_h), 4, 4)
        # 中轴
        p.setPen(QColor("#3a4557"))
        p.drawLine(mid, y - 3, mid, y + bar_h + 3)
        # 数据条
        if self._ratio > 0.005:
            span = mid - 6
            bar_w = max(self._ratio * span, 4.0)
            color = QColor(UP) if self._value >= 0 else QColor(DOWN)
            rect = (QRectF(mid + 3, y, bar_w, bar_h) if self._value >= 0
                    else QRectF(mid - 3 - bar_w, y, bar_w, bar_h))
            p.setBrush(color)
            p.drawRoundedRect(rect, 4, 4)
        p.setPen(QColor(HINT))
        p.setFont(QFont("Microsoft YaHei UI", 7))
        p.drawText(QRectF(2, y + bar_h + 2, 60, 12), Qt.AlignLeft,
                   "← 流出(绿)")
        p.drawText(QRectF(w - 62, y + bar_h + 2, 60, 12), Qt.AlignRight,
                   "流入(红) →")
        p.end()


class HBar(QWidget):
    """水平条形（板块成交额排行），从 0 生长动画。"""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self._label = label
        self._ratio = 0.0
        self._text = ""
        self._bar_color = UP
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 3, 0, 3)
        self.name_label = QLabel(label)
        self.name_label.setFixedWidth(86)
        self.name_label.setStyleSheet(f"color:{TEXT}; background:transparent;")
        self.value_label = QLabel("—")
        self.value_label.setStyleSheet(f"color:{SUB}; background:transparent;")
        self.value_label.setFixedWidth(70)
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.name_label)
        lay.addWidget(self.value_label, 1)
        self._grow = QVariantAnimation(self)
        self._grow.setDuration(800)
        self._grow.setEasingCurve(QEasingCurve.OutCubic)
        self._grow.valueChanged.connect(lambda r: setattr(self, "_ratio", r))
        self._grow.valueChanged.connect(lambda *_: self.update())

    def animate_to(self, ratio: float, text: str, color: str = UP) -> None:
        self._bar_color = color
        self._text = text
        self.value_label.setText(text)
        self.value_label.setStyleSheet(
            f"color:{color}; background:transparent;")
        self._grow.stop()
        self._grow.setStartValue(self._ratio)
        self._grow.setEndValue(max(min(ratio, 1.0), 0.0))
        self._grow.start()

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        x0 = 92          # 名称占位
        x1 = w - 76      # 数值占位
        track_w = max(x1 - x0, 4)
        y, bh = 6, h - 12
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#161c28"))
        p.drawRoundedRect(QRectF(x0, y, track_w, bh), 3, 3)
        if self._ratio > 0.004:
            bw = self._ratio * track_w
            p.setBrush(QColor(self._bar_color))
            p.drawRoundedRect(QRectF(x0, y, bw, bh), 3, 3)
        p.end()


def heat_color(change_pct: Optional[float]) -> str:
    """涨跌幅 → 热力色：+1.5% 亮红 → 0 中性灰 → -1.5% 亮绿（线性插值）。"""
    if change_pct is None:
        return "#173351"
    x = max(-1.5, min(1.5, change_pct)) / 1.5   # -1..1
    # 中性基色（灰蓝），向红/绿两端插值
    base_r, base_g, base_b = 58, 64, 76
    if x >= 0:
        t = x
        r = int(base_r + t * (255 - base_r))
        g = int(base_g - t * (base_g - 20))
        b = int(base_b - t * (base_b - 20))
    else:
        t = -x
        r = int(base_r - t * (base_r - 20))
        g = int(base_g + t * (205 - base_g))
        b = int(base_b - t * (base_b - 30))
    return f"#{r:02x}{g:02x}{b:02x}"


class HeatTile(QFrame):
    """行业板块热力块：颜色=涨跌幅，渐入动画，点击通知领涨股。"""

    clicked_leader = Signal(str, str)   # code, name

    def __init__(self, board, parent=None):
        super().__init__(parent)
        self.board = board
        chg = board.change_pct or 0
        bg = heat_color(chg)
        big = (board.amount_yi or 0) > 300
        name_font = 11 if big else 9
        self.setFixedHeight(76)
        self.setStyleSheet(
            f"QFrame {{ background:{bg}; border:1px solid #1E3B5E;"
            f"border-radius:8px; }}"
            f"QLabel {{ background:transparent; color:{'#ffffff' if abs(chg)>0.8 else '#e8ebf1'}; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(1)
        name = QLabel(board.name)
        name.setStyleSheet(
            f"font-size:{name_font}pt; font-weight:bold; background:transparent;")
        chg_label = QLabel(f"{chg:+.2f}%" if chg is not None else "—")
        chg_label.setStyleSheet("font-size:10pt; font-weight:bold;"
                                "background:transparent;")
        leader = QLabel(f"领涨 {board.leader_name}"
                        + (f" {board.leader_pct:+.1f}%"
                           if board.leader_pct is not None else ""))
        leader.setStyleSheet("font-size:7.5pt; background:transparent;")
        v.addWidget(name)
        v.addWidget(chg_label)
        v.addWidget(leader)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{board.name}　{chg:+.2f}%\n"
                         f"成交额 {board.amount_yi:.0f} 亿　成分 {board.count} 只\n"
                         f"领涨：{board.leader_name}({board.leader_code})"
                         if board.amount_yi else board.name)
        # 渐入动画
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        fade = QVariantAnimation(self)
        fade.setDuration(650)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        fade.valueChanged.connect(lambda v, e=self._opacity: e.setOpacity(float(v)))
        fade.start()
        # 关键：子 QLabel 默认接收点击不冒泡，父 QFrame 收不到 mousePressEvent
        # → 让所有子控件把鼠标事件转发给本块（否则点击"没反应"）
        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if self.board.leader_code:
            self.clicked_leader.emit(self.board.leader_code,
                                     self.board.leader_name)
        super().mousePressEvent(ev)
