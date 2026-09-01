"""Per-thread, per-role conversation memory for subagents.

Goal: when a parent agent calls the same subagent role twice in the
same thread, the second call sees what the first call produced.
Mirrors how Claude Code preserves context across messages in the
same session — without it, "researcher, dig deeper" forces the
agent to re-research from scratch every time.

Storage model
-------------
Memory lives in process memory only (no disk persistence). A bounded
deque per ``(thread_id, role_id)`` key holds the last ``MAX_TURNS_PER_KEY``
turns. Older turns drop off; the parent's session-level memory and the
genome journal are still authoritative for long-term recall.

Lifecycle
---------
- Inserted via ``record_turn`` after each successful or failed
  subagent call.
- Read via ``recent_turns_prompt`` to inject a "Previous turns" prefix
  into the next call's system prompt.
- TTL-pruned: keys idle longer than ``DEFAULT_TTL_SECONDS`` are dropped
  on next access. No background thread; opportunistic cleanup keeps the
  module dependency-free.

When NOT to enable
------------------
- Tests should ``clear_history()`` between runs.
- High-isolation calls (e.g. external partner subagents that should not
  see prior internal context) should pass ``share_history=False`` at the
  call site.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Final

_log = logging.getLogger("runtime.execution.subagents.memory")

# Bounds: 5 turns × ~1500 chars/turn = ~7.5 KB context prefix per role.
# Far smaller than the 200 KB Anthropic context window so this is
# cheap relative to the value (subagent stops re-doing work).
MAX_TURNS_PER_KEY: Final[int] = 5
DEFAULT_TTL_SECONDS: Final[float] = 60 * 60  # 1 hour
MAX_PROMPT_PREVIEW_CHARS: Final[int] = 400
MAX_OUTPUT_PREVIEW_CHARS: Final[int] = 1200


@dataclass(slots=True)
class SubagentTurn:
    """Single recorded turn for one (thread_id, role_id) key.

    ``prompt`` and ``output`` are stored as previews (truncated) so the
    aggregate memory footprint stays bounded even when callers pass
    huge prompts or get verbose outputs.
    """

    prompt: str
    output: str
    success: bool
    rounds: int
    timestamp: float = field(default_factory=time.time)
    error: str = ""


@dataclass(slots=True)
class _Bucket:
    """Per-key storage with last-touched timestamp for TTL cleanup."""

    turns: deque[SubagentTurn]
    last_touched: float


_HISTORY: dict[tuple[str, str], _Bucket] = {}
_LOCK = Lock()


def _key(thread_id: str, role_id: str) -> tuple[str, str]:
    return (str(thread_id or "").strip(), str(role_id or "").strip())


def record_turn(
    *,
    thread_id: str,
    role_id: str,
    prompt: str,
    output: str,
    success: bool,
    rounds: int,
    error: str = "",
) -> None:
    """Append one turn to the (thread_id, role_id) history bucket.

    No-op if either thread_id or role_id is empty (stateless calls
    intentionally don't accumulate history).
    """
    key = _key(thread_id, role_id)
    if not key[0] or not key[1]:
        return

    turn = SubagentTurn(
        prompt=_truncate(prompt, MAX_PROMPT_PREVIEW_CHARS),
        output=_truncate(output, MAX_OUTPUT_PREVIEW_CHARS),
        success=bool(success),
        rounds=int(rounds),
        timestamp=time.time(),
        error=str(error or "")[:240],
    )
    with _LOCK:
        bucket = _HISTORY.get(key)
        if bucket is None:
            bucket = _Bucket(
                turns=deque(maxlen=MAX_TURNS_PER_KEY),
                last_touched=turn.timestamp,
            )
            _HISTORY[key] = bucket
        bucket.turns.append(turn)
        bucket.last_touched = turn.timestamp
    _log.debug(
        "recorded subagent turn · thread=%s role=%s success=%s rounds=%d",
        thread_id,
        role_id,
        success,
        rounds,
    )


def recent_turns(
    thread_id: str,
    role_id: str,
    *,
    limit: int = MAX_TURNS_PER_KEY,
) -> list[SubagentTurn]:
    """Return the most recent turns for the (thread, role) key.

    Returns an empty list when there's no history or the bucket has
    expired. Touches ``last_touched`` so frequently-accessed buckets
    stay alive longer than dormant ones.
    """
    key = _key(thread_id, role_id)
    if not key[0] or not key[1]:
        return []

    now = time.time()
    with _LOCK:
        bucket = _HISTORY.get(key)
        if bucket is None:
            return []
        if now - bucket.last_touched > DEFAULT_TTL_SECONDS:
            del _HISTORY[key]
            return []
        bucket.last_touched = now
        return list(bucket.turns)[-limit:]


def recent_turns_prompt(
    thread_id: str,
    role_id: str,
    *,
    limit: int = MAX_TURNS_PER_KEY,
) -> str:
    """Render recent turns as a system-prompt prefix.

    Returns the empty string when there's no history. The format is
    deliberately compact so the prefix doesn't dominate the call's
    own system_prompt.
    """
    turns = recent_turns(thread_id, role_id, limit=limit)
    if not turns:
        return ""
    lines: list[str] = [
        "## Prior turns in this thread (for continuity)",
        "",
        (
            "You have been called before in this thread. The user may "
            'reference prior outputs ("that patent", "the second '
            'candidate", "continue from where you left off"). Use '
            "this history to resolve such references; do NOT re-run "
            "work that already produced a result."
        ),
        "",
    ]
    for i, turn in enumerate(turns, 1):
        when_ago = max(0.0, time.time() - turn.timestamp)
        status = "✓" if turn.success else "✗"
        header = (
            f"### Turn {i}/{len(turns)} · {status} · "
            f"{turn.rounds} round(s) · {_format_ago(when_ago)} ago"
        )
        lines.append(header)
        lines.append("")
        lines.append(f"**You were asked:** {turn.prompt}")
        lines.append("")
        if turn.success:
            lines.append("**You answered:**")
            lines.append("")
            lines.append(turn.output)
        else:
            err_line = turn.error or "(no error message recorded)"
            partial = turn.output.strip()
            lines.append(
                f"**That call failed:** {err_line}",
            )
            if partial:
                lines.append("")
                lines.append("**Partial output produced:**")
                lines.append("")
                lines.append(partial)
        lines.append("")
    lines.append("## Now: the user's NEW request")
    lines.append("")
    return "\n".join(lines)


def clear_history(
    thread_id: str | None = None,
    role_id: str | None = None,
) -> int:
    """Drop history buckets matching the filter.

    With no args, clears EVERYTHING (used by tests). With ``thread_id``,
    clears all roles for that thread. With both, clears one specific
    bucket. Returns the number of buckets removed.
    """
    with _LOCK:
        if thread_id is None and role_id is None:
            n = len(_HISTORY)
            _HISTORY.clear()
            return n
        keys_to_drop = []
        for key in _HISTORY:
            if thread_id is not None and key[0] != thread_id:
                continue
            if role_id is not None and key[1] != role_id:
                continue
            keys_to_drop.append(key)
        for key in keys_to_drop:
            del _HISTORY[key]
        return len(keys_to_drop)


def stats() -> dict[str, int]:
    """Inspection helper for tests / observability endpoints."""
    with _LOCK:
        total_turns = sum(len(b.turns) for b in _HISTORY.values())
        return {
            "buckets": len(_HISTORY),
            "total_turns": total_turns,
        }


def _truncate(text: str, max_chars: int) -> str:
    if not isinstance(text, str):
        text = str(text or "")
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + " […truncated]"


def _format_ago(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "MAX_OUTPUT_PREVIEW_CHARS",
    "MAX_PROMPT_PREVIEW_CHARS",
    "MAX_TURNS_PER_KEY",
    "SubagentTurn",
    "clear_history",
    "recent_turns",
    "recent_turns_prompt",
    "record_turn",
    "stats",
]
