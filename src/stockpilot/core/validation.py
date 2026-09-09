"""选股验证报告：全策略历史回测横评排名。

回答股民最关心的问题——"21 套策略里哪套历史上真的赚钱？"
对给定股票池并行回测全部内置策略，输出策略×胜率×盈亏比排名表。
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import strategy as stg
from .backtest import BacktestConfig, run_backtest
from .models import KLine

log = logging.getLogger(__name__)


@dataclass
class StrategyScore:
    """单策略验证评分。profit_factor=总盈利/总亏损（0 亏损为 inf→999）。"""

    name: str
    trades: int = 0
    wins: int = 0
    win_rate: float = 0.0
    total_return_pct: float = 0.0
    avg_pnl_pct: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_hold: float = 0.0
    rank: int = 0
    score: float = 0.0


@dataclass
class ValidationReport:
    strategies: List[StrategyScore] = field(default_factory=list)
    codes: int = 0
    days: int = 0
    cash: float = 0.0

    def rank_lines(self) -> List[str]:
        head = (f"{'排名':<4}{'策略':<14}{'交易':<5}{'胜率':<7}"
                f"{'总收益%':<9}{'均盈亏%':<8}{'盈亏比':<7}{'回撤%':<7}{'综合分'}")
        rows = [head, "-" * len(head)]
        for s in self.strategies:
            rows.append(
                f"{s.rank:<6}{s.name:<14}{s.trades:<7}{s.win_rate:<9.1f}"
                f"{s.total_return_pct:<+11.2f}{s.avg_pnl_pct:<+9.2f}"
                f"{s.profit_factor:<9.2f}{s.max_drawdown_pct:<9.2f}{s.score:.1f}")
        return rows

    def summary(self) -> str:
        best = self.strategies[0] if self.strategies else None
        if not best:
            return "（无回测数据）"
        return (f"验证范围：{self.codes} 只股票 × {self.days} 交易日 × "
                f"初始 {self.cash:,.0f} 元\n"
                f"最佳策略：{best.name}（综合分 {best.score:.1f}）"
                f"—— 胜率 {best.win_rate:.0f}%、盈亏比 {best.profit_factor:.2f}、"
                f"总收益 {best.total_return_pct:+.2f}%、最大回撤 {best.max_drawdown_pct:.2f}%")


def _composite_score(s: StrategyScore) -> float:
    """综合分：胜率40% + 盈亏比30% + 收益20% - 回撤10%（各归一后加权）。"""
    win_n = s.win_rate / 100
    pf_n = min(s.profit_factor / 3.0, 1.0)          # 盈亏比 3 视为满分
    ret_n = max(min(s.total_return_pct / 50, 1.0), -1.0)  # ±50% 封顶
    dd_n = min(s.max_drawdown_pct / 30, 1.0)
    trade_n = min(s.trades / 10, 1.0)               # 不足10笔样本可信度折半
    score = (40 * win_n + 30 * pf_n + 20 * ret_n - 10 * dd_n) * (0.5 + 0.5 * trade_n)
    return round(max(score, 0.0), 1)


def run_validation(data: Dict[str, List[KLine]],
                   cfg: Optional[BacktestConfig] = None,
                   strategies: Optional[List[stg.Strategy]] = None,
                   workers: int = 8) -> ValidationReport:
    """全策略横评。data: {code: klines}。默认跑全部内置 21 套。"""
    cfg = cfg or BacktestConfig(days=250, cash=100000.0)
    strategies = strategies or stg.builtin_strategies()
    report = ValidationReport(codes=len(data), days=cfg.days, cash=cfg.cash)

    def one(s: stg.Strategy) -> StrategyScore:
        try:
            res = run_backtest(s, data, cfg)
        except Exception as exc:  # noqa: BLE001
            log.warning("验证回测失败 %s: %s", s.name, exc)
            return StrategyScore(name=s.name)
        trades = res.trades
        wins = [t for t in trades if t.pnl_pct > 0]
        gain = sum(t.pnl_pct for t in wins)
        loss = abs(sum(t.pnl_pct for t in trades if t.pnl_pct <= 0))
        ss = StrategyScore(
            name=s.name,
            trades=len(trades),
            wins=len(wins),
            win_rate=round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
            total_return_pct=round(
                (res.final_value / cfg.cash - 1) * 100, 2),
            avg_pnl_pct=round(
                sum(t.pnl_pct for t in trades) / len(trades), 2) if trades else 0.0,
            profit_factor=round(gain / loss, 2) if loss > 0
                else (999.0 if gain > 0 else 0.0),
            max_drawdown_pct=res.max_drawdown_pct,
            avg_hold=res.avg_hold_days,
        )
        ss.score = _composite_score(ss)
        return ss

    with ThreadPoolExecutor(max_workers=workers) as pool:
        scores = list(pool.map(one, strategies))

    scores.sort(key=lambda s: s.score, reverse=True)
    for i, s in enumerate(scores, 1):
        s.rank = i
    report.strategies = scores
    return report
