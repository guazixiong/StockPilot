"""右下角 AI 助手：悬浮球 + 优美聊天窗（无边框圆角 + 气泡 + 渐变）。"""
from __future__ import annotations

import html
import threading

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QTimer, QPropertyAnimation, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QLinearGradient,
                            QPainter, QPainterPath, QPixmap)
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QTextBrowser,
                               QVBoxLayout, QWidget)

from .ai_stream import append_stream, finalize_stream
from ..core import prompt as prompt_mod
from .workers import submit


class _FAB(QPushButton):
    """悬浮球：渐变圆形 + 机器人 emoji + 呼吸动画。"""

    def __init__(self):
        super().__init__("✦")
        self.setFixedSize(56, 56)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("AI 助手 —— 点击随时提问")
        font = QFont()
        font.setPixelSize(26)
        self.setFont(font)
        self.setStyleSheet(
            "QPushButton{border:none;border-radius:28px;"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            "stop:0 #5B6CFF,stop:1 #8A5CFF);color:white;}"
            "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            "stop:0 #7B8CFF,stop:1 #A07BFF);}")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 140))
        self.setGraphicsEffect(shadow)
        # 呼吸（轻微缩放）
        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(1400)
        self._anim.setEasingCurve(QEasingCurve.InOutSine)
        self._anim.setStartValue(self.geometry())
        self._anim.setLoopCount(-1)

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)

    def start_breath(self, parent_geo) -> None:
        g0 = self.geometry()
        g1 = QRectF(g0).toRect()
        g1.setWidth(int(g1.width() * 1.07))
        g1.setHeight(int(g1.height() * 1.07))
        g1.moveCenter(g0.center())
        self._anim.setStartValue(g0)
        self._anim.setEndValue(g1)
        self._anim.start()


class AiChatWindow(QWidget):
    """悬浮聊天窗：无边框圆角 + 顶部渐变标题栏 + 气泡式对话。"""

    delta = Signal(str, str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.ctx = ctx
        self._running = False
        self._stop = threading.Event()
        self._history: list = []
        self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
        self.resize(420, 560)
        self.setStyleSheet("""
            AiChatWindow{background:#071526;border:1px solid #1E3B5E;
                border-radius:18px;}
            QLabel#chatTitle{color:white;font-size:12pt;font-weight:bold;}
            QLabel#chatSub{color:#7890AA;font-size:8pt;}
        """)
        self.delta.connect(self._on_delta)
        self._build_ui()
        # 阴影
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 160))
        self.setGraphicsEffect(shadow)
        self._drag_pos = None

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        title_v = QVBoxLayout()
        t = QLabel("AI 投资助手")
        t.setObjectName("chatTitle")
        t.setStyleSheet("background:transparent;")
        s = QLabel("随时提问 · 支持行情/策略/个股任何问题")
        s.setObjectName("chatSub")
        s.setStyleSheet("background:transparent;")
        title_v.addWidget(t)
        title_v.addWidget(s)
        head.addLayout(title_v)
        head.addStretch(1)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(
            "QPushButton{border:none;border-radius:12px;color:#7890AA;"
            "background:transparent;}"
            "QPushButton:hover{color:white;background:#1E3B5E;}")
        close_btn.clicked.connect(self.hide)
        head.addWidget(close_btn)
        lay.addLayout(head)

        self.view = QTextBrowser()
        self.view.setStyleSheet(
            "QTextBrowser{background:#0A1A30;border:none;border-radius:12px;"
            "padding:8px;}")
        self.view.setOpenExternalLinks(True)
        self.view.setHtml(
            "<div style='color:#8FA9C7;line-height:1.8;font-size:9pt'>"
            "你好，我是你的 AI 投资助手。<br>"
            "可以问我：<br>"
            "• 今天哪个板块最强？<br>• 600519 现在适合加仓吗？<br>"
            "• 帮我看看放量突破策略最近表现<br>"
            "（需先在「AI 分析」页配置好模型服务）</div>")
        lay.addWidget(self.view, 1)

        bar = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入问题，回车发送…")
        self.input.setStyleSheet(
            "QLineEdit{background:#0A1A30;border:1px solid #1E3B5E;"
            "border-radius:16px;padding:8px 14px;color:#EAF2FF;}"
            "QLineEdit:focus{border-color:#8E6DFF;}")
        self.input.returnPressed.connect(self._send)
        self.send_btn = QPushButton("➤")
        self.send_btn.setFixedSize(36, 36)
        self.send_btn.setStyleSheet(
            "QPushButton{border:none;border-radius:18px;color:white;"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            "stop:0 #5B6CFF,stop:1 #8A5CFF);font-size:14px;}"
            "QPushButton:hover{background:#7B8CFF;}")
        self.send_btn.clicked.connect(self._send)
        bar.addWidget(self.input, 1)
        bar.addWidget(self.send_btn)
        lay.addLayout(bar)

        tip = QLabel("AI 回答仅供参考 · 不构成投资建议")
        tip.setStyleSheet("color:#5D7A9C;font-size:7.5pt;background:transparent;")
        lay.addWidget(tip)

    # 拖动
    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.position().y() < 44:
            self._drag_pos = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._drag_pos and ev.buttons() & Qt.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        self._drag_pos = None
        super().mouseReleaseEvent(ev)

    # ------------------------------------------------------------ 发送
    def _send(self) -> None:
        text = self.input.text().strip()
        if not text or self._running:
            return
        try:
            client = self.ctx.ai_client()
        except RuntimeError as exc:
            self._append_system(f"⚠ {exc}")
            return
        if not self._history:
            self._history = [{"role": "system",
                              "content": prompt_mod.SYSTEM_PROMPT}]
        self._history.append({"role": "user", "content": text})
        self._running = True
        self.input.clear()
        self.send_btn.setEnabled(False)
        append_stream(self.view, "user", text)
        append_stream(self.view, "system",
                      "<div style='color:#8E6DFF;font-size:8pt;margin:2px 0'>● 思考中…</div>",
                      raw=True)
        submit(self._do_run, client, list(self._history),
               on_done=self._on_done, on_err=self._on_err)

    def _do_run(self, client, messages):
        return client.chat(messages, stream=True,
                           on_delta=lambda k, t: self.delta.emit(k, t),
                           stop_event=self._stop)

    def _on_delta(self, kind, text) -> None:
        append_stream(self.view, kind, text)

    def _on_done(self, reply) -> None:
        self._running = False
        self.send_btn.setEnabled(True)
        append_stream(self.view, "content", "<br>")
        append_stream(self.view, "disclaimer", "")
        finalize_stream(self.view)
        if reply:
            self._history.append({"role": "assistant", "content": reply})

    def _on_err(self, msg) -> None:
        self._running = False
        self.send_btn.setEnabled(True)
        append_stream(self.view, "error", str(msg))

    def _append_system(self, msg: str) -> None:
        append_stream(self.view, "system",
                      f"<div style='color:#FFB3BE'>{html.escape(msg)}</div>",
                      raw=True)


class AiAssistant:
    """装配：悬浮球 + 聊天窗（挂在主窗口右下角）。"""

    def __init__(self, main_window):
        self.win = main_window
        self.fab = _FAB()
        self.chat = AiChatWindow(self.win.ctx, None)
        self.fab.setParent(self.win)
        self.fab.clicked.connect(self._toggle)
        self._place()
        # 跟随主窗尺寸
        self.win.resizeEvent_hook = self._place

    def _place(self) -> None:
        g = self.win.geometry()
        self.fab.move(g.width() - 76, g.height() - 100)
        self.chat.move(g.right() - 440, g.bottom() - 590)

    def _toggle(self) -> None:
        if self.chat.isVisible():
            self.chat.hide()
        else:
            self._place()
            self.chat.show()
            self.chat.raise_()
            self.chat.input.setFocus()
