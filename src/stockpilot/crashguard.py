"""全局崩溃捕获：闪退不消失，全部落盘 data/logs/crash.log 可查。

三道防线（v7.2.1，用户报告"总是闪退且无报错信息"）：
1. sys.excepthook —— 主线程未捕获 Python 异常（原本直接 stderr 后闪退）；
2. threading.excepthook —— 后台 Worker/QRunnable 线程异常（此前只打 log，UI 无感）；
3. Qt 消息钩子 —— qWarning/qFatal/qCritical 里的 libpyside 转储（如
   "Failed to disconnect ... from signal"，闪退前兆）统一记录。

写入 crash.log（带版本/时间/线程/堆栈），主线程异常额外弹一个
QMessageBox（应用还没死透时），让"闪退"变成"看得见的报错"。
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_SEP = "\n" + "=" * 72 + "\n"

# v7.2.7：同一异常签名在冷却期内只弹一次窗（递归型异常——如每次
# mouseMove 都抛——会无限叠弹窗，用户点掉一个又弹一个）。落盘/日志不省略。
_DLG_COOLDOWN = 30.0
_last_dlg: tuple[str, float] | None = None


def _crash_path() -> Path:
    from .core.storage import data_dir
    return data_dir() / "logs" / "crash.log"


def write_crash(kind: str, exc: BaseException | None = None,
                raw_text: str = "") -> Path | None:
    """把一次崩溃/严重错误写入 crash.log。返回路径（失败返回 None）。"""
    try:
        p = _crash_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        from . import __version__
        head = (f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
                f"v{__version__} pid={os.getpid()} 线程={threading.current_thread().name}"
                f" 类型={kind}\n")
        if exc is not None:
            body = ("".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__)))
        else:
            body = (raw_text.rstrip() + "\n")
        with open(p, "a", encoding="utf-8") as f:
            f.write(_SEP + head + body)
        return p
    except Exception:  # noqa: BLE001 —— 崩溃记录自身绝不能再崩
        return None


def _py_hook(tp, val, tb) -> None:
    """主线程未捕获异常：写 crash.log + 日志 + 尽力弹窗（替代直接闪退）。"""
    global _last_dlg
    write_crash("未捕获异常(主线程)", exc=val)
    logging.getLogger("crash").critical(
        "未捕获异常(主线程): %s", "".join(
            traceback.format_exception(tp, val, tb))[:2000])
    # v7.2.7：同一 (类型, 消息) 30 秒内只弹一次——缺陷①实况是每次鼠标
    # 移动都触发同一 AttributeError，弹窗点掉一个又弹一个刷屏。崩溃仍
    # 全量落盘，只是不再反复打扰。
    try:
        key = f"{tp.__name__}:{val}"
        now = time.monotonic()
        if _last_dlg is None or (key != _last_dlg[0]
                                 or now - _last_dlg[1] > _DLG_COOLDOWN):
            _last_dlg = (key, now)
            # 应用还能弹窗时给用户一个看得见的报错（而不是无声闪退）
            from PySide6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None, "StockPilot 遇到错误",
                    f"程序遇到未处理的错误（已记录到日志，可到 设置→运行日志 查看）：\n\n"
                    f"{tp.__name__}: {val}\n\n"
                    f"崩溃详情已写入 crash.log。点 OK 后程序可能继续运行或退出。")
    except Exception:  # noqa: BLE001
        pass
    _orig_py_hook(tp, val, tb)


def _thread_hook(args) -> None:
    """后台线程未捕获异常：写 crash.log（此前 Worker 异常只进 app.log）。"""
    write_crash("未捕获异常(后台线程)", exc=args.exc_value)
    log.critical("后台线程 %s 异常: %s",
                 getattr(args.thread, "name", "?"), args.exc_value)


def _qt_msg_handler(mode, context, message) -> None:
    """Qt 侧 qFatal/qCritical/libpyside 转储 → crash.log（闪退前兆可回查）。"""
    level = {0: "QtDebug", 1: "QtWarning", 2: "QtCritical",
             4: "QtFatal"}.get(int(mode), f"Qt({int(mode)})")
    if int(mode) in (2, 4):                      # Critical / Fatal
        write_crash(level, raw_text=f"{level}: {message}")


_orig_py_hook = sys.excepthook
_orig_qt_handler = None


def install() -> None:
    """装上三道防线。幂等（重复安装无副作用）。"""
    global _orig_py_hook, _orig_qt_handler
    # v7.2.7：二次安装不得把已装上的 _py_hook 记成"原钩子"——否则
    # 异常链 _py_hook→_orig→_py_hook… 无限自递归（RecursionError）。
    if sys.excepthook is not _py_hook:
        _orig_py_hook = sys.excepthook
        sys.excepthook = _py_hook
    threading.excepthook = _thread_hook
    try:
        from PySide6.QtCore import qInstallMessageHandler
        qInstallMessageHandler(_qt_msg_handler)
    except ImportError:                          # 无 GUI 场景（smoke/CI）
        pass
    log.info("崩溃捕获已安装：crash.log = %s", _crash_path())
