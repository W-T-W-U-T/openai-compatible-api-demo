#!/usr/bin/env python3
"""一步填好 DeepSeek API Key：粘贴 → 校验格式 → 真实试调 → 写进 .env。

适合不想手动编辑 .env 的情况。Key 只在本地使用，不会显示、不会上传、不会进仓库。

用法：
  python setup_key.py            # 交互式：粘贴 Key，自动验证并写入 .env
  python setup_key.py --no-verify   # 没网/不想花钱时只写入，不真实调用
"""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import sys

from api_client import ApiError, ChatClient
from main import ENV_FILE, is_placeholder_key, setup_stdout

PLACEHOLDER = "sk-在这里替换成你的真实Key"
EXAMPLE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.example")


# ---------------------------------------------------------------- 纯函数（可单测）


def normalize_key(text: str) -> str:
    """去掉粘贴时常见的空格、换行、引号。"""
    return (text or "").strip().strip('"').strip("'").strip()


def key_format_problem(key: str) -> str | None:
    """返回格式问题描述；没问题返回 None。只做常识性检查，不联网。"""
    if not key:
        return "没有输入 Key"
    if is_placeholder_key(key) or key == PLACEHOLDER:
        return "这还是 .env.example 里的占位符，不是真实 Key"
    if any(char.isspace() for char in key):
        return "Key 里包含空格或换行，请重新复制（前后不要带空格）"
    if len(key) < 20:
        return "Key 太短，看起来不像完整 Key（DeepSeek 的 Key 一般是 sk- 开头的一长串）"
    if not key.startswith("sk-"):
        return "Key 通常以 sk- 开头，确认没有复制错（不同平台格式可能不同）"
    return None


def mask_key(key: str) -> str:
    if len(key) <= 10:
        return "***"
    return key[:6] + "…" + key[-4:]


def ensure_env_file(path: str = ENV_FILE, example: str = EXAMPLE_FILE) -> None:
    """.env 不存在时，从 .env.example 复制一份。"""
    if os.path.exists(path):
        return
    if os.path.exists(example):
        shutil.copyfile(example, path)
        return
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# DeepSeek 配置\n")
        fh.write(f"OPENAI_API_KEY={PLACEHOLDER}\n")
        fh.write("OPENAI_BASE_URL=https://api.deepseek.com\n")
        fh.write("OPENAI_MODEL=deepseek-flash\n")


def upsert_env_var(path: str, name: str, value: str) -> None:
    """把 name=value 写进 .env：已有同名行就替换，没有就追加；保留注释和其它行。"""
    lines: list[str] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    entry = f"{name}={value}"
    replaced = False
    for index, line in enumerate(lines):
        if line.strip().startswith(f"{name}="):
            lines[index] = entry
            replaced = True
            break
    if not replaced:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(entry)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def verify_key(key: str, *, timeout: float = 30.0) -> tuple[bool, str]:
    """真实发一条最短请求，确认 Key 能扣费、能出结果。"""
    client = ChatClient.from_env()
    client.api_key = key
    client.timeout = timeout
    try:
        result = client.chat_with_usage(
            [{"role": "user", "content": "只回复两个字母：OK"}], max_tokens=8
        )
        return True, (
            f"验证成功：模型 {result.model}，{result.latency_ms:.0f}ms，"
            f"{result.total_tokens} tokens，回复 {result.content.strip()[:20]!r}"
        )
    except ApiError as exc:
        return False, str(exc)


# ---------------------------------------------------------------- 命令行入口


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="交互式填写 DeepSeek API Key（自动校验 + 真实试调 + 写入 .env）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--no-verify", action="store_true", help="不真实调用，只写入 .env")
    parser.add_argument("--force", action="store_true", help="验证失败也强行写入")
    parser.add_argument("--env-file", default=ENV_FILE, help="要写入的配置文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    setup_stdout()
    args = build_parser().parse_args(argv)

    print("DeepSeek API Key 设置")
    print("=" * 56)
    print("申请地址：https://platform.deepseek.com/api_keys （注册 → 充值 → 创建，Key 只显示一次）")
    print("提示：粘贴时不会显示内容，这是正常的安全设置。\n")

    try:
        raw = getpass.getpass("请粘贴 DeepSeek API Key（输入不回显，直接回车取消）: ")
    except (KeyboardInterrupt, EOFError):
        print("\n[已取消]", file=sys.stderr)
        return 2

    key = normalize_key(raw)
    problem = key_format_problem(key)
    if problem:
        print(f"\n[×] {problem}", file=sys.stderr)
        return 2
    print(f"[✓] Key 格式看起来没问题（{mask_key(key)}）")

    if args.no_verify:
        print("[跳过] 按 --no-verify 要求，不做真实调用。")
    else:
        print("[...] 正在真实调用一次（几分钱），确认 Key 可用……")
        ok, detail = verify_key(key)
        if ok:
            print(f"[✓] {detail}")
        else:
            print(f"[×] 验证失败：{detail}", file=sys.stderr)
            if not args.force:
                print(
                    "\n没有写入 .env。常见原因：Key 复制不完整、账户没充值、网络/代理问题。\n"
                    "确认 Key 没问题后可以重跑本脚本，或加 --force 先写入再说。",
                    file=sys.stderr,
                )
                return 1

    ensure_env_file(args.env_file)
    upsert_env_var(args.env_file, "OPENAI_API_KEY", key)
    print(f"\n[✓] 已写入 {args.env_file}（该文件已在 .gitignore 中，不会提交）")
    print("\n接下来可以：")
    print("  python doctor.py --live        # 复查一遍环境")
    print('  python main.py "你好"          # 单条调用')
    print("  python batch.py examples/prompts.txt --out results/run1.jsonl   # 批量跑")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
