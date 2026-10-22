"""填 Key 脚本的测试（不联网，全部用临时文件）。"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from setup_key import (  # noqa: E402
    ensure_env_file,
    key_format_problem,
    main,
    mask_key,
    normalize_key,
    upsert_env_var,
)


class KeyFormatTest(unittest.TestCase):
    def test_normalize_strips_spaces_and_quotes(self) -> None:
        self.assertEqual(normalize_key('  "sk-abc123"  '), "sk-abc123")
        self.assertEqual(normalize_key("'sk-abc'"), "sk-abc")

    def test_placeholder_is_rejected(self) -> None:
        self.assertIsNotNone(key_format_problem(""))
        self.assertIsNotNone(key_format_problem("sk-在这里替换成你的真实Key"))

    def test_short_key_is_rejected(self) -> None:
        self.assertIsNotNone(key_format_problem("sk-123"))

    def test_space_inside_key_is_rejected(self) -> None:
        self.assertIsNotNone(key_format_problem("sk-1234 5678901234567890"))

    def test_plausible_key_passes(self) -> None:
        self.assertIsNone(key_format_problem("sk-" + "a1b2c3d4e5" * 3))

    def test_mask_does_not_leak(self) -> None:
        masked = mask_key("sk-1234567890abcdef")
        self.assertNotIn("1234567890", masked)
        self.assertTrue(masked.startswith("sk-12"))


class EnvFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_path = os.path.join(self.tmp.name, ".env")
        self.example_path = os.path.join(self.tmp.name, ".env.example")

    def test_ensure_env_file_copies_example(self) -> None:
        with open(self.example_path, "w", encoding="utf-8") as fh:
            fh.write("# 模板\nOPENAI_API_KEY=sk-占位\nOPENAI_BASE_URL=https://api.deepseek.com\n")
        ensure_env_file(self.env_path, self.example_path)
        with open(self.env_path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("OPENAI_BASE_URL=https://api.deepseek.com", content)

    def test_upsert_replaces_existing_line_and_keeps_comments(self) -> None:
        with open(self.env_path, "w", encoding="utf-8") as fh:
            fh.write("# 注释要保留\nOPENAI_API_KEY=sk-旧值\nOPENAI_MODEL=deepseek-flash\n")
        upsert_env_var(self.env_path, "OPENAI_API_KEY", "sk-新值")
        with open(self.env_path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("# 注释要保留", content)
        self.assertIn("OPENAI_API_KEY=sk-新值", content)
        self.assertIn("OPENAI_MODEL=deepseek-flash", content)
        self.assertNotIn("sk-旧值", content)

    def test_upsert_appends_when_missing(self) -> None:
        with open(self.env_path, "w", encoding="utf-8") as fh:
            fh.write("OPENAI_MODEL=deepseek-flash\n")
        upsert_env_var(self.env_path, "OPENAI_API_KEY", "sk-新增")
        with open(self.env_path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("OPENAI_MODEL=deepseek-flash", content)
        self.assertIn("OPENAI_API_KEY=sk-新增", content)

    def test_main_writes_key_without_network(self) -> None:
        with mock.patch("setup_key.getpass.getpass", return_value="sk-" + "a1b2c3d4e5" * 3):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(["--no-verify", "--env-file", self.env_path])
        self.assertEqual(code, 0)
        with open(self.env_path, encoding="utf-8") as fh:
            self.assertIn("OPENAI_API_KEY=sk-a1b2c3d4e5", fh.read())

    def test_main_rejects_placeholder(self) -> None:
        with mock.patch("setup_key.getpass.getpass", return_value="sk-在这里替换成你的真实Key"):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(["--env-file", self.env_path])
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.env_path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
