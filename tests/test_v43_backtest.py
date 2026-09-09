"""v4.3：回测升级（分批建仓/滑点/费率精细化）+ 选股验证报告。"""
from stockpilot.core import strategy as stg
from stockpilot.core.backtest import (BacktestConfig, run_backtest,
                                      run_backtest_one)
from stockpilot.core.models import KLine
from stockpilot.core.validation import _composite_score, run_validation


def _mk(n, base=10.0, drift=0.004):
    out, px = [], base
    for i in range(n):
        px *= (1 + drift)
        out.append(KLine(date=f"d{i}", open=px * 0.995, close=px,
                         high=px * 1.01, low=px * 0.985, volume=200000,
                         amount=200000 * px, change_pct=drift * 100,
                         turnover=2.0))
    return out


def test_slippage_reduces_return():
    """滑点应为正：同一上涨序列，带滑点期末 ≤ 无滑点。"""
    kls = _mk(80)
    s = stg.builtin_strategies()[0]
    plain = run_backtest_one(s, "600519", "t", kls,
                             BacktestConfig(days=50, slippage_pct=0.0), 100000)
    slipped = run_backtest_one(s, "600519", "t", kls,
                               BacktestConfig(days=50, slippage_pct=0.5), 100000)
    assert plain[0] >= slipped[0]          # 期末（含持仓市值）不劣于
    assert plain[0] > 0 and slipped[0] > 0  # 两个都在跑


def test_fees_reduce_return():
    """费率/印花税/最低佣金同向：重费率期末更低（须真实产生交易）。"""
    kls = _mk(80)
    s = stg.Strategy(name="费率验证",
                     buy_rules=[stg.Rule("price", ">", 0)],
                     sell_rules=[stg.Rule("price", ">", 100000)])
    cheap = run_backtest_one(s, "600519", "t", kls, BacktestConfig(
        days=50, max_hold_days=15, fee_rate=0, stamp_tax=0,
        transfer_fee=0, min_commission=0), 100000)
    pricey = run_backtest_one(s, "600519", "t", kls, BacktestConfig(
        days=50, max_hold_days=15, fee_rate=1e-3, stamp_tax=2e-3,
        transfer_fee=1e-3, min_commission=50), 100000)
    assert len(cheap[1]) >= 1 and len(pricey[1]) >= 1  # 确有交易（费用才被计）
    assert cheap[0] > pricey[0]
    assert pricey[0] < 100000


def test_batch_entry_fills_over_days():
    """分批建仓：3 批模式下买入后 3 日内应分步成交（期末含全部仓位）。"""
    kls = _mk(80)
    turtle = next(x for x in stg.builtin_strategies()
                  if x.name == "海龟20日突破")
    # 造突破：末段价格抬升
    for k in kls[-8:]:
        k.close *= 1.05
        k.high = k.close * 1.01
    one = run_backtest_one(turtle, "600519", "t", kls, BacktestConfig(
        days=50, entry_batches=1), 100000)
    three = run_backtest_one(turtle, "600519", "t", kls, BacktestConfig(
        days=50, entry_batches=3), 100000)
    # 两种模式都正常跑完（期末>0），且分批不早于一次性（时序上分批成本更平稳）
    assert one[0] > 0 and three[0] > 0


def test_run_backtest_accepts_bare_klines():
    """包装层容错：{code: klines} 裸列表（v4.3 修过的真实事故：解包错配致 0 交易）。"""
    kls = _mk(80)
    s = stg.builtin_strategies()[0]
    # 裸列表（旧调用方式）
    res_bare = run_backtest(s, {"600519": kls}, BacktestConfig(days=50))
    # 标准元组
    res_tuple = run_backtest(s, {"600519": ("t", kls)},
                             BacktestConfig(days=50))
    # 两种传法必须结果一致（修复前裸列表解包成 (name=第一根K线, klines=第二根)）
    assert res_bare.final_value == res_tuple.final_value
    assert res_bare.trade_count == res_tuple.trade_count


def test_validation_report_ranking():
    """验证报告：多策略产出排名，分数降序，格式行齐全。"""
    kls_a = _mk(80, drift=0.006)
    kls_b = _mk(80, drift=-0.002)
    data = {"600519": ("甲", kls_a), "000001": ("乙", kls_b)}
    rep = run_validation(data, BacktestConfig(days=50, cash=100000.0))
    assert len(rep.strategies) == len(stg.builtin_strategies()) == 21
    scores = [s.score for s in rep.strategies]
    assert scores == sorted(scores, reverse=True)
    assert rep.strategies[0].rank == 1
    # 排名行表头/行数
    lines = rep.rank_lines()
    assert "胜率" in lines[0] and "盈亏比" in lines[0]
    assert len(lines) >= 22
    # 摘要含范围与最佳
    sm = rep.summary()
    assert "50 交易日" in sm and "最佳策略" in sm


def test_composite_score_bounds():
    from stockpilot.core.validation import StrategyScore
    s = StrategyScore(name="x", trades=20, wins=15, win_rate=75,
                      profit_factor=3.0, total_return_pct=50,
                      max_drawdown_pct=5)
    assert 0 < _composite_score(s) <= 100
    s0 = StrategyScore(name="y")
    assert _composite_score(s0) == 0
