# -*- coding: utf-8 -*-
"""v7.2.9 修复闸门：多线程共享 Session 的 native crash（CPython gh-134698）。

背景：v7.2.8 便携包在真实使用中 access violation（python313.dll
0xC0000005，WER 固定偏移 0x1cf5a9），faulthandler 抓到崩溃时多线程
并发 ssl read。定位为 CPython 3.13.5 官方已知 bug gh-134698（ssl
跨线程 native crash，3.13.6 修复）；应用层修复 = Session 线程本地化，
消除共享 SSLContext 的竞态条件。
"""
import threading

import pytest


# ---------------------------------------------------------------- 线程隔离

def test_httpclient_session_is_thread_local():
    """不同线程访问同一 HttpClient 实例必须拿到不同 Session 对象。"""
    from stockpilot.core.providers.base import HttpClient
    c = HttpClient()
    main_session = c.session
    boxes = {}

    def grab(key):
        boxes[key] = c.session

    t = threading.Thread(target=grab, args=("worker",))
    t.start()
    t.join()
    assert boxes["worker"] is not main_session, \
        "工作线程必须拿到独立 Session（共享即 gh-134698 竞态面）"


def test_httpclient_session_reused_within_thread():
    """同一线程内 Session 稳定复用（不能每次访问新建——连接池就没意义了）。"""
    from stockpilot.core.providers.base import HttpClient
    c = HttpClient()
    assert c.session is c.session


def test_openai_client_session_is_thread_local():
    """OpenAIClient（ctx.ai_client() 缓存实例被多个页面 Worker 共用）
    也必须线程隔离。"""
    from stockpilot.core.ai.client import AiConfig, OpenAIClient
    ai = OpenAIClient(AiConfig(base_url="http://localhost:1/v1",
                               model="m", api_key="k"))
    main_session = ai.session
    boxes = {}

    def grab():
        boxes["worker"] = ai.session

    t = threading.Thread(target=grab)
    t.start()
    t.join()
    assert boxes["worker"] is not main_session
    # 新线程独立 Session 也必须无视系统代理
    assert boxes["worker"].trust_env is False


def test_proxy_thread_isolation_with_new_instance():
    """同线程先建无代理实例、再建有代理实例——实例级 thread-local
    不允许串用（类级 _local 的坑：代理配置丢失）。"""
    from stockpilot.core.providers.base import HttpClient
    plain = HttpClient()
    _ = plain.session                      # 先建无代理 Session
    proxied = HttpClient(proxy="http://127.0.0.1:7890")
    assert proxied.session.proxies == {"http": "http://127.0.0.1:7890",
                                      "https": "http://127.0.0.1:7890"}


def test_set_proxy_then_close_rebuilds():
    """set_proxy + reset_thread_sessions 后重建的 Session 带新代理。"""
    from stockpilot.core.providers.base import HttpClient
    c = HttpClient()
    assert c.session.proxies == {}          # 默认无代理
    c.set_proxy("http://127.0.0.1:9999")
    c.reset_thread_sessions()
    assert c.session.proxies == {"http": "http://127.0.0.1:9999",
                                "https": "http://127.0.0.1:9999"}


def test_session_thread_safety_stress():
    """压力闸：12 线程并发首次取 Session——懒建临界区无双建错误
    （threading.local 保证每线程各建一次，不抛异常即过）。"""
    from stockpilot.core.providers.base import HttpClient
    c = HttpClient()
    errors = []

    def hammer():
        try:
            for _ in range(50):
                _ = c.session
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    ts = [threading.Thread(target=hammer) for _ in range(12)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors


# ---------------------------------------------------------------- HostBreaker

def test_breaker_check_takes_lock_under_concurrent_record(monkeypatch):
    """check/record 并发交替不抛错、不死锁（v7.2.9 check 补锁）。"""
    import time as _time
    from stockpilot.core.providers.base import HostBreaker, ProviderError
    b = HostBreaker(threshold=3, cool_down=0.2)
    errors = []
    stop = threading.Event()

    def recorder():
        while not stop.is_set():
            try:
                b.record("https://x.example.com/api", False)
                b.record("https://x.example.com/api", True)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
                return

    def checker():
        while not stop.is_set():
            try:
                b.check("https://x.example.com/api")
            except ProviderError:
                pass                   # 熔断打开属预期
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
                return

    ts = [threading.Thread(target=recorder) for _ in range(4)] + \
         [threading.Thread(target=checker) for _ in range(4)]
    for t in ts:
        t.start()
    _time.sleep(1.0)
    stop.set()
    for t in ts:
        t.join(timeout=5)
    assert not errors
    assert not t.is_alive(), "死锁：持锁线程未退出"


def test_concurrent_sessions_unique_across_threads():
    """端到端：多线程并发请求（mock 传输）各 Session 互不相同。

    注意：必须把 Session 对象本身存进列表持引用再比对——线程死后
    thread-local 存储被回收，后建线程的 Session 可能复用刚释放的
    内存地址，id() 会假性相等（id 仅在活对象间唯一）。"""
    from stockpilot.core.providers.base import HttpClient
    c = HttpClient()
    seen: list = []
    seen_lock = threading.Lock()

    def worker():
        s = c.session
        with seen_lock:
            seen.append(s)          # 主线程持强引用，防止地址复用干扰

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(seen) == 8
    for i in range(len(seen)):
        for j in range(i + 1, len(seen)):
            assert seen[i] is not seen[j], "不同线程拿到了同一 Session 对象"
