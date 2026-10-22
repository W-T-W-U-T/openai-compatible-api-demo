"""端到端测试：用本地 mock 服务验证 真实 HTTP 调用 / 鉴权 / 流式 / 重试。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_client import ApiError, ChatClient, build_messages  # noqa: E402
from mock_server import MockServer  # noqa: E402


class ChatClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = MockServer(expected_key="sk-test").start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        self.server.calls.clear()
        self.server.fail_remaining = 0

    def client(self, **kwargs) -> ChatClient:
        return ChatClient(
            api_key=kwargs.pop("api_key", "sk-test"),
            base_url=self.server.base_url,
            model="mock-model",
            timeout=5,
            backoff=0.01,
            **kwargs,
        )

    # ---- 正常路径 ----

    def test_chat_returns_reply(self) -> None:
        reply = self.client().chat(build_messages(None, "你好"))
        self.assertIn("mock", reply)
        self.assertIn("你好", reply)

    def test_chat_sends_auth_and_model(self) -> None:
        self.client().chat(build_messages("你是助手", "hi"))
        call = self.server.calls[0]
        self.assertEqual(call["authorization"], "Bearer sk-test")
        self.assertEqual(call["payload"]["model"], "mock-model")
        self.assertEqual([m["role"] for m in call["payload"]["messages"]], ["system", "user"])
        self.assertFalse(call["payload"]["stream"])

    def test_chat_stream_yields_chunks(self) -> None:
        pieces = list(self.client().chat_stream(build_messages(None, "流式")))
        self.assertGreater(len(pieces), 1)
        self.assertIn("mock", "".join(pieces))
        self.assertTrue(self.server.calls[0]["payload"]["stream"])

    # ---- 失败路径 ----

    def test_bad_key_raises_401_without_retry(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self.client(api_key="wrong-key").chat(build_messages(None, "hi"))
        self.assertEqual(ctx.exception.status, 401)
        self.assertEqual(len(self.server.calls), 1)  # 401 不重试

    def test_retries_on_503_then_succeeds(self) -> None:
        self.server.fail_remaining = 1
        reply = self.client(max_retries=1).chat(build_messages(None, "hi"))
        self.assertIn("mock", reply)
        self.assertEqual(len(self.server.calls), 2)  # 第一次 503，第二次成功

    def test_network_error_is_wrapped(self) -> None:
        client = ChatClient(api_key="sk-test", base_url="http://127.0.0.1:1", timeout=1,
                            max_retries=0, backoff=0.01)
        with self.assertRaises(ApiError) as ctx:
            client.chat(build_messages(None, "hi"))
        self.assertIsNone(ctx.exception.status)
        self.assertIn("网络错误", str(ctx.exception))


class BuildMessagesTest(unittest.TestCase):
    def test_without_system(self) -> None:
        self.assertEqual(build_messages(None, "hi"), [{"role": "user", "content": "hi"}])

    def test_with_system(self) -> None:
        self.assertEqual(
            build_messages("你是助手", "hi"),
            [{"role": "system", "content": "你是助手"}, {"role": "user", "content": "hi"}],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
