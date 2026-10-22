"""多模型对比的测试（离线，跑本地 mock 服务）。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_client import ChatClient  # noqa: E402
from compare import cut, display_width, run_compare  # noqa: E402
from mock_server import MockServer  # noqa: E402


class CompareTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = MockServer(expected_key="sk-test").start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def client(self, api_key: str = "sk-test") -> ChatClient:
        return ChatClient(
            api_key=api_key,
            base_url=self.server.base_url,
            model="mock-model",
            timeout=5,
            backoff=0.01,
        )

    def test_compares_each_model(self) -> None:
        results = run_compare("你好", ["deepseek-flash", "deepseek-v4-pro"], self.client())
        self.assertEqual([r["model"] for r in results], ["deepseek-flash", "deepseek-v4-pro"])
        self.assertTrue(all(r["ok"] for r in results))
        self.assertTrue(all("mock" in r["reply"] for r in results))
        self.assertTrue(all(r["latency_ms"] >= 0 for r in results))
        self.assertGreater(results[1]["cost_cny"], results[0]["cost_cny"])  # pro 单价更高

    def test_failure_is_per_model(self) -> None:
        results = run_compare("你好", ["deepseek-flash"], self.client(api_key="bad"))
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["ok"])
        self.assertIn("401", results[0]["error"])

    def test_requested_models_are_sent(self) -> None:
        self.server.calls.clear()
        run_compare("你好", ["model-a", "model-b"], self.client())
        sent = {call["payload"]["model"] for call in self.server.calls}
        self.assertEqual(sent, {"model-a", "model-b"})


class WidthTest(unittest.TestCase):
    def test_display_width_counts_cjk_as_two(self) -> None:
        self.assertEqual(display_width("abc"), 3)
        self.assertEqual(display_width("中文"), 4)
        self.assertEqual(display_width("a中"), 3)

    def test_cut_respects_width(self) -> None:
        text = cut("中文中文中文", 10)
        self.assertLessEqual(display_width(text), 10)
        self.assertTrue(text.endswith("…"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
