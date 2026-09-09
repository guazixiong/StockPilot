"""v5.2.3：AI 报告两问题修复的防回归。

问题1：报告完成但视图只剩免责声明（正文全丢）——三重防御：
  a) _on_ai_done 保底：视图缺 reply 可识别片段时全文重渲染；
  b) finalize_stream 保底：清理后正文消失则回滚原文档。
问题2：模型调用异常后点「重新分析」→ 之前对话记录全部消失——
  run_diagnosis 改为保留历史（"新的一轮"分隔），仅首次清空。
"""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QTextBrowser  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ---------------------------------------------------------------- 问题1
def test_on_ai_done_renders_missing_reply(qapp, monkeypatch, tmp_path):
    """保底 a：流式 delta 全丢（视图无正文），_on_ai_done 用 reply 重渲染。"""
    import stockpilot.core.session_store as ss
    monkeypatch.setattr(ss, "data_dir", lambda: tmp_path)
    import stockpilot.ui.pages.detail as dmod
    monkeypatch.setattr(dmod, "submit", lambda *a, **k: None)
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    from stockpilot.ui.pages.detail import DetailDialog
    from stockpilot.ui.ai_stream import append_stream
    from stockpilot.core.models import KLine, Quote
    ctx = AppContext(Config())
    ctx.monitor.market_fetch = lambda max_count=2000: []
    d = DetailDialog(ctx, "600519", "t")
    d.klines["day"] = [KLine(date=f"2026-08-{i:02d}", open=10.0, close=10.0 + i,
                             high=11.0, low=9.0, volume=100.0, amount=1000.0,
                             change_pct=1.0, turnover=2.0)
                       for i in range(1, 31)]
    d.quote = Quote(code="600519", price=10.0)
    from stockpilot.core import indicators
    d.ind = indicators.analyze(d.klines["day"])
    d.news = []
    d.lhb_hit = None
    # 模拟"delta 全丢"：视图只有气泡/头/免责，没有正文
    d.ai_view.clear()
    append_stream(d.ai_view, "user", "请对该股做全面诊断")
    append_stream(d.ai_view, "ai_head", "")
    reply = "诊断结论：谨慎看多。基本面护城河稳固，MACD 金叉。"
    d._on_ai_done(reply)
    plain = d.ai_view.toPlainText()
    assert "谨慎看多" in plain and "护城河" in plain, "保底未渲染 reply 正文"
    assert plain.count("以上分析") >= 1


def test_finalize_keeps_body_when_regex_overreach(qapp):
    """保底 b：finalize 正则误吞整个文档时回滚（宁留模型免责句不丢报告）。"""
    from stockpilot.ui.ai_stream import append_stream, finalize_stream
    v = QTextBrowser()
    append_stream(v, "content", "正文内容甲。" * 40)   # 长正文
    append_stream(v, "content", "以上分析由AI生成，仅供参考，不构成投资建议")
    append_stream(v, "disclaimer", "")
    before = v.toPlainText()
    assert "正文内容甲" in before
    finalize_stream(v)
    after = v.toPlainText()
    # 清理正常时正文必须还在；被吞一半以上则回滚保底
    assert "正文内容甲" in after, "finalize 丢了正文（保底未生效）"


def test_finalize_still_dedup_disclaimer(qapp):
    """保底不破坏原职责：模型免责句仍被清，UI 只留一条。"""
    from stockpilot.ui.ai_stream import append_stream, finalize_stream
    v = QTextBrowser()
    append_stream(v, "content", "结论：观望。")
    # 免责句拆两个 chunk（追加阶段正则匹配不到——finalize 才是清理时机）
    append_stream(v, "content", "以上分析")
    append_stream(v, "content", "由AI生成，仅供参考，不构成投资建议")
    append_stream(v, "disclaimer", "")
    assert v.toPlainText().count("以上分析") == 2
    finalize_stream(v)
    assert v.toPlainText().count("以上分析") == 1
    assert "结论：观望" in v.toPlainText()


# ---------------------------------------------------------------- 问题2
def test_rerun_keeps_history(qapp, monkeypatch, tmp_path):
    """重新分析保留旧对话：历史不重置、视图不清空（仅开新轮分隔）。"""
    import stockpilot.core.session_store as ss
    monkeypatch.setattr(ss, "data_dir", lambda: tmp_path)
    import stockpilot.ui.pages.detail as dmod
    monkeypatch.setattr(dmod, "submit", lambda *a, **k: None)
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    from stockpilot.ui.pages.detail import DetailDialog
    from stockpilot.ui.ai_stream import append_stream
    ctx = AppContext(Config())
    ctx.monitor.market_fetch = lambda max_count=2000: []
    d = DetailDialog(ctx, "600519", "t")
    # 模拟已有的对话历史（用户问过、AI 答过）
    d._ai_history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "之前的问题"},
        {"role": "assistant", "content": "之前的回答"},
    ]
    append_stream(d.ai_view, "user", "之前的问题")
    append_stream(d.ai_view, "content", "之前的回答")
    hist_len = len(d._ai_history)
    view_text = d.ai_view.toPlainText()
    # klines 就绪后点「重新分析」
    from stockpilot.core.models import KLine, Quote
    d.klines["day"] = [KLine(date=f"2026-08-{i:02d}", open=10.0, close=10.0 + i,
                             high=11.0, low=9.0, volume=100.0, amount=1000.0,
                             change_pct=1.0, turnover=2.0)
                       for i in range(1, 31)]
    d.quote = Quote(code="600519", price=10.0)
    from stockpilot.core import indicators
    d.ind = indicators.analyze(d.klines["day"])
    d.news = []
    d.lhb_hit = None
    class _FakeClient:
        def chat(self, msgs, stream=True, **kw):
            return "新的回答"
    d.ctx.ai_client = lambda: _FakeClient()
    d._ai_ready = lambda: True      # 测试环境无 AI 配置，跳过就绪检查
    d.run_diagnosis()
    # 历史：旧 3 条全保留（不再 list(messages) 重置）
    assert len(d._ai_history) >= hist_len, "重新分析丢了历史（问题2）"
    assert d._ai_history[1]["content"] == "之前的问题"
    # 视图：旧内容仍在（不清空）
    assert "之前的回答" in d.ai_view.toPlainText(), "重新分析清空了视图（问题2）"


def test_first_diagnosis_still_clean(qapp, monkeypatch, tmp_path):
    """首次诊断仍走干净开报告（保留行为不破坏）。"""
    import stockpilot.core.session_store as ss
    monkeypatch.setattr(ss, "data_dir", lambda: tmp_path)
    import stockpilot.ui.pages.detail as dmod
    monkeypatch.setattr(dmod, "submit", lambda *a, **k: None)
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    from stockpilot.ui.pages.detail import DetailDialog
    from stockpilot.ui.ai_stream import append_stream
    ctx = AppContext(Config())
    ctx.monitor.market_fetch = lambda max_count=2000: []
    d = DetailDialog(ctx, "600519", "t")
    append_stream(d.ai_view, "content", "残留旧内容")
    d._ai_history = []
    from stockpilot.core.models import KLine, Quote
    d.klines["day"] = [KLine(date=f"2026-08-{i:02d}", open=10.0, close=10.0 + i,
                             high=11.0, low=9.0, volume=100.0, amount=1000.0,
                             change_pct=1.0, turnover=2.0)
                       for i in range(1, 31)]
    d.quote = Quote(code="600519", price=10.0)
    from stockpilot.core import indicators
    d.ind = indicators.analyze(d.klines["day"])
    d.news = []
    d.lhb_hit = None
    class _FakeClient:
        def chat(self, msgs, stream=True, **kw):
            return "答案"
    d.ctx.ai_client = lambda: _FakeClient()
    d._ai_ready = lambda: True
    d.run_diagnosis()
    assert "残留旧内容" not in d.ai_view.toPlainText(), "首次诊断应清空视图"
