"""策略监听页 v2：Tab(交易信号/策略库) + 自动扫描 + 声音托盘提醒 + 导出。"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import SIGNAL, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox,
                               QStackedWidget, QTabWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ...core import notify as notify_mod
from ...core import strategy as stg
from ...core.models import TradeSignal
from ...core.monitor import is_trade_time
from ...core.strategy import INDICATOR_NAMES, en_key, zh_name
from .. import kit
from ..theme import DOWN, UP
from ..workers import submit

# 指标按类别分组（key → 显示名）；编辑器下拉按组展示中文
INDICATOR_GROUPS = [
    ("行情快照", ["price", "open", "high", "low", "change_pct",
                 "prev_close", "prev_low", "amount", "amplitude"]),
    ("均线", ["ma5", "ma10", "ma20", "ma30", "ma60", "ma120", "ma250",
              "ema12", "ema26", "ma_bull", "ma_bear", "ma20_rising"]),
    ("MACD / KDJ / RSI", ["dif", "dea", "macd_hist", "macd_golden",
                          "macd_dead", "macd_bull", "k", "d", "j",
                          "kdj_golden", "rsi6", "rsi12", "rsi24"]),
    ("动量 / 区间", ["roc12", "psy12", "bias6", "bias12", "bias24",
                    "wr14", "cci14", "boll_up", "boll_mid", "boll_low",
                    "dd_from_high20", "chg5d", "chg10d", "chg20d", "chg60d"]),
    ("趋向 / 能量", ["plus_di", "minus_di", "adx", "dmi_golden",
                    "obv_rising", "up_days", "down_days"]),
    ("N日高低（前N日）", ["high10_prev", "high20_prev", "high30_prev",
                         "high60_prev", "low10_prev", "low20_prev",
                         "low30_prev", "low60_prev"]),
    ("量价", ["volume_ratio", "turnover_rate", "vol_vs_ma5", "amt_ratio_5d"]),
    ("基本面", ["pe", "pb", "float_mv", "total_mv"]),
    ("筹码分布", ["cyq_profit", "cyq_avg_cost", "cyq_near", "cyq_conc"]),
]
# 布尔指标：编辑器自动填 true
BOOL_INDICATORS = {"ma_bull", "ma_bear", "ma20_rising", "macd_golden",
                   "macd_dead", "macd_bull", "kdj_golden", "dmi_golden",
                   "obv_rising"}
OP_OPTIONS = [">", "<", ">=", "<=", "==", "between"]
OP_NAMES = {">": "高于", "<": "低于", ">=": "不低于", "<=": "不高于",
            "==": "等于", "between": "介于"}
_BOOL_HINT = ("布尔指标（如 均线多头排列/MACD金叉）直接勾选即成立；"
              "between 填 下限,上限；阈值也可填另一指标中文名做对比（如 现价 高于 20日均线MA20）")
_SIGNAL_COLS = ["时间", "代码", "名称", "策略", "方向", "现价", "止损",
                "目标", "风险", "信号说明"]


def _parse_value(raw: str, op: str):
    raw = raw.strip()
    if op == "between":
        parts = [x for x in raw.replace("，", ",").split(",") if x.strip()]
        return [float(x) for x in parts[:2]]
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    try:
        return float(raw)
    except ValueError:
        return raw if raw else 0.0


def add_rule_row(table: QTableWidget, rule) -> None:
    row = table.rowCount()
    table.insertRow(row)
    ind = QComboBox()
    _fill_indicator_combo(ind)
    op = QComboBox()
    for op_code in OP_OPTIONS:
        op.addItem(OP_NAMES[op_code], userData=op_code)
    val = QLineEdit()
    val.setToolTip("数值；between 填 下限,上限；或另一指标中文名（如 20日均线MA20）")
    if rule is not None:
        ind.setCurrentIndex(ind.findData(rule.indicator))
        op.setCurrentIndex(op.findData(rule.op))
        v = rule.value
        val.setText(",".join(str(x) for x in v) if isinstance(v, (list, tuple))
                    else str(v))
    else:
        val.setText("0")
    # 选中布尔指标时自动填 true（方便用户）
    ind.currentIndexChanged.connect(
        lambda _i, c=ind, v=val: v.setText("true")
        if c.currentData() in BOOL_INDICATORS and v.text().strip() in ("", "0")
        else None)
    table.setCellWidget(row, 0, ind)
    table.setCellWidget(row, 1, op)
    table.setCellWidget(row, 2, val)


def _fill_indicator_combo(combo: QComboBox) -> None:
    """按类别分组填充中文指标下拉；currentData 存英文键。"""
    combo.blockSignals(True)
    combo.clear()
    for group, keys in INDICATOR_GROUPS:
        combo.insertSeparator(combo.count())
        combo.addItem(f"—— {group} ——", userData=None)
        for key in keys:
            combo.addItem(zh_name(key), userData=key)
    combo.blockSignals(False)


class StrategyDialog(QDialog):
    """新建/编辑自定义策略（内置策略打开时为复制副本）。"""

    def __init__(self, parent, base: stg.Strategy):
        super().__init__(parent)
        self.setWindowTitle("编辑策略")
        self.resize(760, 620)
        self.strategy: stg.Strategy = base

        lay = QVBoxLayout(self)
        form = QGridLayout()
        self.name_edit = QLineEdit(base.name)
        self.desc_edit = QLineEdit(base.desc)
        form.addWidget(QLabel("策略名称"), 0, 0)
        form.addWidget(self.name_edit, 0, 1, 1, 3)
        form.addWidget(QLabel("说明"), 1, 0)
        form.addWidget(self.desc_edit, 1, 1, 1, 3)
        lay.addLayout(form)

        self.buy_table = self._make_rule_table(
            base.buy_rules,
            "买入规则（and 全部命中；or 任一命中，需同时满足全部 and）")
        self.sell_table = self._make_rule_table(
            base.sell_rules, "卖出规则（可选，如 price < ma20 跌破均线）")

        hint = QLabel(_BOOL_HINT)
        hint.setProperty("hint", True)
        risk = QHBoxLayout()
        self.stop_spin = QDoubleSpinBox()
        self.tp_spin = QDoubleSpinBox()
        self.hold_spin = QSpinBox()
        self.stop_spin.setRange(1, 30)
        self.tp_spin.setRange(1, 60)
        self.hold_spin.setRange(1, 120)
        self.stop_spin.setValue(base.stop_loss_pct)
        self.tp_spin.setValue(base.take_profit_pct)
        self.hold_spin.setValue(base.max_hold_days)
        risk.addWidget(QLabel("止损%"))
        risk.addWidget(self.stop_spin)
        risk.addWidget(QLabel("止盈%"))
        risk.addWidget(self.tp_spin)
        risk.addWidget(QLabel("最大持有(天)"))
        risk.addWidget(self.hold_spin)
        risk.addStretch(1)

        lay.addWidget(self.buy_table, 2)
        lay.addWidget(self.sell_table, 1)
        lay.addWidget(hint)
        lay.addLayout(risk)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    @staticmethod
    def _make_rule_table(rules: list, title: str) -> QGroupBox:
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["指标", "运算符", "阈值 / 对比指标"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.verticalHeader().hide()
        table.setMinimumHeight(110)
        for r in rules:
            add_rule_row(table, r)
        lay.addWidget(table)
        btns = QHBoxLayout()
        add = QPushButton("添加规则")
        add.setProperty("secondary", True)
        add.clicked.connect(lambda: add_rule_row(table, None))
        rm = QPushButton("删除选中")
        rm.setProperty("secondary", True)
        rm.clicked.connect(lambda: table.removeRow(table.currentRow()))
        btns.addWidget(add)
        btns.addWidget(rm)
        btns.addStretch(1)
        lay.addLayout(btns)
        return box

    def _read_table(self, table: QTableWidget) -> list:
        rules = []
        for row in range(table.rowCount()):
            ind_widget = table.cellWidget(row, 0)
            key = ind_widget.currentData() or en_key(ind_widget.currentText())
            if not key:
                continue  # 分组标题行
            op_widget = table.cellWidget(row, 1)
            op = op_widget.currentData() or op_widget.currentText()
            raw = table.cellWidget(row, 2).text()
            if not raw.strip():
                continue
            try:
                value = _parse_value(raw, op)
            except ValueError:
                continue
            rules.append(stg.Rule(indicator=key, op=op, value=value))
        return rules

    def _save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "策略名称不能为空")
            return
        buy_rules = self._read_table(self.buy_table.findChild(QTableWidget))
        sell_rules = self._read_table(self.sell_table.findChild(QTableWidget))
        if not buy_rules:
            QMessageBox.warning(self, "提示", "至少需要一条买入规则")
            return
        self.strategy = stg.Strategy(
            name=name, desc=self.desc_edit.text().strip(),
            buy_rules=buy_rules, sell_rules=sell_rules,
            stop_loss_pct=float(self.stop_spin.value()),
            take_profit_pct=float(self.tp_spin.value()),
            max_hold_days=int(self.hold_spin.value()))
        self.accept()


def _history_to_signal(h: dict):
    try:
        return TradeSignal(
            time=h.get("time", ""), code=h.get("code", ""), name=h.get("name", ""),
            strategy=h.get("strategy", ""), side=h.get("side", "buy"),
            price=h.get("price"), stop_price=h.get("stop"),
            target_price=h.get("target"), risk_score=float(h.get("risk") or 0),
            hit_rules=[], reason=h.get("reason", ""),
            sparkline=list(h.get("spark") or []),
            op_score=float(h.get("score") or 0), grade=str(h.get("grade") or ""))
    except Exception:
        return None


class MonitorPage(QWidget):
    open_stock = Signal(str, str)
    ask_ai = Signal(str, str)
    signals_found = Signal(list)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.strategies: list = []
        self._scanning = False
        self._view_signals: list = []
        self._build_ui()
        self._load_strategies()
        self._apply_timer()

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(11, 11, 11, 11)
        lay.setSpacing(10)
        lay.addWidget(kit.page_head(
            "策略监听", "多策略并跑 · 实时信号卡片流 · 盘后自动日报"))

        bar = QFrame()
        bar.setProperty("card", True)
        b = QHBoxLayout(bar)
        b.setContentsMargins(14, 10, 14, 10)
        self.auto_check = QCheckBox("交易时段自动扫描全市场")
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 60)
        self.interval_spin.setSuffix(" 分钟")
        # v7.1 §12.2 顶部状态条：● 运行中 / 范围 / 周期 / 上次扫描 / 今日信号
        self.monitor_stat = QLabel("● 待启动　范围：全市场　周期：5 分钟　上次扫描：—")
        self.monitor_stat.setProperty("sub", True)
        scan_btn = QPushButton("立即扫描")
        scan_btn.clicked.connect(self.scan_now)
        hist_btn = QPushButton("历史")
        hist_btn.setProperty("secondary", True)
        hist_btn.clicked.connect(self.show_history)
        export_btn = QPushButton("导出")
        export_btn.setProperty("secondary", True)
        export_btn.clicked.connect(self._export)
        self.view_btn = QPushButton("表格视图")
        self.view_btn.setProperty("secondary", True)
        self.view_btn.setCheckable(True)
        self.view_btn.toggled.connect(self._toggle_view)
        b.addWidget(self.auto_check)
        b.addWidget(self.interval_spin)
        b.addWidget(scan_btn)
        b.addWidget(hist_btn)
        b.addWidget(export_btn)
        b.addWidget(self.view_btn)
        b.addStretch(1)
        self.scan_status = QLabel(" ")
        self.scan_status.setProperty("hint", True)
        b.addWidget(self.monitor_stat, 1)
        b.addWidget(self.scan_status)
        lay.addWidget(bar)

        self.tabs = QTabWidget()
        # --- 交易信号（卡片流 / 表格 双视图） ---
        from ..signal_card import OpportunityFlow
        self.flow = OpportunityFlow()
        self.flow.open_stock.connect(self.open_stock)
        self.flow.ask_ai.connect(self._ask_ai_signal)
        self.flow.add_watch.connect(
            lambda code, name: self._log(f"已加入自选: {name}({code})"))
        self.sig_table = QTableWidget(0, len(_SIGNAL_COLS))
        self.sig_table.setHorizontalHeaderLabels(_SIGNAL_COLS)
        self.sig_table.verticalHeader().hide()
        self.sig_table.setAlternatingRowColors(True)
        self.sig_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.sig_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.sig_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.sig_table.horizontalHeader().setStretchLastSection(True)
        self.sig_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sig_table.customContextMenuRequested.connect(self._signal_menu)
        self.sig_table.itemDoubleClicked.connect(
            lambda item: self.open_stock.emit(
                self.sig_table.item(item.row(), 0).text(),
                self.sig_table.item(item.row(), 2).text()))
        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.flow)      # 0 卡片（默认）
        self.view_stack.addWidget(self.sig_table)  # 1 表格
        self.tabs.addTab(self.view_stack, "交易信号")
        # --- 策略库 ---
        stg_widget = QWidget()
        sl = QHBoxLayout(stg_widget)
        sl.setContentsMargins(8, 8, 8, 8)
        left = QVBoxLayout()
        left.addWidget(QLabel("内置模板可直接使用；编辑内置策略会另存副本"))
        self.stg_list = QListWidget()
        self.stg_list.itemDoubleClicked.connect(lambda _: self.edit_strategy())
        left.addWidget(self.stg_list, 1)
        btns = QHBoxLayout()
        add_btn = QPushButton("新建")
        add_btn.clicked.connect(self.new_strategy)
        edit_btn = QPushButton("编辑/复制")
        edit_btn.setProperty("secondary", True)
        edit_btn.clicked.connect(self.edit_strategy)
        del_btn = QPushButton("删除")
        del_btn.setProperty("danger", True)
        del_btn.clicked.connect(self.del_strategy)
        imp_stg_btn = QPushButton("导入策略")
        imp_stg_btn.setProperty("secondary", True)
        imp_stg_btn.clicked.connect(self._import_strategies)
        exp_stg_btn = QPushButton("导出策略")
        exp_stg_btn.setProperty("secondary", True)
        exp_stg_btn.clicked.connect(self._export_strategies)
        tpl_stg_btn = QPushButton("模板")
        tpl_stg_btn.setProperty("secondary", True)
        tpl_stg_btn.setToolTip("下载策略导入模板（JSON：buy_rules/sell_rules 中文指标示例）")
        tpl_stg_btn.clicked.connect(self._save_strategy_template)
        btns.addWidget(add_btn)
        btns.addWidget(edit_btn)
        btns.addWidget(del_btn)
        btns.addWidget(imp_stg_btn)
        btns.addWidget(exp_stg_btn)
        btns.addWidget(tpl_stg_btn)
        left.addLayout(btns)
        sl.addLayout(left, 1)
        help_card = QFrame()
        help_card.setProperty("card", True)
        hv = QVBoxLayout(help_card)
        hv.setContentsMargins(14, 12, 14, 12)
        hv.addWidget(QLabel("内置策略（盈利导向）"))
        for name, desc in (("趋势启动", "均线多头+MACD金叉，顺势买入"),
                           ("放量突破", "创20日新高且放量，突破追入"),
                           ("回调企稳", "回踩上升MA20缩量企稳，低吸"),
                           ("超卖反弹", "RSI超卖后企稳反抽，博反弹")):
            hv.addWidget(QLabel(f"• <b>{name}</b> — {desc}"))
        hv.addSpacing(8)
        hv.addWidget(QLabel("风控口径"))
        hv.addWidget(QLabel("止损/止盈同时用于回测与卖出跟踪；"
                            "新信号自动写入历史并触发推送（若已启用）。"
                            "持仓管理页录入的持仓会被自动跟踪卖出条件。"))
        hv.addStretch(1)
        sl.addWidget(help_card)
        self.tabs.addTab(stg_widget, "策略库")
        lay.addWidget(self.tabs, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        self.log.setPlaceholderText("扫描与推送日志…")
        lay.addWidget(self.log)

    # ------------------------------------------------------------ 策略
    def _load_strategies(self) -> None:
        builtins = stg.builtin_strategies()
        self.strategies = list(builtins)
        for d in self.ctx.cfg.get_strategy_dicts():
            try:
                self.strategies.append(stg.Strategy.from_dict(d))
            except Exception as exc:  # noqa: BLE001
                self._log(f"策略配置解析失败: {exc}")
        self.stg_list.clear()
        for i, s in enumerate(self.strategies):
            tag = "内置" if i < len(builtins) else "自定义"
            QListWidgetItem(f"[{tag}] {s.name} — {s.desc}", self.stg_list)
            item = self.stg_list.item(self.stg_list.count() - 1)
            item.setData(Qt.UserRole, s)

    def _current_strategy(self):
        item = self.stg_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def new_strategy(self) -> None:
        dlg = StrategyDialog(self, stg.Strategy(name="我的策略"))
        if dlg.exec():
            self.ctx.cfg.save_strategy_dict(dlg.strategy.to_dict())
            self._load_strategies()

    def edit_strategy(self) -> None:
        s = self._current_strategy()
        if not s:
            return
        builtin_count = len(stg.builtin_strategies())
        is_builtin = self.strategies.index(s) < builtin_count
        copy = stg.Strategy.from_dict(s.to_dict())
        if is_builtin:
            copy.name = s.name + "-副本"
        dlg = StrategyDialog(self, copy)
        if dlg.exec():
            self.ctx.cfg.save_strategy_dict(dlg.strategy.to_dict())
            self._load_strategies()

    def _save_strategy_template(self) -> None:
        """v5.2.1：下载策略导入模板。"""
        from ..transfer import save_template
        save_template(self, "strategies")

    def _import_strategies(self) -> None:
        from ..transfer import import_strategies
        ok, msg = import_strategies(self, self.ctx.cfg)
        QMessageBox.information(self, "导入策略", msg)
        if ok:
            self._load_strategies()

    def _export_strategies(self) -> None:
        from ..transfer import export_strategies
        _, msg = export_strategies(self, self.ctx.cfg)
        QMessageBox.information(self, "导出策略", msg)

    def del_strategy(self) -> None:
        s = self._current_strategy()
        if not s:
            return
        if self.strategies.index(s) < len(stg.builtin_strategies()):
            QMessageBox.information(self, "提示", "内置策略不可删除，可「编辑/复制」后得到可删改的副本")
            return
        self.ctx.cfg.remove_strategy(s.name)
        self._load_strategies()

    # ------------------------------------------------------------ 扫描
    def scan_now(self) -> None:
        if self._scanning:
            self._log("已有扫描在进行中…")
            return
        self._scanning = True
        self.scan_status.setText("扫描中…")
        submit(self._do_scan, on_done=self._on_scan_done,
               on_err=self._on_scan_err, on_progress=self.scan_status.setText,
               with_progress=True)

    def _do_scan(self, on_progress=None):
        fresh = self.ctx.monitor.scan_once(self.strategies, on_progress=on_progress)
        logs = notify_mod.notify_signals(self.ctx.cfg.notify, fresh)
        return fresh, logs

    # _on_scan_done 定义于盘后日报段（统一处理信号视图 + 分组推送）

    # ------------------------------------------------------------ 视图
    def _set_view_signals(self, signals: list) -> None:
        """统一入口：更新卡片流与表格两种视图的数据。"""
        self._view_signals = list(signals)
        self.flow.set_signals(self._view_signals)
        self._append_signals(self._view_signals, clear=True)

    def _toggle_view(self, checked: bool) -> None:
        self.view_btn.setText("卡片视图" if checked else "表格视图")
        self.view_stack.setCurrentIndex(1 if checked else 0)

    def _ask_ai_signal(self, sig) -> None:
        from ...core import prompt
        self._log("AI 解读已在「AI 分析」页输出")
        self.ask_ai.emit(prompt.build_signal_context(sig),
                         f"信号解读 - {sig.name}")

    def show_history(self) -> None:
        hist = self.ctx.cfg.monitor.get("history") or []
        signals = [h for h in (_history_to_signal(x) for x in hist[:200]) if h]
        self._set_view_signals(signals)
        self._log(f"已载入 {len(signals)} 条历史信号，正在计算发出以来涨跌…")
        codes = list(dict.fromkeys(s.code for s in signals))[:50]
        if codes:
            submit(self._load_tracking, codes, on_done=self._on_tracking,
                   on_err=lambda m: None)

    def _load_tracking(self, codes):
        return self.ctx.get_quotes(codes)

    def _on_tracking(self, quotes: dict) -> None:
        """信号发出价 → 现价 的累计涨跌。"""
        def fn(sig):
            q = quotes.get(sig.code)
            if q and q.price and sig.price:
                return (q.price / sig.price - 1) * 100
            return None
        self.flow.set_tracking_fn(fn)

    def _on_scan_err(self, msg: str) -> None:
        self._scanning = False
        self.scan_status.setText("扫描失败")
        self._log(f"扫描失败: {msg}")

    def _auto_scan(self) -> None:
        if not self.auto_check.isChecked():
            return
        if not is_trade_time():
            self.scan_status.setText("非交易时段，自动扫描暂停（可手动「立即扫描」）")
            return
        self.scan_now()

    def _apply_timer(self) -> None:
        mon = self.ctx.cfg.monitor
        self.auto_check.setChecked(bool(mon.get("auto_scan")))
        self.interval_spin.setValue(int(mon.get("interval_min") or 5))
        # 定时器只连一次（v7.2.2 修复：此前每次保存设置重复 connect，
        # 保存 N 次后一个周期触发 N 次扫描——按钮点击后行为异常的直接根因）
        if not hasattr(self, "timer") or self.timer is None:
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._auto_scan)
        self.timer.start(self.interval_spin.value() * 60 * 1000)
        # 盘后日报调度：每交易日 15:08 全策略扫描 + 分组推送（Sequoia crontab 等价）
        if not hasattr(self, "eod_timer") or self.eod_timer is None:
            self.eod_timer = QTimer(self)
            self.eod_timer.timeout.connect(self._eod_check)
        self.eod_timer.start(5 * 60 * 1000)   # 每 5 分钟检查是否到点
        try:
            if self.auto_check.receivers(SIGNAL("toggled(bool)")):
                self.auto_check.toggled.disconnect(self._save_scan_cfg)
            if self.interval_spin.receivers(SIGNAL("valueChanged(int)")):
                self.interval_spin.valueChanged.disconnect(self._save_scan_cfg)
        except (RuntimeError, TypeError):
            pass
        self.auto_check.toggled.connect(self._save_scan_cfg)
        self.interval_spin.valueChanged.connect(self._save_scan_cfg)

    def _eod_check(self) -> None:
        """15:08 触发一次盘后全策略扫描，结果按策略分组推送日报。"""
        from datetime import datetime
        now = datetime.now()
        if now.weekday() >= 5 or now.hour != 15 or now.minute < 5:
            return
        key = f"eod_done_{now.strftime('%Y%m%d')}"
        mon = self.ctx.cfg.monitor
        if mon.get(key):
            return
        mon[key] = True
        self.ctx.cfg.save()
        # 扫描走 kline_for_scan（缓存新鲜度守护）：首轮在线回填当日K线，
        # 之后（含同日手动重扫/次日盘后）直接命中缓存秒级完成。
        self._log("盘后自动日报扫描启动（全策略，首轮含K线增量回填）…")
        self.scan_now()

    # 盘后日报推送（重写 _on_scan_done 附带分组文案）
    def _on_scan_done(self, result) -> None:
        self._scanning = False
        fresh, logs = result
        for line in logs:
            self._log(line)
        self._today_signals = getattr(self, "_today_signals", 0) + len(fresh)
        running = "● 自动监听运行中" if self.auto_check.isChecked() else "○ 未启用自动扫描"
        period = f"周期：{self.interval_spin.value()} 分钟"
        last = f"上次扫描：{datetime.now().strftime('%H:%M:%S')}"
        self.monitor_stat.setText(
            f"{running}　范围：全市场　{period}　{last}　今日信号：{self._today_signals}")
        if fresh:
            self.scan_status.setText(f"发现 {len(fresh)} 条新信号")
            self._log(f"新信号 {len(fresh)} 条")
            self.signals_found.emit(fresh)
            self._set_view_signals(fresh)
            # 盘后：按策略分组推送日报
            self._push_eod_report(fresh)
        else:
            self.scan_status.setText("扫描完成，暂无新信号")

    def _push_eod_report(self, fresh: list) -> None:
        """盘后机会日报：按策略分组，一条 webhook 消息送达。"""
        from datetime import datetime
        groups: dict = {}
        for s in fresh:
            groups.setdefault(s.strategy, []).append(
                f"{s.name}({s.code}) 现价{s.price}")
        lines = [f"【盘后机会日报 {datetime.now():%Y-%m-%d}】"]
        for stg_name, items in groups.items():
            lines.append(f"▎{stg_name}（{len(items)}只）：" +
                         "；".join(items[:5]) + ("…" if len(items) > 5 else ""))
        lines.append("—— 策略仅筛形态，买卖自决 ——")
        text = chr(10).join(lines)
        from ...core import notify as notify_mod
        cfg_notify = self.ctx.cfg.notify
        for chan, fn in (("feishu_webhook", notify_mod.send_feishu),
                         ("ding_webhook", notify_mod.send_dingtalk),
                         ("wecom_webhook", notify_mod.send_wecom)):
            url = cfg_notify.get(chan)
            if url:
                try:
                    ok, msg = fn(url, "" if chan != "ding_webhook" else
                                 cfg_notify.get("ding_secret") or "", text)
                    self._log(f"日报推送 {chan}: {'成功' if ok else '失败 ' + msg[:40]}")
                except Exception as exc:  # noqa: BLE001
                    self._log(f"日报推送 {chan} 异常: {exc}")

    def _save_scan_cfg(self, *_):
        mon = self.ctx.cfg.monitor
        mon["auto_scan"] = self.auto_check.isChecked()
        mon["interval_min"] = self.interval_spin.value()
        self.ctx.cfg.save()
        if getattr(self, "timer", None) is not None:
            self.timer.start(self.interval_spin.value() * 60 * 1000)

    # ------------------------------------------------------------ 信号表
    def _append_signals(self, signals: list, clear: bool) -> None:
        if clear:
            self.sig_table.setRowCount(0)
        start = self.sig_table.rowCount()
        self.sig_table.setRowCount(start + len(signals))
        for i, s in enumerate(signals):
            r = start + i
            vals = [s.time, s.code, s.name, s.strategy,
                    "买入" if s.side == "buy" else "卖出",
                    f"{s.price:.2f}" if s.price is not None else "—",
                    f"{s.stop_price:.2f}" if s.stop_price is not None else "—",
                    f"{s.target_price:.2f}" if s.target_price is not None else "—",
                    f"{s.risk_score:.0f}", s.reason]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                item.setData(Qt.UserRole, s)
                if c == 4:
                    item.setForeground(QColor(UP if s.side == "buy" else DOWN))
                if c in (5, 6, 7, 8):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.sig_table.setItem(r, c, item)

    def show_history(self) -> None:
        hist = self.ctx.cfg.monitor.get("history") or []
        signals = [h for h in (_history_to_signal(x) for x in hist[:200]) if h]
        self._set_view_signals(signals)
        self._log(f"已载入 {len(signals)} 条历史信号，正在计算发出以来涨跌…")
        codes = list(dict.fromkeys(s.code for s in signals))[:50]
        if codes:
            submit(self._load_tracking, codes, on_done=self._on_tracking,
                   on_err=lambda m: None)

    def _signal_menu(self, pos) -> None:
        row = self.sig_table.currentRow()
        if row < 0:
            return
        sig: TradeSignal = self.sig_table.item(row, 0).data(Qt.UserRole)
        if sig is None:
            return
        menu = QMenu(self)
        act_detail = menu.addAction("打开K线详情")
        act_ai = menu.addAction("AI 解读该信号")
        act_watch = menu.addAction("加入自选")
        act = menu.exec(self.sig_table.viewport().mapToGlobal(pos))
        if act == act_detail:
            self.open_stock.emit(sig.code, sig.name)
        elif act == act_ai:
            from ...core import prompt
            self._log("AI 解读已在「AI 分析」页输出")
            self.ask_ai.emit(prompt.build_signal_context(sig),
                             f"信号解读 - {sig.name}")
        elif act == act_watch:
            self.ctx.cfg.add_watch(sig.code, sig.name)
            self._log(f"已加入自选: {sig.name}({sig.code})")

    def _export(self) -> None:
        from ...core.export_csv import rows_to_csv
        from PySide6.QtWidgets import QFileDialog

        headers = _SIGNAL_COLS
        keys = ["time", "code", "name", "strategy", "side", "price",
                "stop_price", "target_price", "risk_score", "reason"]
        rows = []
        for r in range(self.sig_table.rowCount()):
            sig: TradeSignal = self.sig_table.item(r, 0).data(Qt.UserRole)
            if sig is None:
                continue
            d = dict(zip(keys, [getattr(sig, k) for k in keys]))
            d["side"] = "买入" if sig.side == "buy" else "卖出"
            rows.append({c: d[k] for c, k in zip(headers, keys)})
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", "交易信号.csv", "CSV (*.csv)")
        if not path:
            return
        rows_to_csv(path, headers, rows)
        self._log(f"已导出 {len(rows)} 条信号到 {path}")

    def _log(self, text: str) -> None:
        self.log.appendPlainText(f"[{datetime.now():%H:%M:%S}] {text}")

    def apply_settings(self) -> None:
        self._apply_timer()
