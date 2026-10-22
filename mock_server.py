"""本地 mock 服务：没有 API Key 也能完整跑通整条 HTTP 调用链路。

它监听随机端口，实现 POST /chat/completions（OpenAI 兼容格式），
支持流式 SSE，并且可以在测试里临时注入 503 来验证重试。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_REPLY = "你好！这是本地 mock 服务的回复：调用链路已经跑通了。"


class _Handler(BaseHTTPRequestHandler):
    server_version = "MockChat/1.0"

    def log_message(self, *args):  # 静音访问日志
        pass

    # ---- 工具 ----

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse_headers(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def _chunk(self, text: str) -> None:
        payload = {
            "id": "chatcmpl-mock",
            "object": "chat.completion.chunk",
            "model": "mock-model",
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
        }
        self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.flush()

    # ---- 路由 ----

    def do_GET(self):  # noqa: N802
        """GET /models：给 doctor.py 做「接口是否可达」体检用。"""
        if self.path.rstrip("/").endswith("/models"):
            self._json(
                200,
                {"object": "list", "data": [{"id": "mock-model", "object": "model"}]},
            )
            return
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802
        cfg: "MockServer" = self.server.cfg  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            self._json(400, {"error": {"message": "invalid json"}})
            return

        cfg.calls.append({"path": self.path, "payload": payload,
                          "authorization": self.headers.get("Authorization", "")})

        if cfg.fail_remaining > 0:
            cfg.fail_remaining -= 1
            self._json(503, {"error": {"message": "mock upstream overloaded"}})
            return

        if self.headers.get("Authorization") != f"Bearer {cfg.expected_key}":
            self._json(401, {"error": {"message": "invalid api key"}})
            return

        messages = payload.get("messages") or []
        user_text = ""
        for item in reversed(messages):
            if item.get("role") == "user":
                user_text = str(item.get("content", ""))
                break
        reply = cfg.reply or f"{DEFAULT_REPLY}（你说的是：{user_text}）"

        if payload.get("stream"):
            self._sse_headers()
            step = max(1, len(reply) // 6)
            for i in range(0, len(reply), step):
                self._chunk(reply[i : i + step])
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        self._json(
            200,
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "model": "mock-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": reply},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            },
        )


class MockServer(ThreadingHTTPServer):
    """上下文管理器式启动：with MockServer() as srv: ..."""

    daemon_threads = True

    def __init__(self, expected_key: str = "sk-mock", reply: str | None = None):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.cfg = self  # 方便 handler 直接读
        self.expected_key = expected_key
        self.reply = reply
        self.fail_remaining = 0
        self.calls: list[dict] = []
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> "MockServer":
        self._thread.start()
        return self

    def __enter__(self) -> "MockServer":
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.shutdown()
        self.server_close()
