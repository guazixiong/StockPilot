"""个股详情 v1.4：报价头条 + Tab(分时/日K/周K/月K/AI报告) + 右栏五档/指标。

AI 诊断就地查看：点「AI 诊断」直接切到 AI 报告 Tab 并自动流式分析，
支持在该 Tab 内追问（多轮）与重新分析。
"""
from __future__ import annotations

import html
import threading

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from ...core import indicators, prompt
from ...core.ai.client import AiError
from ...core.models import KLine, Quote
from ..ai_stream import append_stream, finalize_stream
from ..kline_chart import CandleChart
from ..minute_chart import MinuteChart
from ..theme import FLAT, UP
from ..workers import submit


def card() -> QFrame:
    f = QFrame()
    f.setProperty("card", True)
    return f


class DetailDialog(QDialog):
    ai_requested = Signal(str, str)  # 兼容：跳转主界面 AI 页（卡片入口用）
    _ai_delta = Signal(str, str)     # 流式增量：Worker 线程 → 主线程（自动队列）

    PERIODS = [("分时", None), ("日K", "day"), ("周K", "week"), ("月K", "month")]
    TAB_AI_INDEX = 4  # 分时/日K/周K/月K 之后的 AI 报告

    def __init__(self, ctx, code: str, name: str, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.code = code
        self.name = name or code
        self.quote: Quote = Quote(code=code)
        self.klines: dict = {}       # period -> list[KLine]
        self.minute = None
        self.news: list = []
        self.lhb_hit = None   # 龙虎榜上榜信息（v4.4）
        self.ind: dict = {}
        self._loading = False
        self._ai_running = False
        self._ai_stop = threading.Event()
        self._ai_history: list = []
        self._ai_delta.connect(self._on_ai_delta)
        self._build_ui()
        self.load()
        self._offer_restore()   # v5.1：有暂存会话则显示一键回看条
        # 分时/行情实时刷新：交易时段每 15 秒自动拉一次（分时 Tab 时含分时）
        from PySide6.QtCore import QTimer
        from ...core.monitor import is_trade_time
        self._live_timer = QTimer(self)
        self._live_timer.timeout.connect(self._live_tick)
        self._live_timer.start(15000)

    def _live_tick(self) -> None:
        """交易时段自动刷新：分时 Tab → 报价+分时；其他 Tab → 仅报价头条。"""
        from ...core.monitor import is_trade_time
        if not is_trade_time() or self._loading:
            return
        on_minute_tab = (self.tabs.currentIndex() == 0)
        submit(self._load_live, on_minute_tab,
               on_done=self._on_live, on_err=lambda m: None)

    def _load_live(self, with_minute: bool):
        quotes = self.ctx.get_quotes([self.code])
        minute = None
        if with_minute:
            try:
                minute = self.ctx.tencent.get_minute(self.code)
            except Exception:  # noqa: BLE001 分时不可用不阻塞报价
                minute = None
        return quotes.get(self.code), minute

    def _on_live(self, data) -> None:
        quote, minute = data
        if quote is not None:
            self.quote = quote
            self._update_header()
            self._update_ladder()
        if minute is not None:
            self.minute = minute
            if self.tabs.currentIndex() == 0:
                self.minute_chart.set_series(minute)

    def _on_ai_delta(self, kind: str, text: str) -> None:
        """主线程槽：流式增量追加（Signal 跨线程自动队列，替代 singleShot）。"""
        append_stream(self.ai_view, kind, text)

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        self.setWindowTitle(f"{self.name} ({self.code})")
        self.resize(1200, 800)
        # v5.1：窗口几何临时态——记住上次大小/位置（QSettings 原生，独立于 config.json）
        self._geo = QSettings("StockPilot", "DetailDialog")
        g = self._geo.value("geometry")
        if g is not None:
            try:
                self.restoreGeometry(g)
            except Exception:  # noqa: BLE001 脏数据回退默认
                self.resize(1200, 800)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        # ---- 报价头条 ----
        head = card()
        h = QVBoxLayout(head)
        h.setContentsMargins(16, 12, 16, 10)
        row1 = QHBoxLayout()
        self.title_label = QLabel(
            f"<b style='font-size:15pt'>{self.name}</b> "
            f"<span style='color:{FLAT}'>{self.code}</span>")
        self.price_label = QLabel("加载中…")
        self.price_label.setStyleSheet("font-size:17pt;font-weight:bold;")
        self.ai_btn = QPushButton("AI 诊断")
        self.ai_btn.clicked.connect(self.open_ai_tab)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setProperty("secondary", True)
        self.refresh_btn.clicked.connect(self.load)
        row1.addWidget(self.title_label)
        row1.addWidget(self.price_label, 1)
        row1.addWidget(self.refresh_btn)
        from PySide6.QtWidgets import QComboBox
        # v5.2：副图多选——同时显示 MACD/KDJ/RSI（可勾选菜单，不再是单选下拉）
        from PySide6.QtWidgets import QToolButton
        self.sub_btn = QToolButton()
        self.sub_btn.setText("副图: MACD")
        self.sub_btn.setToolTip(
            "副图指标可同时勾选多个（堆叠显示，各占一格独立坐标）"
            + chr(10) + "日K / 周K / 月K 三周期同步生效")
        self.sub_btn.setPopupMode(QToolButton.InstantPopup)
        from PySide6.QtWidgets import QMenu
        self.sub_menu = QMenu(self.sub_btn)
        self._sub_actions = {}
        for name in ("MACD", "KDJ", "RSI"):
            act = self.sub_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == "MACD")
            act.toggled.connect(lambda checked, _n=name: self._on_sub_toggle(_n, checked))
            self._sub_actions[name] = act
        self.sub_menu.addSeparator()
        act_all = self.sub_menu.addAction("全部")
        act_all.triggered.connect(lambda: self._set_all_subs(True))
        act_none = self.sub_menu.addAction("清空")
        act_none.triggered.connect(lambda: self._set_all_subs(False))
        self.sub_btn.setMenu(self.sub_menu)
        self.sub_btn.setFixedWidth(110)
        row1.addWidget(self.sub_btn)
        row1.addWidget(self.ai_btn)
        h.addLayout(row1)
        self.stats_label = QLabel(" ")
        self.stats_label.setProperty("sub", True)
        h.addWidget(self.stats_label)
        lay.addWidget(head)

        # ---- 主体：左图表 Tab + 右栏 ----
        body = QHBoxLayout()
        body.setSpacing(10)
        lay.addLayout(body, 1)

        self.tabs = QTabWidget()
        self.minute_chart = MinuteChart()
        self.charts: dict = {}
        for label, period in self.PERIODS:
            if period is None:
                w = self.minute_chart
            else:
                w = CandleChart()
                self.charts[period] = w
            self.tabs.addTab(w, label)
        # ---- AI 报告 Tab ----
        self._build_ai_tab()
        self.tabs.currentChanged.connect(self._on_tab_change)
        body.addWidget(self.tabs, 1)

        right = QWidget()
        right.setFixedWidth(252)
        r = QVBoxLayout(right)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(10)

        wu = card()  # 五档盘口
        wu_l = QVBoxLayout(wu)
        wu_l.setContentsMargins(12, 10, 12, 10)
        wu_title = QLabel("五档盘口")
        wu_title.setProperty("title", True)
        wu_l.addWidget(wu_title)
        grid = QGridLayout()
        grid.setVerticalSpacing(3)
        self.ask_labels: dict = {}
        self.bid_labels: dict = {}
        from ..theme import DOWN
        for i in range(5, 0, -1):
            a = QLabel("—")
            a.setStyleSheet(f"color:{DOWN};")
            self.ask_labels[i] = a
            grid.addWidget(QLabel(f"卖{i}"), 5 - i, 0)
            grid.addWidget(a, 5 - i, 1, Qt.AlignRight)
        grid.addWidget(QLabel("　"), 5, 0)
        for i in range(1, 6):
            b = QLabel("—")
            b.setStyleSheet(f"color:{UP};")
            self.bid_labels[i] = b
            grid.addWidget(QLabel(f"买{i}"), 5 + i, 0)
            grid.addWidget(b, 5 + i, 1, Qt.AlignRight)
        grid.setColumnStretch(2, 1)
        wu_l.addLayout(grid)
        r.addWidget(wu)

        ind = card()  # 指标
        ind_l = QVBoxLayout(ind)
        ind_l.setContentsMargins(12, 10, 12, 10)
        ind_title = QLabel("技术指标 / 形态")
        ind_title.setProperty("title", True)
        ind_l.addWidget(ind_title)
        self.form_label = QLabel("—")
        self.form_label.setWordWrap(True)
        self.form_label.setProperty("sub", True)
        ind_l.addWidget(self.form_label)
        r.addWidget(ind)
        self.cyq_label = QLabel("筹码：—")
        self.cyq_label.setWordWrap(True)
        self.cyq_label.setStyleSheet("color:#c9b06a;font-size:8pt;background:transparent;")
        r.addWidget(self.cyq_label)
        r.addStretch(1)
        body.addWidget(right)

        # ---- 底部消息 ----
        news_card = card()
        n = QVBoxLayout(news_card)
        n.setContentsMargins(12, 8, 12, 8)
        self.news_label = QLabel("近期消息：加载中…")
        self.news_label.setWordWrap(True)
        self.news_label.setProperty("hint", True)
        n.addWidget(self.news_label)
        lay.addWidget(news_card)

        tip = QLabel("AI 分析仅供参考，不构成投资建议。")
        tip.setProperty("hint", True)
        lay.addWidget(tip)

    def _build_ai_tab(self) -> None:
        """详情窗内嵌的 AI 报告页：就地流式 + 追问。"""
        from PySide6.QtCore import Signal as _Sig

        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(2, 8, 2, 4)
        v.setSpacing(8)

        bar = QHBoxLayout()
        self.ai_status = QLabel(" ")
        self.ai_status.setProperty("hint", True)
        self.debate_btn = QPushButton("🐂熊 牛熊辩论")
        self.debate_btn.setProperty("secondary", True)
        self.debate_btn.setToolTip(
            "多角色对抗式研判：多头立论 → 空头反驳 → 中性首席裁决\n"
            "（借鉴 TradingAgents 多智能体辩论，桌面轻量版）")
        self.debate_btn.clicked.connect(self.run_debate)
        self.committee_btn = QPushButton("👥 大师投委会")
        self.committee_btn.setProperty("secondary", True)
        self.committee_btn.setToolTip(
            "多位投资大师独立分析后加权共识（v4.5，借鉴 Augur）：\n"
            "段永平/张磊/李录/但斌/巴菲特/芒格/林奇/达利欧…各自打分，\n"
            "多空分布+分歧清单+一句话裁决")
        self.committee_btn.clicked.connect(self.run_committee)
        self.rerun_btn = QPushButton("重新分析")
        self.rerun_btn.setProperty("secondary", True)
        self.rerun_btn.clicked.connect(self.run_diagnosis)
        self.ai_stop_btn = QPushButton("停止")
        self.ai_stop_btn.setProperty("secondary", True)
        self.ai_stop_btn.hide()
        self.ai_stop_btn.clicked.connect(self._ai_stop.set)
        bar.addWidget(self.ai_status, 1)
        bar.addWidget(self.ai_stop_btn)
        bar.addWidget(self.debate_btn)
        bar.addWidget(self.committee_btn)
        bar.addWidget(self.rerun_btn)
        v.addLayout(bar)

        self.ai_view = QTextBrowser()
        self.ai_view.setOpenExternalLinks(True)
        v.addWidget(self.ai_view, 1)

        input_bar = QHBoxLayout()
        self.ai_input = QLineEdit()
        self.ai_input.setPlaceholderText(
            "向 AI 追问这只股票（回车发送），如：现在适合加仓吗？")
        self.ai_input.returnPressed.connect(self._send_followup)
        self.ai_send_btn = QPushButton("发送")
        self.ai_send_btn.clicked.connect(self._send_followup)
        input_bar.addWidget(self.ai_input, 1)
        input_bar.addWidget(self.ai_send_btn)
        v.addLayout(input_bar)

        tip = QLabel("报告由 AI 生成，仅供参考，不构成投资建议。")
        tip.setProperty("hint", True)
        v.addWidget(tip)
        self.tabs.addTab(w, "AI 报告")

    # ------------------------------------------------------------ 加载
    def load(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._set_period_label()
        submit(self._load_data, on_done=self._on_loaded, on_err=self._on_error)

    def _set_period_label(self) -> None:
        idx = self.tabs.currentIndex()
        if idx < len(self.PERIODS):
            name = self.PERIODS[idx][0]
        else:
            name = "AI 报告"
        self.refresh_btn.setText(f"刷新（{name}）")

    def _load_data(self):
        quotes = self.ctx.get_quotes([self.code])
        try:
            minute = self.ctx.tencent.get_minute(self.code)
        except Exception:  # noqa: BLE001 分时不可用不阻塞
            minute = None
        klines = self.ctx.get_kline(self.code, "day", 250)
        try:
            news = self.ctx.em.get_news(self.code, 8)
        except Exception:  # noqa: BLE001
            news = []
        # 龙虎榜上榜信息（v4.4）：接口失败不阻塞个股加载
        try:
            lhb = self.ctx.em.get_lhb_board(size=300)
        except Exception:  # noqa: BLE001
            lhb = []
        lhb_hit = next((x for x in lhb if x["code"] == self.code), None)
        return quotes.get(self.code), minute, klines, news, lhb_hit

    def _on_loaded(self, data) -> None:
        self._loading = False
        quote, minute, klines, news, lhb_hit = data
        self.lhb_hit = lhb_hit
        self.quote = quote or Quote(code=self.code)
        self.minute = minute
        self.klines["day"] = klines or []
        self.news = news or []
        self.ind = indicators.analyze(self.klines["day"])
        self._update_header()
        self._update_ladder()
        self._update_ind()
        self.minute_chart.set_series(minute)
        from ...core import cyq as _cyq_mod
        _curve = _cyq_mod.cyq_curve(self.klines["day"])
        self.charts["day"].set_data(self.klines["day"], self._support_levels(),
                                    cyq=_curve)
        self._update_cyq_label()
        if news:
            self.news_label.setText("近期消息: " + " ｜ ".join(
                f"{n.date} {n.title}" for n in news[:5]))
        else:
            self.news_label.setText("近期消息：暂无")

    def _support_levels(self) -> dict:
        """当前点位的压力/支撑位：均线 + 布林轨道 + 近60日高低点。"""
        levels = {}
        ind = self.ind or {}
        price = self.quote.price
        for key, base in (("ma5", "MA5"), ("ma10", "MA10"), ("ma20", "MA20"),
                          ("ma60", "MA60"), ("boll_up", "布林上轨"),
                          ("boll_mid", "布林中轨"), ("boll_low", "布林下轨")):
            v = ind.get(key)
            if isinstance(v, float) and v > 0:
                tag = "压力" if (price and v > price) else "支撑"
                levels[f"{tag}·{base}"] = round(v, 2)
        kl = self.klines.get("day") or []
        if len(kl) >= 60:
            levels["压力·60日最高"] = round(max(k.high for k in kl[-60:]), 2)
            levels["支撑·60日最低"] = round(min(k.low for k in kl[-60:]), 2)
        return levels

    def _update_header(self) -> None:
        from ..theme import DOWN
        q = self.quote
        chg = q.change_pct or 0
        color = UP if chg > 0 else (DOWN if chg < 0 else FLAT)
        self.price_label.setText(
            f"<span style='color:{color}'>{q.price if q.price else '—'}</span> "
            f"<span style='font-size:11pt;color:{color}'>{chg:+.2f}%</span>")
        stats = (
            f"今开 {q.open or '—'}　昨收 {q.prev_close or '—'}　最高 {q.high or '—'}　"
            f"最低 {q.low or '—'}　换手 {q.turnover_rate or '—'}%　量比 {q.volume_ratio or '—'}　"
            f"振幅 {q.amplitude or '—'}%　PE {q.pe or '—'}　PB {q.pb or '—'}　"
            f"总市值 {q.total_mv or '—'}亿　{q.time or ''}")
        lhb = getattr(self, "lhb_hit", None)
        if lhb and lhb.get("net_amt") is not None:
            amt = lhb["net_amt"] / 1e8
            stats = (f"<b style='color:#f0a030'>🐉龙虎榜上榜</b> "
                     f"净买 <b>{amt:+.2f}亿</b>（{lhb.get('date','')}·"
                     f"{(lhb.get('reason') or '')[:16]}）　" + stats)
        self.stats_label.setText(stats)

    def _update_ladder(self) -> None:
        q = self.quote
        for i in range(1, 6):
            bp = getattr(q, f"bid{i}_price")
            bv = getattr(q, f"bid{i}_vol")
            ap = getattr(q, f"ask{i}_price")
            av = getattr(q, f"ask{i}_vol")
            self.bid_labels[i].setText(
                f"{bp:.2f}  {bv:.0f}" if bp is not None else "—")
            self.ask_labels[i].setText(
                f"{ap:.2f}  {av:.0f}" if ap is not None else "—")

    def _update_ind(self) -> None:
        forms = indicators.signals_text(self.ind)
        self.form_label.setText("；".join(forms) if forms else "无明显形态信号")

    def _on_error(self, msg: str) -> None:
        """v7.2：异常不进主布局——价格位回退占位符，错误走弹窗。"""
        self._loading = False
        from ..err import fail_hint, show_error
        self.price_label.setText("<span style='color:#5C6B84'>—</span>")
        show_error(self, "行情加载失败", msg)
        fail_hint(self.news_label, "加载失败，可点刷新重试")

    def _on_sub_toggle(self, name: str, checked: bool) -> None:
        """v5.2：多选副图——选中集合同步到三周期图表。
        防呆：用户取消的是最后一个勾选项时自动勾回（点选路径不允许全空；
        批量路径（_set_all_subs「清空」= 纯K线合法态）置 _bulk 跳过防呆）。"""
        selected = [n for n, a in self._sub_actions.items() if a.isChecked()]
        if not checked and not selected and not getattr(self, "_bulk_subs", False):
            self._sub_actions[name].setChecked(True)   # 勾回，recurse 一次生效
            return
        for chart in self.charts.values():
            chart.set_sub_modes(selected)
        self.sub_btn.setText(f"副图: {'/'.join(selected) if selected else '无'}")

    def _set_all_subs(self, on: bool) -> None:
        """「全部/清空」菜单：清空=纯K线（合法态）；全空时副图区收起。"""
        self._bulk_subs = True
        try:
            for name in ("MACD", "KDJ", "RSI"):
                self._sub_actions[name].setChecked(on)
        finally:
            self._bulk_subs = False
        selected = [n for n, a in self._sub_actions.items() if a.isChecked()]
        for chart in self.charts.values():
            chart.set_sub_modes(selected)
        self.sub_btn.setText(f"副图: {'/'.join(selected) if selected else '无'}")

    def _update_cyq_label(self) -> None:
        """筹码摘要：获利盘/平均成本/集中度/现价附近筹码。"""
        from ...core import cyq as _cyq_mod
        d = _cyq_mod.cyq_distribution(self.klines.get("day") or [])
        if d["profit_ratio"] is None:
            self.cyq_label.setText("筹码：数据不足")
            return
        cur = self.quote.price
        pos = ""
        if cur and d["avg_cost"]:
            pos = "　获利" if cur >= d["avg_cost"] else "　套牢"
        self.cyq_label.setText(
            f"筹码分布 — 获利盘 {d['profit_ratio']:.0f}%　平均成本 {d['avg_cost']}"
            f"　90%成本 {d['cost_low']}~{d['cost_high']}"
            f"　集中度 {d['concentration']}%　现价±5%筹码 {d['near_current']}%{pos}")

    # ------------------------------------------------------------ Tab 切换
    def _on_tab_change(self, idx: int) -> None:
        self._set_period_label()
        if idx >= len(self.PERIODS):
            return  # AI 报告 Tab
        period = self.PERIODS[idx][1]
        if period is None:
            self.minute_chart.set_series(self.minute)
            return
        if period in self.klines and self.klines[period]:
            # v5.1.1：缓存切回时同样要带 levels/筹码（此前只 set_data(klines)
            # → set_data 内 levels=levels or {} 清空压力位——分时→月K 切回即丢）
            kls = self.klines[period]
            ind = indicators.analyze(kls)
            price = self.quote.price or (kls[-1].close if kls else None)
            levels = {}
            for key, base in (("ma5", "MA5"), ("ma10", "MA10"),
                              ("ma20", "MA20"), ("boll_up", "布林上轨"),
                              ("boll_low", "布林下轨")):
                v = ind.get(key)
                if isinstance(v, float) and v > 0 and price:
                    tag = "压力" if v > price else "支撑"
                    levels[f"{tag}·{base}"] = round(v, 2)
            if len(kls) >= 60:
                levels["压力·60期最高"] = round(max(k.high for k in kls[-60:]), 2)
                levels["支撑·60期最低"] = round(min(k.low for k in kls[-60:]), 2)
            from ...core import cyq as _cyq_mod
            self.charts[period].set_data(kls, levels,
                                         cyq=_cyq_mod.cyq_curve(kls))
            return
        submit(self._load_kline, period, on_done=self._on_kline_loaded,
               on_err=self._on_error)

    def _load_kline(self, period: str):
        return period, self.ctx.get_kline(self.code, period, 250)

    def _on_kline_loaded(self, result) -> None:
        period, kls = result
        self.klines[period] = kls or []
        # 周/月K的压力支撑也基于该周期自身均线/高低点计算
        ind = indicators.analyze(self.klines[period])
        price = self.quote.price or (self.klines[period][-1].close
                                     if self.klines[period] else None)
        levels = {}
        for key, base in (("ma5", "MA5"), ("ma10", "MA10"), ("ma20", "MA20"),
                          ("boll_up", "布林上轨"), ("boll_low", "布林下轨")):
            v = ind.get(key)
            if isinstance(v, float) and v > 0 and price:
                tag = "压力" if v > price else "支撑"
                levels[f"{tag}·{base}"] = round(v, 2)
        kl = self.klines[period]
        if len(kl) >= 60:
            levels["压力·60期最高"] = round(max(k.high for k in kl[-60:]), 2)
            levels["支撑·60期最低"] = round(min(k.low for k in kl[-60:]), 2)
        from ...core import cyq as _cyq_mod
        _c = _cyq_mod.cyq_curve(self.klines[period])
        self.charts[period].set_data(self.klines[period], levels, cyq=_c)
        self._update_cyq_label()

    # ------------------------------------------------------------ AI 报告
    def open_ai_tab(self) -> None:
        """点「AI 诊断」：切到 AI 报告 Tab；无内容时自动开始分析。"""
        self.tabs.setCurrentIndex(self.TAB_AI_INDEX)
        if not self._ai_history and not self._ai_running:
            self.run_diagnosis()

    def _ai_ready(self) -> bool:
        if self.ctx.ai_config() is not None:
            return True
        self.ai_view.clear()
        self.ai_view.setHtml(
            "<div style='color:#8FA9C7;line-height:1.9'>"
            "<b style='color:#e8ebf1'>AI 尚未配置</b><br>"
            "AI 报告需要先配置一个 OpenAI 协议的模型服务（如 DeepSeek）。<br><br>"
            "① 在主界面左侧打开 <b>💡 AI 分析</b> 或 <b>⚙ 设置</b> 页面<br>"
            "② 选择厂商预设 → 填入 API Key 与模型名 → 点「测试连接」→「保存配置」<br>"
            "③ 回到本窗口，点「重新分析」即可生成报告</div>")
        self.ai_status.setText("AI 未配置 —— 请先到主界面完成 AI 配置")
        return False

    def run_debate(self) -> None:
        """牛熊辩论（v4.4）：多角色对抗研判，逐轮流式呈现，终局裁决。"""
        from ...core import debate
        if self._ai_running or not self._ai_ready():
            return
        if not self.klines.get("day"):
            self.ai_status.setText("行情数据未加载完成，请稍候…")
            return
        try:
            client = self.ctx.ai_client()
        except RuntimeError as exc:
            self.ai_status.setText(str(exc))
            return
        context = prompt.build_stock_context(self.quote, self.ind,
                                             self.klines["day"], self.news)
        # 3 轮标准辩论（牛/熊/裁决）；陈词原文在轮间由后台线程回填
        self._debate_plan = debate.build_debate_rounds(
            self._stock_context(), rounds=3)
        self._debate_replies: list = []
        self._ai_history = []          # 辩论结束后的可追问上下文在 _on_debate_done 重建
        self._ai_running = True
        self._ai_stop.clear()
        self.ai_stop_btn.show()
        self.ai_view.clear()
        append_stream(self.ai_view, "system",
                      "<b>🐂熊 牛熊辩论</b>（多头立论 → 空头立论 → ⚖️首席裁决）",
                      raw=True)
        append_stream(self.ai_view, "ai_head", "")
        self.ai_status.setText("辩论进行中：第 1/3 轮…")
        submit(self._do_debate_round, client, 0,
               on_done=self._on_debate_round, on_err=self._on_ai_err)

    def _do_debate_round(self, client, idx):
        """后台逐轮执行：轮到裁决时携带此前真实陈词。"""
        from ...core import debate
        plan = self._debate_plan
        role, _title, msgs = plan[idx]
        if role == "judge":
            nl2 = chr(10) * 2
            prior = nl2.join(
                f"{plan[k][1]}：{self._debate_replies[k]}"
                for k in range(idx))
            msgs = [{"role": "system", "content": debate.JUDGE_SYS},
                    {"role": "user",
                     "content": ("以下是双方完整陈词记录。请给出最终裁决。"
                                 + nl2 + prior)}]
        return client.chat(msgs, stream=True,
                           on_delta=lambda k, t: self._ai_delta.emit(k, t),
                           stop_event=self._ai_stop)

    def _on_debate_round(self, reply: str) -> None:
        """一轮完成：记录陈词 → 渲染分段 → 下一轮或收尾。"""
        from ...core import debate
        idx = len(self._debate_replies)
        role, title, _ = self._debate_plan[idx]
        self._debate_replies.append(reply)
        if reply:
            self._ai_history.extend([
                {"role": "user", "content": title},
                {"role": "assistant", "content": reply},
            ])
        finalize_stream(self.ai_view)
        nxt = idx + 1
        if nxt < len(self._debate_plan):
            self.ai_status.setText(f"辩论进行中：第 {nxt + 1}/3 轮…")
            append_stream(self.ai_view, "ai_head", "")
            try:
                client = self.ctx.ai_client()
            except RuntimeError as exc:
                self._on_ai_err(str(exc))
                return
            submit(self._do_debate_round, client, nxt,
                   on_done=self._on_debate_round, on_err=self._on_ai_err)
        else:
            self._ai_running = False
            self.ai_stop_btn.hide()
            append_stream(self.ai_view, "disclaimer", "", raw=True)
            self.ai_status.setText("辩论完成 —— 裁决已出，可在下方继续追问")
            self._save_ai_session("牛熊辩论")

    # ------------------------------------------------------------ 大师投委会（v4.5）
    def run_committee(self) -> None:
        """大师投委会：12 位大师独立分析 → 加权共识（借鉴 Augur）。"""
        from ...core import masters as mc_mod
        if self._ai_running or not self._ai_ready():
            return
        if not self.klines.get("day"):
            self.ai_status.setText("行情数据未加载完成，请稍候…")
            return
        try:
            client = self.ctx.ai_client()
        except RuntimeError as exc:
            self.ai_status.setText(str(exc))
            return
        self._committee_list = mc_mod.masters_by_keys()
        self._committee_parsed: dict = {}
        self._committee_history: list = []
        self._ai_history = []
        self._ai_running = True
        self._ai_stop.clear()
        self.ai_stop_btn.show()
        self.ai_view.clear()
        n = len(self._committee_list)
        append_stream(self.ai_view, "system",
                      f"<b>👥 大师投委会</b>（{n} 位独立分析 → 加权共识 → "
                      f"分歧清单）", raw=True)
        append_stream(self.ai_view, "ai_head", "")
        self.ai_status.setText(f"投委会进行中：第 1/{n} 位…")
        submit(self._do_committee_one, client, 0,
               on_done=self._on_committee_one, on_err=self._on_ai_err)

    def _do_committee_one(self, client, idx):
        """后台逐师执行（12 位为串行节奏——每师完整呈现后再下一位）。"""
        from ...core import masters as mc_mod
        m = self._committee_list[idx]
        msgs = mc_mod.build_master_prompt(m, self._stock_context())
        return client.chat(msgs, stream=True,
                          on_delta=lambda k, t: self._ai_delta.emit(k, t),
                          stop_event=self._ai_stop)

    def _on_committee_one(self, reply: str) -> None:
        """一位大师完成：解析结论 → 共识累计 → 下一位或收尾。"""
        from ...core import masters as mc_mod
        idx = len(self._committee_parsed)
        m = self._committee_list[idx]
        parsed = mc_mod.parse_master_reply(reply)
        self._committee_parsed[m.key] = parsed
        finalize_stream(self.ai_view)
        # 结论标签随流派着色：多红/空绿/观望灰
        sig = parsed["signal"]
        color = {"看多": "#FF5E77", "看空": "#41D8A6", "观望": "#8FA9C7"}[sig]
        nl = chr(10)
        append_stream(self.ai_view, "system",
                      f"<div style='margin:2px 0 10px 0'><b style='color:{color}'>"
                      f"{m.name}：{sig}（{parsed['score']:+.0f}分）</b> "
                      f"<span style='color:#8FA9C7;font-size:9pt'>"
                      f"{parsed['reason'][:60]}</span></div>", raw=True)
        if reply:
            self._committee_history.extend([
                {"role": "user", "content": f"（{m.name}的独立分析）"},
                {"role": "assistant", "content": reply}])
        nxt = idx + 1
        total = len(self._committee_list)
        if nxt < total:
            self.ai_status.setText(f"投委会进行中：第 {nxt + 1}/{total} 位…")
            append_stream(self.ai_view, "ai_head", "")
            try:
                client = self.ctx.ai_client()
            except RuntimeError as exc:
                self._on_ai_err(str(exc))
                return
            submit(self._do_committee_one, client, nxt,
                   on_done=self._on_committee_one, on_err=self._on_ai_err)
        else:
            cons = mc_mod.compute_consensus(self._committee_parsed,
                                            self._committee_list)
            self._ai_history = ([
                {"role": "user",
                 "content": f"（大师投委会对 {self.name} 的完整分析记录）"}]
                + self._committee_history)
            append_stream(self.ai_view, "system",
                          "<div style='margin:10px 0 4px 0'>"
                          "<span style='color:#8E6DFF'>━━ 投委会共识 ━━</span>"
                          "</div>", raw=True)
            append_stream(self.ai_view, "system",
                          f"<div style='line-height:1.9'>"
                          f"<b style='font-size:11pt'>裁决：{cons.verdict}</b>"
                          f"　加权 {cons.weighted_score:+.1f} 分"
                          f"（{cons.distribution['看多']}多 / "
                          f"{cons.distribution['观望']}观望 / "
                          f"{cons.distribution['看空']}空）"
                          + (f"<br>分歧意见：<br>" + ("<br>".join(
                              f"· {d}" for d in cons.dissent[:4]))
                             if cons.dissent else "")
                          + "</div>", raw=True)
            append_stream(self.ai_view, "disclaimer", "", raw=True)
            self._ai_running = False
            self.ai_stop_btn.hide()
            self.ai_status.setText("投委会完成 —— 裁决已出，可在下方继续追问")
            self._save_ai_session("大师投委会")

    def _stock_context(self) -> str:
        """个股 AI 上下文：基础数据包 + 龙虎榜上榜信息（v4.4）。"""
        ctx_text = prompt.build_stock_context(
            self.quote, self.ind, self.klines.get("day"), self.news)
        lhb = getattr(self, "lhb_hit", None)
        if lhb and lhb.get("net_amt") is not None:
            ctx_text += (f"{chr(10)}龙虎榜: {lhb.get('date', '')}上榜，净买入 "
                         f"{lhb['net_amt'] / 1e8:+.2f}亿，"
                         f"上榜原因：{lhb.get('reason') or '—'}")
        return ctx_text

    def run_diagnosis(self) -> None:
        if self._ai_running or not self._ai_ready():
            return
        if not self.klines.get("day"):
            self.ai_status.setText("行情数据未加载完成，请稍候…")
            return
        try:
            client = self.ctx.ai_client()
        except RuntimeError as exc:
            self.ai_status.setText(str(exc))
            return
        context = self._stock_context()
        messages = prompt.build_messages("个股诊断", context, "")
        # v5.2.3（问题2修复）：重新分析不再清空对话——保留全部历史
        #（异常后点「重新分析」不应丢失之前的问答），新报告以分隔线开新轮。
        # 仅在完全无历史（首次诊断）时才走 new_session（清空视图）。
        first_time = not self._ai_history
        self._ai_history = list(messages) if first_time else self._ai_history + [
            {"role": "user", "content": "请对该股做全面诊断"}]
        self._start_ai(client, self._ai_history,
                       header="请对该股做全面诊断",
                       new_session=first_time)

    def _send_followup(self) -> None:
        text = self.ai_input.text().strip()
        if not text or not self._ai_ready():
            return
        if self._ai_running:
            self.ai_status.setText("AI 正在输出，请稍候或点「停止」…")
            return
        try:
            client = self.ctx.ai_client()
        except RuntimeError as exc:
            self.ai_status.setText(str(exc))
            return
        if not self._ai_history:
            # 未生成过报告直接追问：先构建个股上下文
            if not self.klines.get("day"):
                return
            base = prompt.build_messages("个股诊断", self._stock_context(), "")
            self._ai_history = list(base)
        self._ai_history.append({"role": "user", "content": text})
        self._start_ai(client, self._ai_history, header=f"{text}")

    def _start_ai(self, client, messages, header: str,
                  new_session: bool = False) -> None:
        """new_session=True（首次诊断/重新分析）开新报告；追问时保留历史。"""
        self._ai_running = True
        self._ai_stop.clear()
        self.ai_stop_btn.show()
        self.ai_input.clear()
        if new_session:
            self.ai_view.clear()
        else:
            append_stream(self.ai_view, "system",
                          "<div style='margin:10px 0 4px 0'>"
                          "<span style='color:#8E6DFF'>━━ 新的一轮 ━━</span></div>",
                          raw=True)
        append_stream(self.ai_view, "user", header)
        append_stream(self.ai_view, "ai_head", "")
        self.ai_status.setText("AI 分析中…（若服务商繁忙会自动重试，请稍候）")
        submit(self._do_ai, client, messages, on_done=self._on_ai_done,
               on_err=self._on_ai_err)

    def _do_ai(self, client, messages):
        return client.chat(messages, stream=True,
                           on_delta=lambda k, t: self._ai_delta.emit(k, t),
                           stop_event=self._ai_stop)

    def _emit_delta(self, kind: str, text: str) -> None:
        pass  # 已由 Signal _ai_delta 替代（singleShot 跨线程不可靠）

    # ------------------------------------------------------------ 会话暂存（v5.1）
    def closeEvent(self, event) -> None:
        """关窗即存几何（v5.1）+ 在跑任务停止。"""
        try:
            self._geo.setValue("geometry", self.saveGeometry())
        except Exception:  # noqa: BLE001
            pass
        self._ai_stop.set()
        super().closeEvent(event)

    def _save_ai_session(self, kind: str) -> None:
        """误关窗口防丢（v5.1）：AI 分析完成即落盘（滚动覆盖，TTL 7 天）。"""
        try:
            from ...core import session_store
            session_store.save_session(
                self.code, self.name, kind, self.ai_view.toHtml(),
                self._ai_history)
        except Exception:  # noqa: BLE001 暂存失败不影响分析本体
            pass

    def _offer_restore(self) -> None:
        """重开窗口：有最近会话 → 顶部一键恢复条（不自动覆盖新分析）。"""
        try:
            from ...core import session_store
            data = session_store.load_session(self.code)
        except Exception:  # noqa: BLE001
            return
        if not data or not data.get("html"):
            return
        hrs = session_store.age_hours(data.get("ts", ""))
        when = (f"{hrs:.0f} 小时前" if hrs >= 1 else
                (f"{hrs * 60:.0f} 分钟前" if hrs > 0.016 else "刚刚"))
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(f"🕘 上次 AI {data.get('kind', '分析')}（{when}）"
                     f"已暂存 —— 关窗不丢")
        lbl.setProperty("hint", True)
        btn = QPushButton("回看上次分析")
        btn.setProperty("secondary", True)
        btn.clicked.connect(lambda: self._restore_session(data))
        h.addWidget(lbl, 1)
        h.addWidget(btn)
        # 插在 AI Tab 顶部（报告视图上方）
        v = self.ai_view.parentWidget()
        if v is not None and v.layout() is not None:
            v.layout().insertWidget(0, bar)

    def _restore_session(self, data: dict) -> None:
        """恢复暂存：渲染 HTML + 恢复追问历史。"""
        if self._ai_running:
            self.ai_status.setText("AI 正在输出，请稍候…")
            return
        self._ai_history = [
            {"role": m["role"], "content": m["content"]}
            for m in (data.get("history") or [])
            if m.get("role") and m.get("content")]
        self.ai_view.setHtml(data.get("html", ""))
        self.tabs.setCurrentIndex(self.TAB_AI_INDEX)
        self.ai_status.setText(
            f"已恢复 {data.get('ts', '')} 的{data.get('kind', '分析')}"
            f" —— 可继续追问或「重新分析」")

    def _on_ai_done(self, reply: str) -> None:
        self._ai_running = False
        self.ai_stop_btn.hide()
        # v5.2.3（问题1修复）：流式 delta 若因竞态/异常全部丢失，视图只剩
        # 免责声明（用户实际遇到：报告完成但无正文）。此处以 chat() 返回的
        # 完整 reply 做保底：视图里没有 reply 的可识别片段时全文重渲染。
        if reply and reply.strip():
            plain = self.ai_view.toPlainText()
            probe = reply.strip()[:24]
            if probe and probe not in plain:
                append_stream(self.ai_view, "content", reply.strip())
        append_stream(self.ai_view, "content", "<br>")
        append_stream(self.ai_view, "disclaimer", "", raw=True)
        finalize_stream(self.ai_view)
        if reply:
            self._ai_history.append({"role": "assistant", "content": reply})
        body = self._ai_history[1:]
        if len(body) > 12:
            self._ai_history = [self._ai_history[0]] + body[-12:]
        self.ai_status.setText("报告完成 —— 可在下方继续追问")
        self._save_ai_session("诊断")

    def _on_ai_err(self, msg: str) -> None:
        self._ai_running = False
        self.ai_stop_btn.hide()
        append_stream(self.ai_view, "error", str(msg))
        append_stream(self.ai_view, "system",
                      "可稍后点「重新分析」；若持续失败，请到主界面「AI 分析」页"
                      "测试连接或更换模型。", raw=True)
        self.ai_status.setText("AI 调用失败（详见报告区提示）")
