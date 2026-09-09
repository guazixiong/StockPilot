"""持仓盈亏 / 交易流水统计 / CSV 导出。"""
from stockpilot.core.export_csv import rows_to_csv
from stockpilot.core.models import Quote
from stockpilot.core.portfolio import journal_stats, position_metrics


def _quote(price, prev):
    return Quote(code="600519", name="贵州茅台", price=price, prev_close=prev)


def test_position_metrics():
    pos = {"name": "贵州茅台", "cost": 1250.0, "qty": 200, "date": "2026-08-01"}
    m = position_metrics(pos, _quote(1300.0, 1290.0))
    assert m["market_value"] == 260000.0
    assert m["pnl"] == 10000.0
    assert abs(m["pnl_pct"] - 4.0) < 1e-9
    assert m["today_pnl"] == 2000.0
    assert abs(m["today_pnl_pct"] - (1300.0 / 1290.0 - 1) * 100) < 1e-9


def test_position_metrics_none_quote():
    pos = {"name": "x", "cost": 10.0, "qty": 100}
    m = position_metrics(pos, None)
    assert m["price"] is None and m["pnl"] is None


def test_portfolio_summary():
    from stockpilot.core.portfolio import portfolio_summary
    rows = [position_metrics({"cost": 10, "qty": 100}, _quote(11.0, 10.5)),
            position_metrics({"cost": 20, "qty": 200}, _quote(19.0, 19.5))]
    s = portfolio_summary(rows)
    assert s["market_value"] == 1100 + 3800
    assert s["pnl"] == 100 - 200
    assert s["today_pnl"] == 50 - 100


def test_journal_stats():
    s = journal_stats([
        {"side": "buy", "price": 10.0, "qty": 100, "fee": 5},
        {"side": "sell", "price": 11.0, "qty": 100, "fee": 5},
    ])
    assert s["buy"] == 1000 and s["sell"] == 1100
    assert s["fee"] == 10 and s["net"] == -90


def test_export_csv(tmp_path):
    path = str(tmp_path / "out.csv")
    rows_to_csv(path, ["代码", "现价", "空值"],
                [{"代码": "600519", "现价": 1297.5, "空值": None}])
    content = open(path, "rb").read()
    assert content.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    text = content.decode("utf-8-sig")
    assert "代码,现价,空值" in text
    assert "600519,1297.5," in text
