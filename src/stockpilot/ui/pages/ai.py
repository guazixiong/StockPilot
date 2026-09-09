"""AI 分析页：OpenAI 协议厂商配置（预设+自定义）+ 流式多轮对话。"""
from __future__ import annotations

import html
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QSplitter, QTextBrowser, QVBoxLayout, QWidget)

from ...core.ai.client import AiConfig, OpenAIClient
from ...core.ai.vendors import CUSTOM, PRESETS, get_vendor, vendor_names
from ...core.prompt import build_messages
from .. import kit
from ..ai_stream import append_stream, finalize_stream
from ..workers import submit

TPL_ORDER = ["个股诊断", "信号解读", "回测解读", "选股解读", "新闻解读", "自由问答"]


class AiPage(QWidget):
    delta = Signal(str, str)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._context = ""
        self._history: list = []
        self._running = False
        self._stop = threading.Event()
        self.delta.connect(self._on_delta)
        self._build_ui()
        self._load_profiles()

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(11, 11, 11, 11)
        lay.setSpacing(8)
        lay.addWidget(kit.page_head(
            "AI 分析", "多轮对话 · 上下文注入 · 全场景解读", "✦ AI INSIGHT"))
        row = QHBoxLayout()
        lay.addLayout(row, 1)
        split = QSplitter(Qt.Horizontal)
        row.addWidget(split)

        # 左：AI 配置
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        box = QGroupBox("AI 服务配置（OpenAI 协议，支持自定义厂商）")
        grid = QGridLayout(box)
        grid.setVerticalSpacing(6)

        grid.addWidget(QLabel("配置名"), 0, 0)
        self.profile_combo = QComboBox()
        self.profile_combo.setEditable(True)
        grid.addWidget(self.profile_combo, 0, 1)
        save_btn = QPushButton("保存配置")
        save_btn.setProperty("success", True)
        save_btn.clicked.connect(self._save_profile)
        grid.addWidget(save_btn, 0, 2)
        from ..transfer import export_ai_config as _exp, import_ai_config as _imp
        from ..transfer import save_template as _tpl
        tpl_btn = QPushButton("模板")
        tpl_btn.setProperty("secondary", True)
        tpl_btn.setToolTip("下载 AI 配置导入模板（JSON，含字段说明与示例）")
        tpl_btn.clicked.connect(lambda: self._after_transfer(_tpl(self, "ai_config")))
        grid.addWidget(tpl_btn, 0, 3)
        exp_btn = QPushButton("导出")
        exp_btn.setProperty("secondary", True)
        exp_btn.clicked.connect(lambda: self._after_transfer(_exp(self, self.ctx.cfg)))
        imp_btn = QPushButton("导入")
        imp_btn.setProperty("secondary", True)
        imp_btn.clicked.connect(lambda: (self._after_transfer(_imp(self, self.ctx.cfg)),
                                         self._load_profiles()))
        grid.addWidget(exp_btn, 0, 3)
        grid.addWidget(imp_btn, 0, 4)
        del_btn = QPushButton("删除")
        del_btn.setProperty("danger", True)
        del_btn.clicked.connect(self._del_profile)
        grid.addWidget(del_btn, 0, 5)

        grid.addWidget(QLabel("厂商预设"), 1, 0)
        self.vendor_combo = QComboBox()
        self.vendor_combo.addItems(vendor_names())
        self.vendor_combo.currentTextChanged.connect(self._on_vendor_change)
        grid.addWidget(self.vendor_combo, 1, 1, 1, 3)

        grid.addWidget(QLabel("Base URL"), 2, 0)
        self.base_edit = QLineEdit()
        self.base_edit.setPlaceholderText("https://api.deepseek.com/v1")
        grid.addWidget(self.base_edit, 2, 1, 1, 3)

        grid.addWidget(QLabel("API Key"), 3, 0)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        grid.addWidget(self.key_edit, 3, 1, 1, 3)

        grid.addWidget(QLabel("模型"), 4, 0)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        grid.addWidget(self.model_combo, 4, 1)
        models_btn = QPushButton("获取模型列表")
        models_btn.setProperty("secondary", True)
        models_btn.clicked.connect(self._fetch_models)
        grid.addWidget(models_btn, 4, 2)

        grid.addWidget(QLabel("温度"), 5, 0)
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0, 2)
        self.temp_spin.setSingleStep(0.1)
        self.temp_spin.setValue(0.3)
        grid.addWidget(self.temp_spin, 5, 1)
        test_btn = QPushButton("测试连接")
        test_btn.clicked.connect(self._test_connection)
        grid.addWidget(test_btn, 5, 2)

        self.test_label = QLabel(" ")
        self.test_label.setProperty("hint", True)
        self.test_label.setWordWrap(True)
        grid.addWidget(self.test_label, 6, 0, 1, 4)
        ll.addWidget(box)
        ll.addStretch(1)
        split.addWidget(left)

        # 右：对话
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        # v7.1 §16 顶部模型状态条（当前模型 + 连接状态徽章）
        mbar = QHBoxLayout()
        self.model_status = QLabel("当前模型：未配置")
        self.model_status.setProperty("sub", True)
        mbar.addWidget(self.model_status)
        mbar.addStretch(1)
        rl.addLayout(mbar)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("分析模板"))
        self.tpl_combo = QComboBox()
        self.tpl_combo.addItems(TPL_ORDER)
        bar.addWidget(self.tpl_combo)
        bar.addStretch(1)
        clear_btn = QPushButton("清空会话")
        clear_btn.setProperty("secondary", True)
        clear_btn.clicked.connect(self._clear_chat)
        bar.addWidget(clear_btn)
        rl.addLayout(bar)

        self.context_label = QLabel("未注入上下文（可在行情/监听/回测页发起 AI 分析，或直接提问）")
        self.context_label.setProperty("hint", True)
        self.context_label.setWordWrap(True)
        rl.addWidget(self.context_label)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        rl.addWidget(self.view, 1)

        input_bar = QHBoxLayout()
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText(
            "输入问题（Ctrl+Enter 发送）。AI 分析仅供参考，不构成投资建议。")
        self.input.setMaximumHeight(90)
        self.input.keyPressEvent = self._input_keypress  # noqa: 屏蔽类型检查
        send_btn = QPushButton("发送")
        send_btn.clicked.connect(self.send)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setProperty("secondary", True)
        self.stop_btn.hide()
        self.stop_btn.clicked.connect(self._stop.set)
        input_bar.addWidget(self.input, 1)
        v = QVBoxLayout()
        v.addWidget(send_btn)
        v.addWidget(self.stop_btn)
        input_bar.addLayout(v)
        rl.addLayout(input_bar)
        split.addWidget(right)
        split.setSizes([360, 620])

    # ------------------------------------------------------------ 配置
    def _on_test_err(self, msg: str) -> None:
        from ..err import fail_hint, show_error
        show_error(self, "AI 连接测试失败", msg)
        fail_hint(self.test_label, "❌ 测试失败，详见弹窗")

    def _refresh_model_status(self) -> None:
        """顶部模型状态徽章刷新。"""
        try:
            ai = self.ctx.cfg.ai
            model = ai.get("model") or ""
            vendor = ai.get("vendor") or ""
            api_key = ai.get("api_key") or ""
            state = "● 已连接" if (model and api_key) else "○ 未配置"
            self.model_status.setText(
                f"当前模型：{model or '—'}"
                + (f"（{vendor}）" if vendor else "") + f"　{state}")
        except Exception:  # noqa: BLE001 状态条失败不影响主功能
            pass

    def _load_profiles(self) -> None:
        ai = self.ctx.cfg.ai
        profiles = ai.get("profiles") or {}
        self.profile_combo.clear()
        self.profile_combo.addItems(list(profiles.keys()) or ["默认"])
        active = ai.get("active") or (list(profiles.keys()) or ["默认"])[0]
        self.profile_combo.setCurrentText(active)
        prof = profiles.get(active) or {}
        self.vendor_combo.setCurrentText(prof.get("vendor") or PRESETS[0].name)
        self.base_edit.setText(prof.get("base_url") or PRESETS[0].base_url)
        self.key_edit.setText(prof.get("api_key") or "")
        self.model_combo.setCurrentText(prof.get("model") or "")
        self.temp_spin.setValue(float(prof.get("temperature") or 0.3))
        self._refresh_model_status()

    def _on_vendor_change(self, name: str) -> None:
        v = get_vendor(name)
        if v is None:
            self.base_edit.setPlaceholderText("https://your-openai-compatible/api/v1")
            return
        self.base_edit.setText(v.base_url)
        self.model_combo.clear()
        self.model_combo.addItems(list(v.models))
        if v.models:
            self.model_combo.setCurrentIndex(0)

    def _collect_config(self) -> AiConfig:
        return AiConfig(
            vendor=self.vendor_combo.currentText(),
            base_url=self.base_edit.text().strip(),
            api_key=self.key_edit.text().strip(),
            model=self.model_combo.currentText().strip(),
            temperature=float(self.temp_spin.value()))

    def _save_profile(self) -> None:
        name = self.profile_combo.currentText().strip() or "默认"
        cfg = self._collect_config()
        if not cfg.base_url or not cfg.model:
            QMessageBox.warning(self, "提示", "Base URL 与模型名不能为空")
            return
        ai = self.ctx.cfg.ai
        ai.setdefault("profiles", {})[name] = cfg.to_dict()
        ai["active"] = name
        self.ctx.cfg.save()
        self._load_profiles()
        self.test_label.setText(f"已保存配置「{name}」")

    def _after_transfer(self, result) -> None:
        ok, msg = result
        (QMessageBox.information if ok else QMessageBox.warning)(self, "导入导出", msg)

    def _del_profile(self) -> None:
        name = self.profile_combo.currentText().strip()
        profiles = self.ctx.cfg.ai.get("profiles") or {}
        if name not in profiles:
            return
        del profiles[name]
        self.ctx.cfg.ai["active"] = next(iter(profiles), "")
        self.ctx.cfg.save()
        self._load_profiles()

    def _fetch_models(self) -> None:
        try:
            client = OpenAIClient(self._collect_config(),
                                  proxy=(self.ctx.cfg.get("market") or {}).get("proxy") or "")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "提示", str(exc))
            return
        self.test_label.setText("正在获取模型列表…")
        submit(client.list_models, on_done=self._on_models,
               on_err=self._on_models_err)

    def _on_models(self, models: list) -> None:
        self.model_combo.clear()
        self.model_combo.addItems(models or [])
        if not models:
            self.test_label.setText("模型列表为空（部分服务不支持 /models，可手动输入模型名）")
        else:
            self.test_label.setText(f"获取到 {len(models)} 个模型")

    def _on_models_err(self, msg: str) -> None:
        from ..err import fail_hint, show_error
        show_error(self, "AI 接口测试", msg)
        fail_hint(self.test_label, "❌ 测试失败，详见弹窗")

    def _test_connection(self) -> None:
        cfg = self._collect_config()
        if not cfg.base_url or not cfg.model:
            QMessageBox.warning(self, "提示", "请先填写 Base URL 与模型名")
            return
        client = OpenAIClient(cfg, proxy=(self.ctx.cfg.get("market") or {}).get("proxy") or "")
        self.test_label.setText("测试中…")
        submit(client.test_connection, on_done=self._on_test_done,
               on_err=self._on_test_err)

    def _on_test_done(self, result) -> None:
        ok, msg = result
        self.test_label.setText(("✅ " if ok else "❌ ") + msg)

    # ------------------------------------------------------------ 对话
    def set_context(self, context: str, title: str = "") -> None:
        self._context = context
        show = title or "已注入上下文"
        self.context_label.setText(
            f"📌 {show}（上下文已就绪，{len(context)} 字符）\n"
            + html.escape(context[:200]) + "…")
        for key in ("个股诊断", "信号解读", "回测解读", "新闻解读", "选股解读"):
            if show.startswith(key):
                self.tpl_combo.setCurrentText(key)
                break

    def _clear_context(self) -> None:
        self._context = ""
        self.context_label.setText("未注入上下文")

    def _clear_chat(self) -> None:
        self._history = []
        self.view.clear()
        self._clear_context()

    def _input_keypress(self, ev) -> None:
        from PySide6.QtGui import QKeySequence
        from PySide6.QtCore import Qt as _Qt
        if ev.key() in (_Qt.Key_Return, _Qt.Key_Enter) and ev.modifiers() & _Qt.ControlModifier:
            self.send()
            return
        QPlainTextEdit.keyPressEvent(self.input, ev)

    def send(self) -> None:
        if self._running:
            return
        question = self.input.toPlainText().strip()
        if not question and not self._context:
            return
        try:
            client = self.ctx.ai_client()
        except RuntimeError as exc:
            QMessageBox.warning(self, "AI 未配置", str(exc))
            return
        messages = build_messages(self.tpl_combo.currentText(),
                                  self._context, question)
        full = [messages[0]] + self._history + [messages[-1]]
        self._history = list(full)
        append_stream(self.view, "user", question or "（按模板分析）")
        append_stream(self.view, "ai_head", "")
        self._running = True
        self._stop.clear()
        self.stop_btn.show()
        self.input.clear()
        submit(self._do_send, client, full, on_done=self._on_send_done,
               on_err=self._on_send_err)

    def _do_send(self, client, messages):
        return client.chat(messages, stream=True,
                           on_delta=lambda k, t: self.delta.emit(k, t),
                           stop_event=self._stop)

    def _on_delta(self, kind: str, text: str) -> None:
        append_stream(self.view, kind, text)

    def _on_send_done(self, reply: str) -> None:
        self._running = False
        self.stop_btn.hide()
        append_stream(self.view, "content", "<br>")
        append_stream(self.view, "disclaimer", "", raw=True)
        finalize_stream(self.view)
        if reply:
            self._history.append({"role": "assistant", "content": reply})
        # 多轮截断：保留最近 12 条（不含 system）
        body = self._history[1:]
        if len(body) > 12:
            self._history = [self._history[0]] + body[-12:]

    def _on_send_err(self, msg: str) -> None:
        self._running = False
        self.stop_btn.hide()
        append_stream(self.view, "error", str(msg))
        append_stream(self.view, "system",
                      "可稍后重新发送；若持续失败，请在本页「测试连接」或更换模型。",
                      raw=True)
