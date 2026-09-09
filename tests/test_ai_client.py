"""AI 客户端：SSE 解析 / URL 拼接 / 错误提示。"""
import pytest

from stockpilot.core.ai.client import (AiConfig, AiError, extract_delta_content,
                                       parse_sse_stream)
from stockpilot.core.notify import extract_token


def test_extract_delta():
    c, r = extract_delta_content({"choices": [{"delta": {"content": "你好"}}]})
    assert c == "你好" and r == ""
    c, r = extract_delta_content({"choices": [{"delta": {"reasoning_content": "思考"}}]})
    assert c == "" and r == "思考"
    assert extract_delta_content({}) == ("", "")


def test_parse_sse_stream():
    lines = [
        'data: {"choices":[{"delta":{"content":"Hel"}}]}',
        "",
        ": keep-alive",
        'data: {"choices":[{"delta":{"content":"lo"}}]}',
        'data: {"choices":[{"delta":{"reasoning_content":"think"}}]}',
        "data: [DONE]",
        'data: {"choices":[{"delta":{"content":"ignored"}}]}',
    ]
    out = list(parse_sse_stream(iter(lines)))
    assert out == [("content", "Hel"), ("content", "lo"), ("reasoning", "think")]


def test_parse_sse_tolerates_bad_json():
    lines = ['data: {bad json}', 'data: {"choices":[{"delta":{"content":"ok"}}]}']
    out = list(parse_sse_stream(iter(lines)))
    assert out == [("content", "ok")]


def test_api_url_join():
    cfg = AiConfig(base_url="https://api.deepseek.com/v1/", model="deepseek-chat")
    assert cfg.api("/chat/completions") == "https://api.deepseek.com/v1/chat/completions"
    empty = AiConfig()
    with pytest.raises(AiError):
        empty.api("/chat/completions")


def test_extract_token():
    assert extract_token(
        "https://open.feishu.cn/open-apis/bot/v2/hook/abc-123") == "abc-123"
    assert extract_token(
        "https://oapi.dingtalk.com/robot/send?access_token=xyz789") == "xyz789"
    assert extract_token(
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=k8k") == "k8k"
    assert extract_token("plain-token") == "plain-token"
