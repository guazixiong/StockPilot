"""聚合自检：策略/回测/通知等离线用例必须全部通过。"""
from stockpilot.core.selftest import run_offline_selftest


def test_all_offline_cases_pass():
    results = run_offline_selftest()
    failed = [(n, d) for n, ok, d in results if not ok]
    assert not failed, f"失败用例: {failed}"
    assert len(results) >= 7
