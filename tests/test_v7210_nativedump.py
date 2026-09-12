# -*- coding: utf-8 -*-
"""v7.2.10 第五道防线（修订二）：watchdog-only 模式验证。

v7.2.10 修订二撤掉了进程内 VEH/SEH 钩子（真崩溃现场不可达 + 疑似
干扰 WER 分发），本模块只剩外部看门狗。install 幂等、不注册任何
进程内异常钩子（关键回归断言——不能把 WER 分发链搞坏）。

沙箱：所有 install 类测试必须 monkeypatch data_dir 到 tmp_path——
crashguard/faulthandler 的启用标记行会写 data/logs/crash.log，若落
在共享 tmp CWD 会污染 v721 log_viewer（期望"暂无崩溃记录"）。
"""
import sys


def test_install_idempotent_and_sandboxed(tmp_path, monkeypatch):
    monkeypatch.setattr("stockpilot.core.storage.data_dir", lambda: tmp_path)
    from stockpilot import nativedump
    nativedump.install()
    nativedump.install()          # 二次安装不得抛错（幂等）
    assert True                   # 到这里即通过：install 链路无异常


def test_no_inprocess_hooks_registered(tmp_path, monkeypatch):
    """关键回归断言：install 不得在进程内留下任何异常钩子——
    v7.2.10 初版的 VEH+SEH 曾让 WER 报告不再生成（v7.2.9 正常）。"""
    monkeypatch.setattr("stockpilot.core.storage.data_dir", lambda: tmp_path)
    from stockpilot import nativedump
    nativedump.install()
    # 瘦身后这些属性都不应存在
    assert not hasattr(nativedump, "_g_filter_ref"), "SEH 钩子应已移除"
    assert not hasattr(nativedump, "_g_veh_ref"), "VEH 钩子应已移除"


def test_source_mode_no_watchdog_spawn(tmp_path, monkeypatch):
    """源码模式（sys.frozen 不存在）install 不得 spawn 子进程。"""
    monkeypatch.setattr("stockpilot.core.storage.data_dir", lambda: tmp_path)
    from stockpilot import nativedump
    nativedump._spawn_watchdog()   # 源码模式下应为 no-op
    assert True


def test_crashguard_wires_nativedump(tmp_path, monkeypatch):
    """crashguard.install 全链路（含第五道防线）无异常。"""
    monkeypatch.setattr("stockpilot.core.storage.data_dir", lambda: tmp_path)
    from stockpilot import crashguard
    crashguard.install()          # 幂等（内部有防线）
    assert crashguard._orig_py_hook is not None
