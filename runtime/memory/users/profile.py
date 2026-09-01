from __future__ import annotations

import re
from typing import Any

from runtime.platform.process.utils import message_text as _message_text

_MAX_MEMORY_CHARS = 500

_REMEMBER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?:please\s+)?remember(?:\s+that)?\s*[:：,，]?\s*(.+)$", re.I),
    re.compile(r"^\s*(?:please\s+)?note(?:\s+that)?\s*[:：,，]?\s*(.+)$", re.I),
    re.compile(r"^\s*(?:\u8bf7)?\u8bb0\u4f4f(?:\u4e00\u4e0b)?\s*[:：,，]?\s*(.+)$"),
    re.compile(
        r"^\s*(\u6211\u7684(?:\u540d\u5b57|\u504f\u597d|\u559c\u597d|"
        r"\u4e60\u60ef|\u9879\u76ee|\u8bed\u8a00|\u5de5\u4f5c\u6d41).+)$"
    ),
)


def extract_profile_memories(text: str) -> list[str]:
    """Return explicit memory snippets from one user text."""
    text = _clean_memory_text(text)
    if not text:
        return []
    for pattern in _REMEMBER_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        memory = _clean_memory_text(match.group(1))
        return [memory] if memory else []
    return []


def memories_from_messages(messages: list[dict[str, Any]]) -> list[str]:
    """Extract explicit memories from normalized chat/thread messages."""
    memories: list[str] = []
    for message in messages:
        role = message.get("role") or message.get("type")
        if role not in ("user", "human"):
            continue
        content = message.get("content", "")
        text = _message_text(content)
        memories.extend(extract_profile_memories(text))
    return memories


def merge_profile_memories(
    existing: list[Any] | None,
    updates: list[str],
    *,
    max_memories: int = 50,
) -> list[str]:
    """Append new memories with case-insensitive dedupe."""
    merged: list[str] = []
    seen: set[str] = set()

    for item in list(existing or []) + list(updates):
        if not isinstance(item, str):
            continue
        memory = _clean_memory_text(item)
        if not memory:
            continue
        key = memory.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(memory)

    return merged[-max_memories:]


def render_profile_memories(
    memories: list[Any] | None,
    *,
    max_memories: int = 20,
    max_chars: int = 2_000,
) -> str:
    """Render memories as a compact prompt section."""
    clean = merge_profile_memories([], [m for m in memories or [] if isinstance(m, str)])
    if not clean:
        return ""
    lines = ["USER PROFILE MEMORY:"]
    total = len(lines[0]) + 1
    for memory in clean[-max_memories:]:
        line = f"- {memory}"
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(line) > remaining:
            line = line[: max(0, remaining - 3)] + "..."
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def _clean_memory_text(text: str) -> str:
    text = " ".join(str(text).split()).strip(" .。")
    if len(text) > _MAX_MEMORY_CHARS:
        text = text[: _MAX_MEMORY_CHARS - 3].rstrip() + "..."
    return text
