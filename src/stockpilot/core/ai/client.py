"""OpenAI 协议客户端（Chat Completions + Models），支持流式 SSE 与自定义厂商。

不依赖 openai SDK：任何实现 OpenAI 兼容协议的服务均可接入。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Generator, List, Optional, Tuple

import requests

log = logging.getLogger(__name__)

DeltaCb = Callable[[str, str], None]  # (kind: content|reasoning, text)


class AiError(Exception):
    """AI 调用失败（含人类可读原因）。"""

    def __init__(self, message: str, retryable: bool = False, raw: str = ""):
        super().__init__(message)
        self.retryable = retryable
        self.raw = raw


def friendly_error(status_code: int, body: str) -> AiError:
    """把常见 HTTP 错误翻译成用户能看懂的中文提示。"""
    body = (body or "").strip()
    lowered = body.lower()
    if status_code == 401:
        msg = "鉴权失败（401）：API Key 无效或已过期"
    elif status_code == 403:
        msg = "无权限（403）：检查 Key 权限/可用范围"
    elif status_code == 404:
        msg = "路径或模型不存在（404）：确认 Base URL 以 /v1 结尾、模型名正确"
    elif status_code == 429:
        msg = "请求过于频繁或额度不足（429）：请稍后重试"
    elif status_code == 503:
        if "cache-only" in lowered or "overloaded" in lowered \
                or "unavailable" in lowered or "cold" in lowered:
            msg = ("服务商暂时过载（503）：模型服务繁忙，属临时现象，"
                   "系统已自动重试，请稍后再点「重新分析」")
        else:
            msg = "服务商暂时不可用（503），请稍后重试"
    elif status_code == 502:
        msg = "服务商网关错误（502），请稍后重试"
    elif status_code == 500:
        msg = "服务商内部错误（500），请稍后重试或更换模型"
    elif status_code >= 500:
        msg = f"服务商异常（{status_code}），请稍后重试"
    else:
        msg = f"请求失败（HTTP {status_code}）"
    # 拼一段原文便于诊断（截断）
    if body and len(body) < 400:
        detail = body
    elif body:
        detail = body[:200] + "…"
    else:
        detail = ""
    retryable = status_code in (429, 500, 502, 503, 504)
    return AiError(msg + (f"　[服务方原文] {detail}" if detail else ""),
                   retryable=retryable, raw=body)


@dataclass
class AiConfig:
    vendor: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.3
    max_tokens: int = 2048

    def api(self, path: str) -> str:
        base = (self.base_url or "").strip().rstrip("/")
        if not base:
            raise AiError("Base URL 未配置，请先在设置中配置 AI 服务")
        return base + path

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "AiConfig":
        return cls(
            vendor=str(d.get("vendor") or ""),
            base_url=str(d.get("base_url") or ""),
            api_key=str(d.get("api_key") or ""),
            model=str(d.get("model") or ""),
            temperature=float(d.get("temperature") or 0.3),
            max_tokens=int(d.get("max_tokens") or 2048),
        )


# ---------------------------------------------------------------- SSE 解析

def extract_delta_content(obj: dict) -> Tuple[str, str]:
    """从流式 chunk 中取 (content, reasoning_content)。"""
    choices = obj.get("choices") or []
    if not choices:
        return "", ""
    delta = choices[0].get("delta") or {}
    return (delta.get("content") or "", delta.get("reasoning_content") or "")


def parse_sse_stream(lines: Generator[str, None, None]
                     ) -> Generator[Tuple[str, str], None, None]:
    """解析 SSE 行序列，产出 (kind, text)。单测覆盖。"""
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        content, reasoning = extract_delta_content(obj)
        if content:
            yield "content", content
        elif reasoning:
            yield "reasoning", reasoning


# ---------------------------------------------------------------- 客户端

class OpenAIClient:
    def __init__(self, cfg: AiConfig, proxy: str = ""):
        self.cfg = cfg
        self.session = requests.Session()
        # v7.2.2：与 HttpClient 同规则——未显式配置代理时无视系统代理
        # （trust_env=True 会跟着死掉的系统代理走，AI 请求全部失败）。
        # AI 服务在局域网（LM Studio/Ollama）时直连更是唯一正确行为。
        self.session.trust_env = False
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            h["Authorization"] = f"Bearer {self.cfg.api_key}"
        return h

    @staticmethod
    def _http_error(resp: requests.Response) -> str:
        """已废弃拼接式错误文案，改用 friendly_error；保留给旧调用方。"""
        try:
            obj = resp.json()
            msg = (obj.get("error") or {}).get("message") or json.dumps(
                obj, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            msg = resp.text[:200]
        return f"HTTP {resp.status_code}\n{msg}"

    # ------------------------------------------------------------ 对话
    RETRY_STATUSES = (429, 500, 502, 503, 504)
    RETRY_TIMES = [1.5, 3.0, 6.0]   # 秒，指数退避

    @staticmethod
    def _resp_text_utf8(resp, limit: int = 400) -> str:
        """错误响应体解码：强制 UTF-8（resp.text 在无 charset 时按 latin-1 乱码）。"""
        try:
            data = resp.content[:limit * 3]
        except Exception:  # noqa: BLE001
            return ""
        for enc in ("utf-8", "gbk"):
            try:
                return data.decode(enc)[:limit]
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")[:limit]

    def _post_chat(self, body: dict, stream: bool) -> requests.Response:
        """发起一次 chat 请求（不含重试）。失败抛 AiError(retryable=…)。"""
        url = self.cfg.api("/chat/completions")
        try:
            resp = self.session.post(
                url, headers=self._headers(), json=body,
                timeout=(10, 300), stream=stream)
        except requests.RequestException as exc:
            raise AiError(f"网络错误：{exc.__class__.__name__}: {exc}",
                          retryable=True) from exc
        if resp.status_code in self.RETRY_STATUSES:
            raise friendly_error(resp.status_code, self._resp_text_utf8(resp))
        if resp.status_code != 200:
            raise friendly_error(resp.status_code, self._resp_text_utf8(resp))
        return resp

    def chat(self, messages: List[dict], stream: bool = True,
             on_delta: Optional[DeltaCb] = None,
             stop_event: Optional[threading.Event] = None,
             max_tokens: Optional[int] = None) -> str:
        if not self.cfg.model:
            raise AiError("模型名未配置")
        body = {
            "model": self.cfg.model,
            "messages": messages,
            "stream": stream,
            "temperature": self.cfg.temperature,
            "max_tokens": max_tokens or self.cfg.max_tokens,
        }
        last_err: Optional[AiError] = None
        for attempt in range(len(self.RETRY_TIMES) + 1):
            try:
                resp = self._post_chat(body, stream)
            except AiError as exc:
                last_err = exc
                if not exc.retryable or attempt >= len(self.RETRY_TIMES):
                    raise
                time.sleep(self.RETRY_TIMES[attempt])
                continue
            # 请求成功 → 读流/解析（读流中的中断不重试，避免重复输出）
            return self._read_response(resp, stream, on_delta, stop_event)
        raise last_err or AiError("未知错误")

    def _read_response(self, resp, stream: bool, on_delta, stop_event) -> str:
        collected: List[str] = []
        if stream:
            def _lines():
                # 按字节读行，再强制 UTF-8 解码。
                # 不能用 iter_lines(decode_unicode=True)：服务方 Content-Type
                # 不带 charset=utf-8 时 requests 会按 latin-1 解码 → 中文乱码。
                for raw in resp.iter_lines(decode_unicode=False):
                    if not raw:
                        yield ""
                        continue
                    try:
                        yield raw.decode("utf-8")
                    except UnicodeDecodeError:
                        # 多字节字符恰好跨在两个 chunk 边界：缓存拼接再解码
                        yield raw.decode("utf-8", errors="ignore")

            # v7.2.8：SSE 高频 delta 节流（burst 刷 UI/跨线程 emit）。
            # 部分 reasoning 模型逐 token 吐 chunk（>100/s），每个 chunk
            # 一次跨线程 Signal emit + 一次 insertHtml；Windows 上高频
            # 跨线程信号+文本引擎并发是 native 竞态温床（WER 两记
            # 0xc0000005）。聚合 80ms 窗口成批投递：视觉无感（人眼对
            # 80ms 的"流式感"无差别），emit 压力降 1~2 个数量级。
            import time as _time
            _BURST_MS = 0.080
            _buf_kind: str = ""
            _buf_txt: List[str] = []
            _last = _time.monotonic()

            def _flush(force: bool = False) -> None:
                nonlocal _buf_kind, _buf_txt, _last
                if _buf_txt and (_buf_kind or force):
                    on_delta(_buf_kind, "".join(_buf_txt))
                _buf_kind, _buf_txt, _last = "", [], _time.monotonic()

            try:
                for kind, text in parse_sse_stream(_lines()):
                    if stop_event is not None and stop_event.is_set():
                        break
                    if on_delta:
                        if kind != _buf_kind:
                            _flush()           # 换类型（content↔reasoning）先冲
                        _buf_kind, _ = kind, _buf_txt.append(text)
                        if _time.monotonic() - _last >= _BURST_MS:
                            _flush()           # 到窗即冲
                    if kind == "content":
                        collected.append(text)
                _flush(force=True)             # 流尾残余
            finally:
                resp.close()
        else:
            try:
                obj = resp.json()
            except json.JSONDecodeError as exc:
                raise AiError(f"响应解析失败: {exc}", retryable=True) from exc
            choices = obj.get("choices") or []
            if not choices:
                raise AiError(
                    "响应无 choices：" + json.dumps(obj, ensure_ascii=False)[:200],
                    retryable=True)
            msg = choices[0].get("message") or {}
            text = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or ""
            if reasoning and on_delta:
                on_delta("reasoning", reasoning)
            if on_delta and text:
                on_delta("content", text)
            collected.append(text)
        return "".join(collected).strip()

    # ------------------------------------------------------------ 模型列表
    def list_models(self) -> List[str]:
        try:
            resp = self.session.get(self.cfg.api("/models"),
                                    headers=self._headers(), timeout=(10, 30))
        except requests.RequestException as exc:
            raise AiError(f"网络错误：{exc.__class__.__name__}: {exc}",
                          retryable=True) from exc
        if resp.status_code != 200:
            raise friendly_error(resp.status_code, self._resp_text_utf8(resp))
        try:
            obj = resp.json()
        except json.JSONDecodeError as exc:
            raise AiError(f"响应解析失败: {exc}") from exc
        ids = [m.get("id") for m in (obj.get("data") or []) if m.get("id")]
        return sorted(ids)

    # ------------------------------------------------------------ 连通性
    def test_connection(self) -> Tuple[bool, str]:
        """真实化测试：一次轻量对话（约30字上下文），并区分临时限流。"""
        try:
            reply = self.chat(
                [{"role": "user", "content": "回复OK两个字母即可"}],
                stream=False, max_tokens=16)
        except AiError as exc:
            if exc.retryable:
                return False, (f"服务暂时繁忙（可自动重试）: {exc}\n"
                               "若反复出现，请稍后再试或更换模型。")
            return False, f"不可用: {exc}"
        return True, (f"连接成功，模型 {self.cfg.model} 已正常响应 "
                      f"（{reply[:20]}）。注：小请求成功不代表长报告不触发"
                      "服务商限流，长报告已内置自动重试。")
