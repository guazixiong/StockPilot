"""v7.2.6 回归闸：多分辨率等比例适配。

用户反馈：2560×1440 显示正常、1920×1080 显示异常。根因（几何审计）：
两列流下列宽 423px，行2 固定元素（走势 60 + 按钮组 132 + 现价/风险列）
约 330~380px，价位条被压到 11px 且 paintEvent 三个 130px 标签互叠。

修复设计（等比例）：
  1. _MIN_COL_W 280→390：列宽保底 = 行2 固定 330 + 价位条 60
  2. SignalCard._apply_compact：卡宽 <470 折叠走势/盈亏比（收 tooltip）
  3. PriceRangeBar 分级退化：<400 紧凑三段『损/现/目』；<130 只画现价
  4. MainWindow.setMinimumSize 1440×850→1200×700（1366×768 可用区 728px）
  5. restoreGeometry 后屏内钳制（2560 机器的几何搬到 1920 屏不超界）
"""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402
from PySide6.QtCore import QSize  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _mk(i, score):
    from stockpilot.core.models import TradeSignal
    return TradeSignal(code=f"60000{i}", name=f"股{i}", strategy="放量突破",
                       side="buy", time="09:31", op_score=score, price=10 + i,
                       stop_price=9.0, target_price=12.0, hit_rules=["r"],
                       risk_notes=["n"], grade="B", risk_score=40)


def test_min_col_w_covers_row2_fixed_demand(qapp):
    """列宽保底 ≥ 行2 固定元素（330px）+ 价位条保底（60px）。"""
    from stockpilot.ui.signal_card import OpportunityFlow
    assert OpportunityFlow._MIN_COL_W >= 390, (
        "列宽保底不足：行2 固定 ~330px + 价位条 60px 保底，"
        "低于 390 会出现 1920×1080 实拍的价格条 11px 互叠")


def test_1920_dual_column_no_overflow(qapp):
    """1920×1080 中栏（~820px 视口）：双列 + 价位条 ≥60px + 按钮无溢出。"""
    from stockpilot.ui.signal_card import OpportunityFlow, SignalCard
    flow = OpportunityFlow()
    flow.resize(QSize(820, 500))
    flow.show(); qapp.processEvents()
    flow.set_signals([_mk(i, 100 - i) for i in range(6)])
    qapp.processEvents()
    assert flow._columns() == 2, "820px 视口应保持双列（等比例适配）"
    c = flow._inner.findChildren(SignalCard)[0]
    assert c.range_bar.width() >= 60, "价位条保底 60px（色条模式）"
    for b in c.findChildren(QPushButton):
        assert b.geometry().right() <= c.width() - 2, f"按钮 {b.text()} 溢出卡片"
    flow.hide()


def test_2560_full_elements(qapp):
    """2560×1440 两列卡（~559px）：全元素可见（走势+盈亏比）。"""
    from stockpilot.ui.signal_card import OpportunityFlow, SignalCard
    flow = OpportunityFlow()
    flow.resize(QSize(1135, 500))
    flow.show(); qapp.processEvents()
    flow.set_signals([_mk(i, 100 - i) for i in range(6)])
    qapp.processEvents()
    c = flow._inner.findChildren(SignalCard)[0]
    assert c.width() >= 540
    assert c.spark.isVisible() and c.rr_label.isVisible(), (
        "宽卡应展示迷你走势与盈亏比（2560 基准不回退）")
    assert c.range_bar.width() >= 140
    flow.hide()


def test_compact_folds_and_tooltip_keeps_rr(qapp):
    """窄卡折叠走势/盈亏比，但盈亏比进 tooltip（信息不丢失）。"""
    from stockpilot.ui.signal_card import OpportunityFlow, SignalCard
    flow = OpportunityFlow()
    flow.resize(QSize(460, 500))
    flow.show(); qapp.processEvents()
    flow.set_signals([_mk(0, 90)])
    qapp.processEvents()
    c = flow._inner.findChildren(SignalCard)[0]
    assert not c.spark.isVisible() and not c.rr_label.isVisible()
    assert "盈亏比" in c.toolTip(), "折叠后盈亏比必须收进 tooltip"
    flow.hide()


def test_mainwindow_min_size_fits_small_laptops():
    """最小窗 ≤ 1366×768 可用区（728px 高）：去任务栏后可完整显示。"""
    from stockpilot.ui.app import MainWindow
    src_min = 1200, 700
    # 只验证常量（构造 MainWindow 在测试里成本高且需真实 AppContext）
    import inspect
    src = inspect.getsource(MainWindow.__init__)
    assert "setMinimumSize(1200, 700)" in src, (
        "最小窗口尺寸应 ≤1200×700：1440×850 在 1366×768 笔记本上"
        "超出可用区，底部被系统裁剪")
