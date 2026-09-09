"""指标引擎数值正确性（离线确定性）。"""
import math

from stockpilot.core import indicators
from stockpilot.core.selftest import synthetic_klines


def test_sma_basic():
    out = indicators.sma([1, 2, 3, 4, 5], 5)
    assert out[:4] == [None] * 4
    assert out[4] == 3.0


def test_sma_short_series():
    assert indicators.sma([1, 2], 5) == [None, None]


def test_ema_seed():
    out = indicators.ema([1.0, 2.0, 3.0], 3)
    assert out[0] == 1.0
    assert all(v is not None for v in out)


def test_rsi_extremes():
    up = [float(i) for i in range(1, 30)]
    assert indicators.rsi(up, 14)[-1] == 100.0
    down = [float(-i) for i in range(1, 30)]
    assert indicators.rsi(down, 14)[-1] == 0.0


def test_rsi_mixed_finite():
    closes = [10, 10.4, 10.1, 10.6, 10.2, 10.8, 10.5, 11.0, 10.7, 11.2,
              10.9, 11.4, 11.1, 11.6, 11.3, 11.8, 11.5, 12.0, 11.7, 12.2]
    r = indicators.rsi(closes, 14)
    assert r[-1] is not None and 0 < r[-1] < 100


def test_kdj_bounds_and_boll():
    kls = synthetic_klines(140)
    highs = [k.high for k in kls]
    lows = [k.low for k in kls]
    closes = [k.close for k in kls]
    k, d, j = indicators.kdj(highs, lows, closes)
    assert 0 <= k[-1] <= 100 and 0 <= d[-1] <= 100
    mid, up, low = indicators.boll(closes)
    assert up[-1] > mid[-1] > low[-1]


def test_macd_shape():
    closes = [100 - i * 0.5 for i in range(60)]  # 单边下跌
    dif, dea, hist = indicators.macd(closes)
    assert dif[-1] < dea[-1]  # 下跌趋势 DIF 在 DEA 下方
    assert hist[-1] < 0


def test_analyze_full_context():
    kls = synthetic_klines(140)
    ctx = indicators.analyze(kls)
    expected_numeric = ["ma5", "ma10", "ma20", "ma60", "dif", "dea",
                        "macd_hist", "rsi6", "rsi12", "rsi24", "k", "d", "j",
                        "boll_up", "boll_mid", "boll_low", "vol_vs_ma5",
                        "high20_prev", "low20_prev"]
    for key in expected_numeric:
        v = ctx.get(key)
        assert isinstance(v, float) and math.isfinite(v), f"{key}={v}"
    for key in ("ma_bull", "ma_bear", "macd_golden", "macd_dead",
                "kdj_golden", "ma20_rising"):
        assert isinstance(ctx.get(key), bool)
    # high20_prev 为前20日最高（不含当日）
    highs = [x.high for x in kls]
    assert ctx["high20_prev"] == max(highs[-21:-1])
