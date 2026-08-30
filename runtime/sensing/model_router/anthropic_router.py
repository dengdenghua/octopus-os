# ruff: noqa: E402 — module-level imports below are intentionally late

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from typing import Any

try:
    import anthropic  # type: ignore[import-untyped]

    ANTHROPIC_AVAILABLE = True
except ImportError:  # pragma: no cover
    ANTHROPIC_AVAILABLE = False
    anthropic = None  # type: ignore[assignment]

from runtime.adapters.instrumentation import record_gen_ai_cost, trace_stage
from runtime.platform.models import CostEntry

_logger = logging.getLogger(__name__)

from .models import (
    Message,
    ModelRequest,
    ModelResponse,
    ModelRouter,
    thinking_budget_for_effort,
)

_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-haiku-4-5": (0.80, 4.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-6-20250514": (3.00, 15.00),
    "claude-opus-4-7": (15.00, 75.00),
    "claude-opus-4-7-20250805": (15.00, 75.00),
}
_FALLBACK_PRICE = (3.00, 15.00)  # Implementation note.


def _estimate_usd(model: str, in_tok: int, out_tok: int) -> float:
    in_price, out_price = _PRICING.get(model, _FALLBACK_PRICE)
    return (in_tok * in_price + out_tok * out_price) / 1_000_000.0


from .provider import Provider, ProviderCapabilities


class AnthropicModelRouter(Provider, ModelRouter):
    provider_name = "anthropic"
    capabilities = ProviderCapabilities(
        supports_vision=True,
        supports_tool_use=True,
        supports_streaming=True,
        supports_prompt_cache=True,
        supports_structured_output=True,
        default_model="claude-haiku-4-5-20251001",
        pricing_hint="mid",
    )

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "claude-haiku-4-5-20251001",
        client: Any = None,
        base_url: str | None = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            if not ANTHROPIC_AVAILABLE:
                raise RuntimeError(
                    "anthropic SDK not installed; `pip install anthropic` or pass client=..."
                )
            key = (
                api_key
                or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.environ.get("ANTHROPIC_API_KEY")
            )
            if not key:
                raise RuntimeError(
                    "no ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN in env and api_key not passed"
                )
            client_kwargs: dict[str, Any] = {}
            if base_url:
                client_kwargs["base_url"] = base_url.rstrip("/")
                # Non-official endpoint → Bearer
                client_kwargs["auth_token"] = key
            else:
                # Official Anthropic API → x-api-key
                client_kwargs["api_key"] = key
            self._client = anthropic.Anthropic(**client_kwargs)
        self.default_model = default_model

    def call(self, request: ModelRequest) -> ModelResponse:
        model = request.model or self.default_model
        with trace_stage(
            "eyes.anthropic_call",
            **{"echo.model": model},
        ) as span:
            # Cancellation check: reject before firing the network
            # call when the caller has already tripped the token.
            # Mid-call cancellation is not possible for the non-stream
            # path (Anthropic's sync SDK blocks until the full response
            # is back) — use call_stream for fine-grained cancellation.
            from runtime.safety.approval.cancellation import current_cancellation_token

            current_cancellation_token().throw_if_cancelled()

            system_text, user_assistant_msgs = _split_system(request.messages)

            if request.images_b64 and user_assistant_msgs:
                _attach_images_to_last_user(
                    user_assistant_msgs,
                    request.images_b64,
                )

            system_arg: Any = None
            if system_text:
                if _should_cache_system(system_text):
                    system_arg = [
                        {
                            "type": "text",
                            "text": system_text,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
                else:
                    system_arg = system_text

            # Extended thinking · capable models (Sonnet 4+, Opus 4+)
            # emit a separate thinking block when ``thinking={"type":
            # "enabled", ...}`` is set. The Anthropic API requires
            # ``temperature=1.0`` while thinking is on; we quietly
            # lift the caller's temperature to meet that constraint so
            # planner / react_loop call sites don't need to know.
            create_kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
                "system": system_arg,
                "messages": user_assistant_msgs,
            }
            # Same tools-vs-thinking priority as call_stream · see
            # the long comment there.
            if request.tools:
                from .prompt_cache import prepare_cached_tools

                tool_dicts = [t.model_dump() for t in request.tools]
                create_kwargs["tools"] = prepare_cached_tools(tool_dicts)
                if request.require_tool_use:
                    create_kwargs["tool_choice"] = {"type": "any"}
            elif request.enable_thinking:
                # Budget must be strictly < max_tokens. If the caller
                # set a small max_tokens we clamp the budget.
                requested_budget = (
                    thinking_budget_for_effort(
                        request.reasoning_effort,
                        request.max_tokens,
                    )
                    if request.reasoning_effort
                    else request.thinking_budget
                )
                budget = min(
                    requested_budget,
                    max(1024, request.max_tokens - 256),
                )
                create_kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": budget,
                }
                # Thinking mode requires temperature=1 per Anthropic's
                # API contract (any other value → 400). Overriding
                # silently is the right move because the caller's
                # intent is "I want thinking", not "I want exactly
                # temp=0.3".
                create_kwargs["temperature"] = 1.0

            # Thinking-pipeline trace · DEBUG so production stays
            # quiet. Flip ``logging.getLogger("echo.thinking")``
            # to DEBUG when investigating "why didn't my thinking
            # block show up" — the pair of records below tell you
            # whether the request carried the ``thinking`` param
            # and what block types the response returned.
            import logging as _lg

            _tlog = _lg.getLogger("echo.thinking")
            if _tlog.isEnabledFor(_lg.DEBUG):
                _tlog.debug(
                    "anthropic.call · model=%s enable_thinking=%s "
                    "has_thinking_kwarg=%s temp=%s max_tokens=%s",
                    model,
                    request.enable_thinking,
                    "thinking" in create_kwargs,
                    create_kwargs.get("temperature"),
                    create_kwargs.get("max_tokens"),
                )
            try:
                resp = self._client.messages.create(**create_kwargs)
                if _tlog.isEnabledFor(_lg.DEBUG):
                    _ctypes = [
                        getattr(b, "type", "?") for b in (getattr(resp, "content", None) or [])
                    ]
                    _tlog.debug(
                        "anthropic.resp · model=%s content_types=%r",
                        model,
                        _ctypes,
                    )
            except Exception as exc:
                # Provider outage / rate limit / auth failure ·
                # dispatch a community-visible Notification so
                # subscribers can page / failover / cache-serve.
                # Then re-raise · we DON'T swallow · caller's
                # MultiModelRouter retry logic still runs.
                try:
                    from runtime.safety.hooks.runner import (
                        dispatch_notification,
                    )

                    # Distinguish rate-limit (transient · client should
                    # backoff) from generic provider outage. The
                    # anthropic SDK raises RateLimitError · we match
                    # by class-name substring so this works whether
                    # the SDK is installed or a duck-typed test
                    # client is in use.
                    err_name = type(exc).__name__
                    is_rate = "RateLimit" in err_name or "429" in str(exc)
                    dispatch_notification(
                        kind="rate_limit" if is_rate else "provider_down",
                        details={
                            "provider": "anthropic",
                            "model": model,
                            "error_type": err_name,
                            "error_message": str(exc)[:200],
                        },
                    )
                except (ConnectionError, TimeoutError, TypeError, ValueError):  # noqa: BLE001 — notification send failed; re-raise original
                    pass
                raise

            text, thinking = _extract_text_and_thinking(resp)
            in_tok = getattr(resp.usage, "input_tokens", 0) if hasattr(resp, "usage") else 0
            out_tok = getattr(resp.usage, "output_tokens", 0) if hasattr(resp, "usage") else 0
            # Cache telemetry — Anthropic returns these when
            # cache_control blocks are used. Missing on older SDK
            # versions → default to 0.
            cache_read = (
                getattr(resp.usage, "cache_read_input_tokens", 0) if hasattr(resp, "usage") else 0
            ) or 0
            cache_create = (
                getattr(resp.usage, "cache_creation_input_tokens", 0)
                if hasattr(resp, "usage")
                else 0
            ) or 0
            usd = _estimate_usd(model, in_tok, out_tok)

            record_gen_ai_cost(
                span,
                system="anthropic",
                model=model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                usd=usd,
                finish_reasons=[getattr(resp, "stop_reason", None) or "stop"],
            )

            # Extract tool_use blocks (native tool_use path).
            from .models import ToolCall

            tool_calls: list[ToolCall] = []
            for block in getattr(resp, "content", None) or []:
                if getattr(block, "type", None) == "tool_use":
                    inp = getattr(block, "input", None)
                    tool_calls.append(
                        ToolCall(
                            id=getattr(block, "id", "") or "",
                            name=getattr(block, "name", "") or "",
                            input=inp if isinstance(inp, dict) else {},
                        )
                    )

            return ModelResponse(
                text=text,
                thinking=thinking,
                tool_calls=tool_calls,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_read_tokens=cache_read,
                cache_creation_tokens=cache_create,
                cost=CostEntry(
                    tokens_in=in_tok,
                    tokens_out=out_tok,
                    usd=usd,
                ),
                finish_reason=getattr(resp, "stop_reason", None) or "stop",
                model=model,
                provider="anthropic",
            )

    def call_stream(self, request: ModelRequest):
        """Real streaming via Anthropic SDK's ``messages.stream()``.

        Yields ``ModelStreamEvent`` in roughly this shape:

            thinking_delta* · 0..N chunks of the extended-thinking
                              trace, emitted as the server produces
                              them. Only present when the provider
                              actually supports thinking blocks and
                              honored our request flag — proxies
                              like ``claude-mirror`` that strip
                              ``thinking=enabled`` emit no thinking
                              events and the reasoning comes through
                              as inline ``<thinking>`` XML inside
                              text_delta instead (the SSE consumer
                              cleans that up on the ``done`` event).
            text_delta+       · 1..N chunks of the visible reply body.
            done              · exactly once, carrying the aggregated
                                ``ModelResponse`` so cost tracking and
                                the upstream ``_extract_text_and_thinking``
                                re-clean pass run against the final
                                message, not a partial view.

        Implementation notes:
            * Re-uses the same ``create_kwargs`` shape as ``call()``
              (system prompt, thinking params, cache_control) — the
              only difference is ``messages.stream()`` vs
              ``messages.create()``. Keeping the two paths symmetric
              means any request-side fix in one lands in the other
              too.
            * The ``done`` event's ``final`` carries a
              ``ModelResponse`` built from the SDK's
              ``stream.get_final_message()``, not from the deltas
              we accumulated — so cost/usage numbers come from the
              authoritative server count.
            * Errors mirror ``call()`` behavior: dispatch a
              ``rate_limit`` / ``provider_down`` notification, then
              re-raise. Streaming consumers get an exception out of
              ``next()`` / ``for`` on the generator; they should
              treat it the same way as a ``call()`` exception.
        """
        from .models import ModelStreamEvent

        model = request.model or self.default_model
        with trace_stage(
            "eyes.anthropic_stream",
            **{"echo.model": model},
        ) as span:
            system_text, user_assistant_msgs = _split_system(request.messages)
            if request.images_b64 and user_assistant_msgs:
                _attach_images_to_last_user(
                    user_assistant_msgs,
                    request.images_b64,
                )

            system_arg: Any = None
            if system_text:
                if _should_cache_system(system_text):
                    system_arg = [
                        {
                            "type": "text",
                            "text": system_text,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
                else:
                    system_arg = system_text

            create_kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
                "system": system_arg,
                "messages": user_assistant_msgs,
            }
            # Tool catalog · when the caller opted into native
            # tool_use, translate our ``ToolSpec`` objects into
            # Anthropic's expected dict shape. Tools and thinking
            # are mutually exclusive per the API contract — tools
            # wins because the caller explicitly passed a tool list
            # (whereas enable_thinking defaults to on for Claude 4+
            # via our fast-path heuristic).
            if request.tools:
                from .prompt_cache import prepare_cached_tools

                tool_dicts = [t.model_dump() for t in request.tools]
                create_kwargs["tools"] = prepare_cached_tools(tool_dicts)
                # ``{"type": "any"}`` forces *some* tool call without naming
                # which — the agentic loop asks for it after a prose-only
                # round so narration is not an available decode. Omitted
                # otherwise so the default (auto) semantics are unchanged.
                # See ``_LoopState.zero_action_rounds``.
                if request.require_tool_use:
                    create_kwargs["tool_choice"] = {"type": "any"}
            elif request.enable_thinking:
                requested_budget = (
                    thinking_budget_for_effort(
                        request.reasoning_effort,
                        request.max_tokens,
                    )
                    if request.reasoning_effort
                    else request.thinking_budget
                )
                budget = min(
                    requested_budget,
                    max(1024, request.max_tokens - 256),
                )
                create_kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": budget,
                }
                create_kwargs["temperature"] = 1.0

            # Per-tool_use state · Anthropic streams tool_use
            # content as a ``content_block_start`` (carrying name
            # and id) then a sequence of ``content_block_delta``
            # with ``type=input_json_delta`` chunks, then a
            # ``content_block_stop``. We buffer partial JSON here
            # and emit one ``tool_use`` event per completed block.
            _tool_id: str | None = None
            _tool_name: str | None = None
            _tool_input_buf: list[str] = []

            final_msg: Any = None
            try:
                # Cancellation: import lazily so the safety subpackage
                # stays an optional dependency at import time. Polled
                # before opening the stream and again per-chunk below.
                from runtime.safety.approval.cancellation import (
                    OperationCancelled,
                    current_cancellation_token,
                )

                _cancel_token = current_cancellation_token()
                _cancel_token.throw_if_cancelled()

                with self._client.messages.stream(**create_kwargs) as stream:
                    for event in stream:
                        # Per-chunk cancellation check. If the client
                        # disconnected mid-stream we close the underlying
                        # stream and stop billing immediately.
                        if _cancel_token.is_cancelled:
                            with contextlib.suppress(Exception):
                                stream.close()
                            raise OperationCancelled(
                                _cancel_token.reason or "cancelled mid-stream",
                            )
                        etype = getattr(event, "type", None)
                        # Tool-use block lifecycle ·
                        #   content_block_start · block announces
                        #     ``type=tool_use`` with name + id
                        #   content_block_delta with
                        #     ``type=input_json_delta`` · buffered
                        #     chunks of the input JSON
                        #   content_block_stop · flush the buffer,
                        #     parse, emit ``tool_use`` event
                        if etype == "content_block_start":
                            block = getattr(event, "content_block", None)
                            if block is not None and getattr(block, "type", None) == "tool_use":
                                _tool_id = getattr(block, "id", None)
                                _tool_name = getattr(block, "name", None)
                                _tool_input_buf = []
                            continue
                        if etype == "content_block_stop":
                            if _tool_id and _tool_name:
                                raw_json = "".join(_tool_input_buf)
                                try:
                                    parsed = __import__("json").loads(raw_json) if raw_json else {}
                                except json.JSONDecodeError:
                                    parsed = {}
                                from .models import ToolCall

                                yield ModelStreamEvent(
                                    type="tool_use",
                                    tool_call=ToolCall(
                                        id=_tool_id,
                                        name=_tool_name,
                                        input=parsed if isinstance(parsed, dict) else {},
                                    ),
                                )
                                _tool_id = None
                                _tool_name = None
                                _tool_input_buf = []
                            continue
                        if etype != "content_block_delta":
                            continue
                        delta = getattr(event, "delta", None)
                        if delta is None:
                            continue
                        dtype = getattr(delta, "type", None)
                        if dtype == "text_delta":
                            text = getattr(delta, "text", "") or ""
                            if text:
                                yield ModelStreamEvent(
                                    type="text_delta",
                                    delta=text,
                                )
                        elif dtype == "thinking_delta":
                            t = getattr(delta, "thinking", "") or ""
                            if t:
                                yield ModelStreamEvent(
                                    type="thinking_delta",
                                    delta=t,
                                )
                        elif dtype == "input_json_delta":
                            # Buffer · emitted as a single tool_use
                            # event on content_block_stop.
                            partial = getattr(delta, "partial_json", "") or ""
                            if partial:  # noqa: SIM102
                                # Guard against pathologically large tool
                                # inputs (e.g. base64-encoded blobs). The
                                # Anthropic API enforces its own limits, but
                                # a defensive cap prevents unbounded memory
                                # growth if those limits ever change.
                                if sum(len(s) for s in _tool_input_buf) < 1_000_000:
                                    _tool_input_buf.append(partial)
                        # signature_delta and other unknown delta
                        # types are ignored · not relevant to chat.
                    final_msg = stream.get_final_message()
            except Exception as exc:
                try:
                    from runtime.safety.hooks.runner import (
                        dispatch_notification,
                    )

                    err_name = type(exc).__name__
                    is_rate = "RateLimit" in err_name or "429" in str(exc)
                    dispatch_notification(
                        kind="rate_limit" if is_rate else "provider_down",
                        details={
                            "provider": "anthropic",
                            "model": model,
                            "error_type": err_name,
                            "error_message": str(exc)[:200],
                        },
                    )
                except (ConnectionError, TimeoutError, TypeError, ValueError) as exc:
                    _logger.debug("notification dispatch failed: %s", exc)
                raise

            text, thinking = _extract_text_and_thinking(final_msg)
            in_tok = (
                getattr(final_msg.usage, "input_tokens", 0) if hasattr(final_msg, "usage") else 0
            )
            out_tok = (
                getattr(final_msg.usage, "output_tokens", 0) if hasattr(final_msg, "usage") else 0
            )
            cache_read = (
                getattr(final_msg.usage, "cache_read_input_tokens", 0)
                if hasattr(final_msg, "usage")
                else 0
            ) or 0
            cache_create = (
                getattr(final_msg.usage, "cache_creation_input_tokens", 0)
                if hasattr(final_msg, "usage")
                else 0
            ) or 0
            usd = _estimate_usd(model, in_tok, out_tok)

            record_gen_ai_cost(
                span,
                system="anthropic",
                model=model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                usd=usd,
                finish_reasons=[
                    getattr(final_msg, "stop_reason", None) or "stop",
                ],
            )

            # Harvest tool_calls from the final message · these are
            # also emitted live via ``tool_use`` events during the
            # stream, but the ``done`` frame's ``ModelResponse``
            # carries the full list for consumers that want a
            # single source of truth.
            from .models import ToolCall

            tool_calls: list[ToolCall] = []
            for block in getattr(final_msg, "content", None) or []:
                if getattr(block, "type", None) == "tool_use":
                    inp = getattr(block, "input", None)
                    tool_calls.append(
                        ToolCall(
                            id=getattr(block, "id", "") or "",
                            name=getattr(block, "name", "") or "",
                            input=inp if isinstance(inp, dict) else {},
                        )
                    )

            yield ModelStreamEvent(
                type="done",
                final=ModelResponse(
                    text=text,
                    thinking=thinking,
                    tool_calls=tool_calls,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    cache_read_tokens=cache_read,
                    cache_creation_tokens=cache_create,
                    cost=CostEntry(
                        tokens_in=in_tok,
                        tokens_out=out_tok,
                        usd=usd,
                    ),
                    finish_reason=(getattr(final_msg, "stop_reason", None) or "stop"),
                    model=model,
                    provider="anthropic",
                ),
            )


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════


# Anthropic's ephemeral cache requires a minimum block size to actually
# save money. Below this threshold, caching overhead can even cost more.
# 1024 tokens ≈ 4000 chars for English, ~1600 for Chinese. Use 3000 as a
# conservative byte-level threshold that works for both.
_CACHE_MIN_CHARS = 3000


def _should_cache_system(text: str) -> bool:
    # Keep the local threshold in sync with prompt_cache.MIN_CACHE_CHARS
    # but tolerate the import being absent in stripped-down deployments.
    from .prompt_cache import MIN_CACHE_CHARS as _MCC

    return len(text) >= max(_CACHE_MIN_CHARS, _MCC)


def _split_system(messages: list[Message]) -> tuple[str, list[dict]]:
    sys_parts: list[str] = []
    rest: list[dict] = []
    for m in messages:
        if m.role == "system":
            sys_parts.append(m.content)
        else:
            content = m.content
            # Normalize OpenAI-shaped ``image_url`` content blocks (built
            # by ``_react_context_attachments`` from user uploads) into
            # Anthropic's ``image`` block shape. Passed verbatim they
            # 400 the whole request on any model — the vision guard's
            # crash-recovery would then strip the user's image entirely,
            # so translating here is what actually delivers uploads on
            # the default anthropic route. ``images_b64`` screenshots
            # are attached separately by ``_attach_images_to_last_user``.
            if isinstance(content, list):
                content = [_normalize_image_block(b) for b in content]
            rest.append({"role": m.role, "content": content})
    return "\n\n".join(sys_parts), rest


def _normalize_image_block(block: dict[str, Any]) -> dict[str, Any]:
    """Translate one OpenAI ``image_url`` block to an Anthropic ``image``.

    Data URLs split into a base64 ``source``; https URLs pass through as
    a remote ``url`` source. Anthropic-shaped ``image`` blocks and every
    other block type are returned unchanged.
    """
    if block.get("type") != "image_url":
        return block
    raw = block.get("image_url")
    url = raw.get("url") if isinstance(raw, dict) else raw
    if not isinstance(url, str) or not url:
        return block
    if url.startswith("data:"):
        header, _, data = url.partition(",")
        media_type = header[len("data:") :].split(";", 1)[0] or "image/png"
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }
    return {"type": "image", "source": {"type": "url", "url": url}}


def _attach_images_to_last_user(
    msgs: list[dict[str, Any]],
    images_b64: list[str],
) -> None:
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "user":
            image_blocks: list[dict[str, Any]] = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64,
                    },
                }
                for b64 in images_b64
            ]
            content = msgs[i].get("content", "")
            if isinstance(content, list):
                # The user message already carries inline blocks (e.g.
                # uploads normalized by ``_normalize_image_block``) —
                # append the screenshots instead of wrapping the whole
                # list as a text block (which would 400).
                msgs[i]["content"] = list(content) + image_blocks
            else:
                blocks = list(image_blocks)
                if content:
                    blocks.append({"type": "text", "text": content})
                msgs[i]["content"] = blocks
            return


def _extract_text_and_thinking(resp: Any) -> tuple[str, str]:
    """Split Anthropic response blocks into (text, thinking).

    The content array can interleave ``TextBlock`` (``.text``),
    ``ThinkingBlock`` (``.thinking``, only when extended thinking is
    enabled on capable models), and ``ToolUseBlock`` (ignored here —
    tool use goes through a different plumbing path).

    Two shapes are supported because proxies (e.g. aicodemirror,
    cc-switch) don't always pass the ``thinking={type: enabled}``
    request flag through to the upstream Anthropic API. When the
    flag is dropped, Claude falls back to emitting the reasoning
    as inline ``<thinking>...</thinking>`` XML tags inside a text
    block instead of a separate ``ThinkingBlock``:

      1. Native ``ThinkingBlock`` (direct Anthropic API) →
         ``block.type == "thinking"`` → ``block.thinking``
      2. Inline ``<thinking>...</thinking>`` in text (proxy strips
         the param) → regex-extract, strip from visible text

    Callers get a single ``(text, thinking)`` tuple regardless of
    which shape the upstream returned. Previously shape 2 was
    silently dropped, and every proxy user saw raw ``<thinking>``
    XML tags in their chat bubble.
    """
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    content = getattr(resp, "content", None) or []
    for block in content:
        btype = getattr(block, "type", None)
        if btype == "thinking":
            t = getattr(block, "thinking", None)
            if t:
                thinking_parts.append(str(t))
        else:
            t = getattr(block, "text", None)
            if t:
                text_parts.append(str(t))
    raw_text = "\n".join(text_parts)
    # Shape-2 path · extract inline <thinking>…</thinking> tags.
    if "<thinking>" in raw_text:
        inline = re.findall(
            r"<thinking>(.*?)</thinking>",
            raw_text,
            flags=re.DOTALL,
        )
        if inline:
            thinking_parts.extend(p.strip() for p in inline if p.strip())
            # Remove the matched tags from visible text; trim any
            # leading whitespace left behind so the reply doesn't
            # start with a blank line.
            raw_text = re.sub(
                r"<thinking>.*?</thinking>\s*",
                "",
                raw_text,
                flags=re.DOTALL,
            ).lstrip()
    return raw_text, "\n".join(thinking_parts)


def _extract_text(resp: Any) -> str:
    """Backwards-compat shim · prefer ``_extract_text_and_thinking``.

    Kept because older tests / external plugins may import it
    directly. New code paths should use the tuple form so the
    thinking block isn't silently discarded.
    """
    text, _thinking = _extract_text_and_thinking(resp)
    return text
