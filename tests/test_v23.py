"""v2.3：筹码引擎 + 导入导出 + 聊天助手（核心逻辑离线）。"""
import json

from stockpilot.core import cyq, strategy as stg
from stockpilot.core.models import KLine, Quote


def _mk_klines(n=80, base=10.0, turnover=2.0):
    out = []
    px = base
    for i in range(n):
        px = px * (1 + (0.004 if i % 2 else -0.002))
        out.append(KLine(date=f"d{i}", open=px * 0.999, close=px,
                         high=px * 1.01, low=px * 0.99, volume=50000 + i * 100,
                         amount=(50000 + i * 100) * px, change_pct=0.1,
                         turnover=turnover))
    return out


def test_cyq_distribution_reasonable():
    kls = _mk_klines()
    d = cyq.cyq_distribution(kls)
    assert d["profit_ratio"] is not None and 0 <= d["profit_ratio"] <= 100
    assert 0 < d["avg_cost"] < d["cost_high"]
    assert d["cost_low"] < d["cost_high"]
    assert d["concentration"] > 0
    assert 0 <= d["near_current"] <= 100
    # 上涨序列 → 大多数筹码应获利
    assert d["profit_ratio"] > 50


def test_cyq_curve_shape():
    kls = _mk_klines(60)
    c = cyq.cyq_curve(kls)
    assert c is not None
    prices, weights = c
    assert len(prices) == len(weights) == cyq.PRICE_BUCKETS
    assert abs(sum(weights) - 1.0) < 0.02   # 归一化


def test_cyq_short_data_returns_none():
    assert cyq.cyq_distribution([])["profit_ratio"] is None
    assert cyq.cyq_curve(_mk_klines(10)) is None


def test_cyq_in_indicators_analyze():
    import sys
    sys.path.insert(0, "src")
    from stockpilot.core import indicators
    ctx = indicators.analyze(_mk_klines(90))
    assert ctx.get("cyq_profit") is not None
    assert ctx.get("cyq_avg_cost") is not None


def test_builtin_now_15_with_cyq_strategies():
    names = [s.name for s in stg.builtin_strategies()]
    assert "低位密集突破" in names and "获利盘洗盘企稳" in names
    assert len(names) == 21


def test_cyq_strategy_evaluable():
    s = next(x for x in stg.builtin_strategies() if x.name == "低位密集突破")
    import sys
    sys.path.insert(0, "src")
    from stockpilot.core import indicators
    kls = _mk_klines(90)
    ind = indicators.analyze(kls)
    quote = Quote(code="600519", name="t", price=kls[-1].close,
                  volume_ratio=2.0)
    ctx = stg.build_ctx(ind, quote)
    hit, rules = stg.eval_rules(s.buy_rules, ctx)
    for r in rules:          # 命中文案全中文
        assert any(z in r for z in ("筹码", "现价", "量比")), r


def test_transfer_roundtrip(tmp_path):
    """导入导出模块：自选 CSV 往返。"""
    import sys
    sys.path.insert(0, "src")
    from stockpilot.core.storage import Config

    import threading as _th
    class FakeCfg(Config):
        def __init__(self):
            self._lock = _th.Lock()
            self.data = {"watchlist": [], "ai": {"profiles": {}},
                         "monitor": {"positions": {}, "history": [], "fired": []}}
            self.path = tmp_path / "cfg.json"

    cfg = FakeCfg()
    cfg.add_watch("600519", "贵州茅台")
    # 直接验证底层写读（transfer UI 层依赖 QFileDialog，由 GUI 实测覆盖）
    from stockpilot.core.export_csv import rows_to_csv
    f = tmp_path / "watch.csv"
    rows = [{"code": x["code"], "name": x["name"]} for x in cfg.get_watchlist()]
    rows_to_csv(str(f), ["code", "name"], rows)
    content = open(f, encoding="utf-8-sig").read()
    assert "600519" in content and "贵州茅台" in content
