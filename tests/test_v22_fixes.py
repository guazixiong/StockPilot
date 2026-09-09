"""v2.2 修复：sanitize（免责/零宽字符）、13 套策略、支撑位计算。"""
from stockpilot.core import strategy as stg
from stockpilot.core.models import KLine, Quote


def test_sanitize_removes_disclaimer_and_zerowidth():
    from stockpilot.ui.ai_stream import _sanitize, _DISCLAIMER
    # 模板免责 + 零宽字符 + <br> 字面量
    raw = ("结论：可以小仓试建。\u200b\n"
           f"{_DISCLAIMER}。<br>━━ 新的一轮 ━━")
    out = _sanitize(raw)
    assert _DISCLAIMER not in out
    assert "\u200b" not in out
    assert "小仓试建" in out
    # 措辞变体也去（AI 有时改写）
    v = "以上分析由 AI 生成，仅供参考，不构成投资建议。"
    assert "以上分析" not in _sanitize(v)
    assert _sanitize("正常文本") == "正常文本"


def test_builtin_13_strategies_all_evaluable():
    strs = stg.builtin_strategies()
    assert len(strs) == 21
    names = {s.name for s in strs}
    assert {"均线粘合发散", "强势新高", "缩量回踩60日线", "KDJ超卖金叉",
            "温和放量上行", "BOLL收口突破"} <= names
    # 每套至少 1 条买入规则、可序列化往返
    for s in strs:
        assert s.buy_rules
        assert stg.Strategy.from_dict(s.to_dict()).name == s.name


def test_support_levels_tags_by_price():
    """detail._support_levels：现价上方的均线标'压力'，下方标'支撑'。"""
    from stockpilot.ui.pages.detail import DetailDialog
    dlg = DetailDialog.__new__(DetailDialog)   # 只测纯逻辑，不跑 UI
    dlg.quote = Quote(code="600519", price=10.0)
    dlg.ind = {"ma5": 10.2, "ma10": 9.8, "ma20": 9.5, "ma60": 9.0,
               "boll_up": 10.8, "boll_mid": 9.5, "boll_low": 8.2}
    dlg.klines = {"day": [KLine(date=f"d{i}", open=9, close=9+i * 0.01,
                                high=9.5, low=8.8, volume=1) for i in range(70)]}
    lv = dlg._support_levels()
    assert lv.get("压力·MA5") == 10.2      # 现价上方 → 压力
    assert lv.get("支撑·MA10") == 9.8      # 现价下方 → 支撑
    assert lv.get("压力·布林上轨") == 10.8
    assert "压力·60日最高" in lv and "支撑·60日最低" in lv


def test_kline_chart_short_series_week():
    """周K/月K（<60根）set_data 不再 IndexError。"""
    from PySide6.QtWidgets import QApplication
    from stockpilot.ui.kline_chart import CandleChart
    app = QApplication.instance() or QApplication([])
    chart = CandleChart()
    chart.resize(900, 500)
    wk = [KLine(date=f"2025-w{i}", open=10+i*0.1, close=10.2+i*0.1,
                high=10.5+i*0.1, low=9.8+i*0.1, volume=1000)
          for i in range(58)]
    chart.set_data(wk, {"压力·MA20": 13.0, "支撑·MA10": 11.0})
    chart.grab()  # 触发 paintEvent（含 levels 绘制）
    chart.hover_idx = 5
    chart.grab()
    assert chart.view_len == 58
