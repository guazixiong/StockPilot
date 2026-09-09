"""数据源解析器（离线样例报文）。"""
import json

import pytest

from stockpilot.core.providers.eastmoney import (parse_clist_row,
                                                 parse_kline_json, strip_jsonp)
from stockpilot.core.providers.tencent import parse_quote_text


def _tencent_payload() -> str:
    f = ["1"] * 55
    f[1] = "贵州茅台"
    f[2] = "600519"
    f[3] = "1700.00"   # 现价
    f[4] = "1690.00"   # 昨收
    f[5] = "1695.00"   # 今开
    f[9] = "1699.90"; f[10] = "12"     # 买一 价/量
    f[11] = "1699.80"; f[12] = "30"    # 买二
    f[13] = "1699.70"; f[14] = "44"
    f[15] = "1699.60"; f[16] = "55"
    f[17] = "1699.50"; f[18] = "66"    # 买五
    f[19] = "1700.10"; f[20] = "8"     # 卖一
    f[21] = "1700.20"; f[22] = "21"
    f[23] = "1700.30"; f[24] = "33"
    f[25] = "1700.40"; f[26] = "44"
    f[27] = "1700.50"; f[28] = "99"    # 卖五
    f[30] = "20260902150000"
    f[31] = "+10.00"
    f[32] = "0.59"
    f[33] = "1710.00"
    f[34] = "1688.00"
    f[36] = "30000"    # 成交量(手)
    f[37] = "512345"   # 成交额(万)
    f[38] = "0.31"     # 换手
    f[39] = "21.30"    # PE
    f[43] = "1.30"     # 振幅
    f[44] = "21000.00"  # 流通市值(亿)
    f[45] = "21350.00"  # 总市值
    f[46] = "8.50"     # PB
    f[47] = "1859.00"  # 涨停
    f[48] = "1521.00"  # 跌停
    f[49] = "1.20"     # 量比
    return 'v_sh600519="' + "~".join(f) + '";'


def test_tencent_parse():
    quotes = parse_quote_text(_tencent_payload())
    assert "sh600519" in quotes
    q = quotes["sh600519"]
    assert q.code == "600519"
    assert q.name == "贵州茅台"
    assert q.price == 1700.0
    assert q.prev_close == 1690.0
    assert q.change_pct == 0.59
    assert q.volume == 30000.0
    assert q.amount == 512345.0
    assert q.turnover_rate == 0.31
    assert q.pe == 21.30
    assert q.total_mv == 21350.0
    assert q.volume_ratio == 1.20
    assert q.limit_up == 1859.0
    # 五档盘口
    assert q.bid1_price == 1699.90 and q.bid1_vol == 12
    assert q.bid5_price == 1699.50 and q.bid5_vol == 66
    assert q.ask1_price == 1700.10 and q.ask1_vol == 8
    assert q.ask5_price == 1700.50 and q.ask5_vol == 99


def test_tencent_index_short_fields():
    f = ["1"] * 36
    f[1] = "上证指数"
    f[2] = "000001"
    f[3] = "3200.50"
    f[4] = "3180.00"
    f[32] = "0.64"
    text = 'v_sh000001="' + "~".join(f) + '";'
    q = parse_quote_text(text)["sh000001"]
    assert q.price == 3200.50
    assert q.turnover_rate is None  # 指数无换手字段，防御式为 None


def test_kline_parse():
    data = {"klines": [
        "2026-08-29,1700.00,1710.00,1715.00,1695.00,30000,5123450000,0.9,0.59,10.0,0.31",
        "2026-09-01,1710.00,1699.00,1712.00,1690.00,28000,4780000000,1.1,-0.64,-11.0,0.29",
    ]}
    kls = parse_kline_json(data)
    assert len(kls) == 2
    assert kls[0].date == "2026-08-29"
    assert kls[0].close == 1710.00
    assert kls[0].change_pct == 0.59
    assert kls[1].change_pct == -0.64


def test_clist_row():
    row = {"f12": "600519", "f14": "贵州茅台", "f2": 1700.0, "f3": 0.59,
           "f5": 30000, "f6": 5123450000, "f8": 0.31, "f9": 21.3,
           "f10": 1.2, "f20": 2135000000000, "f21": 2100000000000,
           "f23": 8.5, "f62": 123456}
    out = parse_clist_row(row)
    assert out["code"] == "600519"
    assert out["name"] == "贵州茅台"
    assert out["amount"] == 512345.0      # 元 -> 万
    assert out["total_mv"] == 21350.0     # 元 -> 亿
    assert out["float_mv"] == 21000.0
    assert out["main_inflow"] == pytest.approx(12.3456)   # 元 -> 万（v4.3 统一）


def test_strip_jsonp():
    obj = {"result": {"cmsArticleWebOld": [{"Title": "t", "Date": "d"}]}}
    text = "jQuery123" + json.dumps(obj, ensure_ascii=False) + ");"
    assert strip_jsonp(text) == obj


def test_sina_market_row():
    from stockpilot.core.providers.sina import SinaProvider
    row = SinaProvider._market_row({
        "symbol": "sh600519", "code": "600519", "name": "贵州茅台",
        "trade": "1700.00", "pricechange": 10.0, "changepercent": 0.59,
        "open": "1695.00", "high": "1710.00", "low": "1688.00",
        "settlement": "1690.00", "volume": 3000000, "amount": 5123450000,
        "per": 21.3, "pb": 8.5, "mktcap": 213500000, "nmc": 210000000,
        "turnoverratio": 0.31,
    })
    assert row["code"] == "600519" and row["name"] == "贵州茅台"
    assert row["price"] == 1700.0
    assert row["volume"] == 30000.0        # 股 -> 手
    assert row["amount"] == 512345.0       # 元 -> 万
    assert row["total_mv"] == 21350.0      # 万元 -> 亿
    assert row["float_mv"] == 21000.0
    assert row["turnover_rate"] == 0.31
    assert row["volume_ratio"] is None
