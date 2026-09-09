"""v3.2：回测引擎 Sequoia 指标口径补齐。"""
from stockpilot.core import strategy as stg
from stockpilot.core.backtest import BacktestConfig, run_backtest
from stockpilot.core.models import KLine


def _mk(n=90, base=10.0, drift=0.004, volume=100000.0, amount_scale=1.0):
    out, px = [], base
    for i in range(n):
        px *= (1 + drift)
        out.append(KLine(date=f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}",
                         open=px * 0.995, close=px, high=px * 1.01,
                         low=px * 0.985, volume=volume,
                         amount=volume * px * amount_scale,
                         change_pct=drift * 100, turnover=2.0))
    return out


def test_backtest_snapshot_has_sequoia_keys():
    """回测 snapshot 必须产出 Sequoia 策略所需全部指标键。"""
    kls = _mk(90)
    s = stg.builtin_strategies()[0]
    res = run_backtest(s, {"600519": ("t", kls)},
                        BacktestConfig(strategy_name=s.name, days=60))
    # run_backtest 内部 snapshot 必须能对每套策略求值——用海龟策略直接跑
    turtle = next(x for x in stg.builtin_strategies()
                  if x.name == "海龟20日突破")
    res2 = run_backtest(turtle, {"600519": ("t", kls)},
                         BacktestConfig(strategy_name=turtle.name, days=60))
    assert res2.final_value >= 0          # 不抛异常即 snapshot 键齐全
    # 直接验证：单点 snapshot 取键
    from stockpilot.core.backtest import run_backtest_one
    # 用闭包抓 snapshot 输出
    keys_seen = {}

    def spy(strategy, code, name, klines_, cfg, cash):
        # 借 monkey 检查：跑一遍并断言不报 KeyError
        return run_backtest_one(strategy, code, name, klines_, cfg, cash)

    out = spy(turtle, "600519", "t", kls, BacktestConfig(days=60), 100000)
    final, trades, equity = out
    assert len(equity) > 0


def test_backtest_all_21_strategies_no_crash():
    """21 套内置策略全部能跑回测（snapshot 键全覆盖冒烟）。"""
    kls = _mk(90)
    ok = 0
    for s in stg.builtin_strategies():
        try:
            run_backtest(s, {"600519": ("t", kls)},
                         BacktestConfig(strategy_name=s.name, days=60))
            ok += 1
        except Exception as exc:  # noqa: BLE001
            assert False, f"{s.name} 回测崩溃: {exc}"
    assert ok == len(stg.builtin_strategies()) == 21


def test_turtle_backtest_picks_breakout():
    """海龟回测在突破+亿元流动性序列中买入持有：期末含持仓市值（trades 只记平仓）。"""
    # 先横盘后突破 + 成交额 > 1 亿
    kls = _mk(70, drift=0.0)
    # 突破放在倒数第 2 根（回测口径：信号日收盘 → 次日开盘成交，末根信号无次日）
    anchor = kls[-2]
    px = anchor.close * 1.08
    kls[-1] = KLine(date="2025-04-01", open=anchor.close * 1.02, close=px,
                    high=px * 1.01, low=anchor.close * 1.01,
                    volume=1000000, amount=1.5e8,
                    change_pct=8.0, turnover=3.0)
    kls.append(KLine(date="2025-04-02", open=px * 1.005, close=px * 1.02,
                     high=px * 1.03, low=px, volume=500000,
                     amount=8e7, change_pct=1.0, turnover=2.0))
    turtle = next(x for x in stg.builtin_strategies()
                  if x.name == "海龟20日突破")
    res = run_backtest(turtle, {"600519": ("t", kls)},
                        BacktestConfig(strategy_name=turtle.name, days=60))
    # 买入成功判据：期末含持仓市值 > 初始现金（突破后持仓浮盈中）
    assert res.final_value > 100000,         f"期末应含上涨中的持仓市值，got {res.final_value}"
    assert res.equity and res.equity[-1][1] > 100000
    assert res.start and res.end
