"""本地持久化：config.json（配置/自选/策略/信号记录）。

绿色便携优先：exe/脚本同目录 data/ 可写则用之，否则回退 %APPDATA%/StockPilot。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

APP_DIR_NAME = "StockPilot"

_DEFAULTS: Dict[str, Any] = {
    "ai": {
        "active": "",
        "profiles": {},
    },
    "market": {
        "refresh_sec": 5,
        "kline_limit": 180,
        "theme": "dark",
        "proxy": "",
    },
    "watchlist": [
        {"code": "600519", "name": "贵州茅台"},
        {"code": "300750", "name": "宁德时代"},
        {"code": "601318", "name": "中国平安"},
    ],
    "strategies": [],
    "monitor": {
        "auto_scan": False,
        "interval_min": 5,
        "fired": [],
        "positions": {},
        "history": [],
    },
    "journal": [],
    "screen_plans": [],
    "app": {
        "min_to_tray": False,
        "sound": True,
        "tray_notify": True,
    },
    "opportunity": {
        "strategies": [],           # 空=全部内置策略
        "min_score": 0,             # 展示门槛
        "require_above_ma20": True, # 站上MA20过滤
        "min_amount_wan": 5000,     # 流动性门槛(万元成交额)
        "weights": {"risk": 0.5, "trend": 0.12, "volume": 0.08, "rr": 0.10},
    },
    "notify": {
        "feishu_webhook": "",
        "ding_webhook": "",
        "ding_secret": "",
        "wecom_webhook": "",
        "scope": "buy_sell",   # buy / buy_sell
        "enabled": False,
    },
}


def data_dir() -> Path:
    """数据目录：便携优先，回退 APPDATA。"""
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) \
        else Path.cwd()
    portable = base / "data"
    try:
        portable.mkdir(parents=True, exist_ok=True)
        probe = portable / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return portable
    except OSError:
        appdata = Path(os.environ.get("APPDATA") or Path.home())
        d = appdata / APP_DIR_NAME
        d.mkdir(parents=True, exist_ok=True)
        return d


def config_path() -> Path:
    return data_dir() / "config.json"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """线程安全的 JSON 配置存取。"""

    def __init__(self):
        self.path = config_path()
        self._lock = threading.Lock()
        self.data: Dict[str, Any] = dict(_DEFAULTS)
        self.load()

    # -------------------------------------------------------------- 基础
    def load(self) -> None:
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                with self._lock:
                    self.data = _deep_merge(dict(_DEFAULTS), loaded)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("配置加载失败(%s)，使用默认配置", exc)

    def save(self) -> None:
        with self._lock:
            payload = self.data
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                tmp.replace(self.path)
            except OSError as exc:
                log.error("配置保存失败: %s", exc)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def section(self, key: str) -> dict:
        sec = self.data.setdefault(key, {})
        if not isinstance(sec, dict):
            sec = {}
            self.data[key] = sec
        return sec

    # -------------------------------------------------------------- 自选
    def get_watchlist(self) -> List[dict]:
        return list(self.data.get("watchlist") or [])

    def add_watch(self, code: str, name: str = "") -> bool:
        wl = self.data.setdefault("watchlist", [])
        if any(x.get("code") == code for x in wl):
            return False
        wl.insert(0, {"code": code, "name": name})
        self.save()
        return True

    def remove_watch(self, code: str) -> None:
        self.data["watchlist"] = [
            x for x in self.data.get("watchlist") or [] if x.get("code") != code
        ]
        self.save()

    # -------------------------------------------------------------- 策略
    def get_strategy_dicts(self) -> List[dict]:
        return list(self.data.get("strategies") or [])

    def save_strategy_dict(self, d: dict) -> None:
        st = self.data.setdefault("strategies", [])
        name = d.get("name")
        for i, old in enumerate(st):
            if old.get("name") == name:
                st[i] = d
                break
        else:
            st.append(d)
        self.save()

    def remove_strategy(self, name: str) -> None:
        self.data["strategies"] = [
            x for x in self.data.get("strategies") or [] if x.get("name") != name
        ]
        self.save()

    # -------------------------------------------------------------- 持仓
    @property
    def positions(self) -> dict:
        return self.monitor.setdefault("positions", {})

    def save_position(self, code: str, info: dict) -> None:
        self.positions[code] = info
        self.save()

    def remove_position(self, code: str) -> None:
        self.positions.pop(code, None)
        self.save()

    # -------------------------------------------------------------- 流水
    def get_journal(self) -> List[dict]:
        return list(self.data.get("journal") or [])

    def add_journal(self, record: dict, keep: int = 2000) -> None:
        journal = self.data.setdefault("journal", [])
        journal.insert(0, record)
        del journal[keep:]
        self.save()

    def remove_journal(self, index: int) -> None:
        journal = self.data.setdefault("journal", [])
        if 0 <= index < len(journal):
            del journal[index]
        self.save()

    # -------------------------------------------------------------- 方案
    def get_plans(self) -> List[dict]:
        return list(self.data.get("screen_plans") or [])

    def save_plan(self, name: str, cond: dict, tech: list) -> None:
        plans = self.data.setdefault("screen_plans", [])
        plan = {"name": name, "cond": cond, "tech": tech}
        for i, old in enumerate(plans):
            if old.get("name") == name:
                plans[i] = plan
                break
        else:
            plans.append(plan)
        self.save()

    def remove_plan(self, name: str) -> None:
        self.data["screen_plans"] = [
            p for p in self.data.get("screen_plans") or []
            if p.get("name") != name]
        self.save()

    # -------------------------------------------------------------- 监听
    @property
    def monitor(self) -> dict:
        return self.section("monitor")

    def mark_fired(self, key: str) -> None:
        fired = self.monitor.setdefault("fired", [])
        if key not in fired:
            fired.append(key)

    def is_fired(self, key: str) -> bool:
        return key in (self.monitor.get("fired") or [])

    def trim_fired(self, keep: int = 2000) -> None:
        fired = self.monitor.setdefault("fired", [])
        if len(fired) > keep:
            self.monitor["fired"] = fired[-keep:]

    def append_history(self, item: dict, keep: int = 500) -> None:
        hist = self.monitor.setdefault("history", [])
        hist.insert(0, item)
        del hist[keep:]

    # -------------------------------------------------------------- AI
    @property
    def ai(self) -> dict:
        return self.section("ai")

    def active_ai_profile(self) -> Optional[dict]:
        ai = self.ai
        name = ai.get("active")
        profiles = ai.get("profiles") or {}
        return profiles.get(name) if name else (next(iter(profiles.values()), None))

    # -------------------------------------------------------------- 通知
    @property
    def notify(self) -> dict:
        return self.section("notify")

    @property
    def app(self) -> dict:
        return self.section("app")

    # -------------------------------------------------------------- 机会推荐
    @property
    def opportunity(self) -> dict:
        return self.section("opportunity")

    def opportunity_strategies(self, all_names: list) -> list:
        """参与机会的策略名单（空配置=全部）。all_names 传内置+自定义全名。"""
        names = self.opportunity.get("strategies") or []
        return [n for n in names if n in all_names] or list(all_names)
