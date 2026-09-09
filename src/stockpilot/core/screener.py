"""条件选股流水线：全市场快照 → 数值条件过滤 → 技术形态过滤。"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional

from . import indicators, strategy as stg
from .core_lru import analyze_cached
from .models import Quote

log = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]


def _match(row: dict, key: str, lo=None, hi=None) -> bool:
    v = row.get(key)
    if v is None:
        return False
    if lo is not None and v < lo:
        return False
    if hi is not None and v > hi:
        return False
    return True


def filter_numeric(rows: List[dict], cond: Dict[str, object]) -> List[dict]:
    """按勾选的数值条件过滤（条件键不存在/为 None 则跳过）。"""
    out = []
    for r in rows:
        ok = True
        for key in ("price", "change_pct", "turnover_rate", "volume_ratio",
                    "float_mv", "total_mv", "pe", "pb", "amount"):
            lo = cond.get(f"{key}_min")
            hi = cond.get(f"{key}_max")
            if lo is None and hi is None:
                continue
            if not _match(r, key, lo, hi):
                ok = False
                break
        if ok:
            out.append(r)
    return out


class Screener:
    def __init__(self, market_fetch, kline_fetch):
        self.market_fetch = market_fetch
        self.kline_fetch = kline_fetch

    def run(self, cond: Dict[str, object], tech_rules: Optional[List[stg.Rule]] = None,
            max_count: int = 2000, tech_limit: int = 150,
            on_progress: Optional[ProgressCb] = None) -> List[dict]:
        if on_progress:
            on_progress("拉取全市场快照…")
        rows = self.market_fetch(max_count, on_progress=on_progress)
        rows = filter_numeric(rows, cond)
        if on_progress:
            on_progress(f"数值过滤后剩 {len(rows)} 只")
        if not tech_rules:
            return rows

        candidates = rows[:tech_limit]
        if on_progress:
            on_progress(f"技术形态判定前 {len(candidates)} 只…")

        def check(row: dict) -> Optional[dict]:
            try:
                kls = self.kline_fetch(row["code"], "day", 120)
            except Exception as exc:  # noqa: BLE001
                log.debug("K线失败 %s: %s", row.get("code"), exc)
                return None
            if len(kls) < 30:
                return None
            ind = analyze_cached(row["code"], kls)
            ctx = stg.build_ctx(ind, Quote.from_mapping(row["code"], row))
            hit, _ = stg.eval_rules(tech_rules, ctx)
            return row if hit else None

        passed: List[dict] = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            for res in pool.map(check, candidates):
                if res:
                    passed.append(res)
        return passed
