"""v4.4：龙虎榜数据链（G1 第二切片）单测。"""
from stockpilot.core.providers.eastmoney import EastmoneyProvider


def _row(code="003040", name="楚天龙", net=1.342e9, deal=2.0e9,
         reason="日涨幅偏离值达到7%", date="2026-09-04 00:00:00"):
    return {"SECURITY_CODE": code, "SECURITY_NAME_ABBR": name,
            "BILLBOARD_NET_AMT": net, "BILLBOARD_DEAL_AMT": deal,
            "EXPLANATION": reason, "TRADE_DATE": date}


class _FakeHttp:
    """注入假 JSON 的 http 桩。"""

    def __init__(self, data):
        self._data = data
        self.last_params = None

    def get_json(self, url, params=None, timeout=10, **kw):
        self.last_params = params
        return self._data


def _provider(rows):
    data = {"result": {"data": rows}}
    return EastmoneyProvider(_FakeHttp(data)), data


def test_lhb_parse_and_sort():
    """解析字段名/单位/降序；filter 不传时按最新日过滤。"""
    rows = [_row(net=1.0e8),
            _row(code="000592", name="平潭发展", net=9.6e8,
                 reason="日换手率达到20%"),
            _row(code="600869", name="远东股份", net=-2.0e8)]
    em, _ = _provider(rows)
    out = em.get_lhb_board()
    assert len(out) == 3
    assert out[0]["code"] == "000592"      # 净买入降序
    assert out[-1]["net_amt"] == -2.0e8
    assert out[0]["date"] == "2026-09-04"  # 时间后缀剥掉
    assert out[0]["name"] == "平潭发展"


def test_lhb_merge_same_code():
    """同股同日多条榜单（日榜+3日榜，真实场景：一日内多口径上榜）：
    净额求和、原因去重拼接；跨日条目被过滤。"""
    rows = [_row(net=1.0e8, reason="日涨幅偏离值达到7%"),
            _row(net=0.5e8, reason="连续三个交易日涨幅偏离20%"),
            _row(net=1.0e8, reason="日涨幅偏离值达到7%"),   # 同日重复原因
            _row(net=2.0e8, date="2026-09-03 00:00:00"),    # 跨日：应被过滤
            _row(code="000592", net=-1.0e8)]
    em, _ = _provider(rows)
    out = em.get_lhb_board()
    assert len(out) == 2                   # 合并去重
    top = next(x for x in out if x["code"] == "003040")
    assert top["net_amt"] == 2.5e8         # 同日两条求和；跨日 2.0e8 被过滤
    assert top["reason"].count("7%") == 1  # 原因去重
    assert "三个交易日" in top["reason"]


def test_lhb_cross_day_filter():
    """跨日数据只保留最新上榜日。"""
    rows = [_row(date="2026-09-04 00:00:00", net=1.0e8),
            _row(code="000592", net=5.0e8,
                 date="2026-09-03 00:00:00")]
    em, _ = _provider(rows)
    out = em.get_lhb_board()
    assert len(out) == 1
    assert out[0]["date"] == "2026-09-04"


def test_lhb_explicit_date_uses_single_quote_filter():
    """显式日期：东财 datacenter 只认单引号 filter（双引号实测 0 行）。"""
    em, _ = _provider([_row()])
    em.http.last_params = None
    try:
        em.get_lhb_board(trade_date="2026-09-04")
    except Exception:
        pass
    f = em.http.last_params.get("filter")
    assert f == "(TRADE_DATE='2026-09-04')"
    assert '"' not in f


def test_lhb_empty_raises():
    """空结果 → ProviderError（供上层降级提示）。"""
    em, _ = _provider([])
    from stockpilot.core.providers.base import ProviderError
    try:
        em.get_lhb_board()
        assert False, "应抛 ProviderError"
    except ProviderError:
        pass
