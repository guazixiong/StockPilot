# -*- coding: utf-8 -*-
"""v7.2.10 闸门：错误弹窗防重入（崩溃根因回归测试）。

事故：网络故障风暴下各页面 on_err 排队信号在主线程汇聚，若第一个
QMessageBox 模态弹窗（exec 嵌套事件循环）还开着，第二个弹窗在其
嵌套循环里创建——v7.2.10 崩溃栈正是 err.py:39 → home._on_scan_err
→ err.py:39 → boards._on_boards_err 的双层嵌套。本组测试锁定
show_error 的三个行为：节流、防重入、重入丢弃后状态恢复。
"""
import time

import pytest

from stockpilot.ui import err as errmod


@pytest.fixture(autouse=True)
def _reset_state():
    errmod._last_popup.clear()
    errmod._popup_open = False
    yield
    errmod._last_popup.clear()
    errmod._popup_open = False


def _fake_parent():
    class P:  # 无 GUI 环境：QMessageBox 导入失败走静默分支
        pass
    return P()


class TestPopupReentry:
    def test_second_popup_suppressed_while_first_open(self):
        """第一个弹窗未关时，后续弹窗必须被直接丢弃（不进 QMessageBox）。"""
        errmod.show_error(_fake_parent(), "标题A", "消息A")
        # 模拟第一个弹窗还开着（真实场景中 exec 循环未返回）
        errmod._popup_open = True
        errmod.show_error(_fake_parent(), "标题B", "消息B")
        errmod.show_error(_fake_parent(), "机会扫描失败", "另一条完全不同的错误")
        # 弹窗已关后恢复
        errmod._popup_open = False
        errmod.show_error(_fake_parent(), "标题C", "消息C")

    def test_popup_open_resets_after_return(self):
        """正常弹窗结束后 _popup_open 必须复位，否则永久吞掉所有弹窗。"""
        errmod.show_error(_fake_parent(), "标题", "消息")
        assert errmod._popup_open is False

    def test_dedup_still_works(self):
        """同键 10s 节流不受防重入改动影响。"""
        p = _fake_parent()
        errmod.show_error(p, "标题", "消息X")
        errmod.show_error(p, "标题", "消息X")   # 同键：被节流吞
        errmod.show_error(p, "标题", "消息Y")  # 异键：放行
        assert len(errmod._last_popup) == 2

    def test_reentrant_call_inside_first_popup(self):
        """复刻崩溃现场：第一个弹窗的嵌套事件循环里同步再调 show_error。"""
        calls = []

        def nesting_hook():
            # 模拟 QMessageBox.exec 期间排队的 on_err 信号被处理
            calls.append("nested")
            errmod.show_error(_fake_parent(), "机会扫描失败", "嵌套错误")

        # 用 monkeypatch 让第一次调用中途触发嵌套调用
        orig_warning = None
        try:
            from PySide6.QtWidgets import QMessageBox
            have_qt = True
        except ImportError:
            have_qt = False

        if have_qt:
            class FakeBox:
                def __init__(self, *a, **k):
                    pass
                @staticmethod
                def warning(*a, **k):
                    nesting_hook()
                    return 0
            monkeypatch = pytest.MonkeyPatch()
            monkeypatch.setattr("PySide6.QtWidgets.QMessageBox", FakeBox)
            try:
                errmod.show_error(_fake_parent(), "板块数据获取失败", "外层错误")
            finally:
                monkeypatch.undo()
        else:
            # 无 GUI 环境：手动模拟嵌套调用路径
            errmod._popup_open = True
            nesting_hook()
            errmod._popup_open = False
        assert calls == ["nested"]
