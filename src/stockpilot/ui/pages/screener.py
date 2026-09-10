"""机会雷达页 v1.4：策略找机会（默认）+ 条件筛选（次级）。

Tab1 策略找机会：策略多选 + 一键全市场执行 → 机会卡片流（按机会分降序）。
Tab2 条件筛选：v1.2 的条件表单 + 方案 + 表格 + 导出。
"""
from __future__ import annotations

from PySide6.QtCore import SIGNAL, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QMessageBox, QPushButton, QSpinBox,
                               QTabWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ...core import strategy as stg
from ...core.monitor import run_strategy_scan
from ...core.screener import filter_numeric
from .. import kit
from ..ai_stream import AiStreamPanel
from ..signal_card import OpportunityFlow
from ..theme import SUB
from ..workers import submit
from .market import card_frame, num_item, pct_item

_COND_DEFS = [
    ("price", "现价", 0, 0, 1),
    ("change_pct", "涨跌幅%", 0, 0, 0.5),
    ("turnover_rate", "换手率%", 3, 15, 0.5),
    ("volume_ratio", "量比", 0, 0, 0.1),
    ("float_mv", "流通市值(亿)", 30, 300, 5),
    ("pe", "市盈率", 0, 0, 1),
    ("pb", "市净率", 0, 0, 0.1),
    ("amount", "成交额(万)", 0, 0, 1000),
]

_COLS = ["代码", "名称", "现价", "涨跌幅", "换手%", "量比", "PE",
         "PB", "流通市值(亿)", "成交额(万)"]


class CondRow(QHBoxLayout):
    def __init__(self, key: str, label: str, lo: float, hi: float, step: float):
        super().__init__()
        self.key = key
        self.box = QCheckBox(label)
        self.min_edit = QDoubleSpinBox()
        self.max_edit = QDoubleSpinBox()
        for sp, val in ((self.min_edit, lo), (self.max_edit, hi)):
            sp.setRange(-1000000, 1000000)
            sp.setDecimals(2)
            sp.setSingleStep(step)
            sp.setValue(val)
            sp.setFixedWidth(88)
        self.addWidget(self.box)
        self.addWidget(self.min_edit)
        self.addWidget(QLabel("~"))
        self.addWidget(self.max_edit)
        self.addStretch(1)

    def collect(self):
        if not self.box.isChecked():
            return None, None
        return (float(self.min_edit.value()), float(self.max_edit.value()))


class ScreenerPage(QWidget):
    """机会雷达：进页即见「策略找机会」。"""

    ask_ai = Signal(str, str)
    open_stock = Signal(str, str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.rows: list = []
        self._signals: list = []
        self._radar_running = False
        self._build_ui()
        self._load_strategies()
        self._load_plans()

    # ================================================================ UI
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(11, 11, 11, 11)
        lay.setSpacing(10)

        lay.addWidget(kit.page_head(
            "机会雷达", "策略找机会 · 多策略并集扫描 · 条件筛选"))
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_radar_tab(), "策略找机会")
        self.tabs.addTab(self._build_filter_tab(), "条件筛选")
        lay.addWidget(self.tabs, 1)

    # ---------------- Tab1：策略找机会（默认） ----------------
    def _build_radar_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(10)

        # 策略多选卡（可折叠，默认展开一次后自动折叠提示）
        stg_card = QFrame()
        stg_card.setProperty("card", True)
        sv = QVBoxLayout(stg_card)
        sv.setContentsMargins(10, 6, 10, 6)
        head = QHBoxLayout()
        from PySide6.QtWidgets import QToolButton
        self.fold_btn = QToolButton()
        self.fold_btn.setText("▼ 收起策略选择")
        self.fold_btn.setCheckable(True)
        self.fold_btn.setStyleSheet("color:#7890AA;background:transparent;border:none;padding:2px;")
        head.addWidget(self.fold_btn)
        head.addStretch(1)
        all_btn = QPushButton("全选")
        all_btn.setProperty("secondary", True)
        all_btn.setFixedHeight(26)
        all_btn.clicked.connect(lambda: self._set_all_strategies(True))
        none_btn = QPushButton("全不选")
        none_btn.setProperty("secondary", True)
        none_btn.setFixedHeight(26)
        none_btn.clicked.connect(lambda: self._set_all_strategies(False))
        head.addWidget(all_btn)
        head.addWidget(none_btn)
        sv.addLayout(head)
        hint = QLabel("勾选多个策略输出机会并集，按机会分降序；收起此区可把空间留给机会与AI")
        hint.setProperty("hint", True)
        sv.addWidget(hint)
        self.stg_checks_frame = QFrame()
        self.stg_checks_lay = QGridLayout(self.stg_checks_frame)
        self.stg_checks_lay.setContentsMargins(0, 6, 0, 0)
        self.stg_checks_lay.setHorizontalSpacing(18)
        self.stg_checks_lay.setVerticalSpacing(4)
        sv.addWidget(self.stg_checks_frame)
        # 折叠逻辑：切换隐藏 frame 与提示
        self._stg_area = [hint, self.stg_checks_frame]
        self.fold_btn.toggled.connect(self._fold_strategies)
        v.addWidget(stg_card)

        # 执行条
        bar_card = card_frame()
        bar = QHBoxLayout(bar_card)
        bar.setContentsMargins(14, 8, 14, 8)
        bar.addWidget(QLabel("扫描范围(按成交额):"))
        self.radar_max = QSpinBox()
        self.radar_max.setRange(500, 5500)
        self.radar_max.setSingleStep(500)
        self.radar_max.setValue(2000)
        bar.addWidget(self.radar_max)
        bar.addStretch(1)
        self.radar_status = QLabel(" ")
        self.radar_status.setProperty("hint", True)
        bar.addWidget(self.radar_status, 1)
        self.radar_btn = QPushButton("开始找机会")
        self.radar_btn.setMinimumWidth(130)
        self.radar_btn.setToolTip("对全市场执行所选策略的买入规则，输出机会卡片（按机会分降序）")
        self.radar_btn.clicked.connect(self.run_radar)
        bar.addWidget(self.radar_btn)
        v.addWidget(bar_card)

        # 机会卡片流 + 右侧就地 AI 面板
        split = QHBoxLayout()
        flow_card = card_frame()
        f_lay = QVBoxLayout(flow_card)
        f_lay.setContentsMargins(8, 8, 8, 8)
        self.flow = OpportunityFlow()
        f_lay.addWidget(self.flow)
        self.flow.open_stock.connect(self.open_stock)
        self.flow.add_watch.connect(self._watch_from_flow)
        split.addWidget(flow_card, 1)

        from ..ai_side_panel import AiSidePanel
        self.ai_side = AiSidePanel(self.ctx, "💡 就地 AI 解读")
        self.ai_side.setFixedWidth(420)
        self.flow.ask_ai.connect(self.ai_side.analyze_signal)
        split.addWidget(self.ai_side)
        v.addLayout(split, 1)
        return w

    # ---------------- Tab2：条件筛选 ----------------
    def _build_filter_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(10)

        cond_card = QGroupBox("筛选条件（勾选生效）")
        grid = QGridLayout()
        grid.setVerticalSpacing(6)
        self.cond_rows = []
        for i, (key, label, lo, hi, step) in enumerate(_COND_DEFS):
            row = CondRow(key, label, lo, hi, step)
            self.cond_rows.append(row)
            grid.addLayout(row, i // 3, i % 3)
        cond_card.setLayout(grid)
        v.addWidget(cond_card)

        tech_card = card_frame()
        bar = QHBoxLayout(tech_card)
        bar.setContentsMargins(14, 8, 14, 8)
        self.tech_bull = QCheckBox("均线多头")
        self.tech_macd = QCheckBox("MACD金叉")
        self.tech_rsi = QCheckBox("RSI6未超买")
        bar.addWidget(QLabel("技术形态:"))
        bar.addWidget(self.tech_bull)
        bar.addWidget(self.tech_macd)
        bar.addWidget(self.tech_rsi)
        bar.addStretch(1)
        bar.addWidget(QLabel("方案:"))
        self.plan_combo = QComboBox()
        self.plan_combo.setMinimumWidth(110)
        bar.addWidget(self.plan_combo)
        save_btn = QPushButton("保存方案")
        save_btn.setProperty("secondary", True)
        save_btn.clicked.connect(self._save_plan)
        del_btn = QPushButton("删除方案")
        del_btn.setProperty("secondary", True)
        del_btn.clicked.connect(self._del_plan)
        imp_plan_btn = QPushButton("📥 导入方案")
        imp_plan_btn.setProperty("secondary", True)
        imp_plan_btn.clicked.connect(self._import_plans)
        exp_plan_btn = QPushButton("📤 导出方案")
        exp_plan_btn.setProperty("secondary", True)
        exp_plan_btn.clicked.connect(self._export_plans)
        tpl_plan_btn = QPushButton("模板")
        tpl_plan_btn.setProperty("secondary", True)
        tpl_plan_btn.setToolTip("下载选股方案导入模板（JSON：cond 条件键与 tech 策略名单示例）")
        tpl_plan_btn.clicked.connect(self._save_plan_template)
        bar.addWidget(save_btn)
        bar.addWidget(del_btn)
        bar.addWidget(imp_plan_btn)
        bar.addWidget(exp_plan_btn)
        bar.addWidget(tpl_plan_btn)
        bar.addWidget(QLabel("范围:"))
        self.max_count = QSpinBox()
        self.max_count.setRange(500, 5500)
        self.max_count.setSingleStep(500)
        self.max_count.setValue(2000)
        bar.addWidget(self.max_count)
        self.run_btn = QPushButton("开始选股")
        self.run_btn.clicked.connect(self.run_filter)
        bar.addWidget(self.run_btn)
        v.addWidget(tech_card)

        bar2 = card_frame()
        b2 = QHBoxLayout(bar2)
        b2.setContentsMargins(14, 8, 14, 8)
        export_btn = QPushButton("导出结果 CSV")
        export_btn.setProperty("secondary", True)
        export_btn.clicked.connect(self._export)
        watch_btn = QPushButton("选中行加入自选")
        watch_btn.setProperty("secondary", True)
        watch_btn.clicked.connect(self._watch_selected)
        b2.addWidget(export_btn)
        b2.addWidget(watch_btn)
        b2.addStretch(1)
        self.progress = QLabel(" ")
        self.progress.setProperty("hint", True)
        b2.addWidget(self.progress, 1)
        v.addWidget(bar2)

        self.table = QTableWidget(0, len(_COLS))
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemDoubleClicked.connect(self._open_detail)
        v.addWidget(self.table, 2)

        self.ai_panel = AiStreamPanel(self.ctx, "AI 选股解读")
        v.addWidget(self.ai_panel, 1)
        return w

    # ================================================================ 策略
    def _load_strategies(self) -> None:
        self.all_strategies = self._all_strategies()
        # 清空旧 checkbox
        while self.stg_checks_lay.count():
            item = self.stg_checks_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.stg_checks: dict = {}
        for i, s in enumerate(self.all_strategies):
            cb = QCheckBox(f"{s.name}　· {s.desc}")
            cb.setChecked(True)
            cb.setStyleSheet(f"color:{SUB}; font-weight:normal;")
            self.stg_checks[s.name] = cb
            self.stg_checks_lay.addWidget(cb, i // 2, i % 2)

    def _all_strategies(self) -> list:
        builtins = stg.builtin_strategies()
        custom = []
        for d in self.ctx.cfg.get_strategy_dicts():
            try:
                custom.append(stg.Strategy.from_dict(d))
            except Exception:  # noqa: BLE001
                pass
        return builtins + custom

    def _fold_strategies(self, folded: bool) -> None:
        self.fold_btn.setText(("▶ 展开策略选择（15套）") if folded
                              else "▼ 收起策略选择")
        for w in self._stg_area:
            w.setVisible(not folded)

    def _set_all_strategies(self, checked: bool) -> None:
        for cb in self.stg_checks.values():
            cb.setChecked(checked)

    def _selected_strategies(self) -> list:
        sel = [s for s in self.all_strategies
               if self.stg_checks.get(s.name) and
               self.stg_checks[s.name].isChecked()]
        # 同步到全局机会配置（首页随之生效）
        self.ctx.cfg.opportunity["strategies"] = [s.name for s in sel]
        self.ctx.cfg.save()
        return sel

    # ================================================================ 找机会
    def run_radar(self) -> None:
        if self._radar_running:
            return
        selected = self._selected_strategies()
        if not selected:
            QMessageBox.information(self, "提示", "请至少勾选一个策略")
            return
        self._radar_running = True
        self.radar_btn.setEnabled(False)
        self.radar_status.setText("启动中…")
        submit(self._do_radar, selected, int(self.radar_max.value()),
               on_done=self._on_radar_done, on_err=self._on_radar_err,
               on_progress=self.radar_status.setText, with_progress=True)

    def _do_radar(self, strategies, max_count, on_progress=None):
        rows = self.ctx._market_fetch(max_count, on_progress=on_progress)
        names = {s.name for s in strategies}
        seq = [s for s in strategies if s.name != "RPS强度突破"]
        signals = run_strategy_scan(seq, rows, self.ctx.kline_for_scan,
                                    on_progress,
                                    opp_cfg=self.ctx.cfg.opportunity) if seq else []
        # RPS：全市场横截面（缓存优先）
        if "RPS强度突破" in names:
            if on_progress:
                on_progress("RPS 横截面计算（120日强度排名）…")
            from ...core.rps import rps_breakout
            from ...core.models import TradeSignal
            market = {}
            for r in rows:
                code = r["code"]
                kls = self.ctx.kline_for_scan(code, 130)
                if len(kls) >= 121:
                    market[code] = kls
            hits = rps_breakout(market, period=120, rps_min=90)
            from datetime import datetime
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            name_by_code = {r["code"]: r.get("name") or "" for r in rows}
            for h in hits:
                code = h["code"]
                sig = TradeSignal(
                    time=now, code=code, name=name_by_code.get(code, code),
                    strategy="RPS强度突破", side="buy", price=h["close"],
                    stop_price=h["close"] * 0.94,
                    target_price=h["close"] * 1.12,
                    risk_score=30, hit_rules=[f"RPS {h['rps']:.0f}",
                                             f"120日涨 {h['pct_change']:+.0f}%"],
                    reason=f"120日涨幅全市场排名前{100 - h['rps']:.0f}%"
                            f"（RPS {h['rps']:.0f}）且贴近区间高点")
                sig.op_score, sig.grade = h["rps"], "A" if h["rps"] >= 95 else "B"
                sig.sparkline = [k.close for k in market[code][-30:]]
                signals.append(sig)
        signals.sort(key=lambda s: s.op_score, reverse=True)
        return signals

    def _on_radar_done(self, signals: list) -> None:
        self._radar_running = False
        self.radar_btn.setEnabled(True)
        self._signals = signals or []
        self.flow.set_signals(self._signals)
        a_grade = sum(1 for s in self._signals if s.grade == "A")
        b_grade = sum(1 for s in self._signals if s.grade == "B")
        self.radar_status.setText(
            f"找到 {len(self._signals)} 个机会 · A级 {a_grade} · B级 {b_grade}"
            if self._signals else
            "本次未发现符合所选策略的机会（可增加范围或换策略）")

    def _watch_from_flow(self, code: str, name: str) -> None:
        added = self.ctx.cfg.add_watch(code, name)
        self.radar_status.setText(
            f"已加入自选: {name}({code})" if added else f"{name}({code}) 已在自选中")

    def _on_radar_err(self, msg: str) -> None:
        self._radar_running = False
        self.radar_btn.setEnabled(True)
        from ..err import fail_hint, show_error
        show_error(self, "雷达扫描失败", msg)
        fail_hint(self.radar_status, "扫描失败，详见弹窗")

    # ================================================================ 条件筛选
    def _load_plans(self) -> None:
        self.plan_combo.clear()
        self.plan_combo.addItem("（选择方案）")
        for p in self.ctx.cfg.get_plans():
            self.plan_combo.addItem(p["name"])
        try:
            if self.plan_combo.receivers(SIGNAL("currentIndexChanged(int)")):
                self.plan_combo.currentIndexChanged.disconnect(self._on_plan)
        except (RuntimeError, TypeError):
            pass
        self.plan_combo.currentIndexChanged.connect(self._on_plan)

    def _on_plan(self, idx: int) -> None:
        if idx <= 0:
            return
        plans = {p["name"]: p for p in self.ctx.cfg.get_plans()}
        plan = plans.get(self.plan_combo.currentText())
        if not plan:
            return
        cond = plan.get("cond") or {}
        for row in self.cond_rows:
            lo = cond.get(f"{row.key}_min")
            hi = cond.get(f"{row.key}_max")
            if lo is not None or hi is not None:
                row.box.setChecked(True)
                if lo is not None:
                    row.min_edit.setValue(float(lo))
                if hi is not None:
                    row.max_edit.setValue(float(hi))
            else:
                row.box.setChecked(False)
        rules = [stg.Rule.from_dict(r) for r in (plan.get("tech") or [])]
        self.tech_bull.setChecked(any(r.indicator == "ma_bull" for r in rules))
        self.tech_macd.setChecked(
            any(r.indicator == "macd_golden" for r in rules))
        self.tech_rsi.setChecked(any(r.indicator == "rsi6" for r in rules))
        self.progress.setText(f"已加载方案「{plan['name']}」")

    def _save_plan(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "保存方案", "方案名称：")
        if not ok or not name.strip():
            return
        self.ctx.cfg.save_plan(name.strip(), self._collect_cond(),
                               [r.to_dict() for r in self._collect_tech()])
        self._load_plans()
        self.plan_combo.setCurrentText(name.strip())

    def _save_plan_template(self) -> None:
        """v5.2.1：下载方案导入模板。"""
        from ..transfer import save_template
        save_template(self, "screen_plans")

    def _import_plans(self) -> None:
        from ..transfer import import_screen_plans
        ok, msg = import_screen_plans(self, self.ctx.cfg)
        self.progress.setText(msg)
        if ok:
            self._load_plans()

    def _export_plans(self) -> None:
        from ..transfer import export_screen_plans
        _, msg = export_screen_plans(self, self.ctx.cfg)
        self.progress.setText(msg)

    def _del_plan(self) -> None:
        name = self.plan_combo.currentText()
        if name.startswith("（"):
            return
        self.ctx.cfg.remove_plan(name)
        self._load_plans()

    def _collect_cond(self) -> dict:
        cond = {}
        for row in self.cond_rows:
            lo, hi = row.collect()
            if lo is not None:
                cond[f"{row.key}_min"] = lo
                cond[f"{row.key}_max"] = hi
        return cond

    def _collect_tech(self) -> list:
        rules = []
        if self.tech_bull.isChecked():
            rules.append(stg.Rule("ma_bull", "==", True))
        if self.tech_macd.isChecked():
            rules.append(stg.Rule("macd_golden", "==", True))
        if self.tech_rsi.isChecked():
            rules.append(stg.Rule("rsi6", "<=", 70))
        return rules

    def run_filter(self) -> None:
        cond = self._collect_cond()
        tech = self._collect_tech()
        if not cond and not tech:
            QMessageBox.information(self, "提示", "请至少勾选一个筛选条件")
            return
        self.run_btn.setEnabled(False)
        self.progress.setText("启动中…")
        submit(self.ctx.screener.run, cond, tech,
               int(self.max_count.value()), on_done=self._on_result,
               on_err=self._on_err, on_progress=self._on_progress,
               with_progress=True)

    def _on_progress(self, text: str) -> None:
        self.progress.setText(text)

    def _on_result(self, rows: list) -> None:
        self.run_btn.setEnabled(True)
        self.rows = rows or []
        self.progress.setText(f"选股完成：{len(self.rows)} 只")
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            self.table.setItem(r, 0, self._text(row.get("code")))
            self.table.setItem(r, 1, self._text(row.get("name")))
            self.table.setItem(r, 2, num_item(row.get("price")))
            self.table.setItem(r, 3, pct_item(row.get("change_pct")))
            self.table.setItem(r, 4, num_item(row.get("turnover_rate")))
            self.table.setItem(r, 5, num_item(row.get("volume_ratio")))
            self.table.setItem(r, 6, num_item(row.get("pe"), 1))
            self.table.setItem(r, 7, num_item(row.get("pb")))
            self.table.setItem(r, 8, num_item(row.get("float_mv"), 0))
            self.table.setItem(r, 9, num_item(row.get("amount"), 0))

    @staticmethod
    def _text(v) -> QTableWidgetItem:
        item = QTableWidgetItem("" if v is None else str(v))
        item.setData(Qt.UserRole, v)
        return item

    def _open_detail(self, item) -> None:
        row = item.row()
        code = self.table.item(row, 0).text()
        name = self.table.item(row, 1).text()
        self.open_stock.emit(code, name)

    def _on_err(self, msg: str) -> None:
        self.run_btn.setEnabled(True)
        from ..err import fail_hint, show_error
        show_error(self, "选股失败", msg)
        fail_hint(self.progress, "选股失败，详见弹窗")

    def _watch_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先在结果表选中一行")
            return
        code = self.table.item(row, 0).text()
        name = self.table.item(row, 1).text()
        self.ctx.cfg.add_watch(code, name)
        self.progress.setText(f"已加入自选: {name}({code})")

    def _export(self) -> None:
        if not self.rows:
            QMessageBox.information(self, "提示", "暂无选股结果")
            return
        from ...core.export_csv import rows_to_csv
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            self, "导出选股结果", "选股结果.csv", "CSV (*.csv)")
        if not path:
            return
        headers = ["code", "name", "price", "change_pct", "turnover_rate",
                   "volume_ratio", "pe", "pb", "float_mv", "amount"]
        zh = ["代码", "名称", "现价", "涨跌幅%", "换手率%", "量比", "PE",
              "PB", "流通市值(亿)", "成交额(万)"]
        rows = [{zh[i]: row.get(h) for i, h in enumerate(headers)}
                for row in self.rows]
        rows_to_csv(path, zh, rows)
        self.progress.setText(f"已导出 {len(rows)} 行到 {path}")

    def run_ai(self) -> None:
        if not self.rows:
            return
        from ...core import prompt
        context = prompt.build_screener_context(self.rows)
        self.ai_panel.run("选股解读", context)
