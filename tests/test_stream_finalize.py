"""v2.2 AI 对话流清理：分块流式 + finalize_stream 端到端。"""
from PySide6.QtWidgets import QApplication

from stockpilot.ui.ai_stream import append_stream, finalize_stream


def test_chunked_stream_disclaimer_dedup():
    """免责句被 SSE 切块 → 逐块清洗失效 → finalize_stream 全文兜底只留 UI 渲染 1 次。"""
    app = QApplication.instance() or QApplication([])
    from PySide6.QtWidgets import QTextBrowser
    view = QTextBrowser()
    append_stream(view, "user", "请对该股做全面诊断")
    append_stream(view, "system",
                  "<div style='color:#9aa3b2;margin:2px 0 4px 0'>🤖 AI：</div>",
                  raw=True)
    append_stream(view, "content", "结论：技术面偏强。")
    append_stream(view, "content", "以上分析由AI生")
    append_stream(view, "content", "成，仅供参考，不构成投资建议。<br>")
    append_stream(view, "disclaimer", "")
    append_stream(view, "user", "适合建仓吗？")
    append_stream(view, "content", "可以小仓试建。")
    append_stream(view, "content", "以上分析")
    append_stream(view, "content", "由 AI 生成，仅供参考，不构成投资建议。")
    append_stream(view, "disclaimer", "")
    finalize_stream(view)
    plain = view.toPlainText()
    assert plain.count("以上分析") == 1      # 仅 UI 渲染那一次
    assert "<br>" not in plain               # 无字面标签
    assert "可以小仓试建" in plain and "适合建仓吗" in plain  # 两轮保留
    assert "技术面偏强" in plain


def test_finalize_keeps_content_when_no_disclaimer():
    app = QApplication.instance() or QApplication([])
    from PySide6.QtWidgets import QTextBrowser
    view = QTextBrowser()
    append_stream(view, "content", "正常回答内容，无免责。")
    finalize_stream(view)
    assert "正常回答内容" in view.toPlainText()
