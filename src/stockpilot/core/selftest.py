"""离线自检：合成数据验证指标/策略/回测/通知，供 --smoke 与 pytest 复用。"""
from __future__ import annotations

import math
from typing import List, Tuple

from . import indicators, strategy as stg
from .backtest import BacktestConfig, run_backtest
from .models import KLine, Quote, TradeSignal
from .notify import _ding_sign, format_signal_message


def synthetic_klines(n: int = 140, start: float = 10.0, drift: float = 0.002,
                     seed: int = 7) -> List[KLine]:
    """确定性合成日K（正弦扰动 + 漂移），可复现。"""
    out: List[KLine] = []
    price = start
    for i in range(n):
        wave = math.sin(i / 7.0 + seed) * 0.012 + math.sin(i / 23.0) * 0.02
        change = drift + wave * 0.01
        o = price
        c = max(price * (1 + change), 0.5)
        h = max(o, c) * 1.015
        low = min(o, c) * 0.985
        vol = 50000 * (1 + 0.6 * math.sin(i / 5.0 + seed)) + 10000
        out.append(KLine(
            date=f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}",
            open=round(o, 2), close=round(c, 2), high=round(h, 2),
            low=round(low, 2), volume=vol, amount=vol * c,
            change_pct=round((c - o) / o * 100, 2),
            turnover=round(1.5 + wave * 20, 2)))
        price = c
    return out


def synthetic_osc_klines(n: int = 300, start: float = 10.0) -> List[KLine]:
    """振荡序列（围绕缓慢上行趋势正弦波动），保证产生 MACD 交叉与超卖，用于回测。"""
    out: List[KLine] = []
    prev_close = start
    for i in range(n):
        chg = 0.0015 + 0.018 * math.sin(i / 6.0)
        c = max(prev_close * (1 + chg), 0.5)
        o = prev_close
        h = max(o, c) * 1.004
        low = min(o, c) * 0.996
        out.append(KLine(
            date=f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}",
            open=round(o, 3), close=round(c, 3), high=round(h, 3),
            low=round(low, 3), volume=100000.0, amount=100000.0 * c,
            change_pct=round(chg * 100, 2), turnover=5.0))
        prev_close = c
    return out


def run_offline_selftest() -> List[Tuple[str, bool, str]]:
    """返回 [(用例名, 是否通过, 详情)]，全部离线确定性。"""
    results: List[Tuple[str, bool, str]] = []

    def check(name: str, fn):
        try:
            detail = fn()
            results.append((name, True, detail or "ok"))
        except Exception as exc:  # noqa: BLE001
            results.append((name, False, f"{exc.__class__.__name__}: {exc}"))

    # 1 指标数值边界
    def t_indicators():
        kls = synthetic_klines(140)
        ind = indicators.analyze(kls)
        for k in ("ma5", "ma20", "dif", "dea", "rsi6", "k", "d", "j",
                  "boll_up", "boll_low", "vol_vs_ma5"):
            v = ind.get(k)
            assert isinstance(v, float) and math.isfinite(v), f"{k}={v}"
        assert isinstance(ind.get("ma_bull"), bool)
        return (f"ma5={ind['ma5']:.2f} rsi6={ind['rsi6']:.1f} "
                f"j={ind['j']:.1f} ma_bull={ind['ma_bull']}")
    check("指标引擎(analyze)", t_indicators)

    # 2 RSI 已知序列：全涨序列 RSI=100
    def t_rsi():
        up = [float(i) for i in range(1, 25)]
        r = indicators.rsi(up, 14)
        assert r[-1] == 100.0, f"全涨RSI应为100, got {r[-1]}"
        down = [float(-i) for i in range(1, 25)]
        r2 = indicators.rsi(down, 14)
        assert r2[-1] == 0.0, f"全跌RSI应为0, got {r2[-1]}"
        return "全涨=100 全跌=0 校验通过"
    check("RSI极端序列", t_rsi)

    # 3 规则求值
    def t_rules():
        ctx = {"ma_bull": True, "macd_golden": True, "change_pct": 2.0,
               "price": 10.0, "ma20": 9.0, "volume_ratio": 1.8,
               "turnover_rate": 8.0, "high20_prev": 9.8, "low": 9.9,
               "ma20_rising": True, "rsi6": 55.0, "prev_low": 9.5}
        s = stg.builtin_strategies()
        trend = next(x for x in s if x.name == "趋势启动")
        hit, hit_rules = stg.eval_rules(trend.buy_rules, ctx)
        assert hit and len(hit_rules) == 3, f"趋势启动应命中3条: {hit_rules}"
        breakout = next(x for x in s if x.name == "放量突破")
        hit2, rules2 = stg.eval_rules(breakout.buy_rules, ctx)
        assert hit2, f"放量突破应命中: {rules2}"
        # 反例：涨幅 8% 超出 between[0,6]
        ctx_bad = dict(ctx, change_pct=8.0)
        hit3, _ = stg.eval_rules(trend.buy_rules, ctx_bad)
        assert not hit3, "涨幅8%不应命中趋势启动"
        return f"命中规则数 {len(hit_rules)}"
    check("策略规则求值", t_rules)

    # 4 信号生成与风控价
    def t_signal():
        ctx = {"ma_bull": True, "macd_golden": True, "ma20": 9.0,
               "rsi6": 55.0}
        quote = Quote(code="600519", name="测试股", price=10.0,
                      change_pct=2.0, amplitude=3.0, turnover_rate=8.0)
        s = stg.builtin_strategies()[0]
        sig = stg.check_buy(s, quote, ctx, "2026-09-02 10:00:00")
        assert sig is not None, "应产生买入信号"
        assert sig.stop_price == round(max(10 * 0.94, 9.0 * 0.99), 2), sig.stop_price
        assert sig.target_price == round(10 * 1.12, 2)
        assert 0 <= sig.risk_score <= 100
        msg = format_signal_message(sig)
        assert "参考买入成本" in msg and "10.0" in msg
        assert "不构成投资建议" in msg
        return f"止损{sig.stop_price} 目标{sig.target_price} 风险{sig.risk_score}"
    check("信号生成与风控价", t_signal)

    # 5 回测引擎（振荡序列，保证产生交易）
    def t_backtest():
        kls = synthetic_osc_klines(300)
        cfg = BacktestConfig(strategy_name="自检", days=250, cash=100000.0)
        total_trades = 0
        for s in stg.builtin_strategies():
            res = run_backtest(s, {"600519": ("测试股", kls)}, cfg)
            assert res.final_value > 0
            assert 0 <= res.win_rate <= 100
            assert res.max_drawdown_pct >= 0
            for t in res.trades:
                assert t.entry_price > 0 and t.exit_price > 0 and t.hold_days >= 1
                assert t.entry_date and t.exit_date
            total_trades += res.trade_count
            assert res.equity, f"{s.name} 权益曲线为空"
        assert total_trades >= 1, f"振荡序列应产生交易, 共{total_trades}笔"
        # 混合长度（含次新股短K线）不破坏组合权益曲线
        mixed = {"a": ("A", synthetic_osc_klines(300)),
                 "b": ("B", synthetic_osc_klines(60))}
        res2 = run_backtest(s, mixed, cfg)
        assert len(res2.equity) >= 100, f"混合长度权益曲线过短: {len(res2.equity)}"
        assert res2.max_drawdown_pct >= 0
        return f"4策略共成交{total_trades}笔，混合K线权益曲线{len(res2.equity)}点"
    check("回测引擎", t_backtest)

    # 6 钉钉加签确定性
    def t_sign():
        s1 = _ding_sign("SECRET123", 1700000000000)
        s2 = _ding_sign("SECRET123", 1700000000000)
        s3 = _ding_sign("SECRET123", 1700000000001)
        assert s1 == s2 and s1 != s3 and len(s1) > 10
        return "加签可复现且随时戳变化"
    check("钉钉加签", t_sign)

    # 7 卖出信号
    def t_sell():
        s = stg.builtin_strategies()[0]  # 卖出规则 price < ma20
        ctx = {"price": 8.0, "ma20": 9.0, "rsi6": 50.0}
        quote = Quote(code="000001", name="测试", price=8.0)
        sig = stg.check_sell(s, quote, ctx)
        assert sig is not None and sig.side == "sell"
        return "跌破MA20触发卖出规则"
    check("卖出信号", t_sell)

    return results
