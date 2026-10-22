#!/usr/bin/env python3
"""同一句提示词，跑多个模型做横向对比（速度 / token / 费用 / 回答）。

典型用法：
  python compare.py "用一句话解释什么是递归" --mock                      # 离线演示
  python compare.py "用一句话解释什么是递归" --models deepseek-flash,deepseek-v4-pro
  python compare.py --input examples/prompts.txt --models deepseek-flash,deepseek-v4-pro --limit 3

结果会同时打印到终端，并保存成 results/compare-<时间>.json，方便以后回看。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime

from api_client import ApiError, ChatClient, build_messages
from batch import default_prices, parse_prompts
from main import is_placeholder_key, load_dotenv, setup_stdout

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
DEFAULT_MODELS = "deepseek-flash,deepseek-v4-pro"


# ---------------------------------------------------------------- 表格对齐


def display_width(text: str) -> int:
    """中日韩字符按 2 列宽计算，保证表格对齐。"""
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def cut(text: str, limit: int) -> str:
    """按显示宽度截断，超长时用 … 结尾（总宽度不超过 limit）。"""
    if display_width(text) <= limit:
        return text
    target = max(0, limit - 1)  # 给省略号留 1 列
    out, used = [], 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + w > target:
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


# ---------------------------------------------------------------- 执行


def _cost(prompt_tokens: int, completion_tokens: int, price_in: float, price_out: float) -> float:
    return (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000


def run_compare(
    prompt: str,
    models: list[str],
    client: ChatClient,
    *,
    system: str | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    workers: int = 4,
    price_in: float | None = None,
    price_out: float | None = None,
) -> list[dict]:
    """对每个模型各调用一次，返回按传入顺序排列的结果。"""
    results: list[dict] = []
    messages = build_messages(system, prompt)

    def one(model: str) -> dict:
        model_client = replace(client, model=model)
        pin, pout = default_prices(model)
        pin = pin if price_in is None else price_in
        pout = pout if price_out is None else price_out
        record = {"model": model, "ok": False, "reply": None, "error": None,
                  "latency_ms": None, "prompt_tokens": 0, "completion_tokens": 0,
                  "cost_cny": 0.0}
        try:
            result = model_client.chat_with_usage(
                messages, temperature=temperature, max_tokens=max_tokens
            )
            record.update(
                ok=True,
                reply=result.content,
                latency_ms=round(result.latency_ms, 1),
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                cost_cny=round(_cost(result.prompt_tokens, result.completion_tokens, pin, pout), 6),
            )
        except ApiError as exc:
            record["error"] = str(exc)
        except Exception as exc:  # 兜底，避免一个模型挂掉影响其它模型
            record["error"] = f"未预期错误：{exc!r}"
        return record

    workers = max(1, min(int(workers), len(models) or 1))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(one, model) for model in models]
        results = [future.result() for future in futures]
    return results


# ---------------------------------------------------------------- 展示


def print_table(results: list[dict], prompt: str) -> None:
    headers = ["模型", "状态", "耗时", "输入tok", "输出tok", "费用(元)", "回复预览"]
    rows = []
    for item in results:
        if item["ok"]:
            rows.append([
                item["model"],
                "成功",
                f'{item["latency_ms"]:.0f}ms',
                str(item["prompt_tokens"]),
                str(item["completion_tokens"]),
                f'{item["cost_cny"]:.4f}',
                cut(str(item["reply"]).replace("\n", " "), 60),
            ])
        else:
            first_line = (item["error"] or "未知错误").splitlines()[0]
            rows.append([item["model"], "失败", "-", "-", "-", "-", cut(first_line, 60)])

    widths = [
        max(display_width(headers[i]), *(display_width(row[i]) for row in rows))
        for i in range(len(headers))
    ]
    line = "  ".join(pad(headers[i], widths[i]) for i in range(len(headers)))
    print("\n提示词：" + prompt.strip().replace("\n", " "))
    print(line)
    print("-" * display_width(line))
    for row in rows:
        print("  ".join(pad(row[i], widths[i]) for i in range(len(headers))))


def print_details(results: list[dict]) -> None:
    print("\n--- 回复详情 ---")
    for item in results:
        print(f'\n[{item["model"]}] {"成功" if item["ok"] else "失败"}')
        print(item["reply"] if item["ok"] else item["error"])


def save_results(path: str, runs: list[dict]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "runs": runs,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 命令行入口


def _parse_models(text: str) -> list[str]:
    models = [item.strip() for item in text.replace("，", ",").split(",")]
    return [model for model in models if model]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="同一提示词跑多个模型，横向对比速度 / token / 费用 / 回答",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("prompt", nargs="?", help="要对比的提示词（或用 --input 从文件取）")
    parser.add_argument("--input", help="从提示词文件取（.txt/.md/.jsonl/.json，只取前 --limit 条）")
    parser.add_argument("--limit", type=int, default=3, help="--input 时最多对比几条")
    parser.add_argument("--models", default=DEFAULT_MODELS, help="逗号分隔的模型名")
    parser.add_argument("--system", help="system 人设提示词")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--out", default=None, help="结果 JSON 路径；默认 results/compare-<时间>.json")
    parser.add_argument("--mock", action="store_true", help="使用本地 mock 服务，不花钱")
    parser.add_argument("--no-detail", action="store_true", help="只打印对比表，不打印完整回复")
    parser.add_argument("--price-in", type=float, default=None, help="输入单价（元/百万 tokens）")
    parser.add_argument("--price-out", type=float, default=None, help="输出单价（元/百万 tokens）")
    return parser


def main(argv: list[str] | None = None) -> int:
    setup_stdout()
    load_dotenv()
    args = build_parser().parse_args(argv)

    if args.input:
        prompts = parse_prompts(args.input)[: max(1, args.limit)]
        if not prompts:
            print(f"[错误] {args.input} 里没有解析到提示词。", file=sys.stderr)
            return 2
        jobs = [(p.text, p.system or args.system) for p in prompts]
    elif args.prompt:
        jobs = [(args.prompt, args.system)]
    else:
        print("[错误] 请提供提示词，或用 --input 指定提示词文件。", file=sys.stderr)
        return 2

    models = _parse_models(args.models)
    if not models:
        print("[错误] --models 不能为空。", file=sys.stderr)
        return 2

    mock_server = None
    if args.mock:
        from mock_server import MockServer

        mock_server = MockServer(expected_key="sk-mock").start()
        client = ChatClient(api_key="sk-mock", base_url=mock_server.base_url,
                            model=models[0], max_retries=1)
        print(f"[mode] 离线 mock 演示，base_url={mock_server.base_url}", file=sys.stderr)
    else:
        client = ChatClient.from_env()
        if is_placeholder_key(client.api_key):
            print(
                "\n[提示] 还没填 API Key，无法真实调用。先打开 .env 填 OPENAI_API_KEY，\n"
                "  或加 --mock 离线看效果：python compare.py \"你好\" --mock",
                file=sys.stderr,
            )
            return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = args.out or os.path.join(RESULTS_DIR, f"compare-{stamp}.json")
    all_ok = True
    runs: list[dict] = []

    try:
        for prompt, system in jobs:
            results = run_compare(
                prompt,
                models,
                client,
                system=system,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                price_in=args.price_in,
                price_out=args.price_out,
            )
            print_table(results, prompt)
            if not args.no_detail:
                print_details(results)
            all_ok = all_ok and all(item["ok"] for item in results)
            runs.append({"prompt": prompt, "system": system, "results": results})
        save_results(out_path, runs)
        print(f"\n[已保存] {out_path}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\n[已取消]", file=sys.stderr)
        return 130
    finally:
        if mock_server is not None:
            mock_server.shutdown()
            mock_server.server_close()

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
