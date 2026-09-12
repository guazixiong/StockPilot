# -*- coding: utf-8 -*-
"""第五道防线（v7.2.10 修订二）：外部看门狗——唯一可靠的 native 崩溃取证通道。

演进史（为什么进程内通道全撤）：
1. SEH filter（SetUnhandledExceptionFilter）：被后装的 faulthandler 顶掉，
   从未被调（marker=0 实测）。
2. VEH（AddVectoredExceptionHandler，插队头）：可达性更糟——真崩溃现场
   （解释器状态/GIL/堆已损）ctypes 回调体不可达，v7.2.10 三次真崩溃
   marker=0、无 dmp；且 ctypes-Python 回调插进异常分发链路，疑似干扰
   v7.2.9 时代正常生成的 WER 报告（v7.2.10 三次真崩溃 WER 全无踪迹，
   ReportArchive 最新条目停留在 v7.2.9 的 9-11 11:20）。
3. **watchdog（定型）**：第二个进程（exe 自身 --watchdog <gui_pid>）
   周期 WaitForSingleObject 观察 GUI。死亡 → 精确秒级时间戳写入
   crash.log → 事后按时间窗从 WER（事件日志/ReportArchive）提取
   "模块+偏移"。观察者在崩溃进程之外，不受解释器死亡影响。

进程内仍保留 faulthandler（纯 C，crashguard 管理）——它不受本模块影响。
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys

log = logging.getLogger(__name__)

if sys.platform == "win32":
    import ctypes.wintypes as wt

    class _SYSTEMTIME(ctypes.Structure):
        # ctypes.wintypes 没有 SYSTEMTIME（曾让 watchdog 死亡标记写不出，
        # v7.2.10 实测 AttributeError）
        _fields_ = [(n, wt.WORD) for n in
                    ("wYear", "wMonth", "wDayOfWeek", "wDay",
                     "wHour", "wMinute", "wSecond", "wMilliseconds")]


def install() -> None:
    """注册看门狗（幂等；仅 frozen GUI 进程实际起子进程）。

    由 crashguard.install 在 faulthandler.enable 之后调用。
    进程内 VEH/SEH 钩子已在 v7.2.10 修订二全部移除（见模块 docstring）。
    """
    if sys.platform != "win32":
        return
    _spawn_watchdog()


def watchdog_main(gui_pid: int) -> int:
    """--watchdog 模式入口：监控父 GUI 进程，死亡时记录精确时刻。

    由 install() spawn。循环 WaitForSingleObject(2s)：
    - GUI 终止（rc==0）→ 异常死亡标记写 crash.log（秒级时间戳，
      供对窗提取 WER 报告）；正常关闭路径 GUI 的 atexit 会先写
      stop 文件，watchdog 看到即自杀，不会误报。
    - WAIT_TIMEOUT → 查 stop 文件。返回 0。
    """
    if sys.platform != "win32":
        return 0
    import time as _time

    k32 = ctypes.windll.kernel32
    SYNCHRONIZE = 0x00100000
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
                        False, gui_pid)
    if not h:
        return 0          # 拿不到句柄（权限/已死）：无从监控
    try:
        while True:
            rc = k32.WaitForSingleObject(h, 2000)
            if rc == 0:            # WAIT_OBJECT_0：GUI 进程已终止
                # bootlifetime 正常关闭时 install() 已调用 _stop_watchdog
                # （watchdog 收到退出指令）；这里只处理异常死亡：
                from .core.storage import data_dir
                st = _SYSTEMTIME()
                k32.GetLocalTime(ctypes.byref(st))
                line = (f"[{st.wYear:04d}-{st.wMonth:02d}-{st.wDay:02d} "
                        f"{st.wHour:02d}:{st.wMinute:02d}:{st.wSecond:02d}] "
                        f"watchdog: GUI 进程 pid={gui_pid} 异常终止"
                        f"（此刻起 5 秒内 WER 事件即对应崩溃现场）\n")
                try:
                    d = data_dir() / "logs"
                    d.mkdir(parents=True, exist_ok=True)
                    with open(d / "crash.log", "a",
                              encoding="utf-8", buffering=1) as f:
                        f.write(line)
                except OSError:
                    pass
                return 0
            if rc != 0x102:        # 非 WAIT_TIMEOUT：句柄失效（GUI 已死）
                return 0
            # WAIT_TIMEOUT：GUI 活着。正常退出路径（closeEvent → 进程退出）
                # 由 Python atexit 调 _stop_watchdog 置位标志——watchdog
                # 进程读取该标志后自杀。标志通过临时文件传递（简单可靠）。
            _stop = os.environ.get("SP_WATCHDOG_STOP_FILE")
            if _stop and os.path.exists(_stop):
                try:
                    os.remove(_stop)
                except OSError:
                    pass
                return 0
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
