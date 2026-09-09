"""腾讯行情源：实时报价（含指数）+ K线（ifzq，东财K线受限时自动降级）。"""
from __future__ import annotations

import logging
import re
from typing import Dict, List

from ..models import KLine, NewsItem, Quote
from .base import HttpClient, Provider, ProviderError, register_provider, to_market_symbol

log = logging.getLogger(__name__)

URL = "https://qt.gtimg.cn/q="
# v4.4.3：web.ifzq 开始对 fqkline 返回 501（2026-09 实测），同路径无 web 前缀域正常。
KLINE_URL = "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
KLINE_URL_FALLBACK = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_LINE_RE = re.compile(r'v_(\w+)="([^"]*)"')

# 腾讯字段（0 基）索引
F_NAME, F_CODE, F_PRICE, F_PREV, F_OPEN = 1, 2, 3, 4, 5
F_TIME, F_CHANGE, F_CHANGE_PCT, F_HIGH, F_LOW = 30, 31, 32, 33, 34
F_VOLUME, F_AMOUNT, F_TURNOVER, F_PE = 36, 37, 38, 39
F_AMPLITUDE, F_FLOAT_MV, F_TOTAL_MV, F_PB = 43, 44, 45, 46
F_LIMIT_UP, F_LIMIT_DOWN, F_VOL_RATIO = 47, 48, 49
# 五档盘口：9~18 买一~买五 价/量，19~28 卖一~卖五 价/量
_BID_START, _ASK_START, _LEVEL = 9, 19, 5


def _s(parts: List[str], idx: int) -> str:
    return parts[idx] if idx < len(parts) else ""


def parse_quote_text(text: str) -> Dict[str, Quote]:
    """解析腾讯行情报文（单测覆盖）。"""
    out: Dict[str, Quote] = {}
    for sym, payload in _LINE_RE.findall(text):
        parts = payload.split("~")
        if len(parts) < F_CHANGE_PCT:
            continue
        raw_code = _s(parts, F_CODE) or sym[2:]
        m = {
            "name": _s(parts, F_NAME),
            "price": _s(parts, F_PRICE),
            "prev_close": _s(parts, F_PREV),
            "open": _s(parts, F_OPEN),
            "change": _s(parts, F_CHANGE),
            "change_pct": _s(parts, F_CHANGE_PCT),
            "high": _s(parts, F_HIGH),
            "low": _s(parts, F_LOW),
            "volume": _s(parts, F_VOLUME),
            "amount": _s(parts, F_AMOUNT),
            "turnover_rate": _s(parts, F_TURNOVER),
            "pe": _s(parts, F_PE),
            "amplitude": _s(parts, F_AMPLITUDE),
            "float_mv": _s(parts, F_FLOAT_MV),
            "total_mv": _s(parts, F_TOTAL_MV),
            "pb": _s(parts, F_PB),
            "limit_up": _s(parts, F_LIMIT_UP),
            "limit_down": _s(parts, F_LIMIT_DOWN),
            "volume_ratio": _s(parts, F_VOL_RATIO),
            "time": _s(parts, F_TIME),
        }
        for i in range(1, _LEVEL + 1):
            m[f"bid{i}_price"] = _s(parts, _BID_START + (i - 1) * 2)
            m[f"bid{i}_vol"] = _s(parts, _BID_START + (i - 1) * 2 + 1)
            m[f"ask{i}_price"] = _s(parts, _ASK_START + (i - 1) * 2)
            m[f"ask{i}_vol"] = _s(parts, _ASK_START + (i - 1) * 2 + 1)
        out[sym] = Quote.from_mapping(raw_code, m)
    return out


@register_provider
class TencentProvider(Provider):
    name = "腾讯行情"

    def get_quotes(self, codes: List[str]) -> Dict[str, Quote]:
        """返回值按**原始请求键**索引（600519 / sh000001 等原样返回）。"""
        if not codes:
            return {}
        req_map: Dict[str, str] = {}  # 响应sym -> 原始请求键
        for c in codes:
            req_map[to_market_symbol(c)] = c
        symbols = list(req_map.keys())
        parsed: Dict[str, Quote] = {}
        # 每次最多 60 只，避免 URL 过长
        for i in range(0, len(symbols), 60):
            chunk = symbols[i:i + 60]
            text = self.http.get_text(URL + ",".join(chunk), encoding="gbk")
            parsed.update(parse_quote_text(text))
        result: Dict[str, Quote] = {}
        for sym, quote in parsed.items():
            result[req_map.get(sym, self._sym_to_code(sym))] = quote
        return result

    @staticmethod
    def _sym_to_code(sym: str) -> str:
        return sym[2:] if len(sym) > 2 else sym

    def get_kline(self, code: str, period: str = "day", limit: int = 250
                  ) -> List[KLine]:
        sym = to_market_symbol(code)
        params = {"param": f"{sym},{period},,,{limit},qfq"}
        try:
            data = self.http.get_json(KLINE_URL, params=params, timeout=12)
        except ProviderError:
            # v4.4.3：主域偶发 501 时退老域（web.ifzq），两域互备
            data = self.http.get_json(KLINE_URL_FALLBACK, params=params,
                                      timeout=12)
        node = ((data or {}).get("data") or {}).get(sym) or {}
        arr = (node.get(f"qfq{period}") or node.get(period) or [])
        out: List[KLine] = []
        for p in arr:
            try:
                out.append(KLine(
                    date=str(p[0]), open=float(p[1]), close=float(p[2]),
                    high=float(p[3]), low=float(p[4]),
                    volume=float(p[5]) if len(p) > 5 and p[5] else 0.0,
                    amount=float(p[6]) if len(p) > 6 and p[6] else 0.0))
            except (ValueError, IndexError, TypeError):
                continue
        if not out:
            raise ProviderError(f"腾讯K线为空: {code}")
        return out[-limit:] if limit and len(out) > limit else out

    # -------------------------------------------------------------- 分时
    def get_minute(self, code: str):
        """腾讯分时：返回 MinuteSeries（date/昨收/分钟点含均价线）。"""
        from ..models import MinutePoint, MinuteSeries

        sym = to_market_symbol(code)
        data = self.http.get_json(
            "https://web.ifzq.gtimg.cn/appstock/app/minute/query",
            params={"code": sym}, timeout=12)
        node = ((data or {}).get("data") or {}).get(sym) or {}
        rows = (node.get("data") or {}).get("data") or []
        if not rows:
            raise ProviderError(f"分时数据为空: {code}")
        qt = (node.get("qt") or {}).get(sym) or []
        prev_close = None
        try:
            prev_close = float(qt[4])
        except (IndexError, TypeError, ValueError):
            prev_close = None
        series = MinuteSeries(date=str((node.get("data") or {}).get("date") or ""),
                              prev_close=prev_close)
        prev_cum_vol = 0.0
        prev_avg = None
        for row in rows:
            p = str(row).split(" ")
            if len(p) < 2:
                continue
            try:
                t = f"{p[0][:2]}:{p[0][2:4]}"
                price = float(p[1])
                cum_vol = float(p[2]) if len(p) > 2 else 0.0
                cum_amt = float(p[3]) if len(p) > 3 else 0.0
            except (ValueError, IndexError):
                continue
            avg = cum_amt / (cum_vol * 100) if cum_vol > 0 else prev_avg
            series.points.append(MinutePoint(
                time=t, price=price,
                avg=round(avg, 3) if avg else prev_avg,
                volume=max(cum_vol - prev_cum_vol, 0.0)))
            prev_cum_vol = cum_vol
            prev_avg = avg
        if not series.points:
            raise ProviderError(f"分时解析为空: {code}")
        return series

    def get_news(self, code: str, size: int = 10) -> List[NewsItem]:
        return []
