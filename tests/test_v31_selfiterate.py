"""v3.1 自迭代修复：连板阈值分流 / 封单补拉 / 回测缓存。"""
from stockpilot.core.models import KLine, Quote


def _kl(closes):
    out, prev = [], None
    for c in closes:
        out.append(KLine(date="d", open=(prev or c) * 0.99, close=c,
                         high=c * 1.01, low=(prev or c) * 0.99,
                         volume=100))
        prev = c
    return out


def test_streak_main_board():
    from stockpilot.ui.pages.limitup import _streak_limit_up
    # 主板 3 连板：+9.9% 三天
    c = 10.0
    closes = [c]
    for _ in range(3):
        c *= 1.099
        closes.append(c)
    assert _streak_limit_up(_kl(closes), "600000", "") == 3


def test_streak_chinext_20pct():
    from stockpilot.ui.pages.limitup import _streak_limit_up
    c = 10.0
    closes = [c]
    for _ in range(2):
        c *= 1.199            # 创科板真实涨停约 +19.9%
        closes.append(c)
    assert _streak_limit_up(_kl(closes), "300001", "") == 2
    # +18% 未触 20% 涨停不算连板
    c2 = 10.0
    closes2 = [c2]
    for _ in range(2):
        c2 *= 1.18
        closes2.append(c2)
    assert _streak_limit_up(_kl(closes2), "300001", "") == 0


def test_streak_st_5pct():
    from stockpilot.ui.pages.limitup import _streak_limit_up
    c = 10.0
    closes = [c]
    for _ in range(2):
        c *= 1.049
        closes.append(c)
    # ST：+4.9% 连板 ×2
    assert _streak_limit_up(_kl(closes), "600000", "ST某某") == 2


def test_streak_breaks_on_non_limit():
    from stockpilot.ui.pages.limitup import _streak_limit_up
    c = 10.0
    closes = [c, c * 1.099, c * 1.099 * 1.02, c * 1.099 * 1.02 * 1.099]
    # 第2根非涨停 → 只算最末1板
    assert _streak_limit_up(_kl(closes), "600000", "") == 1


def test_limit_th_routing():
    from stockpilot.ui.pages.limitup import _limit_th
    assert _limit_th("600000", "") < 0.10
    assert _limit_th("300001", "") > 0.19
    assert _limit_th("688001", "") > 0.19


def test_backtest_uses_cache():
    import inspect
    from stockpilot.ui.pages import backtest as bt
    src = inspect.getsource(bt.BacktestPage._do_backtest)
    assert "kline_for_scan" in src
