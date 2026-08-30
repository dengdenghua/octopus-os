"""Response-parsing helpers for OpenAI-compatible providers.

Extracted from ``openai_compat_providers.py``.  These pure functions
parse provider responses (reasoning text extraction, usage accounting,
and tool-call argument decoding including the XML ``<parameter>``
fallback) without depending on the provider profile catalog or the
request/retry engine.  ``openai_compat_providers.py`` re-exports the
public entry points so existing import sites continue to work.
"""

from __future__ import annotations

import ast
import html
import json
import re
from typing import Any


def extract_openai_compat_reasoning(message: dict[str, Any]) -> str:
    pieces: list[str] = []
    for key in (
        "reasoning_content",
        "reasoning",
        "thinking",
        "reasoning_text",
        "thought",
    ):
        value = message.get(key)
        rendered = _render_reasoning_value(value)
        if rendered:
            pieces.append(rendered)

    details = _render_reasoning_value(message.get("reasoning_details"))
    if details:
        pieces.append(details)

    return "\n".join(piece for piece in pieces if piece)


def extract_openai_compat_usage(data: dict[str, Any]) -> tuple[int, int]:
    usage = _coerce_usage(data.get("usage"))
    if usage is None:
        choices = data.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                usage = _coerce_usage(choice.get("usage"))
                if usage is not None:
                    break
    if usage is None:
        return 0, 0
    return (
        _int_from_any(
            usage.get("prompt_tokens")
            or usage.get("input_tokens")
            or usage.get("promptTokens")
            or usage.get("inputTokens")
        ),
        _int_from_any(
            usage.get("completion_tokens")
            or usage.get("output_tokens")
            or usage.get("completionTokens")
            or usage.get("outputTokens")
        ),
    )


_XML_PARAMETER_RE = re.compile(
    r"<parameter\b(?P<attrs>[^>]*)>(?P<value>.*?)</parameter>",
    re.IGNORECASE | re.DOTALL,
)
_XML_PARAMETER_NAME_RE = re.compile(
    r"\bname\s*=\s*(['\"])(?P<name>[^'\"]+)\1",
    re.IGNORECASE,
)


def _xml_parameter_arguments(text: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for match in _XML_PARAMETER_RE.finditer(text):
        attrs = match.group("attrs") or ""
        name_match = _XML_PARAMETER_NAME_RE.search(attrs)
        if name_match is None:
            continue
        name = html.unescape(name_match.group("name")).strip()
        if not name:
            continue
        raw = html.unescape(match.group("value") or "").strip()
        if re.search(r"\bstring\s*=\s*(['\"])true\1", attrs, re.IGNORECASE):
            parsed[name] = raw
            continue
        if raw.lower() == "true":
            parsed[name] = True
        elif raw.lower() == "false":
            parsed[name] = False
        elif raw.lower() in {"null", "none"}:
            parsed[name] = None
        else:
            try:
                decoded = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                decoded = raw
            parsed[name] = decoded
    return parsed


def _normalize_tool_argument_mapping(parsed: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(parsed)
    wrapper_keys: list[str] = []
    recovered: dict[str, Any] = {}
    for key, raw in parsed.items():
        if not isinstance(raw, str) or "<parameter" not in raw.lower():
            continue
        xml_args = _xml_parameter_arguments(raw)
        if xml_args:
            wrapper_keys.append(key)
            recovered.update(xml_args)
    for key in wrapper_keys:
        normalized.pop(key, None)
    normalized.update(recovered)
    return normalized


def parse_tool_call_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return _normalize_tool_argument_mapping(value)
    if value is None:
        return {}
    text = value if isinstance(value, str) else str(value)
    text = text.strip()
    if not text:
        return {}

    parsed: Any
    try:
        parsed = json.loads(text)
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
        return _normalize_tool_argument_mapping(parsed) if isinstance(parsed, dict) else {}
    except (
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):  # expected · falls through to the ast.literal_eval fallback below
        pass

    try:
        parsed = ast.literal_eval(text)
        return _normalize_tool_argument_mapping(parsed) if isinstance(parsed, dict) else {}
    except (SyntaxError, ValueError, TypeError):
        return _xml_parameter_arguments(text)


def _render_reasoning_value(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, int | float | bool):
        return str(value)
    if isinstance(value, list):
        pieces = [_render_reasoning_detail(item) for item in value]
        return "\n".join(piece for piece in pieces if piece)
    if isinstance(value, dict):
        return _render_reasoning_detail(value)
    return json.dumps(value, ensure_ascii=False, default=str)


def _render_reasoning_detail(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return _render_reasoning_value(value)
    for key in ("text", "content", "reasoning", "summary", "delta"):
        item = value.get(key)
        if isinstance(item, str) and item:
            return item
    return json.dumps(value, ensure_ascii=False, default=str)


def _coerce_usage(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _int_from_any(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        match = re.search(r"\d+", str(value or ""))
        return int(match.group(0)) if match else 0


# Some OpenAI-compat providers emit reasoning INLINE in ``content``, wrapped
# in ``<think>`` tags, and send no ``reasoning_content`` field at all
# (measured on minimax-m3: content is
# ``"<think>\nThe user is asking...\n</think>\n4"``). Without splitting it,
# the model's private reasoning is shown to the user as the answer, and every
# downstream consumer that parses the answer — ReAct's Final Answer matcher,
# tool-call extraction, verification guards — sees the reasoning first.
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


def split_inline_reasoning(text: str) -> tuple[str, str]:
    """Split ``<think>``-wrapped reasoning out of a content string.

    Returns ``(content, reasoning)``. Text outside the tags is content;
    text inside is reasoning. An unclosed ``<think>`` treats the rest as
    reasoning, which is the right reading for a truncated generation.
    Strings with no opening tag are returned unchanged so the overwhelmingly
    common case costs one substring check.
    """
    if _THINK_OPEN not in text:
        return text, ""
    content: list[str] = []
    reasoning: list[str] = []
    rest = text
    while True:
        head, sep, tail = rest.partition(_THINK_OPEN)
        content.append(head)
        if not sep:
            break
        thought, closed, rest = tail.partition(_THINK_CLOSE)
        reasoning.append(thought)
        if not closed:
            break
    return "".join(content).strip(), "\n".join(reasoning).strip()


class InlineReasoningSplitter:
    """Streaming counterpart to :func:`split_inline_reasoning`.

    A ``<think>`` tag can straddle two SSE chunks (``"<thi"`` / ``"nk>"``),
    so the split cannot be done per-chunk in isolation. Feed each content
    delta through :meth:`feed`; it returns the ``(content, reasoning)``
    fragments that are safe to emit now and buffers any trailing text that
    could still turn out to be a partial tag.

    Call :meth:`flush` at end of stream to release a buffered partial tag as
    content — if it never completed, it was literal text.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._in_thinking = False

    def feed(self, delta: str) -> tuple[str, str]:
        if not delta:
            return "", ""
        self._buffer += delta
        content: list[str] = []
        reasoning: list[str] = []
        while self._buffer:
            if self._in_thinking:
                thought, closed, rest = self._buffer.partition(_THINK_CLOSE)
                if not closed:
                    # Hold back only what could be a partial closing tag;
                    # everything before it is settled reasoning.
                    keep = _partial_tag_suffix_len(self._buffer, _THINK_CLOSE)
                    if keep:
                        reasoning.append(self._buffer[:-keep])
                        self._buffer = self._buffer[-keep:]
                    else:
                        reasoning.append(self._buffer)
                        self._buffer = ""
                    break
                reasoning.append(thought)
                self._buffer = rest
                self._in_thinking = False
                continue
            head, sep, rest = self._buffer.partition(_THINK_OPEN)
            if not sep:
                keep = _partial_tag_suffix_len(self._buffer, _THINK_OPEN)
                if keep:
                    content.append(self._buffer[:-keep])
                    self._buffer = self._buffer[-keep:]
                else:
                    content.append(self._buffer)
                    self._buffer = ""
                break
            content.append(head)
            self._buffer = rest
            self._in_thinking = True
        return "".join(content), "".join(reasoning)

    def flush(self) -> tuple[str, str]:
        """Release the tail. A never-completed tag was literal text."""
        tail, self._buffer = self._buffer, ""
        if self._in_thinking:
            self._in_thinking = False
            return "", tail
        return tail, ""

    @property
    def saw_inline_reasoning(self) -> bool:
        return self._in_thinking


def _partial_tag_suffix_len(text: str, tag: str) -> int:
    """Length of the longest suffix of ``text`` that prefixes ``tag``.

    ``"answer: <thi"`` against ``"<think>"`` returns 4 — that much must be
    buffered because the next chunk may complete the tag.
    """
    limit = min(len(text), len(tag) - 1)
    for size in range(limit, 0, -1):
        if tag.startswith(text[-size:]):
            return size
    return 0
