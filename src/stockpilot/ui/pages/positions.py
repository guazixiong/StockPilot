"""持仓管理页：手动持仓 + 实时盈亏 + 交易记录流水 + CSV 导出。"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox,
                               QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMenu,
                               QMessageBox, QPushButton, QSpinBox, QSplitter,
                               QTabWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ...core.monitor import is_trade_time
from ...core.portfolio import journal_stats, position_metrics
from ...core.providers.base import normalize_code
from .. import kit
from ..theme import DOWN, DANGER, FLAT, SUB, SUCCESS, UP, WARNING
from ..workers import submit
from .market import card_frame, num_item, pct_item

_POS_COLS = ["代码", "名称", "持仓日期", "成本", "数量", "现价", "市值",
             "浮动盈亏", "盈亏%", "今日盈亏", "今日%"]
_JNL_COLS = ["日期", "代码", "名称", "方向", "价格", "数量", "金额", "费用"]


class StatCard(QFrame):
    def __init__(self, name: str):
        super().__init__()
        self.setProperty("card", True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(2)
        title = QLabel(name)
        title.setProperty("statName", True)
        self.value_label = QLabel("—")
        self.value_label.setProperty("statValue", True)
        lay.addWidget(title)
        lay.addWidget(self.value_label)

    def set_value(self, text: str, color: str | None = None) -> None:
        self.value_label.setText(text)
        self.value_label.setStyleSheet(f"color:{color};" if color else "")


class PositionDialog(QDialog):
    def __init__(self, parent, strategies: list, pos: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("编辑持仓")
        self.resize(380, 260)
        g = QGridLayout(self)
        self.code_edit = QLineEdit(str(pos.get("code", "")) if pos else "")
        self.name_edit = QLineEdit(str(pos.get("name", "")) if pos else "")
        self.cost_spin = QDoubleSpinBox()
        self.cost_spin.setRange(0.01, 100000)
        self.cost_spin.setDecimals(3)
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(100, 10000000)
        self.qty_spin.setSingleStep(100)
        self.date_edit = QLineEdit(
            str(pos.get("date")) if pos and pos.get("date") else
            datetime.now().strftime("%Y-%m-%d"))
        self.stg_combo = QComboBox()
        self.stg_combo.addItems(strategies or ["趋势启动"])
        if pos:
            self.cost_spin.setValue(float(pos.get("cost") or 0))
            self.qty_spin.setValue(int(pos.get("qty") or 0))
            self.stg_combo.setCurrentText(str(pos.get("strategy") or "趋势启动"))
        g.addWidget(QLabel("代码"), 0, 0)
        g.addWidget(self.code_edit, 0, 1)
        g.addWidget(QLabel("名称"), 1, 0)
        g.addWidget(self.name_edit, 1, 1)
        g.addWidget(QLabel("成本价"), 2, 0)
        g.addWidget(self.cost_spin, 2, 1)
        g.addWidget(QLabel("数量(股)"), 3, 0)
        g.addWidget(self.qty_spin, 3, 1)
        g.addWidget(QLabel("买入日期"), 4, 0)
        g.addWidget(self.date_edit, 4, 1)
        g.addWidget(QLabel("卖出监听策略"), 5, 0)
        g.addWidget(self.stg_combo, 5, 1)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._ok)
        btns.rejected.connect(self.reject)
        g.addWidget(btns, 6, 0, 1, 2)

    def _ok(self) -> None:
        code = normalize_code(self.code_edit.text())
        if not code or self.cost_spin.value() <= 0 or self.qty_spin.value() <= 0:
            QMessageBox.warning(self, "提示", "请填写有效代码、成本与数量")
            return
        self.result_data = {
            "code": code, "name": self.name_edit.text().strip() or code,
            "cost": round(self.cost_spin.value(), 3),
            "qty": self.qty_spin.value(),
            "date": self.date_edit.text().strip(),
            "strategy": self.stg_combo.currentText(),
        }
        self.accept()


class TradeDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("新增交易记录")
        self.resize(380, 280)
        g = QGridLayout(self)
        self.date_edit = QLineEdit(datetime.now().strftime("%Y-%m-%d"))
        self.code_edit = QLineEdit("")
        self.name_edit = QLineEdit("")
        self.side_combo = QComboBox()
        self.side_combo.addItems(["buy", "sell"])
        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0.01, 100000)
        self.price_spin.setDecimals(3)
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 10000000)
        self.qty_spin.setSingleStep(100)
        self.fee_spin = QDoubleSpinBox()
        self.fee_spin.setRange(0, 100000)
        self.fee_spin.setDecimals(2)
        rows = [("日期", self.date_edit), ("代码", self.code_edit),
                ("名称", self.name_edit), ("方向(buy/sell)", self.side_combo),
                ("价格", self.price_spin), ("数量(股)", self.qty_spin),
                ("费用", self.fee_spin)]
        for i, (label, w) in enumerate(rows):
            g.addWidget(QLabel(label), i, 0)
            g.addWidget(w, i, 1)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        g.addWidget(btns, len(rows), 0, 1, 2)

    def record(self) -> dict:
        price = self.price_spin.value()
        qty = self.qty_spin.value()
        return {
            "date": self.date_edit.text().strip(),
            "code": normalize_code(self.code_edit.text()),
            "name": self.name_edit.text().strip(),
            "side": self.side_combo.currentText(),
            "price": round(price, 3), "qty": qty,
            "amount": round(price * qty, 2),
            "fee": round(self.fee_spin.value(), 2),
        }


class PositionsPage(QWidget):
    open_stock = Signal(str, str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._in_flight = False
        self._watch_tick = 0
        self._watch_running = False
        self._build_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(10000)

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(11, 11, 11, 11)
        lay.setSpacing(10)
        lay.addWidget(kit.page_head(
            "持仓管理", "实时盈亏 · 盯盘告警 · 交易流水 · 买入计划"))

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.card_mv = StatCard("总市值")
        self.card_pnl = StatCard("浮动盈亏")
        self.card_today = StatCard("今日盈亏")
        for c in (self.card_mv, self.card_pnl, self.card_today):
            cards.addWidget(c, 1)
        lay.addLayout(cards)

        bar = QFrame()
        bar.setProperty("card", True)
        b = QHBoxLayout(bar)
        b.setContentsMargins(14, 10, 14, 10)
        advice_btn = QPushButton("AI 持仓建议")
        advice_btn.clicked.connect(self._advice_all)
        add_pos = QPushButton("新增持仓")
        add_pos.clicked.connect(self._add_position)
        add_jnl = QPushButton("新增交易记录")
        add_jnl.setProperty("secondary", True)
        add_jnl.clicked.connect(self._add_journal)
        export = QPushButton("导出 CSV")
        export.setProperty("secondary", True)
        export.clicked.connect(self._export)
        b.addWidget(add_pos)
        b.addWidget(add_jnl)
        imp_btn = QPushButton("导入持仓")
        imp_btn.setProperty("secondary", True)
        imp_btn.clicked.connect(self._import_positions)
        exp_btn = QPushButton("导出持仓")
        exp_btn.setProperty("secondary", True)
        exp_btn.clicked.connect(self._export_positions)
        tpl_btn = QPushButton("模板")
        tpl_btn.setProperty("secondary", True)
        tpl_btn.setToolTip("下载持仓导入模板（CSV：code,name,cost,qty,date,strategy 六列示例）")
        from ..transfer import save_template as _tpl
        tpl_btn.clicked.connect(
            lambda: self._notice(_tpl(self, "positions")))
        b.addWidget(imp_btn)
        b.addWidget(exp_btn)
        b.addWidget(tpl_btn)
        b.addWidget(advice_btn)
        b.addWidget(export)
        b.addStretch(1)
        self.hint = QLabel(" ")
        self.hint.setProperty("hint", True)
        b.addWidget(self.hint)
        lay.addWidget(bar)

        self.tabs = QTabWidget()
        # 持仓表
        self.pos_table = QTableWidget(0, len(_POS_COLS))
        self.pos_table.setHorizontalHeaderLabels(_POS_COLS)
        self.pos_table.verticalHeader().hide()
        self.pos_table.setAlternatingRowColors(True)
        self.pos_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.pos_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.pos_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.pos_table.horizontalHeader().setStretchLastSection(True)
        self.pos_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pos_table.customContextMenuRequested.connect(self._pos_menu)
        self.tabs.addTab(self.pos_table, "当前持仓")
        # 流水表
        self.jnl_table = QTableWidget(0, len(_JNL_COLS))
        self.jnl_table.setHorizontalHeaderLabels(_JNL_COLS)
        self.jnl_table.verticalHeader().hide()
        self.jnl_table.setAlternatingRowColors(True)
        self.jnl_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.jnl_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.jnl_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.jnl_table.horizontalHeader().setStretchLastSection(True)
        self.jnl_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.jnl_table.customContextMenuRequested.connect(self._jnl_menu)
        self.tabs.addTab(self.jnl_table, "交易记录")
        # ---- 👁 盯盘 Tab（v6.0，融合 PanWatch 盘中监测思想）----
        watch_w = QWidget()
        wv = QVBoxLayout(watch_w)
        wv.setContentsMargins(2, 6, 2, 4)
        wv.setSpacing(6)
        wbar = QHBoxLayout()
        self.watch_toggle = QCheckBox("盘中开启盯盘（交易时段自动监控持仓风险与机会）")
        self.watch_toggle.setChecked(
            bool((self.ctx.cfg.monitor.get("watch") or {}).get("enabled")))
        self.watch_toggle.toggled.connect(self._save_watch_cfg)
        self.watch_btn = QPushButton("立即盯盘")
        self.watch_btn.clicked.connect(self._watch_now)
        self.watch_ai_btn = QPushButton("AI 解读全部告警")
        self.watch_ai_btn.setProperty("secondary", True)
        self.watch_ai_btn.clicked.connect(self._watch_ai)
        wbar.addWidget(self.watch_toggle, 1)
        wbar.addWidget(self.watch_btn)
        wbar.addWidget(self.watch_ai_btn)
        wv.addLayout(wbar)
        self.watch_status = QLabel("盯盘未开启——勾选后交易时段每 60 秒自动检查持仓"
                                   "（异动/止损止盈/破位/量能/超买卖）")
        self.watch_status.setProperty("hint", True)
        wv.addWidget(self.watch_status)
        self.alert_table = QTableWidget(0, 5)
        self.alert_table.setHorizontalHeaderLabels(
            ["级别", "股票", "告警", "建议动作", "详情"])
        self.alert_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        self.alert_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch)
        self.alert_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self.alert_table.verticalHeader().hide()
        wv.addWidget(self.alert_table, 1)
        self.tabs.addTab(watch_w, "盯盘")
        # ---- 🎯 Regime Tab（v6.2，融合用户 investment-system v4.11.0）----
        regime_w = QWidget()
        rv = QVBoxLayout(regime_w)
        rv.setContentsMargins(2, 6, 2, 4)
        rv.setSpacing(6)
        self.regime_btn = QPushButton("刷新市场状态（ETF 广度状态机）")
        self.regime_btn.clicked.connect(self._regime_refresh)
        rv.addWidget(self.regime_btn)
        self.plans_imp_btn = QPushButton("导入买入计划 JSON")
        self.plans_imp_btn.setProperty("secondary", True)
        self.plans_imp_btn.setToolTip(
            "导入自己的分层买入计划（支持 investment-system v4.x 的 buyPlans 格式："
            "双区间+追价上限+趋势门控思路）——导入后盘中调度器自动监控")
        self.plans_imp_btn.clicked.connect(self._import_plans_json)
        rv.addWidget(self.plans_imp_btn)
        self.holdings_imp_btn = QPushButton("导入持仓 JSON")
        self.holdings_imp_btn.setProperty("secondary", True)
        self.holdings_imp_btn.setToolTip(
            "从 JSON 文件导入持仓（holdings 格式：code/name/shares/cost + available_cash）")
        self.holdings_imp_btn.clicked.connect(self._import_holdings_json)
        rv.addWidget(self.holdings_imp_btn)
        # v7.1 §13.2 状态仪表盘：大字状态徽章 + 广度 + 建议仓位（稿式排版）
        self.regime_badge = QLabel("— 未刷新 —")
        self.regime_badge.setAlignment(Qt.AlignCenter)
        self.regime_badge.setStyleSheet(
            "font-size:26pt; font-weight:bold; background:transparent;"
            "padding:14px 0 6px 0;")
        rv.addWidget(self.regime_badge)
        self.regime_label = QLabel("基于 6 只核心 ETF 的上涨广度判定市场状态"
                                    "（BULL/RANGE/BEAR，连续 3 日确认切换）")
        self.regime_label.setWordWrap(True)
        self.regime_label.setAlignment(Qt.AlignCenter)
        self.regime_label.setProperty("hint", True)
        rv.addWidget(self.regime_label)
        self.regime_pos_label = QLabel("")
        self.regime_pos_label.setAlignment(Qt.AlignCenter)
        self.regime_pos_label.setProperty("sub", True)
        rv.addWidget(self.regime_pos_label)
        self.regime_table = QTableWidget(0, 3)
        self.regime_table.setHorizontalHeaderLabels(["ETF", "今日涨跌", "角色"])
        self.regime_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.regime_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self.regime_table.verticalHeader().hide()
        rv.addWidget(self.regime_table, 1)
        self.tabs.addTab(regime_w, "Regime 状态")
        # 盯盘状态（不持久化 UI 态）
        self._watch_throttle = None
        self._watch_last_alerts: list = []
        lay.addWidget(self.tabs, 1)

        tip = QLabel("双击持仓行打开详情 · 持仓自动纳入「策略监听」的卖出跟踪 · 数据仅存本机")
        tip.setProperty("hint", True)
        lay.addWidget(tip)

        self.refresh()


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

    # ------------------------------------------------------------ 刷新
    def refresh(self) -> None:
        if self._in_flight:
            return
        # v6.0 盯盘：60 秒节拍（复用 10s 行情定时器计数）、交易时段、开关三重门
        self._watch_tick += 1
        if (self._watch_tick % 6 == 0
                and (self.ctx.cfg.monitor.get("watch") or {}).get("enabled")
                and is_trade_time()):
            self._watch_now()
        codes = list(self.ctx.cfg.positions.keys())
        self._fill_journal()
        if not codes:
            self._fill_positions({})
            return
        self._in_flight = True
        submit(self.ctx.get_quotes, codes, on_done=self._on_quotes,
               on_err=self._on_err)

    def _on_quotes(self, quotes: dict) -> None:
        self._in_flight = False
        self._fill_positions(quotes)

    def _on_err(self, msg: str) -> None:
        self._in_flight = False
        self._notify(f"行情刷新失败: {msg}", ok=False)

    def _fill_positions(self, quotes: dict) -> None:
        from ...core.portfolio import portfolio_summary
        rows = []
        positions = self.ctx.cfg.positions
        for code, pos in positions.items():
            m = position_metrics(pos, quotes.get(code))
            rows.append((code, pos, m))
        rows.sort(key=lambda x: x[0])
        self.pos_table.setRowCount(len(rows))
        for r, (code, pos, m) in enumerate(rows):
            vals = [code, pos.get("name", ""), pos.get("date", ""),
                    f"{pos.get('cost'):.3f}", f"{pos.get('qty'):,.0f}",
                    f"{m['price']:.2f}" if m["price"] is not None else "—",
                    f"{m['market_value']:,.0f}" if m["market_value"] is not None else "—",
                    f"{m['pnl']:+,.0f}" if m["pnl"] is not None else "—",
                    f"{m['pnl_pct']:+.2f}%" if m["pnl_pct"] is not None else "—",
                    f"{m['today_pnl']:+,.0f}" if m["today_pnl"] is not None else "—",
                    f"{m['today_pnl_pct']:+.2f}%" if m["today_pnl_pct"] is not None else "—"]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                item.setData(Qt.UserRole, (code, pos))
                if c in (5, 6, 7, 8, 9, 10):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 7 and m["pnl"] is not None and m["pnl"] != 0:
                    item.setForeground(QColor(UP if m["pnl"] > 0 else DOWN))
                if c == 8 and m["pnl_pct"]:
                    item.setForeground(QColor(
                        UP if m["pnl_pct"] > 0 else DOWN))
                if c == 9 and m["today_pnl"] is not None and m["today_pnl"] != 0:
                    item.setForeground(QColor(
                        UP if m["today_pnl"] > 0 else DOWN))
                self.pos_table.setItem(r, c, item)
        s = portfolio_summary([m for _, _, m in rows])
        self.card_mv.set_value(f"{s['market_value']:,.0f}")
        self.card_pnl.set_value(f"{s['pnl']:+,.0f}",
                                UP if s["pnl"] > 0 else (DOWN if s["pnl"] < 0 else None))
        self.card_today.set_value(f"{s['today_pnl']:+,.0f}",
                                  UP if s["today_pnl"] > 0 else (DOWN if s["today_pnl"] < 0 else None))

    def _fill_journal(self) -> None:
        records = self.ctx.cfg.get_journal()
        self.jnl_table.setRowCount(len(records))
        for r, rec in enumerate(records):
            side = str(rec.get("side") or "")
            vals = [rec.get("date", ""), rec.get("code", ""),
                    rec.get("name", ""),
                    "买入" if side == "buy" else "卖出",
                    f"{rec.get('price')}", f"{rec.get('qty'):,}",
                    f"{rec.get('amount'):,.0f}", f"{rec.get('fee'):,.0f}"]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                item.setData(Qt.UserRole, r)
                if c == 3:
                    item.setForeground(QColor(UP if side == "buy" else DOWN))
                if c in (4, 5, 6, 7):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.jnl_table.setItem(r, c, item)
        s = journal_stats(records)
        # v7.1 §14 财务流水顶统计（四联统计卡复用 StatCard 组件）
        if not hasattr(self, "jnl_cards"):
            from PySide6.QtWidgets import QHBoxLayout as _H
            wrap = _H()
            wrap.setSpacing(12)
            self.jnl_card_buy = StatCard("总买入")
            self.jnl_card_sell = StatCard("总卖出")
            self.jnl_card_fee = StatCard("总费用")
            self.jnl_card_net = StatCard("净投入")
            for c in (self.jnl_card_buy, self.jnl_card_sell,
                      self.jnl_card_fee, self.jnl_card_net):
                wrap.addWidget(c, 1)
            # 插到 Tabs 上方（页面纵向布局，索引定位）
            page_lay = self.layout()
            wrap_widget = QWidget()
            wrap_widget.setLayout(wrap)
            page_lay.insertWidget(page_lay.indexOf(self.tabs), wrap_widget)
            self.jnl_cards = True
        self.jnl_card_buy.set_value(f"¥{s['buy']:,.0f}")
        self.jnl_card_sell.set_value(f"¥{s['sell']:,.0f}")
        self.jnl_card_fee.set_value(f"¥{s['fee']:,.0f}")
        self.jnl_card_net.set_value(f"¥{s['net']:,.0f}")
        self.hint.setText("")

    # ------------------------------------------------------------ 操作
    def _add_position(self) -> None:
        strategies = [s.name for s in self._all_strategies()]
        dlg = PositionDialog(self, strategies)
        if dlg.exec():
            d = dlg.result_data
            self.ctx.cfg.save_position(d["code"], d)
            self.refresh()

    def _all_strategies(self):
        from ...core import strategy as stg
        builtins = stg.builtin_strategies()
        custom = [stg.Strategy.from_dict(d)
                  for d in self.ctx.cfg.get_strategy_dicts()]
        return builtins + custom

    def _pos_menu(self, pos) -> None:
        row = self.pos_table.currentRow()
        if row < 0:
            return
        code, info = self.pos_table.item(row, 0).data(Qt.UserRole)
        menu = QMenu(self)
        act_detail = menu.addAction("打开详情")
        act_edit = menu.addAction("修改")
        act_del = menu.addAction("删除")
        act = menu.exec(self.pos_table.viewport().mapToGlobal(pos))
        if act == act_detail:
            self.open_stock.emit(code, info.get("name", ""))
        elif act == act_edit:
            dlg = PositionDialog(self, [s.name for s in self._all_strategies()],
                                 info)
            if dlg.exec():
                d = dlg.result_data
                d["code"] = code
                self.ctx.cfg.save_position(code, d)
                self.refresh()
        elif act == act_del:
            self.ctx.cfg.remove_position(code)
            self.refresh()

    def _add_journal(self) -> None:
        dlg = TradeDialog(self)
        if dlg.exec():
            rec = dlg.record()
            if not rec["code"]:
                QMessageBox.warning(self, "提示", "代码不能为空")
                return
            self.ctx.cfg.add_journal(rec)
            self._fill_journal()

    def _jnl_menu(self, pos) -> None:
        row = self.jnl_table.currentRow()
        if row < 0:
            return
        idx = self.jnl_table.item(row, 0).data(Qt.UserRole)
        menu = QMenu(self)
        act_del = menu.addAction("删除该记录")
        act = menu.exec(self.jnl_table.viewport().mapToGlobal(pos))
        if act == act_del:
            self.ctx.cfg.remove_journal(int(idx))
            self._fill_journal()

    def _import_plans_json(self) -> None:
        """导入用户自己的买入计划 JSON（通用文件选择，不预置标的）。"""
        from ..transfer import import_plans_json
        ok, msg = import_plans_json(self, self.ctx.cfg)
        self._notify(msg, ok=ok)
        if ok:
            self._regime_refresh()

    def _import_holdings_json(self) -> None:
        """从 JSON 导入持仓（通用——任何人的 holdings 格式文件）。"""
        from ..transfer import import_holdings_json
        ok, msg = import_holdings_json(self, self.ctx.cfg)
        self._notify(msg, ok=ok)
        if ok:
            self.refresh()

    def _on_watch_err(self, msg: str) -> None:
        from ..err import fail_hint, show_error
        show_error(self, "盯盘失败", msg)
        fail_hint(self.watch_status, "盯盘失败，详见弹窗")

    def _on_regime_err(self, msg: str) -> None:
        """v7.2：Regime 刷新失败弹窗，仪表盘副标题保持占位不被长错误撑破。"""
        from ..err import fail_hint, show_error
        show_error(self, "Regime 刷新失败", msg)
        fail_hint(self.regime_label, "刷新失败，可重试")

    # ================================================================ Regime 状态机（v6.2）
    def _regime_refresh(self) -> None:
        """拉 ETF 行情 → 状态机推进（每日一次）→ 目标配置展示。"""
        from ...core import regime as rg
        codes = [c for c, _, _ in rg.DEFAULT_UNIVERSE]
        submit(self._do_regime, codes, on_done=self._on_regime,
               on_err=self._on_regime_err)

    def _do_regime(self, codes):
        from ...core import regime as rg
        from datetime import datetime
        quotes = self.ctx.get_quotes(codes)
        rows = [{"code": c, "change_pct": q.change_pct}
                for c, q in quotes.items()]
        # 确认状态持久化于 config（与用户系统存 JSON state 同思路）
        saved = self.ctx.cfg.monitor.get("regime") or {}
        st = rg.RegimeState(confirmed=saved.get("confirmed") or "RANGE",
                            pending=saved.get("pending"),
                            pending_days=int(saved.get("pending_days") or 0),
                            last_date=saved.get("last_date") or "")
        res = rg.classify_regime(rows, st, today=datetime.now().strftime("%Y-%m-%d"))
        self.ctx.cfg.monitor["regime"] = {
            "confirmed": st.confirmed, "pending": st.pending,
            "pending_days": st.pending_days, "last_date": st.last_date}
        self.ctx.cfg.save()
        capital = float((self.ctx.cfg.monitor.get("watch") or {}).get("capital")
                        or 0.0)   # 未设置资金时 0=只显示权重不显示金额
        targets = rg.target_weights(res.state, capital)
        return res, rows, targets

    def _on_regime(self, result) -> None:
        """渲染状态卡：状态徽章 + 广度 + 目标仓位 + 各 ETF 配置。"""
        res, rows, targets = result
        badge = {"BULL": "🐂", "RANGE": "⚖️", "BEAR": "🐻"}[res.state]
        arrow = " → 状态切换！" if res.changed else ""
        color = {"BULL": UP, "RANGE": FLAT, "BEAR": DOWN}[res.state]
        self.regime_badge.setText(f"{badge} {res.state}")
        self.regime_badge.setStyleSheet(
            f"font-size:26pt; font-weight:bold; background:transparent;"
            f"color:{color}; padding:14px 0 6px 0;")
        self.regime_label.setText(
            f"ETF 广度：{res.advancing}/{res.total} = {res.score:.0%}"
            f"　·　连续确认 3 日（防假突破）{arrow}")
        self.regime_pos_label.setText(
            f"建议目标仓位：{res.target_position:.0%}")
        t = self.regime_table
        t.setRowCount(0)
        capital = float((self.ctx.cfg.monitor.get("watch") or {}).get("capital") or 0.0)
        from ...core import regime as rg
        roles = {c: r for c, _, r in rg.DEFAULT_UNIVERSE}
        names = {c: n for c, n, _ in rg.DEFAULT_UNIVERSE}
        chg = {r["code"]: r["change_pct"] for r in rows}
        for code in [c for c, _, _ in rg.DEFAULT_UNIVERSE]:
            row = t.rowCount()
            t.insertRow(row)
            tgt = targets.get(code)
            t.setItem(row, 0, QTableWidgetItem(
                f"{names.get(code, code)}（{code}）"
                + (f" 目标 ¥{tgt:,.0f}" if tgt
                   else ("  本状态无配置" if capital <= 0 else "  目标 ¥0"))))
            pct = chg.get(code)
            item = pct_item(pct)
            t.setItem(row, 1, item)
            t.setItem(row, 2, QTableWidgetItem(roles.get(code, "")))

    # ================================================================ 盯盘（v6.0）
    def _save_watch_cfg(self, on: bool) -> None:
        w = self.ctx.cfg.monitor.get("watch") or {}
        w["enabled"] = on
        self.ctx.cfg.monitor["watch"] = w
        self.ctx.cfg.save()
        self.watch_status.setText(
            "盯盘已开启——交易时段每 60 秒自动检查（每条告警 30 分钟节流）"
            if on else "盯盘未开启——勾选后交易时段自动监控")

    def _watch_now(self) -> None:
        """手动/定时触发一轮盯盘：持仓逐只跑规则引擎，节流后渲染+推送。"""
        from ...core import watch as w_mod
        from ...core.monitor import is_trade_time
        positions = self.ctx.cfg.positions
        if not positions:
            self._render_alerts([])
            self.watch_status.setText("暂无持仓——先在「当前持仓」页录入")
            return
        if not is_trade_time():
            self.watch_status.setText("当前不在 A 股交易时段（盯盘仍可手动执行）")
        if self._watch_throttle is None:
            self._watch_throttle = w_mod.AlertThrottle(minutes=30)
        submit(self._do_watch, on_done=self._on_watch,
               on_err=self._on_watch_err)

    def _do_watch(self):
        """后台：逐只持仓取行情+日K指标+**分时** → 双层规则（v6.1）。

        日K层（watch）：位置与风控（止损止盈/均线/RSI）；
        分时层（intraday）：当下盘口行为（急拉跳水/冲高回落/VWAP 乖离/
        尾盘异动/量能脉冲）——用户指出盯盘应"根据分时走"，此为新增层。
        """
        from ...core import (indicators, intraday, regime as rg,
                            watch as w_mod)
        positions = dict(self.ctx.cfg.positions)
        codes = list(positions.keys())
        quotes = self.ctx.get_quotes(codes)
        out = []
        intraday_parts = []
        for code, pos in positions.items():
            q = quotes.get(code)
            if not q or not q.price:
                continue
            try:
                kls = self.ctx.kline_for_scan(code, 800)
                ind = indicators.analyze(kls)
            except Exception:  # noqa: BLE001 单只失败不拖垮盯盘
                ind = {}
            # 日K层
            out.extend(w_mod.check_position(code, q.name or code, pos,
                                            q, ind))
            # v6.2 卖出优先级链（用户 v4.11.0 口径：硬止损>趋势破位>止盈减半仓）
            from ...core import regime as rg
            cost = float(pos.get("cost") or 0)
            sig = rg.sell_decision(cost, q.price or 0.0, ind)
            if sig.level in ("RISK", "TAKE_PROFIT"):
                out.append(w_mod.WatchAlert(
                    code=code, name=q.name or code,
                    kind=f"纪律·{sig.reason.split()[0]}",
                    level="danger" if sig.level == "RISK" else "chance",
                    title=sig.title,
                    detail=f"成本 {cost:.2f} 现价 {q.price:.2f}",
                    action=sig.reason,
                    price=q.price or 0.0, change_pct=q.change_pct or 0.0))
            # v6.2 买入计划执行层（用户 buyPlans：双区间+追价拦截+趋势门控）
            try:
                plans = {pl.code: pl for pl in rg.load_buy_plans(self.ctx.cfg.monitor)}
                pl = plans.get(code)
                if pl:
                    psig = rg.classify_plan(
                        pl, q.price, rg.trend_gate(ind, q.price))
                    if psig.level == "BUY":
                        out.append(w_mod.WatchAlert(
                            code=code, name=q.name or code,
                            kind="计划·BUY",
                            level="chance", title=psig.title,
                            detail=f"目标金额 ¥{pl.target_amount:,.0f}；{psig.reason}",
                            action="进入你设定的买入区间（仅提醒，人工执行）",
                            price=q.price or 0.0,
                            change_pct=q.change_pct or 0.0))
            except Exception:  # noqa: BLE001 计划层失败不拖垮盯盘
                pass
            # 分时层（拉不到分时不阻塞日K告警）
            try:
                ms = self.ctx.tencent.get_minute(code)
                sigs = intraday.analyze_intraday(ms)
                for sg in sigs:
                    out.append(w_mod.WatchAlert(
                        code=code, name=q.name or code,
                        kind=f"分时·{sg.kind}",
                        level=sg.level, title=sg.title,
                        detail=sg.detail, action=sg.action,
                        price=q.price or 0.0,
                        change_pct=q.change_pct or 0.0))
                    intraday_parts.append(f"{q.name}：{sg.title}")
            except Exception:  # noqa: BLE001
                pass
        return out

    def _on_watch(self, alerts: list) -> None:
        """节流 → 渲染 → 声音/托盘/推送（与监听通知同一出口）。"""
        from ...core import watch as w_mod
        fresh = self._watch_throttle.filter(alerts) if self._watch_throttle else alerts
        self._watch_last_alerts = alerts
        from ...core import intraday as _in
        _has_intraday = any(a.kind.startswith("分时·") for a in alerts)
        self._render_alerts(alerts)          # 表格显示全量（当轮）
        from ...core.monitor import is_trade_time
        if fresh:
            text = w_mod.alerts_summary(fresh)
            self.watch_status.setText(text)
            # 复用既有通知链（webhook 推送+声音+托盘由主窗统一处理策略）
            try:
                from ...core import notify as notify_mod
                notify_mod.notify_signals(self.ctx.cfg.notify, fresh)
            except Exception:  # noqa: BLE001
                pass
        elif is_trade_time():
            self.watch_status.setText(f"盯盘中（{datetime.now().strftime('%H:%M')}）"
                                      f"—— 本轮无新增告警")
        else:
            self.watch_status.setText("非交易时段——本轮检查完成，无新增告警")

    def _render_alerts(self, alerts: list) -> None:
        """v7.1 §13.1：风险永远优先于机会（稿核心原则）——danger 恒置顶。"""
        from ...core import watch as w_mod
        from ..theme import DANGER, SUCCESS, WARNING
        color = {"danger": DANGER, "warning": WARNING,
                 "chance": SUCCESS, "info": SUB}
        order = {"danger": 0, "warning": 1, "chance": 2, "info": 3}
        alerts = sorted(alerts,
                        key=lambda a: order.get(getattr(a, "level", "info"), 9))
        t = self.alert_table
        t.setRowCount(0)
        for a in alerts:
            row = t.rowCount()
            t.insertRow(row)
            lvl = QTableWidgetItem(a.level)
            lvl.setForeground(QColor(color.get(a.level, "#7890AA")))
            t.setItem(row, 0, lvl)
            t.setItem(row, 1, QTableWidgetItem(f"{a.name} {a.code}"))
            t.setItem(row, 2, QTableWidgetItem(a.title))
            t.setItem(row, 3, QTableWidgetItem(a.action))
            t.setItem(row, 4, QTableWidgetItem(a.detail))
        if not alerts:
            t.setRowCount(1)
            t.insertRow(0)
            t.setItem(0, 0, QTableWidgetItem("✅ 全部持仓状态正常，无告警"))

    def _watch_ai(self) -> None:
        """AI 解读全部告警（就地输出到建议栏——同 AI 持仓建议通道）。"""
        if not self._watch_last_alerts:
            QMessageBox.information(self, "AI 解读", "当前无告警可解读")
            return
        from ...core import watch as w_mod
        lines = [f"{a.name}({a.code}) {a.title}：{a.detail} → 建议{a.action}"
                 for a in self._watch_last_alerts[:20]]
        nl = chr(10)
        self._advice_with_context(
            "以下是持仓盯盘产生的告警列表，请逐条给出应对思路（按风险从高到低），"
            "并对整体持仓健康度给一句总评：" + nl + nl.join(lines))

    def _advice_with_context(self, context: str) -> None:
        """通用 AI 建议入口：给定上下文（盯盘告警等）流式出建议窗。"""
        try:
            client = self.ctx.ai_client()
        except RuntimeError as exc:
            QMessageBox.warning(self, "AI 未配置", str(exc))
            return
        from ...core import prompt
        messages = prompt.build_messages("自由问答", context, "")
        self._advice_win = self._make_advice_win()
        self._advice_run(client, messages, self._advice_win)

    def _advice_all(self) -> None:
        """对全部持仓生成当日操作建议（含盈亏/风控/盘中位置）。"""
        positions = self.ctx.cfg.positions
        if not positions:
            QMessageBox.information(self, "AI 持仓建议", "暂无持仓，请先新增")
            return
        try:
            client = self.ctx.ai_client()
        except RuntimeError as exc:
            QMessageBox.warning(self, "AI 未配置", str(exc))
            return
        codes = list(positions.keys())
        submit(self._load_for_advice, codes, positions,
               on_done=lambda r: self._on_advice_ctx(client, r))

    def _load_for_advice(self, codes, positions):
        quotes = self.ctx.get_quotes(codes)
        lines = []
        for code, pos in positions.items():
            m = position_metrics(pos, quotes.get(code))
            lines.append(
                f"持仓 {pos.get('name','')}({code})：成本 {pos.get('cost')}"
                f" × {pos.get('qty')}股，现价 {m['price'] if m['price'] else '—'}，"
                f"浮动盈亏 {m['pnl_pct'] if m['pnl_pct'] is not None else '—'}%，"
                f"今日盈亏 {m['today_pnl'] if m['today_pnl'] is not None else '—'}")
        return chr(10).join(lines)

    def _on_advice_ctx(self, client, context):
        from ...core import prompt
        messages = prompt.build_messages(
            "自由问答",
            "以下是用户当前全部持仓实况：" + chr(10) + context,
            "请逐只给出今日操作建议：加仓/持有/减仓/清仓 + 理由 + 止盈止损位，"
            "并给组合层面建议（仓位是否过重/风格集中度）。分点输出。")
        self._advice_win = self._make_advice_win()
        self._advice_run(client, messages, self._advice_win)

    def _make_advice_win(self):
        from PySide6.QtWidgets import QDialog, QTextBrowser
        dlg = QDialog(self)
        dlg.setWindowTitle("AI 持仓建议")
        dlg.resize(560, 520)
        v = QVBoxLayout(dlg)
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        v.addWidget(view)
        dlg.show()
        return dlg, view

    def _advice_run(self, client, messages, pair):
        dlg, view = pair
        from ..ai_stream import append_stream, finalize_stream
        from PySide6.QtCore import Signal as _S

        class _Bridge(QWidget):
            d = _S(str, str)

        bridge = _Bridge()
        # view 存引用防 GC
        dlg._bridge = bridge
        dlg._view = view

        def _mk(dlg=dlg, view=view, bridge=bridge):
            bridge.d.connect(lambda k, t: append_stream(view, k, t))

        class _Worker2:
            pass

        # 用全局 Worker 框架
        from ..workers import submit as _submit
        _mk()

        def on_done(reply):
            append_stream(view, "content", "<br>")
            append_stream(view, "disclaimer", "")
            finalize_stream(view)

        def on_err(msg):
            append_stream(view, "error", str(msg))

        def run(client=client, messages=messages, bridge=bridge):
            return client.chat(messages, stream=True,
                               on_delta=lambda k, t: bridge.d.emit(k, t))
        _submit(run, on_done=on_done, on_err=on_err)

    def _import_positions(self) -> None:
        from ..transfer import import_positions
        ok, msg = import_positions(self, self.ctx.cfg)
        QMessageBox.information(self, "导入持仓", msg)
        if ok:
            self.refresh()

    def _export_positions(self) -> None:
        from ..transfer import export_positions
        ok, msg = export_positions(self, self.ctx.cfg)
        QMessageBox.information(self, "导出持仓", msg)

    def _export(self) -> None:
        from ...core.export_csv import rows_to_csv
        from PySide6.QtWidgets import QFileDialog

        if self.tabs.currentIndex() == 0:
            headers = _POS_COLS
            rows = [{c: self.pos_table.item(r, c).text()
                     for c in range(len(headers))}
                    for r in range(self.pos_table.rowCount())]
            default = "持仓.csv"
        else:
            headers = _JNL_COLS
            rows = [{c: self.jnl_table.item(r, c).text()
                     for c in range(len(headers))}
                    for r in range(self.jnl_table.rowCount())]
            default = "交易记录.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", default, "CSV (*.csv)")
        if not path:
            return
        rows_to_csv(path, headers, rows)
        QMessageBox.information(self, "导出", f"已导出 {len(rows)} 行到\n{path}")

    def on_show(self) -> None:
        self.refresh()

    def _notice(self, r) -> None:
        """导入/模板结果提示（ok, msg）二元组统一弹出。"""
        from PySide6.QtWidgets import QMessageBox
        ok, msg = r
        (QMessageBox.information if ok else QMessageBox.warning)(self, "提示", msg)
