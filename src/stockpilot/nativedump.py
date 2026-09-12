# -*- coding: utf-8 -*-
"""第五道防线（v7.2.10 修订三）：外部看门狗——native 崩溃取证通道。

演进史（为什么进程内通道全撤、为什么加活体快照）：
1. SEH filter（SetUnhandledExceptionFilter）：被后装的 faulthandler
   顶掉，从未被调（marker=0 实测）。
2. VEH（AddVectoredExceptionHandler）：真崩溃现场（解释器状态/GIL/
   堆已损）ctypes 回调体不可达，marker=0、无 dmp；且疑似干扰 WER
   分发（v7.2.10 初版期间 WER 全无踪迹）。已全部移除。
3. WER LocalDumps（HKCU 注册表，实测已配置）：本机 WerSvc 常停，
   A 形态真崩溃后无 dmp、无事件——发布机器不可依赖。
4. **watchdog 定型（修订三）**：exe spawn 自身 --watchdog <gui_pid>
   子进程。两个职责：
   a) 活体快照：每 DUMP_INTERVAL 秒对 GUI 进程做一次外部
      MiniDumpWriteDump（进程外调用，不依赖目标进程内任何状态；
      dump 落 data/logs/dumps/snap-<pid>-<seq>.dmp）。A 形态崩溃
      的 C 栈缺位问题由此兜底：崩溃前的最后一次快照 + 崩溃时刻
      faulthandler Python 栈 + 秒级死亡时间戳 = 完整取证链。
      快照只保留最近 KEEP 份（MiniDumpNormal 级别 ~几 MB，循环覆盖）。
   b) 死亡标记：WaitForSingleObject 感知 GUI 终止 → 秒级时间戳写
      crash.log（正常退出经 atexit stop 文件静默，不误报）。
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys

log = logging.getLogger(__name__)

DUMP_INTERVAL = 30      # 活体快照间隔（秒）
SNAPSHOT_KEEP = 3       # 快照循环保留份数


if sys.platform == "win32":
    import ctypes.wintypes as wt

    class _SYSTEMTIME(ctypes.Structure):
        # ctypes.wintypes 没有 SYSTEMTIME（曾让 watchdog 死亡标记写不出，
        # v7.2.10 实测 AttributeError）
        _fields_ = [(n, wt.WORD) for n in
                    ("wYear", "wMonth", "wDayOfWeek", "wDay",
                     "wHour", "wMinute", "wSecond", "wMilliseconds")]

    PROCESS_VM_READ = 0x0010
    PROCESS_QUERY_INFORMATION = 0x0400
    # MiniDumpNormal|WithProcessThreadData|WithUnloadedModules|
    # WithFullThreadInfo —— 全线程栈可回溯。**不含** WithHandleData：
    # 实测 GUI 进程句柄数据会把快照从 0.4MB 撑到 100MB+
    # （v7.2.10 修订三实机验证），循环保留 3 份会吃满便携用户磁盘。
    _DUMP_FLAGS = (0x00000000 | 0x00000001 | 0x00000004 |
                   0x00000020)


def install() -> None:
    """注册看门狗（幂等；仅 frozen GUI 进程实际起子进程）。

    由 crashguard.install 在 faulthandler.enable 之后调用。
    """
    if sys.platform != "win32":
        return
    _spawn_watchdog()


def _snapshot(gui_pid: int, dump_dir: str) -> bool:
    """对存活 GUI 进程做外部 minidump（一次；失败不重试不抛错）。"""
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        dbg = ctypes.WinDLL("dbghelp", use_last_error=True)
        dbg.MiniDumpWriteDump.restype = wt.BOOL
        dbg.MiniDumpWriteDump.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p]
        h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION,
                            False, gui_pid)
        if not h:
            return False
        try:
            path = os.path.join(dump_dir, "snap.dmp.tmp")
            h_file = k32.CreateFileW(path, 0x40000000, 0, None, 2,
                                     0x80, None)
            if h_file in (None, 0, -1, 0xFFFFFFFFFFFFFFFF):
                return False
            try:
                ok = dbg.MiniDumpWriteDump(h, gui_pid, h_file,
                                           _DUMP_FLAGS, None, None, None)
            finally:
                k32.CloseHandle(h_file)
            if not ok:
                return False
            # 快照循环保留：snap.dmp.tmp → snap-<n>.dmp（n 循环 0..KEEP-1）
            seq = getattr(_snapshot, "_seq", -1) + 1
            _snapshot._seq = seq
            final = os.path.join(dump_dir, f"snap-{seq % SNAPSHOT_KEEP}.dmp")
            os.replace(path, final)
            return True
        finally:
            k32.CloseHandle(h)
    except Exception:  # noqa: BLE001 —— 快照失败不影响看门狗本职
        return False


def watchdog_main(gui_pid: int) -> int:
    """--watchdog 模式入口：活体快照 + 监控 GUI 死亡记录精确时刻。

    循环 WaitForSingleObject(2s)：
    - WAIT_OBJECT_0（GUI 终止）→ 异常死亡标记写 crash.log（正常关闭
      路径 GUI 的 atexit 已先写 stop 文件，watchdog 见到即自杀）。
    - WAIT_TIMEOUT → 每满 DUMP_INTERVAL 做一次活体快照。
    返回 0。
    """
    if sys.platform != "win32":
        return 0

    k32 = ctypes.windll.kernel32
    SYNCHRONIZE = 0x00100000
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
                        False, gui_pid)
    if not h:
        return 0          # 拿不到句柄（权限/已死）：无从监控
    try:
        from .core.storage import data_dir
        dump_dir = data_dir() / "logs" / "dumps"
        dump_dir.mkdir(parents=True, exist_ok=True)
        dump_dir_s = str(dump_dir)
        import time as _time
        last_snap = 0.0
        while True:
            rc = k32.WaitForSingleObject(h, 2000)
            if rc == 0:            # WAIT_OBJECT_0：GUI 进程已终止
                # bootlifetime 正常关闭时 install() 已调用 _stop_watchdog
                # （watchdog 收到退出指令）；这里只处理异常死亡：
                st = _SYSTEMTIME()
                k32.GetLocalTime(ctypes.byref(st))
                line = (f"[{st.wYear:04d}-{st.wMonth:02d}-{st.wDay:02d} "
                        f"{st.wHour:02d}:{st.wMinute:02d}:{st.wSecond:02d}] "
                        f"watchdog: GUI 进程 pid={gui_pid} 异常终止"
                        f"（此刻起 5 秒内 WER 事件即对应崩溃现场）\n")
                try:
                    with open(dump_dir.parent / "crash.log", "a",
                              encoding="utf-8", buffering=1) as f:
                        f.write(line)
                except OSError:
                    pass
                return 0
            if rc != 0x102:        # 非 WAIT_TIMEOUT：句柄失效（GUI 已死）
                return 0
            # WAIT_TIMEOUT：GUI 活着
            _stop = os.environ.get("SP_WATCHDOG_STOP_FILE")
            if _stop and os.path.exists(_stop):
                try:
                    os.remove(_stop)
                except OSError:
                    pass
                return 0
            now = _time.monotonic()
            if now - last_snap >= DUMP_INTERVAL:
                last_snap = now
                _snapshot(gui_pid, dump_dir_s)
    finally:
        k32.CloseHandle(h)


def _spawn_watchdog() -> None:
    """启动自看门狗子进程（exe 自身 --watchdog <本进程 pid>）。"""
    if not getattr(sys, "frozen", False):
        return          # 源码模式不启用（开发时崩溃靠 IDE/终端观察）
    try:
        import subprocess
        stop_file = os.path.join(os.environ.get("TEMP", "."),
                                 f"sp-watchdog-stop-{os.getpid()}")
        env = dict(os.environ, SP_WATCHDOG_STOP_FILE=stop_file)
        subprocess.Popen(
            [sys.executable, "--watchdog", str(os.getpid())],
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=env, close_fds=True)
        import atexit
        atexit.register(lambda: _stop_watchdog(stop_file))
        # 正常退出信号：atexit 写 stop 文件，watchdog 看到后自杀，
        # 不会把正常关闭误报成"异常终止"。
    except Exception:  # noqa: BLE001 —— 看门狗失败不影响主程序
        log.debug("watchdog spawn failed", exc_info=True)


def _stop_watchdog(stop_file: str) -> None:
    """正常退出路径：置位 stop 标志（watchdog 2s 内退出）。"""
    try:
        with open(stop_file, "w", encoding="utf-8") as f:
            f.write("stop")
    except OSError:
        pass
