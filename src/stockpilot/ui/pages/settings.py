"""设置页：行情参数 / 主题 / 代理 / 消息通知（飞书·钉钉·企微）。"""
from __future__ import annotations

import webbrowser

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)

from ...core import notify as notify_mod
from .. import kit
from ...core.storage import data_dir
from ..workers import submit


class SettingsPage(QWidget):
    settings_changed = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._build_ui()
        self._load()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(11, 11, 11, 11)
        lay.setSpacing(10)
        lay.addWidget(kit.page_head(
            "设置", "行情 · 通知 · 常驻 · 机会推荐", "⚙ SETTINGS"))

        # v7.1 §17 分类导航条：点击锚点按钮滚动到对应分组
        # （保留全部分组于一页便于"保存设置"统一提交，导航只做快速定位）
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        nav_bar = QHBoxLayout()
        nav_bar.setSpacing(8)
        self._group_targets: dict = {}
        for label, group in (
                ("行情与界面", "行情与界面"),
                ("消息通知", "消息通知"),
                ("应用", "常驻与提醒"),
                ("机会推荐", "机会推荐")):
            b = QPushButton(label)
            b.setProperty("secondary", True)
            b.setFixedHeight(30)
            b.clicked.connect(
                lambda _=False, g=group: self._scroll_to(g))
            nav_bar.addWidget(b)
        nav_bar.addStretch(1)
        lay.addLayout(nav_bar)
        mkt = QGroupBox("行情与界面")
        g1 = QGridLayout(mkt)
        g1.addWidget(QLabel("行情刷新间隔(秒)"), 0, 0)
        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(3, 300)
        g1.addWidget(self.refresh_spin, 0, 1)
        g1.addWidget(QLabel("K线根数"), 0, 2)
        self.kline_spin = QSpinBox()
        self.kline_spin.setRange(60, 800)
        g1.addWidget(self.kline_spin, 0, 3)
        g1.addWidget(QLabel("主题"), 1, 0)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["dark", "light"])
        g1.addWidget(self.theme_combo, 1, 1)
        g1.addWidget(QLabel("HTTP代理(可选)"), 1, 2)
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("http://127.0.0.1:7890（留空直连）")
        g1.addWidget(self.proxy_edit, 1, 3)
        content = QWidget()
        clay = QVBoxLayout(content)
        clay.setSpacing(12)
        self._group_box_map = {}
        clay.addWidget(mkt)
        self._group_box_map['行情与界面'] = mkt

        notify_box = QGroupBox("消息通知（出现买入/卖出机会时推送到群机器人）")
        g2 = QGridLayout(notify_box)
        g2.addWidget(QLabel("飞书 webhook"), 0, 0)
        self.feishu_edit = QLineEdit()
        self.feishu_edit.setPlaceholderText("https://open.feishu.cn/open-apis/bot/v2/hook/xxxx")
        g2.addWidget(self.feishu_edit, 0, 1, 1, 3)
        g2.addWidget(QLabel("钉钉 webhook"), 1, 0)
        self.ding_edit = QLineEdit()
        self.ding_edit.setPlaceholderText("https://oapi.dingtalk.com/robot/send?access_token=xxxx")
        g2.addWidget(self.ding_edit, 1, 1, 1, 3)
        g2.addWidget(QLabel("钉钉加签密钥"), 2, 0)
        self.ding_secret_edit = QLineEdit()
        self.ding_secret_edit.setPlaceholderText("SEC 开头的加签密钥（安全设置选「加签」时必填）")
        g2.addWidget(self.ding_secret_edit, 2, 1, 1, 3)
        g2.addWidget(QLabel("企微 webhook"), 3, 0)
        self.wecom_edit = QLineEdit()
        self.wecom_edit.setPlaceholderText("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx")
        g2.addWidget(self.wecom_edit, 3, 1, 1, 3)
        g2.addWidget(QLabel("推送范围"), 4, 0)
        self.scope_combo = QComboBox()
        self.scope_combo.addItems(["buy_sell", "buy"])
        g2.addWidget(self.scope_combo, 4, 1)
        self.notify_check = QCheckBox("启用推送")
        g2.addWidget(self.notify_check, 4, 2)
        test_btn = QPushButton("发送测试消息")
        test_btn.setProperty("secondary", True)
        test_btn.clicked.connect(self._test_notify)
        g2.addWidget(test_btn, 4, 3)
        warn = QLabel("⚠ webhook 地址等同密钥，请勿外传；钉钉机器人需在安全设置中配置关键词「股票机会」或加签。")
        warn.setProperty("hint", True)
        warn.setWordWrap(True)
        g2.addWidget(warn, 5, 0, 1, 4)
        clay.addWidget(notify_box)
        self._group_box_map['消息通知'] = notify_box

        app_box = QGroupBox("常驻与提醒")
        g3 = QGridLayout(app_box)
        self.tray_check = QCheckBox("关闭时最小化到托盘（监听继续运行）")
        self.sound_check = QCheckBox("发现新信号时播放提示音")
        self.tray_notify_check = QCheckBox("发现新信号时弹出托盘通知")
        g3.addWidget(self.tray_check, 0, 0)
        g3.addWidget(self.tray_notify_check, 0, 1)
        g3.addWidget(self.sound_check, 1, 0)
        clay.addWidget(app_box)
        self._group_box_map['常驻与提醒'] = app_box

        # ---- 机会推荐配置卡 ----
        opp_box = QGroupBox("机会推荐（全链路生效：首页/雷达/盘后日报）")
        og = QGridLayout(opp_box)
        og.addWidget(QLabel("展示门槛分(0=不过滤)"), 0, 0)
        self.opp_min_score = QDoubleSpinBox()
        self.opp_min_score.setRange(0, 95)
        self.opp_min_score.setDecimals(0)
        self.opp_min_score.setSpecialValueText("不过滤")
        og.addWidget(self.opp_min_score, 0, 1)
        og.addWidget(QLabel("流动性门槛(成交额,万)"), 0, 2)
        self.opp_min_amount = QDoubleSpinBox()
        self.opp_min_amount.setRange(0, 100000)
        self.opp_min_amount.setDecimals(0)
        self.opp_min_amount.setSpecialValueText("不限")
        og.addWidget(self.opp_min_amount, 0, 3)
        self.opp_ma20_check = QCheckBox("须站上MA20（过滤博反弹噪声）")
        og.addWidget(self.opp_ma20_check, 1, 0, 1, 2)
        hint_w = QLabel("评分权重：风险 越高越严 / 趋势、量能、盈亏比 越高越敏感")
        hint_w.setProperty("hint", True)
        og.addWidget(hint_w, 2, 0, 1, 4)
        og.addWidget(QLabel("风险权重"), 3, 0)
        self.w_risk = QDoubleSpinBox()
        self.w_risk.setRange(0, 2); self.w_risk.setSingleStep(0.05)
        og.addWidget(self.w_risk, 3, 1)
        og.addWidget(QLabel("趋势权重"), 3, 2)
        self.w_trend = QDoubleSpinBox()
        self.w_trend.setRange(0, 1); self.w_trend.setSingleStep(0.02)
        og.addWidget(self.w_trend, 3, 3)
        og.addWidget(QLabel("量能权重"), 4, 0)
        self.w_volume = QDoubleSpinBox()
        self.w_volume.setRange(0, 1); self.w_volume.setSingleStep(0.02)
        og.addWidget(self.w_volume, 4, 1)
        og.addWidget(QLabel("盈亏比权重"), 4, 2)
        self.w_rr = QDoubleSpinBox()
        self.w_rr.setRange(0, 1); self.w_rr.setSingleStep(0.02)
        og.addWidget(self.w_rr, 4, 3)
        og.addWidget(QLabel("主力资金权重"), 5, 0)
        self.w_money = QDoubleSpinBox()
        self.w_money.setRange(0, 1); self.w_money.setSingleStep(0.02)
        og.addWidget(self.w_money, 5, 1)
        stg_hint = QLabel("参与策略集在「机会雷达」页勾选（勾选即保存并同步首页）")
        stg_hint.setProperty("hint", True)
        og.addWidget(stg_hint, 6, 0, 1, 4)
        clay.addWidget(opp_box)
        self._group_box_map['机会推荐'] = opp_box

        bar = QHBoxLayout()
        save_btn = QPushButton("保存设置")
        save_btn.clicked.connect(self._save)
        open_btn = QPushButton("打开数据目录")
        open_btn.setProperty("secondary", True)
        open_btn.clicked.connect(lambda: webbrowser.open(str(data_dir())))
        bar.addWidget(save_btn)
        bar.addWidget(open_btn)
        bar.addStretch(1)
        clay.addLayout(bar)

        tip = QLabel("所有功能以辅助盈利决策为目标；AI 与策略信号均为研究参考，不构成投资建议；系统不支持自动下单。")
        tip.setProperty("hint", True)
        clay.addWidget(tip)
        clay.addStretch(1)
        self._scroll.setWidget(content)
        lay.addWidget(self._scroll, 1)

    def _scroll_to(self, group: str) -> None:
        """分类导航：滚动到对应分组（§17 分类定位）。"""
        w = self._group_box_map.get(group)
        if w is None or self._scroll is None:
            return
        y = 0
        node = w
        while node is not None and node is not self._scroll.widget():
            y += node.y()
            node = node.parentWidget()
        sb = self._scroll.verticalScrollBar()
        sb.setValue(max(0, y - 8))

    def _load(self) -> None:
        mkt = self.ctx.cfg.get("market") or {}
        self.refresh_spin.setValue(int(mkt.get("refresh_sec") or 5))
        self.kline_spin.setValue(int(mkt.get("kline_limit") or 180))
        self.theme_combo.setCurrentText(mkt.get("theme") or "dark")
        self.proxy_edit.setText(mkt.get("proxy") or "")
        app = self.ctx.cfg.app
        self.tray_check.setChecked(bool(app.get("min_to_tray")))
        self.sound_check.setChecked(bool(app.get("sound")))
        self.tray_notify_check.setChecked(bool(app.get("tray_notify")))
        opp = self.ctx.cfg.opportunity
        self.opp_min_score.setValue(float(opp.get("min_score") or 0))
        self.opp_min_amount.setValue(float(opp.get("min_amount_wan") or 0))
        self.opp_ma20_check.setChecked(bool(opp.get("require_above_ma20")))
        w = opp.get("weights") or {}
        self.w_risk.setValue(float(w.get("risk", 0.5)))
        self.w_trend.setValue(float(w.get("trend", 0.12)))
        self.w_volume.setValue(float(w.get("volume", 0.08)))
        self.w_rr.setValue(float(w.get("rr", 0.10)))
        self.w_money.setValue(float(w.get("money", 0.06)))
        n = self.ctx.cfg.notify
        self.feishu_edit.setText(n.get("feishu_webhook") or "")
        self.ding_edit.setText(n.get("ding_webhook") or "")
        self.ding_secret_edit.setText(n.get("ding_secret") or "")
        self.wecom_edit.setText(n.get("wecom_webhook") or "")
        self.scope_combo.setCurrentText(n.get("scope") or "buy_sell")
        self.notify_check.setChecked(bool(n.get("enabled")))

    def _save(self) -> None:
        self.ctx.cfg.set("market", {
            "refresh_sec": self.refresh_spin.value(),
            "kline_limit": self.kline_spin.value(),
            "theme": self.theme_combo.currentText(),
            "proxy": self.proxy_edit.text().strip(),
        })
        self.ctx.cfg.set("notify", {
            "feishu_webhook": self.feishu_edit.text().strip(),
            "ding_webhook": self.ding_edit.text().strip(),
            "ding_secret": self.ding_secret_edit.text().strip(),
            "wecom_webhook": self.wecom_edit.text().strip(),
            "scope": self.scope_combo.currentText(),
            "enabled": self.notify_check.isChecked(),
        })
        self.ctx.cfg.set("opportunity", {
            "strategies": self.ctx.cfg.opportunity.get("strategies") or [],
            "min_score": int(self.opp_min_score.value()),
            "min_amount_wan": int(self.opp_min_amount.value()),
            "require_above_ma20": self.opp_ma20_check.isChecked(),
            "weights": {"risk": self.w_risk.value(),
                        "trend": self.w_trend.value(),
                        "volume": self.w_volume.value(),
                        "rr": self.w_rr.value(),
                        "money": self.w_money.value()},
        })
        self.ctx.cfg.set("app", {
            "min_to_tray": self.tray_check.isChecked(),
            "sound": self.sound_check.isChecked(),
            "tray_notify": self.tray_notify_check.isChecked(),
        })
        self.ctx.cfg.save()
        self.settings_changed.emit()
        QMessageBox.information(self, "设置", "已保存")

    def _test_notify(self, *_):
        cfg = {
            "feishu_webhook": self.feishu_edit.text().strip(),
            "ding_webhook": self.ding_edit.text().strip(),
            "ding_secret": self.ding_secret_edit.text().strip(),
            "wecom_webhook": self.wecom_edit.text().strip(),
        }
        submit(notify_mod.test_notify, cfg, on_done=self._on_notify_test,
               on_err=lambda m: QMessageBox.warning(self, "测试失败", m))

    def _on_notify_test(self, result) -> None:
        ok, detail = result
        if ok:
            QMessageBox.information(self, "通知测试", f"测试消息已发送：\n{detail}")
        else:
            QMessageBox.warning(self, "通知测试", f"发送失败：\n{detail}")
