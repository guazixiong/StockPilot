"""统一错误呈现（v7.2）：异常不进页面主布局，弹窗报错。

用户痛点：异常文本（往往是长串 ProviderError）直接 setText 到主布局控件
（价格标签/进度区/标题区），把整个页面布局挤变形。

规则（docs 设计稿一致性 + 本条用户要求）：
- **弹窗报错**：`show_error(parent, title, msg)`——QMessageBox，错误第一时间
  可见，且不占用页面任何布局空间；
- **节流**：同一错误 10 秒内不重复弹（后台定时器每轮扫描都可能失败，
  不做去重会弹窗轰炸）；
- **主区域只留短状态**：`fail_hint(label, short)`——状态区（hint 小字）
  允许写 ≤20 字短文案，超长自动截断，绝不写主数据位。

线程语义：show_error 必须在主线程调用（Worker 的 on_err 回调本来就是
主线程槽，直接可用）。
"""
from __future__ import annotations

import time
from typing import Optional

_last_popup: dict = {}      # key -> ts（弹窗节流）


def show_error(parent, title: str, msg: str, *,
               dedup_sec: int = 10) -> None:
    """错误弹窗（唯一正确的异常出口）。

    parent: 弹窗宿主（页面/窗口）；msg 任意长度——弹窗内自然换行，
    不影响任何页面布局。同键 dedup_sec 内不重复弹。
    """
    key = f"{title}|{str(msg)[:80]}"
    now = time.time()
    if now - _last_popup.get(key, 0.0) < dedup_sec:
        return
    _last_popup[key] = now
    try:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(parent, title, str(msg))
    except Exception:  # noqa: BLE001 无 GUI 环境（测试）静默
        pass


def fail_hint(label, short: str, max_len: int = 20) -> None:
    """状态区短文案（允许写 hint 标签——布局安全）。

    超过 max_len 截断加省略号：状态条是单行小字，长串会撑破工具行布局。
    """
    text = str(short).strip()
    if len(text) > max_len:
        text = text[:max_len] + "…"
    try:
        label.setText(text)
    except Exception:  # noqa: BLE001
        pass


def shorten(msg: str, max_len: int = 60) -> str:
    """错误消息压缩（弹窗正文用——保留可读性但去噪声）。"""
    text = str(msg).strip().replace(chr(10), " ")
    return text if len(text) <= max_len else text[:max_len] + "…"
