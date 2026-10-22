#!/usr/bin/env python3
"""批量跑模型：把提示词文件整批发给 DeepSeek（或任何 OpenAI 兼容接口）。

为什么需要它（而不是手写 for 循环）：
  - 一次跑几十上百条提示词，自动并发、自动重试；
  - 每条都记录 token 用量、耗时、预估费用，方便估算「跑大模型」的成本；
  - 断点续跑：中途 Ctrl+C / 断网 / 关机，重跑时跳过已成功的条目；
  - 单条失败不中断整批（401、429、超时都会写进结果文件，方便排查）。

提示词文件格式（按扩展名自动识别）：
  .txt / .md   每行一条提示词；空行和 # 开头的行忽略；
               想写多行提示词，用单独一行 --- 分隔（见 examples/prompts.txt）
  .jsonl       每行一个 JSON：{"id": "p1", "system": "可选", "prompt": "..."}
  .json        JSON 数组，元素同上（也允许直接是字符串）

用法：
  python batch.py examples/prompts.txt --out results/demo.jsonl --mock
  python batch.py examples/prompts.txt --out results/run1.jsonl --workers 4
  python batch.py examples/prompts.jsonl --out results/run2.jsonl --resume
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime

from api_client import ApiError, ChatClient, build_messages
from main import ENV_FILE, is_placeholder_key, load_dotenv, setup_stdout

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

# DeepSeek 官方价格（元 / 百万 tokens，取高峰价，估算偏保守）
# 参考：https://api-docs.deepseek.com/zh-cn/quick_start/pricing
DEFAULT_PRICES = {
    "deepseek-flash": (2.0, 8.0),      # 输入 2 元，输出 8 元（高峰）
    "deepseek-v4-pro": (9.0, 27.0),    # 输入 9 元，输出 27 元（高峰）
}


def default_prices(model: str) -> tuple[float, float]:
    """按模型名给出默认单价；未知模型按 deepseek-flash 估。"""
    return DEFAULT_PRICES.get(model.strip().lower(), DEFAULT_PRICES["deepseek-flash"])


@dataclass
class Prompt:
    idx: int
    id: str
    text: str
    system: str | None = None


# ---------------------------------------------------------------- 解析提示词


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def _strip_leading_comments(block: str) -> str:
    """丢掉块开头的注释行，避免把文件头的说明文字当成提示词。"""
    lines = block.splitlines()
    while lines and lines[0].lstrip().startswith("#"):
        lines.pop(0)
    return "\n".join(lines).strip()


def _txt_items(path: str) -> list:
    """txt/md：支持「一行一条」和「--- 分隔多行」两种写法。"""
    lines = _read_text(path).splitlines()
    if any(line.strip() == "---" for line in lines):
        blocks: list[str] = []
        current: list[str] = []
        for line in lines:
            if line.strip() == "---":
                if current:
                    blocks.append("\n".join(current))
                current = []
            else:
                current.append(line)
        if current:
            blocks.append("\n".join(current))
        items = [_strip_leading_comments(block) for block in blocks]
        return [{"prompt": block} for block in items if block]
    items = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            items.append({"prompt": stripped})
    return items


def _json_items(path: str) -> list:
    if path.lower().endswith(".jsonl"):
        items = []
        for line in _read_text(path).splitlines():
            if line.strip():
                items.append(json.loads(line))
        return items
    data = json.loads(_read_text(path))
    if isinstance(data, dict):
        data = data.get("prompts", [])
    if not isinstance(data, list):
        raise ValueError("JSON 提示词文件必须是数组，或包含 prompts 数组的对象")
    return data


def parse_prompts(path: str, default_system: str | None = None) -> list[Prompt]:
    """读取提示词文件，返回带序号/ID 的 Prompt 列表。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到提示词文件：{path}")
    if path.lower().endswith((".json", ".jsonl")):
        raw = _json_items(path)
    else:
        raw = _txt_items(path)

    prompts: list[Prompt] = []
    for item in raw:
        if isinstance(item, str):
            item = {"prompt": item}
        if not isinstance(item, dict):
            continue
        text = str(item.get("prompt") or item.get("user") or "").strip()
        if not text:
            continue
        prompts.append(
            Prompt(
                idx=len(prompts),
                id=str(item.get("id") or f"p{len(prompts) + 1}"),
                text=text,
                system=item.get("system") or default_system,
            )
        )
    return prompts


# ---------------------------------------------------------------- 断点续跑


def load_finished_ids(out_path: str) -> set[str]:
    """读取已有结果文件，返回「已成功」的 id 集合（用于 --resume）。"""
    done: set[str] = set()
    if not os.path.exists(out_path):
        return done
    with open(out_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue  # 上次被 Ctrl+C 截断的半行
            if isinstance(record, dict) and not record.get("error"):
                done.add(str(record.get("id")))
    return done


# ---------------------------------------------------------------- 执行


def run_batch(
    prompts: list[Prompt],
    client: ChatClient,
    out_path: str,
    *,
    workers: int = 1,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    resume: bool = False,
    progress: bool = True,
    price_in: float | None = None,
    price_out: float | None = None,
) -> dict:
    """执行批量调用，边跑边写 JSONL，返回统计信息。"""
    price_in = default_prices(client.model)[0] if price_in is None else price_in
    price_out = default_prices(client.model)[1] if price_out is None else price_out

    finished = load_finished_ids(out_path) if resume else set()
    pending = [p for p in prompts if p.id not in finished]

    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)

    stats = {
        "total": len(prompts),
        "ran": len(pending),
        "skipped": len(prompts) - len(pending),
        "ok": 0,
        "failed": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_cny": 0.0,
        "total_latency_ms": 0.0,
        "avg_latency_ms": 0.0,
        "elapsed_s": 0.0,
        "out_path": out_path,
    }
    if not pending:
        return stats

    workers = max(1, min(int(workers), 16))
    lock = threading.Lock()
    started = time.perf_counter()
    done_count = 0
    write_fh = open(out_path, "a", encoding="utf-8")

    def work(prompt: Prompt) -> dict:
        messages = build_messages(prompt.system, prompt.text)
        try:
            result = client.chat_with_usage(
                messages, temperature=temperature, max_tokens=max_tokens
            )
            cost = (
                result.prompt_tokens * price_in + result.completion_tokens * price_out
            ) / 1_000_000
            record = {
                "id": prompt.id,
                "index": prompt.idx,
                "model": result.model,
                "system": prompt.system,
                "prompt": prompt.text,
                "reply": result.content,
                "error": None,
                "latency_ms": round(result.latency_ms, 1),
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.total_tokens,
                "usage": result.usage,
                "cost_cny": round(cost, 6),
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            ok = True
        except ApiError as exc:
            record = _error_record(prompt, client.model, str(exc))
            ok = False
        except Exception as exc:  # 兜底：单条意外错误不能拖垮整批
            record = _error_record(prompt, client.model, f"未预期错误：{exc!r}")
            ok = False

        with lock:
            if write_fh.closed:  # 被 Ctrl+C 中断后，迟到的结果不再写入
                return record
            write_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            write_fh.flush()
            stats["ok" if ok else "failed"] += 1
            if ok:
                stats["prompt_tokens"] += record["prompt_tokens"]
                stats["completion_tokens"] += record["completion_tokens"]
                stats["cost_cny"] += record["cost_cny"]
                stats["total_latency_ms"] += record["latency_ms"]
            nonlocal done_count
            done_count += 1
            if progress:
                label = "ok  " if ok else "FAIL"
                detail = (
                    f'{record["latency_ms"]:.0f}ms {record["total_tokens"]}tok'
                    if ok
                    else record["error"].splitlines()[0][:80]
                )
                print(
                    f'[{done_count:>3}/{len(pending)}] {label} {prompt.id:>12}  {detail}',
                    file=sys.stderr,
                )
        return record

    executor = ThreadPoolExecutor(max_workers=workers)
    futures = [executor.submit(work, prompt) for prompt in pending]
    try:
        for future in as_completed(futures):
            future.result()  # 异常已在 work 内兜底，这里只为触发
    except BaseException:
        executor.shutdown(wait=False, cancel_futures=True)
        with lock:
            write_fh.close()
        stats["elapsed_s"] = round(time.perf_counter() - started, 2)
        raise
    else:
        executor.shutdown(wait=True)
        with lock:
            write_fh.close()
        stats["elapsed_s"] = round(time.perf_counter() - started, 2)
        stats["avg_latency_ms"] = round(
            stats["total_latency_ms"] / max(1, stats["ok"]), 1
        )
    return stats


def _error_record(prompt: Prompt, model: str, message: str) -> dict:
    return {
        "id": prompt.id,
        "index": prompt.idx,
        "model": model,
        "system": prompt.system,
        "prompt": prompt.text,
        "reply": None,
        "error": message,
        "latency_ms": None,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "usage": {},
        "cost_cny": 0.0,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def print_summary(stats: dict) -> None:
    print("\n" + "=" * 56, file=sys.stderr)
    print(f'提示词总数 : {stats["total"]}', file=sys.stderr)
    print(f'本次执行   : {stats["ran"]}（跳过已成功 {stats["skipped"]}）', file=sys.stderr)
    print(f'成功 / 失败: {stats["ok"]} / {stats["failed"]}', file=sys.stderr)
    print(
        f'tokens     : 输入 {stats["prompt_tokens"]} + 输出 {stats["completion_tokens"]}',
        file=sys.stderr,
    )
    print(f'预估费用   : {stats["cost_cny"]:.4f} 元（按高峰价估算）', file=sys.stderr)
    print(f'平均耗时   : {stats["avg_latency_ms"]} ms / 条', file=sys.stderr)
    print(f'总耗时     : {stats["elapsed_s"]} 秒', file=sys.stderr)
    print(f'结果文件   : {stats["out_path"]}', file=sys.stderr)
    print("=" * 56, file=sys.stderr)


# ---------------------------------------------------------------- 命令行入口


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="批量跑模型：提示词文件 -> JSONL 结果（含用量/耗时/费用）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", help="提示词文件（.txt/.md/.jsonl/.json）")
    parser.add_argument(
        "--out",
        default=None,
        help="结果文件（JSONL，可续跑）；默认 results/batch-<时间>.jsonl",
    )
    parser.add_argument("--model", help="模型名，默认读 .env 的 OPENAI_MODEL")
    parser.add_argument("--base-url", help="接口地址，默认读 .env 的 OPENAI_BASE_URL")
    parser.add_argument("--key", help="API Key（不建议写在命令行）")
    parser.add_argument("--system", help="统一的 system 人设（文件里单独指定的优先）")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--workers", type=int, default=1, help="并发数（DeepSeek 建议 1~4）")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条，先小样本试水")
    parser.add_argument("--resume", action="store_true", help="跳过结果文件里已成功的条目")
    parser.add_argument("--mock", action="store_true", help="使用本地 mock 服务，不花钱")
    parser.add_argument("--price-in", type=float, default=None, help="输入单价（元/百万 tokens）")
    parser.add_argument("--price-out", type=float, default=None, help="输出单价（元/百万 tokens）")
    parser.add_argument("--quiet", action="store_true", help="不打印每条进度")
    return parser


def default_out_path(model: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in model)
    return os.path.join(RESULTS_DIR, f"batch-{safe}-{stamp}.jsonl")


def main(argv: list[str] | None = None) -> int:
    setup_stdout()
    load_dotenv()
    args = build_parser().parse_args(argv)

    prompts = parse_prompts(args.input, default_system=args.system)
    if args.limit is not None:
        prompts = prompts[: max(0, args.limit)]
    if not prompts:
        print(f"[错误] {args.input} 里没有解析到任何提示词。", file=sys.stderr)
        return 2

    mock_server = None
    if args.mock:
        from mock_server import MockServer

        mock_server = MockServer(expected_key="sk-mock").start()
        client = ChatClient(api_key="sk-mock", base_url=mock_server.base_url,
                            model="mock-model", max_retries=1)
        print(f"[mode] 离线 mock 演示，base_url={mock_server.base_url}", file=sys.stderr)
    else:
        client = ChatClient.from_env()
        if args.key:
            client.api_key = args.key
        if args.base_url:
            client.base_url = args.base_url.rstrip("/")
        if args.model:
            client.model = args.model

    if is_placeholder_key(client.api_key):
        print(
            "\n[提示] 还没填 API Key，无法真实调用。\n"
            "  1) 打开 .env，把 OPENAI_API_KEY 换成真实 Key（https://platform.deepseek.com/api_keys）\n"
            "  2) 再运行：python batch.py examples/prompts.txt --out results/run1.jsonl\n"
            "  不想花 Key 也可以先看流程：python batch.py examples/prompts.txt --mock",
            file=sys.stderr,
        )
        return 2

    out_path = args.out or default_out_path(client.model)
    print(
        f"[batch] 模型={client.model} 接口={client.base_url} 共 {len(prompts)} 条 -> {out_path}",
        file=sys.stderr,
    )

    try:
        stats = run_batch(
            prompts,
            client,
            out_path,
            workers=args.workers,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            resume=args.resume,
            progress=not args.quiet,
            price_in=args.price_in,
            price_out=args.price_out,
        )
    except KeyboardInterrupt:
        print("\n[已中断] 已成功的结果都写进了结果文件，加 --resume 可继续跑。", file=sys.stderr)
        return 130
    finally:
        if mock_server is not None:
            mock_server.shutdown()
            mock_server.server_close()

    print_summary(stats)
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
