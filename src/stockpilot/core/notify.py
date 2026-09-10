"""消息通知：飞书 / 钉钉 / 企业微信群机器人 webhook 推送。

webhook 即密钥，仅保存在本地配置；发送失败重试 1 次并在 UI 日志提示。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import time
import urllib.parse
from typing import List, Optional, Tuple

import requests

from .models import TradeSignal

log = logging.getLogger(__name__)

TIMEOUT = 8.0
DING_KEYWORD = "【股票机会】"  # 钉钉机器人关键词安全校验兜底

_HEADERS = {"Content-Type": "application/json"}


def extract_token(url: str) -> str:
    """从完整 webhook URL 中提取 token/key（容错用户只粘 token）。"""
    url = (url or "").strip()
    for pattern in (r"access_token=([\w-]+)", r"[?&]key=([\w-]+)",
                    r"/hook/([\w-]+)"):
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return url


def _post(url: str, payload: dict) -> Tuple[bool, str]:
    import json

    last_err = ""
    # v7.2.2：webhook 推送无视系统代理（trust_env 跟死代理走 → 推送静默失败）
    sess = requests.Session()
    sess.trust_env = False
    for attempt in range(2):
        try:
            r = sess.post(url, data=json.dumps(payload, ensure_ascii=False)
                          .encode("utf-8"), headers=_HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                body = r.json()
                if body.get("code") in (0, None) and body.get("errcode", 0) == 0:
                    return True, "ok"
                return False, f"接口返回: {body}"
            # 错误体强制 UTF-8 解码（r.text 无 charset 时按 latin-1 会乱码）
            try:
                err_text = r.content[:360].decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                err_text = ""
            last_err = f"HTTP {r.status_code}: {err_text}"
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
        if attempt == 0:
            time.sleep(1)
    return False, last_err


# ---------------------------------------------------------------- 各通道

def send_feishu(webhook: str, text: str) -> Tuple[bool, str]:
    token = extract_token(webhook)
    if not token:
        return False, "飞书 webhook 未配置"
    url = f"https://open.feishu.cn/open-apis/bot/v2/hook/{token}"
    return _post(url, {"msg_type": "text", "content": {"text": text}})


def _ding_sign(secret: str, ts_ms: int) -> str:
    string_to_sign = f"{ts_ms}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                      hashlib.sha256).digest()
    return urllib.parse.quote_plus(base64.b64encode(digest))


def send_dingtalk(webhook: str, secret: str, text: str) -> Tuple[bool, str]:
    token = extract_token(webhook)
    if not token:
        return False, "钉钉 webhook 未配置"
    url = f"https://oapi.dingtalk.com/robot/send?access_token={token}"
    if secret:
        ts = int(time.time() * 1000)
        url += f"&timestamp={ts}&sign={_ding_sign(secret, ts)}"
    payload_text = text if text.startswith(DING_KEYWORD) else DING_KEYWORD + "\n" + text
    return _post(url, {"msgtype": "text", "text": {"content": payload_text}})


def send_wecom(webhook: str, text: str) -> Tuple[bool, str]:
    key = extract_token(webhook)
    if not key:
        return False, "企业微信 webhook 未配置"
    url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
    return _post(url, {"msgtype": "text", "text": {"content": text}})


# ---------------------------------------------------------------- 业务封装

def format_signal_message(sig: TradeSignal) -> str:
    side = "🟢 买入机会" if sig.side == "buy" else "🔴 卖出提醒"
    lines = [
        f"{side} | {sig.name}({sig.code})",
        f"策略: {sig.strategy}",
        f"现价(参考买入成本): {sig.price}",
    ]
    if sig.side == "buy":
        lines.append(f"建议止损: {sig.stop_price}   建议目标: {sig.target_price}")
    lines.append(f"风险评分: {sig.risk_score}/100（越低越稳）")
    lines.append(f"命中: {'；'.join(sig.hit_rules)}")
    if sig.risk_notes:
        lines.append("风险: " + "；".join(sig.risk_notes))
    lines.append(f"时间: {sig.time}")
    lines.append("—— AI分析仅供参考，不构成投资建议 ——")
    return "\n".join(lines)


def notify_signals(notify_cfg: dict, signals: List[TradeSignal]) -> List[str]:
    """按配置推送信号，返回结果日志行。"""
    logs: List[str] = []
    if not notify_cfg.get("enabled") or not signals:
        return logs
    scope = notify_cfg.get("scope") or "buy_sell"
    todo = [s for s in signals if scope == "buy_sell" or s.side == scope]
    for sig in todo:
        text = format_signal_message(sig)
        if notify_cfg.get("feishu_webhook"):
            ok, msg = send_feishu(notify_cfg["feishu_webhook"], text)
            logs.append(f"飞书推送 {sig.code}: {'成功' if ok else '失败 ' + msg}")
        if notify_cfg.get("ding_webhook"):
            ok, msg = send_dingtalk(notify_cfg["ding_webhook"],
                                    notify_cfg.get("ding_secret") or "", text)
            logs.append(f"钉钉推送 {sig.code}: {'成功' if ok else '失败 ' + msg}")
        if notify_cfg.get("wecom_webhook"):
            ok, msg = send_wecom(notify_cfg["wecom_webhook"], text)
            logs.append(f"企微推送 {sig.code}: {'成功' if ok else '失败 ' + msg}")
    return logs


def test_notify(notify_cfg: dict) -> Tuple[bool, str]:
    """发送测试消息，返回 (是否至少一个通道成功, 详情)。"""
    text = ("StockPilot 测试消息\n通道连通性验证成功。\n"
            "—— AI分析仅供参考，不构成投资建议 ——")
    results: List[str] = []
    any_ok = False
    if notify_cfg.get("feishu_webhook"):
        ok, msg = send_feishu(notify_cfg["feishu_webhook"], text)
        results.append(f"飞书: {'成功' if ok else msg}")
        any_ok = any_ok or ok
    if notify_cfg.get("ding_webhook"):
        ok, msg = send_dingtalk(notify_cfg["ding_webhook"],
                                notify_cfg.get("ding_secret") or "", text)
        results.append(f"钉钉: {'成功' if ok else msg}")
        any_ok = any_ok or ok
    if notify_cfg.get("wecom_webhook"):
        ok, msg = send_wecom(notify_cfg["wecom_webhook"], text)
        results.append(f"企微: {'成功' if ok else msg}")
        any_ok = any_ok or ok
    if not results:
        return False, "尚未配置任何 webhook"
    return any_ok, "\n".join(results)


# ---------------------------------------------------------------- 本地即时通知（v6.3）
def should_notify_local(notify_cfg: dict, alerts) -> bool:
    """桌面即时通知判定：告警存在且用户开了本地提示（默认开）。

    v6.2 的问题：通知链只有 webhook 且要求 enabled+webhook 都配置——
    桌面用户不配 webhook 就零通知。用户系统语义是 cron 一轮即推（有
    消息就出）；桌面版的"即时"= 托盘弹窗+提示音必达，webhook 增强可选。
    """
    if not alerts:
        return False
    return bool((notify_cfg.get("local") or {}).get("enabled", True))


def local_alert_text(alerts) -> str:
    """本地弹窗文本：按级别取最重要的前几条拼摘要（一轮一弹，不轰炸）。"""
    if not alerts:
        return ""
    order = {"danger": 0, "warning": 1, "chance": 2, "info": 3}
    top = sorted(alerts, key=lambda a: order.get(getattr(a, "level", "info"), 9))
    lines = []
    for a in top[:4]:
        name = getattr(a, "name", "") or ""
        title = getattr(a, "title", "") or getattr(a, "reason", "") or ""
        lines.append(f"{name} {title}")
    if len(alerts) > 4:
        lines.append(f"…等 {len(alerts)} 条")
    head = "⚠️ 持仓风险提醒" if top[0].level == "danger" else "🔔 持仓盯盘提醒"
    return head + chr(10) + chr(10).join(lines)
