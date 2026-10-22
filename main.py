#!/usr/bin/env python3
"""最小可运行示例：调用 OpenAI 兼容的 Chat API。

用法：
  python main.py --mock "你好"          # 不用 Key、不联网，完整跑通链路
  python main.py "用一句话介绍 Python"   # 调用 .env 里配置的真实 API
  python main.py --stream "写首五言绝句"  # 流式输出

配置来源（优先级从高到低）：命令行参数 > 环境变量 > .env 文件 > 内置默认值
"""

from __future__ import annotations

import argparse
import os
import sys

from api_client import ApiError, ChatClient, build_messages

ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def setup_stdout() -> None:
    """Windows 控制台默认 GBK：非交互输出(管道/重定向)转 UTF-8，交互时只容错不崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty() and (stream.encoding or "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
            else:
                stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def load_dotenv(path: str = ENV_FILE) -> None:
    """极简 .env 加载器：KEY=VALUE，忽略注释和空行，不覆盖已有环境变量。"""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


# 这些是 .env 模板里的占位值，视为"还没填 Key"
PLACEHOLDER_MARKERS = ("在这里替换", "your-key", "your_key", "yourkey", "sk-xxx", "sk-你的")


def is_placeholder_key(key: str) -> bool:
    if not key:
        return True
    lowered = key.lower()
    return any(marker in lowered or marker in key for marker in PLACEHOLDER_MARKERS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="调用 OpenAI 兼容 Chat API 的最小示例",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("prompt", help="要发送给模型的用户消息")
    parser.add_argument("--mock", action="store_true", help="使用本地 mock 服务，不需要 API Key")
    parser.add_argument("--stream", action="store_true", help="流式打印回复")
    parser.add_argument("--system", help="system 人设提示词")
    parser.add_argument("--model", help="覆盖模型名，如 deepseek-flash / deepseek-v4-pro / gpt-4o-mini")
    parser.add_argument("--base-url", help="覆盖接口地址，如 https://api.openai.com/v1")
    parser.add_argument("--key", help="覆盖 API Key（不建议写在命令行，会进历史记录）")
    parser.add_argument("--temperature", type=float, default=0.7, help="随机性 0~2")
    return parser


def configure(args: argparse.Namespace):
    """根据参数/环境变量组装客户端；--mock 时指向本地 mock 服务。"""
    if args.mock:
        from mock_server import MockServer

        server = MockServer(expected_key="sk-mock").start()
        client = ChatClient(
            api_key="sk-mock",
            base_url=server.base_url,
            model="mock-model",
            max_retries=1,
        )
        print(f"[mode] 离线 mock 演示，base_url={server.base_url}", file=sys.stderr)
        return client, server

    client = ChatClient.from_env()
    if args.key:
        client.api_key = args.key
    if args.base_url:
        client.base_url = args.base_url.rstrip("/")
    if args.model:
        client.model = args.model
    return client, None


def print_setup_hint() -> None:
    print(
        "\n还没填 API Key（.env 里目前是占位符）。只需要改一行：\n"
        "  1) 打开配置：notepad .env\n"
        "  2) 把 OPENAI_API_KEY=sk-在这里替换成你的真实Key\n"
        "     换成你在 https://platform.deepseek.com/api_keys 申请的 Key\n"
        "  3) 重新运行：run.cmd \"你好\"\n\n"
        "  不填 Key 也能先看效果：run.cmd --mock \"你好\"\n",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    setup_stdout()
    load_dotenv()
    args = build_parser().parse_args(argv)
    client, mock_server = configure(args)

    try:
        if is_placeholder_key(client.api_key):
            print_setup_hint()
            return 2

        print(f"[model] {client.model}  [endpoint] {client.base_url}", file=sys.stderr)
        messages = build_messages(args.system, args.prompt)

        if args.stream:
            for piece in client.chat_stream(messages, temperature=args.temperature):
                sys.stdout.write(piece)
                sys.stdout.flush()
            sys.stdout.write("\n")
        else:
            print(client.chat(messages, temperature=args.temperature))
        return 0
    except ApiError as exc:
        print(f"\n[调用失败] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[已取消]", file=sys.stderr)
        return 130
    finally:
        if mock_server is not None:
            mock_server.shutdown()
            mock_server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
