#!/usr/bin/env python3
"""环境体检：一次检查「DeepSeek 这条路」通不通，并给出下一步该做什么。

检查项：
  1. Python 版本
  2. .env 是否存在、KEY 是否还停在占位符
  3. 接口地址 / 模型名是否合理
  4. 接口能不能连上（GET /models；401 也算「连得上」，说明只是 Key 问题）
  5. 可选 --live：真实发一条最短请求，确认 Key 能扣费、能出结果

用法：
  python doctor.py                # 常规体检（不花钱）
  python doctor.py --live         # 真发一条请求验证 Key（几分钱）
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request

from api_client import DEFAULT_MODEL, ApiError, ChatClient
from main import ENV_FILE, is_placeholder_key, load_dotenv, setup_stdout


# ---------------------------------------------------------------- 单项检查


def check_python() -> tuple[bool, str]:
    version = sys.version_info
    ok = version >= (3, 10)
    return ok, f"Python {version.major}.{version.minor}.{version.micro}" + (
        "" if ok else "（需要 3.10+）"
    )


def check_env_file(path: str = ENV_FILE) -> tuple[bool, str]:
    if os.path.exists(path):
        return True, f"配置文件存在：{path}"
    return False, f"没有找到 {path}（复制 .env.example 即可）"


def check_key(key: str) -> tuple[bool, str]:
    if not key:
        return False, "没有设置 API Key（.env 里的 OPENAI_API_KEY）"
    if is_placeholder_key(key):
        return False, "API Key 还是占位符，打开 .env 换成真实 Key"
    masked = key[:6] + "…" + key[-4:] if len(key) > 12 else "***"
    return True, f"API Key 已填写（{masked}）"


def check_base_url(base_url: str) -> tuple[bool, str]:
    if not base_url.startswith(("http://", "https://")):
        return False, f"接口地址应以 http(s):// 开头：{base_url}"
    if base_url.rstrip("/").endswith("/chat/completions"):
        return False, "接口地址应填到 base（如 https://api.deepseek.com），不要带 /chat/completions"
    return True, f"接口地址：{base_url}"


def check_endpoint(base_url: str, api_key: str, timeout: float = 10.0) -> dict:
    """GET {base_url}/models。返回 reachable / status / authenticated / detail。"""
    url = base_url.rstrip("/") + "/models"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    has_key = bool(api_key) and not is_placeholder_key(api_key)
    if has_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            response.read(2048)
            return {
                "reachable": True,
                "status": status,
                "authenticated": True,
                "ok": True,
                "detail": f"HTTP {status}，{base_url} 可访问、鉴权通过",
            }
    except urllib.error.HTTPError as exc:
        exc.read()
        status = exc.code
        if status == 401:
            detail = "HTTP 401：接口连得上，但 Key 无效或没带 Key"
            return {"reachable": True, "status": status, "authenticated": False,
                    "ok": False, "detail": detail}
        if status == 403:
            return {"reachable": True, "status": status, "authenticated": False,
                    "ok": False, "detail": "HTTP 403：接口连得上，但没有访问该模型的权限"}
        if status == 404:
            return {"reachable": True, "status": status, "authenticated": has_key,
                    "ok": False, "detail": "HTTP 404：接口连得上，但路径不对（检查 base_url 是否缺 /v1）"}
        return {"reachable": True, "status": status, "authenticated": has_key,
                "ok": False, "detail": f"HTTP {status}：接口连得上，但返回异常"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return {"reachable": False, "status": None, "authenticated": False,
                "ok": False, "detail": f"连不上 {base_url}：{reason}（检查网络 / 代理 / base_url）"}


def check_live(client: ChatClient) -> tuple[bool, str]:
    """真实发一条最短请求，验证 Key 能扣费、能出结果。"""
    try:
        result = client.chat_with_usage(
            [{"role": "user", "content": "只回复两个字母：OK"}], max_tokens=16
        )
        return True, (
            f"真实调用成功：模型 {result.model}，{result.latency_ms:.0f}ms，"
            f"{result.total_tokens} tokens，回复：{result.content.strip()[:40]!r}"
        )
    except ApiError as exc:
        return False, f"真实调用失败：{exc}"


# ---------------------------------------------------------------- 主流程


def section(title: str) -> None:
    print(f"\n{title}")


def report(ok: bool, detail: str, *, skipped: bool = False) -> None:
    mark = "✓" if ok else ("-" if skipped else "✗")
    print(f"  [{mark}] {detail}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="体检：Python / .env / Key / 接口连通性 / 可选真实调用",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--live", action="store_true", help="真实发一条最短请求验证 Key（会花几分钱）")
    parser.add_argument("--timeout", type=float, default=10.0, help="网络检查超时（秒）")
    parser.add_argument("--model", help="覆盖模型名")
    parser.add_argument("--base-url", help="覆盖接口地址")
    return parser


def main(argv: list[str] | None = None) -> int:
    setup_stdout()
    load_dotenv()
    args = build_parser().parse_args(argv)

    print("DeepSeek 环境体检")
    print("=" * 56)

    python_ok, python_detail = check_python()
    env_ok, env_detail = check_env_file()
    client = ChatClient.from_env()
    if args.model:
        client.model = args.model
    if args.base_url:
        client.base_url = args.base_url.rstrip("/")

    key_ok, key_detail = check_key(client.api_key)
    url_ok, url_detail = check_base_url(client.base_url)

    section("基础环境")
    report(python_ok, python_detail)
    report(env_ok, env_detail)
    report(key_ok, key_detail, skipped=not key_ok)
    report(url_ok, url_detail)
    report(True, f"模型：{client.model}" + ("" if client.model == DEFAULT_MODEL else f"（默认 {DEFAULT_MODEL}）"))

    section("接口连通性（不花钱）")
    net = check_endpoint(client.base_url, client.api_key, timeout=args.timeout)
    report(net["reachable"], net["detail"], skipped=False)
    if net["reachable"] and net["status"] == 200:
        report(True, "鉴权通过，Key 可用于该接口")

    live_ok = False
    if args.live:
        section("真实调用（会花几分钱）")
        if not (key_ok and net["reachable"]):
            report(False, "先解决上面的 Key / 网络问题，再跑 --live", skipped=True)
        else:
            live_ok, live_detail = check_live(client)
            report(live_ok, live_detail)

    section("结论与下一步")
    if not key_ok:
        print("  还差一步：打开 .env，把 OPENAI_API_KEY 换成真实 Key")
        print("  申请地址：https://platform.deepseek.com/api_keys")
        print("  填完再跑：python doctor.py --live")
        return 2
    if not net["reachable"]:
        print("  网络不通：检查代理 / 防火墙，或把 OPENAI_BASE_URL 换成可达的地址")
        return 1
    if net["status"] in (401, 403):
        print("  Key 或权限有问题：确认 Key 没写错、没带引号，且账户已开通对应模型")
        return 1
    if args.live and not live_ok:
        print("  接口能连、Key 也在，但真实调用失败：看上面的报错（常见是余额不足 402）")
        return 1
    print("  一切就绪，可以直接跑模型了：")
    print('    python main.py "你好" --stream')
    print("    python batch.py examples/prompts.txt --out results/run1.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
