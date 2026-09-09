"""东方财富数据源：K线 / 全市场快照（选股）/ 7x24快讯 / 个股新闻与公告 / 搜索联想。"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from ..models import KLine, NewsItem, Quote, Suggestion
from .base import HttpClient, ProgressCb, Provider, ProviderError, register_provider, to_secid

log = logging.getLogger(__name__)

KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
FAST_NEWS_URL = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
ANN_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"
SUGGEST_URL = "https://searchapi.eastmoney.com/api/suggest/get"
SUGGEST_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"  # 东财网页公开 token

# 全市场（沪深主板/创业板/科创板/北交所）
FS_ALL = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"

_PERIOD_KLT = {"day": 101, "week": 102, "month": 103}

# clist 字段含义
CLIST_FIELDS = "f2,f3,f5,f6,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23,f62"
_F2COL = {
    "f2": "price", "f3": "change_pct", "f5": "volume", "f6": "amount",
    "f8": "turnover_rate", "f9": "pe", "f10": "volume_ratio", "f12": "code",
    "f14": "name", "f15": "high", "f16": "low", "f17": "open",
    "f18": "prev_close", "f20": "total_mv", "f21": "float_mv", "f23": "pb",
    "f62": "main_inflow",
}
# f20/f21 单位为元 -> 亿；f6/f62 单位为元 -> 万元；f5 单位为手
_SCALE = {"f6": 1e-4, "f20": 1e-8, "f21": 1e-8, "f62": 1e-4}


def _num(x) -> Optional[float]:
    try:
        if x in (None, "", "-"):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_clist_row(row: dict) -> dict:
    out = {"main_inflow": None}
    for f, col in _F2COL.items():
        v = _num(row.get(f))
        if v is not None and f in _SCALE:
            v = v * _SCALE[f]
        out[col] = v
    out["code"] = str(row.get("f12") or "")
    out["name"] = str(row.get("f14") or "").strip()
    return out


def parse_kline_json(data: dict) -> List[KLine]:
    """解析东财 kline 接口 data 部分（单测覆盖）。"""
    klines = (data or {}).get("klines") or []
    out: List[KLine] = []
    for line in klines:
        p = line.split(",")
        if len(p) < 7:
            continue
        out.append(KLine(
            date=p[0], open=float(p[1]), close=float(p[2]), high=float(p[3]),
            low=float(p[4]), volume=float(p[5]), amount=float(p[6]),
            change_pct=_num(p[8]) if len(p) > 8 else None,
            turnover=_num(p[10]) if len(p) > 10 else None,
        ))
    return out


def strip_jsonp(text: str) -> dict:
    """去掉 jsonp 外壳 'cb({...});' -> dict。"""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ProviderError("jsonp 响应格式异常")
    return json.loads(text[start:end + 1])


def _clean(s: str) -> str:
    return re.sub(r"</?em>", "", s or "").strip()


@register_provider
class EastmoneyProvider(Provider):
    name = "东方财富"

    # ---------------------------------------------------------- K线
    def get_kline(self, code: str, period: str = "day", limit: int = 250
                  ) -> List[KLine]:
        klt = _PERIOD_KLT.get(period, 101)
        # 用起始日期截断（lmt 参数在部分环境下不生效）
        days_back = int(limit * 1.7) + 30
        beg = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")
        data = self.http.get_json(KLINE_URL, params={
            "secid": to_secid(code),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": klt, "fqt": 1, "beg": beg, "end": 20500101,
        })
        if not isinstance(data, dict) or not data.get("data"):
            raise ProviderError(f"K线数据为空: {code}")
        kls = parse_kline_json(data["data"])
        return kls[-limit:] if limit and len(kls) > limit else kls

    # ------------------------------------------------------ 全市场快照
    def fetch_market_page(self, page: int, page_size: int = 200,
                          sort_field: str = "f6") -> Tuple[int, List[dict]]:
        """返回 (总数, 当页解析后的行列表)。按 sort_field 降序。"""
        data = self.http.get_json(CLIST_URL, params={
            "pn": page, "pz": page_size, "po": 1, "np": 1,
            "fltt": 2, "invt": 2, "fid": sort_field, "fs": FS_ALL,
            "fields": CLIST_FIELDS,
        }, timeout=12)
        d = (data or {}).get("data") or {}
        total = int(d.get("total") or 0)
        diff = d.get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        return total, [parse_clist_row(r) for r in diff if r.get("f12")]

    def fetch_all_market(self, max_count: int = 2000,
                         on_progress: Optional[ProgressCb] = None) -> List[dict]:
        rows: List[dict] = []
        page = 1
        total = None
        while True:
            t, part = self.fetch_market_page(page)
            total = total or t
            rows.extend(part)
            if on_progress:
                on_progress(f"已拉取 {len(rows)}/{min(total, max_count)} 只")
            if not part or len(rows) >= max_count or len(rows) >= total:
                break
            page += 1
            time.sleep(0.15)
        return rows[:max_count]

    # ---------------------------------------------------------- 大盘资金
    def get_market_moneyflow(self):
        """沪深两市主力净额（元）。返回 {名称: 净额}。点查接口，列表接口被限流时仍可用。"""
        data = self.http.get_json(
            "https://push2.eastmoney.com/api/qt/ulist.np/get", params={
                "fltt": 2, "fields": "f12,f14,f62",
                "secids": "1.000001,0.399001",
            }, timeout=10)
        out = {}
        for row in ((data or {}).get("data") or {}).get("diff") or []:
            out[str(row.get("f14"))] = float(row.get("f62") or 0)
        if not out:
            raise ProviderError("大盘资金流为空")
        return out

    # ---------------------------------------------------------- 龙虎榜（v4.4）
    LHB_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

    def get_lhb_board(self, trade_date: str = "", size: int = 300) -> List[dict]:
        """龙虎榜榜单（东财 datacenter 域，与 push2 限流互不影响）。

        返回 [{code, name, net_amt, deal_amt, reason, date}]（金额单位元，
        同股多条上榜原因合并为 net_amt 求和）。trade_date 空 = 最新交易日
        （按 TRADE_DATE 降序取首页后按最新日过滤——实测单日约 150~300 行）。"""
        params = {
            "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
            "columns": "ALL", "pageNumber": 1, "pageSize": size,
            "sortTypes": "-1", "sortColumns": "TRADE_DATE",
            "source": "WEB", "client": "WEB",
        }
        if trade_date:
            # 东财 datacenter filter 只认单引号（双引号实测 0 行）
            params["filter"] = f"(TRADE_DATE='{trade_date}')"
        data = self.http.get_json(self.LHB_URL, params=params, timeout=12)
        rows = ((data or {}).get("result") or {}).get("data") or []
        raw: List[dict] = []
        for r in rows or []:
            code = str(r.get("SECURITY_CODE") or "").strip()
            if not code:
                continue
            raw.append({
                "code": code,
                "name": str(r.get("SECURITY_NAME_ABBR") or "").strip(),
                "net_amt": _num(r.get("BILLBOARD_NET_AMT")),
                "deal_amt": _num(r.get("BILLBOARD_DEAL_AMT")),
                "reason": str(r.get("EXPLANATION") or "").strip(),
                "date": str(r.get("TRADE_DATE") or "")[:10],
            })
        if not raw:
            raise ProviderError("龙虎榜为空（非交易日或接口变更）")
        latest = raw[0]["date"]
        raw = [x for x in raw if x["date"] == latest]
        # 同股多榜单条目（如日榜+3日榜）合并：净额求和、原因拼接
        merged: dict = {}
        for x in raw:
            if x["code"] in merged:
                m = merged[x["code"]]
                m["net_amt"] = (m["net_amt"] or 0) + (x["net_amt"] or 0)
                m["deal_amt"] = (m["deal_amt"] or 0) + (x["deal_amt"] or 0)
                if x["reason"] and x["reason"] not in m["reasons"]:
                    m["reasons"].append(x["reason"])
            else:
                merged[x["code"]] = dict(x, reasons=[x["reason"]])
        out = []
        for m in merged.values():
            m["reason"] = "；".join(m.pop("reasons"))[:60]
            out.append(m)
        out.sort(key=lambda x: x["net_amt"] or 0, reverse=True)
        return out

    # ---------------------------------------------------------- 资讯
    def get_fast_news(self, size: int = 30) -> List[NewsItem]:
        data = self.http.get_json(FAST_NEWS_URL, params={
            "client": "web", "biz": "web_724", "fastColumn": "102",
            "sortEnd": "", "pageSize": size, "req_trace": int(time.time() * 1000),
        }, timeout=10)
        items = ((data or {}).get("data") or {}).get("fastNewsList") or []
        out: List[NewsItem] = []
        for it in items:
            if not isinstance(it, dict) or not it.get("title"):
                continue
            out.append(NewsItem(
                title=str(it.get("title", "")).strip(),
                date=str(it.get("showTime", "")),
                url=str(it.get("url") or ""),
                summary=str(it.get("summary", "")).strip(),
                source="7x24快讯",
            ))
        return out

    def get_announcements(self, code: str, size: int = 10) -> List[NewsItem]:
        data = self.http.get_json(ANN_URL, params={
            "sr": -1, "page_size": size, "page_index": 1, "ann_type": "A",
            "client_source": "web", "stock_list": code, "f_node": 0, "s_node": 0,
        }, timeout=10)
        lst = ((data or {}).get("data") or {}).get("list") or []
        out: List[NewsItem] = []
        for it in lst:
            title = str(it.get("art_title") or it.get("title") or "").strip()
            if not title:
                continue
            art = str(it.get("art_code") or "")
            out.append(NewsItem(
                title=title,
                date=str(it.get("notice_date") or it.get("eiTime") or "")[:10],
                url=f"https://data.eastmoney.com/notices/detail/{code}/{art}.html"
                    if art else "",
                source="公告",
            ))
        return out

    def get_news(self, code: str, size: int = 10) -> List[NewsItem]:
        """个股新闻（搜索接口，jsonp）。失败返回公告列表兜底。"""
        param = {
            "uid": "", "keyword": code, "type": ["cmsArticleWebOld"],
            "client": "web", "clientType": "web", "clientVersion": "curr",
            "param": {"cmsArticleWebOld": {
                "searchScope": "default", "sort": "default",
                "pageIndex": 1, "pageSize": size,
                "preTag": "<em>", "postTag": "</em>",
            }},
        }
        try:
            text = self.http.get_text(SEARCH_URL, params={
                "cb": "jQuery_stockpilot", "param": json.dumps(param, ensure_ascii=False),
            }, timeout=10)
            obj = strip_jsonp(text)
            lst = (((obj or {}).get("result") or {}).get("cmsArticleWebOld")) or []
            out: List[NewsItem] = []
            for it in lst:
                title = _clean(str(it.get("Title") or it.get("title") or ""))
                if not title:
                    continue
                out.append(NewsItem(
                    title=title,
                    date=str(it.get("Date") or it.get("date") or "")[:10],
                    url=str(it.get("Url") or it.get("url") or ""),
                    summary=_clean(str(it.get("Content") or ""))[:200],
                    source="新闻",
                ))
            if out:
                return out
        except ProviderError as exc:
            log.warning("个股新闻接口失败(%s)，回退公告: %s", code, exc)
        return self.get_announcements(code, size)

    # ---------------------------------------------------------- 搜索联想
    def suggest(self, keyword: str, count: int = 10) -> List[Suggestion]:
        if not keyword.strip():
            return []
        data = self.http.get_json(SUGGEST_URL, params={
            "input": keyword.strip(), "type": 14,
            "token": SUGGEST_TOKEN, "count": count,
        }, timeout=8)
        table = ((data or {}).get("QuotationCodeTable") or {})
        out: List[Suggestion] = []
        for it in table.get("Data") or []:
            code = str(it.get("Code") or "")
            if not code.isdigit() or len(code) != 6:
                continue
            out.append(Suggestion(
                code=code, name=str(it.get("Name") or ""),
                market_num=str(it.get("MktNum") or ""),
            ))
        return out

    def get_quotes(self, codes: List[str]) -> Dict[str, Quote]:
        """全市场快照中提取指定股票（用于备用行情）。"""
        want = set(codes)
        out: Dict[str, Quote] = {}
        for row in self.fetch_market_page(1, page_size=200)[1]:
            if row["code"] in want:
                out[row["code"]] = Quote.from_mapping(row["code"], row)
        return out
