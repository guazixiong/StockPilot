"""日级事件驱动回测引擎（与监听共用策略定义，口径一致）。

成交假设：信号当日收盘命中 → 次日开盘买入；止损/止盈按触发价当日成交；
卖出规则收盘命中按收盘卖出；超过最大持有天数次日开盘卖出。
费用：买卖均收佣金 fee_rate，卖出另收印花税 stamp_tax；按 100 股整手。
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import indicators, strategy as stg
from .models import KLine, Trade

log = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    strategy_name: str = ""
    codes: List[str] = field(default_factory=list)
    days: int = 250
    cash: float = 100000.0
    fee_rate: float = 2.5e-4       # 佣金（双边）
    stamp_tax: float = 5e-4        # 印花税（卖出）
    transfer_fee: float = 1e-4     # 过户费（双边，沪市）v4.3
    min_commission: float = 5.0    # 单笔最低佣金（元）v4.3
    slippage_pct: float = 0.1     # 滑点%（成交价劣化）v4.3
    entry_batches: int = 1        # 分批建仓批数：1=一次性全仓（旧行为），3=信号次日+回踩 MA10+第3日 v4.3
    take_profit_pct: float = 12.0
    stop_loss_pct: float = 6.0
    max_hold_days: int = 20


@dataclass
class BacktestResult:
    strategy: str = ""
    start: str = ""
    end: str = ""
    initial_cash: float = 0.0
    final_value: float = 0.0
    total_return_pct: float = 0.0
    annual_return_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    trade_count: int = 0
    avg_hold_days: float = 0.0
    trades: List[Trade] = field(default_factory=list)
    equity: List[Tuple[str, float]] = field(default_factory=list)
    universe_size: int = 0

    def summary_text(self) -> str:
        return (
            f"策略: {self.strategy}\n"
            f"区间: {self.start} ~ {self.end}（股票数 {self.universe_size}）\n"
            f"初始资金: {self.initial_cash:,.0f} → 期末: {self.final_value:,.0f}\n"
            f"总收益: {self.total_return_pct:.2f}%   年化: {self.annual_return_pct:.2f}%\n"
            f"交易次数: {self.trade_count}   胜率: {self.win_rate:.1f}%   "
            f"盈亏比: {self.profit_factor:.2f}\n"
            f"最大回撤: {self.max_drawdown_pct:.2f}%   平均持有: {self.avg_hold_days:.1f} 天"
        )


def _buy_price_of(idx: int, klines: List[KLine]) -> Optional[Tuple[str, float]]:
    """信号出现在 idx 收盘 → 次日开盘买入。"""
    if idx + 1 >= len(klines):
        return None
    k = klines[idx + 1]
    if k.open <= 0:
        return None
    return k.date, k.open


def run_backtest_one(strategy: stg.Strategy, code: str, name: str,
                     klines: List[KLine], cfg: BacktestConfig,
                     cash: float) -> Tuple[float, List[Trade], List[Tuple[str, float]]]:
    """单只股票回测：返回 (期末权益, 交易列表, 权益曲线)。等权单股全仓。"""
    trades: List[Trade] = []
    equity: List[Tuple[str, float]] = []
    if len(klines) < 35:
        return cash, trades, equity

    closes = [k.close for k in klines]
    ind_series = {
        "ma5": indicators.sma(closes, 5),
        "ma10": indicators.sma(closes, 10),
        "ma20": indicators.sma(closes, 20),
        "ma60": indicators.sma(closes, 60),
    }
    dif, dea, hist = indicators.macd(closes)
    rsi6 = indicators.rsi(closes, 6)
    kv, dv, jv = indicators.kdj([k.high for k in klines],
                                [k.low for k in klines], closes)
    start_idx = 61  # 预热 60 根

    holding = False
    entry_price = entry_date = ""
    hold_days = 0
    shares = 0

    def snapshot(i: int) -> Dict[str, object]:
        """第 i 根K线对应的指标上下文（与 analyze 同口径的历史版本）。"""
        highs = [k.high for k in klines[:i + 1]]
        lows = [k.low for k in klines[:i + 1]]
        vols = [k.volume for k in klines[:i + 1]]
        ctx: Dict[str, object] = {
            "ma5": ind_series["ma5"][i], "ma10": ind_series["ma10"][i],
            "ma20": ind_series["ma20"][i], "ma60": ind_series["ma60"][i],
            "dif": dif[i], "dea": dea[i], "macd_hist": hist[i],
            "rsi6": rsi6[i], "k": kv[i], "d": dv[i], "j": jv[i],
            "prev_close": closes[i - 1] if i >= 1 else None,
            "prev_low": lows[-2] if len(lows) >= 2 else None,
        }
        for w in (10, 20):
            seg = highs[max(0, i - w):i]
            ctx[f"high{w}_prev"] = max(seg) if seg else None
        if i >= 6 and sum(vols[-6:-1]) > 0:
            ctx["vol_vs_ma5"] = vols[-1] / (sum(vols[-6:-1]) / 5)
        else:
            ctx["vol_vs_ma5"] = None
        m5, m10, m20, m60 = (ctx.get(f"ma{w}") for w in (5, 10, 20, 60))
        try:
            ctx["ma_bull"] = all(v is not None for v in (m5, m10, m20, m60)) \
                and m5 > m10 > m20 > m60
        except TypeError:
            ctx["ma_bull"] = False
        # 金叉：与前一根比较
        ctx["macd_golden"] = bool(
            i >= 1 and dif[i - 1] is not None and dea[i - 1] is not None
            and dif[i] is not None and dea[i] is not None
            and dif[i - 1] <= dea[i - 1] and dif[i] > dea[i])
        ctx["kdj_golden"] = bool(
            i >= 1 and kv[i - 1] is not None and dv[i - 1] is not None
            and kv[i] is not None and dv[i] is not None
            and kv[i - 1] <= dv[i - 1] and kv[i] > dv[i])
        # 快照字段（用当日 OHLC 近似 quote）
        k = klines[i]
        prev = closes[i - 1] if i >= 1 else k.open
        ctx["price"] = k.close
        ctx["change_pct"] = (k.close - prev) / prev * 100 if prev else None
        ctx["turnover_rate"] = k.turnover
        ctx["volume_ratio"] = ctx["vol_vs_ma5"]
        ctx["low"] = k.low
        ctx["high"] = k.high
        ctx["open"] = k.open
        ctx["ma20_rising"] = bool(i >= 4 and ind_series["ma20"][i]
                                  and ind_series["ma20"][i - 3]
                                  and ind_series["ma20"][i] > ind_series["ma20"][i - 3])
        # ---- v3.2：Sequoia 策略回测口径补齐（与 indicators.analyze 逐项一致）----
        k_i = klines[i]
        ctx["is_yang"] = k_i.close > k_i.open
        ctx["is_bear_today"] = k_i.close < k_i.open
        # 昨日涨停/跌停（主板 9.5%；回测不区分创科，保守主板口径）
        if i >= 2 and closes[i - 2]:
            ctx["prev_limit_up"] = closes[i - 1] >= closes[i - 2] * 1.095
            ctx["prev_limit_down"] = closes[i - 1] <= closes[i - 2] * 0.905
        else:
            ctx["prev_limit_up"] = ctx["prev_limit_down"] = False
        # 今量/昨量
        if i >= 1 and vols[i - 1]:
            ctx["vol_vs_prev"] = vols[i] / vols[i - 1]
        else:
            ctx["vol_vs_prev"] = None
        # 今量/20日均量（不含当日）
        if i >= 21 and sum(vols[i - 20:i]) > 0:
            ctx["vol_vs_ma20"] = vols[i] / (sum(vols[i - 20:i]) / 20)
        else:
            ctx["vol_vs_ma20"] = None
        # 昨日 MA5 / MA20（用截断序列）
        if i >= 25:
            prev5 = closes[max(0, i - 5):i]
            prev20 = closes[max(0, i - 20):i]
            ctx["prev_ma5"] = sum(prev5) / len(prev5) if len(prev5) == 5 else None
            ctx["prev_ma20"] = sum(prev20) / len(prev20) if len(prev20) == 20 else None
        else:
            ctx["prev_ma5"] = ctx["prev_ma20"] = None
        # 昨 MA20>MA60
        ctx["prev_ma20_gt_ma60"] = bool(
            i >= 60 and ctx["prev_ma20"] is not None
            and sum(closes[i - 60:i]) / 60 < ctx["prev_ma20"])
        # 40/10日振幅比 + 高位抗跌（高窄旗形）
        if i >= 40:
            h40 = max(highs[i - 40:i]); l40 = min(lows[i - 40:i])
            h10 = max(highs[i - 10:i]); l10 = min(lows[i - 10:i])
            ctx["amp40"] = (h40 / l40) if l40 > 0 else None
            ctx["amp10"] = (h10 / l10) if l10 > 0 else None
            ctx["high10_hold"] = bool(h40 > 0 and l10 >= h40 * 0.8)
        else:
            ctx["amp40"] = ctx["amp10"] = None
            ctx["high10_hold"] = False
        # 成交额（万元）——海龟 1 亿流动性
        ctx["amount"] = k_i.amount / 1e4 if k_i.amount else None
        return ctx

    # v4.3 分批建仓批内比例（entry_batches>=2 时生效）：信号日 T+1 首批50%，
    # T+2 回踩不破 MA10 补 30%，T+3 再补 20%；跌破止损线则停止补仓。
    batch_plan = None
    pending_shares = 0
    entry_avg = 0.0    # 加权平均建仓成本

    def _buy_lot(sh, price):
        """按价成交一手批次，返回费用（佣金取 min_commission、含过户费）。"""
        nonlocal cash
        fee = max(sh * price * cfg.fee_rate, cfg.min_commission)
        transfer = sh * price * cfg.transfer_fee
        cash -= sh * price + fee + transfer

    for i in range(start_idx, len(klines)):
        k = klines[i]
        if not holding:
            ctx = snapshot(i)
            hit, _ = stg.eval_rules(strategy.buy_rules, ctx)
            if hit:
                bp = _buy_price_of(i, klines)
                if bp:
                    entry_date, raw_price = bp
                    entry_price = raw_price * (1 + cfg.slippage_pct / 100)
                    invest = cash
                    total_shares = int(invest / (entry_price * 100)) * 100
                    if total_shares >= 100:
                        holding = True
                        hold_days = 0
                        if cfg.entry_batches >= 2:
                            batch_plan = [(0.5, 0), (0.3, 1), (0.2, 2)][
                                :cfg.entry_batches]
                            first = int(total_shares * batch_plan[0][0])
                            first = max(int(first / 100) * 100, 100)
                            shares = first
                            pending_shares = total_shares - first
                            _buy_lot(shares, entry_price)
                            entry_avg = entry_price
                        else:
                            shares = total_shares
                            _buy_lot(shares, entry_price)
                            entry_avg = entry_price
        elif pending_shares > 0:
            # v4.3 补仓日：止损线破位则放弃余仓；MA10 之上才补
            hold_days += 1
            if k.low <= entry_avg * (1 - cfg.stop_loss_pct / 100):
                pending_shares = 0   # 破止损：停止补仓（只持有已建部分）
            else:
                ma10 = ind_series["ma10"][i]
                step_days = [ratio for ratio, off in (batch_plan or []) if off == hold_days]
                if step_days and (ma10 is None or k.close >= ma10 * 0.98):
                    add = max(int(pending_shares / 100) * 100, 0)
                    if add >= 100:
                        add_px = k.close * (1 + cfg.slippage_pct / 100)
                        _buy_lot(add, add_px)
                        entry_avg = (entry_avg * shares + add_px * add) / (shares + add)
                        shares += add
                        pending_shares -= add
                    else:
                        pending_shares = 0
                elif hold_days > 3:
                    pending_shares = 0   # 3 日内未触发补仓则放弃
        else:
            hold_days += 1
            exit_price = None
            reason = ""
            # 盘中止损/止盈（按触发价）
            if k.low <= entry_avg * (1 - cfg.stop_loss_pct / 100):
                exit_price = entry_avg * (1 - cfg.stop_loss_pct / 100)
                reason = f"止损{cfg.stop_loss_pct}%"
            elif k.high >= entry_avg * (1 + cfg.take_profit_pct / 100):
                exit_price = entry_avg * (1 + cfg.take_profit_pct / 100)
                reason = f"止盈{cfg.take_profit_pct}%"
            else:
                ctx = snapshot(i)
                hit, hit_rules = stg.eval_rules(strategy.sell_rules, ctx)
                if hit:
                    exit_price = k.close
                    reason = "规则卖出:" + (";".join(hit_rules)[:40])
                elif hold_days >= cfg.max_hold_days:
                    bp = _buy_price_of(i, klines)
                    if bp:
                        exit_price = bp[1]
                        reason = f"超{cfg.max_hold_days}天"
            if exit_price:
                exit_price = exit_price * (1 - cfg.slippage_pct / 100)
                gross = shares * exit_price
                fee = max(gross * cfg.fee_rate, cfg.min_commission)
                tax = gross * cfg.stamp_tax
                transfer = gross * cfg.transfer_fee
                cash += gross - fee - tax - transfer
                pnl = (exit_price - entry_avg) / entry_avg * 100
                trades.append(Trade(
                    code=code, name=name, entry_date=entry_date,
                    entry_price=round(entry_avg, 3), exit_date=k.date,
                    exit_price=round(exit_price, 3), pnl_pct=round(pnl, 2),
                    hold_days=hold_days, reason=reason))
                holding = False
                shares = 0
                pending_shares = 0
                entry_avg = 0.0
        # 当日权益 = 现金 + 持仓市值
        market_value = shares * k.close if holding else 0
        equity.append((k.date, round(cash + market_value, 2)))

    return cash, trades, equity


def run_backtest(strategy: stg.Strategy, data: Dict[str, Tuple[str, List[KLine]]],
                 cfg: BacktestConfig,
                 on_progress: Optional[callable] = None) -> BacktestResult:
    """多股票回测。data: {code: (name, klines)}。"""
    per_cash = cfg.cash / max(len(data), 1)
    all_trades: List[Trade] = []
    curves: Dict[str, List[Tuple[str, float]]] = {}
    done = 0

    def _pack(item, code):
        """容错：裸 klines 列表自动包装为 (code, klines)；元组原样。"""
        if isinstance(item, tuple):
            nm, kls = item[0], item[1]
        else:
            nm, kls = code, item
        return nm, kls

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(run_backtest_one, strategy, code, *_pack(data[code], code),
                            cfg, per_cash): code
                for code in data}
        for fut, code in futs.items():
            try:
                final, trades, equity = fut.result()
            except Exception as exc:  # noqa: BLE001
                log.warning("回测失败 %s: %s", code, exc)
                final, trades, equity = per_cash, [], []
            all_trades.extend(trades)
            curves[code] = equity
            done += 1
            if on_progress:
                on_progress(f"回测进度 {done}/{len(data)}")

    all_trades.sort(key=lambda t: (t.exit_date, t.code))
    final_value = sum(
        (curve[-1][1] if curve else per_cash) for curve in curves.values())
    total_return = (final_value - cfg.cash) / cfg.cash * 100

    wins = [t for t in all_trades if t.pnl_pct > 0]
    losses = [t for t in all_trades if t.pnl_pct <= 0]
    win_rate = len(wins) / len(all_trades) * 100 if all_trades else 0.0
    gain = sum(t.pnl_pct for t in wins)
    loss = abs(sum(t.pnl_pct for t in losses))
    profit_factor = (gain / loss) if loss > 0 else (float("inf") if gain > 0 else 0.0)

    # 最大回撤 + 组合权益曲线：以最长曲线为基准对齐，短曲线尾部用其期末值补齐
    combined: List[Tuple[str, float]] = []
    dd = 0.0
    non_empty = [c for c in curves.values() if c]
    if non_empty:
        ref = max(non_empty, key=len)
        per_cash = cfg.cash / max(len(data), 1)
        peak = -1.0
        for i, (d, _) in enumerate(ref):
            val = sum((c[i][1] if i < len(c) else (c[-1][1] if c else per_cash))
                      for c in non_empty)
            combined.append((d, round(val, 2)))
            peak = max(peak, val)
            if peak > 0:
                dd = max(dd, (peak - val) / peak * 100)

    start = combined[0][0] if combined else ""
    end = combined[-1][0] if combined else ""
    days_n = max(len(combined), 1)
    annual = ((final_value / cfg.cash) ** (250.0 / days_n) - 1) * 100 \
        if final_value > 0 and days_n >= 30 else 0.0

    return BacktestResult(
        strategy=strategy.name, start=start, end=end,
        initial_cash=cfg.cash, final_value=round(final_value, 2),
        total_return_pct=round(total_return, 2),
        annual_return_pct=round(annual, 2),
        win_rate=round(win_rate, 1),
        profit_factor=round(profit_factor, 2) if profit_factor != float("inf") else 999.0,
        max_drawdown_pct=round(dd, 2),
        trade_count=len(all_trades),
        avg_hold_days=round(
            sum(t.hold_days for t in all_trades) / len(all_trades), 1)
        if all_trades else 0.0,
        trades=all_trades, universe_size=len(data),
        equity=combined,
    )
