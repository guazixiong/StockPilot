"""个股 AI 对比对话框（v7.2.1）：多选股票 → 同口径数据 → 一次 AI 横向分析。

入口在行情页「AI 对比」。选股源：自选列表勾选 + 代码搜索添加；
确认后后台并行拉每只的实时行情与日线指标，拼 build_compare_context
发 ask_ai 信号跳转 AI 分析页流式输出。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QTableWidget, QVBoxLayout,
                               QWidget)

from .workers import submit


class CompareDialog(QDialog):
    """多选股票的 AI 对比选择器。"""

    ask_ai = Signal(str, str)     # (对比上下文, 标题) → 主窗跳 AI 页

    MAX = 8                        # 对比上限（上下文长度保护）

    def __init__(self, ctx, watchlist: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 多股对比")
        self.resize(460, 560)
        self.ctx = ctx
        self._picks: list[dict] = []
        self._pick_rows: dict[str, int] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        hint = QLabel(f"从自选中勾选，或输入代码添加（最多同时对比 {self.MAX} 只）。"
                      "每只取实时行情 + 日线技术指标，同口径供 AI 横向对比。")
        hint.setProperty("hint", True)
        hint.setWordWrap(True)
        lay.addWidget(hint)

        # 添加行
        add_row = QHBoxLayout()
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("输入 6 位代码（如 600519），回车添加")
        self.code_edit.returnPressed.connect(self._add_code)
        add_btn = QPushButton("添加")
        add_btn.clicked.connect(self._add_code)
        add_row.addWidget(self.code_edit, 1)
        add_row.addWidget(add_btn)
        lay.addLayout(add_row)

        # 勾选表：自选预填
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["", "代码", "名称"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setMaximumHeight(300)
        for w in watchlist or []:
            self._append_pick(w.get("code", ""), w.get("name", ""), checked=False)
        lay.addWidget(self.table)

        # 已选计数 + 可选快捷
        self.count_label = QLabel("已选 0 只")
        self.count_label.setProperty("sub", True)
        lay.addWidget(self.count_label)

        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("开始 AI 对比")
        btns.button(QDialogButtonBox.Ok).setEnabled(False)
        btns.accepted.connect(self._run)
        btns.rejected.connect(self.reject)
        self.ok_btn = btns.button(QDialogButtonBox.Ok)
        lay.addWidget(btns)

    # ------------------------------------------------------------ 选股
    def _append_pick(self, code: str, name: str, checked: bool) -> None:
        from ..core.providers.base import normalize_code
        code = normalize_code(code)
        if not code or code in self._pick_rows:
            return
        row = self.table.rowCount()
        self.table.insertRow(row)
        cb = QCheckBox()
        cb.setChecked(checked)
        cb.toggled.connect(lambda _: self._refresh_count())
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(cb)
        h.setAlignment(cb, Qt.AlignCenter)
        self.table.setCellWidget(row, 0, w)
        from PySide6.QtWidgets import QTableWidgetItem
        self.table.setItem(row, 1, QTableWidgetItem(code))
        self.table.setItem(row, 2, QTableWidgetItem(name))
        self._pick_rows[code] = row

    def _add_code(self) -> None:
        from ..core.providers.base import normalize_code
        text = normalize_code(self.code_edit.text())
        if not text:
            return
        self.code_edit.clear()
        self._append_pick(text, "", checked=True)
        # 后台补名称（行情拉回后按代码填表）
        submit(self.ctx.get_quotes, [text], on_done=self._on_name)

    def _on_name(self, quotes: dict) -> None:
        for code, q in (quotes or {}).items():
            row = self._pick_rows.get(code)
            if row is None or not q or not q.name:
                continue
            item = self.table.item(row, 2)
            if item is not None and not item.text():
                item.setText(q.name)
        self._refresh_count()

    def _checked_codes(self) -> list:
        out = []
        for code, row in self._pick_rows.items():
            w = self.table.cellWidget(row, 0)
            cb = w.findChild(QCheckBox)
            if cb and cb.isChecked():
                q = self.table.item(row, 2)
                out.append({"code": code, "name": q.text() if q else ""})
        return out

    def _refresh_count(self) -> None:
        n = len(self._checked_codes())
        self.count_label.setText(
            f"已选 {n} 只" + (f"（上限 {self.MAX}）" if n >= self.MAX else ""))
        self.ok_btn.setEnabled(2 <= n <= self.MAX)

    # ------------------------------------------------------------ 运行
    def _run(self) -> None:
        picks = self._checked_codes()
        if len(picks) < 2:
            return
        self.ok_btn.setEnabled(False)
        self.ok_btn.setText("正在取行情…")
        submit(self._fetch_all, picks,
               on_done=self._on_ready, on_err=self._on_err)

    def _fetch_all(self, picks: list) -> list:
        """同口径拉取：行情 + 日线指标（一次线程完成，主线程只收结果）。"""
        from ..core import indicators
        out = []
        quotes = self.ctx.get_quotes([p["code"] for p in picks]) or {}
        for p in picks:
            item = {"code": p["code"], "name": p["name"], "quote": None,
                    "ind": {}}
            q = quotes.get(p["code"])
            if q is not None:
                item["quote"] = q
                item["name"] = p["name"] or q.name
                try:
                    kls = self.ctx.get_kline(p["code"], "day", 120)
                    if kls:
                        item["ind"] = indicators.analyze(kls)
                except Exception:  # noqa: BLE001 —— 单只指标失败不拖垮对比
                    item["ind"] = {}
            out.append(item)
        return out

    def _on_ready(self, stocks: list) -> None:
        from ..core.prompt import build_compare_context
        names = "、".join(
            f"{s['name'] or s['code']}" for s in stocks if s.get("quote"))
        self.ask_ai.emit(build_compare_context(stocks),
                         f"AI对比 - {names[:40]}")
        self.accept()

    def _on_err(self, msg: str) -> None:
        self.ok_btn.setEnabled(True)
        self.ok_btn.setText("开始 AI 对比")
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(self, "对比失败", f"获取对比数据失败：\n{msg}")
