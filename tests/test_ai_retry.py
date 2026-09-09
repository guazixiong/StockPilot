"""AI 客户端重试与友好错误（离线，monkeypatch session）。"""
import pytest

from stockpilot.core.ai.client import AiConfig, AiError, OpenAIClient, friendly_error


class FakeResp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def _client() -> OpenAIClient:
    return OpenAIClient(AiConfig(base_url="https://x.test/v1", model="m"))


def test_friendly_error_503_cache_only():
    err = friendly_error(503, "cache-only admission rejected a cold, "
                              "unavailable, or overloaded request")
    assert err.retryable
    assert "服务商暂时过载" in str(err)
    assert "自动重试" in str(err)


def test_friendly_error_401_not_retryable():
    err = friendly_error(401, "invalid api key")
    assert not err.retryable
    assert "鉴权失败" in str(err)


def test_chat_retries_then_success(monkeypatch):
    """503 两次后第三次成功 → 返回内容，不抛错。"""
    c = _client()
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            return FakeResp(503, "cache-only admission rejected")
        return FakeResp(200, "{}")

    monkeypatch.setattr(c.session, "post", fake_post)
    monkeypatch.setattr("stockpilot.core.ai.client.time.sleep", lambda s: None)
    # 200 响应走 _read_response；stream=True 走 iter_lines，伪造
    class OKResp:
        status_code = 200
        text = ""

        def iter_lines(self, decode_unicode=False):
            # 现在实现按字节读行再 UTF-8 解码，桩返回 bytes
            yield ('data: {"choices":[{"delta":{"content":"你好"}}]}'  # noqa
                   ).encode("utf-8")
            yield b"data: [DONE]"

        def close(self):
            pass

    def fake_post2(url, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            return FakeResp(503, "cache-only admission rejected")
        return OKResp()

    monkeypatch.setattr(c.session, "post", fake_post2)
    out = c.chat([{"role": "user", "content": "hi"}], stream=True)
    assert out == "你好"
    assert calls["n"] == 3


def test_chat_non_retryable_raises_immediately(monkeypatch):
    c = _client()
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        return FakeResp(401, "bad key")

    monkeypatch.setattr(c.session, "post", fake_post)
    with pytest.raises(AiError) as ei:
        c.chat([{"role": "user", "content": "hi"}], stream=True)
    assert calls["n"] == 1
    assert "鉴权失败" in str(ei.value)


def test_chat_gives_up_after_max_retries(monkeypatch):
    c = _client()
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        return FakeResp(503, "overloaded")

    monkeypatch.setattr(c.session, "post", fake_post)
    monkeypatch.setattr("stockpilot.core.ai.client.time.sleep", lambda s: None)
    with pytest.raises(AiError) as ei:
        c.chat([{"role": "user", "content": "hi"}], stream=True)
    assert calls["n"] == 4  # 1 次初始 + 3 次重试
    assert ei.value.retryable
