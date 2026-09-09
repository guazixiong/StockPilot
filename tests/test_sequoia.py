"""v3.0：K线缓存 + Sequoia 策略等价性 + RPS 横截面。"""
from stockpilot.core import indicators, strategy as stg
from stockpilot.core.kline_store import KLineStore
from stockpilot.core.models import KLine, Quote


def _mk(n, base=10.0, drift=0.004):
    out, px = [], base
    for i in range(n):
        px *= (1 + drift + (0.01 if i % 10 == 0 else 0))
        out.append(KLine(date=f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}",
                         open=px * 0.995, close=px, high=px * 1.01,
                         low=px * 0.985, volume=100000 + i * 100,
                         amount=(100000 + i * 100) * px,
                         change_pct=drift * 100, turnover=2.0))
    return out


# ---------------------------------------------------------------- 缓存
def test_kline_store_roundtrip(tmp_path):
    store = KLineStore(tmp_path / "k.db")
    kls = _mk(40)
    assert store.put("600519", kls) == 40
    got = store.get("600519", 320)
    assert len(got) == 40 and got[0].date == kls[0].date
    assert store.last_date("600519") == kls[-1].date
    assert store.codes() == ["600519"]
    assert store.count() == 40
    # 重复写不膨胀
    store.put("600519", kls)
    assert store.count() == 40


def test_kline_store_warm_and_incremental(tmp_path):
    store = KLineStore(tmp_path / "k.db")
    # warm_up 用假 fetcher：只给前 30 根（模拟"落后"）
    partial = _mk(30)
    n = store.warm_up(lambda c, p, l: partial, ["600519", "000001"], 320)
    assert n == 2 and store.count() == 60
    # daily_incremental：昨日数据 → 落后于今天 → 重拉
    n2 = store.daily_incremental(lambda c, p, l: _mk(31), ["600519", "000001"])
    assert n2 == 2


def test_kline_store_get_empty(tmp_path):
    store = KLineStore(tmp_path / "k.db")
    assert store.get("999999") == []
    assert store.last_date("999999") == ""


# ---------------------------------------------------------------- Sequoia 等价性
def test_turtle_equivalent():
    """海龟：close>前20日high + amount>1亿 + 阳线 + 真涨。"""
    kls = _mk(30)
    # 人为把前 20 日 high 压低，末根突破
    for k in kls[:-1]:
        k.high = min(k.high, kls[-1].close * 0.95)
    ind = indicators.analyze(kls)
    # 真实场景成交额以亿计；测试给足 1.2 亿（12000 万元）
    q = Quote(code="600519", price=kls[-1].close, amount=12000.0)
    ctx = stg.build_ctx(ind, q)
    s = next(x for x in stg.builtin_strategies() if x.name == "海龟20日突破")
    hit, rules = stg.eval_rules(s.buy_rules, ctx)
    assert hit, f"应命中: {ctx['high20_prev']} vs {q.price} amount={q.amount}"


def test_limit_up_shakeout_equivalent():
    """涨停洗盘：昨涨停+今阴+放量2倍+低不破昨收。"""
    kls = _mk(30)
    # 昨日涨停：close[-2] >= close[-3]*1.095
    kls[-3].close = 10.0
    kls[-2].close = 11.0                            # +10% 涨停
    kls[-1].open = 11.5                             # 今高开
    kls[-1].close = 11.2                            # 收阴
    kls[-1].volume = kls[-2].volume * 2.5           # 放量
    kls[-1].low = 11.0                              # 不破昨收
    ind = indicators.analyze(kls)
    q = Quote(code="600519", price=kls[-1].close, low=kls[-1].low)
    ctx = stg.build_ctx(ind, q)
    assert ctx["prev_limit_up"] is True
    assert ctx["is_bear_today"] is True
    assert ctx["vol_vs_prev"] > 2.0
    s = next(x for x in stg.builtin_strategies() if x.name == "涨停洗盘")
    hit, _ = stg.eval_rules(s.buy_rules, ctx)
    assert hit


def test_high_tight_flag_equivalent():
    """高窄旗形：40日涨>60% + 近10日收敛<15% + 高位抗跌 + 缩量。"""
    kls = _mk(45)
    # 前 35 根强动量（每根+3% → 区间约 2.7 倍），后 10 根窄幅整理
    for i, k in enumerate(kls[:35]):
        scale = 1.0 * (1.03 ** i)
        k.open, k.close = 10 * scale * 0.99, 10 * scale
        k.high, k.low = 10 * scale * 1.005, 10 * scale * 0.995
    base = kls[34].close
    for k in kls[35:]:
        k.open, k.close = base * 0.998, base
        k.high, k.low = base * 1.01, base * 0.99   # 振幅≈2%
    kls[-1].volume = sum(x.volume for x in kls[-21:-1]) / 20 * 0.5  # 缩量
    ind = indicators.analyze(kls)
    q = Quote(code="600519", price=kls[-1].close)
    ctx = stg.build_ctx(ind, q)
    assert ctx["amp40"] > 1.6
    assert ctx["amp10"] < 1.15
    assert ctx["high10_hold"] is True
    assert ctx["vol_vs_ma20"] < 0.6
    s = next(x for x in stg.builtin_strategies() if x.name == "高窄旗形突破")
    hit, _ = stg.eval_rules(s.buy_rules, ctx)
    assert hit


def test_ma_volume_cross_equivalent():
    s = next(x for x in stg.builtin_strategies() if x.name == "均线金叉放量")
    ctx = {"ma5": 9.6, "ma20": 9.5, "prev_ma5": 9.0, "prev_ma20": 9.5,
           "vol_vs_ma20": 1.6}
    hit, rules = stg.eval_rules(s.buy_rules, ctx)
    assert hit and len(rules) == 3
    # 反例：昨日已金叉则不算"上穿"
    ctx2 = dict(ctx, prev_ma5=9.6)
    assert not stg.eval_rules(s.buy_rules, ctx2)[0]


def test_uptrend_limit_down_equivalent():
    s = next(x for x in stg.builtin_strategies() if x.name == "趋势跌停反包")
    # 规则：昨MA20>MA60 + 跌至昨收0.905以下 + 量>20日均量2倍
    # 跌停价位规则用比值字段表达：prev_close 归一为 1.0、price=0.90
    ctx = {"prev_ma20_gt_ma60": True, "vol_vs_ma20": 2.2,
           "prev_close": 1.0, "price": 0.90}
    hit, rules = stg.eval_rules(s.buy_rules, ctx)
    assert hit and len(rules) == 3
    # 反例：非跌停（0.93）不命中
    assert not stg.eval_rules(s.buy_rules, dict(ctx, price=0.93))[0]


# ---------------------------------------------------------------- RPS
def test_rps_ranking():
    from stockpilot.core.rps import rps_breakout
    # 构造 5 只小市场：涨幅各不同
    market = {}
    for i, drift in enumerate((0.30, 0.10, 0.02, -0.05, -0.20)):
        market[f"60000{i}"] = _mk(130, base=10.0, drift=drift)
    res = rps_breakout(market, period=120, rps_min=80)
    codes = [r["code"] for r in res]
    # 涨幅 30% 的那只应 RPS 最高且入选
    assert "600000" in codes
    top = res[0]
    assert top["rps"] >= 95
    assert "rps" in top and "pct_change" in top
