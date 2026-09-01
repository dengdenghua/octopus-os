"""Schema-forced structured output for sub-agents.

Sub-agents return free text. When a caller passes a JSON Schema (the
``output_schema`` argument to :func:`runtime.execution.subagents.bridge.call_subagent`)
this module turns that free text into a *validated* object:

1. :func:`schema_instruction` appends a compact directive to the prompt asking
   the model to emit ONLY a JSON value matching the schema.
2. :func:`extract_json` pulls a single JSON value out of the (possibly chatty)
   reply — trying the whole string, then a fenced ``json`` block, then the
   first balanced ``{...}`` / ``[...]`` span.
3. :func:`validate_against_schema` checks the value against the JSON-Schema
   subset these agents actually use (``type`` / ``properties`` / ``required`` /
   ``items`` / ``enum`` / ``minItems``) with NO third-party dependency.
4. On mismatch :func:`schema_correction` produces a re-ask string so the caller
   can give the model one more shot.

Enforcement is deliberately post-hoc validation rather than a provider
``response_format``: it works on every model the router can reach (including the
cheap ``glm-4-flash`` tier) instead of only the endpoints that support native
structured output.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

__all__ = [
    "coerce_schema_output",
    "extract_json",
    "schema_correction",
    "schema_instruction",
    "validate_against_schema",
]

# Sentinel distinguishing "no JSON value found" from a legitimately-decoded
# ``null`` / ``0`` / ``""`` / ``[]`` value.
_MISSING: Any = object()

_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>.+?)```", re.DOTALL | re.IGNORECASE)

# Maximum chars of the prior reply echoed back in a correction prompt. Keeps the
# re-ask cheap while still showing the model what it got wrong.
_CORRECTION_ECHO_CHARS = 800


def _try_load(text: str) -> tuple[Any] | None:
    """Return ``(value,)`` if ``text`` is valid JSON, else ``None``.

    Wrapped in a 1-tuple so a decoded ``null`` (Python ``None``) is still
    reported as a success rather than mistaken for a parse failure.
    """
    try:
        return (json.loads(text),)
    except (json.JSONDecodeError, ValueError):
        return None


def _first_json_span(text: str) -> str | None:
    """Return the first balanced ``{...}`` or ``[...]`` substring, or ``None``.

    Scans character by character, tracking string literals (so braces inside
    quoted strings don't affect nesting depth) and escape sequences.
    """
    start = -1
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(text)):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return text[start : j + 1]
    return None


def extract_json(text: str) -> Any:
    """Best-effort extraction of one JSON value from a model reply.

    Tries, in order: the whole stripped string, each fenced ```` ```json ````
    block, then the first balanced object/array span. Returns the decoded value
    (which may itself be falsy, e.g. ``[]``) or the private ``_MISSING`` sentinel
    when nothing parses. Callers should compare against the value returned by
    :func:`coerce_schema_output` rather than truthiness.
    """
    if not isinstance(text, str):
        return _MISSING
    stripped = text.strip()
    if not stripped:
        return _MISSING
    whole = _try_load(stripped)
    if whole is not None:
        return whole[0]
    for match in _FENCE_RE.finditer(stripped):
        fenced = _try_load(match.group("body").strip())
        if fenced is not None:
            return fenced[0]
    span = _first_json_span(stripped)
    if span is not None:
        spanned = _try_load(span)
        if spanned is not None:
            return spanned[0]
    return _MISSING


_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    # bool is a subclass of int in Python — exclude it from number/integer so a
    # schema asking for an integer doesn't silently accept ``true``.
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}


def validate_against_schema(value: Any, schema: Any, *, path: str = "$") -> str | None:
    """Validate ``value`` against a JSON-Schema subset.

    Returns ``None`` when valid, otherwise a human-readable error string naming
    the offending JSON path. Supported keywords: ``type`` (single or list),
    ``enum``, ``properties``, ``required``, ``items``, ``minItems``. Unknown
    keywords are ignored (lenient), so richer schemas still validate on the
    parts this understands.
    """
    if not isinstance(schema, dict):
        return None

    declared = schema.get("type")
    if declared is not None:
        types = declared if isinstance(declared, list) else [declared]
        if not any(_TYPE_CHECKS.get(name, lambda _v: True)(value) for name in types):
            return f"{path}: expected type {declared!r}, got {type(value).__name__}"

    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: {value!r} is not one of {schema['enum']!r}"

    if isinstance(value, dict):
        for key in schema.get("required") or []:
            if key not in value:
                return f"{path}: missing required key {key!r}"
        for key, subschema in (schema.get("properties") or {}).items():
            if key in value:
                err = validate_against_schema(value[key], subschema, path=f"{path}.{key}")
                if err is not None:
                    return err

    if isinstance(value, list):
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            return f"{path}: expected at least {min_items} item(s), got {len(value)}"
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, element in enumerate(value):
                err = validate_against_schema(element, item_schema, path=f"{path}[{idx}]")
                if err is not None:
                    return err

    return None


def coerce_schema_output(text: str, schema: Any) -> tuple[bool, Any, str]:
    """Extract a JSON value from ``text`` and validate it against ``schema``.

    Returns ``(ok, value, error)``. On success ``ok`` is ``True``, ``value`` is
    the decoded+validated object (possibly falsy), and ``error`` is empty. On
    failure ``ok`` is ``False``, ``value`` is ``None``, and ``error`` describes
    the first problem found.
    """
    value = extract_json(text)
    if value is _MISSING:
        return False, None, "no JSON value found in output"
    err = validate_against_schema(value, schema)
    if err is not None:
        return False, None, err
    return True, value, ""


def schema_instruction(schema: Any) -> str:
    """Prompt suffix telling the model to emit only schema-valid JSON."""
    pretty = json.dumps(schema, ensure_ascii=False, indent=2)
    return (
        "\n\n---\n\n## OUTPUT FORMAT (REQUIRED)\n\n"
        "Return your final answer as a SINGLE JSON value that validates against "
        "this JSON Schema. Output ONLY the JSON — no prose, no markdown fences, "
        "no commentary before or after it.\n\n"
        "```json\n"
        f"{pretty}\n"
        "```"
    )


def schema_correction(error: str, schema: Any, raw: str) -> str:
    """Prompt suffix re-asking the model after a schema mismatch."""
    pretty = json.dumps(schema, ensure_ascii=False, indent=2)
    echo = raw.strip()[:_CORRECTION_ECHO_CHARS]
    return (
        "\n\n---\n\n## FORMAT CORRECTION\n\n"
        "Your previous reply did NOT satisfy the required JSON Schema.\n"
        f"Validation error: {error}\n\n"
        "Previous reply (head):\n"
        f"{echo}\n\n"
        "Reply again with ONLY a single JSON value that validates against this "
        "schema. No prose, no fences:\n\n"
        "```json\n"
        f"{pretty}\n"
        "```"
    )
