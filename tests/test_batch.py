"""批量跑模型的测试：解析提示词、写结果、断点续跑、失败记录（全部离线）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_client import ChatClient  # noqa: E402
from batch import (  # noqa: E402
    Prompt,
    default_prices,
    load_finished_ids,
    parse_prompts,
    run_batch,
)
from mock_server import MockServer  # noqa: E402


class ParsePromptTest(unittest.TestCase):
    def write(self, text: str, suffix: str = ".txt") -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8")
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_txt_one_per_line(self) -> None:
        path = self.write("# 注释\n第一句\n\n第二句\n")
        prompts = parse_prompts(path)
        self.assertEqual([p.text for p in prompts], ["第一句", "第二句"])
        self.assertEqual([p.id for p in prompts], ["p1", "p2"])

    def test_txt_blocks_separated_by_dash(self) -> None:
        path = self.write("第一段\n第一段第二行\n---\n第二段\n")
        prompts = parse_prompts(path)
        self.assertEqual([p.text for p in prompts], ["第一段\n第一段第二行", "第二段"])

    def test_txt_blocks_ignore_leading_comments(self) -> None:
        path = self.write("# 文件说明\n# 第二行说明\n第一段\n第二段\n---\n# 块注释\n第三段\n")
        prompts = parse_prompts(path)
        self.assertEqual([p.text for p in prompts], ["第一段\n第二段", "第三段"])

    def test_example_multiline_file(self) -> None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        prompts = parse_prompts(os.path.join(root, "examples", "prompts_multiline.txt"))
        self.assertEqual(len(prompts), 2)
        self.assertIn("三国演义", prompts[0].text)
        self.assertIn("翻译", prompts[1].text)

    def test_jsonl_with_id_and_system(self) -> None:
        path = self.write(
            '{"id": "a", "system": "你是翻译", "prompt": "hi"}\n{"prompt": "yo"}\n',
            suffix=".jsonl",
        )
        prompts = parse_prompts(path, default_system="默认人设")
        self.assertEqual(prompts[0].id, "a")
        self.assertEqual(prompts[0].system, "你是翻译")
        self.assertEqual(prompts[1].system, "默认人设")

    def test_json_array(self) -> None:
        path = self.write('["第一句", {"prompt": "第二句"}]', suffix=".json")
        prompts = parse_prompts(path)
        self.assertEqual([p.text for p in prompts], ["第一句", "第二句"])


class BatchRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = MockServer(expected_key="sk-test").start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        self.server.calls.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = os.path.join(self.tmp.name, "out.jsonl")

    def client(self, api_key: str = "sk-test") -> ChatClient:
        return ChatClient(
            api_key=api_key,
            base_url=self.server.base_url,
            model="mock-model",
            timeout=5,
            backoff=0.01,
        )

    def prompts(self) -> list[Prompt]:
        return [Prompt(idx=0, id="p1", text="你好"), Prompt(idx=1, id="p2", text="再见")]

    def read_records(self) -> list[dict]:
        with open(self.out, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def test_writes_one_record_per_prompt(self) -> None:
        stats = run_batch(self.prompts(), self.client(), self.out, workers=2, progress=False)
        records = self.read_records()
        self.assertEqual(len(records), 2)
        self.assertEqual(stats["ok"], 2)
        self.assertEqual(stats["failed"], 0)
        self.assertEqual({r["id"] for r in records}, {"p1", "p2"})
        self.assertTrue(all("mock" in r["reply"] for r in records))
        self.assertTrue(all(r["error"] is None for r in records))
        self.assertEqual(stats["prompt_tokens"], 20)
        self.assertEqual(stats["completion_tokens"], 40)
        self.assertGreater(stats["cost_cny"], 0)
        self.assertGreater(stats["avg_latency_ms"], 0)

    def test_resume_skips_finished(self) -> None:
        run_batch(self.prompts(), self.client(), self.out, progress=False)
        stats = run_batch(self.prompts(), self.client(), self.out, progress=False, resume=True)
        self.assertEqual(stats["skipped"], 2)
        self.assertEqual(stats["ran"], 0)
        self.assertEqual(len(self.read_records()), 2)  # 没有重复写入

    def test_failure_is_recorded_not_raised(self) -> None:
        stats = run_batch(self.prompts(), self.client(api_key="bad"), self.out, progress=False)
        self.assertEqual(stats["ok"], 0)
        self.assertEqual(stats["failed"], 2)
        records = self.read_records()
        self.assertTrue(all("401" in r["error"] for r in records))
        self.assertTrue(all(r["reply"] is None for r in records))

    def test_load_finished_ids_ignores_errors(self) -> None:
        with open(self.out, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": "a", "error": None}) + "\n")
            handle.write(json.dumps({"id": "b", "error": "HTTP 401"}) + "\n")
            handle.write("{半行被截断")  # 模拟 Ctrl+C 截断
        self.assertEqual(load_finished_ids(self.out), {"a"})
        self.assertEqual(load_finished_ids(os.path.join(self.tmp.name, "missing.jsonl")), set())


class PriceTest(unittest.TestCase):
    def test_known_and_unknown_models(self) -> None:
        self.assertEqual(default_prices("deepseek-flash"), (2.0, 8.0))
        self.assertEqual(default_prices("deepseek-v4-pro"), (9.0, 27.0))
        self.assertEqual(default_prices("some-other-model"), (2.0, 8.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
