# ruff: noqa: E402 — module-level imports below are intentionally late

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator
from typing import Any

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

from .actor_context import current_actor
from .models import CostEntry, ModelRequest, ModelResponse, ModelRouter, ModelStreamEvent


class OctCredentialsRequired(RuntimeError):
    pass


from .provider import Provider, ProviderCapabilities


class OctModelRouter(Provider, ModelRouter):
    """走 EchoAI 账号网关(api.echo-age.com)/v1/chat/completions 的模型路由。

    用 OctLink 里存的网关 JWT 做 ``Authorization: Bearer``,网关侧按 token 用量扣积分
    → agent 的 LLM 用量计入统一积分池（会员 BYO 在网关侧免扣）。
    """

    provider_name = "oct"
    capabilities = ProviderCapabilities(
        supports_vision=True,
        supports_tool_use=True,
        supports_streaming=True,
        supports_prompt_cache=False,
        supports_structured_output=False,
        default_model="qwen3.5-flash",
        pricing_hint="low",
    )

    def __init__(
        self,
        *,
        link_store: Any,
        base_url: str = "https://api.echo-age.com",
        default_model: str = "qwen3.5-flash",
        timeout_seconds: float = 120.0,
        http_client: Any = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url required")
        self.link_store = link_store
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.timeout_seconds = timeout_seconds
        self._http = http_client

    def _resolve_actor(self) -> str:
        actor = current_actor.get()
        if actor:
            return actor
        if os.environ.get("ECHO_DESKTOP") == "1":
            try:
                actor_ids = self.link_store.all_actor_ids()
            except (OSError, RuntimeError):  # noqa: BLE001
                actor_ids = []
            if len(actor_ids) == 1:
                return actor_ids[0]
        raise OctCredentialsRequired("no current_actor set · OctModelRouter 需要登录态")

    def _link(self) -> tuple[str, Any]:
        actor = self._resolve_actor()
        link = self.link_store.get(actor)
        if link is None:
            raise OctCredentialsRequired(f"actor {actor!r} 没有绑定 oct 账号")
        if getattr(link, "token_invalid", False):
            raise OctCredentialsRequired(f"actor {actor!r} 的 oct 登录已过期 · 请重新登录")
        return actor, link

    def _mark_dead(self, actor: str) -> None:
        """收到网关 401 时标记 link 失效 → 下回合 OctFallbackRouter 自动降级到自配模型。"""
        with contextlib.suppress(Exception):
            self.link_store.mark_token_invalid(actor, "TOKEN_EXPIRED")

    def _build_payload(self, request: ModelRequest, *, stream: bool) -> dict[str, Any]:
        """call 与 call_stream 共享:messages 经 _messages_to_openai、带图片、带 tools。

        统一构造杜绝两条路径漂移(曾出现 call_stream 丢 tools/图片导致流式 native 工具循环空转)。
        """
        from .openai_router import _messages_to_openai

        messages = _messages_to_openai(list(request.messages or []))
        imgs = list(getattr(request, "images_b64", []) or [])
        if imgs:
            for i in range(len(messages) - 1, -1, -1):
                if messages[i]["role"] == "user":
                    text = messages[i]["content"]
                    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
                    for b64 in imgs:
                        content.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64}"},
                            }
                        )
                    messages[i] = {"role": "user", "content": content}
                    break

        payload: dict[str, Any] = {
            "model": request.model or self.default_model,
            "messages": messages,
            "stream": stream,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in request.tools
            ]
            # ``required`` is the agentic-loop escape hatch for a model that
            # answered the previous round with prose only: it removes
            # text-only as a decode option for exactly one round. See
            # ``_LoopState.zero_action_rounds``.
            payload["tool_choice"] = "required" if request.require_tool_use else "auto"
        return payload

    def call(self, request: ModelRequest) -> ModelResponse:
        actor, link = self._link()
        model = request.model or self.default_model
        payload = self._build_payload(request, stream=False)
        headers = {"Authorization": f"Bearer {link.oct_token}", "Content-Type": "application/json"}
        client = self._http or (httpx if HTTPX_AVAILABLE else None)
        if client is None:
            raise RuntimeError("httpx 未安装 · 无法调 oct 网关 · 注入 http_client")

        url = f"{self.base_url}/v1/chat/completions"
        try:
            r = client.post(url, json=payload, headers=headers, timeout=self.timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"oct gateway unreachable: {exc}") from exc
        status = getattr(r, "status_code", 0)
        text = getattr(r, "text", "") or ""
        if status == 401:
            self._mark_dead(actor)
            raise OctCredentialsRequired("oct 登录已过期 · 请重新登录")
        if status == 402:
            raise RuntimeError("oct 积分不足 · 请充值或开通会员")
        if status >= 400:
            raise RuntimeError(f"oct gateway HTTP {status}: {text[:500]}")
        try:
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"oct gateway non-JSON: {text[:200]}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"oct response missing choices: {str(data)[:200]}")
        content = choices[0].get("message", {}).get("content") or choices[0].get("text") or ""
        from .models import ToolCall

        raw_calls = choices[0].get("message", {}).get("tool_calls") or []
        tool_calls: list[ToolCall] = []
        for tc in raw_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            args_raw = fn.get("arguments") or ""
            try:
                args = json.loads(args_raw) if args_raw else {}
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(
                ToolCall(
                    id=str(tc.get("id") or ""),
                    name=str(fn.get("name") or ""),
                    input=args if isinstance(args, dict) else {},
                )
            )
        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)

        return ModelResponse(
            text=content,
            tool_calls=tool_calls,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            cost=CostEntry(tokens_in=prompt_tokens, tokens_out=completion_tokens, usd=0.0),
            model=model,
            provider="oct",
        )

    def call_stream(self, request: ModelRequest) -> Iterator[ModelStreamEvent]:
        from .openai_compat_stream import iter_openai_sse

        actor, link = self._link()
        model = request.model or self.default_model
        payload = self._build_payload(request, stream=True)
        headers = {"Authorization": f"Bearer {link.oct_token}", "Content-Type": "application/json"}
        client = self._http or (httpx if HTTPX_AVAILABLE else None)
        if client is None:
            yield from super().call_stream(request)
            return

        url = f"{self.base_url}/v1/chat/completions"
        try:
            with client.stream(
                "POST", url, json=payload, headers=headers, timeout=self.timeout_seconds
            ) as r:
                status = getattr(r, "status_code", 0)
                if status == 401:
                    self._mark_dead(actor)
                    raise OctCredentialsRequired("oct 登录已过期 · 请重新登录")
                if status == 402:
                    raise RuntimeError("oct 积分不足 · 请充值或开通会员")
                if status >= 400:
                    raise RuntimeError(f"oct gateway HTTP {status}")
                yield from iter_openai_sse(r, model=model, provider="oct")
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"oct stream failed: {exc}") from exc


class OctFallbackRouter(ModelRouter):
    """actor 感知的 dispatcher fallback。

    当前 actor 已绑定有效 oct 账号 → 走 OctModelRouter(网关计费,LLM 用量计入统一积分池);
    否则(guest / 未登录 / 无 link)→ 走自配模型 fallback。**这样不会回退 P0(31b7900ba)**
    修掉的"登录门控 fallback 把 guest 卡死"那个 bug:guest 永远走 self_router,不碰登录门控的网关。
    """

    provider_name = "oct"

    def __init__(
        self, *, oct_router: OctModelRouter, self_router: ModelRouter, link_store: Any
    ) -> None:
        self._oct = oct_router
        self._self = self_router
        self._link_store = link_store

    @property
    def default_model(self) -> str | None:
        return getattr(self._self, "default_model", None) or getattr(
            self._oct, "default_model", None
        )

    def _pick(self) -> ModelRouter:
        actor = current_actor.get()
        if actor:
            try:
                link = self._link_store.get(actor)
            except Exception:  # noqa: BLE001
                link = None
            if link is not None and not getattr(link, "token_invalid", False):
                return self._oct
        return self._self

    def call(self, request: ModelRequest) -> ModelResponse:
        return self._pick().call(request)

    def call_stream(self, request: ModelRequest) -> Iterator[ModelStreamEvent]:
        yield from self._pick().call_stream(request)
