"""ChatGPT-subscription model transport for the native Echo kernel.

This adapter is the reverse half of the dual-engine bridge:

* Codex kernel -> Echo system models is handled by ``ScopedResponsesProxy``.
* Echo kernel -> ChatGPT login models is handled here.

The native ReAct/planning loop remains in charge.  Only the provider-neutral
``ModelRequest`` crosses the bridge.  ChatGPT credentials are refreshed and
read from the principal-scoped Codex account home inside this process; they are
never accepted from a request, copied into a model context, or exposed to the
frontend.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import stat
import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from runtime.adapters.instrumentation import record_gen_ai_cost, trace_stage
from runtime.execution.codex_backend.account import (
    codex_account_home,
    refresh_codex_execution_auth_home,
    resolve_codex_execution_auth_home,
)
from runtime.execution.codex_backend.paths import resolve_codex_state_root
from runtime.platform.models import CostEntry
from runtime.platform.models.llm import (
    Message,
    ModelRequest,
    ModelResponse,
    ModelRouter,
    ModelStreamEvent,
    ToolCall,
)
from runtime.platform.process.session import current_session
from runtime.safety.auth.scope import TenantScope

from .models import DEFAULT_USER_AGENT, LLMResponseFormatError
from .provider import Provider, ProviderCapabilities

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency failure
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]


_CHATGPT_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
_MAX_AUTH_BYTES = 1024 * 1024
_MODEL_PREFIXES = ("chatgpt/", "chatgpt:")


class ChatGPTSubscriptionRouterError(LLMResponseFormatError):
    """A ChatGPT-login model could not be called safely."""


@dataclass(frozen=True, slots=True)
class _ChatGPTCredentials:
    access_token: str = field(repr=False)
    account_id: str


class _CredentialBroker(Protocol):
    def load(self, *, force_refresh: bool = False) -> _ChatGPTCredentials: ...


class ChatGPTSubscriptionCredentialBroker:
    """Server-only view of the current principal's managed Codex login."""

    def load(self, *, force_refresh: bool = False) -> _ChatGPTCredentials:
        scope = _current_tenant_scope()
        state_root = resolve_codex_state_root()
        managed_home = codex_account_home(state_root, scope)
        if force_refresh or (managed_home / "auth.json").is_file():
            try:
                refreshed = _run_async(
                    refresh_codex_execution_auth_home(
                        state_root=state_root,
                        scope=scope,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - hide auth implementation details
                raise ChatGPTSubscriptionRouterError(
                    "ChatGPT 登录凭据刷新失败，请在设置中重新登录 ChatGPT。"
                ) from exc
            if refreshed is not None:
                managed_home = refreshed

        auth_home = resolve_codex_execution_auth_home(
            state_root=state_root,
            scope=scope,
            deployment_mode=_deployment_mode(),
            legacy_source_home=_legacy_source_home(),
            # A local desktop installation has one OS-user trust boundary.
            # Native turns must not depend on the Codex settings panel having
            # pre-seeded this principal's managed account directory first.
            allow_local_principal_inheritance=_deployment_mode() == "local",
        )
        if auth_home is None:
            raise ChatGPTSubscriptionRouterError(
                "当前账号尚未登录 ChatGPT，请先在模型设置中完成 ChatGPT 登录。"
            )
        return _read_credentials(auth_home / "auth.json")


class ChatGPTSubscriptionModelRouter(Provider, ModelRouter):
    """Run ChatGPT-login models while retaining the Echo native kernel."""

    provider_name = "chatgpt_subscription"
    capabilities = ProviderCapabilities(
        supports_vision=True,
        supports_tool_use=True,
        supports_streaming=True,
        supports_prompt_cache=True,
        supports_structured_output=True,
        default_model="gpt-5.6-sol",
        pricing_hint="subscription",
        extra={"billing": "chatgpt_subscription", "engine": "echo_native"},
    )

    def __init__(
        self,
        *,
        credential_broker: _CredentialBroker | None = None,
        client: Any = None,
        responses_url: str = _CHATGPT_RESPONSES_URL,
        timeout_seconds: float = 180.0,
    ) -> None:
        if not HTTPX_AVAILABLE:
            raise ChatGPTSubscriptionRouterError(
                "httpx not installed · install the web runtime dependencies"
            )
        if responses_url != _CHATGPT_RESPONSES_URL and client is None:
            raise ChatGPTSubscriptionRouterError(
                "ChatGPT Responses endpoint override is only allowed with an injected client"
            )
        self.default_model = "gpt-5.6-sol"
        self._credential_broker = credential_broker or ChatGPTSubscriptionCredentialBroker()
        self._client = client
        self._responses_url = responses_url
        self._timeout_seconds = float(timeout_seconds)

    def call(self, request: ModelRequest) -> ModelResponse:
        final: ModelResponse | None = None
        for event in self.call_stream(request):
            if event.type == "done":
                final = event.final
        if final is None:
            raise ChatGPTSubscriptionRouterError(
                "ChatGPT Responses stream ended before response.completed"
            )
        return final

    def call_stream(self, request: ModelRequest) -> Iterator[ModelStreamEvent]:
        model = _upstream_model(request.model or self.default_model)
        payload = _build_responses_payload(request, model=model)
        with trace_stage(
            "eyes.chatgpt_subscription_router.stream",
            **{
                "echo.model": model,
                "echo.provider": self.provider_name,
                "echo.billing": "subscription",
            },
        ) as span:
            credentials = self._credential_broker.load(force_refresh=False)
            response, owned_client = self._open_stream(payload, credentials)
            if response.status_code == 401:
                response.close()
                if owned_client is not None:
                    owned_client.close()
                credentials = self._credential_broker.load(force_refresh=True)
                response, owned_client = self._open_stream(payload, credentials)
            try:
                if response.status_code >= 400:
                    response.read()
                    raise ChatGPTSubscriptionRouterError(
                        _safe_http_error(response.status_code, response.text)
                    )
                final: ModelResponse | None = None
                for event in _iter_responses_sse(response, model=model):
                    if event.type == "done":
                        final = event.final
                    yield event
                if final is None:
                    raise ChatGPTSubscriptionRouterError(
                        "ChatGPT Responses stream ended before response.completed"
                    )
                record_gen_ai_cost(
                    span,
                    system=self.provider_name,
                    model=final.model or model,
                    input_tokens=final.input_tokens,
                    output_tokens=final.output_tokens,
                    usd=0.0,
                )
            finally:
                response.close()
                if owned_client is not None:
                    owned_client.close()

    def _open_stream(
        self,
        payload: Mapping[str, Any],
        credentials: _ChatGPTCredentials,
    ) -> tuple[Any, Any | None]:
        headers = {
            "Authorization": f"Bearer {credentials.access_token}",
            "ChatGPT-Account-ID": credentials.account_id,
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
            "originator": "echo_native",
        }
        client = self._client
        owned_client = None
        if client is None:
            owned_client = httpx.Client(
                timeout=httpx.Timeout(
                    connect=30.0,
                    read=self._timeout_seconds,
                    write=30.0,
                    pool=10.0,
                )
            )
            client = owned_client
        request = client.build_request(
            "POST",
            self._responses_url,
            json=dict(payload),
            headers=headers,
        )
        return client.send(request, stream=True), owned_client


def _current_tenant_scope() -> TenantScope | None:
    session = current_session()
    mode = _deployment_mode()
    if session is None:
        return None if mode == "local" else _missing_scope()
    metadata = session.metadata if isinstance(session.metadata, Mapping) else {}
    tenant = str(metadata.get("tenant_id") or "local").strip() or "local"
    principal = str(session.actor or metadata.get("principal_id") or "local").strip() or "local"
    if mode == "local" and tenant == "local" and principal == "local":
        return None
    return TenantScope(tenant_id=tenant, actor_id=principal)


def _missing_scope() -> None:
    raise ChatGPTSubscriptionRouterError(
        "共享部署中的 ChatGPT 模型调用缺少租户身份，已拒绝使用本机登录。"
    )


def _legacy_source_home() -> Path | None:
    if _deployment_mode() != "local":
        return None
    explicit = str(os.environ.get("ECHO_CODEX_SOURCE_HOME") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        return path.resolve(strict=False) if path.is_absolute() else None
    return (Path.home() / ".codex").resolve(strict=False)


def _deployment_mode() -> str:
    return str(os.environ.get("ECHO_DEPLOYMENT_MODE") or "local").strip().lower()


def _read_credentials(path: Path) -> _ChatGPTCredentials:
    try:
        metadata = path.stat()
    except OSError as exc:
        raise ChatGPTSubscriptionRouterError("ChatGPT 登录凭据不可用，请重新登录。") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size <= 0
        or metadata.st_size > _MAX_AUTH_BYTES
        or metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        raise ChatGPTSubscriptionRouterError("ChatGPT 登录凭据文件权限不安全。")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ChatGPTSubscriptionRouterError("ChatGPT 登录凭据文件无效。") from exc
    tokens = payload.get("tokens") if isinstance(payload, Mapping) else None
    if not isinstance(tokens, Mapping):
        raise ChatGPTSubscriptionRouterError("当前登录不是 ChatGPT 账号授权。")
    access_token = tokens.get("access_token")
    account_id = tokens.get("account_id")
    if not isinstance(access_token, str) or not access_token.strip():
        raise ChatGPTSubscriptionRouterError("ChatGPT 登录访问令牌缺失，请重新登录。")
    if not isinstance(account_id, str) or not account_id.strip():
        raise ChatGPTSubscriptionRouterError("ChatGPT 登录账号标识缺失，请重新登录。")
    return _ChatGPTCredentials(access_token.strip(), account_id.strip())


def _run_async(awaitable: Any) -> Any:
    """Run one account refresh without nesting an already-running event loop."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result: list[Any] = []
    error: list[BaseException] = []

    def _worker() -> None:
        try:
            result.append(asyncio.run(awaitable))
        except BaseException as exc:  # noqa: BLE001 - re-raised on caller thread
            error.append(exc)

    thread = threading.Thread(target=_worker, name="chatgpt-auth-refresh", daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0] if result else None


def _upstream_model(model: str) -> str:
    normalized = str(model or "").strip()
    for prefix in _MODEL_PREFIXES:
        if normalized.casefold().startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
            break
    if not normalized or len(normalized) > 256 or any(c in normalized for c in "\x00\r\n"):
        raise ChatGPTSubscriptionRouterError("ChatGPT 模型标识无效。")
    return normalized


def _build_responses_payload(request: ModelRequest, *, model: str) -> dict[str, Any]:
    instructions: list[str] = []
    input_items: list[dict[str, Any]] = []
    for message in request.messages:
        if message.role == "system":
            instructions.append(_flatten_content(message.content))
            continue
        input_items.extend(_message_to_input_items(message))
    if request.images_b64:
        image_parts = [
            {
                "type": "input_image",
                "image_url": f"data:image/png;base64,{encoded}",
            }
            for encoded in request.images_b64
            if _valid_base64_image(encoded)
        ]
        if image_parts:
            input_items.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": image_parts,
                }
            )
    if not input_items:
        raise ChatGPTSubscriptionRouterError("ChatGPT 模型请求缺少可见输入。")
    tools = [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
            "strict": False,
        }
        for tool in request.tools
    ]
    effort = request.reasoning_effort or ("high" if request.enable_thinking else None)
    return {
        "model": model,
        "instructions": "\n\n".join(part for part in instructions if part.strip()),
        "input": input_items,
        "tools": tools,
        "tool_choice": "required" if request.require_tool_use and tools else "auto",
        "parallel_tool_calls": True,
        "reasoning": ({"effort": effort, "summary": "auto"} if effort is not None else None),
        "store": False,
        "stream": True,
        "include": ["reasoning.encrypted_content"],
    }


def _message_to_input_items(message: Message) -> list[dict[str, Any]]:
    text_type = "output_text" if message.role == "assistant" else "input_text"
    if isinstance(message.content, str):
        return [
            {
                "type": "message",
                "role": message.role,
                "content": [{"type": text_type, "text": message.content}],
            }
        ]
    items: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for part in message.content:
        part_type = str(part.get("type") or "")
        if part_type == "tool_use":
            items.append(
                {
                    "type": "function_call",
                    "call_id": str(part.get("id") or f"call_{uuid4().hex}"),
                    "name": str(part.get("name") or "tool"),
                    "arguments": json.dumps(
                        part.get("input") if isinstance(part.get("input"), Mapping) else {},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            )
        elif part_type == "tool_result":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(part.get("tool_use_id") or ""),
                    "output": _flatten_content(part.get("content", "")),
                }
            )
        else:
            text_parts.append(_flatten_content(part.get("text", part.get("content", ""))))
    if text_parts:
        items.insert(
            0,
            {
                "type": "message",
                "role": message.role,
                "content": [{"type": text_type, "text": "\n".join(text_parts)}],
            },
        )
    return items


def _flatten_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "\n".join(_flatten_content(item) for item in value)
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return "" if value is None else str(value)


def _valid_base64_image(value: str) -> bool:
    try:
        return bool(base64.b64decode(value, validate=True))
    except (ValueError, TypeError):
        return False


def _iter_responses_sse(
    response: Any,
    *,
    model: str,
    provider: str = "chatgpt_subscription",
    service_name: str = "ChatGPT Responses",
) -> Iterator[ModelStreamEvent]:
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    completed: Mapping[str, Any] | None = None
    data_lines: list[str] = []

    def _dispatch(raw: str) -> Iterator[ModelStreamEvent]:
        nonlocal completed
        try:
            event = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return
        if not isinstance(event, Mapping):
            return
        event_type = str(event.get("type") or "")
        if event_type == "response.output_text.delta":
            delta = str(event.get("delta") or "")
            if delta:
                text_parts.append(delta)
                yield ModelStreamEvent(type="text_delta", delta=delta)
        elif event_type in {
            "response.reasoning_summary_text.delta",
            "response.reasoning_text.delta",
        }:
            delta = str(event.get("delta") or "")
            if delta:
                thinking_parts.append(delta)
                yield ModelStreamEvent(type="thinking_delta", delta=delta)
        elif event_type == "response.output_item.done":
            item = event.get("item")
            call = _tool_call_from_output(item)
            if call is not None and all(existing.id != call.id for existing in tool_calls):
                tool_calls.append(call)
                yield ModelStreamEvent(type="tool_use", tool_call=call)
        elif event_type == "response.completed":
            raw_response = event.get("response")
            if isinstance(raw_response, Mapping):
                completed = raw_response
        elif event_type in {"response.failed", "error"}:
            raise ChatGPTSubscriptionRouterError(_responses_error(event, service_name=service_name))

    for line in response.iter_lines():
        if line is None:
            continue
        line = str(line)
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif not line and data_lines:
            raw = "\n".join(data_lines)
            data_lines.clear()
            if raw != "[DONE]":
                yield from _dispatch(raw)
    if data_lines:
        yield from _dispatch("\n".join(data_lines))
    if completed is None:
        return
    output = completed.get("output")
    if isinstance(output, list):
        if not text_parts:
            text_parts.extend(_text_from_output(output))
        for item in output:
            call = _tool_call_from_output(item)
            if call is not None and all(existing.id != call.id for existing in tool_calls):
                tool_calls.append(call)
                yield ModelStreamEvent(type="tool_use", tool_call=call)
    usage = completed.get("usage") if isinstance(completed.get("usage"), Mapping) else {}
    input_tokens = _safe_int(usage.get("input_tokens"))
    output_tokens = _safe_int(usage.get("output_tokens"))
    final = ModelResponse(
        text="".join(text_parts),
        thinking="".join(thinking_parts),
        tool_calls=tool_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=CostEntry(tokens_in=input_tokens, tokens_out=output_tokens, usd=0.0),
        finish_reason="stop",
        model=str(completed.get("model") or model),
        provider=provider,
    )
    yield ModelStreamEvent(type="done", final=final)


def _text_from_output(output: Sequence[Any]) -> list[str]:
    parts: list[str] = []
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "output_text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return parts


def _tool_call_from_output(raw: Any) -> ToolCall | None:
    if not isinstance(raw, Mapping) or raw.get("type") != "function_call":
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        return None
    arguments = raw.get("arguments", "{}")
    try:
        parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError:
        parsed = {"input": arguments}
    return ToolCall(
        id=str(raw.get("call_id") or raw.get("id") or f"call_{uuid4().hex}"),
        name=name,
        input=parsed if isinstance(parsed, dict) else {"input": parsed},
    )


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _responses_error(
    event: Mapping[str, Any],
    *,
    service_name: str = "ChatGPT Responses",
) -> str:
    raw = event.get("response") or event.get("error") or event
    if isinstance(raw, Mapping):
        detail = raw.get("error") or raw.get("message") or raw.get("status")
        if isinstance(detail, Mapping):
            detail = detail.get("message") or detail.get("code")
        if isinstance(detail, str) and detail.strip():
            return f"{service_name} failed: {detail[:300]}"
    return f"{service_name} failed"


def _safe_http_error(status: int, body: str) -> str:
    if status in {401, 403}:
        return f"ChatGPT 登录授权已失效或无模型权限（HTTP {status}），请重新登录。"
    if status == 429:
        return "ChatGPT 订阅额度暂时受限，请稍后重试或切换系统模型。"
    lowered = str(body or "").casefold()
    if status == 400 and any(
        marker in lowered
        for marker in (
            "context_length_exceeded",
            "maximum context length",
            "context window",
            "too many tokens",
            "input is too long",
            "input tokens",
        )
    ):
        return "ChatGPT 请求上下文超过当前订阅模型限制（HTTP 400）。"
    if status == 400 and any(
        marker in lowered
        for marker in (
            "invalid_function_parameters",
            "invalid tool",
            "function_call_output",
            "tool call",
        )
    ):
        return "ChatGPT 请求中的工具调用链或参数格式无效（HTTP 400）。"
    return f"ChatGPT 模型服务请求失败（HTTP {status}）。"


__all__ = [
    "ChatGPTSubscriptionCredentialBroker",
    "ChatGPTSubscriptionModelRouter",
    "ChatGPTSubscriptionRouterError",
]
