"""v1.8 新指标 + 规则中文化。"""
import math

from stockpilot.core import strategy as stg
from stockpilot.core.indicators import (analyze, bias, cci, obv_rising, pct_change,
                                        psy, roc, streak, wr, dmi)
from stockpilot.core.models import Quote
from stockpilot.core.selftest import synthetic_klines, synthetic_osc_klines


def test_bias_wr_cci_roc_psy_basic():
    closes = [float(i) for i in range(1, 41)]  # 单边上涨
    b = bias(closes, 6)
    assert b[-1] is not None and b[-1] > 0
    wrv = wr(closes, closes, closes, 14)
    assert wrv[-1] is not None and 0 <= wrv[-1] <= 100
    cc = cci(closes, closes, closes, 14)
    assert cc[-1] is not None
    rc = roc(closes, 12)
    assert rc[-1] == (40 - 28) / 28 * 100
    ps = psy(closes, 12)
    assert ps[-1] == 100.0  # 连涨
    assert obv_rising(closes, [100.0] * 40)


def test_pct_change_and_streak():
    closes = [10, 11, 12, 11.5, 12.5]
    pc = pct_change(closes, 3)
    # 与 3 根前（closes[-4]=11）比较
    assert abs(pc[-1] - (12.5 / 11 - 1) * 100) < 1e-9
    up, down = streak([10, 11, 12, 13])
    assert up == 3 and down == 0
    up2, down2 = streak([10, 9, 8])
    assert up2 == 0 and down2 == 2


def test_dmi_uptrend():
    # 稳步上涨：+DI 应大于 -DI，ADX 为正
    highs = [10 + i * 0.5 + 0.2 for i in range(40)]
    lows = [10 + i * 0.5 - 0.2 for i in range(40)]
    closes = [10 + i * 0.5 for i in range(40)]
    pdi, mdi, adx = dmi(highs, lows, closes, 14)
    assert pdi[-1] is not None and pdi[-1] > mdi[-1]
    assert (adx[-1] or 0) > 0


def test_analyze_new_keys():
    kls = synthetic_klines(320)
    ctx = analyze(kls)
    new_keys = ["ma30", "ma120", "ma250", "ema12", "ema26", "bias6", "bias12",
                "bias24", "wr14", "cci14", "roc12", "psy12", "obv_rising",
                "plus_di", "minus_di", "adx", "dmi_golden", "chg5d", "chg10d",
                "chg20d", "chg60d", "up_days", "down_days", "dd_from_high20"]
    for k in new_keys:
        assert k in ctx, f"analyze 缺少 {k}"
    for k in ("bias6", "wr14", "cci14", "roc12", "chg20d"):
        v = ctx[k]
        assert v is None or (isinstance(v, float) and math.isfinite(v)), k
    assert isinstance(ctx["obv_rising"], bool)
    assert isinstance(ctx["up_days"], int) and ctx["up_days"] >= 0


def test_rule_text_chinese():
    assert stg._rule_text(stg.Rule("price", "<", "ma20")) == \
        "现价 低于 20日均线MA20"
    assert stg._rule_text(stg.Rule("ma_bull", "==", True)) == "均线多头排列"
    assert stg._rule_text(stg.Rule("macd_golden", "==", True)) == "MACD金叉"
    assert stg._rule_text(stg.Rule("macd_golden", "==", False)) == "非MACD金叉"
    assert stg._rule_text(stg.Rule("rsi6", "between", [30, 70])) == \
        "RSI6 介于 30~70"
    assert stg._rule_text(stg.Rule("pe", "<=", 30)) == "市盈率PE 不高于 30"


def test_eval_rule_chinese_value():
    ctx = {"price": 10.0, "ma20": 9.0, "ma_bull": True}
    # 右值填中文指标名也能比较
    assert stg.eval_rule(stg.Rule("price", ">", "20日均线MA20"), ctx)
    assert stg.eval_rule(stg.Rule("ma_bull", "==", "true"), ctx)
    assert stg.eval_rule(stg.Rule("ma_bull", "==", "成立"), ctx)
    assert not stg.eval_rule(stg.Rule("ma_bull", "==", "false"), ctx)


def test_zh_en_bidirectional():
    assert stg.zh_name("ma_bull") == "均线多头排列"
    assert stg.en_key("均线多头排列") == "ma_bull"
    assert stg.en_key("price") == "price"
    assert stg.en_key("不存在") is None
    # 全部映射表无重复中文名（反查无歧义）
    names = list(stg.INDICATOR_NAMES.values())
    assert len(names) == len(set(names))


def test_builtin_signal_chinese_reason():
    ctx = {"ma_bull": True, "macd_golden": True, "change_pct": 2.0,
            "price": 10.0, "ma20": 9.0, "rsi6": 55.0, "amplitude": 3.0,
            "turnover_rate": 8.0, "obv_rising": True}
    quote = Quote(code="600519", name="测试股", price=10.0, change_pct=2.0,
                  amplitude=3.0, turnover_rate=8.0)
    s = stg.builtin_strategies()[0]
    sig = stg.check_buy(s, quote, ctx, "2026-09-03 10:00:00")
    assert sig is not None
    assert "均线多头排列" in sig.reason
    assert "MACD金叉" in sig.reason
    # 全中文：不再含英文键
    assert "ma_bull" not in sig.reason and "macd_golden" not in sig.reason
    for h in sig.hit_rules:
        assert all(k not in h for k in
                   ("ma_bull", "macd_golden", "price", "rsi6")), h


def test_new_indicators_usable_in_strategy():
    """新指标可组合成策略并通过执行链。"""
    s = stg.Strategy(
        name="测试新指标",
        buy_rules=[stg.Rule("obv_rising", "==", True),
                   stg.Rule("bias6", "<", 5),
                   stg.Rule("chg20d", ">", 0)])
    kls = synthetic_osc_klines(300)
    from stockpilot.core import indicators
    ind = indicators.analyze(kls)
    quote = Quote(code="600519", name="t", price=kls[-1].close)
    ctx = stg.build_ctx(ind, quote)
    hit, rules = stg.eval_rules(s.buy_rules, ctx)
    # 无论涨跌路径，规则文本必须是中文
    for r in rules:
        assert any(zh in r for zh in ("OBV", "乖离率", "20日涨幅")), r
