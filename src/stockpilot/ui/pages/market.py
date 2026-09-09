"""行情看板 v5：demo .market 页版式（PageHead + 指数大卡 + 自选表）。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QHeaderView, QLabel,
                                QLineEdit, QListWidget, QListWidgetItem, QMenu,
                                QPushButton, QTableWidget, QTableWidgetItem,
                                QVBoxLayout, QWidget)

from .. import kit
from ..theme import DOWN, FLAT, UP
from ..workers import submit
from ...core.monitor import is_trade_time
from ...core.providers.base import normalize_code


def pct_item(value, fmt: str = "{:+.2f}%") -> QTableWidgetItem:
    text = "—" if value is None else fmt.format(value)
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if value is not None and value != 0:
        item.setForeground(QColor(UP if value > 0 else DOWN))
    else:
        item.setForeground(QColor(FLAT))
    return item


def num_item(value, nd: int = 2, suffix: str = "") -> QTableWidgetItem:
    text = "—" if value is None else f"{value:.{nd}f}{suffix}"
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return item


def card_frame() -> QFrame:
    f = QFrame()
    f.setProperty("card", True)
    return f


class IndexCard(QFrame):
    """demo .quote：指数大卡（名称 / 大字价格 / 涨跌 + sparkline）。"""

    def __init__(self, name: str):
        super().__init__()
        self.setProperty("quote", True)
        self.name = name
        self.setMinimumHeight(110)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(15, 12, 15, 10)
        lay.setSpacing(2)
        self.title_label = QLabel(name)
        self.title_label.setProperty("statName", True)
        self.value_label = QLabel("—")
        self.value_label.setProperty("quoteValue", True)
        self.chg_label = QLabel("加载中…")
        self.chg_label.setProperty("quoteChg", True)
        self.spark = kit.SparkLine()
        self.spark.setFixedHeight(32)
        lay.addWidget(self.title_label)
        lay.addWidget(self.value_label)
        lay.addWidget(self.chg_label)
        lay.addWidget(self.spark)

    def update_quote(self, q) -> None:
        if q is None or q.price is None:
            self.value_label.setText("—")
            self.chg_label.setText("")
            return
        chg = q.change_pct or 0
        color = UP if chg > 0 else (DOWN if chg < 0 else FLAT)
        self.value_label.setText(f"{q.price:,.2f}")
        self.value_label.setStyleSheet(f"color:{color};")
        self.chg_label.setText(f"{q.change:+.2f}  {chg:+.2f}%")
        self.chg_label.setStyleSheet(f"color:{color};")


class MarketPage(QWidget):
    open_stock = Signal(str, str)
    today_advice = Signal(str, str)
    ask_ai = Signal(str, str)

    COLS = ["代码", "名称", "现价", "涨跌幅", "涨跌额", "今开", "最高",
            "最低", "换手%", "量比", "PE", "总市值(亿)"]
    INDEXES = [("sh000001", "上证指数"), ("sz399001", "深证成指"),
               ("sz399006", "创业板指"), ("sh000688", "科创50")]

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._in_flight = False
        self._cards = {}
        self._build_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self._apply_interval()

    # ------------------------------------------------------------ 结果提示（v5.2.2）
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

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(11, 11, 11, 11)
        lay.setSpacing(10)

        lay.addWidget(kit.page_head(
            "行情看板", "全市场实时行情 · 指数走势 · 自选监控"))

        # demo .quotes：四大指数大卡
        cards = QHBoxLayout()
        cards.setSpacing(10)
        for sym, name in self.INDEXES:
            card = IndexCard(name)
            self._cards[sym] = card
            cards.addWidget(card, 1)
        lay.addLayout(cards)

        # 搜索 + 导入导出条
        search_card = card_frame()
        s_lay = QHBoxLayout(search_card)
        s_lay.setContentsMargins(12, 8, 12, 8)
        s_lay.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            "输入 6 位代码回车直接加自选；或输入名称/代码搜索（如 茅台）")
        self.search_edit.returnPressed.connect(self.search)
        self.search_edit.setFixedHeight(32)
        btn = QPushButton("搜索")
        btn.clicked.connect(self.search)
        imp_btn = QPushButton("导入自选")
        imp_btn.setProperty("secondary", True)
        imp_btn.clicked.connect(self._import_watch)
        exp_btn = QPushButton("导出自选")
        exp_btn.setProperty("secondary", True)
        exp_btn.clicked.connect(self._export_watch)
        tpl_btn = QPushButton("模板")
        tpl_btn.setProperty("secondary", True)
        tpl_btn.setToolTip("下载自选股导入模板（CSV：code,name 两列示例）")
        from ..transfer import save_template as _tpl
        tpl_btn.clicked.connect(
            lambda: self._notice(_tpl(self, "watchlist")))
        cmp_btn = QPushButton("AI 对比")
        cmp_btn.setToolTip("多选自选/输入代码，一次让 AI 横向对比（趋势/量能/估值/风险）")
        cmp_btn.clicked.connect(self.open_compare)
        s_lay.addWidget(self.search_edit, 1)
        s_lay.addWidget(btn)
        s_lay.addWidget(imp_btn)
        s_lay.addWidget(exp_btn)
        s_lay.addWidget(tpl_btn)
        s_lay.addWidget(cmp_btn)
        lay.addWidget(search_card)

        self.suggest_list = QListWidget()
        self.suggest_list.setMaximumHeight(118)
        self.suggest_list.hide()
        self.suggest_list.itemClicked.connect(self._on_suggest_click)
        lay.addWidget(self.suggest_list)

        # demo .page-table：自选实时表
        table_card = card_frame()
        t_lay = QVBoxLayout(table_card)
        t_lay.setContentsMargins(8, 8, 8, 8)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemDoubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        t_lay.addWidget(self.table)
        lay.addWidget(table_card, 1)

        tip = QLabel("双击行打开个股详情；右键可删除自选 · Ctrl+1~9 切换页面")
        tip.setProperty("hint", True)
        lay.addWidget(tip)

    # ------------------------------------------------------------ 数据
    def _watch_codes(self):
        return [x["code"] for x in self.ctx.cfg.get_watchlist()]

    def refresh(self) -> None:
        if self._in_flight:
            return
        codes = [sym for sym, _ in self.INDEXES] + self._watch_codes()
        if not codes:
            return
        self._in_flight = True
        submit(self.ctx.get_quotes, codes, on_done=self._on_quotes,
               on_err=self._on_quotes_err)

    def _on_quotes(self, quotes: dict) -> None:
        self._in_flight = False
        for sym, card in self._cards.items():
            card.update_quote(quotes.get(sym))
        self._fill_table(quotes)

    def _on_quotes_err(self, msg: str) -> None:
        self._in_flight = False
        self._notify(f"行情刷新失败: {msg}", ok=False)

    def _fill_table(self, quotes: dict) -> None:
        watch = self.ctx.cfg.get_watchlist()
        self.table.setRowCount(len(watch))
        for r, w in enumerate(watch):
            q = quotes.get(w["code"])
            code_item = QTableWidgetItem(w["code"])
            code_item.setData(Qt.UserRole, w["code"])
            self.table.setItem(r, 0, code_item)
            self.table.setItem(r, 1, QTableWidgetItem(
                (q.name if q and q.name else w.get("name") or "")))
            if q is None:
                for c in range(2, len(self.COLS)):
                    self.table.setItem(r, c, QTableWidgetItem("—"))
                continue
            price_item = num_item(q.price)
            price_item.setForeground(QColor(
                UP if (q.change_pct or 0) > 0
                else (DOWN if (q.change_pct or 0) < 0 else FLAT)))
            self.table.setItem(r, 2, price_item)
            self.table.setItem(r, 3, pct_item(q.change_pct))
            self.table.setItem(r, 4, pct_item(q.change, "{:+.2f}"))
            self.table.setItem(r, 5, num_item(q.open))
            self.table.setItem(r, 6, num_item(q.high))
            self.table.setItem(r, 7, num_item(q.low))
            self.table.setItem(r, 8, num_item(q.turnover_rate))
            self.table.setItem(r, 9, num_item(q.volume_ratio))
            self.table.setItem(r, 10, num_item(q.pe, 1))
            self.table.setItem(r, 11, num_item(q.total_mv, 0))

    # ------------------------------------------------------------ 搜索
    def search(self) -> None:
        kw = self.search_edit.text().strip()
        if not kw:
            return
        code = normalize_code(kw)
        if code and len(kw) == 6:
            self._add_watch(code, "")
            return
        submit(self.ctx.suggest, kw, on_done=self._on_suggest,
               on_err=self._on_suggest_err)

    def _on_suggest(self, items: list) -> None:
        self.suggest_list.clear()
        if not items:
            self.suggest_list.hide()
            self._notify("未找到匹配的股票", ok=False)
            return
        for it in items:
            QListWidgetItem(f"{it.name}  {it.code}", self.suggest_list)
            row = self.suggest_list.item(self.suggest_list.count() - 1)
            row.setData(Qt.UserRole, (it.code, it.name))
        self.suggest_list.show()

    def _on_suggest_err(self, msg: str) -> None:
        self._notify(f"搜索失败: {msg}", ok=False)

    def _on_suggest_click(self, item: QListWidgetItem) -> None:
        code, name = item.data(Qt.UserRole)
        self._add_watch(code, name)

    def _add_watch(self, code: str, name: str) -> None:
        if self.ctx.cfg.add_watch(code, name):
            self.suggest_list.hide()
            self.search_edit.clear()
            self.refresh()
        else:
            self._notify(f"{code} 已在自选中")

    # ------------------------------------------------------------ 导入导出
    def open_compare(self) -> None:
        """AI 多股对比：勾选自选/添加代码 → ask_ai 跳 AI 分析页流式输出。"""
        from ..compare_dialog import CompareDialog
        dlg = CompareDialog(self.ctx, self.ctx.cfg.get_watchlist(), self)
        dlg.ask_ai.connect(self.ask_ai)
        dlg.exec()

    def _import_watch(self) -> None:
        from ..transfer import import_watchlist
        ok, msg = import_watchlist(self, self.ctx.cfg)
        self._notify(msg, ok=ok)
        if ok:
            self.refresh()

    def _export_watch(self) -> None:
        from ..transfer import export_watchlist
        _, msg = export_watchlist(self, self.ctx.cfg)
        self._notify(msg)

    # ------------------------------------------------------------ 交互
    def _on_double_click(self, item) -> None:
        row = item.row()
        code_item = self.table.item(row, 0)
        name_item = self.table.item(row, 1)
        if code_item:
            self.open_stock.emit(code_item.text(),
                                 name_item.text() if name_item else "")

    def _context_menu(self, pos) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        code = self.table.item(row, 0).text()
        name = self.table.item(row, 1).text()
        menu = QMenu(self)
        act_open = menu.addAction("打开详情")
        act_ai = menu.addAction("AI 今日操作建议")
        act_del = menu.addAction("删除自选")
        act = menu.exec(self.table.viewport().mapToGlobal(pos))
        if act == act_open:
            self.open_stock.emit(code, name)
        elif act == act_ai:
            self.today_advice.emit(code, name)
        elif act == act_del:
            self.ctx.cfg.remove_watch(code)
            self.refresh()

    # ------------------------------------------------------------ 定时
    def _apply_interval(self) -> None:
        market = self.ctx.cfg.get("market") or {}
        sec = int(market.get("refresh_sec") or 5)
        if not is_trade_time():
            sec = max(sec, 60)
        self.timer.start(sec * 1000)

    def apply_settings(self) -> None:
        self._apply_interval()
        self.refresh()

    def on_show(self) -> None:
        self.refresh()

    def _notice(self, r) -> None:
        """导入/模板结果提示（ok, msg）二元组统一弹出。"""
        from PySide6.QtWidgets import QMessageBox
        ok, msg = r
        (QMessageBox.information if ok else QMessageBox.warning)(self, "提示", msg)
