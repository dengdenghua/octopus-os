"""history_skill · cross-thread conversation history retrieval.

Context isolation means a fresh thread starts blind: the agent cannot see
what was said in *other* conversations. The gateway already exposes
``GET /api/threads/search`` and ``GET /api/threads/{id}/history``, but those
are HTTP endpoints for the UI — the agent had no in-process equivalent.

This module wraps the same ``ThreadStateStore`` the runtime uses, adding the
one thing the HTTP search lacks: **date-range filtering**. Two skills:

    history_search · find past threads by keyword and/or time window.
    history_read   · read the message history of one specific thread.

Store resolution is a two-tier fallback:

    1. the live store injected by the runtime at boot
       (``set_default_thread_store``) — authoritative, no extra disk read;
    2. a lazily-built read-only ``ThreadStateStore`` pointed at the same
       ``per_agent_base`` the runtime derives from ``app_paths()``.

Both skills are read-only. They never create, mutate, or delete a thread.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.suckers.testing import SkillExpect, SkillTestCase

# Snippet/preview budgets — keep tool output small enough that a wide search
# doesn't blow the context window it's meant to compensate for.
_SNIPPET_CHARS = 240
_PREVIEW_CHARS = 400
_MAX_SCAN = 500
_MAX_LIMIT = 100

# ═══════════════════════════════════════════════════════════
# Store resolution
# ═══════════════════════════════════════════════════════════

_DEFAULT_STORE: Any = None
_FALLBACK_STORE: Any = None


def set_default_thread_store(store: Any) -> None:
    """Inject the runtime's live ThreadStateStore (called at app boot)."""
    global _DEFAULT_STORE
    _DEFAULT_STORE = store


def _resolve_store() -> Any:
    """Return the live store, else a lazily-built read-only one, else None."""
    global _FALLBACK_STORE
    if _DEFAULT_STORE is not None:
        return _DEFAULT_STORE
    if _FALLBACK_STORE is not None:
        return _FALLBACK_STORE
    try:
        from runtime.memory.threads.store import ThreadStateStore
        from runtime.platform.process.paths import app_paths

        # Mirrors _app_stack.py: per_agent_base = <data_dir>/threads.jsonl -> .parent.parent
        _FALLBACK_STORE = ThreadStateStore(per_agent_base=app_paths().threads_path.parent.parent)
    except Exception:  # noqa: BLE001 — no store on disk yet is a normal cold start
        return None
    return _FALLBACK_STORE


def _current_thread_id() -> str | None:
    try:
        from runtime.platform.process.session import current_session

        sess = current_session()
        return getattr(sess, "thread_id", None) if sess is not None else None
    except Exception:  # noqa: BLE001 — skills must work outside a Session
        return None


# ═══════════════════════════════════════════════════════════
# Date-range helpers
# ═══════════════════════════════════════════════════════════
#
# Store timestamps are naive-UTC ISO strings with a "Z" suffix
# (see store._utc_now_iso). Same format everywhere, so lexicographic
# comparison is safe once we render boundaries identically.


def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=None).isoformat() + "Z"


def _parse_boundary(value: str, *, end_of_day: bool) -> str | None:
    """Parse 'YYYY-MM-DD' or a full ISO timestamp into a comparable string.

    A bare date expands to the start (00:00:00) or end (23:59:59.999999) of
    that day so ``end_date`` is inclusive, which is what a human means by
    "up to the 4th".
    """
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1]
    try:
        if len(text) == 10:  # bare YYYY-MM-DD
            day = datetime.strptime(text, "%Y-%m-%d")
            return _iso(
                day.replace(hour=23, minute=59, second=59, microsecond=999999)
                if end_of_day
                else day
            )
        return _iso(datetime.fromisoformat(text))
    except ValueError:
        return None


def _resolve_window(
    start_date: str | None,
    end_date: str | None,
    days_back: int | None,
) -> tuple[str | None, str | None, str | None]:
    """Return ``(start, end, error)`` as comparable ISO strings."""
    start = _parse_boundary(start_date or "", end_of_day=False)
    end = _parse_boundary(end_date or "", end_of_day=True)
    if start_date and start is None:
        return None, None, f"unparseable start_date: {start_date!r} (use YYYY-MM-DD or ISO)"
    if end_date and end is None:
        return None, None, f"unparseable end_date: {end_date!r} (use YYYY-MM-DD or ISO)"
    if days_back is not None and start is None:
        try:
            back = max(0, int(days_back))
        except (TypeError, ValueError):
            return None, None, f"days_back must be an integer, got {days_back!r}"
        now = datetime.now(UTC)
        start = _iso(
            (now - timedelta(days=back)).replace(hour=0, minute=0, second=0, microsecond=0)
        )
    if start and end and start > end:
        return None, None, "start_date is after end_date"
    return start, end, None


def _in_window(thread: dict[str, Any], start: str | None, end: str | None) -> bool:
    """Overlap test: the thread was active at some point inside [start, end].

    Uses the thread's whole lifespan (created_at .. updated_at) rather than a
    single timestamp, so a long-running conversation still matches a window
    that falls in the middle of it.
    """
    created = str(thread.get("created_at") or "")
    updated = str(thread.get("updated_at") or created)
    started_after_window = bool(end and created and created > end)
    ended_before_window = bool(start and updated and updated < start)
    return not (started_after_window or ended_before_window)


# ═══════════════════════════════════════════════════════════
# Handlers
# ═══════════════════════════════════════════════════════════


def _messages_of(thread: dict[str, Any]) -> list[dict[str, Any]]:
    values = thread.get("values") if isinstance(thread.get("values"), dict) else {}
    raw = values.get("messages")
    return [m for m in raw if isinstance(m, dict)] if isinstance(raw, list) else []


def _history_search(
    query: str = "",
    start_date: str = "",
    end_date: str = "",
    days_back: Any = None,
    limit: int = 20,
    include_current: bool = False,
    **_kw: Any,
) -> dict[str, Any]:
    """Search past conversation threads by keyword and/or date range."""
    start, end, err = _resolve_window(start_date, end_date, days_back)
    if err:
        return {"error": err, "count": 0, "threads": []}

    store = _resolve_store()
    if store is None:
        return {"error": "no thread store available", "count": 0, "threads": []}

    needle = str(query or "").strip().lower()
    try:
        cap = max(1, min(_MAX_LIMIT, int(limit)))
    except (TypeError, ValueError):
        cap = 20
    skip_id = None if include_current else _current_thread_id()

    try:
        candidates = store.search(limit=_MAX_SCAN, offset=0)
    except Exception as exc:  # noqa: BLE001 — surface as data, never raise at the agent
        return {"error": f"thread search failed: {exc}", "count": 0, "threads": []}

    results: list[dict[str, Any]] = []
    scanned = 0
    for thread in candidates:
        scanned += 1
        thread_id = thread.get("thread_id")
        if skip_id and thread_id == skip_id:
            continue
        if not _in_window(thread, start, end):
            continue

        values = thread.get("values") if isinstance(thread.get("values"), dict) else {}
        title = str(values.get("title") or "")
        messages = _messages_of(thread)
        parts = [title] + [str(m.get("content") or "") for m in messages]

        snippet = ""
        if needle:
            if needle not in "\n".join(parts).lower():
                continue
            for part in parts:
                low = part.lower()
                if needle in low:  # centre the snippet on the match
                    at = low.index(needle)
                    lo = max(0, at - _SNIPPET_CHARS // 3)
                    snippet = ("…" if lo else "") + part[lo : lo + _SNIPPET_CHARS]
                    break
        else:
            snippet = next((p for p in parts[1:] if p.strip()), "")[:_SNIPPET_CHARS]

        results.append(
            {
                "thread_id": thread_id,
                "title": title or "New chat",
                "snippet": snippet,
                "created_at": thread.get("created_at"),
                "updated_at": thread.get("updated_at"),
                "message_count": len(messages),
            }
        )
        if len(results) >= cap:
            break

    return {
        "count": len(results),
        "threads": results,
        "window": {"start": start, "end": end},
        "truncated": len(results) >= cap and scanned < len(candidates),
        "next_action": (
            "Call history_read with a thread_id to see the full conversation."
            if results
            else "No matching threads — widen the date range or drop the query."
        ),
    }


def _history_read(
    thread_id: str = "",
    limit: int = 50,
    **_kw: Any,
) -> dict[str, Any]:
    """Read the message history of one past thread (newest-first)."""
    tid = str(thread_id or "").strip()
    if not tid:
        return {"error": "thread_id is required", "count": 0, "messages": []}

    store = _resolve_store()
    if store is None:
        return {"error": "no thread store available", "count": 0, "messages": []}

    try:
        cap = max(1, min(_MAX_LIMIT, int(limit)))
    except (TypeError, ValueError):
        cap = 50

    try:
        snapshots = store.get_history(tid, limit=cap)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"thread history failed: {exc}", "count": 0, "messages": []}
    if not snapshots:
        return {"error": f"thread not found: {tid}", "count": 0, "messages": []}

    # get_history returns state SNAPSHOTS (newest first); the newest one
    # already carries the full message list, so read messages from it.
    latest = snapshots[0]
    messages = _messages_of(latest)
    values = latest.get("values") if isinstance(latest.get("values"), dict) else {}

    trimmed = [
        {
            "role": str(m.get("role") or ""),
            "content": str(m.get("content") or "")[:_PREVIEW_CHARS],
            "truncated": len(str(m.get("content") or "")) > _PREVIEW_CHARS,
        }
        for m in messages[-cap:]
    ]
    return {
        "thread_id": tid,
        "title": str(values.get("title") or "") or "New chat",
        "count": len(trimmed),
        "total_messages": len(messages),
        "messages": trimmed,
        "snapshots": len(snapshots),
    }


# ═══════════════════════════════════════════════════════════
# Registration
# ═══════════════════════════════════════════════════════════


def register_history_skill(registry: SkillRegistry) -> int:
    registry.register(
        Skill(
            name="history_search",
            description=(
                "Search your PAST CONVERSATIONS (other threads) by keyword "
                "and/or date range. CALL THIS WHEN the user refers to "
                "something discussed 'before', 'last time', 'the other day', "
                "or in a named past session — you cannot see other threads "
                "otherwise. Distinct from `recall`, which reads saved "
                "MEMORY.md facts; this reads the actual chat transcripts.\n"
                "Args: {query?: string (case-insensitive substring over "
                "title + message bodies; omit to list by date alone), "
                "start_date?: 'YYYY-MM-DD' or ISO, "
                "end_date?: 'YYYY-MM-DD' or ISO (inclusive), "
                "days_back?: int (shortcut for start_date = N days ago), "
                "limit?: int (default 20, max 100), "
                "include_current?: bool (default false — the thread you're "
                "already in is excluded)}.\n"
                "Returns {count, threads: [{thread_id, title, snippet, "
                "created_at, updated_at, message_count}]}. Follow up with "
                "`history_read` on a thread_id to read the full transcript."
            ),
            affinity=["memory", "history", "threads"],
            cost_profile="low",
            trusted_source="skill://private/history_search",
            handler=_history_search,
            tests=[
                SkillTestCase(
                    name="empty_query_returns_structure",
                    tier="golden",
                    args={"limit": 1},
                    expect=SkillExpect(schema_keys=["count", "threads"]),
                ),
                SkillTestCase(
                    name="bad_date_returns_error",
                    tier="golden",
                    args={"start_date": "not-a-date"},
                    expect=SkillExpect(schema_keys=["error", "count", "threads"]),
                    custom_predicate=lambda r: isinstance(r, dict) and r.get("count") == 0,
                ),
            ],
        )
    )

    registry.register(
        Skill(
            name="history_read",
            description=(
                "Read the message transcript of one past conversation "
                "thread. Use after `history_search` gives you a thread_id. "
                "Args: {thread_id: string (required), limit?: int (default "
                "50, max 100 — most recent N messages)}. Returns "
                "{thread_id, title, count, total_messages, messages: "
                "[{role, content, truncated}]}. Long messages are clipped; "
                "`truncated: true` marks them."
            ),
            affinity=["memory", "history", "threads"],
            cost_profile="low",
            trusted_source="skill://private/history_read",
            handler=_history_read,
            tests=[
                SkillTestCase(
                    name="missing_thread_id_returns_error",
                    tier="golden",
                    args={},
                    expect=SkillExpect(schema_keys=["error", "count", "messages"]),
                    custom_predicate=lambda r: isinstance(r, dict) and r.get("count") == 0,
                ),
            ],
        )
    )
    return 2


__all__ = ["register_history_skill", "set_default_thread_store"]
