"""资讯页：7x24 快讯 + 个股新闻/公告 + AI 解读。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QSplitter,
                               QVBoxLayout, QWidget)

from .. import kit
from ..ai_stream import AiStreamPanel
from ..workers import submit
from ...core.providers.base import normalize_code


class NewsPage(QWidget):
    ask_ai = Signal(str, str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._news_items: list = []
        self._build_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_fast_news)
        self.timer.start(60 * 1000)
        self.refresh_fast_news()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(11, 11, 11, 11)
        lay.setSpacing(10)
        lay.addWidget(kit.page_head(
            "市场资讯", "7×24 快讯 · 个股新闻公告 · AI 解读"))
        split = QSplitter(Qt.Horizontal)
        lay.addWidget(split, 1)

        # 左：7x24
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        t1 = QLabel("市场快讯")
        t1.setProperty("title", True)
        head.addWidget(t1)
        head.addStretch(1)
        rbtn = QPushButton("刷新")
        rbtn.setProperty("secondary", True)
        rbtn.clicked.connect(self.refresh_fast_news)
        head.addWidget(rbtn)
        left_lay.addLayout(head)
        self.fast_list = QListWidget()
        self.fast_list.itemDoubleClicked.connect(self._on_fast_ai)
        left_lay.addWidget(self.fast_list, 1)
        fast_tip = QLabel("双击条目 → AI 解读")
        fast_tip.setProperty("hint", True)
        left_lay.addWidget(fast_tip)
        split.addWidget(left)

        # 右：个股新闻
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("输入股票代码（如 600519）")
        self.code_edit.returnPressed.connect(self.load_stock_news)
        btn = QPushButton("查新闻")
        btn.clicked.connect(self.load_stock_news)
        bar.addWidget(self.code_edit, 1)
        bar.addWidget(btn)
        t2 = QLabel("个股新闻 / 公告")
        t2.setProperty("title", True)
        right_lay.addWidget(t2)
        right_lay.addLayout(bar)
        self.stock_list = QListWidget()
        self.stock_list.itemDoubleClicked.connect(self._on_stock_ai)
        right_lay.addWidget(self.stock_list, 1)
        stock_tip = QLabel("双击条目 → AI 解读该消息对该股的影响")
        stock_tip.setProperty("hint", True)
        right_lay.addWidget(stock_tip)
        split.addWidget(right)
        split.setSizes([420, 560])

        self.ai_panel = AiStreamPanel(self.ctx, "AI 新闻解读")
        lay.addWidget(self.ai_panel, 1)


    # ------------------------------------------------------------ 结果提示（v5.2.2）
    # 页面是 QWidget，window() 未必是 QMainWindow（无 statusBar 可用）——
    # v5.2.2 前直接调 self.window().statusBar() 在独立场景点击即崩且被 Qt 吞掉。
    def _notify(self, msg: str, ok: bool = True) -> None:
        """结果提示：优先主窗状态栏，无则弹窗（绝不因宿主环境抛错）。"""
        try:
            sb = self.window().statusBar()
            sb.showMessage(msg, 5000)
            return
        except (AttributeError, RuntimeError):
            pass
        from PySide6.QtWidgets import QMessageBox
        (QMessageBox.information if ok else QMessageBox.warning)(
            self, "提示", msg)

    # ------------------------------------------------------------ 快讯
    def refresh_fast_news(self) -> None:
        submit(self.ctx.em.get_fast_news, 30, on_done=self._on_fast_news,
               on_err=self._on_news_err)

    def _on_fast_news(self, items: list) -> None:
        self._news_items = items or []
        self.fast_list.clear()
        for n in self._news_items:
            QListWidgetItem(f"{n.date}  {n.title}", self.fast_list)
            row = self.fast_list.item(self.fast_list.count() - 1)
            row.setData(Qt.UserRole, n)

    # ------------------------------------------------------------ 个股新闻
    def load_stock_news(self) -> None:
        code = normalize_code(self.code_edit.text())
        if not code:
            return
        submit(self.ctx.em.get_news, code, 15, on_done=self._on_stock_news,
               on_err=self._on_news_err)

    def _on_stock_news(self, items: list) -> None:
        self.stock_list.clear()
        for n in items or []:
            QListWidgetItem(f"{n.date}  [{n.source}] {n.title}", self.stock_list)
            row = self.stock_list.item(self.stock_list.count() - 1)
            row.setData(Qt.UserRole, n)

    def _on_news_err(self, msg: str) -> None:
        self._notify(f"资讯加载失败: {msg}", ok=False)

    # ------------------------------------------------------------ AI
    def _on_fast_ai(self, item: QListWidgetItem) -> None:
        n = item.data(Qt.UserRole)
        context = f"【快讯】{n.date}\n{n.title}\n{n.summary}"
        self.ai_panel.run("新闻解读", context, "请分析该消息对相关板块/个股的影响")

    def _on_stock_ai(self, item: QListWidgetItem) -> None:
        n = item.data(Qt.UserRole)
        code = normalize_code(self.code_edit.text())
        title = f"新闻解读 - {code} {n.title[:20]}"
        context = f"【{n.source}】{n.date} 股票 {code}\n标题: {n.title}\n摘要: {n.summary or n.title}"
        self.ai_panel.run("新闻解读", context, f"分析该消息对 {code} 的影响与应对")
