"""自绘K线图控件 v2.3。

- 蜡烛 + MA + 成交量；副图指标可切换（MACD/KDJ/RSI/无）
- 压力/支撑位横线标注 + 悬停显示名称（tooltip）
- 筹码分布横图（与K线同价轴对照）
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QPainter, QPen, QBrush,
                           QLinearGradient)
from PySide6.QtWidgets import QToolTip, QWidget

from ..core import indicators
from ..core.models import KLine

from .theme import (CHART_BG, CHART_GRID, DANGER, DOWN, FLAT, MA5, MA10,
                       MA20, MA60, SUB, TEXT, UP as _UP_HEX, WARNING)

BG = QColor(CHART_BG)
GRID = QColor(CHART_GRID)
TEXT = QColor(SUB)
UP = QColor(_UP_HEX)
DOWN = QColor(DOWN)
MA_COLORS = {5: MA5, 10: MA10, 20: MA20, 60: MA60}
SUB_INDICATORS = ["MACD", "KDJ", "RSI", "无"]


class CandleChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(430)
        self.klines: List[KLine] = []
        self.mas: dict = {}
        self.view_len = 120
        self.hover_idx: Optional[int] = None
        self._empty_text = "暂无数据"
        self.levels: Dict[str, float] = {}
        self._level_hit: Optional[str] = None      # 当前悬停命中的标注名
        self.sub_modes: list = ["MACD"]              # 副图指标（v5.2 多选堆叠）
        self.cyq: Optional[Tuple[list, list]] = None  # 筹码分布 (prices, weights)
        self._py_hi = self._py_lo = 0.0

    # ------------------------------------------------------------ 数据
    def set_data(self, klines: List[KLine], levels: Optional[Dict[str, float]] = None,
                 cyq: Optional[Tuple[list, list]] = None) -> None:
        self.klines = klines or []
        closes = [k.close for k in self.klines]
        self.mas = {w: indicators.sma(closes, w) for w in (5, 10, 20, 60)}
        self.view_len = min(max(self.view_len, 30), max(len(self.klines), 30))
        self.view_len = min(self.view_len, max(len(self.klines), 30))
        self.levels = levels or {}
        self.cyq = cyq
        self.hover_idx = None
        self._level_hit = None
        self.update()

    def set_sub_mode(self, mode: str) -> None:
        """兼容旧单选接口：切换到单一指标。"""
        if mode in SUB_INDICATORS:
            self.sub_modes = [mode] if mode != "无" else []
            self.update()

    def set_sub_modes(self, modes: list) -> None:
        """v5.2：多选副图——按给定顺序堆叠绘制（去重、去"无"）。"""
        seen, out = set(), []
        for m in modes or []:
            if m in SUB_INDICATORS and m != "无" and m not in seen:
                seen.add(m)
                out.append(m)
        self.sub_modes = out
        self.update()

    def clear(self, text: str = "暂无数据") -> None:
        self.klines = []
        self._empty_text = text
        self.update()

    # ------------------------------------------------------------ 交互
    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self.klines:
            x, y = ev.position().x(), ev.position().y()
            # 命中标注线（±4px）→ tooltip 显示名称与价位
            hit = None
            if self._py_hi:
                for name, lv in self.levels.items():
                    if lv is None:
                        continue
                    yy = self._py(lv)
                    if abs(yy - y) <= 4:
                        hit = name
                        break
            if hit != self._level_hit:
                self._level_hit = hit
                if hit:
                    lvv = self.levels[hit]
                    QToolTip.showText(ev.globalPosition().toPoint(),
                                      f"{hit}　{lvv:.2f}", self)
                else:
                    QToolTip.hideText()
            idx = self._x_to_index(x)
            if idx != self.hover_idx:
                self.hover_idx = idx
            self.update()
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev) -> None:  # noqa: N802
        self.hover_idx = None
        self._level_hit = None
        self.update()
        super().leaveEvent(ev)

    def wheelEvent(self, ev) -> None:  # noqa: N802
        if not self.klines:
            return
        step = 10 if ev.angleDelta().y() > 0 else -10
        self.view_len = max(30, min(len(self.klines), self.view_len + step))
        self.update()

    def _x_to_index(self, x: float) -> Optional[int]:
        n = len(self.view())
        if n == 0:
            return None
        left, right = 8, self.width() - 60
        plot_w = max(right - left, 1)
        i = int((x - left) / plot_w * n)
        return i if 0 <= i < n else None

    def view(self) -> List[KLine]:
        return self.klines[-self.view_len:] if self.klines else []

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(CHART_BG))
        if not self.klines:
            p.setPen(TEXT)
            p.drawText(self.rect(), Qt.AlignCenter, self._empty_text)
            p.end()
            return

        data = self.view()
        n = len(data)
        offset = len(self.klines) - n
        w, h = self.width(), self.height()
        left, right_margin, top, bottom = 8, 60, 26, 20
        plot_w = max(w - left - right_margin, 10)
        total_h = max(h - top - bottom, 10)
        # v5.2：副图多选时按指标数动态扩容（1个=20%，2个=28%，3个=36%），从量能区借高
        n_sub = len(self.sub_modes)
        sub_ratio = {0: 0.0, 1: 0.20, 2: 0.28, 3: 0.36}.get(n_sub, 0.40)
        price_h = int(total_h * (0.78 - sub_ratio))
        sub_h = int(total_h * sub_ratio)
        cyq_h = int(total_h * 0.14) if self.cyq else 0
        price_top = top
        sub_top = price_top + price_h + 8
        vol_top = sub_top + sub_h + 6
        vol_h = max(total_h - (vol_top - top) - bottom, 12) if cyq_h == 0 else int(total_h * 0.08)

        highs = [k.high for k in data]
        lows = [k.low for k in data]
        for ma_list in self.mas.values():
            for v in ma_list[offset:offset + n]:
                if v is not None:
                    highs.append(v * 1.001)
                    lows.append(v * 0.999)
        for lv in self.levels.values():
            if lv is not None:
                highs.append(lv)
                lows.append(lv)
        hi_v, lo_v = max(highs), min(lows)
        if hi_v <= lo_v:
            hi_v = lo_v * 1.02 + 0.01
        span = hi_v - lo_v
        self._py_hi, self._py_lo = hi_v, lo_v

        def py(price: float) -> float:
            return price_top + (hi_v - price) / span * price_h

        step = plot_w / n
        body_w = max(step * 0.62, 1.5)

        # 网格与价格标签
        p.setFont(QFont("Microsoft YaHei UI", 7))
        for i in range(5):
            y = price_top + price_h * i / 4
            p.setPen(QPen(GRID, 1))
            p.drawLine(left, int(y), w - right_margin, int(y))
            p.setPen(TEXT)
            p.drawText(QRectF(w - right_margin + 4, y - 7, right_margin - 6, 14),
                       Qt.AlignLeft | Qt.AlignVCenter, f"{hi_v - span * i / 4:.2f}")

        # 压力/支撑位横线（黄色点线；悬停高亮加粗并放大标签）
        for name, lv in self.levels.items():
            if lv is None or not (lo_v <= lv <= hi_v):
                continue
            y = py(lv)
            hovered = (name == self._level_hit)
            p.setPen(QPen(QColor(WARNING), 2 if hovered else 1,
                          Qt.SolidLine if hovered else Qt.DotLine))
            p.drawLine(left, int(y), w - right_margin, int(y))
            label = f"{name} {lv:.2f}"
            if hovered:
                label = f"◆ {label}"
            p.setPen(QColor(WARNING))
            p.drawText(QRectF(left + 2, y - 13, 220, 12),
                       Qt.AlignLeft | Qt.AlignVCenter, label)

        # 蜡烛
        for i, k in enumerate(data):
            x = left + step * (i + 0.5)
            color = UP if k.close >= k.open else DOWN
            p.setPen(QPen(color, 1))
            p.drawLine(int(x), int(py(k.high)), int(x), int(py(k.low)))
            y1, y2 = py(max(k.open, k.close)), py(min(k.open, k.close))
            if abs(y1 - y2) < 1:
                y2 = y1 + 1
            p.setBrush(color)
            p.drawRect(QRectF(x - body_w / 2, y1, body_w, y2 - y1))

        # MA 线
        for period, color_hex in MA_COLORS.items():
            ma_list = self.mas.get(period) or []
            pts = [(left + step * (i + 0.5), ma_list[offset + i])
                   for i in range(n)
                   if offset + i < len(ma_list) and ma_list[offset + i] is not None]
            if len(pts) < 2:
                continue
            p.setPen(QPen(QColor(color_hex), 1))
            for (x1, v1), (x2, v2) in zip(pts, pts[1:]):
                p.drawLine(int(x1), int(py(v1)), int(x2), int(py(v2)))

        # 副图指标（MACD/KDJ/RSI）
        self._paint_sub(p, data, offset, n, left, w, right_margin, sub_top, sub_h, step)

        # 成交量
        max_vol = max((k.volume for k in data), default=1) or 1
        p.setPen(GRID)
        p.drawLine(left, vol_top + vol_h, w - right_margin, vol_top + vol_h)
        for i, k in enumerate(data):
            x = left + step * (i + 0.5)
            vh = k.volume / max_vol * vol_h
            color = UP if k.close >= k.open else DOWN
            p.fillRect(QRectF(x - body_w / 2, vol_top + vol_h - vh, body_w, vh), color)

        # 筹码分布横图（右侧对齐价格轴）
        if self.cyq:
            prices, weights = self.cyq
            self._paint_cyq(p, prices, weights, w, right_margin,
                            vol_top + vol_h + 6, cyq_h, lo_v, hi_v, span, price_top, price_h)

        # 日期轴
        p.setPen(TEXT)
        tick = max(n // 6, 1)
        for i in range(0, n, tick):
            x = left + step * (i + 0.5)
            p.drawText(QRectF(x - 30, h - bottom + 2, 60, 14),
                       Qt.AlignCenter, data[i].date[5:])

        # 十字光标与信息
        idx = self.hover_idx if (self.hover_idx is not None and self.hover_idx < n) else n - 1
        k = data[idx]
        cx = left + step * (idx + 0.5)
        p.setPen(QPen(QColor("#3A4A63"), 1, Qt.DashLine))
        p.drawLine(int(cx), price_top, int(cx), vol_top + vol_h)
        chg = f"{k.change_pct:+.2f}%" if k.change_pct is not None else "-"
        info = (f"{k.date}  开{k.open:.2f} 高{k.high:.2f} 低{k.low:.2f} "
                f"收{k.close:.2f}  {chg}  量{k.volume / 10000:.1f}万手")
        p.setPen(QColor("#D9E8FF"))
        p.drawText(QRectF(left, 4, w - right_margin - left, 18),
                   Qt.AlignLeft | Qt.AlignVCenter, info)
        ma_parts = []
        for period in MA_COLORS:
            v = self.mas[period][offset + idx] if offset + idx < len(self.mas[period]) else None
            ma_parts.append(f"MA{period}:{v:.2f}" if v is not None else f"MA{period}:-")
        p.setPen(QColor(MA5))
        p.drawText(QRectF(left, h - bottom - 16, w - right_margin - left, 14),
                   Qt.AlignLeft | Qt.AlignVCenter, "  ".join(ma_parts))
        p.end()

    # --------------------------------------------------------- 副图（v5.2 多选堆叠）
    def _paint_sub(self, p, data, offset, n, left, w, right_margin,
                   sub_top, sub_h, step) -> None:
        modes = self.sub_modes
        if not modes:
            return
        # N 个指标均分副图总高，各画在自辖区（独立坐标，互不压缩变形）
        k = len(modes)
        gap = 6 if k > 1 else 0
        each_h = (sub_h - gap * (k - 1)) / k
        for i, m in enumerate(modes):
            top_i = sub_top + i * (each_h + gap)
            self._paint_one_sub(p, m, data, offset, n, left, w,
                                right_margin, top_i, each_h, step)

    def _paint_one_sub(self, p, sub_lbl, data, offset, n, left, w,
                       right_margin, sub_top, sub_h, step) -> None:
        closes = [k.close for k in self.klines]
        p.setPen(QColor("#6F86A2"))
        p.setFont(QFont("Microsoft YaHei UI", 7))
        p.drawText(QRectF(left + 2, sub_top + 2, 200, 12),
                   Qt.AlignLeft | Qt.AlignVCenter, f"副图: {sub_lbl}")

        offset_slice = slice(offset, offset + n)
        if sub_lbl == "MACD":
            dif, dea, hist = indicators.macd(closes)
            dif_s, dea_s = dif[offset_slice], dea[offset_slice]
            hist_s = hist[offset_slice]
            m = max((abs(v) for v in hist_s + dif_s + dea_s if v is not None),
                    default=1) or 1
            mid = sub_top + sub_h / 2

            def my(v):
                return mid - (v or 0) / m * (sub_h / 2 - 3)

            x0 = left + step * 0.5
            for i, v in enumerate(hist_s):
                if v is None:
                    continue
                color = UP if v >= 0 else DOWN
                p.setPen(color)
                x = x0 + step * i
                y2 = my(v)
                p.drawLine(int(x), int(mid), int(x), int(y2))
            for series, color in ((dif_s, MA5), (dea_s, MA10)):
                p.setPen(QPen(QColor(color), 1))
                pts = [(x0 + step * i, my(v)) for i, v in enumerate(series)
                       if v is not None]
                for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                    p.drawLine(int(x1), int(y1), int(x2), int(y2))
        elif sub_lbl == "KDJ":
            highs = [k.high for k in self.klines]
            lows = [k.low for k in self.klines]
            kv, dv, jv = indicators.kdj(highs, lows, closes)
            ks, ds, js = kv[offset_slice], dv[offset_slice], jv[offset_slice]

            def ky(v):
                return sub_top + (100 - (v or 50)) / 100 * sub_h

            x0 = left + step * 0.5
            for series, color in ((ks, MA5), (ds, MA10), (js, MA20)):
                p.setPen(QPen(QColor(color), 1))
                pts = [(x0 + step * i, ky(v)) for i, v in enumerate(series)
                       if v is not None]
                for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                    p.drawLine(int(x1), int(y1), int(x2), int(y2))
        elif sub_lbl == "RSI":
            r6 = indicators.rsi(closes, 6)[offset_slice]

            def ry(v):
                return sub_top + (100 - (v or 50)) / 100 * sub_h

            x0 = left + step * 0.5
            p.setPen(QPen(QColor("#e5534b"), 1))
            pts = [(x0 + step * i, ry(v)) for i, v in enumerate(r6)
                   if v is not None]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                p.drawLine(int(x1), int(y1), int(x2), int(y2))

    def _paint_cyq(self, p, prices, weights, w, right_margin,
                   top, h_, lo_v, hi_v, span, price_top, price_h) -> None:
        """筹码分布横条图：纵轴对齐K线价格轴（画在最右侧或独立小区带）。"""
        max_w_ratio = max(weights) if weights else 0
        if max_w_ratio <= 0 or h_ < 10:
            return
        p.setPen(QColor("#6F86A2"))
        p.setFont(QFont("Microsoft YaHei UI", 7))
        p.drawText(QRectF(w - right_margin + 4, top, right_margin - 6, 12),
                   Qt.AlignLeft | Qt.AlignVCenter, "筹码")
        # 独立小区带按自身价格范围展开（与K线价区一致更直观）
        for px, wt in zip(prices, weights):
            y = price_top + (hi_v - px) / span * price_h
            bw = wt / max_w_ratio * (right_margin - 10)
            # 获利（低于现价）暖色、套牢冷色
            cur = self.klines[-1].close
            color = QColor(_UP_HEX) if px <= cur else QColor(MA10)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(color))
            p.drawRect(QRectF(w - right_margin + 4, y - price_h / (len(prices) * 2),
                              max(bw, 0.5), max(price_h / len(prices), 1.2)))
