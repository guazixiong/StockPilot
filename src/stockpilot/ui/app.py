"""主窗口 v5：demo Fusion Terminal 外壳（顶栏指数流 + 侧栏导航 + 托盘常驻）。"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QKeySequence,
                           QLinearGradient, QPainter, QPixmap, QShortcut)
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                                QLineEdit, QListWidget, QMainWindow, QMenu,
                                QStackedWidget, QSystemTrayIcon, QVBoxLayout,
                                QWidget)

from .. import APP_NAME, __version__
from ..core.monitor import is_trade_time
from ..core.storage import Config
from .context import AppContext
from .pages.ai import AiPage
from .pages.backtest import BacktestPage
from .pages.boards import BoardsPage
from .pages.detail import DetailDialog
from .pages.limitup import LimitUpPage
from .pages.home import HomePage
from .pages.market import MarketPage
from .pages.monitor import MonitorPage
from .pages.news import NewsPage
from .pages.positions import PositionsPage
from .pages.screener import ScreenerPage
from .pages.settings import SettingsPage
from .theme import DOWN, SUB, UP, apply_theme

log = logging.getLogger(__name__)

# demo .nav-item 图标字 + 文案（保序：与 stack 索引一一对应）
NAV = [("⌂", "首页"), ("▥", "行情看板"), ("▦", "板块热力"), ("🚀", "涨停监控"),
       ("◎", "机会雷达"), ("◌", "策略监听"), ("◫", "策略回测"), ("▣", "持仓管理"),
       ("✦", "AI 分析"), ("↗", "资讯"), ("⚙", "设置")]
INDEXES = [("sh000001", "上证指数"), ("sz399001", "深证成指"),
           ("sz399006", "创业板指"), ("sh000688", "科创50")]


def app_icon() -> QIcon:
    """应用图标：优先磁盘 ico，否则现场绘制。"""
    candidates = [Path.cwd() / "stockpilot.ico",
                  Path(getattr(sys, "executable", ".")).parent / "stockpilot.ico"]
    for candidate in candidates:
        if candidate.exists():
            return QIcon(str(candidate))
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    grad = QLinearGradient(0, 0, 64, 64)
    grad.setColorAt(0, QColor("#1476d4"))
    grad.setColorAt(1, QColor("#0d3775"))
    painter.setBrush(QBrush(grad))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(3, 3, 58, 58, 14, 14)
    # demo .brand-mark 闪电斜杠
    painter.setPen(QColor("#b8f2ff"))
    painter.drawLine(24, 12, 20, 38)
    painter.setPen(QColor("#6ba2ff"))
    painter.drawLine(38, 12, 34, 38)
    painter.end()
    return QIcon(pm)


class BrandMark(QFrame):
    """demo .brand-mark：渐变方块 + 双色斜杠 logo。"""

    def __init__(self):
        super().__init__()
        self.setFixedSize(37, 37)
        self._pm = QPixmap(37, 37)
        self._pm.fill(Qt.transparent)
        p = QPainter(self._pm)
        p.setRenderHint(QPainter.Antialiasing)
        grad = QLinearGradient(0, 0, 37, 37)
        grad.setColorAt(0, QColor("#0d3775"))
        grad.setColorAt(1, QColor("#1476d4"))
        p.setBrush(QBrush(grad))
        p.setPen(QColor(99, 181, 255, 90))
        p.drawRoundedRect(1, 1, 35, 35, 10, 10)
        p.setPen(QColor("#b8f2ff"))
        p.drawLine(14, 9, 10, 30)
        p.setPen(QColor("#6ba2ff"))
        p.drawLine(27, 9, 23, 30)
        p.end()

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pm)


class TickerBar(QFrame):
    """顶部全局市场栏（demo .topbar）：四大指数横排 + 搜索胶囊 + 状态 chip。"""

    def __init__(self, ctx):
        super().__init__()
        self.setObjectName("topbar")
        self.ctx = ctx
        self.setFixedHeight(72)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 14, 0)
        lay.setSpacing(0)

        self.tickers: dict = {}
        for sym, name in INDEXES:
            # demo .ticker：指数小卡（名称/大数/涨跌 + 右下 sparkline 位）
            card = QFrame()
            card.setObjectName("tickerCard")
            card.setFixedHeight(60)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(14, 8, 14, 6)
            cl.setSpacing(1)
            nm = QLabel(name)
            nm.setStyleSheet("color:#7F94AE; font-size:7.5pt;"
                             "background:transparent;")
            px = QLabel("—")
            px.setStyleSheet("font-size:12pt; font-weight:bold;"
                             "background:transparent;")
            ch = QLabel("—")
            ch.setStyleSheet("font-size:7.5pt; font-weight:700;"
                             "background:transparent;")
            cl.addWidget(nm)
            cl.addWidget(px)
            cl.addWidget(ch)
            self.tickers[sym] = (px, ch)
            lay.addWidget(card)

        lay.addStretch(1)

        # demo .search：胶囊搜索
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索股票 / ETF / 名称…")
        self.search_edit.setFixedWidth(240)
        self.search_edit.setFixedHeight(34)
        self.search_edit.setStyleSheet(
            "border-radius:17px; padding:0 16px; background:"
            "qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 rgba(20,50,91,153),"
            "stop:1 rgba(11,28,52,128)); border:1px solid rgba(87,144,229,41);")
        self.search_edit.returnPressed.connect(self._global_search)
        lay.addWidget(self.search_edit)
        lay.addSpacing(12)

        # demo .top-tools 右侧 chip：交易状态 + 时钟 + 更新时间
        chip = QFrame()
        chip.setObjectName("chip")
        chip.setFixedHeight(38)
        chip_l = QHBoxLayout(chip)
        chip_l.setContentsMargins(12, 4, 12, 4)
        chip_l.setSpacing(10)
        self.status_label = QLabel("—")
        self.clock_label = QLabel(datetime.now().strftime("%H:%M:%S"))
        self.updated_label = QLabel("")
        chip_l.addWidget(self.status_label)
        chip_l.addWidget(self.clock_label)
        chip_l.addWidget(self.updated_label)
        lay.addWidget(chip)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self.quote_timer = QTimer(self)
        self.quote_timer.timeout.connect(self.refresh)
        self.quote_timer.start(15000)
        self._in_flight = False
        self._tick()
        self.refresh()

    def _global_search(self) -> None:
        """顶部搜索：跳行情页并带入关键词。"""
        text = self.search_edit.text().strip()
        if not text:
            return
        win = self.window()
        if hasattr(win, "market") and hasattr(win, "nav"):
            win.nav.setCurrentRow(1)
            win.market.search_edit.setText(text)
            try:
                win.market.search()
            except Exception:  # noqa: BLE001 页面未就绪时忽略
                pass

    def _tick(self) -> None:
        now = datetime.now()
        self.clock_label.setText(now.strftime("%H:%M:%S"))
        trading = is_trade_time(now)
        self.status_label.setText("🟢 交易时段" if trading else "⏸ 已收盘")

    def refresh(self) -> None:
        if self._in_flight:
            return
        self._in_flight = True
        from .workers import submit
        submit(self.ctx.get_quotes, [s for s, _ in INDEXES],
               on_done=self._on_quotes, on_err=self._on_err)

    def _on_quotes(self, quotes: dict) -> None:
        self._in_flight = False
        for sym, (px_label, ch_label) in self.tickers.items():
            q = quotes.get(sym)
            if q is None or q.price is None:
                continue
            color = UP if (q.change_pct or 0) > 0 else (
                DOWN if (q.change_pct or 0) < 0 else SUB)
            px_label.setText(f"{q.price:.2f}")
            px_label.setStyleSheet(
                f"font-size:12pt; font-weight:bold; color:{color};"
                "background:transparent;")
            ch_label.setText(
                f"{q.change_pct:+.2f}%"
                + (f"　{q.change:+.2f}" if q.change is not None else ""))
            ch_label.setStyleSheet(
                f"font-size:7.5pt; font-weight:700; color:{color};"
                "background:transparent;")
        ts = datetime.now().strftime('%H:%M:%S')
        if hasattr(self, "updated_label"):
            self.updated_label.setText(f"更新 {ts}")
        win = self.window()
        if hasattr(win, "sidebar_updated"):
            win.sidebar_updated.setText(f"数据更新时间：{ts}")

    def _on_err(self, msg: str) -> None:
        self._in_flight = False


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{__version__}")
        self.resize(1600, 900)
        self.setMinimumSize(1440, 850)
        self._geo = QSettings("StockPilot", "MainWindow")
        g = self._geo.value("geometry")
        if g is not None:
            try:
                self.restoreGeometry(g)
            except Exception:  # noqa: BLE001
                self.resize(1600, 900)
        self.cfg = Config()
        self.ctx = AppContext(self.cfg)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        self.topbar = TickerBar(self.ctx)
        root.addWidget(self.topbar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        root.addLayout(body, 1)

        # ---- demo .sidebar：品牌头部 + 导航 + 底部状态卡 ----
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(180)
        sb_lay = QVBoxLayout(sidebar)
        sb_lay.setContentsMargins(10, 13, 10, 10)
        sb_lay.setSpacing(0)
        brand = QFrame()
        brand.setProperty("card", False)
        bl = QHBoxLayout(brand)
        bl.setContentsMargins(7, 0, 7, 0)
        bl.setSpacing(10)
        bl.addWidget(BrandMark())
        bt = QVBoxLayout()
        bt.setSpacing(3)
        brand_title = QLabel("StockPilot")
        brand_title.setObjectName("brandTitle")
        brand_sub = QLabel("AI INVESTMENT TERMINAL")
        brand_sub.setObjectName("brandSub")
        bt.addWidget(brand_title)
        bt.addWidget(brand_sub)
        bl.addLayout(bt)
        sb_lay.addWidget(brand)
        sb_lay.addSpacing(15)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for icon, name in NAV:
            self.nav.addItem(f"{icon}  {name}")
        sb_lay.addWidget(self.nav, 1)

        # demo .status-card：绿点运行状态 + 数据更新时间
        stat = QFrame()
        stat.setObjectName("statusCard")
        sl = QVBoxLayout(stat)
        sl.setContentsMargins(10, 13, 10, 10)
        sl.setSpacing(7)
        b = QLabel("● 市场监控运行中")
        b.setStyleSheet("color:#57D9B6; font-size:8pt; font-weight:bold;"
                        "background:transparent;")
        self.sidebar_state = QLabel("盯盘调度器 · 行情链路正常")
        self.sidebar_state.setStyleSheet(
            "color:#7A91AC; font-size:7pt; background:transparent;")
        self.sidebar_updated = QLabel("")
        self.sidebar_updated.setStyleSheet(
            "color:#526780; font-size:7pt; background:transparent;")
        sl.addWidget(b)
        sl.addWidget(self.sidebar_state)
        sl.addWidget(self.sidebar_updated)
        sb_lay.addWidget(stat)
        body.addWidget(sidebar)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)

        self.home = HomePage(self.ctx)
        self.market = MarketPage(self.ctx)
        self.boards = BoardsPage(self.ctx)
        self.limitup = LimitUpPage(self.ctx)
        self.screener = ScreenerPage(self.ctx)
        self.monitor = MonitorPage(self.ctx)
        self.backtest = BacktestPage(self.ctx)
        self.positions = PositionsPage(self.ctx)
        self.ai = AiPage(self.ctx)
        self.news = NewsPage(self.ctx)
        self.settings = SettingsPage(self.ctx)
        for page in (self.home, self.market, self.boards, self.limitup,
                     self.screener, self.monitor, self.backtest,
                     self.positions, self.ai, self.news, self.settings):
            self.stack.addWidget(page)

        self.nav.currentRowChanged.connect(self._on_nav)
        self.nav.setCurrentRow(0)

        for i in range(len(NAV)):
            sc = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            sc.activated.connect(lambda idx=i: self.nav.setCurrentRow(idx))

        # ---- 页面联动 ----
        self.market.open_stock.connect(self._open_detail)
        self.home.open_stock.connect(self._open_detail)
        self.boards.open_stock.connect(self._open_detail)
        self.limitup.open_stock.connect(self._open_detail)
        self.monitor.open_stock.connect(self._open_detail)
        self.positions.open_stock.connect(self._open_detail)
        self.screener.open_stock.connect(self._open_detail)
        self.monitor.ask_ai.connect(self._goto_ai)
        self.backtest.ask_ai.connect(self._goto_ai)
        self.screener.ask_ai.connect(self._goto_ai)
        self.monitor.signals_found.connect(self._on_signals_found)
        self.settings.settings_changed.connect(self._apply_settings)

        self._init_tray()
        from .ai_assistant import AiAssistant
        self.ai_assistant = AiAssistant(self)
        sb = self.statusBar()
        sb.setStyleSheet("QStatusBar{border-top:1px solid #1E3B5E;}")
        self.statusBar().showMessage(
            " 数据源：腾讯/东财/新浪 · 仅供研究参考，不构成投资建议 · 不支持自动下单")

    # ------------------------------------------------------------ 导航
    def _on_nav(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        page = self.stack.widget(row)
        if hasattr(page, "on_show"):
            page.on_show()

    # ------------------------------------------------------------ 详情
    def _open_detail(self, code: str, name: str) -> None:
        dlg = DetailDialog(self.ctx, code, name, self)
        dlg.ai_requested.connect(self._goto_ai)
        dlg.setModal(False)
        dlg.show()

    def _goto_ai(self, context: str, title: str) -> None:
        self.nav.setCurrentRow(9)   # AI 分析
        self.ai.set_context(context, title)

    # ------------------------------------------------------------ 托盘
    def _init_tray(self) -> None:
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(f"{APP_NAME} v{__version__}")
        menu = QMenu()
        act_show = menu.addAction("显示 / 隐藏")
        act_show.triggered.connect(self._toggle_visible)
        menu.addSeparator()
        act_exit = menu.addAction("退出")
        act_exit.triggered.connect(QApplication.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

        # 盘中独立调度器（v6.3）：交易时段 cron 常开循环 + 持仓/计划必监控
        from .scheduler import MonitorScheduler
        self.scheduler = MonitorScheduler(self.ctx, interval_sec=60, parent=self)
        self.scheduler.on_alerts = self._on_scheduler_alerts
        self.scheduler.start()

    def _on_scheduler_alerts(self, alerts: list) -> None:
        """调度器产出新告警 → 托盘弹窗+提示音（必达）+ webhook（可选）+ 持仓页同步。"""
        from ..core.notify import local_alert_text, notify_signals
        text = local_alert_text(alerts)
        if (self.cfg.app.get("tray_notify", True) and self.tray.isVisible()):
            try:
                self.tray.showMessage(APP_NAME, text,
                                       QSystemTrayIcon.Information, 6000)
            except Exception:  # noqa: BLE001
                pass
        if self.cfg.app.get("sound"):
            try:
                import sys as _sys
                if _sys.platform == "win32":
                    import winsound
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:  # noqa: BLE001
                pass
        try:
            notify_signals(self.cfg.notify, alerts)
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(self, "positions"):
                self.positions._watch_last_alerts = alerts
                self.positions._render_alerts(alerts)
                self.positions.watch_status.setText(text.split(chr(10))[0])
        except Exception:  # noqa: BLE001
            pass

    def _tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self._toggle_visible()

    def _toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.showNormal()
            self.activateWindow()

    def _on_signals_found(self, signals: list) -> None:
        """新信号 → 托盘气泡 + 可选提示音。"""
        if not signals:
            return
        app_cfg = self.cfg.app
        if app_cfg.get("sound"):
            QApplication.beep()
        if app_cfg.get("tray_notify") and self.tray.isVisible():
            first = signals[0]
            self.tray.showMessage(
                f"发现 {len(signals)} 条新交易信号",
                f"{first.name}({first.code}) {first.strategy}\n"
                f"现价 {first.price} 止损 {first.stop_price} "
                f"目标 {first.target_price}",
                QSystemTrayIcon.Information, 8000)

    # ------------------------------------------------------------ 设置
    def _apply_settings(self) -> None:
        apply_theme(QApplication.instance(),
                    (self.cfg.get("market") or {}).get("theme"))
        self.market.apply_settings()
        self.monitor.apply_settings()
        self.topbar.refresh()
        proxy = (self.cfg.get("market") or {}).get("proxy") or ""
        if (proxy or None) != self.ctx.http.session.proxies.get("https"):
            self.ctx.http.session.proxies = (
                {"http": proxy, "https": proxy} if proxy else {})

    def resizeEvent(self, ev) -> None:  # noqa: N802
        if hasattr(self, "ai_assistant"):
            self.ai_assistant._place()
        super().resizeEvent(ev)

    def closeEvent(self, ev) -> None:  # noqa: N802
        if self.cfg.app.get("min_to_tray") and self.tray.isVisible():
            self.hide()
            self.tray.showMessage(
                APP_NAME, "程序已最小化到托盘，策略监听继续运行。",
                QSystemTrayIcon.Information, 5000)
            ev.ignore()
            return
        self.cfg.save()
        if hasattr(self, "scheduler"):
            self.scheduler.stop()
        try:
            self._geo.setValue("geometry", self.saveGeometry())
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(ev)


def run_app() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    cfg = Config()
    apply_theme(app, (cfg.get("market") or {}).get("theme") or "dark")
    win = MainWindow()
    win.show()
    win.market.refresh()
    win.news.refresh_fast_news()
    return app.exec()
