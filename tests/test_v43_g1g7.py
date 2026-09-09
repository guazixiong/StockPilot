"""v4.3 增补：主力资金因子（G1 切片）+ K 线窗口 800 根（G7）防回归。"""
from stockpilot.core.models import Quote
from stockpilot.core import opportunity as opp
from stockpilot.core import strategy as stg


class _Sig:
    price = 10.0
    stop_price = 9.0
    target_price = 12.0
    risk_score = 10.0


def _quote(inflow, amount=50000.0):
    return Quote.from_mapping("600519", {
        "price": 10.0, "main_inflow": inflow, "amount": amount})


def test_quote_carries_main_inflow():
    """东财 f62 已解析但曾因 Quote 无字段被丢弃（v4.3 修的真实断点）。"""
    q = _quote(1234.5)
    assert q.main_inflow == 1234.5
    q0 = Quote.from_mapping("600519", {})
    assert q0.main_inflow is None


def test_money_factor_direction():
    """主力净流入加分 / 净流出扣分（其余条件相同的两条报价）。"""
    ind = {"ma_bull": True, "ma20": 9.0, "vol_vs_ma5": 1.5}
    s_in, _ = opp.opportunity_score(_Sig(), ind, _quote(12000.0))
    s_out, _ = opp.opportunity_score(_Sig(), ind, _quote(-12000.0))
    s_none, _ = opp.opportunity_score(_Sig(), ind, _quote(None))
    assert s_in > s_out
    assert s_none == opp.opportunity_score(
        _Sig(), ind, _quote(None))[0]
    # 无资金数据不惩罚（源缺失时公平）
    assert s_none >= s_out


def test_money_factor_weight_configurable():
    """money 权重可置 0（关因子不改变其它因子结果）。"""
    ind = {"ma_bull": True, "ma20": 9.0, "vol_vs_ma5": 1.5}
    q = _quote(12000.0)
    on, _ = opp.opportunity_score(_Sig(), ind, q,
                                  weights={"money": 0.06})
    off, _ = opp.opportunity_score(_Sig(), ind, q,
                                   weights={"money": 0.0})
    assert on > off
    # 关掉后应与"无数据"口径一致
    none_, _ = opp.opportunity_score(_Sig(), ind, _quote(None),
                                      weights={"money": 0.0})
    assert abs(off - none_) < 1e-9


def test_indicator_registry_has_main_inflow():
    """中文指标表包含主力净流入（89 项，含新增主力资金）。"""
    assert stg.INDICATOR_NAMES.get("main_inflow") == "主力净流入(万)"
    # build_ctx 必须透传
    q = _quote(888.0)
    ctx = stg.build_ctx({"ma20": 9.0}, q)
    assert ctx["main_inflow"] == 888.0


def test_kline_window_800_constant():
    """G7：扫描/缓存窗口统一 800 根（源码级断言防回退到 320）。"""
    import inspect
    from stockpilot.core import kline_store as ks
    from stockpilot.ui import context as ui_ctx
    src_ks = inspect.getsource(ks)
    assert 'limit: int = 800' in src_ks
    src_ui = inspect.getsource(ui_ctx)
    # v4.4.3 签名兼容改造后：默认值改为 None+内部归一 800
    assert '800' in src_ui and 'kline_for_scan' in src_ui
    # 钳制：回测预热不超 800
    from stockpilot.ui.pages import backtest as bt
    src_bt = inspect.getsource(bt)
    assert "min(days + 80, 800)" in src_bt or "min(cfg.days + 80, 800)" in src_bt


def test_kline_store_fetched_count_meta():
    """源容量元数据：635 根天然不足 800，但已实得 635 → 视为足量不回源。"""
    import datetime
    import tempfile
    from pathlib import Path
    from stockpilot.core.kline_store import KLineStore
    from stockpilot.core.models import KLine
    store = KLineStore(Path(tempfile.mkdtemp()) / "t.db")   # 隔离，不碰真实库
    d = datetime.date(2024, 1, 1)
    kls = [KLine(date=str(d + datetime.timedelta(days=i)), open=1, close=1,
                 high=1, low=1) for i in range(635)]
    store.put("T_META", kls)
    assert store.fetched_count("T_META") is None
    store.record_fetched("T_META", 635)
    assert store.fetched_count("T_META") == 635
    assert len(store.get("T_META", 800)) == 635
