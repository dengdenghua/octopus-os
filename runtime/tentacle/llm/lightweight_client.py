"""LightweightLlmClient —— 裸 HTTP 调 OpenAI 兼容 LLM API.

与 Echo Mobile 端 ``LightweightLlmClient.kt`` 完全对称：

- **零外部依赖**：用标准库 ``urllib.request``，不引入 ``requests`` / ``httpx``
- **OpenAI 兼容**：DeepSeek / Qwen / GLM / Ollama / vLLM 全部可对接
- **同步阻塞**：适合母体 Worker 直接调用，ReAct 循环也只在一个线程里跑
- **可注入 transport**：测试时换成 ``FakeTransport`` 不发真请求

设计哲学见 :ref:`ADR-008 <adr-008>`。本类只解决"HTTP 收发 + 解析响应"，
不解决"如何循环"（那是 :mod:`react_loop`）和"如何决策"（那是 :mod:`skill_manifest`）。
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from .chat_types import (
    ChatMessage,
    FinishReason,
    LlmResponse,
    SkillSpec,
    TokenUsage,
    ToolCall,
)

logger = logging.getLogger(__name__)


# ── Transport 协议（可注入，便于测试） ──────────────────────────


class Transport(Protocol):
    """HTTP transport 协议 —— 任何"接受 url+headers+body，返回 dict"的对象都行."""

    def post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]: ...


class UrllibTransport:
    """默认 transport：标准库 ``urllib.request``."""

    def post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 — audited HTTP LLM endpoint
            payload = resp.read().decode("utf-8")
        return json.loads(payload)


# ── 配置 ──────────────────────────────────────────────────────


@dataclass(slots=True)
class LlmConfig:
    """LLM 配置 —— 一次构造，多处复用.

    预设工厂方法覆盖主流 OpenAI 兼容供应商：

    - :meth:`deepSeek`     DeepSeek（默认推荐，便宜/中文强）
    - :meth:`qwen`         阿里 Qwen（中文强）
    - :meth:`openAi`       OpenAI 官方
    - :meth:`ollama`       本地 Ollama（offline 开发）
    """

    base_url: str
    api_key: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 2048
    timeout_s: float = 60.0

    # ── 预设工厂 ────────────────────────────────────────────

    @classmethod
    def deepSeek(cls, api_key: str | None = None, model: str = "deepseek-chat") -> LlmConfig:  # noqa: N802
        return cls(
            base_url="https://api.deepseek.com/v1",
            api_key=api_key or os.environ.get("DEEPSEEK_API_KEY", ""),
            model=model,
        )

    @classmethod
    def qwen(cls, api_key: str | None = None, model: str = "qwen-plus") -> LlmConfig:
        return cls(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key=api_key or os.environ.get("QWEN_API_KEY", ""),
            model=model,
        )

    @classmethod
    def openAi(cls, api_key: str | None = None, model: str = "gpt-4o-mini") -> LlmConfig:  # noqa: N802
        return cls(
            base_url="https://api.openai.com/v1",
            api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
            model=model,
        )

    @classmethod
    def ollama(
        cls, base_url: str = "http://localhost:11434/v1", model: str = "qwen2.5:7b"
    ) -> LlmConfig:
        # Ollama OpenAI 兼容模式不需要 key
        return cls(base_url=base_url, api_key="ollama", model=model)

    @classmethod
    def glm(cls, api_key: str | None = None, model: str = "glm-4-flash") -> LlmConfig:
        return cls(
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key=api_key or os.environ.get("GLM_API_KEY", ""),
            model=model,
        )

    def require_key(self) -> None:
        if not self.api_key or self.api_key == "ollama":
            return
        if not self.api_key.strip():
            raise ValueError(
                f"LLM api_key missing for model={self.model!r}. Set env var or pass explicitly."
            )


# ── 客户端 ────────────────────────────────────────────────────


class LightweightLlmClient:
    """极简 OpenAI 兼容 LLM 客户端.

    用法::

        client = LightweightLlmClient(LlmConfig.deepSeek())
        resp = client.chat(
            messages=[ChatMessage.user("打开微信，发送 hello")],
            skills=[SkillSpec(name="android.tap", description="点击", parameters={...})],
        )
        if resp.has_tool_calls:
            for tc in resp.tool_calls:
                ...  # 调 Tentacle.execute

    线程安全：单实例可被多线程共用（urllib 是线程安全的）。
    异步：本类同步；如需 async，请用 ``asyncio.to_thread(client.chat, ...)`` 包装。
    """

    def __init__(
        self,
        config: LlmConfig,
        transport: Transport | None = None,
    ) -> None:
        self.config = config
        self.transport: Transport = transport or UrllibTransport()
        config.require_key()

    # ── 主入口 ──────────────────────────────────────────────

    def chat(
        self,
        messages: list[ChatMessage],
        skills: list[SkillSpec] | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LlmResponse:
        """发起一次 Chat Completions 请求.

        Args:
            messages: 对话历史
            skills: 工具规格（OpenAI tools 格式）
            temperature: 覆盖 config 默认值
            max_tokens: 覆盖 config 默认值

        Returns:
            :class:`LlmResponse`，含 content / tool_calls / usage
        """
        body = self._build_request_body(
            messages,
            skills or [],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.config.api_key}"}

        start = time.time()
        try:
            payload = self.transport.post(url, headers, body)
        except urllib.error.HTTPError as e:
            # OpenAI 风格的错误体是 JSON；尝试解析
            try:
                err = json.loads(e.read().decode("utf-8"))
            except Exception:
                err = {"error": {"message": str(e)}}
            logger.error("LLM HTTP %s: %s", e.code, err)
            return LlmResponse(
                content=err.get("error", {}).get("message", str(e)),
                finish_reason=FinishReason.ERROR,
                model=self.config.model,
                latency_ms=_ms_since(start),
            )
        except urllib.error.URLError as e:
            logger.error("LLM URL error: %s", e)
            return LlmResponse(
                content=f"network error: {e}",
                finish_reason=FinishReason.ERROR,
                model=self.config.model,
                latency_ms=_ms_since(start),
            )
        return self._parse_response(payload, start)

    # ── 请求 / 响应构造 ─────────────────────────────────────

    def _build_request_body(
        self,
        messages: list[ChatMessage],
        skills: list[SkillSpec],
        *,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        # 1. messages
        msg_dicts: list[dict[str, Any]] = []
        for m in messages:
            d = m.to_dict()
            # assistant 消息携带 tool_calls 时补上
            if m.role == "assistant" and m.tool_calls():
                d["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in m.tool_calls()
                ]
            msg_dicts.append(d)

        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": msg_dicts,
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": self.config.max_tokens if max_tokens is None else max_tokens,
        }
        # 2. tools
        if skills:
            body["tools"] = [s.to_openai_tool() for s in skills]
            body["tool_choice"] = "auto"
        return body

    def _parse_response(self, payload: dict[str, Any], start: float) -> LlmResponse:
        """把 OpenAI 风格响应解析成 :class:`LlmResponse`."""
        try:
            choice = payload["choices"][0]
        except (KeyError, IndexError):
            logger.error("LLM response missing choices: %s", payload)
            return LlmResponse(
                content="malformed response: no choices",
                finish_reason=FinishReason.ERROR,
                latency_ms=_ms_since(start),
            )

        message = choice.get("message", {})
        content = message.get("content") or ""
        raw_tool_calls = message.get("tool_calls") or []

        tool_calls: list[ToolCall] = []
        for tc in raw_tool_calls:
            fn = tc.get("function", {})
            args_str = fn.get("arguments", "{}")
            # DeepSeek / Qwen 偶发返回空字符串，做一次兜底
            if not args_str:
                args_str = "{}"
            try:
                args = json.loads(args_str) if isinstance(args_str, str) else args_str
            except json.JSONDecodeError:
                logger.warning("Bad tool args JSON: %r", args_str)
                args = {"_raw": args_str}
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=fn.get("name", ""),
                    arguments=args,
                )
            )

        finish_raw = choice.get("finish_reason", "stop")
        try:
            finish_reason = FinishReason(finish_raw)
        except ValueError:
            finish_reason = FinishReason.STOP

        usage_data = payload.get("usage", {}) or {}
        usage = TokenUsage(
            prompt_tokens=int(usage_data.get("prompt_tokens", 0)),
            completion_tokens=int(usage_data.get("completion_tokens", 0)),
            total_tokens=int(usage_data.get("total_tokens", 0)),
        )

        return LlmResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            model=payload.get("model", self.config.model),
            latency_ms=_ms_since(start),
        )

    # ── 便利方法 ────────────────────────────────────────────

    def quick(self, prompt: str, system: str | None = None) -> str:
        """一次性的文本问答（不传 skills）."""
        msgs: list[ChatMessage] = []
        if system:
            msgs.append(ChatMessage.system(system))
        msgs.append(ChatMessage.user(prompt))
        resp = self.chat(msgs)
        return resp.content


# ── Fake Transport（测试用） ──────────────────────────────────


class FakeTransport:
    """测试用 transport：预设响应序列，按调用顺序返回.

    用法::

        fake = FakeTransport([
            {"choices": [{"message": {"content": "hi", "tool_calls": [...]}, "finish_reason": "tool_calls"}],
             "usage": {...}, "model": "test"},
            {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}], ...},
        ])
        client = LightweightLlmClient(LlmConfig.deepSeek("test"), transport=fake)
    """

    def __init__(self, responses: Iterable[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"url": url, "headers": headers, "body": body})
        if not self._responses:
            raise RuntimeError("FakeTransport: no more preset responses")
        return self._responses.pop(0)


# ── 工具函数 ──────────────────────────────────────────────────


def _ms_since(start: float) -> int:
    return int((time.time() - start) * 1000)
