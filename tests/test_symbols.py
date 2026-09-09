"""代码↔市场映射。"""
import pytest

from stockpilot.core.providers.base import normalize_code, to_market_symbol, to_secid


@pytest.mark.parametrize("raw,code", [
    ("600519", "600519"), ("sh600519", "600519"), ("600519.SH", "600519"),
    ("1.600519", "600519"), ("茅台600519", "600519"), (" 000001 ", "000001"),
])
def test_normalize(raw, code):
    assert normalize_code(raw) == code


@pytest.mark.parametrize("code,sym", [
    ("600519", "sh600519"), ("688981", "sh688981"), ("510300", "sh510300"),
    ("900901", "sh900901"), ("000001", "sz000001"), ("300750", "sz300750"),
    ("200012", "sz200012"), ("830799", "bj830799"), ("430047", "bj430047"),
    ("920001", "bj920001"), ("sh600519", "sh600519"),
    ("sh000001", "sh000001"), ("sz399001", "sz399001"), ("sh000688", "sh000688"),
])
def test_market_symbol(code, sym):
    assert to_market_symbol(code) == sym


@pytest.mark.parametrize("code,secid", [
    ("600519", "1.600519"), ("000001", "0.000001"),
    ("300750", "0.300750"), ("830799", "0.830799"),
])
def test_secid(code, secid):
    assert to_secid(code) == secid
