"""最小可用的 OpenAI 兼容 Chat API 客户端（只用 Python 标准库，零依赖）。

兼容平台（换 OPENAI_BASE_URL + OPENAI_API_KEY + OPENAI_MODEL 即可）：
  - DeepSeek   https://api.deepseek.com
  - OpenAI     https://api.openai.com/v1
  - 通义千问   https://dashscope.aliyuncs.com/compatible-mode/v1
  - 月之暗面   https://api.moonshot.cn/v1
  - 本地 Ollama http://127.0.0.1:11434/v1

关键点：鉴权头 Bearer、超时、429/5xx 指数退避重试、HTTP 错误转成可读异常、
流式 SSE 解析。业务代码只需要调用 chat() 或 chat_stream()。
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from typing import Iterator

DEFAULT_BASE_URL = "https://api.deepseek.com"
# DeepSeek 官方当前模型名：deepseek-flash（V4.1-Flash，便宜快）/ deepseek-v4-pro（更强）
# 旧的 deepseek-chat / deepseek-reasoner 已不是当前模型名，不要再用
DEFAULT_MODEL = "deepseek-flash"

# 值得重试的状态码：限流 + 服务端临时故障
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    """API 调用失败。status 为 HTTP 状态码，网络错误时为 None。"""

    def __init__(self, message: str, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


def _friendly_message(status: int | None, body: str) -> str:
    """把 HTTP 状态码翻译成能直接照做的中文提示。"""
    if status is None:
        return "网络错误：连不上接口，请检查网络、代理或 base_url。"
    hint = {
        400: "请求参数不对（模型名写错？temperature/max_tokens 超范围？）",
        401: "API Key 无效或没设置（检查环境变量 OPENAI_API_KEY）。",
        402: "账户余额不足，请到平台充值。",
        403: "没有访问该接口/模型的权限。",
        404: "接口地址不对（检查 OPENAI_BASE_URL，注意是否缺 /v1）。",
        413: "请求体太大（上下文/输入过长）。",
        422: "请求体格式错误。",
        429: "触发限流，降低频率或稍后重试。",
        500: "服务端内部错误，稍后重试。",
        502: "网关错误，稍后重试。",
        503: "服务暂不可用，稍后重试。",
    }.get(status, "请求失败")
    detail = ""
    try:
        payload = json.loads(body)
        detail = payload.get("error", {}).get("message", "") if isinstance(payload, dict) else ""
    except (ValueError, AttributeError):
        detail = body[:300]
    return f"HTTP {status}：{hint}" + (f"\n服务端返回：{detail}" if detail else "")


@dataclass
class ChatResult:
    """一条完整的非流式回复，附带用量和耗时，批量跑的时候用来记账。"""

    content: str
    usage: dict
    model: str
    latency_ms: float

    @property
    def prompt_tokens(self) -> int:
        return int(self.usage.get("prompt_tokens") or 0)

    @property
    def completion_tokens(self) -> int:
        return int(self.usage.get("completion_tokens") or 0)

    @property
    def total_tokens(self) -> int:
        return int(self.usage.get("total_tokens") or 0)


@dataclass
class ChatClient:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: float = 60.0
    max_retries: int = 2
    backoff: float = 1.0

    # ---------- 构造 ----------

    @classmethod
    def from_env(cls) -> "ChatClient":
        """从环境变量读取配置，兼容 OPENAI_* / DEEPSEEK_* 两种命名。"""
        api_key = (
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("API_KEY")
            or ""
        ).strip()
        base_url = (
            os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("DEEPSEEK_BASE_URL")
            or DEFAULT_BASE_URL
        ).strip().rstrip("/")
        model = (os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL).strip()
        return cls(api_key=api_key, base_url=base_url, model=model)

    # ---------- 公开接口 ----------

    def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        """一次性返回完整回复文本。"""
        return self.chat_with_usage(
            messages, temperature=temperature, max_tokens=max_tokens
        ).content

    def chat_with_usage(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """一次性返回完整回复 + token 用量 + 耗时（做批量评估/成本核算用）。"""
        payload = self._payload(messages, temperature, max_tokens, stream=False)
        started = time.perf_counter()
        with closing(self._post_with_retries(payload)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        latency_ms = (time.perf_counter() - started) * 1000.0
        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError):
            raise ApiError(f"响应格式不符合预期：{raw[:500]}") from None
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return ChatResult(
            content=content,
            usage=usage,
            model=str(data.get("model") or self.model),
            latency_ms=latency_ms,
        )

    def chat_stream(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """流式返回增量文本（SSE），适合边生成边打印。"""
        payload = self._payload(messages, temperature, max_tokens, stream=True)
        resp = self._post_with_retries(payload)
        try:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if isinstance(chunk, dict) and chunk.get("error"):
                    raise ApiError(f"流式返回错误：{chunk['error']}")
                choices = chunk.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        yield piece
        finally:
            resp.close()

    # ---------- 内部实现 ----------

    def _endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "minimal-chat-client/1.0",
        }

    def _payload(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int | None,
        *,
        stream: bool,
    ) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        return payload

    def _post(self, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint(), data=data, headers=self._headers(), method="POST"
        )
        return urllib.request.urlopen(req, timeout=self.timeout)

    def _post_with_retries(self, payload: dict):
        """带指数退避的重试；4xx（除 408/409/429）不重试。"""
        attempt = 0
        while True:
            try:
                return self._post(payload)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                err = ApiError(_friendly_message(exc.code, body), status=exc.code, body=body)
                if exc.code in RETRYABLE_STATUS and attempt < self.max_retries:
                    time.sleep(self.backoff * (2**attempt))
                    attempt += 1
                    continue
                raise err from None
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
                reason = getattr(exc, "reason", exc)
                err = ApiError(f"网络错误：{reason}")
                if attempt < self.max_retries:
                    time.sleep(self.backoff * (2**attempt))
                    attempt += 1
                    continue
                raise err from None


def build_messages(system_prompt: str | None, user_text: str) -> list[dict]:
    """组装消息列表：可选的 system 人设 + 单轮 user 输入。"""
    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_text})
    return messages
