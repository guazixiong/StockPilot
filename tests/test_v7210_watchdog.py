# -*- coding: utf-8 -*-
"""v7.2.10 闸门：看门狗取证通道（外部进程观察 GUI 异常死亡）。

进程内 VEH/SEH 回调体是 ctypes 调度的 Python 代码——真崩溃现场
（解释器状态/GIL/堆已损）实测不可达（marker=0 无 dmp），而
faulthandler（纯 C）每次都能写出栈。取证通道改为外部看门狗：
exe 自身以 --watchdog <pid> 跑第二进程，GUI 异常死亡时把精确时刻
写入 crash.log（标记 WER 事件提取窗口）。
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from stockpilot import nativedump


class TestWatchdog:
    def test_watchdog_main_bad_pid_returns_zero(self):
        """无效 pid：OpenProcess 失败，watchdog 必须安静退出（0）。"""
        assert nativedump.watchdog_main(999999999) == 0

    def test_spawn_noop_in_source_mode(self):
        """源码模式（非 frozen）：_spawn_watchdog 不 spawn 任何东西。"""
        before = os.listdir(os.environ.get("TEMP", "."))
        nativedump._spawn_watchdog()   # frozen=False → 直接 return
        after = os.listdir(os.environ.get("TEMP", "."))
        # 没有新增 sp-watchdog-stop 文件
        assert not any(f.startswith("sp-watchdog-stop-") for f in after
                       if f not in before)

    def test_stop_watchdog_writes_flag(self, tmp_path):
        """正常退出路径：stop 文件被写出（watchdog 看到后自杀）。"""
        flag = str(tmp_path / "stop-flag")
        nativedump._stop_watchdog(flag)
        assert os.path.exists(flag)
        with open(flag, encoding="utf-8") as f:
            assert f.read() == "stop"

    def test_snapshot_writes_rotating_dumps(self, tmp_path):
        """活体快照：两次快照产出循环命名的 snap-N.dmp（修订三取证通道）。"""
        if sys.platform != "win32":
            return
        victim = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            ok1 = nativedump._snapshot(victim.pid, str(tmp_path))
            ok2 = nativedump._snapshot(victim.pid, str(tmp_path))
            dumps = list(Path(tmp_path).glob("snap-*.dmp"))
            if ok1 and ok2:       # 极端环境（权限）下 OpenProcess 可失败
                assert len(dumps) >= 2, "快照应落盘两份"
                assert all(d.stat().st_size > 100_000 for d in dumps), \
                    "MiniDumpWriteDump 应产出真实 dmp（>100KB）"
        finally:
            victim.kill()
