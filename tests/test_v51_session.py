"""v5.1：临时态（会话暂存）单测。

用户痛点：误关个股窗 → AI 分析（诊断/辩论/投委会）全部丢失须重跑。
session_store：滚动暂存 + TTL 7 天 + 原子写。
"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_session_save_load_roundtrip(tmp_path, monkeypatch):
    """存取往返：HTML/历史/元数据完整。"""
    import stockpilot.core.session_store as ss
    monkeypatch.setattr(ss, "data_dir", lambda: tmp_path)
    ok = ss.save_session("600519", "贵州茅台", "大师投委会",
                         "<b>裁决：看多</b>",
                         [{"role": "user", "content": "Q"},
                          {"role": "assistant", "content": "A"}])
    assert ok
    data = ss.load_session("600519")
    assert data["kind"] == "大师投委会"
    assert "裁决" in data["html"]
    assert len(data["history"]) == 2
    assert data["ts"]


def test_session_rolling_overwrite(tmp_path, monkeypatch):
    """滚动覆盖：同一只股只保留最近一份（重跑不膨胀）。"""
    import stockpilot.core.session_store as ss
    monkeypatch.setattr(ss, "data_dir", lambda: tmp_path)
    ss.save_session("600519", "a", "诊断", "h1", [])
    ss.save_session("600519", "a", "大师投委会", "h2", [])
    data = ss.load_session("600519")
    assert data["kind"] == "大师投委会" and data["html"] == "h2"
    files = list((tmp_path / "sessions").glob("*.json"))
    assert len(files) == 1


def test_session_ttl_expired(tmp_path, monkeypatch):
    """TTL 过期：load 判 ts（改文件内容）、清理判 mtime（两路都验证）。"""
    import stockpilot.core.session_store as ss
    monkeypatch.setattr(ss, "data_dir", lambda: tmp_path)
    ss.save_session("000001", "a", "诊断", "x", [])
    # ① ts 老化 8 天 → load 返回 None
    f = tmp_path / "sessions" / "000001.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    data["ts"] = (datetime.now() -
                  timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert ss.load_session("000001") is None
    # ② mtime 老化 8 天 → 保存动作触发惰性清理
    import os
    old = time.time() - 8 * 86400
    os.utime(f, (old, old))
    ss.save_session("000002", "b", "诊断", "y", [])
    assert not f.exists()
    assert ss.load_session("000002") is not None   # 新鲜会话不受影响


def test_session_corrupted_json(tmp_path, monkeypatch):
    """损坏 JSON → None 不抛异常（半截文件防御）。"""
    import stockpilot.core.session_store as ss
    monkeypatch.setattr(ss, "data_dir", lambda: tmp_path)
    d = tmp_path / "sessions"
    d.mkdir(parents=True)
    (d / "600000.json").write_text("{broken", encoding="utf-8")
    assert ss.load_session("600000") is None


def test_session_atomic_write(tmp_path, monkeypatch):
    """原子写：不留 .tmp 残留。"""
    import stockpilot.core.session_store as ss
    monkeypatch.setattr(ss, "data_dir", lambda: tmp_path)
    ss.save_session("600519", "a", "诊断", "x", [])
    sess = tmp_path / "sessions"
    assert not list(sess.glob("*.tmp"))


def test_detail_dialog_offer_restore(qapp, monkeypatch, tmp_path):
    """GUI 端到端：有暂存 → 恢复条出现 → 点击回看 → HTML/历史还原。"""
    import stockpilot.core.session_store as ss
    monkeypatch.setattr(ss, "data_dir", lambda: tmp_path)
    ss.save_session("600519", "贵州茅台", "牛熊辩论",
                    "<b>⚖️ 首席裁决</b>：观望",
                    [{"role": "user", "content": "Q"},
                     {"role": "assistant", "content": "A"}])
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    from stockpilot.ui.pages.detail import DetailDialog
    ctx = AppContext(Config())
    ctx.monitor.market_fetch = lambda max_count=2000: []
    d = DetailDialog(ctx, "600519", "贵州茅台")
    btns = [b for b in d.findChildren(type(d.debate_btn)) if "回看" in b.text()]
    assert btns, "恢复条未出现"
    btns[0].click()
    assert "首席裁决" in d.ai_view.toHtml()
    assert len(d._ai_history) == 2
    assert "恢复" in d.ai_status.text() or "已恢复" in d.ai_status.text()


def test_detail_dialog_no_session_clean(qapp, tmp_path, monkeypatch):
    """无暂存 → 不出现恢复条（干净开窗）。"""
    import stockpilot.core.session_store as ss
    monkeypatch.setattr(ss, "data_dir", lambda: tmp_path)
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    from stockpilot.ui.pages.detail import DetailDialog
    ctx = AppContext(Config())
    ctx.monitor.market_fetch = lambda max_count=2000: []
    d = DetailDialog(ctx, "300999", "无暂存股")
    btns = [b for b in d.findChildren(type(d.debate_btn)) if "回看" in b.text()]
    assert not btns
