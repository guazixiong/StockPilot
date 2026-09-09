"""v5.2.1：导入模板防回归——模板与导入器字段必须严格往返兼容。

用户痛点：导入按钮有，但不知道文件长什么样（无模板）。
模板已内置（transfer.TEMPLATES 5 种）；本测试锁死"模板可被导入器
正确解析"的契约——模板改了字段名而导入器没跟上（或反之）立刻失败。
"""
import csv
import io
import json

import pytest

pytest.importorskip("PySide6")


def _templates():
    from stockpilot.ui.transfer import TEMPLATES
    return TEMPLATES


def test_templates_registry_complete():
    """5 种模板齐全且都能产出非空内容。"""
    for kind, (fname, maker) in _templates().items():
        content = maker()
        assert content and content.strip(), f"{kind} 模板为空"
        assert fname.endswith((".json", ".csv"))


def test_ai_config_template_parses():
    """AI 模板 → import_ai_config 的 ai 节校验（isinstance dict）。"""
    _, maker = _templates()["ai_config"]
    data = json.loads(maker())
    ai = data.get("ai")
    assert isinstance(ai, dict), "缺少 ai 节（导入器将拒绝）"
    assert ai.get("vendor") and ai.get("api_key") and ai.get("model")


def test_watchlist_template_columns():
    """自选模板：code,name 两列 + 示例行 code 为 6 位数字（导入器过滤条件）。"""
    _, maker = _templates()["watchlist"]
    rows = list(csv.DictReader(io.StringIO(maker())))
    assert rows and set(rows[0]) == {"code", "name"}
    for r in rows:
        assert len(r["code"]) == 6 and r["code"].isdigit(), \
            "示例 code 必须满足导入器 6 位数字校验"
    assert rows[0]["name"] == "贵州茅台"


def test_positions_template_columns():
    """持仓模板：6 列齐全 + 示例行通过导入器类型/正负校验。"""
    _, maker = _templates()["positions"]
    text = maker()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    header = lines[0].split(",")
    assert header == ["code", "name", "cost", "qty", "date", "strategy"]
    # 第一条示例行按导入器逻辑校验
    r = dict(zip(header, lines[1].split(",")))
    assert len(r["code"]) == 6 and r["code"].isdigit()
    assert float(r["cost"]) > 0 and int(float(r["qty"])) > 0


def test_strategies_template_parses():
    """策略模板 → import_strategies 的 strategies 列表 + Strategy.from_dict 兼容。"""
    from stockpilot.core import strategy as stg
    _, maker = _templates()["strategies"]
    data = json.loads(maker())
    st = data.get("strategies")
    assert isinstance(st, list) and st, "缺少 strategies 节"
    for d in st:
        assert isinstance(d, dict) and d.get("name")
        # 模板里的策略必须能被核心 Strategy 反序列化（导入后即可用）
        obj = stg.Strategy.from_dict(d)
        assert obj.name == d["name"]
        assert obj.buy_rules


def test_screen_plans_template_parses():
    """方案模板 → import_screen_plans 的 plans 列表 + cond 键在真实注册表内。"""
    from stockpilot.ui.pages.screener import _COND_DEFS
    _, maker = _templates()["screen_plans"]
    data = json.loads(maker())
    plans = data.get("plans")
    assert isinstance(plans, list) and plans
    legal_keys = {k for k, *_ in _COND_DEFS}
    for p in plans:
        assert p.get("name")
        for cond_key in (p.get("cond") or {}):
            base = cond_key.rsplit("_", 1)[0]
            assert base in legal_keys, \
                f"cond 键 {cond_key} 不在筛选条件注册表（导入后无法生效）"
        assert isinstance(p.get("tech"), list)


def test_save_template_writes_file(tmp_path, monkeypatch):
    """save_template 落盘：文件名默认值 + 内容即模板。"""
    from PySide6.QtWidgets import QApplication, QFileDialog
    app = QApplication.instance() or QApplication([])
    from stockpilot.ui import transfer as tf
    out = tmp_path / "自选股导入模板.csv"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out), "")))
    ok, msg = tf.save_template(None, "watchlist")
    assert ok, msg
    assert out.exists()
    assert out.read_text(encoding="utf-8").startswith("code,name")
    # 未知类型
    ok2, _ = tf.save_template(None, "nope")
    assert not ok2
