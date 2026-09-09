"""机会评分 / 新增内置策略 / 策略执行链（run_strategy_scan）。"""
from stockpilot.core import strategy as stg
from stockpilot.core.models import KLine, Quote, TradeSignal
from stockpilot.core.monitor import run_strategy_scan
from stockpilot.core.opportunity import opportunity_score
from stockpilot.core.selftest import synthetic_osc_klines


def _sig(price=10.0, stop=9.4, target=11.2, risk=15):
    return TradeSignal(code="600519", name="测试", strategy="测试", side="buy",
                       price=price, stop_price=stop, target_price=target,
                       risk_score=risk)


def test_builtin_strategy_count():
    names = [s.name for s in stg.builtin_strategies()]
    assert len(names) == 21
    for extra in ("强势回调", "布林下轨反弹", "平台突破", "均线粘合发散",
                  "强势新高", "缩量回踩60日线", "KDJ超卖金叉",
                  "温和放量上行", "BOLL收口突破", "低位密集突破", "获利盘洗盘企稳"):
        assert extra in names


def test_opportunity_score_grading():
    ind = {"ma_bull": True, "ma20": 9.5, "vol_vs_ma5": 1.5, "rsi6": 50}
    q = Quote(code="600519", name="t", price=10.0, volume_ratio=1.5)
    score, grade = opportunity_score(_sig(risk=10), ind, q)
    assert score >= 75 and grade == "A"

    score2, grade2 = opportunity_score(_sig(risk=90), {}, q)
    assert score2 < 60 and grade2 in ("B", "C")
    assert score2 < score


def test_new_strategy_rules():
    # 强势回调：price>ma60, low<=ma10, price>ma10
    s = next(x for x in stg.builtin_strategies() if x.name == "强势回调")
    ctx = {"price": 10.05, "ma60": 9.0, "low": 9.95, "ma10": 10.0,
           "change_pct": 1.0}
    hit, rules = stg.eval_rules(s.buy_rules, ctx)
    assert hit and len(rules) == 4
    # 布林下轨反弹
    s2 = next(x for x in stg.builtin_strategies() if x.name == "布林下轨反弹")
    ctx2 = {"low": 9.0, "boll_low": 9.2, "price": 9.5, "vol_vs_ma5": 1.3}
    assert stg.eval_rules(s2.buy_rules, ctx2)[0]
    # 平台突破
    s3 = next(x for x in stg.builtin_strategies() if x.name == "平台突破")
    ctx3 = {"price": 10.5, "high30_prev": 10.4, "turnover_rate": 8.0,
            "volume_ratio": 1.5}
    assert stg.eval_rules(s3.buy_rules, ctx3)[0]


def test_run_strategy_scan_fills_score_and_sparkline():
    # 构造一根突破K线确保至少命中"放量突破"
    kls = synthetic_osc_klines(120)
    highs = [k.high for k in kls]
    last_close = max(highs) * 1.02
    kls.append(KLine(date="2026-09-03", open=kls[-1].close, close=last_close,
                     high=last_close * 1.005, low=kls[-1].close * 0.995,
                     volume=200000, amount=2e9, change_pct=2.5, turnover=8.0))
    rows = [{"code": "600519", "name": "测试股", "price": last_close,
             "change_pct": 2.5, "turnover_rate": 8.0, "volume_ratio": 1.8}]

    signals = run_strategy_scan(stg.builtin_strategies(), rows,
                                lambda code, period, limit: kls)
    assert signals, "突破样例应产生信号"
    # 按机会分降序
    scores = [s.op_score for s in signals]
    assert scores == sorted(scores, reverse=True)
    for s in signals:
        assert len(s.sparkline) == 30
        assert s.grade in ("A", "B", "C")
        assert 0 <= s.op_score <= 100
