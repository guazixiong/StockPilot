"""本地K线缓存（SQLite）：全市场策略扫描的数据底座。

借鉴 Sequoia-X 的"本地存储 + 增量更新"架构——首次全量拉取后落盘，
后续扫描直接读缓存，零网络、秒级完成；每日增量补齐到最新交易日。
实时行情场景仍走在线源（保证最新价），缓存仅用于策略扫描。
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from .models import KLine
from .storage import data_dir

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kline (
  code TEXT NOT NULL, date TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL,
  volume REAL, amount REAL, change_pct REAL, turnover REAL,
  PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_kline_code ON kline(code, date);
CREATE TABLE IF NOT EXISTS kline_meta (
  code TEXT PRIMARY KEY, fetched_count INTEGER
);
"""


class KLineStore:
    """线程安全的 SQLite K线缓存。"""

    def __init__(self, db_path: Optional[Path] = None):
        self.path = Path(db_path) if db_path else (data_dir() / "kline.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ------------------------------------------------------------ 读取
    def get(self, code: str, limit: int = 800) -> List[KLine]:
        """按时间升序返回最近 limit 根（无网络，纯本地）。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT date, open, high, low, close, volume, amount,"
                " change_pct, turnover FROM kline WHERE code=?"
                " ORDER BY date DESC LIMIT ?", (code, limit)).fetchall()
        rows.reverse()
        return [KLine(date=r[0], open=r[1], high=r[2], low=r[3], close=r[4],
                      volume=r[5] or 0, amount=r[6] or 0,
                      change_pct=r[7], turnover=r[8]) for r in rows]

    def last_date(self, code: str) -> str:
        with self._conn() as c:
            row = c.execute("SELECT MAX(date) FROM kline WHERE code=?",
                            (code,)).fetchone()
        return row[0] or ""

    def codes(self) -> List[str]:
        with self._conn() as c:
            return [r[0] for r in c.execute(
                "SELECT DISTINCT code FROM kline").fetchall()]

    def count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM kline").fetchone()[0]

    # ------------------------------------------------------------ 写入
    def put(self, code: str, klines: List[KLine]) -> int:
        if not klines:
            return 0
        payload = [(code, k.date, k.open, k.high, k.low, k.close,
                    k.volume, k.amount, k.change_pct, k.turnover)
                   for k in klines]
        with self._lock:
            with self._conn() as c:
                c.executemany(
                    "INSERT OR REPLACE INTO kline VALUES"
                    " (?,?,?,?,?,?,?,?,?,?)", payload)
        return len(payload)

    def record_fetched(self, code: str, count: int) -> None:
        """记录该股上次在线实得根数（源容量天然上限的锚点）。"""
        with self._lock:
            with self._conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO kline_meta VALUES (?,?)",
                    (code, int(count)))

    def fetched_count(self, code: str) -> Optional[int]:
        with self._conn() as c:
            row = c.execute(
                "SELECT fetched_count FROM kline_meta WHERE code=?",
                (code,)).fetchone()
        return int(row[0]) if row and row[0] else None

    def warm_up(self, fetch_kline: Callable, codes: List[str],
                limit: int = 800, on_progress: Optional[Callable] = None,
                workers: int = 8) -> int:
        """首次全量回填：并行拉取并落盘（fetch_kline(code, 'day', limit)）。"""
        done = 0
        total = len(codes)

        def _one(code: str):
            try:
                kls = fetch_kline(code, "day", limit)
                self.put(code, kls)
                return len(kls)
            except Exception as exc:  # noqa: BLE001
                log.debug("缓存回填失败 %s: %s", code, exc)
                return 0

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for n in pool.map(_one, codes):
                done += 1
                if on_progress and done % 100 == 0:
                    on_progress(f"K线缓存回填 {done}/{total}")
        return done

    def daily_incremental(self, fetch_kline: Callable, codes: List[str],
                          on_progress: Optional[Callable] = None,
                          workers: int = 8) -> int:
        """每日增量：只补齐缓存落后于今天的股票（fetch 同 warm_up）。"""
        today = datetime.now().strftime("%Y-%m-%d")
        need = [c for c in codes if self.last_date(c) < today]
        if not need:
            if on_progress:
                on_progress("K线缓存已是最新，无需增量")
            return 0
        return self.warm_up(fetch_kline, need, limit=800,
                            on_progress=on_progress, workers=workers)

    # ------------------------------------------------------------ 扫描接口
    def scan_klines(self, code: str, limit: int = 800) -> List[KLine]:
        """策略扫描专用：缓存优先；miss 时由调用方决定是否回填。"""
        kls = self.get(code, limit)
        return kls
