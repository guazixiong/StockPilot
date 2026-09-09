"""新浪行情源（备用）：腾讯源失败时降级；全市场列表（东财受限时降级）。"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Callable, Dict, List, Optional, Tuple

from ..models import KLine, Quote
from .base import HttpClient, ProgressCb, Provider, ProviderError, register_provider, to_market_symbol

log = logging.getLogger(__name__)

URL = "https://hq.sinajs.cn/list="
KLINE_URL = ("https://quotes.sina.cn/cn/api/json_v2.php/"
             "CN_MarketDataService.getKLineData")
MARKET_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/"
              "json_v2.php/Market_Center.getHQNodeData")
MARKET_COUNT_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/"
                    "json_v2.php/Market_Center.getHQNodeStockCount")
_LINE_RE = re.compile(r'var hq_str_(\w+)="([^"]*)"')

_HEADERS = {"Referer": "https://finance.sina.com.cn/"}


def parse_sina_text(text: str) -> Dict[str, Quote]:
    out: Dict[str, Quote] = {}
    for sym, payload in _LINE_RE.findall(text):
        p = payload.split(",")
        if len(p) < 32:
            continue
        m = {
            "name": p[0], "open": p[1], "prev_close": p[2], "price": p[3],
            "high": p[4], "low": p[5],
            "volume": _shares_to_lots(p[8]) if len(p) > 8 else "",
            "amount": _yuan_to_wan(p[9]) if len(p) > 9 else "",
            "time": f"{p[30]} {p[31]}" if len(p) > 31 else "",
        }
        # 新浪不直接给涨跌幅，用现价/昨收计算
        try:
            price, prev = float(p[3]), float(p[2])
            m["change"] = str(round(price - prev, 3))
            m["change_pct"] = str(round((price - prev) / prev * 100, 2)) if prev else ""
        except (ValueError, ZeroDivisionError):
            pass
        out[sym] = Quote.from_mapping(sym[2:], m)
    return out


def _shares_to_lots(x: str) -> str:
    try:
        return str(float(x) / 100.0)
    except ValueError:
        return ""


def _yuan_to_wan(x: str) -> str:
    try:
        return str(float(x) / 1e4)
    except ValueError:
        return ""


@register_provider
class SinaProvider(Provider):
    name = "新浪行情(备)"

    def get_quotes(self, codes: List[str]) -> Dict[str, Quote]:
        if not codes:
            return {}
        req_map = {to_market_symbol(c): c for c in codes}
        symbols = list(req_map.keys())
        text = self.http.get_text(
            URL + ",".join(symbols), encoding="gbk",
            headers={"Referer": "https://finance.sina.com.cn/"},
        )
        return {req_map.get(s, self._sym_to_code(s)): q
                for s, q in parse_sina_text(text).items()}

    @staticmethod
    def _sym_to_code(sym: str) -> str:
        return sym[2:] if len(sym) > 2 else sym

    # -------------------------------------------------------------- K线
    # -------------------------------------------------------------- 行业板块
    BOARD_URL = ("https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php")

    def get_industry_boards(self):
        """新浪 49 个行业板块：名称/涨跌幅/成交额/领涨股。"""
        import json as _json
        from ..models import Board

        text = self.http.get_text(self.BOARD_URL, headers=_HEADERS, timeout=12)
        m = re.search(r'=\s*(\{.*\})', text, re.S)
        if not m:
            raise ProviderError("新浪行业板块格式异常")
        data = _json.loads(m.group(1))
        boards = []
        for key, row in data.items():
            p = str(row).split(",")
            if len(p) < 13:
                continue
            try:
                boards.append(Board(
                    name=p[1],
                    change_pct=float(p[5]) if p[5] else None,
                    amount_yi=float(p[7]) / 1e8 if p[7] else None,
                    count=int(float(p[2])) if p[2] else 0,
                    leader_code=(p[8] or "")[-6:],
                    leader_name=p[12] if len(p) > 12 else "",
                    leader_pct=float(p[9]) if len(p) > 9 and p[9] else None,
                ))
            except (ValueError, IndexError):
                continue
        if not boards:
            raise ProviderError("新浪行业板块解析为空")
        return boards

    # -------------------------------------------------------------- 搜索联想
    SUGGEST_URL = "https://suggest3.sinajs.cn/suggest/type=&key="

    def suggest(self, keyword: str, count: int = 10):
        """新浪搜索联想：覆盖 A股(11/10/20/30) 与 沪深ETF(203/22)。"""
        from ..models import Suggestion

        keyword = (keyword or "").strip()
        if not keyword:
            return []
        text = self.http.get_text(self.SUGGEST_URL + keyword,
                                  headers=_HEADERS, timeout=8)
        out = []
        seen = set()
        for row in text.split(";"):
            parts = row.split(",")
            if len(parts) < 8:
                continue
            # parts: 名称,类型,代码,带前缀代码,显示名,...（A股与ETF类型码不同顺序容错）
            kind = parts[1]
            code = parts[3] if parts[3][:2] in ("sh", "sz", "bj") else parts[2]
            code6 = code[-6:] if len(code) >= 6 else ""
            name = parts[4] or parts[0]
            if not (code6.isdigit() and len(code6) == 6):
                continue
            # 只保留 股票(11,10,20,30) 与 ETF(203,22)；排除基金申购码(60/72等)
            if kind not in ("11", "10", "20", "30", "203", "22"):
                continue
            if code6 in seen:
                continue
            seen.add(code6)
            out.append(Suggestion(code=code6, name=name,
                                  market_num="1" if code.startswith("sh") else "0"))
            if len(out) >= count:
                break
        return out

    def get_kline(self, code: str, period: str = "day", limit: int = 250
                  ) -> List[KLine]:
        """新浪日K（scale=240 分钟）。周/月线不支持，抛错由上层降级链处理。"""
        if period != "day":
            raise ProviderError("新浪源仅支持日线")
        data = self.http.get_json(KLINE_URL, params={
            "symbol": to_market_symbol(code), "scale": 240, "ma": "no",
            "datalen": limit}, headers=_HEADERS, timeout=12)
        if not isinstance(data, list) or not data:
            raise ProviderError(f"新浪K线为空: {code}")
        out: List[KLine] = []
        for d in data:
            try:
                out.append(KLine(
                    date=str(d.get("day") or d.get("date") or ""),
                    open=float(d["open"]), close=float(d["close"]),
                    high=float(d["high"]), low=float(d["low"]),
                    volume=float(d.get("volume") or 0) / 100.0,  # 股 -> 手
                    amount=0.0))
            except (KeyError, TypeError, ValueError):
                continue
        if not out:
            raise ProviderError(f"新浪K线解析为空: {code}")
        return out[-limit:] if limit and len(out) > limit else out

    # ------------------------------------------------------ 全市场列表
    def fetch_market_page(self, page: int, page_size: int = 100,
                          sort_field: str = "amount") -> Tuple[int, List[dict]]:
        """新浪沪深A股列表（东财 clist 受限时的备用源）。"""
        text = self.http.get_text(MARKET_URL, params={
            "page": page, "num": page_size, "sort": sort_field, "asc": 0,
            "node": "hs_a", "symbol": "", "_s_r_a": "page"},
            headers=_HEADERS, timeout=12)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"新浪市场列表解析失败: {exc}") from exc
        if not data:
            return 0, []
        rows = [self._market_row(d) for d in data if d.get("code")]
        total = self._market_total()
        return (total or (page - 1) * page_size + len(rows)), rows

    def _market_total(self) -> int:
        try:
            text = self.http.get_text(MARKET_COUNT_URL, params={"node": "hs_a"},
                                      headers=_HEADERS, timeout=8)
            return int(re.search(r"\d+", text).group(0))
        except (ProviderError, AttributeError, ValueError):
            return 0

    @staticmethod
    def _market_row(d: dict) -> dict:
        """新浪字段 -> StockPilot 统一快照行。"""
        def _f(key, scale=1.0):
            try:
                v = d.get(key)
                return None if v in (None, "", "-") else float(v) * scale
            except (TypeError, ValueError):
                return None
        return {
            "code": str(d.get("code") or ""),
            "name": str(d.get("name") or "").strip(),
            "price": _f("trade"),
            "change_pct": _f("changepercent"),
            "open": _f("open"), "high": _f("high"), "low": _f("low"),
            "prev_close": _f("settlement"),
            "volume": _f("volume", 1e-2),          # 股 -> 手
            "amount": _f("amount", 1e-4),          # 元 -> 万
            "turnover_rate": _f("turnoverratio"),
            "volume_ratio": None,                   # 新浪不提供，扫描时用量能等效值兜底
            "pe": _f("per"), "pb": _f("pb"),
            "total_mv": _f("mktcap", 1e-4),         # 万元 -> 亿
            "float_mv": _f("nmc", 1e-4),
            "main_inflow": None,
        }

    def fetch_all_market(self, max_count: int = 2000,
                         on_progress: Optional[ProgressCb] = None) -> List[dict]:
        """全市场快照。v4.4.2：页间并行（原串行 20 页 ≈14s → ≈2s，
        每次机会扫描/盘后日报的固定开销）。排序稳定：页号收集后按序拼接，
        同页内顺序天然保持。"""
        from concurrent.futures import ThreadPoolExecutor
        first_total, probe = self.fetch_market_page(1)
        if not probe:
            return []
        if on_progress:
            on_progress("已拉取 1 页（新浪）")
        rows: List[dict] = list(probe)
        pages = (min(first_total, max_count) + 99) // 100   # page_size=100
        if pages > 1:
            def _page(p: int):
                try:
                    return self.fetch_market_page(p)[1]
                except Exception:  # noqa: BLE001 单页失败不拖垮整体
                    return []
            with ThreadPoolExecutor(max_workers=6) as pool:
                for i, part in enumerate(pool.map(_page, range(2, pages + 1)), 2):
                    rows.extend(part)
                    if on_progress:
                        on_progress(f"已拉取 {min(len(rows), max_count)}/{max_count} 只（新浪并行）")
        return rows[:max_count]
