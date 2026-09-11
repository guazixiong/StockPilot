"""数据源层公共设施：HTTP 客户端（超时/重试/UA/代理）、代码↔市场映射。

新增数据源：继承 Provider 实现 get_quotes/get_kline/get_news，并在
``register_provider`` 中注册即可（详见 README 维护指南）。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Callable, Dict, List, Optional

import requests

from ..models import KLine, NewsItem, Quote

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class ProviderError(Exception):
    """数据源请求/解析失败。"""


class HostBreaker:
    """按主机熔断（v4.4.2）：同一 host 连续失败 N 次后短路 M 秒，
    期间直接抛错不再发请求——push2 限流时避免"每页白等 2s 重试×20 页"
    的灾难（实测快照从 14s 恶化到 40+s 的场景）。线程安全。"""
    _lock = __import__("threading").Lock()
    _fail: Dict[str, int] = {}
    _open_until: Dict[str, float] = {}
    _last_fail_ts: Dict[str, float] = {}

    def __init__(self, threshold: int = 3, cool_down: float = 60.0):
        self.threshold = threshold
        self.cool_down = cool_down

    def check(self, url: str) -> None:
        host = self._host(url)
        # v7.2.9：读也持锁——record 清计数与 check 读之间的无锁竞态
        # 在 12 线程高频下虽无 native 风险，但语义上可能读到半更新状态。
        with self._lock:
            until = self._open_until.get(host, 0.0)
        if until > time.time():
            raise ProviderError(
                f"主机 {host} 熔断中（近 {self.threshold} 连败，"
                f"冷却至 {time.strftime('%H:%M:%S', time.localtime(until))}）")

    def record(self, url: str, ok: bool) -> None:
        """失败计数带时间窗去重（v4.4.3）：扫描并发 12 线程同轮重复撞墙
        时，10s 内同 host 的失败只计 1 次——既保住"真坏源快速熔断"，
        又不会把降级链里的好源（如腾讯）被瞬时并发失败累计熔断。"""
        host = self._host(url)
        now = time.time()
        with self._lock:
            if ok:
                self._fail.pop(host, None)
                self._open_until.pop(host, None)
                self._last_fail_ts.pop(host, None)
                return
            last = self._last_fail_ts.get(host, 0.0)
            if now - last < 10.0:
                return   # 同轮重复失败不重复计数（阈值内）
            self._last_fail_ts[host] = now
            self._fail[host] = self._fail.get(host, 0) + 1
            if self._fail[host] >= self.threshold:
                self._open_until[host] = now + self.cool_down
                log.warning("主机 %s 连续失败 %d 次，熔断 %.0fs",
                            host, self._fail[host], self.cool_down)

    @staticmethod
    def _host(url: str) -> str:
        m = re.match(r"https?://([^/]+)", url or "")
        return m.group(1) if m else url or ""


class HttpClient:
    """带重试与可选代理的同步 HTTP 客户端。

    v7.2.9 关键变更——线程本地 Session：
    CPython 3.13.5 存在官方已知 native crash（gh-134698，3.13.6 修复）：
    多线程并发调用共享 Session 的 ssl 读写时，OpenSSL 内部状态在 GIL
    释放窗口竞态 → access violation（python313.dll 0xC0000005，WER
    实锤、dist exe 复现 81s 必崩、固定偏移 0x1cf5a9）。12 线程全市场
    扫描 + AI SSE + 行情轮询并发即可触发。
    修复：每线程独立 Session（thread-local）——线程内连接池/SSLContext
    完全隔离，跨线程共享对象不复存在，竞态面归零。附带收益：消除
    urllib3 池满 discard 告警（每线程独享池）。Session 创建轻量
    （毫秒级），线程池工作线程数固定，泄漏可控。"""

    def __init__(self, proxy: Optional[str] = None, timeout: float = 8.0):
        self.timeout = timeout
        self.proxy = proxy or ""
        # 实例级 thread-local（不能是类属性：多个 client 实例会串用
        # 同一存储——无代理实例建过 Session 后，带代理实例在同线程
        # 会复用它，代理配置丢失。v7.2.9 单测已覆盖此坑）。
        self._local = __import__("threading").local()
        # v4.4.2：主机级熔断（状态在 HostBreaker 类级共享，跨 client 生效）
        self._breaker = HostBreaker()

    def set_proxy(self, proxy: str) -> None:
        """更新代理配置并丢弃所有线程的旧 Session（懒重建生效新代理）。"""
        self.proxy = proxy or ""

    def reset_thread_sessions(self) -> None:
        """关闭并清空当前线程 Session；其余线程下次访问时按新配置重建。

        注：threading.local 无法跨线程枚举，各工作线程的旧 Session
        会存留到线程退出（闭池关窗即回收）。代理变更后旧线程 Session
        仍持旧代理属已知折衷——新任务线程均使用新代理；存量极小
        （QThreadPool 固定线程，proxy 变更是低频操作）。"""
        self.close_thread_session()

    @property
    def session(self) -> requests.Session:
        """当前线程专属 Session（懒建）。语义兼容旧属性访问。"""
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            # v7.2.2：无视系统代理抓取（trust_env 默认 True 会跟着环境变量/
            # 注册表的死代理走，导致全部行情请求 ProxyError——用户日志实锤）。
            # 本应用数据源均为国内公网接口，未显式配置代理时必须直连。
            s.trust_env = False
            s.headers.update({
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            if self.proxy:
                s.proxies = {"http": self.proxy, "https": self.proxy}
            self._local.session = s
        return s

    def close_thread_session(self) -> None:
        """显式关闭当前线程的 Session（长寿命线程退出钩子可调）。"""
        s = getattr(self._local, "session", None)
        if s is not None:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
            self._local.session = None

    def get_text(
        self,
        url: str,
        *,
        params=None,
        headers=None,
        encoding: Optional[str] = None,
        retries: int = 2,
        timeout: Optional[float] = None,
    ) -> str:
        """GET 请求返回文本。

        encoding 指定时强制按该编码解码（腾讯/新浪为 GBK）；
        未指定时先取响应头 charset，缺失则探测（apparent_encoding），
        兜底 UTF-8——避免 requests 在无 charset 时按 latin-1 解码导致中文乱码。
        """
        last_err: Optional[Exception] = None
        self._breaker.check(url)          # v4.4.2 熔断中直接抛错
        for attempt in range(retries + 1):
            try:
                resp = self.session.get(
                    url, params=params, headers=headers,
                    timeout=timeout or self.timeout,
                )
                resp.raise_for_status()
                self._breaker.record(url, True)
                if encoding:
                    return resp.content.decode(encoding, errors="replace")
                charset = (resp.encoding or "").strip()
                if not charset or charset.lower() in ("iso-8859-1", "latin-1"):
                    probed = resp.apparent_encoding or "utf-8"
                    try:
                        return resp.content.decode(probed, errors="strict")
                    except (UnicodeDecodeError, LookupError):
                        return resp.content.decode("utf-8", errors="replace")
                return resp.text
            except Exception as exc:  # noqa: BLE001 网络异常统一降级
                last_err = exc
                self._breaker.record(url, False)
                if attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
        raise ProviderError(f"请求失败 {url}: {last_err}") from last_err

    def get_json(self, url: str, *, params=None, headers=None, retries: int = 2,
                 timeout: Optional[float] = None):
        import json

        text = self.get_text(url, params=params, headers=headers,
                             retries=retries, timeout=timeout)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"JSON 解析失败 {url}: {exc}") from exc


# ---------------------------------------------------------------- 代码映射

_INDEX_SYMBOLS = {
    "sh000001", "sz399001", "sz399006", "sh000688", "sh000300", "sh000016",
}


def normalize_code(text: str) -> str:
    """把 '600519' / 'sh600519' / '600519.SH' / '1.600519' 归一化为 6 位代码。"""
    text = (text or "").strip()
    m = re.search(r"(\d{6})", text)
    if not m:
        return ""
    return m.group(1)


def to_market_symbol(code: str) -> str:
    """600519 -> sh600519；000001 -> sz000001；830799 -> bj830799。

    输入已带 sh/sz/bj 前缀（如指数 sh000001）时直接保留前缀。
    """
    text = (code or "").strip().lower()
    m = re.search(r"(sh|sz|bj)?(\d{6})", text)
    if not m:
        raise ProviderError(f"非法股票代码: {code!r}")
    prefix, digits = m.group(1), m.group(2)
    if prefix:
        return prefix + digits
    first = digits[0]
    if digits.startswith("92"):        # 北交所 920xxx
        return "bj" + digits
    if first in "569":                 # 沪市基金(5)/B股(9)/科创板(688)
        return "sh" + digits
    if first in "48":                  # 北交所 43x/83x/87x
        return "bj" + digits
    return "sz" + digits               # 0/2/3 开头：深主板/创业板/B股


def to_secid(code: str) -> str:
    """东财 secid：沪=1，深/北=0。600519 -> '1.600519'。"""
    code = normalize_code(code)
    sym = to_market_symbol(code)
    if sym.startswith("sh"):
        return "1." + code
    return "0." + code


# ---------------------------------------------------------------- Provider


class Provider:
    """数据源基类。子类实现具体接口；名称用于设置页展示。"""

    name = "base"

    def __init__(self, http: HttpClient):
        self.http = http

    def get_quotes(self, codes: List[str]) -> Dict[str, Quote]:
        raise NotImplementedError

    def get_kline(self, code: str, period: str = "day", limit: int = 250
                  ) -> List[KLine]:
        raise NotImplementedError

    def get_news(self, code: str, size: int = 10) -> List[NewsItem]:
        raise NotImplementedError


_REGISTRY: Dict[str, type] = {}


def register_provider(cls):
    _REGISTRY[cls.name] = cls
    return cls


def get_provider_classes() -> Dict[str, type]:
    return dict(_REGISTRY)


ProgressCb = Optional[Callable[[str], None]]
