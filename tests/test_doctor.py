"""体检脚本的测试（离线，跑本地 mock 服务）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from doctor import (  # noqa: E402
    check_base_url,
    check_endpoint,
    check_env_file,
    check_key,
    check_python,
)
from mock_server import MockServer  # noqa: E402


class DoctorCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = MockServer(expected_key="sk-test").start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_python_is_new_enough(self) -> None:
        ok, detail = check_python()
        self.assertTrue(ok)
        self.assertIn("Python", detail)

    def test_placeholder_key_is_rejected(self) -> None:
        for value in ("", "sk-在这里替换成你的真实Key", "your-key"):
            ok, _ = check_key(value)
            self.assertFalse(ok, value)

    def test_real_key_is_accepted_and_masked(self) -> None:
        ok, detail = check_key("sk-1234567890abcdef")
        self.assertTrue(ok)
        self.assertNotIn("1234567890", detail)  # 不打印完整 Key

    def test_env_file(self) -> None:
        ok, _ = check_env_file(os.path.join(tempfile.gettempdir(), "definitely-missing.env"))
        self.assertFalse(ok)
        handle = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8")
        handle.write("OPENAI_API_KEY=sk-test\n")
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        ok, _ = check_env_file(handle.name)
        self.assertTrue(ok)

    def test_base_url_validation(self) -> None:
        self.assertTrue(check_base_url("https://api.deepseek.com")[0])
        self.assertFalse(check_base_url("api.deepseek.com")[0])
        self.assertFalse(check_base_url("https://api.deepseek.com/chat/completions")[0])

    def test_endpoint_reachable_against_mock(self) -> None:
        result = check_endpoint(self.server.base_url, "sk-test", timeout=5)
        self.assertTrue(result["reachable"])
        self.assertEqual(result["status"], 200)
        self.assertTrue(result["ok"])

    def test_endpoint_unreachable(self) -> None:
        result = check_endpoint("http://127.0.0.1:1", "", timeout=1)
        self.assertFalse(result["reachable"])
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
