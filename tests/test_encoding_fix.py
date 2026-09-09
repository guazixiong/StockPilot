"""无 charset 的 SSE/错误体解码修复（中文乱码根因）。"""
from stockpilot.core.ai.client import AiConfig, OpenAIClient


class RawResp:
    """模拟服务方 Content-Type 不带 charset 的流式响应（requests 桩）。"""

    status_code = 200
    # UTF-8 字节：无论响应头如何，服务端实际发的是 UTF-8
    lines = [
        'data: {"choices":[{"delta":{"content":"达实智能（002421）综合诊断"}}]}',
        'data: {"choices":[{"delta":{"content":"技术面偏强，回调低吸。"}}]}',
        "data: [DONE]",
    ]

    def iter_lines(self, decode_unicode=False):
        for ln in self.lines:
            yield ln.encode("utf-8")

    def close(self):
        pass


class NoCharsetErr:
    """503 错误体为 UTF-8 字节但无 charset。"""

    status_code = 503
    content = ("cache-only admission rejected a cold, unavailable, or "
               "overloaded request 过载").encode("utf-8")
    encoding = "ISO-8859-1"   # requests 无 charset 时的默认


def test_sse_chinese_without_charset():
    """Content-Type 无 charset 时中文必须正常（不出现 è¾¾å® 型乱码）。"""
    c = OpenAIClient(AiConfig(base_url="https://x.test/v1", model="m"))
    got = []
    out = c._read_response(RawResp(), stream=True,
                           on_delta=lambda k, t: got.append(t), stop_event=None)
    assert "达实智能（002421）综合诊断" in out
    assert "回调低吸" in "".join(got)
    # 关键断言：不得出现 latin-1 双重编码乱码特征
    assert "è¾¾" not in out and "å®" not in out and "ç" not in out


def test_error_body_utf8_without_charset():
    err = OpenAIClient._resp_text_utf8(NoCharsetErr())
    assert "过载" in err
    assert "cache-only admission rejected" in err
    assert "è¿è½½" not in err
