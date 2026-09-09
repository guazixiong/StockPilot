"""涨停监控页：涨停列表/封单强度/连板数/新高/炸板（借鉴 Rockyzsu/stock 涨停分析）。"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .. import kit
from ..theme import DOWN, SUB, UP, WARNING
from ..workers import submit
from .market import card_frame, num_item, pct_item

_COLS = ["代码", "名称", "现价", "涨停价", "涨幅%", "封单(万)", "强度%",
         "连板", "破50日新高", "流通市值(亿)", "量比"]


def _limit_th(code: str, name: str) -> float:
    """涨停阈值：创/科 20%，主板 10%（含 ST 5% 的近似由名称提示）。"""
    if code.startswith(("30", "68")):
        return 0.199
    return 0.099


def _streak_limit_up(kls, code: str = "", name: str = "") -> int:
    """从缓存K线回溯连板数（含今日）。阈值分流：
    创/科板 19%，ST/带退字 4.7%（含4.8~5.1容差），主板 9%。"""
    import re as _re
    if code.startswith(("30", "68")):
        th = 0.19
    elif _re.search(r"ST|退", name or ""):
        th = 0.047
    else:
        th = 0.09
    if len(kls) < 2:
        return 0
    n = 0
    # 从最新往回：每根与其前一根（更早）比涨幅
    for i in range(len(kls) - 1, 0, -1):
        prev = kls[i - 1].close
        if not prev:
            break
        pct = kls[i].close / prev - 1
        if pct >= th:
            n += 1
        else:
            break
    return n


class LimitUpPage(QWidget):
    open_stock = Signal(str, str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._loading = False
        self.rows: List[dict] = []
        self._build_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(60 * 1000)

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(11, 11, 11, 11)
        lay.setSpacing(10)

        lay.addWidget(kit.page_head(
            "涨停监控", "封单强度 · 连板高度 · 破50日新高 · 炸板标记"))

        head = QHBoxLayout()
        self.status = QLabel(" ")
        self.status.setProperty("hint", True)
        only_new = QCheckBox("仅看破新高")
        only_new.toggled.connect(lambda _: self._fill())
        self.only_new = only_new
        head.addWidget(self.status, 1)
        head.addWidget(only_new)
        refresh_btn = QPushButton("立即刷新")
        refresh_btn.setProperty("secondary", True)
        refresh_btn.setFixedHeight(30)
        refresh_btn.clicked.connect(self.refresh)
        head.addWidget(refresh_btn)
        lay.addLayout(head)

        card = card_frame()
        v = QVBoxLayout(card)
        v.setContentsMargins(8, 8, 8, 8)
        self.table = QTableWidget(0, len(_COLS))
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemDoubleClicked.connect(self._open_row)
        v.addWidget(self.table)
        lay.addWidget(card, 1)

        tip = QLabel("双击行打开个股详情 · 数据源：全市场快照 + 本地K线缓存 · 仅供研究参考")
        tip.setProperty("hint", True)
        lay.addWidget(tip)

    # ------------------------------------------------------------ 数据
    def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        self.status.setText("扫描全市场…")
        submit(self._load, on_done=self._on_loaded,
               on_err=self._on_err)

    def _load(self):
        """全市场快照筛选涨停（含炸板），K 线补充连板/新高。"""
        rows = self.ctx._market_fetch(5500)
        th_by_code = {}
        out: List[dict] = []
        for r in rows:
            code = r.get("code") or ""
            name = r.get("name") or ""
            price = r.get("price")
            high = r.get("high")
            prev = r.get("prev_close")
            if not price or not prev:
                continue
            th = _limit_th(code, name)
            limit_price = prev * (1 + th)
            is_limit = price >= limit_price * 0.998
            is_blast = (not is_limit) and high and prev and \
                high >= limit_price * 0.998   # 曾触涨停但收盘未封住
            if not (is_limit or is_blast):
                continue
            # 封单金额：全市场快照无买一档 → 对涨停股逐只补拉五档（腾讯）
            seal = None
            r["_is_limit"] = is_limit
            out.append({
                "code": code, "name": name, "price": price,
                "limit_price": round(limit_price, 2),
                "pct": r.get("change_pct"), "seal": seal,
                "blast": is_blast, "float_mv": r.get("float_mv"),
                "volume_ratio": r.get("volume_ratio"),
                "amount": r.get("amount"),
                "_is_limit": is_limit,
            })
        # 涨停股补拉五档取封单（量小：通常 <30 只）
        limit_codes = [r["code"] for r in out if r.get("_is_limit")][:40]
        if limit_codes:
            try:
                quotes = self.ctx.get_quotes(limit_codes)
                for r in out:
                    q = quotes.get(r["code"])
                    if q is not None and q.bid1_price and q.bid1_vol:
                        r["seal"] = q.bid1_price * q.bid1_vol / 1e4
            except Exception:  # noqa: BLE001 五档失败不阻塞
                pass
        # K 线补连板/新高（缓存优先，只对涨停股）
        for row in out[:80]:
            try:
                kls = self.ctx.kline_for_scan(row["code"], 120)
            except Exception:  # noqa: BLE001
                kls = []
            row["streak"] = _streak_limit_up(kls[-12:], row["code"],
                                              row["name"]) if kls else 1
            if kls:
                h50 = max(k.high for k in kls[-51:-1]) if len(kls) > 51 else None
                row["new_high"] = bool(h50 and row["price"] >= h50)
            else:
                row["new_high"] = False
        out.sort(key=lambda r: (r.get("streak") or 1,
                                r.get("seal") or 0), reverse=True)
        return out

    def _on_loaded(self, rows: List[dict]) -> None:
        self._loading = False
        self.rows = rows
        n_blast = sum(1 for r in rows if r.get("blast"))
        self.status.setText(
            f"涨停 {len(rows) - n_blast} 只 · 炸板 {n_blast} 只 · {datetime.now():%H:%M}")
        self._fill()

    def _on_err(self, msg: str) -> None:
        self._loading = False
        from ..err import fail_hint, show_error
        show_error(self, "涨停数据获取失败", msg)
        fail_hint(self.status, "获取失败，可点刷新重试")

    def _fill(self) -> None:
        rows = self.rows
        if self.only_new.isChecked():
            rows = [r for r in rows if r.get("new_high")]
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r["code"], r["name"],
                    f"{r['price']:.2f}", f"{r['limit_price']:.2f}",
                    f"{r['pct']:+.2f}%"
                    if r.get("pct") is not None else "—",
                    f"{r['seal']:,.0f}" if r.get("seal") else "—",
                    self._strength_text(r), str(r.get("streak") or 1),
                    "✓" if r.get("new_high") else "",
                    f"{r.get('float_mv') or 0:,.0f}",
                    f"{r.get('volume_ratio') or 0:.1f}"]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if r.get("blast"):
                    item.setForeground(QColor(WARNING))
                if c == 1 and r.get("blast"):
                    item.setText(item.text() + "（炸板）")
                if c in (2, 3, 5, 7, 8, 9, 10):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 7 and (r.get("streak") or 1) >= 3:
                    item.setForeground(QColor(UP))
                self.table.setItem(i, c, item)

    @staticmethod
    def _strength_text(r: dict) -> str:
        """封单强度 = 封单金额 / 成交额（越高封得越牢）。"""
        seal, amount = r.get("seal"), r.get("amount")
        if not seal or not amount:
            return "—"
        pct = seal / amount * 100
        band = "强" if pct >= 30 else ("中" if pct >= 10 else "弱")
        return f"{pct:.0f}%（{band}）"

    def _open_row(self, item) -> None:
        row = item.row()
        code = self.table.item(row, 0).text()
        name = self.table.item(row, 1).text().replace("（炸板）", "")
        self.open_stock.emit(code, name)

    def on_show(self) -> None:
        if not self.rows:
            self.refresh()
