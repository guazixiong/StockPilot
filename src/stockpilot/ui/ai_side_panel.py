"""通用就地 AI 面板：流式报告 + 追问 + 停止 + 未配置引导。

首页 / 机会雷达复用；详情窗沿用其内嵌 Tab 实现（结构不同，不强行统一）。
"""
from __future__ import annotations

import html
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTextBrowser, QVBoxLayout, QWidget)

from .ai_stream import append_stream, finalize_stream
from ..core import prompt
from .workers import submit


class AiSidePanel(QFrame):
    """右侧 AI 分析面板。analyze_* 触发后就地流式输出，可多轮追问。"""

    delta = Signal(str, str)  # 流式增量：Worker 线程 → 主线程（自动队列）

    def __init__(self, ctx, title: str = "AI 分析", parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        self.ctx = ctx
        self._running = False
        self._stop = threading.Event()
        self._history: list = []
        self._object_title = ""
        self.delta.connect(self._on_delta)
        self._build_ui(title)

    def _on_delta(self, kind: str, text: str) -> None:
        """主线程槽：流式追加（Signal 跨线程自动队列，替代 singleShot）。"""
        append_stream(self.view, kind, text)

    # ------------------------------------------------------------ UI
    def _build_ui(self, title: str):
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        head = QHBoxLayout()
        self.title_label = QLabel(title)
        self.title_label.setProperty("title", True)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setProperty("secondary", True)
        self.stop_btn.setFixedHeight(26)
        self.stop_btn.hide()
        self.stop_btn.clicked.connect(self._stop.set)
        head.addWidget(self.title_label)
        head.addStretch(1)
        head.addWidget(self.stop_btn)
        v.addLayout(head)

        self.object_label = QLabel("选择一个机会或个股，点「AI 解读」即可就地分析")
        self.object_label.setProperty("hint", True)
        self.object_label.setWordWrap(True)
        v.addWidget(self.object_label)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        v.addWidget(self.view, 1)

        input_bar = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("继续追问（回车发送）…")
        self.input_edit.returnPressed.connect(self._send_followup)
        self.send_btn = QPushButton("发送")
        self.send_btn.setFixedHeight(30)
        self.send_btn.clicked.connect(self._send_followup)
        input_bar.addWidget(self.input_edit, 1)
        input_bar.addWidget(self.send_btn)
        v.addLayout(input_bar)

        tip = QLabel("AI 生成，仅供参考，不构成投资建议")
        tip.setProperty("hint", True)
        v.addWidget(tip)

    # ------------------------------------------------------------ 就地分析
    def _ready(self) -> bool:
        if self.ctx.ai_config() is not None:
            return True
        self._set_object("AI 未配置")
        self._show_guide()
        return False

    def _show_guide(self) -> None:
        self.view.clear()
        self.view.setHtml(
            "<div style='color:#8FA9C7;line-height:1.9'>"
            "<b style='color:#e8ebf1'>AI 尚未配置</b><br>"
            "AI 分析需要先配置一个 OpenAI 协议的模型服务（如 DeepSeek）。<br><br>"
            "① 打开 <b>💡 AI 分析</b> 或 <b>⚙ 设置</b> 页面<br>"
            "② 选择厂商预设 → 填入 API Key 与模型名 → 测试连接 → 保存配置<br>"
            "③ 回到本页，重新点击「AI 解读」即可就地查看报告</div>")

    def _set_object(self, title: str) -> None:
        self._object_title = title
        self.object_label.setText(title)

    def _start(self, client, messages, header: str, new_session: bool = False) -> None:
        """new_session=True 切换分析对象时开新报告（清空）；
        追问时 False —— 历史报告保留，回答追加在下方。"""
        self._running = True
        self._stop.clear()
        self.stop_btn.show()
        self.input_edit.clear()
        if new_session:
            self.view.clear()
        else:
            append_stream(self.view, "system",
                          "<div style='margin:10px 0 4px 0'>"
                          "<span style='color:#8E6DFF'>━━ 新的一轮 ━━</span></div>",
                          raw=True)
        append_stream(self.view, "user", header)
        append_stream(self.view, "ai_head", "")
        self._set_object(f"{self._object_title} · 分析中…（繁忙时自动重试）")
        submit(self._do_run, client, messages, on_done=self._on_done,
               on_err=self._on_err)

    def _do_run(self, client, messages):
        return client.chat(messages, stream=True,
                           on_delta=lambda k, t: self.delta.emit(k, t),
                           stop_event=self._stop)

    def _emit(self, kind: str, text: str) -> None:
        pass  # 已由 Signal delta 替代（singleShot 跨线程不可靠）

    def _on_done(self, reply: str) -> None:
        self._running = False
        self.stop_btn.hide()
        append_stream(self.view, "content", "<br>")
        append_stream(self.view, "disclaimer", "", raw=True)
        finalize_stream(self.view)
        if reply:
            self._ai_history_append(reply)
        self._set_object(f"{self._object_title} · 报告完成，可继续追问")

    def _on_err(self, msg: str) -> None:
        self._running = False
        self.stop_btn.hide()
        append_stream(self.view, "error", str(msg))
        append_stream(self.view, "system",
                      "可稍后重试；若持续失败，请在「AI 分析」页测试连接或更换模型。",
                      raw=True)
        self._set_object(f"{self._object_title} · 调用失败")

    def _ai_history_append(self, reply: str) -> None:
        self._history.append({"role": "assistant", "content": reply})
        body = self._history[1:]
        if len(body) > 12:
            self._history = [self._history[0]] + body[-12:]

    # ------------------------------------------------------------ 公共入口
    def analyze_signal(self, sig) -> None:
        """机会信号解读：就地流式。"""
        if self._running or not self._ready():
            return
        try:
            client = self.ctx.ai_client()
        except RuntimeError as exc:
            self._set_object(str(exc))
            return
        self._set_object(f"信号解读 · {sig.name}({sig.code})")
        context = prompt.build_signal_context(sig)
        messages = prompt.build_messages("信号解读", context, "")
        self._history = list(messages)
        self._start(client, self._history,
                    f"解读信号：{sig.name}({sig.code}) {sig.strategy}",
                    new_session=True)

    def analyze_stock(self, quote, ind: dict, klines, news) -> None:
        """个股诊断：上下文由调用方备好后就地流式。"""
        if self._running or not self._ready():
            return
        try:
            client = self.ctx.ai_client()
        except RuntimeError as exc:
            self._set_object(str(exc))
            return
        name = getattr(quote, "name", "") or getattr(quote, "code", "")
        code = getattr(quote, "code", "")
        self._set_object(f"个股诊断 · {name}({code})")
        context = prompt.build_stock_context(quote, ind, klines, news)
        messages = prompt.build_messages("个股诊断", context, "")
        self._history = list(messages)
        self._start(client, self._history, f"诊断：{name}({code})",
                    new_session=True)

    def analyze_news(self, item) -> None:
        """快讯解读：就地流式。"""
        if self._running or not self._ready():
            return
        try:
            client = self.ctx.ai_client()
        except RuntimeError as exc:
            self._set_object(str(exc))
            return
        self._set_object(f"快讯解读 · {item.title[:24]}")
        context = f"【快讯】{item.date}\n{item.title}\n{item.summary}"
        messages = prompt.build_messages("新闻解读", context, "")
        self._history = list(messages)
        self._start(client, self._history, f"解读快讯：{item.title[:40]}",
                    new_session=True)

    # ------------------------------------------------------------ 追问
    def _send_followup(self) -> None:
        text = self.input_edit.text().strip()
        if not text:
            return
        if not self._ready():
            return
        if self._running:
            self._set_object("AI 正在输出，请稍候或点「停止」…")
            return
        if not self._history:
            self._set_object("请先选择一个对象进行分析，再追问")
            return
        try:
            client = self.ctx.ai_client()
        except RuntimeError as exc:
            self._set_object(str(exc))
            return
        self._history.append({"role": "user", "content": text})
        self._start(client, self._history, f"{text}")
