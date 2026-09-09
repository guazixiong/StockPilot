"""后台任务框架：Worker(QRunnable) + Signals。

约定：on_done/on_err/on_progress 必须传 UI 对象的**绑定方法**（QObject），
Qt 自动使用队列连接，槽函数在主线程执行；Worker 在 QThreadPool 中运行。
"""
from __future__ import annotations

import traceback
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRunnable, Signal


class WorkerSignals(QObject):
    done = Signal(object)
    err = Signal(str)
    progress = Signal(str)


class Worker(QRunnable):
    """fn(*args, **kwargs) 在线程池执行；with_progress=True 时自动注入 on_progress。"""

    def __init__(self, fn: Callable, *args, on_done: Optional[Callable] = None,
                 on_err: Optional[Callable] = None,
                 on_progress: Optional[Callable] = None,
                 with_progress: bool = False, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.with_progress = with_progress
        self.signals = WorkerSignals()
        if on_done is not None:
            self.signals.done.connect(on_done)
        if on_err is not None:
            self.signals.err.connect(on_err)
        if on_progress is not None:
            self.signals.progress.connect(on_progress)
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover Qt 线程
        try:
            kwargs = dict(self.kwargs)
            if self.with_progress:
                kwargs["on_progress"] = _SafeEmitter(self.signals.progress)
            result = self.fn(*self.args, **kwargs)
            self._emit_done(result)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            msg = str(exc) if exc.__class__.__name__ == "AiError" else f"{exc.__class__.__name__}: {exc}"
            self._emit_err(msg)

    def _emit_done(self, result) -> None:
        try:
            self.signals.done.emit(result)
        except RuntimeError:
            pass    # 槽方（页面）已销毁：测试关窗/切页时竞态，静默即可

    def _emit_err(self, msg: str) -> None:
        try:
            self.signals.err.emit(msg)
        except RuntimeError:
            pass


class _SafeEmitter:
    """on_progress 包装：接收方已销毁时不再向上抛（Qt 竞态下 3 次崩溃已见）。"""

    def __init__(self, sig):
        self._sig = sig

    def __call__(self, text: str) -> None:
        try:
            self._sig.emit(text)
        except RuntimeError:
            pass


def submit(fn: Callable, *args, on_done=None, on_err=None, on_progress=None,
           with_progress: bool = False, pool=None, **kwargs) -> Worker:
    """构造并投递到全局线程池。"""
    from PySide6.QtCore import QThreadPool

    worker = Worker(fn, *args, on_done=on_done, on_err=on_err,
                    on_progress=on_progress, with_progress=with_progress, **kwargs)
    (pool or QThreadPool.globalInstance()).start(worker)
    return worker
