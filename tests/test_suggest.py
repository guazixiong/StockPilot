"""新浪搜索联想（ETF + 股票）解析。"""
from stockpilot.core.providers.sina import SinaProvider


def _sample_suggest(kw: str) -> str:
    # 真实格式（GBK 解码后）——含 ETF、股票、场外基金混合
    if kw == "沪深300ETF":
        return ('var suggestvalue="沪深300ETF嘉实,22,159919,of159919,沪深300ETF嘉实,,'
                '沪深300ETF嘉实,99,1,,,;沪深300ETF华泰柏瑞,203,510300,sh510300,'
                '沪深300ETF华泰柏瑞,,沪深300ETF华泰柏瑞,99,1,,,;')
    if kw == "茅台":
        return 'var suggestvalue="贵州茅台,11,600519,sh600519,贵州茅台,,贵州茅台,99,1,ESG,,";'
    return ""


def test_suggest_etf_and_stock(monkeypatch):
    def fake_get_text(self, url, **kw):
        return _sample_suggest(url.split("key=")[-1])
    monkeypatch.setattr("stockpilot.core.providers.base.HttpClient.get_text",
                        fake_get_text)
    from stockpilot.core.providers.base import HttpClient

    sp = SinaProvider(HttpClient())
    etfs = sp.suggest("沪深300ETF", 5)
    assert len(etfs) == 2
    # 203=沪深ETF（场内），22 也可能是场外——两条例子均应为 6 位可交易代码
    assert etfs[0].code == "159919" and "沪深300ETF" in etfs[0].name
    assert etfs[1].code == "510300"

    stocks = sp.suggest("茅台", 5)
    assert stocks[0].code == "600519"
    assert stocks[0].name == "贵州茅台"


def test_suggest_filters_non_tradeable(monkeypatch):
    """类型码不在白名单（如 60/72 场外申赎码）的条目应被过滤。"""
    def fake_get_text(self, url, **kw):
        return ('var suggestvalue="某场外基金,60,OF123456,of123456,某场外基金,,'
                '某场外基金,99,1,,,;创业板ETF易方达,203,159915,sz159915,'
                '创业板ETF易方达,,创业板ETF易方达,99,1,,,;"')
    monkeypatch.setattr("stockpilot.core.providers.base.HttpClient.get_text",
                        fake_get_text)
    from stockpilot.core.providers.base import HttpClient

    items = SinaProvider(HttpClient()).suggest("x", 5)
    assert [x.code for x in items] == ["159915"]
