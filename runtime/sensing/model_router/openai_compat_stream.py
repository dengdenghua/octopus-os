"""Shared OpenAI-compatible SSE stream parser.

Every provider that speaks the ``/v1/chat/completions`` wire format
(Oct, OpenAI, DeepSeek, Kimi, Gemini-compat, vLLM, Ollama, …)
emits the same SSE shape::

    data: {"choices":[{"delta":{"content":"tok"}}]}
    data: {"choices":[{"delta":{"content":"en"}}]}
    data: [DONE]

This module provides a single ``iter_openai_sse`` generator that
turns an ``httpx`` streaming response into ``ModelStreamEvent``
objects. Router subclasses call it from their ``call_stream()``
override — no per-provider SSE parsing needed.

Adding a new OpenAI-compat provider
------------------------------------

1. Subclass ``ModelRouter`` (or duck-type it).
2. In ``call_stream()``, open an ``httpx.stream("POST", ...)`` with
   ``stream=True`` in the JSON payload.
3. Pass the response to ``iter_openai_sse(response, model=..., provider=...)``.
4. ``yield from`` the result. Done.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

from .models import CostEntry, ModelResponse, ModelStreamEvent
from .openai_compat_providers import (
    InlineReasoningSplitter,
    extract_openai_compat_reasoning,
    extract_openai_compat_usage,
    parse_tool_call_arguments,
)


def iter_openai_sse(
    response: Any,
    *,
    model: str = "",
    provider: str = "openai_compat",
    cost_usd: float = 0.0,
    cancelled: Callable[[], bool] | None = None,
) -> Iterator[ModelStreamEvent]:
    """Parse an httpx streaming response of OpenAI-compat SSE chunks.

    Parameters
    ----------
    response
        An ``httpx.Response`` opened with ``client.stream(...)`` that
        exposes ``.iter_lines()``.
    model
        Model name echoed into the final ``ModelResponse``.
    provider
        Provider tag echoed into the final ``ModelResponse``.
    cost_usd
        Per-request cost estimate (most proxies don't report usage in
        streaming mode · caller can fill this from a post-hoc lookup).
    cancelled
        Optional probe for an explicit caller cancellation. A cancelled
        transport exits without logging a provider warning or emitting a
        synthetic ``done`` event.

    Yields
    ------
    ModelStreamEvent
        ``text_delta`` for each content fragment, ``tool_call_delta``
        for each tool-call fragment while it assembles, ``tool_use``
        once per completed call, ``done`` once at the end with the
        aggregated ``ModelResponse``.
    """
    accumulated: list[str] = []
    accumulated_reasoning: list[str] = []
    inline_reasoning_splitter = InlineReasoningSplitter()
    tool_state: dict[int, dict[str, Any]] = {}
    input_tokens = 0
    output_tokens = 0
    finish_reason = "stop"

    # Some OpenAI-compat upstreams (mimo, smaller community proxies)
    # finish the model's output but never send ``data: [DONE]`` and
    # never close the chunked transfer cleanly. Without catching the
    # ReadTimeout here, the producer thread is stuck inside
    # ``iter_lines`` indefinitely — the user sees "350 秒没新进展"
    # and the interrupt button can't unstick it. Treating a timeout
    # as a graceful stream end lets us yield a normal ``done`` event
    # so the ReAct loop can finalise on whatever text we accumulated.
    try:
        line_iter = response.iter_lines()
    except Exception:  # noqa: BLE001
        line_iter = iter(())

    event_data_lines: list[str] = []

    def dispatch_payload(payload: str):
        nonlocal input_tokens, output_tokens, finish_reason
        payload = payload.strip()
        if not payload:
            return False
        if payload == "[DONE]":
            return True
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            return False

        # Usage block · some providers (OpenAI, DeepSeek) include
        # ``usage`` on the final chunk even in streaming mode.
        chunk_input, chunk_output = extract_openai_compat_usage(chunk)
        if chunk_input or chunk_output:
            input_tokens = chunk_input
            output_tokens = chunk_output

        choices = chunk.get("choices") or []
        if not choices:
            return False
        # Capture finish_reason whenever it's present. OpenAI-compat
        # providers send it on the FINAL chunk for the active choice;
        # we keep the latest non-null value seen so the ReAct loop can
        # detect "length"-truncated generations and resume them.
        chunk_finish = choices[0].get("finish_reason")
        if isinstance(chunk_finish, str) and chunk_finish:
            finish_reason = chunk_finish
        delta = choices[0].get("delta") or {}
        reasoning_piece = extract_openai_compat_reasoning(delta)
        if reasoning_piece:
            accumulated_reasoning.append(reasoning_piece)
            yield ModelStreamEvent(
                type="thinking_delta",
                delta=reasoning_piece,
            )

        raw_piece = _render_content_delta(delta.get("content"))
        if raw_piece:
            # Providers that inline reasoning in ``content`` as ``<think>…``
            # (minimax-m3, measured) need it split out here, not downstream: a
            # tag can straddle two chunks, so the splitter buffers a partial
            # tag rather than emitting it as text.
            piece, inline_reasoning = inline_reasoning_splitter.feed(raw_piece)
            if inline_reasoning:
                accumulated_reasoning.append(inline_reasoning)
                yield ModelStreamEvent(type="thinking_delta", delta=inline_reasoning)
            if piece:
                accumulated.append(piece)
                yield ModelStreamEvent(type="text_delta", delta=piece)

        raw_tool_calls = delta.get("tool_calls") or []
        if isinstance(raw_tool_calls, list):
            for raw_call in raw_tool_calls:
                if not isinstance(raw_call, dict):
                    continue
                index = int(raw_call.get("index", 0) or 0)
                slot = tool_state.setdefault(
                    index,
                    {"id": "", "name": "", "arguments": ""},
                )
                if raw_call.get("id"):
                    slot["id"] = str(raw_call["id"])
                fn = raw_call.get("function")
                if isinstance(fn, dict):
                    if fn.get("name") and str(fn["name"]) != slot["name"]:
                        slot["name"] = str(fn["name"])
                        yield ModelStreamEvent(
                            type="tool_call_delta",
                            index=index,
                            call_id=slot["id"],
                            name=slot["name"],
                        )
                    args_piece = fn.get("arguments")
                    if isinstance(args_piece, dict):
                        args_json = json.dumps(
                            args_piece,
                            ensure_ascii=False,
                        )
                        slot["arguments"] = args_json
                        yield ModelStreamEvent(
                            type="tool_call_delta",
                            index=index,
                            call_id=slot["id"],
                            name=slot["name"],
                            arguments_delta=args_json,
                        )
                    elif args_piece:
                        args_fragment = str(args_piece)
                        slot["arguments"] += args_fragment
                        yield ModelStreamEvent(
                            type="tool_call_delta",
                            index=index,
                            call_id=slot["id"],
                            name=slot["name"],
                            arguments_delta=args_fragment,
                        )

        legacy_function_call = delta.get("function_call")
        if isinstance(legacy_function_call, dict):
            slot = tool_state.setdefault(
                0,
                {"id": "function_call_0", "name": "", "arguments": ""},
            )
            if (
                legacy_function_call.get("name")
                and str(legacy_function_call["name"]) != slot["name"]
            ):
                slot["name"] = str(legacy_function_call["name"])
                yield ModelStreamEvent(
                    type="tool_call_delta",
                    index=0,
                    call_id=slot["id"],
                    name=slot["name"],
                )
            args_piece = legacy_function_call.get("arguments")
            if isinstance(args_piece, dict):
                args_json = json.dumps(
                    args_piece,
                    ensure_ascii=False,
                )
                slot["arguments"] = args_json
                yield ModelStreamEvent(
                    type="tool_call_delta",
                    index=0,
                    call_id=slot["id"],
                    name=slot["name"],
                    arguments_delta=args_json,
                )
            elif args_piece:
                args_fragment = str(args_piece)
                slot["arguments"] += args_fragment
                yield ModelStreamEvent(
                    type="tool_call_delta",
                    index=0,
                    call_id=slot["id"],
                    name=slot["name"],
                    arguments_delta=args_fragment,
                )
        return False

    def dispatch_or_buffer(value: str):
        nonlocal event_data_lines
        if value == "[DONE]":
            return (yield from dispatch_payload(value))
        try:
            json.loads(value.strip())
        except json.JSONDecodeError:
            event_data_lines.append(value)
            return False
        return (yield from dispatch_payload(value))

    def flush_event() -> bool:
        if not event_data_lines:
            return False
        payload = "\n".join(event_data_lines)
        event_data_lines.clear()
        return (yield from dispatch_payload(payload))

    while True:
        try:
            line = next(line_iter)
        except StopIteration:
            break
        except Exception as exc:  # noqa: BLE001
            if cancelled is not None and cancelled():
                return
            # httpx.ReadTimeout, httpx.RemoteProtocolError, transport
            # errors all land here. Don't propagate — the caller (ReAct
            # loop) treats an empty "done" as a finished step and can
            # decide to re-prompt or finalise.
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "openai-compat stream cut short (%s) — finalising on accumulated text",
                exc.__class__.__name__,
            )
            break
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="ignore")
        line = line.rstrip("\r")
        if not line:
            if (yield from flush_event()):
                break
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            value = line[5:]
            value = value[1:] if value.startswith(" ") else value
            if event_data_lines:
                event_data_lines.append(value)
            else:
                if (yield from dispatch_or_buffer(value)):
                    break
            continue
        if not event_data_lines and (yield from dispatch_payload(line)):
            break

    if cancelled is not None and cancelled():
        return

    if event_data_lines:
        yield from flush_event()

    tool_calls: list[Any] = []
    if tool_state:
        from .models import ToolCall

        for index in sorted(tool_state):
            slot = tool_state[index]
            args_raw = slot.get("arguments") or ""
            args = parse_tool_call_arguments(args_raw)
            tool_calls.append(
                ToolCall(
                    id=str(slot.get("id") or ""),
                    name=str(slot.get("name") or ""),
                    input=args if isinstance(args, dict) else {},
                )
            )
            yield ModelStreamEvent(type="tool_use", tool_call=tool_calls[-1])

    # Release anything the inline-reasoning splitter was holding back as a
    # possible partial ``<think>`` tag. If the stream ended mid-tag it was
    # literal text, so it belongs in the answer.
    _tail_content, _tail_reasoning = inline_reasoning_splitter.flush()
    if _tail_reasoning:
        accumulated_reasoning.append(_tail_reasoning)
        yield ModelStreamEvent(type="thinking_delta", delta=_tail_reasoning)
    if _tail_content:
        accumulated.append(_tail_content)
        yield ModelStreamEvent(type="text_delta", delta=_tail_content)

    yield ModelStreamEvent(
        type="done",
        final=ModelResponse(
            text="".join(accumulated),
            thinking="".join(accumulated_reasoning),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            provider=provider,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            cost=CostEntry(
                tokens_in=input_tokens,
                tokens_out=output_tokens,
                usd=cost_usd,
            ),
        ),
    )


def _render_content_delta(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return json.dumps(value, ensure_ascii=False, default=str)
