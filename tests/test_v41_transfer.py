"""v4.1：策略库/选股方案导入导出往返 + 配置迁移对称性。"""
import json
import threading as _th

from stockpilot.core.storage import Config


class _FakeCfg(Config):
    def __init__(self, tmp):
        self._lock = _th.Lock()
        self.path = tmp / "cfg.json"
        self.data = {"watchlist": [], "ai": {"profiles": {}}, "strategies": [],
                     "screen_plans": [], "monitor": {"positions": {},
                                                     "history": [], "fired": []}}


def test_strategy_roundtrip(tmp_path, monkeypatch):
    from stockpilot.ui import transfer as tf

    class FakeDlg:
        @staticmethod
        def getSaveFileName(*a, **k):
            return (str(tmp_path / "stg.json"), "")
        @staticmethod
        def getOpenFileName(*a, **k):
            return (str(tmp_path / "stg.json"), "")

    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog", FakeDlg)
    cfg = _FakeCfg(tmp_path)
    cfg.save_strategy_dict({"name": "我的策略",
                           "buy_rules": [{"indicator": "price", "op": ">",
                                          "value": "ma20", "logic": "and"}],
                           "sell_rules": [], "stop_loss_pct": 5,
                           "take_profit_pct": 10, "max_hold_days": 20})
    ok, msg = tf.export_strategies(None, cfg)
    assert ok and "1 套" in msg
    # 新机器导入
    cfg2 = _FakeCfg(tmp_path)
    ok2, msg2 = tf.import_strategies(None, cfg2)
    assert ok2 and "1 套" in msg2
    assert cfg2.get_strategy_dicts()[0]["name"] == "我的策略"


def test_plan_roundtrip(tmp_path, monkeypatch):
    from stockpilot.ui import transfer as tf

    class FakeDlg:
        @staticmethod
        def getSaveFileName(*a, **k):
            return (str(tmp_path / "plan.json"), "")
        @staticmethod
        def getOpenFileName(*a, **k):
            return (str(tmp_path / "plan.json"), "")

    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog", FakeDlg)
    cfg = _FakeCfg(tmp_path)
    cfg.save_plan("稳健", {"turnover_rate_min": 3, "turnover_rate_max": 15},
                  [{"indicator": "ma_bull", "op": "==", "value": True}])
    ok, _ = tf.export_screen_plans(None, cfg)
    assert ok
    cfg2 = _FakeCfg(tmp_path)
    ok2, _ = tf.import_screen_plans(None, cfg2)
    assert ok2
    p = cfg2.get_plans()[0]
    assert p["name"] == "稳健"
    assert p["cond"]["turnover_rate_min"] == 3


def test_import_bad_format(tmp_path, monkeypatch):
    from stockpilot.ui import transfer as tf

    class FakeDlg:
        @staticmethod
        def getOpenFileName(*a, **k):
            return (str(tmp_path / "bad.json"), "")

    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog", FakeDlg)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"foo": 1}), encoding="utf-8")
    cfg = _FakeCfg(tmp_path)
    ok, msg = tf.import_strategies(None, cfg)
    assert not ok and "格式" in msg
