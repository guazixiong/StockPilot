"""临时会话存储（v5.1）：误关窗口后的"最近分析"恢复。

用户痛点：个股详情窗 AI 分析（诊断/牛熊辩论/大师投委会）全部内存态，
误关窗口即全部丢失，重开要重新花 token 跑一遍。

设计：data/sessions/ 目录按股票代码滚动暂存（每只股票保留最近一份）：
  {code}.json = {
    "code": "600519", "name": "贵州茅台",
    "ts": "2026-09-06 12:00:00",        # 保存时刻
    "kind": "诊断|辩论|投委会",           # 最后一次完成的模式
    "html": "<完整渲染后的报告 HTML>",    # ai_view 最终内容（含共识卡）
    "history": [...],                    # 可追问的消息对（role/content）
  }
- TTL 7 天（过期清理，防膨胀）；单文件 KB 级，全市场也就几百只——容量无忧。
- 原子写（tmp+rename）：崩溃不留半截 JSON。
- 主动查询接口：恢复提示"上次分析 N 小时前，一键回看"。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .storage import data_dir

log = logging.getLogger(__name__)

SESSION_TTL_DAYS = 7


def _sessions_dir() -> Path:
    d = data_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_session(code: str, name: str, kind: str, html: str,
                history: Optional[List[dict]] = None) -> bool:
    """保存（滚动覆盖）一只股票的最近 AI 分析会话。"""
    if not code:
        return False
    payload = {
        "code": code,
        "name": name or code,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kind": kind,
        "html": html or "",
        "history": [m for m in (history or [])
                    if isinstance(m, dict) and m.get("content")],
    }
    path = _sessions_dir() / f"{code}.json"
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(path)          # 原子替换
        _cleanup_stale()
        return True
    except OSError as exc:
        log.warning("会话保存失败 %s: %s", code, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def load_session(code: str) -> Optional[Dict]:
    """读取最近会话；不存在/损坏/TTL 过期返回 None。"""
    path = _sessions_dir() / f"{code}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("会话读取失败 %s: %s", code, exc)
        return None
    ts = data.get("ts", "")
    try:
        age = (datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).days
    except ValueError:
        return None
    if age > SESSION_TTL_DAYS:
        return None
    return data


def age_hours(ts: str) -> float:
    """会话时间距现在几小时（提示文案用）。"""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        return max(0.0, (datetime.now() - dt).total_seconds() / 3600)
    except ValueError:
        return 0.0


def _cleanup_stale() -> int:
    """删除超过 TTL 的会话文件（保存时顺带清理，无需定时器）。"""
    n = 0
    cutoff = time.time() - SESSION_TTL_DAYS * 86400
    for p in _sessions_dir().glob("*.json"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                n += 1
        except OSError:
            continue
    return n


def clear_all() -> int:
    """清空全部会话（设置页入口预留）。"""
    n = 0
    for p in _sessions_dir().glob("*.json"):
        try:
            p.unlink()
            n += 1
        except OSError:
            continue
    return n
