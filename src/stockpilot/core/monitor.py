"""全市场策略监听服务。

纯 Python（无 Qt 依赖，可单测）：UI 层用 QTimer/Worker 驱动 scan_once。
流程：全市场快照粗筛 → 候选股拉日K算指标 → 策略判定 → 去重 → 新信号。
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable, Dict, List, Optional

from . import indicators, opportunity, strategy as stg
from .core_lru import analyze_cached
from .models import KLine, Quote, TradeSignal
from .storage import Config

log = logging.getLogger(__name__)

# 粗筛后最多对多少只做技术判定（控制网络量）
MAX_TECH_CHECK = 150


def run_strategy_scan(strategies: List[stg.Strategy], universe_rows: List[dict],
                      kline_fetch: Callable, on_progress: Optional[Callable[[str], None]] = None,
                      max_candidates: int = MAX_TECH_CHECK,
                      now: Optional[str] = None,
                      quote_lookup: Optional[Callable] = None,
                      opp_cfg: Optional[dict] = None,
                      merge_same_code: bool = True) -> List[TradeSignal]:
    """对给定股票集合执行策略买入规则（监听与选股共用的执行核心）。

    opp_cfg: config["opportunity"]（策略名单过滤由调用方完成；本函数消费
    weights/min_score/require_above_ma20/min_amount_wan）。
    merge_same_code: 同股多策略命中合并为一张卡（策略徽章聚合，分取最高，
    止损取高/目标取低=最保守）。
    返回按机会分降序的信号列表。

    v4.4.3 三项机会分析优化：
    - 去重：universe 内同 code 多行（多源快照重复）只分析一次；
    - 指标缓存：indicators.analyze 按 (code, 最新K线日期) 进程级缓存——
      同轮多策略共享一次计算，跨轮（当日K线未变）零重算；
    - 并行分析：分析阶段与 K 线获取解耦，workers=12（I/O 与 CPU 混合负载）。
    """
    now = now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 去重：同 code 保留首行（快照行内容等价，先到先得）
    seen_codes: set = set()
    candidates = []
    for r in MonitorService.prefilter(universe_rows, max_candidates):
        c = r.get("code")
        if c in seen_codes:
            continue
        seen_codes.add(c)
        candidates.append(r)
    if on_progress:
        on_progress(f"粗筛候选 {len(candidates)} 只（去重后），开始技术判定…")

    def check_row(row: dict) -> List[TradeSignal]:
        code = row["code"]
        try:
            klines = kline_fetch(code, "day", 800)
        except Exception as exc:  # noqa: BLE001
            log.debug("K线获取失败 %s: %s", code, exc)
            return []
        if len(klines) < 30:
            return []
        quote = Quote.from_mapping(code, row)
        ind = analyze_cached(code, klines)   # 当日K线未变 → 跨轮零重算
        # 趋势过滤开关：须站上 MA20（砍博反弹噪声）
        if opp_cfg and opp_cfg.get("require_above_ma20"):
            ma20 = ind.get("ma20")
            if not (isinstance(ma20, float) and quote.price and ma20
                    and quote.price > ma20):
                return []
        out = []
        for s in strategies:
            sig = stg.check_buy(s, quote, ind, now)
            if sig:
                sig.sparkline = [k.close for k in klines[-30:]]
                sig.op_score, sig.grade = opportunity.opportunity_score(
                    sig, ind, quote,
                    weights=(opp_cfg or {}).get("weights"),
                    min_amount_wan=(opp_cfg or {}).get("min_amount_wan", 0))
                out.append(sig)
        # 同股合并：策略徽章聚合，分取最高，止损/目标取保守值
        if merge_same_code and len(out) > 1:
            best = max(out, key=lambda s: s.op_score)
            best.hit_rules = [f"{s.strategy}" for s in out]
            best.stop_price = max(x.stop_price or 0 for x in out) or None
            best.target_price = min(
                x.target_price or 9e18 for x in out)
            if best.target_price and best.target_price > 9e17:
                best.target_price = None
            best.reason = (f"命中{len(out)}套策略：" +
                           "、".join(x.strategy for x in out))
        return out[:1] if merge_same_code else out

    signals: List[TradeSignal] = []
    checked = 0
    with ThreadPoolExecutor(max_workers=12) as pool:   # v4.4.3 8→12
        for result in pool.map(check_row, candidates):
            checked += 1
            if on_progress and checked % 20 == 0:
                on_progress(f"技术判定 {checked}/{len(candidates)}…")
            signals.extend(result)
    signals.sort(key=lambda s: s.op_score, reverse=True)
    if opp_cfg:
        min_score = float(opp_cfg.get("min_score") or 0)
        if min_score > 0:
            signals = [s for s in signals if s.op_score >= min_score]
    return signals


def is_trade_time(dt: Optional[datetime] = None) -> bool:
    """是否 A 股交易时段（周一~五 9:25-11:35 / 12:55-15:05，不含节假日判断）。"""
    dt = dt or datetime.now()
    if dt.weekday() >= 5:
        return False
    hm = dt.hour * 100 + dt.minute
    return 925 <= hm <= 1135 or 1255 <= hm <= 1505


class MonitorService:
    """一次扫描 = 一个纯函数调用，方便测试与后台线程复用。

    quote_provider: 提供 get_quotes(codes)；market_fetch: 全市场快照；
    kline_fetch(code, period, limit): K线（内部含多源降级）。
    """

    def __init__(self, cfg: Config, quote_provider, market_fetch, kline_fetch):
        self.cfg = cfg
        self.quote_provider = quote_provider
        self.market_fetch = market_fetch
        self.kline_fetch = kline_fetch

    # ------------------------------------------------------------ 粗筛
    @staticmethod
    def prefilter(rows: List[dict], limit: int = MAX_TECH_CHECK) -> List[dict]:
        """快照级粗筛：剔除停牌/ST/极端价，按量比+换手热度排序取前 limit。"""
        def heat(r: dict) -> float:
            vr = r.get("volume_ratio") or 0
            tr = r.get("turnover_rate") or 0
            return vr * 2 + tr

        out = []
        for r in rows:
            price = r.get("price")
            if not price or price < 2:          # 低价股/停牌（价格为空）
                continue
            name = r.get("name") or ""
            if "ST" in name or "退" in name:
                continue
            out.append(r)
        out.sort(key=heat, reverse=True)
        return out[:limit]

    # ------------------------------------------------------------ 扫描
    def scan_once(self, strategies: List[stg.Strategy],
                  universe_rows: Optional[List[dict]] = None,
                  max_count: int = 2000,
                  on_progress: Optional[Callable[[str], None]] = None,
                  watch_codes: Optional[List[str]] = None) -> List[TradeSignal]:
        """执行一轮全市场扫描，返回去重后的新信号。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if on_progress:
            on_progress("拉取全市场快照…")
        if universe_rows is None:
            universe_rows = self.market_fetch(max_count)

        signals = run_strategy_scan(strategies, universe_rows,
                                    self.kline_fetch, on_progress)

        # 卖出跟踪（持仓股）
        sell_signals = self._scan_sells(strategies, now)
        signals.extend(sell_signals)

        # 去重
        fresh: List[TradeSignal] = []
        for sig in signals:
            key = sig.key()
            if self.cfg.is_fired(key):
                continue
            self.cfg.mark_fired(key)
            fresh.append(sig)
        self.cfg.trim_fired()

        # 写历史
        for sig in fresh:
            self.cfg.append_history({
                "time": sig.time, "code": sig.code, "name": sig.name,
                "strategy": sig.strategy, "side": sig.side,
                "price": sig.price, "stop": sig.stop_price,
                "target": sig.target_price, "risk": sig.risk_score,
                "score": sig.op_score, "grade": sig.grade,
                "spark": sig.sparkline, "reason": sig.reason,
            })
        self.cfg.save()
        if on_progress:
            on_progress(f"扫描完成：新信号 {len(fresh)} 条")
        return fresh

    # ------------------------------------------------------------ 卖出
    def _scan_sells(self, strategies: List[stg.Strategy], now: str) -> List[TradeSignal]:
        positions: Dict[str, dict] = self.cfg.monitor.get("positions") or {}
        codes = list(positions.keys())
        if not codes:
            return []
        try:
            quotes = self.quote_provider.get_quotes(codes)
        except Exception as exc:  # noqa: BLE001
            log.warning("卖出监听行情失败: %s", exc)
            return []
        out: List[TradeSignal] = []
        for code, pos in positions.items():
            quote = quotes.get(code)
            if not quote or not quote.price:
                continue
            sname = pos.get("strategy") or ""
            s = next((x for x in strategies if x.name == sname), None)
            if not s:
                continue
            try:
                klines = self.kline_fetch(code, "day", 800)
                ind = analyze_cached(code, klines)
            except Exception as exc:  # noqa: BLE001
                log.debug("卖出监听K线失败 %s: %s", code, exc)
                continue
            # 风控线：止损/止盈优先
            entry = pos.get("entry_price") or 0
            if entry:
                if quote.price <= entry * (1 - s.stop_loss_pct / 100):
                    sig = TradeSignal(
                        time=now, code=code, name=quote.name, strategy=s.name,
                        side="sell", price=quote.price, risk_score=40,
                        hit_rules=[f"触发止损({s.stop_loss_pct}%)"],
                        reason=f"持仓成本{entry:.2f}，现价{quote.price:.2f}触发止损线")
                    out.append(sig)
                    continue
                if quote.price >= entry * (1 + s.take_profit_pct / 100):
                    sig = TradeSignal(
                        time=now, code=code, name=quote.name, strategy=s.name,
                        side="sell", price=quote.price, risk_score=20,
                        hit_rules=[f"达到止盈({s.take_profit_pct}%)"],
                        reason=f"持仓成本{entry:.2f}，现价{quote.price:.2f}达到止盈线")
                    out.append(sig)
                    continue
            sig = stg.check_sell(s, quote, ind, now)
            if sig:
                out.append(sig)
        return out

    # ------------------------------------------------------------ 持仓
    def add_position(self, code: str, name: str, strategy: str, price: float) -> None:
        positions = self.cfg.monitor.setdefault("positions", {})
        positions[code] = {"name": name, "strategy": strategy,
                           "entry_price": price,
                           "entry_date": datetime.now().strftime("%Y-%m-%d")}
        self.cfg.save()

    def remove_position(self, code: str) -> None:
        positions = self.cfg.monitor.setdefault("positions", {})
        positions.pop(code, None)
        self.cfg.save()
