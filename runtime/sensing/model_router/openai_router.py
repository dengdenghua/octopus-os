# ruff: noqa: E402 — module-level imports below are intentionally late

from __future__ import annotations

import json
import os
import socket
import threading
from typing import Any

from runtime.adapters.instrumentation import record_gen_ai_cost, trace_stage
from runtime.platform.models import CostEntry
from runtime.platform.models.model_capabilities import (
    model_is_reasoning,
    model_rejects_temperature,
)

from .custom_model_flags import (
    custom_model_entry_for,
    custom_model_supports_thinking,
    model_omits_sampling_parameters,
    model_supports_tool_use,
)
from .custom_model_flags import (
    entry_matches_model as _entry_matches_model,
)
from .custom_model_flags import (
    read_custom_models as _read_custom_models,
)
from .models import (
    DEFAULT_USER_AGENT,
    LLMResponseFormatError,
    Message,
    ModelRequest,
    ModelResponse,
    ModelRouter,
    normalize_reasoning_effort,
)
from .openai_compat_providers import (
    OpenAICompatProviderProfile,
    OpenAICompatRetryPayload,
    apply_custom_openai_compat_profile,
    extract_openai_compat_reasoning,
    extract_openai_compat_usage,
    normalize_openai_compat_payload,
    parse_tool_call_arguments,
    plan_openai_compat_retries,
    resolve_openai_compat_profile,
    split_inline_reasoning,
)

try:
    import httpx  # type: ignore[import-untyped]

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]


_DEFAULT_INPUT_USD_PER_TOKEN = 1e-7
_DEFAULT_OUTPUT_USD_PER_TOKEN = 3e-7
# Floor for a request that may spend output tokens on reasoning before it
# writes anything. Measured on agnes-2.5-flash against a real question: at 128
# the reasoning consumed the entire budget and content came back EMPTY in 3/3
# runs, at 192 in 0/3. 256 keeps headroom above that observed cliff without
# meaningfully raising cost — this is a floor, so it only ever applies to
# callers that asked for less.
_MIN_THINKING_OUTPUT_TOKENS = 256
_MAX_COMPAT_RETRY_ATTEMPTS = 6

# OpenAI's reasoning_effort only accepts minimal/low/medium/high. Echo's
# xhigh tier (and the ultra/extra_high aliases that normalize to it) has no
# native value, so clamp it to "high" rather than putting an unknown string on
# the wire — a strict endpoint 400s on it, and a lenient one silently ignores
# it (losing the high-effort signal entirely). Anthropic is unaffected: it
# routes effort through a numeric thinking budget, not this string.
_OPENAI_REASONING_EFFORT: dict[str, str] = {
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}


def _openai_reasoning_effort(value: Any) -> str:
    """Map an echo reasoning-effort tier onto a value native OpenAI accepts."""
    return _OPENAI_REASONING_EFFORT.get(normalize_reasoning_effort(value) or "high", "high")


def _model_might_think(model: str) -> bool:
    """Whether ``model`` may spend output tokens reasoning before it writes.

    Two sources, deliberately OR'ed rather than ranked: the operator's own
    ``supports_thinking`` declaration, and the bundled models.dev snapshot.
    Either saying yes is enough, because the cost of a false positive is a
    slightly larger output floor while the cost of a false negative is a
    response with no content in it.
    """
    return custom_model_supports_thinking(model) or model_is_reasoning(model)


def _compat_payload_fingerprint(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


class OpenAIRouterError(LLMResponseFormatError):
    pass


from .provider import Provider, ProviderCapabilities


class OpenAIModelRouter(Provider, ModelRouter):
    provider_name = "openai"
    capabilities = ProviderCapabilities(
        supports_vision=True,
        supports_tool_use=True,
        supports_streaming=True,
        supports_prompt_cache=True,  # OpenAI honors `prompt_cache_key`
        supports_structured_output=True,
        default_model="gpt-4o-mini",
        pricing_hint="mid",
    )

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        default_model: str = "gpt-4o-mini",
        env_var_name: str = "OPENAI_API_KEY",
        timeout_seconds: float = 60.0,
        pricing_per_1k: dict[str, tuple[float, float]] | None = None,
        extra_headers: dict[str, str] | None = None,
        custom_model_entry: dict[str, Any] | None = None,
        client: Any = None,
    ) -> None:
        if not HTTPX_AVAILABLE:
            raise OpenAIRouterError(
                "httpx not installed · `pip install httpx` (or install extras: '.[web]')"
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get(env_var_name, "")
        self.default_model = default_model
        self.timeout_seconds = timeout_seconds
        self.pricing_per_1k = pricing_per_1k or {}
        self.extra_headers = dict(extra_headers or {})
        # A routed selection_id already resolved one exact endpoint entry.
        # Bind that metadata so entries sharing the same upstream model cannot
        # borrow each other's capability or compatibility flags after rewrite.
        self._custom_model_entry = (
            dict(custom_model_entry) if isinstance(custom_model_entry, dict) else None
        )
        self._client = client  # Implementation note.
        self._owns_client = client is None
        self._provider_profile = resolve_openai_compat_profile(
            self.base_url,
            self.default_model,
        )
        self.last_compatibility_events: list[dict[str, Any]] = []

    def call(self, request: ModelRequest) -> ModelResponse:
        model = request.model or self.default_model

        with trace_stage(
            "eyes.openai_router.call",
            **{"echo.model": model, "echo.provider": "openai_compat"},
        ) as span:
            self.last_compatibility_events = []
            profile = self._profile_for_model(model)
            span.set_attribute("echo.openai_compat.profile", profile.id)
            payload = self._build_payload(request, model)
            client = (
                self._client
                if self._client is not None
                else httpx.Client(
                    timeout=self.timeout_seconds,
                )
            )
            try:
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=self._build_headers(),
                )
                seen_payloads = {_compat_payload_fingerprint(payload)}
                retry_queue = self._retry_payloads(
                    resp.status_code,
                    resp.text,
                    payload,
                    model,
                    seen_payloads=seen_payloads,
                )
                attempt = 0
                while (
                    resp.status_code >= 400 and retry_queue and attempt < _MAX_COMPAT_RETRY_ATTEMPTS
                ):
                    retry = retry_queue.pop(0)
                    attempt += 1
                    self._record_compat_retry(span, model, profile, attempt, retry)
                    resp = client.post(
                        f"{self.base_url}/chat/completions",
                        json=retry.payload,
                        headers=self._build_headers(),
                    )
                    if resp.status_code < 400:
                        break
                    retry_queue.extend(
                        self._retry_payloads(
                            resp.status_code,
                            resp.text,
                            retry.payload,
                            model,
                            seen_payloads=seen_payloads,
                        ),
                    )
            except Exception as e:  # noqa: BLE001
                raise OpenAIRouterError(
                    f"http_error: {type(e).__name__}: {_redact_error_text(str(e))}"
                ) from e
            finally:
                if self._client is None:
                    client.close()

            if resp.status_code >= 400:
                raise OpenAIRouterError(
                    _format_openai_http_error(
                        resp.status_code,
                        resp.text,
                        compatibility_events=self.last_compatibility_events,
                    )
                )

            try:
                data = resp.json()
            except Exception as e:  # noqa: BLE001
                raise OpenAIRouterError(f"invalid_json: {e}") from e

            text, finish_reason, thinking = self._extract_text(data)
            tool_calls = self._extract_tool_calls(data)
            input_tokens, output_tokens = extract_openai_compat_usage(data)
            cost_usd = self._estimate_cost(model, input_tokens, output_tokens)

            cost = CostEntry(
                tokens_in=input_tokens,
                tokens_out=output_tokens,
                usd=cost_usd,
            )
            record_gen_ai_cost(
                span,
                system="openai_compat",
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                usd=cost_usd,
            )

            return ModelResponse(
                text=text,
                thinking=thinking,
                tool_calls=tool_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=cost,
                finish_reason=finish_reason,
                model=data.get("model", model),
                provider="openai_compat",
            )

    # ─── Streaming ────────────────────────────────────

    def call_stream(self, request: ModelRequest):
        """Real SSE streaming via the shared OpenAI-compat parser.

        Opens an httpx streaming POST with ``stream=True`` and
        delegates line parsing to ``iter_openai_sse``. Any new
        OpenAI-compat provider gets streaming for free by following
        the same pattern.
        """
        from runtime.safety.approval.cancellation import current_cancellation_token

        from .openai_compat_stream import iter_openai_sse

        model = request.model or self.default_model
        self.last_compatibility_events = []

        with trace_stage(
            "eyes.openai_router.stream",
            **{"echo.model": model, "echo.provider": "openai_compat"},
        ) as span:
            profile = self._profile_for_model(model)
            span.set_attribute("echo.openai_compat.profile", profile.id)
            payload = self._build_payload(request, model)
            payload["stream"] = True

            client = (
                self._client
                if self._client is not None
                else httpx.Client(
                    # Streaming-tuned timeouts: ``connect`` for the initial
                    # handshake, ``read`` is the gap between successive bytes
                    # — must be tight or a hung upstream (mimo / smaller
                    # OpenAI-compat proxies sometimes finish the model
                    # output but never send ``data: [DONE]``) leaves the
                    # request blocked indefinitely. Without a read cap the
                    # ReAct loop's interrupt watcher can't break us out
                    # because the producer thread is stuck inside
                    # ``response.iter_lines()``.
                    timeout=httpx.Timeout(
                        connect=30.0,
                        read=45.0,
                        write=30.0,
                        pool=10.0,
                    ),
                )
            )
            close_after = self._client is None
            url = f"{self.base_url}/chat/completions"
            cancellation = current_cancellation_token()
            transport_cancelled = threading.Event()
            response_lock = threading.Lock()
            active_response: Any = None

            def _close_quietly(target: Any) -> None:
                close = getattr(target, "close", None)
                if not callable(close):
                    return
                try:
                    close()
                except Exception:  # noqa: BLE001 — cancellation is best-effort
                    return

            def _shutdown_owned_response_socket(response: Any) -> None:
                """Wake a cross-thread socket read before closing the response.

                ``httpx.Response.close()`` alone does not reliably interrupt a
                synchronous ``recv`` already blocked in another thread (notably
                on macOS). The response exposes its dedicated httpcore network
                stream through extensions; shutting that socket down wakes the
                reader immediately. Only owned clients take this path so a
                multiplexed/shared transport cannot lose unrelated requests.
                """
                if not close_after:
                    return
                extensions = getattr(response, "extensions", None)
                if not isinstance(extensions, dict):
                    return
                network_stream = extensions.get("network_stream")
                get_extra_info = getattr(network_stream, "get_extra_info", None)
                if not callable(get_extra_info):
                    return
                try:
                    response_socket = get_extra_info("socket")
                    shutdown = getattr(response_socket, "shutdown", None)
                    if callable(shutdown):
                        shutdown(socket.SHUT_RDWR)
                except (OSError, RuntimeError, ValueError):
                    return

            def _cancel_active_transport(_reason: str) -> None:
                """Abort the provider read instead of waiting for its timeout."""
                transport_cancelled.set()
                with response_lock:
                    response = active_response
                if response is not None:
                    _shutdown_owned_response_socket(response)
                    _close_quietly(response)
                # An owned client may still be waiting for response headers,
                # before ``client.stream`` has exposed a response to close.
                # Never close an injected/shared client: abort only this
                # response in that case.
                if close_after:
                    _close_quietly(client)

            def _activate_response(response: Any) -> bool:
                nonlocal active_response
                with response_lock:
                    if transport_cancelled.is_set():
                        close_now = True
                    else:
                        active_response = response
                        close_now = False
                if close_now:
                    _shutdown_owned_response_socket(response)
                    _close_quietly(response)
                return not close_now

            def _deactivate_response(response: Any) -> None:
                nonlocal active_response
                with response_lock:
                    if active_response is response:
                        active_response = None

            unsubscribe_cancel = cancellation.on_cancelled(_cancel_active_transport)
            try:
                if cancellation.is_cancelled:
                    return
                with client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers=self._build_headers(),
                ) as r:
                    if not _activate_response(r):
                        return
                    try:
                        if r.status_code < 400:
                            yield from iter_openai_sse(
                                r,
                                model=model,
                                provider="openai_compat",
                                cancelled=lambda: cancellation.is_cancelled,
                            )
                            return
                        r.read()
                        first_status = r.status_code
                        first_text = r.text
                    finally:
                        _deactivate_response(r)

                seen_payloads = {_compat_payload_fingerprint(payload)}
                retry_queue = self._retry_payloads(
                    first_status,
                    first_text,
                    payload,
                    model,
                    seen_payloads=seen_payloads,
                )
                attempt = 0
                while retry_queue and attempt < _MAX_COMPAT_RETRY_ATTEMPTS:
                    if cancellation.is_cancelled:
                        return
                    retry = retry_queue.pop(0)
                    attempt += 1
                    self._record_compat_retry(span, model, profile, attempt, retry)
                    with client.stream(
                        "POST",
                        url,
                        json=retry.payload,
                        headers=self._build_headers(),
                    ) as r:
                        if not _activate_response(r):
                            return
                        try:
                            if r.status_code < 400:
                                yield from iter_openai_sse(
                                    r,
                                    model=model,
                                    provider="openai_compat",
                                    cancelled=lambda: cancellation.is_cancelled,
                                )
                                return
                            r.read()
                            first_status = r.status_code
                            first_text = r.text
                        finally:
                            _deactivate_response(r)
                    retry_queue.extend(
                        self._retry_payloads(
                            first_status,
                            first_text,
                            retry.payload,
                            model,
                            seen_payloads=seen_payloads,
                        ),
                    )

                raise OpenAIRouterError(
                    _format_openai_http_error(
                        first_status,
                        first_text,
                        compatibility_events=self.last_compatibility_events,
                    )
                )
            except Exception:
                # Closing a live httpx response/client wakes a blocked read by
                # raising a transport exception on the producer thread. Once
                # the caller has explicitly cancelled, that exception is an
                # expected control signal rather than a provider failure.
                if cancellation.is_cancelled:
                    return
                raise
            finally:
                unsubscribe_cancel()
                if close_after:
                    _close_quietly(client)

    def _build_payload(self, request: ModelRequest, model: str) -> dict[str, Any]:
        # Message shape · caller may hand us Anthropic-style
        # block lists (tool_use / tool_result) that we need to
        # translate into OpenAI's flat function-call format
        # before sending. ``_messages_to_openai`` is the
        # translator; it falls back to the old 1-to-1 mapping
        # when no blocks are present.
        msgs = _messages_to_openai(request.messages)
        if "glm-5.1" in (model or "").lower():
            msgs = [m for m in msgs if m.get("role") != "system"]
        if request.images_b64 and msgs:
            _attach_images_to_last_user_openai(msgs, request.images_b64)
        payload: dict[str, Any] = {
            "model": model,
            "messages": msgs,
        }
        # ``model_rejects_temperature`` covers models the operator never
        # declared: kimi-k3 answers HTTP 400 on any temperature, and on a relay
        # hosting dozens of models nobody hand-configures each one. The
        # operator's own ``omit_sampling_parameters`` still takes precedence by
        # being checked first.
        omits_sampling = (
            bool(self._custom_model_entry.get("omit_sampling_parameters"))
            if self._custom_model_entry is not None
            else model_omits_sampling_parameters(model)
        )
        if not omits_sampling and not model_rejects_temperature(model):
            payload["temperature"] = request.temperature
        max_tokens = request.max_tokens
        # A reasoning model spends max_tokens on reasoning FIRST, so a budget
        # that only covers the thinking leaves finish_reason="length" with
        # empty content — an HTTP 200 carrying no answer. Raise the floor for
        # any model that might think, including ones with no config entry: on a
        # relay most models are unconfigured, and ``custom_model_supports_
        # thinking`` returns False for those, so the floor never applied where
        # it was needed most.
        if (
            (
                request.enable_thinking
                or (
                    bool(self._custom_model_entry.get("supports_thinking"))
                    if self._custom_model_entry is not None
                    else _model_might_think(model)
                )
            )
            and max_tokens is not None
            and max_tokens < _MIN_THINKING_OUTPUT_TOKENS
        ):
            max_tokens = _MIN_THINKING_OUTPUT_TOKENS
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        # Native function calling · OpenAI ``tools`` spec shape is
        # ``[{type:"function", function:{name, description, parameters}}]``
        # where parameters is JSON Schema (== our input_schema).
        # Most OpenAI-compat providers (GLM, Kimi, DeepSeek,
        # Qwen, OpenRouter) follow the same shape. Providers that
        # don't will just ignore the field — except some (mimo,
        # smaller community models) silently swallow it AND have the
        # model hallucinate "I cannot call tools" in pure prose.
        # Skip the tools block entirely when ``custom_models.json``
        # explicitly declares ``supports_tool_use=false`` for this
        # model id, so the LLM doesn't get a tools spec it can't act
        # on. The caller (ReAct loop / ephemeral runner) will see
        # the lack of tool_calls and fall back to text-only synthesis.
        supports_tool_use = (
            self._custom_model_entry.get("supports_tool_use") is not False
            if self._custom_model_entry is not None
            else model_supports_tool_use(model)
        )
        if request.tools and supports_tool_use:
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
            # Let the model decide · default "auto" means tools
            # are available but not required · matches Anthropic
            # default behavior and works for agentic loops.
            payload["tool_choice"] = "required" if request.require_tool_use else "auto"
        # Some compatible reasoning models deliberate by default even when
        # ``thinking`` is omitted.  Let callers bound that work without also
        # enabling a vendor-specific thinking envelope.
        if (
            request.reasoning_effort is not None
            and str(request.reasoning_effort).strip().lower() == "off"
        ):
            # DeepSeek-native disable wins over any enable_thinking flag.
            # Only the deepseek profile knows how to serialize it
            # (thinking:{type:disabled} via normalize); for every other
            # profile "off" simply means "no thinking fields" — emitting an
            # unknown effort would 400 on strict endpoints.
            if self._profile_for_model(model).thinking_request_style == "deepseek":
                payload["reasoning_effort"] = "off"
        else:
            if request.reasoning_effort is not None:
                payload["reasoning_effort"] = _openai_reasoning_effort(request.reasoning_effort)
            if request.enable_thinking:
                payload["thinking"] = {"type": "enabled"}
        return normalize_openai_compat_payload(
            payload,
            profile=self._profile_for_model(model),
        )

    def _profile_for_model(self, model: str) -> OpenAICompatProviderProfile:
        profile = resolve_openai_compat_profile(self.base_url, model)
        if profile.id == "openai_compat" and self._can_fall_back_to_entry_profile(model):
            profile = self._provider_profile
        return apply_custom_openai_compat_profile(
            self._custom_model_entry or custom_model_entry_for(model),
            base_profile=profile,
        )

    def _can_fall_back_to_entry_profile(self, model: str) -> bool:
        """Whether the entry-level profile may stand in for this model.

        ``self._provider_profile`` is resolved once from the configured
        ``default_model``. That is a useful fallback when the per-call model
        carries no signal at all — an entry pointing at DeepSeek still wants
        the DeepSeek quirks for a bare model id.

        It is wrong when the per-call model DOES carry a vendor signal and
        simply isn't one we have a profile for. A relay hosts many vendors
        behind one base_url (opencode.ai advertises 25 models across 8-plus
        vendors), so the entry's ``default_model`` says nothing about the
        model actually being called: ``gpt-5.6-luna`` was inheriting the
        DeepSeek profile purely because a sibling entry named a DeepSeek
        model. Harmless today, but it is quirks crossing vendor lines in
        exactly the setup we run in production.

        So the fallback only applies when the entry-level profile was matched
        on the shared base_url — which genuinely describes the endpoint — and
        not when it was inferred from a sibling model's name.
        """
        if self._provider_profile.id == "openai_compat":
            return False
        base = self.base_url.lower()
        return any(marker in base for marker in self._provider_profile.base_url_markers)

    def _retry_payloads(
        self,
        status_code: int,
        body: str,
        payload: dict[str, Any],
        model: str,
        *,
        seen_payloads: set[str] | None = None,
    ) -> list[OpenAICompatRetryPayload]:
        plan = plan_openai_compat_retries(
            payload,
            status_code=status_code,
            body=body,
            profile=self._profile_for_model(model),
        )
        if seen_payloads is None:
            return plan
        out: list[OpenAICompatRetryPayload] = []
        for retry in plan:
            fingerprint = _compat_payload_fingerprint(retry.payload)
            if fingerprint in seen_payloads:
                continue
            seen_payloads.add(fingerprint)
            out.append(retry)
        return out

    def _record_compat_retry(
        self,
        span: Any,
        model: str,
        profile: OpenAICompatProviderProfile,
        attempt: int,
        retry: OpenAICompatRetryPayload,
    ) -> None:
        event = {
            "attempt": attempt,
            "model": model,
            "profile": profile.id,
            "reason": retry.reason,
            "removed_fields": list(retry.removed_fields),
            "added_fields": list(retry.added_fields),
            "changed_fields": list(retry.changed_fields),
        }
        self.last_compatibility_events.append(event)
        span.set_attribute("echo.openai_compat.retry_count", attempt)
        span.set_attribute("echo.openai_compat.retry_reason", retry.reason)
        span.set_attribute(
            "echo.openai_compat.retry_reasons",
            [item["reason"] for item in self.last_compatibility_events],
        )
        if retry.removed_fields:
            span.set_attribute(
                "echo.openai_compat.removed_fields",
                list(retry.removed_fields),
            )
        if retry.added_fields:
            span.set_attribute(
                "echo.openai_compat.added_fields",
                list(retry.added_fields),
            )

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "User-Agent": DEFAULT_USER_AGENT}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        # An entry's ``default_headers`` wins, so a provider that wants a
        # specific UA (or none of ours) can still say so.
        headers.update(self.extra_headers)
        return headers

    def _extract_text(self, data: dict[str, Any]) -> tuple[str, str, str]:
        choices = data.get("choices") or []
        if not choices:
            raise OpenAIRouterError(f"no choices in response · keys={list(data.keys())}")
        first = choices[0]
        if not isinstance(first, dict):
            raise OpenAIRouterError("choice[0] not a dict")
        msg = first.get("message") or {}
        content = msg.get("content", "")
        if content is None:
            content = ""
        thinking = extract_openai_compat_reasoning(msg)
        if isinstance(content, list):
            parts = [
                p.get("text") or ""
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            content = "".join(parts)
        if not isinstance(content, str):
            content = json.dumps(content)
        if not isinstance(thinking, str):
            thinking = json.dumps(thinking, ensure_ascii=False)
        # Providers that emit reasoning inline in ``content`` rather than in
        # ``reasoning_content`` (minimax-m3, measured) would otherwise hand
        # the model's private reasoning back as the answer.
        content, inline_reasoning = split_inline_reasoning(content)
        if inline_reasoning:
            thinking = f"{thinking}\n{inline_reasoning}".strip() if thinking else inline_reasoning
        finish_reason = first.get("finish_reason") or "stop"
        return content, finish_reason, thinking

    def _extract_tool_calls(self, data: dict[str, Any]) -> list[Any]:
        """Pull native function calls from an OpenAI response.

        Response shape::

            choices[0].message.tool_calls = [
                {"id": "call_...",
                 "type": "function",
                 "function": {"name": "...", "arguments": "<json str>"}},
                ...
            ]

        Returns a list of ``ToolCall`` · empty when the model
        didn't invoke any functions. JSON argument parsing is
        permissive: a malformed ``arguments`` string yields an
        empty dict rather than raising, so the agentic loop can
        surface the error back to the model instead of 500-ing."""
        from .models import ToolCall

        choices = data.get("choices") or []
        if not choices:
            return []
        first = choices[0]
        if not isinstance(first, dict):
            return []
        msg = first.get("message") or {}
        raw_calls = msg.get("tool_calls") or []
        out: list[ToolCall] = []
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            args_raw = fn.get("arguments") or ""
            args = parse_tool_call_arguments(args_raw)
            out.append(
                ToolCall(
                    id=str(call.get("id") or ""),
                    name=str(name),
                    input=args if isinstance(args, dict) else {},
                )
            )
        legacy_call = msg.get("function_call")
        if isinstance(legacy_call, dict):
            name = legacy_call.get("name") or ""
            args_raw = legacy_call.get("arguments") or ""
            args = parse_tool_call_arguments(args_raw)
            out.append(
                ToolCall(
                    id=str(legacy_call.get("id") or "function_call_0"),
                    name=str(name),
                    input=args if isinstance(args, dict) else {},
                )
            )
        return out

    def _estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        pricing = self.pricing_per_1k.get(model)
        if pricing is not None:
            in_usd, out_usd = pricing
            return (input_tokens / 1000) * in_usd + (output_tokens / 1000) * out_usd
        return (
            input_tokens * _DEFAULT_INPUT_USD_PER_TOKEN
            + output_tokens * _DEFAULT_OUTPUT_USD_PER_TOKEN
        )


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════


def build_fallback_router_from_custom_models(prefer: str | None = None) -> Any:
    """Build a ModelRouter from the user's custom_models.json — a self-configured
    upstream usable as the dispatch *fallback*.

    Why: an account-backed fallback may require a logged-in actor; any
    unresolved / guest model request would otherwise die with "no current_actor set".
    Pointing the fallback at a self-configured model (e.g. the planner's own
    model) keeps the runtime usable without a login.

    Picks the entry matching ``prefer`` (the planner model), else the first
    entry with a ``base_url``. Returns ``None`` when no usable entry exists, so
    callers can keep their existing last-resort fallback.
    """
    models = _read_custom_models()
    if not models:
        return None
    entry: dict[str, Any] | None = None
    if prefer:
        for candidate in models.values():
            if _entry_matches_model(candidate, prefer):
                entry = candidate
                break
    if entry is None:
        for candidate in models.values():
            if isinstance(candidate, dict) and candidate.get("base_url"):
                entry = candidate
                break
    if not isinstance(entry, dict):
        return None
    base_url = entry.get("base_url")
    if not base_url:
        return None
    raw_models = entry.get("models")
    upstreams = (
        [str(m).strip() for m in raw_models if str(m or "").strip()]
        if isinstance(raw_models, list)
        else []
    )
    primary = (
        upstreams[0] if upstreams else str(entry.get("model") or entry.get("id") or "").strip()
    )
    if not primary:
        return None
    provider = str(entry.get("provider") or "openai").lower()
    headers = entry.get("default_headers")
    headers = headers if isinstance(headers, dict) else {}
    # Slow reasoning models (e.g. agnes-2.0-flash spends 60–120s on hidden
    # reasoning before answering) blow past the 60s default and raise
    # ReadTimeout. Let an entry declare its own ceiling via ``timeout`` /
    # ``request_timeout``; fast models simply omit it and keep the default.
    _raw_timeout = entry.get("timeout") or entry.get("request_timeout")
    try:
        timeout_seconds = float(_raw_timeout) if _raw_timeout else 60.0
    except (TypeError, ValueError):
        timeout_seconds = 60.0
    try:
        if provider in ("anthropic", "claude"):
            from runtime.sensing.model_router.anthropic_router import AnthropicModelRouter

            return AnthropicModelRouter(
                api_key=entry.get("api_key") or "",
                default_model=primary,
                base_url=(base_url or None),
            )
        if provider in ("gemini", "google"):
            from runtime.sensing.model_router.gemini_router import GeminiModelRouter

            return GeminiModelRouter(
                api_key=entry.get("api_key") or "",
                default_model=primary,
                base_url=base_url,
                extra_headers=headers,
            )
        return OpenAIModelRouter(
            base_url=base_url,
            api_key=entry.get("api_key") or "dummy",
            default_model=primary,
            extra_headers=headers,
            timeout_seconds=timeout_seconds,
        )
    except Exception:  # noqa: BLE001 — keep the existing fallback if the entry is malformed
        return None


def _redact_error_text(text: str) -> str:
    if not text:
        return text
    try:
        from runtime.platform.observability.redactor import redact_text

        return redact_text(text)
    except Exception:  # pragma: no cover - diagnostics must not fail calls
        return text


def _format_openai_http_error(
    status_code: int,
    body: str,
    *,
    compatibility_events: list[dict[str, Any]] | None = None,
) -> str:
    body_preview = _redact_error_text((body or "").strip())[:500]
    parsed_message = ""
    parsed_type = ""
    try:
        payload = json.loads(body or "{}")
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            parsed_message = _redact_error_text(
                str(error.get("message") or "").strip(),
            )
            parsed_type = _redact_error_text(
                str(error.get("type") or error.get("code") or "").strip(),
            )
    except (TypeError, json.JSONDecodeError):  # noqa: BLE001 — error body parse failed; keep empty parsed fields
        pass

    lower = f"{parsed_type} {parsed_message} {body_preview}".lower()
    if (
        status_code == 402
        or "insufficient_balance" in lower
        or "insufficient account balance" in lower
    ):
        return _append_compatibility_retry_summary(
            f"http_{status_code}: 模型账户余额不足，请充值当前模型供应商账户，"
            "或在模型选择里切换到可用模型。",
            compatibility_events,
        )
    if status_code in (401, 403):
        detail = parsed_message or body_preview
        suffix = f"（{detail}）" if detail else ""
        return _append_compatibility_retry_summary(
            f"http_{status_code}: 模型 API Key 无效或没有权限{suffix}",
            compatibility_events,
        )

    detail = parsed_message or body_preview
    if status_code == 400 and (not detail or detail == "openai_error"):
        return _append_compatibility_retry_summary(
            f"http_{status_code}: 上游 OpenAI 兼容接口拒绝请求"
            f"{f'（{detail}）' if detail else ''}。"
            "通常是模型名、API Key、额度或供应商不支持的 reasoning/thinking "
            "参数导致；请切换到可用模型，或在模型设置里关闭该模型的思考能力后重试。",
            compatibility_events,
        )
    return _append_compatibility_retry_summary(
        f"http_{status_code}: {detail}",
        compatibility_events,
    )


def _append_compatibility_retry_summary(
    message: str,
    events: list[dict[str, Any]] | None,
) -> str:
    if not events:
        return message
    rows: list[str] = []
    for index, event in enumerate(events, start=1):
        reason = _redact_error_text(str(event.get("reason") or "unknown"))
        parts: list[str] = []
        for key, label in (
            ("removed_fields", "移除"),
            ("added_fields", "新增"),
            ("changed_fields", "调整"),
        ):
            values = [
                _redact_error_text(str(value))
                for value in (event.get(key) or [])
                if str(value).strip()
            ]
            if values:
                parts.append(f"{label}:{','.join(values)}")
        detail = f"{reason}（{';'.join(parts)}）" if parts else reason
        rows.append(f"{index}.{detail}")
    return f"{message} 已自动尝试 OpenAI 兼容降级：{'；'.join(rows)}。"


def _without_openai_thinking(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a Chat Completions payload without thinking extensions.

    OpenAI-compatible gateways disagree on reasoning knobs: some accept
    ``reasoning_effort`` and/or ``thinking``, others return a generic
    ``400 openai_error`` for either field. A one-shot fallback keeps custom
    providers usable without forcing operators to know every proxy dialect.
    """
    fallback = dict(payload)
    fallback.pop("reasoning_effort", None)
    fallback.pop("thinking", None)
    return fallback


def _should_retry_without_openai_thinking(
    status_code: int,
    payload: dict[str, Any],
) -> bool:
    if status_code != 400:
        return False
    return "reasoning_effort" in payload or "thinking" in payload


def _message_to_openai(m: Message) -> dict[str, str]:
    """Legacy 1-to-1 translator · preserved for callers that only
    handle plain string content. New code should use
    ``_messages_to_openai`` which handles Anthropic-style blocks.
    """
    content = m.content if isinstance(m.content, str) else ""
    return {"role": m.role, "content": content}


def _messages_to_openai(messages: list[Message]) -> list[dict[str, Any]]:
    """Translate Echo messages to OpenAI chat-completion shape.

    Echo internally uses Anthropic-style block lists for
    multi-turn tool flows:

        assistant · content=[{"type":"text",...}, {"type":"tool_use", id, name, input}]
        user      · content=[{"type":"tool_result", tool_use_id, content, [is_error]}]

    OpenAI's schema is different:

        assistant · {"role":"assistant", "content":"...", "tool_calls":[{id, type:"function", function:{name, arguments}}]}
        tool      · {"role":"tool", "tool_call_id":"...", "content":"..."}

    This translator does the mapping. A plain-string content
    message passes through unchanged.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m.content, str):
            # Fast path · plain string · pass through.
            out.append({"role": m.role, "content": m.content})
            continue

        # Block-list content · need to split and re-shape.
        blocks = m.content if isinstance(m.content, list) else []

        if m.role == "assistant":
            # Collect text + tool_use blocks into a single
            # assistant message.
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")
                if btype == "text":
                    text_parts.append(str(b.get("text", "")))
                elif btype == "tool_use":
                    args = b.get("input") or {}
                    tool_calls.append(
                        {
                            "id": b.get("id") or "",
                            "type": "function",
                            "function": {
                                "name": b.get("name") or "",
                                "arguments": json.dumps(
                                    args,
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    )
            msg: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(text_parts),
            }
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
            continue

        if m.role == "user":
            # Separate tool_result blocks from text content.
            # Tool results become standalone ``{"role":"tool"}``
            # messages; any stray text goes into a user message.
            #
            # Image blocks must survive as blocks: a user upload arrives as
            # an ``image_url`` block (built by ``_react_context_attachments``)
            # and OpenAI's only way to carry it is multimodal list content.
            # Collapsing the message to a joined string here silently dropped
            # every uploaded image — the model was told about the text and
            # never saw the picture.
            text_parts = []
            image_blocks: list[dict[str, Any]] = []
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")
                if btype == "tool_result":
                    content = b.get("content") or ""
                    if not isinstance(content, str):
                        content = json.dumps(
                            content,
                            ensure_ascii=False,
                            default=str,
                        )
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": b.get("tool_use_id") or "",
                            "content": content,
                        }
                    )
                elif btype == "text":
                    text_parts.append(str(b.get("text", "")))
                elif btype in ("image_url", "image"):
                    normalized = _image_block_to_openai(b)
                    if normalized is not None:
                        image_blocks.append(normalized)
            if image_blocks:
                # Text first mirrors ``_build_user_message_content``'s order
                # and how vision providers expect the prompt to read.
                content_blocks: list[dict[str, Any]] = []
                joined = "".join(text_parts)
                if joined:
                    content_blocks.append({"type": "text", "text": joined})
                content_blocks.extend(image_blocks)
                out.append({"role": "user", "content": content_blocks})
            elif text_parts:
                out.append(
                    {
                        "role": "user",
                        "content": "".join(text_parts),
                    }
                )
            continue

        # System (or unknown role) · stringify best-effort.
        text_parts = [
            str(b.get("text", ""))
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        out.append({"role": m.role, "content": "".join(text_parts)})
    return out


def _image_block_to_openai(block: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one image block to OpenAI's ``image_url`` shape.

    Accepts both the OpenAI shape we build internally and the Anthropic
    ``{"type": "image", "source": {...}}`` variant, so a message that took
    a detour through an Anthropic-shaped path still delivers its picture.
    Returns ``None`` when the block carries no usable reference — dropping
    an empty block beats sending one the upstream will 400 on.
    """
    if block.get("type") == "image_url":
        raw = block.get("image_url")
        url = raw.get("url") if isinstance(raw, dict) else raw
        if not isinstance(url, str) or not url:
            return None
        return {"type": "image_url", "image_url": {"url": url}}
    source = block.get("source")
    if not isinstance(source, dict):
        return None
    if source.get("type") == "url":
        url = source.get("url")
        return (
            {"type": "image_url", "image_url": {"url": url}}
            if isinstance(url, str) and url
            else None
        )
    data = source.get("data")
    if not isinstance(data, str) or not data:
        return None
    media_type = str(source.get("media_type") or "image/png")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{data}"},
    }


def _attach_images_to_last_user_openai(
    msgs: list[dict[str, Any]],
    images_b64: list[str],
) -> None:
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "user":
            existing = msgs[i].get("content", "")
            screenshots: list[dict[str, Any]] = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                }
                for b64 in images_b64
            ]
            if isinstance(existing, list):
                # The message already carries multimodal blocks (a user
                # upload). Append the screenshots instead of wrapping the
                # whole list into a text block, which would both stringify
                # the upload away and 400 on strict providers.
                msgs[i]["content"] = list(existing) + screenshots
                return
            blocks: list[dict[str, Any]] = list(screenshots)
            if existing:
                blocks.append({"type": "text", "text": existing})
            msgs[i]["content"] = blocks
            return
