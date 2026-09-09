"""AI 流式解读面板：选股/监听/回测页内嵌的流式输出组件。"""
from __future__ import annotations

import html
import threading

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (QHBoxLayout, QMessageBox, QTextBrowser,
                               QVBoxLayout, QWidget, QPushButton, QLabel)


_DISCLAIMER = "以上分析由AI生成，仅供参考，不构成投资建议"

# 零宽/不可见字符（会显示成"â&#8203;"或导致换行异常）
_INVISIBLE = "\u200b\u200c\u200d\ufeff\u2028\u2029"


def _sanitize(text: str) -> str:
    """清洗流式文本：去零宽字符；去 AI 输出的免责声明（UI 统一渲染一次）；
    去模型混入的字面 <br>/<br/> 标签残留（会被当纯文本显示出来）。"""
    import re
    for ch in _INVISIBLE:
        text = text.replace(ch, "")
    # 触发条件放宽：只要含"以上分析…AI…参考"即认为模型输出了免责句（含空格变体）
    if re.search(r"以上分析.{0,4}由\s*AI\s*生成", text):
        text = re.sub(
            r"[。\n\r]*\s*以上分析.{0,6}由\s*AI\s*生成.{0,10}仅供参考.{0,10}"
            r"不构成.{0,10}投资建议\s*", "", text)
        # 句尾残留的孤立 <br> 字面量一并清除
        text = re.sub(r"(<br\s*/?>\s*)+$", "", text.strip())
        text = text.rstrip()
        if text and not text.endswith(("\n", "。", "！", "？", "；")):
            text += ""
    # 兜底：清除句中孤立的字面 <br>（AI 偶发输出）
    text = re.sub(r"<br\s*/?>", "\n", text)
    return text


def append_stream(view: QTextBrowser, kind: str, text: str,
                  raw: bool = False) -> None:
    """向 QTextBrowser 追加一段流式文本（线程槽，主线程执行）。

    kind: content / reasoning / error / user（气泡）/ system（提示）/
          disclaimer（免责声明，UI 统一渲染一次）
    raw=True 时 text 视为已转义好的 HTML 片段，直接插入。
    """
    if kind == "disclaimer":   # 固定文案，不依赖 text
        fragment = (f"<div style='color:#6F86A2;font-size:8pt;"
                    f"border-top:1px solid #14263D;margin-top:8px;"
                    f"padding-top:4px'>{_DISCLAIMER}。</div>")
    elif raw:
        text = _sanitize(text)
        fragment = text
    elif kind == "reasoning":
        fragment = (f"<span style='color:#7B92AE;font-size:8pt'>"
                     f"{html.escape(text)}</span>")
    elif kind == "error":
        fragment = (f"<div style='color:#FFB3BE;background:#2A1627;"
                    f"border:1px solid #6E2C3D;border-left:3px solid #FF5E77;"
                    f"border-radius:6px;padding:8px 12px;margin:6px 0;"
                    f"white-space:pre-wrap'>{html.escape(text)}</div>")
    elif kind == "user":
        fragment = (f"<table width='98%' cellpadding='0' cellspacing='0'>"
                    f"<tr><td align='right'>"
                    f"<div style='color:#EAF2FF;background:rgba(43,116,215,107);"
                    f"border:1px solid rgba(75,152,255,90);border-radius:12px 12px 3px 12px;"
                    f"padding:7px 13px;margin:6px 0 8px 6px;"
                    f"font-size:9.5pt;display:inline-block'>"
                    f"{html.escape(text)}</div></td></tr></table>")
    elif kind == "ai_head":
        fragment = ("<table width='98%' cellpadding='0' cellspacing='0'>"
                    "<tr><td><div style='display:inline-block;"
                    "background:rgba(10,26,48,180);border:1px solid #1E3B5E;"
                    "border-radius:12px 12px 12px 3px;padding:2px 9px;"
                    "margin:0 6px 2px 0'>"
                    "<span style='color:#8E6DFF'>✦</span> "
                    "<span style='color:#8FA9C7;font-size:8pt'>AI</span>"
                    "</div></td></tr></table>")
    else:
        text = _sanitize(text)
        fragment = html.escape(text).replace("\n", "<br>")
    cursor = view.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    view.setTextCursor(cursor)
    view.insertHtml(fragment)
    sb = view.verticalScrollBar()
    sb.setValue(sb.maximum())


class AiStreamPanel(QWidget):
    """用法：panel.run(template_key, context, question)。"""

    delta = Signal(str, str)

    def __init__(self, ctx, title: str = "AI 解读", parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._stop = threading.Event()
        self._running = False
        self.delta.connect(self._on_delta)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self.title_label = QLabel(title)
        self.title_label.setProperty("title", True)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setProperty("secondary", True)
        self.stop_btn.clicked.connect(self._stop.set)
        self.stop_btn.hide()
        head.addWidget(self.title_label)
        head.addStretch(1)
        head.addWidget(self.stop_btn)
        lay.addLayout(head)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        lay.addWidget(self.view, 1)

    # ------------------------------------------------------------ 运行
    def run(self, template_key: str, context: str, question: str = "") -> None:
        if self._running:
            return
        try:
            from ..core.prompt import build_messages
            client = self.ctx.ai_client()
            messages = build_messages(template_key, context, question)
        except (RuntimeError, Exception) as exc:  # noqa: BLE001
            QMessageBox.warning(self, "AI 未就绪", str(exc) if isinstance(
                exc, RuntimeError) else f"构建请求失败: {exc}")
            return
        self.view.clear()
        self._stop.clear()
        self._running = True
        self.stop_btn.show()
        from .workers import submit
        submit(self._do_run, client, messages, on_done=self._on_done,
               on_err=self._on_err)

    def _do_run(self, client, messages):
        return client.chat(messages, stream=True,
                           on_delta=lambda k, t: self.delta.emit(k, t),
                           stop_event=self._stop)

    def _on_delta(self, kind: str, text: str) -> None:
        append_stream(self.view, kind, text)

    def _on_done(self, _result) -> None:
        self._running = False
        self.stop_btn.hide()

    def _on_err(self, msg: str) -> None:
        self._running = False
        self.stop_btn.hide()
        append_stream(self.view, "error", f"\n[AI 调用失败] {msg}\n")


def finalize_stream(view: QTextBrowser) -> None:
    """流式结束后对报告区做一次全文清理：

    流式是分块追加的，免责句/字面 <br> 常被切在两个 chunk 里、逐块 sanitize
    匹配不到——此处对完整 HTML 做一遍收尾清理并重设。
    """
    import re as _re
    doc = view.toHtml()
    plain = view.toPlainText()
    if _re.search(r"以上分析.{0,4}由\s*AI\s*生成", plain):
        # AI 正文里所有免责句实例删除；UI 最后渲染的免责 div 带样式类，不受影响
        # （免责 div 的文本以 border-top 样式包裹，正则只匹配纯文本实例）
        cleaned = _re.sub(
            r"以上分析.{0,6}由\s*AI\s*生成.{0,10}仅供参考.{0,10}"
            r"不构成.{0,10}投资建议", "", doc)
        if cleaned != doc:
            # v5.2.3 保底：清理后正文若消失（正则误吞整个文档体），
            # 回滚原文档——宁可保留模型免责句也不能丢报告。
            probe = _re.sub(r"<[^>]+>\s*", "", cleaned)
            probe = _re.sub(r"\s+", "", probe)
            min_body = len(_re.sub(r"\s+", "", plain)) // 2 or 1
            if len(probe) < max(min_body, 30):
                cleaned = doc
            view.setHtml(cleaned)
            sb = view.verticalScrollBar()
            sb.setValue(sb.maximum())
            # setHtml 后确认仍保留一条免责（UI 渲染实例）
            plain2 = view.toPlainText()
            if "以上分析" not in plain2:
                append_stream(view, "disclaimer", "")
