"""
runtime.platform.process.utils · cross-module utility functions.

Consolidated from duplicated implementations across the codebase.
Each function has a single canonical home here; consumers import
from this module instead of re-defining locally.
"""

from __future__ import annotations

import json
import re
from typing import Any

_RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_RE_LINE_COMMENT = re.compile(r"(?<![\":])//[^\n]*")


def message_text(content: Any) -> str:
    """Extract plain text from a LangChain-style message content block.

    Accepts:
      - str → returned as-is (stripped)
      - list[dict] → concatenates all ``{"type": "text", "text": "..."}``
        parts, skipping non-dict items
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(parts).strip()
    return ""


def safe_repr(obj: Any, max_len: int = 500) -> Any:
    """JSON-safe representation — recursive for dict/list/tuple.

    - Primitives (str/int/float/bool/None) pass through unchanged
    - dict → recurse values, stringify keys
    - list/tuple → recurse elements
    - Everything else → ``repr()`` capped at *max_len* chars
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            key = str(k)
            child_max_len = max_len
            if key in {"diff", "diff_preview", "unified_diff"}:
                child_max_len = max(max_len, 20_000)
            out[key] = safe_repr(v, child_max_len)
        return out
    if isinstance(obj, (list, tuple)):
        return [safe_repr(v, max_len) for v in obj]
    try:
        return repr(obj)[:max_len]
    except (TypeError, ValueError, RecursionError):
        return "<unrepr>"


def parse_jsonc(text: str) -> dict[str, Any]:
    """Tolerant JSONC parser — strips ``//`` line and ``/* */`` block
    comments before feeding ``json.loads``.

    Uses negative lookbehind for ``:`` and ``"`` to avoid mangling
    URLs with ``//`` in string values.
    """
    stripped = _RE_BLOCK_COMMENT.sub("", text)
    stripped = _RE_LINE_COMMENT.sub("", stripped)
    return json.loads(stripped)
