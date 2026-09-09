"""盘中独立调度器（v6.3，对齐用户 v4.11.0 的 cron 运行时语义）。

用户系统的运行时（run_v4.3_scheduled.sh）：
- 交易时段内每 5 分钟由 cron 拉起一轮完整循环（snapshot→决策→信号→限流→推送）；
- flock 防重入；午休（11:30-13:00）/收盘/开盘前不跑；
- 持仓（holdings.json）永远在监控池内，即使不在自选。

StockPilot 桌面版对应实现：
- MonitorScheduler 挂在主窗（页面切走/持仓页关闭都不中断）；
- 交易时段（9:30-11:30 / 13:00-15:00）每 interval 秒一轮；
- 重入锁：上一轮 Worker 未完成不叠下一轮（flock 等价物）；
- 一轮 = 持仓 + 买入计划池（未持仓也监控）全量检查
  → 四层规则（日K风控/纪律卖出/计划执行/分时形态）→ DigestThrottle
  → **本地托盘弹窗+提示音必达**（webhook 可选增强）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, List

from PySide6.QtCore import QObject, QTimer

log = logging.getLogger(__name__)


def is_trading_hours(dt=None) -> bool:
    """A 股盘中：9:30-11:30 / 13:00-15:00（用户 sh 门禁同口径）。"""
    dt = dt or datetime.now()
    hm = dt.hour * 100 + dt.minute
    # 9:30-11:30 / 13:00-14:59（15:00 收盘即停——用户 sh 口径 hour>14 exit）
    return 930 <= hm <= 1130 or 1300 <= hm <= 1459


class MonitorScheduler(QObject):
    """主窗持有的盘中调度器：一轮 = 全池检查 + 即时通知。"""

    def __init__(self, ctx, interval_sec: int = 60, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.interval_sec = interval_sec
        self._in_flight = False
        self._running = False
        self._throttle = None
        self._timer = QTimer(self)
        self._timer.setInterval(interval_sec * 1000)
        self._timer.timeout.connect(self._tick)
        self.on_alerts: Callable = None     # 主窗回调（渲染+托盘+声音）

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._timer.start()
        log.info("盘中调度器启动（每 %ss 一轮，交易时段门禁生效）",
                 self.interval_sec)

    def stop(self) -> None:
        self._running = False
        self._timer.stop()

    def _tick(self) -> None:
        if self._in_flight or not is_trading_hours():
            return
        self._in_flight = True
        import stockpilot.ui.scheduler as _self_mod
        _self_mod.submit(self._scan_all, on_done=self._on_done,
                         on_err=self._on_err)

    # ------------------------------------------------------------ 一轮扫描
    def _scan_all(self) -> List:
        """后台：持仓 + 买入计划池 全量四层检查（用户 intraday 主循环等价物）。"""
        from ..core import (indicators, intraday, regime as rg,
                            watch as w_mod)
        positions = dict(self.ctx.cfg.positions)
        plans = {p.code: p for p in rg.load_buy_plans(self.ctx.cfg.monitor)}
        # 监控池 = 持仓 ∪ 计划池（用户语义：holdings 必监控，计划标的即时盯）
        pool = list(dict.fromkeys(list(positions.keys()) + list(plans.keys())))
        if not pool:
            return []
        quotes = self.ctx.get_quotes(pool)
        out = []
        for code in pool:
            q = quotes.get(code)
            if not q or not q.price:
                continue
            pos = positions.get(code)
            try:
                kls = self.ctx.kline_for_scan(code, 800)
                ind = indicators.analyze(kls)
            except Exception:  # noqa: BLE001
                ind = {}
            # 持仓层：日K风控 + 纪律卖出（用户优先级链）
            if pos:
                out.extend(w_mod.check_position(
                    code, q.name or code, pos, q, ind))
                sig = rg.sell_decision(float(pos.get("cost") or 0),
                                        q.price or 0.0, ind)
                if sig.level in ("RISK", "TAKE_PROFIT"):
                    out.append(w_mod.WatchAlert(
                        code=code, name=q.name or code,
                        kind=f"纪律·{sig.reason.split()[0]}",
                        level="danger" if sig.level == "RISK" else "chance",
                        title=sig.title,
                        detail=f"成本 {pos.get('cost')} 现价 {q.price}",
                        action=sig.reason, price=q.price or 0.0,
                        change_pct=q.change_pct or 0.0))
            # 计划层（未持仓也监控——用户 buyPlans 语义）
            pl = plans.get(code)
            if pl:
                psig = rg.classify_plan(pl, q.price, rg.trend_gate(ind, q.price))
                if psig.level == "BUY":
                    out.append(w_mod.WatchAlert(
                        code=code, name=q.name or code, kind="计划·BUY",
                        level="chance", title=psig.title,
                        detail=f"目标 ¥{pl.target_amount:,.0f}；{psig.reason}",
                        action="进入你的买入区间（仅提醒，人工执行）",
                        price=q.price or 0.0, change_pct=q.change_pct or 0.0))
            # 分时层（持仓标的优先，控制请求量）
            if pos:
                try:
                    ms = self.ctx.tencent.get_minute(code)
                    for sg in intraday.analyze_intraday(ms):
                        out.append(w_mod.WatchAlert(
                            code=code, name=q.name or code,
                            kind=f"分时·{sg.kind}", level=sg.level,
                            title=sg.title, detail=sg.detail,
                            action=sg.action, price=q.price or 0.0,
                            change_pct=q.change_pct or 0.0))
                except Exception:  # noqa: BLE001
                    pass
        return out

    def _on_done(self, alerts: List) -> None:
        self._in_flight = False
        if not alerts:
            return
        # 用户 v4.4 限流口径：同条30min/摘要≤8/时≤6/日≤30
        from ..core import regime as rg
        if self._throttle is None:
            self._throttle = rg.DigestThrottle()
        items = [{"key": f"{a.code}|{a.kind}|{a.title}",
                  "level": "RISK" if a.level == "danger" else "BUY",
                  "alert": a} for a in alerts]
        fresh = self._throttle.route(items)
        if not fresh:
            return
        alerts_fresh = [it["alert"] for it in fresh]
        if self.on_alerts:
            try:
                self.on_alerts(alerts_fresh)      # 主窗：托盘+声音+渲染
            except Exception as exc:  # noqa: BLE001
                log.warning("通知回调失败: %s", exc)

    def _on_err(self, msg: str) -> None:
        self._in_flight = False
        log.warning("盘中扫描失败: %s", msg)
