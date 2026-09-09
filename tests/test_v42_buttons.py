"""v4.2：按钮可点击性防回归（静态审计规则固化）。

规则：每个 QPushButton 变量创建后 ±40 行窗口内必须存在该变量的
clicked.connect（可切换按钮允许 toggled.connect）。捕获"按钮造了忘接槽"
这类静默失效（用户看到的是点击无反应）。
"""
import re
import pathlib

UI_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "stockpilot" / "ui"

# 特例白名单：特殊信号或装饰器连接
WHITELIST = {
    "view_btn",        # toggled.connect（可切换按钮）
    "close_btn",       # AiChatWindow 内部（chat.hide()）
    "send_btn",        # AiChatWindow returnPressed 之外的直连
}


def test_all_buttons_have_click_handler():
    issues = []
    for f in UI_DIR.rglob("*.py"):
        lines = f.read_text(encoding="utf-8").splitlines()
        for i, ln in enumerate(lines):
            m = re.match(r"\s*(?:self\.)?(\w+)\s*=\sQPushButton\(", ln)
            if not m:
                continue
            var = m.group(1)
            if var in WHITELIST:
                continue
            window = "\n".join(lines[max(0, i - 5):i + 40])
            has = (f"{var}.clicked.connect" in window
                   or f"{var}.toggled.connect" in window
                   or re.search(rf"clicked\.connect\(\s*{var}\.", window)
                   or (f"clicked.connect(lambda" in window
                       and f"{var}." in window))
            if not has:
                issues.append(f"{f.name}:{i + 1} {var}")
    assert not issues, f"存在无信号按钮（点击无效）: {issues}"


def test_monitor_scan_btn_actually_works():
    """曾经的真实死按钮回归闸：监听页立即扫描必须触发扫描。"""
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    from stockpilot.core.storage import Config
    from stockpilot.ui.context import AppContext
    from stockpilot.ui.pages.monitor import MonitorPage
    ctx = AppContext(Config())
    # 打桩：避免测试里发起真实全市场扫描（会拖慢且在解释器关闭后崩）。
    ctx.monitor.market_fetch = lambda max_count=2000: []
    page = MonitorPage(ctx)
    page._scanning = False
    st0 = page.scan_status.text()
    # 直接触发按钮的槽（不依赖信号机制，验证槽本身有效）
    page.scan_now()
    st1 = page.scan_status.text()
    assert st1 != st0, "立即扫描槽未生效"
    # 再验证按钮信号连接存在（源码级断言，防再出现忘 connect）
    import inspect
    from stockpilot.ui.pages import monitor as m
    src = inspect.getsource(m)
    assert 'scan_btn.clicked.connect(self.scan_now)' in src, \
        "监听页 scan_btn 缺少 clicked.connect（v4.2 修过的死按钮回归！）"
