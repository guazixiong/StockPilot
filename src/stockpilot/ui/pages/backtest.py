"""策略回测页 v2：配置卡 + 绩效指标卡 + 收益曲线 + 逐笔明细 + AI 解读。"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDoubleSpinBox,
                               QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                               QRadioButton, QSpinBox, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ...core import strategy as stg
from ...core.backtest import BacktestConfig, run_backtest
from .. import kit
from ..ai_stream import AiStreamPanel
from ..line_chart import LineChart
from ..theme import DOWN, UP
from ..workers import submit

log = logging.getLogger(__name__)

_TRADE_COLS = ["代码", "名称", "买入日", "买入价", "卖出日", "卖出价",
               "收益%", "持有天", "卖出原因"]


def StatCard(name: str):
    from PySide6.QtWidgets import QFrame, QVBoxLayout
    f = QFrame()
    f.setProperty("card", True)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 10, 14, 10)
    lay.setSpacing(2)
    t = QLabel(name)
    t.setProperty("statName", True)
    v = QLabel("—")
    v.setProperty("statValue", True)
    v.setObjectName("statValue")
    lay.addWidget(t)
    lay.addWidget(v)
    f.value_label = v
    return f


class BacktestPage(QWidget):
    ask_ai = Signal(str, str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.result = None
        self._running = False
        self._build_ui()
        self._load_strategies()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(11, 11, 11, 11)
        lay.setSpacing(10)
        lay.addWidget(kit.page_head(
            "策略回测", "同口径回测 · 绩效指标 · 权益曲线 · AI 解读"))

        cfg_box = QGroupBox("回测配置（策略在「策略监听」页管理，回测与监听同口径）")
        grid = QGridLayout(cfg_box)
        row = 0
        grid.addWidget(QLabel("策略"), row, 0)
        self.stg_combo = QComboBox()
        grid.addWidget(self.stg_combo, row, 1)
        self.stg_desc = QLabel(" ")
        self.stg_desc.setProperty("hint", True)
        grid.addWidget(self.stg_desc, row, 2, 1, 3)
        row += 1
        grid.addWidget(QLabel("区间(交易日)"), row, 0)
        self.days_spin = QSpinBox()
        self.days_spin.setRange(60, 800)
        self.days_spin.setValue(250)
        grid.addWidget(self.days_spin, row, 1)
        grid.addWidget(QLabel("初始资金"), row, 2)
        self.cash_spin = QDoubleSpinBox()
        self.cash_spin.setRange(10000, 100000000)
        self.cash_spin.setDecimals(0)
        self.cash_spin.setValue(100000)
        grid.addWidget(self.cash_spin, row, 3)
        grid.addWidget(QLabel("佣金(‱双边)"), row, 4)
        self.fee_spin = QDoubleSpinBox()
        self.fee_spin.setRange(0, 30)
        self.fee_spin.setDecimals(2)
        self.fee_spin.setValue(2.5)
        grid.addWidget(self.fee_spin, row, 5)
        row += 1
        self.tp_spin = QDoubleSpinBox()
        self.sl_spin = QDoubleSpinBox()
        self.hold_spin = QSpinBox()
        grid.addWidget(QLabel("止盈%"), row, 0)
        self.tp_spin.setRange(1, 60)
        self.tp_spin.setValue(12)
        grid.addWidget(self.tp_spin, row, 1)
        grid.addWidget(QLabel("止损%"), row, 2)
        self.sl_spin.setRange(1, 30)
        self.sl_spin.setValue(6)
        grid.addWidget(self.sl_spin, row, 3)
        grid.addWidget(QLabel("最大持有(天)"), row, 4)
        self.hold_spin.setRange(1, 120)
        self.hold_spin.setValue(20)
        grid.addWidget(self.hold_spin, row, 5)
        lay.addWidget(cfg_box)

        uni_box = QGroupBox("股票范围（最多60只）")
        uv = QGridLayout(uni_box)
        self.rb_watch = QRadioButton("自选股")
        self.rb_top = QRadioButton("全市场成交额Top")
        self.rb_codes = QRadioButton("指定代码")
        self.top_spin = QSpinBox()
        self.top_spin.setRange(5, 60)
        self.top_spin.setValue(30)
        self.codes_edit = QPlainTextEdit()
        self.codes_edit.setPlaceholderText("每行一个代码，如\n600519\n000001\n300750")
        self.codes_edit.setMaximumHeight(66)
        grp = QButtonGroup(self)
        grp.addButton(self.rb_watch)
        grp.addButton(self.rb_top)
        grp.addButton(self.rb_codes)
        self.rb_top.setChecked(True)
        uv.addWidget(self.rb_watch, 0, 0)
        uv.addWidget(self.rb_top, 0, 1)
        uv.addWidget(self.top_spin, 0, 2)
        uv.addWidget(self.rb_codes, 0, 3)
        uv.addWidget(self.codes_edit, 1, 0, 1, 4)
        lay.addWidget(uni_box)

        bar = QHBoxLayout()
        self.run_btn = QPushButton("开始回测")
        self.run_btn.clicked.connect(self.run)
        bar.addWidget(self.run_btn)
        self.validate_btn = QPushButton("选股验证报告")
        self.validate_btn.setToolTip("全部策略在本股票池上横评：胜率/盈亏比/收益/回撤排名")
        self.validate_btn.clicked.connect(self.run_validation)
        bar.addWidget(self.validate_btn)
        export_btn = QPushButton("导出本次交易明细")
        export_btn.setProperty("secondary", True)
        export_btn.clicked.connect(self._export)
        bar.addWidget(export_btn)
        self.progress = QLabel(" ")
        self.progress.setProperty("hint", True)
        bar.addWidget(self.progress, 1)
        lay.addLayout(bar)

        split = QSplitter(Qt.Vertical)
        lay.addWidget(split, 1)
        top_w = QWidget()
        tl = QVBoxLayout(top_w)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(10)

        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.cards = {}
        for name in ("总收益%", "年化%", "胜率%", "盈亏比", "最大回撤%", "交易次数"):
            c = StatCard(name)
            self.cards[name] = c
            cards.addWidget(c, 1)
        tl.addLayout(cards)

        self.equity_chart = LineChart("组合权益曲线")
        tl.addWidget(self.equity_chart, 1)

        self.trade_table = QTableWidget(0, len(_TRADE_COLS))
        self.trade_table.setHorizontalHeaderLabels(_TRADE_COLS)
        self.trade_table.verticalHeader().hide()
        self.trade_table.setAlternatingRowColors(True)
        self.trade_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.trade_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.trade_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.trade_table.horizontalHeader().setStretchLastSection(True)
        tl.addWidget(self.trade_table, 2)
        split.addWidget(top_w)

        self.ai_panel = AiStreamPanel(self.ctx, "AI 回测报告解读")
        split.addWidget(self.ai_panel)
        split.setSizes([430, 180])

    def _load_strategies(self) -> None:
        self.stg_combo.clear()
        self.all_strategies = list(stg.builtin_strategies())
        for d in self.ctx.cfg.get_strategy_dicts():
            try:
                self.all_strategies.append(stg.Strategy.from_dict(d))
            except Exception:  # noqa: BLE001
                pass
        for s in self.all_strategies:
            self.stg_combo.addItem(s.name)
        self.stg_combo.currentIndexChanged.connect(self._on_stg_change)
        self._on_stg_change()

    def _on_stg_change(self, *_):
        s = self._current_strategy()
        if s:
            self.stg_desc.setText(s.desc or "")
            self.tp_spin.setValue(s.take_profit_pct)
            self.sl_spin.setValue(s.stop_loss_pct)
            self.hold_spin.setValue(s.max_hold_days)

    def _current_strategy(self):
        idx = self.stg_combo.currentIndex()
        return self.all_strategies[idx] if 0 <= idx < len(self.all_strategies) else None

    # ------------------------------------------------------------ 运行
    def _resolve_codes(self) -> list:
        codes: list = []
        if self.rb_watch.isChecked():
            codes = [x["code"] for x in self.ctx.cfg.get_watchlist()]
        elif self.rb_top.isChecked():
            rows = self.ctx.market_top_n(self.top_spin.value())
            codes = [r["code"] for r in rows]
        else:
            import re as _re
            text = self.codes_edit.toPlainText()
            codes = _re.findall(r"\b\d{6}\b", text)[:60]
        return list(dict.fromkeys(codes))[:60]

    def run(self) -> None:
        if self._running:
            return
        s = self._current_strategy()
        if not s:
            QMessageBox.information(self, "提示", "请先选择策略")
            return
        codes = self._resolve_codes()
        if not codes:
            QMessageBox.information(self, "提示", "股票范围为空")
            return
        self._running = True
        self.run_btn.setEnabled(False)
        # v4.4.1：submit 后立即给反馈（此前空白期可达 15~30s，用户以为点击无效）
        self.progress.setText(f"预热 {len(codes)} 只K线并回测中…")
        cfg = BacktestConfig(
            strategy_name=s.name, codes=codes,
            days=int(self.days_spin.value()),
            cash=float(self.cash_spin.value()),
            fee_rate=float(self.fee_spin.value()) / 10000,
            take_profit_pct=float(self.tp_spin.value()),
            stop_loss_pct=float(self.sl_spin.value()),
            max_hold_days=int(self.hold_spin.value()))
        submit(self._do_backtest, s, codes, cfg, on_done=self._on_done,
               on_err=self._on_err, on_progress=self.progress.setText,
               with_progress=True)

    def _do_backtest(self, strategy, codes, cfg: BacktestConfig, on_progress=None):
        data = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            warm = min(cfg.days + 80, 800)   # 多源K线上限约800根（v4.3实测）
            futs = {c: pool.submit(self.ctx.kline_for_scan, c,
                                   warm) for c in codes}
            done = 0
            for code, fut in futs.items():
                try:
                    kls = fut.result()
                except Exception as exc:  # noqa: BLE001
                    log.warning("回测K线获取失败 %s: %s", code, exc)
                    if on_progress:
                        on_progress(f"{code} K线获取失败")
                    continue
                name = ""
                try:
                    q = self.ctx.get_quotes([code]).get(code)
                    name = q.name if q else ""
                except Exception:  # noqa: BLE001
                    pass
                data[code] = (name or code, kls)
                done += 1
                if on_progress:
                    on_progress(f"K线就绪 {done}/{len(codes)}")
        if on_progress:
            on_progress("回测计算中…")
        return run_backtest(strategy, data, cfg, on_progress=on_progress)

    def _on_done(self, result) -> None:
        self._running = False
        self.run_btn.setEnabled(True)
        self.result = result
        self.progress.setText(f"回测完成：{result.trade_count} 笔交易")

        color = UP if result.total_return_pct >= 0 else DOWN
        values = {f"{name}": txt for name, txt in (
            ("总收益%", f"{result.total_return_pct:+.2f}%"),
            ("年化%", f"{result.annual_return_pct:+.2f}%"),
            ("胜率%", f"{result.win_rate:.1f}%"),
            ("盈亏比", f"{result.profit_factor:.2f}"),
            ("最大回撤%", f"{result.max_drawdown_pct:.2f}%"),
            ("交易次数", f"{result.trade_count}"))}
        for name, txt in values.items():
            card = self.cards[name]
            card.value_label.setText(txt)
            card.value_label.setStyleSheet(
                f"color:{color};" if name in ("总收益%", "年化%") else "")

        # 组合权益曲线（回测引擎已按索引对齐求和）
        self.equity_chart.set_series(result.equity, base=result.initial_cash)

        self.trade_table.setRowCount(len(result.trades))
        for r, t in enumerate(result.trades):
            vals = [t.code, t.name, t.entry_date, f"{t.entry_price:.2f}",
                    t.exit_date, f"{t.exit_price:.2f}", f"{t.pnl_pct:+.2f}%",
                    str(t.hold_days), t.reason]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c == 6:
                    item.setForeground(QColor(
                        UP if t.pnl_pct > 0 else DOWN))
                if c in (3, 5, 6):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.trade_table.setItem(r, c, item)

    def run_validation(self) -> None:
        """全策略×本页股票池 横评报告。"""
        if self._running:
            return
        codes = self._resolve_codes()
        if not codes:
            QMessageBox.information(self, "提示", "股票范围为空")
            return
        self._running = True
        self.validate_btn.setEnabled(False)
        self.progress.setText("验证中：拉取K线并回测全部策略…")
        submit(self._do_validation, codes, int(self.days_spin.value()),
               on_done=self._on_validation, on_err=self._on_err,
               on_progress=self.progress.setText, with_progress=True)

    def _do_validation(self, codes, days, on_progress=None):
        from ...core.validation import run_validation
        from concurrent.futures import ThreadPoolExecutor
        data = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {c: pool.submit(
                        self.ctx.kline_for_scan, c, min(days + 80, 800))
                    for c in codes}
            done = 0
            for c, fut in futs.items():
                try:
                    kls = fut.result()
                    if kls:
                        data[c] = (c, kls)
                except Exception:  # noqa: BLE001
                    pass
                done += 1
                if on_progress:
                    on_progress(f"K线就绪 {done}/{len(codes)}")
        if on_progress:
            on_progress("全部策略回测中…")
        from ...core.backtest import BacktestConfig
        cfg = BacktestConfig(
            days=days, cash=float(self.cash_spin.value()),
            fee_rate=float(self.fee_spin.value()) / 10000)
        return run_validation(data, cfg)

    def _on_validation(self, report) -> None:
        self._running = False
        self.validate_btn.setEnabled(True)
        self.progress.setText(f"验证完成：{len(report.strategies)} 套策略")
        lines = report.rank_lines()
        from PySide6.QtWidgets import QDialog, QTextBrowser
        dlg = QDialog(self)
        dlg.setWindowTitle(f"🏆 选股验证报告（{report.codes}只 × {report.days}日）")
        dlg.resize(820, 640)
        v = QVBoxLayout(dlg)
        view = QTextBrowser()
        view.setStyleSheet(
            "QTextBrowser{font-family:'Microsoft YaHei UI';font-size:9pt;"
            "background:#0A1A30;color:#EAF2FF;border:none;padding:10px;}")
        html = ("<pre style='line-height:1.7'>" + chr(10).join(lines[:30])
                + "</pre><br><div style='color:#8FA9C7'>" + report.summary()
                + "</div><div style='color:#6b7383;font-size:8pt;margin-top:8px'>"
                  "基于历史数据回测，过往表现不代表未来收益 · 分批建仓/滑点/最低佣金已计入"
                  "</div>")
        view.setHtml(html)
        v.addWidget(view)
        dlg.show()

    def _on_err(self, msg: str) -> None:
        """v7.2：错误弹窗呈现，进度区只留短状态。"""
        self._running = False
        self.run_btn.setEnabled(True)
        self.validate_btn.setEnabled(True)
        from ..err import fail_hint, show_error
        show_error(self, "回测失败", msg)
        fail_hint(self.progress, "回测失败，详见弹窗")

    def _export(self) -> None:
        if not self.result:
            return
        from ...core.export_csv import rows_to_csv
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            self, "导出逐笔交易", "回测逐笔交易.csv", "CSV (*.csv)")
        if not path:
            return
        rows = [{"代码": t.code, "名称": t.name, "买入日": t.entry_date,
                 "买入价": t.entry_price, "卖出日": t.exit_date,
                 "卖出价": t.exit_price, "收益%": t.pnl_pct,
                 "持有天": t.hold_days, "卖出原因": t.reason}
                for t in self.result.trades]
        rows_to_csv(path, list(rows[0].keys()) if rows else
                    ["代码", "名称", "买入日", "买入价", "卖出日", "卖出价",
                     "收益%", "持有天", "卖出原因"], rows)
        self.progress.setText(f"已导出 {len(rows)} 笔到 {path}")

    def run_ai(self) -> None:
        if not self.result:
            return
        from ...core import prompt
        self.ai_panel.run("回测解读", prompt.build_backtest_context(self.result))
