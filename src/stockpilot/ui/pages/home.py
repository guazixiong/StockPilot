"""首页 v5：demo Fusion Terminal 三栏工作台。

demo 布局映射 + 底层功能保留：
- 顶部「今日市场概览」：四大指数实况卡 + 市场情绪（涨跌家数 gauge）+ 北向资金
- 左栏：市场风向（板块涨跌 Top 行）+ 热门概念（板块表格）+ 龙虎榜速览
- 中栏：上证指数 K 线（真实 CandleChart，demo 中央核心视觉）+ 今日机会流 + 7×24 快讯
- 右栏：AI 分析助手面板 + 最新资讯
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QListWidget, QListWidgetItem, QPushButton,
                               QScrollArea, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .. import kit
from ..signal_card import OpportunityFlow
from ..theme import DOWN, FLAT, SUB, UP
from ..workers import submit
from ...core import strategy as stg
from ...core.monitor import run_strategy_scan

_SYMBOLS = {"sh000001", "sz399001", "sz399006", "sh000688"}
_SH = "sh000001"


def _card() -> QFrame:
    f = QFrame()
    f.setProperty("card", True)
    return f


class StockRow(QFrame):
    """自选股行（保留：首页机会流的加自选交互入口）。"""

    def __init__(self, code: str, name: str):
        super().__init__()
        self.code, self.name = code, name
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("card", True)
        self.setFixedHeight(50)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 6, 12, 6)
        self.name_label = QLabel(name or code)
        self.name_label.setStyleSheet("font-weight:bold;background:transparent;")
        self.price_label = QLabel("—")
        self.price_label.setStyleSheet(
            "font-size:11pt; font-weight:bold;background:transparent;")
        self.chg_label = QLabel("—")
        h.addWidget(self.name_label, 1)
        h.addWidget(self.price_label)
        h.addWidget(self.chg_label)

    def update_quote(self, q) -> None:
        if q is None or q.price is None:
            return
        chg = q.change_pct or 0
        color = UP if chg > 0 else (DOWN if chg < 0 else FLAT)
        self.price_label.setText(f"{q.price:.2f}")
        self.price_label.setStyleSheet(
            f"font-size:11pt; font-weight:bold; color:{color};"
            "background:transparent;")
        self.chg_label.setText(f"{chg:+.2f}%")
        self.chg_label.setStyleSheet(
            f"color:{color}; font-weight:700;background:transparent;")


class HomePage(QWidget):
    open_stock = Signal(str, str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._scanned = False
        self._scanning = False
        self._quotes_in_flight = False
        self._stock_rows: list = []
        self._news_items: list = []
        self._lhb_items: list = []
        self._boards: list = []
        self._build_ui()
        self._refresh_lhb()
        self._refresh_overview()
        self._refresh_boards()
        self._refresh_sh_kline()

        self.quote_timer = QTimer(self)
        self.quote_timer.timeout.connect(self._tick_refresh)
        self.quote_timer.start(15000)
        self._refresh_watch()
        self._refresh_news()

    def _tick_refresh(self) -> None:
        self._refresh_overview()
        self._refresh_sh_kline()

    # ================================================================ UI
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(11, 11, 11, 11)   # demo #view padding
        lay.setSpacing(10)

        # ---- demo .market-overview：今日市场概览 ----
        ov = kit.panel("今日市场概览",
                       "A股四大指数 · 市场情绪 · 资金动向")
        ov_row = QHBoxLayout()
        ov_row.setContentsMargins(12, 4, 12, 10)
        ov_row.setSpacing(8)
        self.overview_cards: dict = {}
        from ..app import INDEXES as _IDX
        for sym, name in _IDX:
            c = kit.index_quote(name)
            self.overview_cards[sym] = c
            ov_row.addWidget(c, 1)
        # 市场情绪（demo .sentiment：涨跌家数概览卡）
        senti = QFrame()
        senti.setProperty("quote", True)
        s_v = QVBoxLayout(senti)
        s_v.setContentsMargins(13, 10, 13, 9)
        s_v.setSpacing(3)
        st = QLabel("市场情绪")
        st.setProperty("statName", True)
        self.senti_value = QLabel("—")
        self.senti_value.setProperty("quoteValue", True)
        self.senti_sub = QLabel("待扫描")
        self.senti_sub.setStyleSheet("color:#8095AF;font-size:7.5pt;"
                                     "background:transparent;")
        s_v.addWidget(st)
        s_v.addWidget(self.senti_value)
        s_v.addWidget(self.senti_sub)
        ov_row.addWidget(senti)
        ov.body_lay.addLayout(ov_row)
        ov.setFixedHeight(148)
        lay.addWidget(ov)

        # ---- demo .home-grid 三栏 ----
        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(10)
        lay.addWidget(split, 1)

        # ============ 左栏：市场风向 + 热门概念 + 龙虎榜 ============
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(10)

        wind = kit.panel("市场风向", sub="行业板块涨跌")
        w_body = QVBoxLayout()
        w_body.setContentsMargins(8, 6, 8, 8)
        w_body.setSpacing(2)
        self.wind_rows: list = []
        for _ in range(8):
            row = QFrame()
            r_h = QHBoxLayout(row)
            r_h.setContentsMargins(8, 5, 8, 5)
            nm = QLabel("—")
            nm.setStyleSheet("font-size:8.5pt;background:transparent;")
            pct = QLabel("—")
            pct.setStyleSheet("font-size:8.5pt;font-weight:bold;"
                              "background:transparent;")
            spark = kit.SparkLine()
            spark.setFixedSize(46, 18)
            r_h.addWidget(nm, 1)
            r_h.addWidget(pct)
            r_h.addWidget(spark)
            w_body.addWidget(row)
            self.wind_rows.append((nm, pct, spark))
        wind.body_lay.addLayout(w_body)
        lv.addWidget(wind, 3)

        lhb = kit.panel("龙虎榜速览", more="双击进个股 ›")
        self.lhb_list = QListWidget()
        self.lhb_list.setFixedHeight(120)
        self.lhb_list.itemDoubleClicked.connect(self._on_lhb_double)
        lhb.body_lay.addWidget(self.lhb_list)
        lv.addWidget(lhb, 2)
        split.addWidget(left)

        # ============ 中栏：上证 K 线 + 今日机会 + 快讯 ============
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(10)

        # demo 中央核心视觉：上证指数 K 线
        chart_card = kit.panel("", ai=False)
        ch_body = QVBoxLayout()
        ch_body.setContentsMargins(8, 8, 8, 8)
        ch_body.setSpacing(6)
        # chart-title（demo .chart-title：名称+大价+统计）
        ct = QHBoxLayout()
        ct_left = QVBoxLayout()
        ct_left.setSpacing(2)
        ct_name = QLabel("上证指数 <span style='color:#7287A3'>000001</span>")
        ct_name.setStyleSheet("font-size:11pt;font-weight:bold;"
                              "background:transparent;")
        self.sh_price = QLabel("—")
        self.sh_price.setStyleSheet(
            "font-size:16pt;font-weight:800;background:transparent;")
        ct_left.addWidget(ct_name)
        ct_left.addWidget(self.sh_price)
        self.sh_stats = QLabel("—")
        self.sh_stats.setStyleSheet(
            "color:#8298B4;font-size:8pt;background:transparent;")
        ct.addLayout(ct_left)
        ct.addStretch(1)
        ct.addWidget(self.sh_stats)
        ch_body.addLayout(ct)
        from ..kline_chart import CandleChart
        self.chart = CandleChart()
        # v7.2.4：首页 K 线压缩（详细图在行情/详情页看），把纵向空间
        # 让给机会流 —— 此前图表最小高 430px 把中栏顶死，机会区只剩 1 张卡
        self.chart.setMinimumHeight(240)
        ch_body.addWidget(self.chart, 1)
        chart_card.body_lay.addLayout(ch_body)
        cv.addWidget(chart_card, 3)

        # 今日机会（保留机会雷达核心功能；v7.2.4 布局调整：中栏纵向配额
        # 向机会区倾斜 3:6:1——此前 5:4:2 一屏只能看到 1 张卡，用户反馈拥挤）
        opp = kit.panel("今日机会", sub="策略扫描 · 按机会分降序", more="")
        op_body = QVBoxLayout()
        op_body.setContentsMargins(8, 4, 8, 8)
        op_body.setSpacing(4)
        head = QHBoxLayout()
        self.scan_status = QLabel(" ")
        self.scan_status.setProperty("hint", True)
        self.opp_info = QLabel(" ")
        self.opp_info.setProperty("hint", True)
        rescan = QPushButton("重新找机会")
        rescan.setProperty("secondary", True)
        rescan.setFixedHeight(26)
        rescan.clicked.connect(lambda: self.scan_opportunities(force=True))
        head.addWidget(self.scan_status, 1)
        head.addWidget(self.opp_info)
        head.addWidget(rescan)
        op_body.addLayout(head)
        self.flow = OpportunityFlow()
        self.flow.open_stock.connect(self.open_stock)
        self.flow.add_watch.connect(self._watch_from_flow)
        op_body.addWidget(self.flow, 1)
        opp.body_lay.addLayout(op_body)
        cv.addWidget(opp, 6)
        self._update_opp_info()

        # 7×24 快讯（v7.2.4：压缩为单行滚动条，把纵向空间让给机会区）
        news = kit.panel("7×24 快讯", more="双击 AI 解读 ›")
        self.news_list = QListWidget()
        self.news_list.setFixedHeight(54)
        self.news_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.news_list.itemDoubleClicked.connect(self._on_news_double)
        news.body_lay.addWidget(self.news_list)
        cv.addWidget(news, 1)
        split.addWidget(center)

        # ============ 右栏：AI 助手 + 我的自选 ============
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(10)

        from ..ai_side_panel import AiSidePanel
        self.ai_panel = AiSidePanel(self.ctx, "今日机会分析")
        self.ai_panel.setProperty("aiPanel", True)
        self.flow.ask_ai.connect(self.ai_panel.analyze_signal)
        rv.addWidget(self.ai_panel, 3)

        watch = kit.panel("我的自选", more="行情页管理 ›")
        w_scroll = QScrollArea()
        w_scroll.setWidgetResizable(True)
        w_scroll.setFrameShape(QFrame.NoFrame)
        w_inner = QWidget()
        w_inner.setStyleSheet("background:transparent;")
        self.watch_lay = QVBoxLayout(w_inner)
        self.watch_lay.setContentsMargins(8, 4, 8, 8)
        self.watch_lay.setSpacing(6)
        self.watch_lay.addStretch(1)
        w_scroll.setWidget(w_inner)
        watch.body_lay.addWidget(w_scroll)
        rv.addWidget(watch, 2)
        split.addWidget(right)

        split.setSizes([230, 640, 430])

    def _watch_from_flow(self, code: str, name: str) -> None:
        added = self.ctx.cfg.add_watch(code, name)
        self.scan_status.setText(
            f"已加入自选: {name}({code})" if added else f"{name}({code}) 已在自选中")
        self._rebuild_watch_rows()

    # ================================================================ 机会
    def on_show(self) -> None:
        if not self._scanned:
            self.scan_opportunities()

    def scan_opportunities(self, force: bool = False) -> None:
        if self._scanning or (self._scanned and not force):
            return
        self._scanning = True
        self.scan_status.setText("正在全市场寻找机会…")
        submit(self._do_scan, on_done=self._on_scan_done,
               on_err=self._on_scan_err, on_progress=self.scan_status.setText,
               with_progress=True)

    def _do_scan(self, on_progress=None):
        rows = self.ctx._market_fetch(1500, on_progress=on_progress)
        return self.ctx.opportunity_scan(rows, on_progress)

    def _update_opp_info(self) -> None:
        names = self.ctx.cfg.opportunity_strategies(
            self.ctx.all_strategy_names())
        min_score = self.ctx.cfg.opportunity.get("min_score") or 0
        trend = "开" if self.ctx.cfg.opportunity.get("require_above_ma20") else "关"
        self.opp_info.setText(
            f"策略 {len(names)} 套 · 门槛分 {min_score} · MA20过滤{trend}")

    def _on_scan_done(self, signals: list) -> None:
        self._scanning = False
        self._scanned = True
        self.flow.set_signals(signals or [])
        n = len(signals or [])
        a = sum(1 for s in (signals or []) if s.grade == "A")
        self.scan_status.setText(
            f"找到 {n} 个机会 · A级 {a}" if n else "暂无符合策略的机会")
        # 市场情绪卡（demo .sentiment 等价物：机会面情绪）
        if n:
            self.senti_value.setText(f"{min(n, 99)}")
            self.senti_value.setStyleSheet(
                "color:#4EDCC8;font-size:14pt;font-weight:800;"
                "background:transparent;")
            self.senti_sub.setText(f"A级 {a} · 机会温度")
        else:
            self.senti_value.setText("—")
            self.senti_sub.setText("机会温度 · 扫描中")

    def _on_scan_err(self, msg: str) -> None:
        self._scanning = False
        from ..err import fail_hint, show_error
        show_error(self, "机会扫描失败", msg)
        fail_hint(self.scan_status, "扫描失败，可重试")

    # ================================================================ 概览 / K线
    def _refresh_overview(self) -> None:
        from ..app import INDEXES
        submit(self.ctx.get_quotes, [s for s, _ in INDEXES],
               on_done=lambda qs: self._fill_overview(qs, INDEXES),
               on_err=lambda m: None)

    def _fill_overview(self, quotes: dict, index_defs) -> None:
        for sym, _name in index_defs:
            card = self.overview_cards.get(sym)
            if card:
                kit.fill_index_quote(card, quotes.get(sym))
        sh = quotes.get(_SH)
        if sh and sh.price is not None:
            color = UP if (sh.change_pct or 0) > 0 else (
                DOWN if (sh.change_pct or 0) < 0 else FLAT)
            chg = sh.change_pct or 0
            self.sh_price.setText(
                f"{sh.price:,.2f}　"
                f"<span style='color:{color};font-size:10pt'>"
                f"{chg:+.2f}%</span>")
            self.sh_price.setStyleSheet(
                "font-size:16pt;font-weight:800;background:transparent;")
            stats = []
            for label, v in (("今开", sh.open), ("最高", sh.high),
                             ("最低", sh.low)):
                if v is not None:
                    stats.append(f"{label} {v:,.2f}")
            self.sh_stats.setText("　".join(stats))

    def _refresh_sh_kline(self) -> None:
        submit(self.ctx.get_kline, _SH, "day", 180,
               on_done=self._on_kline, on_err=lambda m: None)

    def _on_kline(self, kls) -> None:
        if kls:
            self.chart.set_data(kls)

    def _refresh_boards(self) -> None:
        submit(self.ctx.get_industry_boards,
               on_done=self._on_boards, on_err=lambda m: None)

    def _on_boards(self, boards: list) -> None:
        self._boards = boards or []
        order = sorted(self._boards,
                       key=lambda b: abs(b.change_pct or 0), reverse=True)
        for i, (nm, pct, spark) in enumerate(self.wind_rows):
            if i < len(order):
                b = order[i]
                p = b.change_pct or 0
                color = UP if p > 0 else (DOWN if p < 0 else FLAT)
                nm.setText(b.name)
                pct.setText(f"{p:+.2f}%")
                pct.setStyleSheet(
                    f"font-size:8.5pt;font-weight:bold;color:{color};"
                    "background:transparent;")
                # sparkline 用板块内不可得日线——退化为趋势点（红涨绿跌）
                spark.set_data([0, p / 2, p] if p else [])

    # ================================================================ 自选
    def _rebuild_watch_rows(self) -> None:
        while self.watch_lay.count():
            item = self.watch_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._stock_rows = []
        for entry in self.ctx.cfg.get_watchlist()[:10]:
            row = StockRow(entry["code"], entry.get("name") or "")
            row.mousePressEvent = lambda ev, r=row: self.open_stock.emit(
                r.code, r.name)
            self.watch_lay.addWidget(row)
            self._stock_rows.append(row)
        self.watch_lay.addStretch(1)

    def _refresh_watch(self) -> None:
        entries = [x["code"] for x in self.ctx.cfg.get_watchlist()][:10]
        if [r.code for r in self._stock_rows] != entries:
            self._rebuild_watch_rows()
        if self._quotes_in_flight or not self._stock_rows:
            return
        self._quotes_in_flight = True
        codes = [r.code for r in self._stock_rows]
        submit(self.ctx.get_quotes, codes, on_done=self._on_quotes,
               on_err=lambda m: setattr(self, "_quotes_in_flight", False))

    def _on_quotes(self, quotes: dict) -> None:
        self._quotes_in_flight = False
        for row in self._stock_rows:
            row.update_quote(quotes.get(row.code))

    # ================================================================ 快讯
    def _refresh_news(self) -> None:
        submit(self.ctx.em.get_fast_news, 12, on_done=self._on_news,
               on_err=lambda m: None)

    def _on_news(self, items: list) -> None:
        self._news_items = items or []
        self.news_list.clear()
        for n in self._news_items[:10]:
            QListWidgetItem(f"{n.date[-8:]}  {n.title[:40]}", self.news_list)
            item = self.news_list.item(self.news_list.count() - 1)
            item.setData(Qt.UserRole, n)

    def _on_news_double(self, item: QListWidgetItem) -> None:
        n = item.data(Qt.UserRole)
        if n is not None:
            self.ai_panel.analyze_news(n)

    # ================================================================ 龙虎榜
    def _refresh_lhb(self) -> None:
        submit(self.ctx.em.get_lhb_board, on_done=self._on_lhb,
               on_err=lambda m: None)

    def _on_lhb(self, rows: list) -> None:
        self._lhb_items = rows or []
        self.lhb_list.clear()
        for r in self._lhb_items[:15]:
            amt = (r.get("net_amt") or 0) / 1e8
            sign = "+" if amt >= 0 else ""
            QListWidgetItem(
                f"{sign}{amt:.2f}亿  {r.get('name', '')[:7]} "
                f"{(r.get('reason') or '')[:12]}", self.lhb_list)
            item = self.lhb_list.item(self.lhb_list.count() - 1)
            item.setData(Qt.UserRole, r)

    def _on_lhb_double(self, item: QListWidgetItem) -> None:
        r = item.data(Qt.UserRole)
        if r:
            self.open_stock.emit(r["code"], r.get("name", ""))
