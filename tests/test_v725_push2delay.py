"""v7.2.5 回归闸：东财 push2 clist 定向拒绝的修复（app.log 425 条
『东财全市场列表失败，切换新浪』/180 条 Connection aborted 的根因闭环）。

现象：东财对本机的 push2.eastmoney.com/api/qt/clist/get 端点定向拒绝
——TLS 握手正常、HTTP 请求一发出即被 RST（6/6 稳定复现），同主机
ulist.np 短暂可用后同样被拒（8 连发 0/8）。应用自 v7.2.2 修掉死代理后
仍 100% 失败，长期跑在新浪降级源上。

修复：clist/ulist 主源换 push2delay.eastmoney.com（实测 5912 只全市场、
f62 主力净额字段齐全、8/8 稳定），push2 域留作回退。
"""
import pytest


def test_clist_primary_is_push2delay():
    """主源必须是 push2delay；push2 只能作为回退域存在。"""
    from stockpilot.core.providers import eastmoney as em
    assert em.CLIST_URL.startswith(
        "https://push2delay.eastmoney.com/api/qt/clist/get"), (
        "clist 主源必须是 push2delay（push2 对本机定向拒绝，app.log 实锤）")
    assert em.CLIST_FALLBACK_URL.startswith("https://push2.eastmoney.com/")


def test_fetch_market_page_falls_back_to_push2(monkeypatch):
    """主域失败时必须回退 push2 域重试（防 CDN 单点抖动）。"""
    from stockpilot.core.providers.base import HttpClient, ProviderError
    from stockpilot.core.providers.eastmoney import EastmoneyProvider

    calls = []

    def fake_get_json(url, **kw):
        calls.append(url)
        if "push2delay" in url:
            raise ProviderError("主域熔断（模拟）")
        return {"data": {"total": 1, "diff": [
            {"f12": "600519", "f14": "贵州茅台", "f2": "1500",
             "f6": "3000000000", "f62": "100000000", "f20": "100"}]}}

    http = HttpClient()
    prov = EastmoneyProvider(http)
    monkeypatch.setattr(http, "get_json", fake_get_json)
    total, rows = prov.fetch_market_page(1)
    assert total == 1 and rows[0]["code"] == "600519"
    assert len(calls) == 2 and "push2delay" in calls[0] and calls[1].startswith(
        "https://push2.eastmoney.com/")


def test_fetch_market_page_both_dead_raises():
    """两域都挂时抛 ProviderError（交给上层降级新浪），且异常带最后错误。"""
    from stockpilot.core.providers.base import HttpClient, ProviderError
    from stockpilot.core.providers.eastmoney import EastmoneyProvider

    http = HttpClient()
    prov = EastmoneyProvider(http)

    def always_fail(url, **kw):
        raise ProviderError(f"请求失败 {url}: 模拟双域全挂")

    http.get_json = always_fail
    with pytest.raises(ProviderError, match="模拟双域全挂"):
        prov.fetch_market_page(1)


def test_moneyflow_uses_push2delay_with_fallback(monkeypatch):
    """大盘资金流：push2delay 主源 + push2 回退。"""
    from stockpilot.core.providers.base import HttpClient, ProviderError
    from stockpilot.core.providers.eastmoney import EastmoneyProvider

    calls = []

    def fake_get_json(url, **kw):
        calls.append(url)
        if "push2.eastmoney.com" in url:      # 注意 push2delay 也含 "push2."
            raise ProviderError("模拟 push2 域挂")
        return {"data": {"diff": [
            {"f14": "上证指数", "f62": "-6591404032"},
            {"f14": "深证成指", "f62": "-5453590784"}]}}

    http = HttpClient()
    prov = EastmoneyProvider(http)
    monkeypatch.setattr(http, "get_json", fake_get_json)
    out = prov.get_market_moneyflow()
    assert out["上证指数"] == -6591404032.0
    assert len(calls) == 1 and "push2delay" in calls[0]
