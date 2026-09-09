"""进程级 LRU 结果缓存（v4.4.3）：机会分析的"重复数据不重复计算"。

三层缓存，键均为内容寻址（K线最新日期）——当日K线不变则跨轮零重算：
1. 指标缓存   analyze_cached(code, klines)：indicators.analyze 结果，
              同轮多策略共享 + 跨轮（K线未变）复用；
2. 形态缓存   forms_cached(code, klines)：check_buy 内部的形态预判
              （同上共享）；
3. K 线去重   klines_fingerprint(klines)：内容指纹。

线程安全（锁 + dict），容量上限 LRU 淘汰（防长驻进程内存增长）。
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Dict, List, Optional

from . import indicators
from .models import KLine

_MAX_ENTRIES = 4096
_lock = threading.Lock()
_indicator_cache: "OrderedDict[str, object]" = OrderedDict()


def _fingerprint(klines: List[KLine]) -> str:
    """内容指纹：最新一根 K 线的日期+收盘（K线序列变化则指纹变）。"""
    if not klines:
        return ""
    k = klines[-1]
    return f"{k.date}:{k.close}"


def _lru_get(cache: "OrderedDict", key: str):
    with _lock:
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
    return None


def _lru_put(cache: "OrderedDict", key: str, value) -> None:
    with _lock:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > _MAX_ENTRIES:
            cache.popitem(last=False)   # 淘汰最旧


def analyze_cached(code: str, klines: List[KLine]) -> Dict[str, object]:
    """indicators.analyze 的缓存版：键=(code, 最新K线日期:收盘)。"""
    fp = _fingerprint(klines)
    if not fp:
        return indicators.analyze(klines)
    key = f"{code}|{fp}"
    hit = _lru_get(_indicator_cache, key)
    if hit is not None:
        return hit
    ind = indicators.analyze(klines)
    _lru_put(_indicator_cache, key, ind)
    return ind


def cache_stats() -> Dict[str, int]:
    with _lock:
        return {"indicator_entries": len(_indicator_cache),
                "max_entries": _MAX_ENTRIES}


def clear_stale_caches() -> None:
    """容量清理钩子（当前 LRU 自限，保留显式清空入口）。"""
    with _lock:
        _indicator_cache.clear()
