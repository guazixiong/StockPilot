"""板块热力页 v5：demo 版式（PageHead + 概览统计卡 + 热力网格 + 资金流）。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                                QPushButton, QScrollArea, QTabWidget,
                                QVBoxLayout, QWidget)

from .. import kit
from ..ai_side_panel import AiSidePanel
from ..heat_widgets import (AnimatedNumber, FlowBar, HBar, HeatTile,
                             heat_color)
from ..theme import DOWN, SUB, UP
from ..workers import submit
from ...core.export_csv import rows_to_csv


def _card() -> QFrame:
    f = QFrame()
    f.setProperty("card", True)
    return f


class BoardsPage(QWidget):

    open_stock = Signal(str, str)   # 领涨股 → 个股详情

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._boards = []
        self._moneyflow = None
        self._loading_boards = False
        self._build_ui()
        self.refresh()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(60 * 1000)

    # ================================================================ UI
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(11, 11, 11, 11)
        lay.setSpacing(10)

        head = kit.page_head(
            "板块热力", "行业热度网格 · 资金流向 · 每分钟自动刷新")
        lay.addWidget(head)

        body = QHBoxLayout()
        body.setSpacing(10)
        lay.addLayout(body, 1)

        # 左：Tabs（板块热度 / 资金流向）
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(10)

        # 概览统计卡（demo .quotes 版式：上涨/下跌/平均）
        summary = QHBoxLayout()
        summary.setSpacing(10)
        self.up_card = kit.stat_card("上涨板块")
        self.down_card = kit.stat_card("下跌板块")
        self.avg_card = kit.stat_card("板块平均涨幅")
        summary.addWidget(self.up_card)
        summary.addWidget(self.down_card)
        summary.addWidget(self.avg_card)
        self.up_count = AnimatedNumber(ndigits=0)
        self.down_count = AnimatedNumber(ndigits=0)
        self.avg_pct = AnimatedNumber(ndigits=2, suffix="%")
        self.up_card.value_label.deleteLater()
        self.down_card.value_label.deleteLater()
        self.avg_card.value_label.deleteLater()
        self.up_card.layout().addWidget(self.up_count)
        self.down_card.layout().addWidget(self.down_count)
        self.avg_card.layout().addWidget(self.avg_pct)
        lv.addLayout(summary)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_heat_tab(), "板块热度")
        self.tabs.addTab(self._build_flow_tab(), "资金流向")
        lv.addWidget(self.tabs, 1)

        # 工具行
        bar = _card()
        bh = QHBoxLayout(bar)
        bh.setContentsMargins(12, 6, 12, 6)
        self.status = QLabel(" ")
        self.status.setProperty("hint", True)
        refresh_btn = QPushButton("立即刷新")
        refresh_btn.setProperty("secondary", True)
        refresh_btn.setFixedHeight(30)
        refresh_btn.clicked.connect(self.refresh)
        export_btn = QPushButton("导出 CSV")
        export_btn.setProperty("secondary", True)
        export_btn.setFixedHeight(30)
        export_btn.clicked.connect(self._export)
        ai_btn = QPushButton("✦ AI 解读板块")
        ai_btn.setProperty("ai", True)
        ai_btn.setFixedHeight(30)
        ai_btn.clicked.connect(self._ai_analyze)
        bh.addWidget(self.status, 1)
        bh.addWidget(refresh_btn)
        bh.addWidget(export_btn)
        bh.addWidget(ai_btn)
        lv.addWidget(bar)

        body.addWidget(left, 3)

        # 右：就地 AI 解读面板
        self.ai_panel = AiSidePanel(self.ctx, "板块 AI 解读")
        self.ai_panel.setProperty("aiPanel", True)
        self.ai_panel.setFixedWidth(400)
        body.addWidget(self.ai_panel, 1)

    # ---------------- Tab1：板块热度 ----------------
    def _build_heat_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(8)
        legend = QLabel("块色：红=涨 绿=跌 · 字号：成交额越大越大 · 点击块看领涨股")
        legend.setProperty("hint", True)
        v.addWidget(legend)
        self.grid_holder = QScrollArea()
        self.grid_holder.setWidgetResizable(True)
        self.grid_holder.setFrameShape(QFrame.NoFrame)
        self._grid_inner = QWidget()
        self.grid = QGridLayout(self._grid_inner)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(8)
        self._grid_inner.setStyleSheet("background:transparent;")
        self.grid_holder.setWidget(self._grid_inner)
        v.addWidget(self.grid_holder, 1)
        return w

    # ---------------- Tab2：资金流向 ----------------
    def _build_flow_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(10)

        money_card = _card()
        mv = QVBoxLayout(money_card)
        mv.setContentsMargins(14, 10, 14, 10)
        mv.setSpacing(4)
        mt = QLabel("沪深两市主力资金（净流入向右·红 / 净流出向左·绿）")
        mt.setProperty("panelTitle", True)
        self.flow_sh = FlowBar("上证指数")
        self.flow_sz = FlowBar("深证成指")
        self.money_note = QLabel(" ")
        self.money_note.setProperty("hint", True)
        mv.addWidget(mt)
        mv.addWidget(self.flow_sh)
        mv.addWidget(self.flow_sz)
        mv.addWidget(self.money_note)
        v.addWidget(money_card)

        amt_card = _card()
        av = QVBoxLayout(amt_card)
        av.setContentsMargins(14, 10, 14, 10)
        av.setSpacing(4)
        at = QLabel("行业板块成交额排行 TOP15")
        at.setProperty("panelTitle", True)
        self.amt_holder = QScrollArea()
        self.amt_holder.setWidgetResizable(True)
        self.amt_holder.setFrameShape(QFrame.NoFrame)
        self._amt_inner = QWidget()
        self._amt_inner.setStyleSheet("background:transparent;")
        self.amt_lay = QVBoxLayout(self._amt_inner)
        self.amt_lay.setContentsMargins(0, 4, 0, 4)
        self.amt_lay.setSpacing(3)
        self.amt_holder.setWidget(self._amt_inner)
        av.addWidget(at)
        av.addWidget(self.amt_holder, 1)
        v.addWidget(amt_card, 1)
        return w

    # ================================================================ 数据
    def refresh(self) -> None:
        submit(self._load_boards, on_done=self._on_boards,
               on_err=self._on_boards_err)
        submit(self._load_moneyflow, on_done=self._on_moneyflow,
               on_err=lambda m: self._on_moneyflow(None))

    def _load_boards(self):
        return self.ctx.get_industry_boards()

    def _load_moneyflow(self):
        return self.ctx.get_market_moneyflow()

    def _on_boards_err(self, msg: str) -> None:
        from ..err import fail_hint, show_error
        show_error(self, "板块数据获取失败", msg)
        fail_hint(self.status, "获取失败，可点刷新重试")

    def _on_boards(self, boards: list) -> None:
        self._boards = boards or []
        self.status.setText(f"共 {len(self._boards)} 个行业板块")

        ups = sum(1 for b in self._boards if (b.change_pct or 0) > 0)
        downs = sum(1 for b in self._boards if (b.change_pct or 0) < 0)
        avg = (sum(b.change_pct or 0 for b in self._boards)
               / len(self._boards)) if self._boards else 0
        self.up_count.animate_to(ups)
        self.up_count.setStyleSheet(
            f"font-size:14pt;font-weight:bold;color:{UP};"
            "background:transparent;")
        self.down_count.animate_to(downs)
        self.down_count.setStyleSheet(
            f"font-size:14pt;font-weight:bold;color:{DOWN};"
            "background:transparent;")
        self.avg_pct.animate_to(avg, color=UP if avg >= 0 else DOWN)

        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        order = sorted(self._boards,
                       key=lambda b: b.change_pct or -99, reverse=True)
        cols = 5
        for i, b in enumerate(order):
            tile = HeatTile(b)
            tile.clicked_leader.connect(self._open_leader)
            self.grid.addWidget(tile, i // cols, i % cols)

        while self.amt_lay.count():
            item = self.amt_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        top = sorted(self._boards,
                     key=lambda b: b.amount_yi or 0, reverse=True)[:15]
        max_amt = (top[0].amount_yi if top else 1) or 1
        for b in top:
            bar = HBar(b.name)
            self.amt_lay.addWidget(bar)
            bar.animate_to(
                (b.amount_yi or 0) / max_amt,
                f"{b.amount_yi or 0:,.0f} 亿 {b.change_pct or 0:+.2f}%",
                color=UP if (b.change_pct or 0) >= 0 else DOWN)
        self.amt_lay.addStretch(1)

    def _on_moneyflow(self, data) -> None:
        self._moneyflow = data
        if not data:
            self.money_note.setText(
                "两市主力净额暂不可用（数据源限流中，稍后自动重试）；"
                "下方板块成交额排行仍可参考资金热度。")
            return
        sh = data.get("上证指数") or 0
        sz = data.get("深证成指") or 0
        self.money_note.setText("")
        max_abs = max(abs(sh), abs(sz), 1e8)
        self.flow_sh.animate_to(sh / 1e8, max_abs / 1e8)
        self.flow_sz.animate_to(sz / 1e8, max_abs / 1e8)

    def _open_leader(self, code: str, name: str) -> None:
        self.open_stock.emit(code, name)

    # ================================================================ 导出 / AI
    def _export(self) -> None:
        if not self._boards:
            return
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            self, "导出板块数据", "行业板块.csv", "CSV (*.csv)")
        if not path:
            return
        headers = ["板块", "涨跌幅%", "成交额(亿)", "成分数", "领涨股", "代码"]
        rows = [{"板块": b.name, "涨跌幅%": b.change_pct,
                 "成交额(亿)": round(b.amount_yi or 0, 1),
                 "成分数": b.count, "领涨股": b.leader_name, "代码": b.leader_code}
                for b in self._boards]
        rows_to_csv(path, headers, rows)
        self.status.setText(f"已导出 {len(rows)} 个板块到 {path}")

    def _ai_analyze(self) -> None:
        """组装板块热度+资金概览 → 右侧 AI 面板就地分析（真实调用）。"""
        if not self._boards:
            self.status.setText("暂无板块数据，请先刷新")
            return
        order = sorted(self._boards,
                       key=lambda b: b.change_pct or -99, reverse=True)
        up5 = "、".join(f"{b.name}({b.change_pct:+.2f}%)"
                       + (f" 领涨:{b.leader_name}" if b.leader_name else "")
                       for b in order[:5])
        down5 = "、".join(f"{b.name}({b.change_pct:+.2f}%)"
                          for b in order[-5:])
        amt5 = "、".join(
            f"{b.name}({b.amount_yi:.0f}亿)" for b in sorted(
                self._boards, key=lambda x: x.amount_yi or 0,
                reverse=True)[:5])
        ups = sum(1 for b in self._boards if (b.change_pct or 0) > 0)
        downs = sum(1 for b in self._boards if (b.change_pct or 0) < 0)
        context = (f"【行业板块热度】共{len(self._boards)}个行业板块，"
                   f"上涨{ups}个、下跌{downs}个。\n"
                   f"涨幅前五：{up5}\n跌幅前五：{down5}\n"
                   f"成交额前五：{amt5}")
        if self._moneyflow:
            context += ("\n沪深两市主力净额：上证 "
                        f"{self._moneyflow.get('上证指数', 0)/1e8:.1f}亿、"
                        f"深证 {self._moneyflow.get('深证成指', 0)/1e8:.1f}亿")
        else:
            context += "\n（两市主力净额暂不可用，可结合板块成交额判断资金热度）"
        try:
            client = self.ctx.ai_client()
        except RuntimeError as exc:
            self.status.setText(str(exc))
            return
        from ...core import prompt
        self.ai_panel._set_object("板块热点分析")
        messages = prompt.build_messages("自由问答", context,
                                        "请分析今日行业板块热点与资金流向，"
                                        "指出最强主线、风险方向与操作参考")
        self.ai_panel._history = list(messages)
        self.ai_panel._start(client, self.ai_panel._history,
                             "解读板块热点与资金流向", new_session=True)

    def on_show(self) -> None:
        pass
