"""v4.4.2：性能优化防回归（新浪并行分页 + 主机熔断器）。"""
import time

import pytest

from stockpilot.core.providers.base import (HostBreaker, HttpClient,
                                             ProviderError)


def _breaker_fresh():
    HostBreaker._fail.clear()
    HostBreaker._open_until.clear()
    HostBreaker._last_fail_ts.clear()
    return HostBreaker(threshold=3, cool_down=60.0)


@pytest.fixture
def fake_clock(monkeypatch):
    """假时钟：跨窗失败各计 1 次（窗口 10s），strftime 保留。"""
    import stockpilot.core.providers.base as base_mod
    clock = {"t": 1000.0}
    real_time = base_mod.time

    class _T:
        time = staticmethod(lambda: clock["t"])
        strftime = staticmethod(real_time.strftime)
        localtime = staticmethod(real_time.localtime)
        sleep = staticmethod(lambda s: None)

    monkeypatch.setattr(base_mod, "time", _T)
    return clock


def test_breaker_opens_after_threshold(monkeypatch, fake_clock):
    """跨时间窗 3 败 → 熔断期内 check 直接抛错（不再发请求）。"""
    b = _breaker_fresh()
    url = "https://break-test.example.com/a"
    for i in range(3):
        fake_clock["t"] += 20.0        # 每次失败间隔 >10s：各计 1 次
        b.record(url, False)
    try:
        b.check("https://break-test.example.com/c")
        assert False, "3 败应熔断"
    except ProviderError as e:
        assert "熔断" in str(e)


def test_breaker_success_resets(monkeypatch, fake_clock):
    """成功请求清零计数（host 恢复时不误熔断）。"""
    b = _breaker_fresh()
    u = "https://ok.example.com/a"
    for i in range(3):
        fake_clock["t"] += 20.0
        b.record(u, False)
    b.record(u, True)                             # 恢复清零
    for i in range(2):
        fake_clock["t"] += 20.0
        b.record(u, False)
    b.check("https://ok.example.com/b")           # 只有 2 败：不熔断
    fake_clock["t"] += 20.0
    b.record(u, False)
    try:
        b.check("https://ok.example.com/c")
        assert False, "恢复后再 3 败应熔断"
    except ProviderError:
        pass


def test_breaker_same_window_dedup(monkeypatch, fake_clock):
    """v4.4.3 同轮去重：10s 内重复失败只计 1 次（扫描并发 12 线程
    逐股撞墙时不把好源累计熔断——本轮真实事故）。"""
    b = _breaker_fresh()
    u = "https://dedup.example.com/a"
    for _ in range(50):           # 同窗 50 连败
        b.record(u, False)
    assert HostBreaker._fail.get("dedup.example.com") == 1
    b.check("https://dedup.example.com/x")        # 未熔断
    for i in range(2):
        fake_clock["t"] += 20.0
        b.record(u, False)
    try:
        b.check("https://dedup.example.com/y")
        assert False, "跨窗累计 3 败应熔断"
    except ProviderError:
        pass


def test_breaker_host_isolation(monkeypatch, fake_clock):
    """不同主机互不影响（push2 熔断不拖累 datacenter）。"""
    b = _breaker_fresh()
    for i in range(5):
        fake_clock["t"] += 20.0
        b.record("https://push2.example.com/x", False)
    try:
        b.check("https://push2.example.com/y")
        assert False
    except ProviderError:
        pass
    b.check("https://datacenter.example.com/z")   # 另一主机正常


def test_http_client_uses_breaker():
    """HttpClient 集成：熔断中的主机 get_text 直接抛（不发请求）。"""
    HostBreaker._fail.clear()
    HostBreaker._open_until.clear()
    b = HostBreaker(threshold=1, cool_down=60.0)
    b.record("https://closed.example.com/", False)
    hc = HttpClient(timeout=1)
    hc._breaker = b
    t0 = time.time()
    try:
        hc.get_text("https://closed.example.com/api", retries=2, timeout=1)
        assert False, "熔断中应抛"
    except ProviderError:
        pass
    assert time.time() - t0 < 0.5   # 未发生真实重试等待（0.5+1.0s sleep）


def test_sina_fetch_all_market_parallel(monkeypatch):
    """新浪全市场：分页并行（ThreadPoolExecutor），单页失败不拖垮整体。"""
    from stockpilot.core.providers.sina import SinaProvider

    class _FakeHttp:
        def __init__(self):
            self.page_calls = []

        def get_text(self, url, *, params=None, headers=None, **kw):
            page = (params or {}).get("page", 1)
            self.page_calls.append(page)
            # 总数接口与页接口共用 get_text：按 num 参数区分
            if (params or {}).get("num") is None:
                return "count=5530"
            if page == 3:
                raise ProviderError("单页炸了")     # 容错：其余页仍要拿到
            import json
            code = 600000 + page
            return json.dumps([{
                "code": str(code), "name": f"股{page}", "trade": "10.0",
                "changepercent": "1.0", "volume": "1000", "amount": "2000000",
                "per": "5", "pb": "1", "mktcap": "3000000",
                "nmc": "2000000", "turnoverratio": "2.0",
            } for _ in range(2)])

    em = SinaProvider(_FakeHttp())
    rows = em.fetch_all_market(300)
    # 第 1 页 + 并行页（pages = min(5530,300)/100 → 3 页）
    assert len(rows) >= 2          # 第 3 页失败被吞，第 1、2 页照拿
    assert all(r["code"].startswith("60000") for r in rows)
    assert {p for p in em.http.page_calls} == {1, 2, 3}   # 确实并行拉了 3 页
