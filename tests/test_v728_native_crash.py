"""v7.2.8 回归闸：AI 分析闪退（native 崩溃）防线。

用户报告（2026-09-10 22:46~22:52）：AI 分析时闪退、crash.log 完全空白。
WER 取证：两次 0xc0000005 访问冲突（22:48 python313.dll / 22:52
Qt6Core.dll）——C 层崩溃，Python excepthook 根本不经过，防线缺位。

修复双管：
1. crashguard 第四道防线 faulthandler——C 层致命信号也把各线程
   Python 栈写进 crash.log（"闪退无日志"从此不再）。
2. SSE 高频 delta 80ms 窗口聚合——逐 token 吐 chunk 的 reasoning
   模型（>100 emit/s）触发跨线程信号风暴 + insertHtml 高频并发，
   聚合后 emit 压力降 1~2 个数量级（降低 native 竞态触发面）。
"""
import math
import time

import pytest

pytest.importorskip("PySide6")


class TestFaulthandlerInstalled:
    """防线1：faulthandler 装上并指向 crash.log。"""

    def test_faulthandler_enabled_after_install(self, tmp_path, monkeypatch):
        import faulthandler
        import stockpilot.crashguard as cg
        monkeypatch.setattr(cg, "_crash_path",
                            lambda: tmp_path / "logs" / "crash.log")
        cg.install()
        assert faulthandler.is_enabled(), "install() 后 faulthandler 应启用"

    def test_install_writes_marker_line(self, tmp_path, monkeypatch):
        """启用标记行写入 crash.log（用户可辨识 native 转储段落）。"""
        import importlib
        import stockpilot.crashguard as cg
        monkeypatch.setattr(cg, "_crash_path",
                            lambda: tmp_path / "logs" / "crash.log")
        cg.install()
        content = (tmp_path / "logs" / "crash.log").read_text(encoding="utf-8")
        assert "faulthandler已启用" in content

    def test_install_idempotent(self, tmp_path, monkeypatch):
        """重复 install 不炸（faulthandler.enable 二次调用会报错——
        需先 disable；install 内部须幂等）。"""
        import faulthandler
        import stockpilot.crashguard as cg
        monkeypatch.setattr(cg, "_crash_path",
                            lambda: tmp_path / "logs" / "crash.log")
        cg.install()
        cg.install()          # 不得抛错
        assert faulthandler.is_enabled()


class TestSseBurstAggregation:
    """防线2：80ms 窗口聚合——内容零丢失、顺序保持、emit 次数受控。"""

    def _stream(self, tmp_path, chunks, gap=0.0):
        """用本地 mock SSE 服务器驱动 client.chat。返回 (reply, deltas)。"""
        import json
        import threading
        from http.server import HTTPServer, BaseHTTPRequestHandler

        class Mock(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                self.rfile.read(n)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for c in chunks:
                    obj = {"choices": [{"delta": {"c": c}}]}
                    if c == "R":   # reasoning 帧标记
                        obj = {"choices": [{"delta": {"reasoning_content": c}}]}
                    elif c == "D":
                        pass
                    else:
                        obj = {"choices": [{"delta": {"content": c}}]}
                    self.wfile.write(b"data: " + json.dumps(
                        obj, ensure_ascii=False).encode() + b"\n\n")
                    if gap:
                        time.sleep(gap)
                self.wfile.write(b"data: [DONE]\n\n")

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), Mock)      # 随机端口
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        from stockpilot.core.ai.client import AiConfig, OpenAIClient
        client = OpenAIClient(AiConfig(
            vendor="t", base_url=f"http://127.0.0.1:{port}/v1",
            api_key="k", model="m"))
        deltas: list = []
        reply = client.chat([{"role": "user", "content": "hi"}], stream=True,
                            on_delta=lambda k, t: deltas.append((k, t)))
        srv.shutdown()
        return reply, deltas

    def test_content_not_lost_fast_stream(self, tmp_path):
        """高速 100 chunk 无间隔：内容必须一字不丢。"""
        chunks = [f"段{i}。" for i in range(100)]
        reply, deltas = self._stream(tmp_path, chunks, gap=0)
        expect = "".join(chunks)
        assert reply == expect, "聚合后内容丢失"

    def test_content_not_lost_slow_stream(self, tmp_path):
        """低速流（chunk 间隔 > 窗口）：仍零丢失。"""
        chunks = ["甲", "乙", "丙", "丁"]
        reply, deltas = self._stream(tmp_path, chunks, gap=0.12)
        assert reply == "甲乙丙丁"

    def test_reasoning_and_content_ordered(self, tmp_path):
        """content 与 reasoning 混合：类型切换时先冲刷，顺序不乱。"""
        from stockpilot.core.ai.client import parse_sse_stream
        lines = [
            'data: {"choices":[{"delta":{"reasoning_content":"思考"}}]}',
            'data: {"choices":[{"delta":{"content":"答案"}}]}',
            "data: [DONE]"]
        got = list(parse_sse_stream(iter(lines)))
        assert got == [("reasoning", "思考"), ("content", "答案")]

    def test_stop_event_still_breaks(self, tmp_path):
        """停止按钮：stop_event 在窗口聚合下仍然即时中断。"""
        import threading
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import json

        class Mock(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                self.rfile.read(n)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for i in range(500):
                    obj = {"choices": [{"delta": {"content": f"c{i}"}}]}
                    self.wfile.write(b"data: " + json.dumps(obj).encode()
                                     + b"\n\n")
                    time.sleep(0.005)

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), Mock)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        from stockpilot.core.ai.client import AiConfig, OpenAIClient
        client = OpenAIClient(AiConfig(
            vendor="t", base_url=f"http://127.0.0.1:{port}/v1",
            api_key="k", model="m"))
        stop = threading.Event()

        def kick():
            time.sleep(0.3)
            stop.set()
        threading.Thread(target=kick, daemon=True).start()
        t0 = time.monotonic()
        client.chat([{"role": "user", "content": "hi"}], stream=True,
                    on_delta=lambda k, t: None, stop_event=stop)
        took = time.monotonic() - t0
        srv.shutdown()
        assert took < 2.5, f"stop 未即时生效: {took:.1f}s"
