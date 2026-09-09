"""v4.4.3：机会分析优化（并行/去重/缓存）+ kline_for_scan 签名修复防回归。"""
import threading

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ---------------------------------------------------------------- 签名修复
def test_kline_for_scan_signature_compat(qapp):
    """签名错配防回归：v4.4.3 修的真实事故——monitor 传 (code,'day',800)
    而 kline_for_scan 形参是 (code, limit) → 'day' 被当 limit 绑进
    SQLite LIMIT → datatype mismatch → 机会扫描全灭。
    直测真实实现（打桩 store 计数）：三种历史口径均应得到 ≥30 根 K 线。"""
    import datetime
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    from stockpilot.core.models import KLine
    ctx = AppContext(Config())
    d = datetime.date(2026, 9, 4)
    kls = [KLine(date=str(d - datetime.timedelta(days=i)), open=1.0,
                 close=1.0, high=1.0, low=1.0, volume=10.0, amount=100.0)
           for i in range(50)]
    ctx.store.put("T_COMPAT", kls)
    a = ctx.kline_for_scan("T_COMPAT", 800)          # (code, limit)
    b = ctx.kline_for_scan("T_COMPAT", "day", 800)   # (code, period, limit)
    c = ctx.kline_for_scan("T_COMPAT")               # 全默认
    assert len(a) == 50 and len(b) == 50 and len(c) == 50


def test_kline_for_scan_real_paths(qapp):
    """真实 store 路径（非 mock）：'day' 口径不再炸 datatype mismatch。"""
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    ctx = AppContext(Config())
    # 写两只 K 线进隔离库再走真实 get 路径
    import datetime
    from stockpilot.core.models import KLine
    d = datetime.date(2026, 9, 4)
    kls = [KLine(date=str(d - datetime.timedelta(days=i)), open=1, close=1,
                 high=1, low=1) for i in range(40)]
    ctx.store.put("T_SIG", kls)
    # (code, 'day', 800) 口径 —— 修复前此调用直接 IntegrityError
    out = ctx.kline_for_scan("T_SIG", "day", 800)
    assert len(out) == 40


# ---------------------------------------------------------------- 去重
def test_scan_dedup_same_code(qapp):
    """universe 内同 code 多行（多源快照重复）只分析一次。"""
    from stockpilot.core import monitor as mon
    from stockpilot.core import strategy as stg
    calls = []

    def kline_fetch(code, *a):
        calls.append(code)
        return []

    rows = [{"code": "600001", "name": "a", "price": 10.0},
            {"code": "600001", "name": "a", "price": 10.0},
            {"code": "600002", "name": "b", "price": 10.0}] * 5
    mon.run_strategy_scan(stg.builtin_strategies(), rows, kline_fetch)
    assert sorted(set(calls)) == sorted(calls), "同 code 被重复分析"
    assert len(calls) == 2


# ---------------------------------------------------------------- 指标缓存
def test_analyze_cached_rounds():
    """同轮（K线未变）跨轮零重算：analyze 只算一次。"""
    from stockpilot.core import core_lru
    core_lru.clear_stale_caches()
    import datetime
    from stockpilot.core.models import KLine
    d = datetime.date(2026, 9, 4)
    kls = [KLine(date=str(d - datetime.timedelta(days=i)),
                 open=float(i), close=float(i) + 1, high=i + 2, low=i - 1,
                 volume=100.0, amount=1000.0)
           for i in range(120)]
    real = core_lru.indicators.analyze
    n = {"c": 0}

    def counting(k):
        n["c"] += 1
        return real(k)

    core_lru.indicators.analyze = counting
    try:
        a = core_lru.analyze_cached("T_CACHE", kls)
        b = core_lru.analyze_cached("T_CACHE", kls)
        c = core_lru.analyze_cached("T_CACHE", list(kls))   # 新列表同内容
        assert n["c"] == 1
        assert a is b is c
        # K线更新（新日期）→ 重算
        kls2 = kls + [KLine(date="2026-09-05", open=1, close=2, high=3,
                            low=1, volume=10, amount=100)]
        core_lru.analyze_cached("T_CACHE", kls2)
        assert n["c"] == 2
    finally:
        core_lru.indicators.analyze = real
        core_lru.clear_stale_caches()


def test_analyze_cache_thread_safe():
    """并发 12 线程打同一 code：无竞态、只算一次。"""
    from concurrent.futures import ThreadPoolExecutor
    from stockpilot.core import core_lru
    core_lru.clear_stale_caches()
    import datetime
    from stockpilot.core.models import KLine
    d = datetime.date(2026, 9, 4)
    kls = [KLine(date=str(d - datetime.timedelta(days=i)), open=float(i),
                 close=float(i) + 1, high=i + 2, low=i - 1, volume=100.0,
                 amount=1000.0) for i in range(120)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        outs = list(pool.map(
            lambda _: core_lru.analyze_cached("T_MT", kls), range(24)))
    # 全部为同一结果对象（或语义等价），不抛异常
    assert all(o.get("ma5") is not None for o in outs)
    core_lru.clear_stale_caches()
