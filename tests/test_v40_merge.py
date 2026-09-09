"""v4.0：同股合并 / min_score 过滤 / MA20 趋势过滤 / 权重与流动性配置。"""
from stockpilot.core import indicators, strategy as stg
from stockpilot.core.models import KLine, Quote, TradeSignal
from stockpilot.core.monitor import run_strategy_scan


def _mk_kls(n=120, base=10.0, drift=0.004, volume=200000.0):
    out, px = [], base
    for i in range(n):
        px *= (1 + drift)
        out.append(KLine(date=f"d{i}", open=px * 0.995, close=px,
                         high=px * 1.01, low=px * 0.985, volume=volume,
                         amount=volume * px, change_pct=drift * 100,
                         turnover=2.0))
    return out


def _rows(price, name="测试股"):
    return [{"code": "600519", "name": name, "price": price,
             "change_pct": 2.0, "turnover_rate": 8.0, "volume_ratio": 1.8}]


def test_same_code_merged_into_one_card():
    """同一股命中多套策略 → 只产出一张卡，策略徽章聚合、分取最高。"""
    # 构造K线让多套策略同时命中：上升趋势股回踩 MA10 → 强势回调+海龟难同中
    # 用直接可控方式：两套"必然命中"的自定义策略
    s1 = stg.Strategy(name="策略A", buy_rules=[stg.Rule("price", ">", 0)])
    s2 = stg.Strategy(name="策略B", buy_rules=[stg.Rule("price", ">", 0)])
    kls = _mk_kls()
    sigs = run_strategy_scan([s1, s2], _rows(11.0),
                             lambda c, p, l: kls)
    assert len(sigs) == 1, f"同股两策略应合并为1张卡, got {len(sigs)}"
    sig = sigs[0]
    assert set(sig.hit_rules) == {"策略A", "策略B"}
    assert "命中2套策略" in sig.reason
    # 合并口径：策略名取高分的（hit_rules 展示两套）
    assert sig.strategy in ("策略A", "策略B")


def test_merge_takes_conservative_levels():
    """合并时止损取高、目标取低（最保守）。用横盘K线使 MA 不干扰止损。"""
    s1 = stg.Strategy(name="A止损高", buy_rules=[stg.Rule("price", ">", 0)],
                     stop_loss_pct=8, take_profit_pct=10)
    s2 = stg.Strategy(name="B止损低", buy_rules=[stg.Rule("price", ">", 0)],
                      stop_loss_pct=4, take_profit_pct=20)
    kls = _mk_kls(120, drift=0.0)          # 横盘 → MA≈10
    sigs = run_strategy_scan([s1, s2], _rows(10.0), lambda c, p, l: kls)
    assert len(sigs) == 1
    sig = sigs[0]
    # 止损取保守：两套的 stop 均被 ma20*0.99=9.9 主导（高于价 8%线 9.2）→ 合并取 max=9.9
    assert sig.stop_price == 9.9
    # 目标取低：A 套 10% 档（11.0）< B 套 20% 档（12.0）
    assert sig.target_price == 10.0 * 1.10


def test_min_score_filter():
    s = stg.Strategy(name="低分", buy_rules=[stg.Rule("price", ">", 0)],
                     stop_loss_pct=4, take_profit_pct=8)
    kls = _mk_kls()
    # 高风险上下文压低分数
    rows = _rows(10.0)
    rows[0]["turnover_rate"] = 40.0     # 过热→风险分高
    sigs = run_strategy_scan([s], rows, lambda c, p, l: kls,
                             opp_cfg={"min_score": 99})
    assert sigs == []                    # 高门槛下全滤
    sigs2 = run_strategy_scan([s], _rows(10.0), lambda c, p, l: kls,
                              opp_cfg={"min_score": 0})
    assert len(sigs2) == 1               # 零门槛保留


def test_require_above_ma20_filter():
    """趋势过滤：价在 MA20 之下的股票不出现。"""
    s = stg.Strategy(name="任意", buy_rules=[stg.Rule("price", ">", 0)])
    kls = _mk_kls()
    # 价 5.0 远低于 MA20（约10+）
    sigs = run_strategy_scan([s], _rows(5.0), lambda c, p, l: kls,
                             opp_cfg={"require_above_ma20": True})
    assert sigs == []
    # 关闭过滤则出现
    sigs2 = run_strategy_scan([s], _rows(5.0), lambda c, p, l: kls,
                             opp_cfg={"require_above_ma20": False})
    assert len(sigs2) == 1


def test_weights_configurable():
    """权重配置改变分数（risk 权重加倍 → 分更低）。"""
    from stockpilot.core.opportunity import opportunity_score
    sig = TradeSignal(code="600519", price=10.0, stop_price=9.4,
                      target_price=11.2, risk_score=60)
    ind = {"ma_bull": True, "ma20": 9.5, "vol_vs_ma5": 1.5}
    q = Quote(code="600519", price=10.0, volume_ratio=1.5, amount=30000)
    s_def, _ = opportunity_score(sig, ind, q)
    s_hard, _ = opportunity_score(sig, ind, q,
                                   weights={"risk": 1.0, "trend": 0.12,
                                            "volume": 0.08, "rr": 0.10})
    assert s_hard < s_def, "risk 权重加倍应扣更多分"


def test_liquidity_penalty():
    """成交额低于门槛线性扣分。"""
    from stockpilot.core.opportunity import opportunity_score
    sig = TradeSignal(code="600519", price=10.0, stop_price=9.4,
                      target_price=11.2, risk_score=0)
    ind = {"ma_bull": True, "ma20": 9.5, "vol_vs_ma5": 1.5}
    rich = Quote(code="600519", price=10.0, volume_ratio=1.5, amount=50000)
    poor = Quote(code="600519", price=10.0, volume_ratio=1.5, amount=1000)
    s_rich, _ = opportunity_score(sig, ind, rich, min_amount_wan=5000)
    s_poor, _ = opportunity_score(sig, ind, poor, min_amount_wan=5000)
    assert s_poor < s_rich
    assert s_rich - s_poor >= 15  # 线性扣分（1000万远低于5000万门槛）


def test_storage_opportunity_defaults():
    from stockpilot.core.storage import Config
    cfg = Config()
    sec = cfg.opportunity
    assert sec["min_score"] == 0
    assert sec["require_above_ma20"] is True
    assert "weights" in sec
    # 名单：空=全部
    names = ["趋势启动", "海龟20日突破", "自定义X"]
    assert cfg.opportunity_strategies(names) == names
    cfg.opportunity["strategies"] = ["趋势启动"]
    assert cfg.opportunity_strategies(names) == ["趋势启动"]
    # 名单里的未知项被剔除；全空回退全部
    cfg.opportunity["strategies"] = ["不存在", "趋势启动"]
    assert cfg.opportunity_strategies(names) == ["趋势启动"]
    cfg.opportunity["strategies"] = ["不存在"]
    assert cfg.opportunity_strategies(names) == names  # 全无效回退
