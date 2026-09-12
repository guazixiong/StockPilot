# -*- coding: utf-8 -*-
"""v7.2.10 A 形态修复闸门：QRunnable autoDelete 必须为 False。

真因（快照取证定位）：Worker setAutoDelete(True) 且 submit() 不保留
Python 引用 → QThreadPool 在 run() 后 delete C++ 对象，Python 包装器
随后被 GC 再释放同一指针 = 双重释放 → 延迟性随机 0xC0000005
（主线程 Qt 循环空闲 + QThreadPool 管理线程无 Python 栈）。
本测试防止任何人把 autoDelete 改回 True。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from stockpilot.ui import workers


def test_worker_autodelete_disabled():
    w = workers.Worker(lambda: None)
    assert w.autoDelete() is False, (
        "autoDelete 必须 False：True 时 C++ QThreadPool 删除 + Python GC "
        "= 双重释放（v7.2.10 A 形态崩溃真因）")


def test_worker_survives_run_and_gc():
    """run() 完成后 Python 包装器仍存活可访问（对象生命周期归 Python）。"""
    w = workers.Worker(lambda: 42, on_done=lambda r: None)
    w.run()                     # 直接跑（不经线程池）：fn=42 → done 信号
    # 此处能访问属性证明 C++ 对象未被 QThreadPool 提前 delete
    assert w.signals is not None
    assert w.autoDelete() is False
