"""
blackboard · turn-scoped shared key/value store for multi-agent collab.

Why this exists
---------------

When the lead agent spawns multiple sub-agents in parallel (via
``call_agent_parallel``), the sub-agents run in isolated context
windows and only return a single summary string each. That's fine
for "spawn 3 researchers, get 3 summaries" but **breaks down** the
moment one sub-agent's mid-task finding would be useful to another
parallel sub-agent. Without a shared substrate, each sibling is a
mini silo.

The blackboard fixes this: a tiny dict keyed by Session.turn_id,
exposed as `bb_read` / `bb_write` / `bb_keys` skills. Lead and all
its parallel sub-agents share the same turn_id (we propagate it
explicitly when spawning, see ``delegation_skills.call_agent_parallel``)
so they all see the same dict.

Lifecycle
---------

- Created lazily on first write/read.
- Dropped when the entry hasn't been touched for ``_TTL_SECONDS``
  (default 1 hour) and we're past the bounded-dict cap.
- Survives the lifetime of one user-message turn; new turn → new
  turn_id → new (empty) blackboard. Memory across turns is what the
  ``remember`` / ``recall`` skills are for.

Concurrency
-----------

Backed by a single ``threading.Lock`` per blackboard. Reads and
writes in the same turn (which can come from multiple ThreadPool
workers when sub-agents run concurrently) serialize on this lock.
The contention is fine — a typical turn does maybe 10-50 bb ops,
nowhere near hot. If it ever becomes hot, swap for per-key locks.

Not for
-------

- Cross-thread / cross-conversation sharing (use long-term memory).
- Large blobs (keep entries short · the blackboard goes into LLM
  context when ``bb_keys`` enumerates).
- Persistence — the default board is in-memory (gone on restart). For
  durable, cross-process coordination set ``ECHO_BLACKBOARD_DB`` to a
  file path; see ``blackboard_store.SqliteBlackboard``.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from typing import Any

# Configuration knobs · constants for now, can be promoted to config
# later if anyone needs to tune them in the wild.
_TTL_SECONDS: float = 60 * 60  # 1 hour · long enough for any single turn
_MAX_BOARDS: int = 256  # cap to prevent unbounded growth in long-running daemons


class Blackboard:
    """One shared key/value namespace, scoped to a single turn."""

    __slots__ = (
        "_data",
        "_key_writers",
        "_lock",
        "_last_touched",
        "_overwrite_count",
        "_write_count",
        "_claims",
        "_pinned",
    )

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._key_writers: dict[str, set[str]] = {}
        self._lock = threading.Lock()
        self._last_touched: float = time.monotonic()
        self._overwrite_count: int = 0
        self._write_count: int = 0
        self._claims: dict[str, str] = {}
        self._pinned: set[str] = set()

    def write(self, key: str, value: Any, *, writer: str | None = None) -> None:
        with self._lock:
            if key in self._data:
                self._overwrite_count += 1
            self._data[key] = value
            self._write_count += 1
            if writer:
                self._key_writers.setdefault(key, set()).add(writer)
            self._last_touched = time.monotonic()

    def can_write(self, key: str, writer: str | None = None) -> tuple[bool, str]:
        """Guard before a write: a pinned key is sealed; a claimed key is
        only writable by its owner.

        Returns ``(allowed, reason)``. ``reason`` is empty when allowed.
        """
        with self._lock:
            if key in self._pinned:
                return False, "pinned"
            owner = self._claims.get(key)
            if owner is not None and writer is not None and owner != writer:
                return False, f"claimed_by:{owner}"
            return True, ""

    def claim(self, key: str, writer: str | None) -> tuple[bool, str]:
        """Claim a slot for ``writer``.

        A slot can be claimed once; re-claiming by the same writer is a
        no-op success. Returns ``(ok, reason)``.
        """
        if not writer:
            return False, "writer is required to claim"
        with self._lock:
            existing = self._claims.get(key)
            if existing is not None and existing != writer:
                return False, f"claimed_by:{existing}"
            self._claims[key] = writer
            self._last_touched = time.monotonic()
            return True, ""

    def pin(self, key: str) -> tuple[bool, str]:
        """Seal a key as an immutable snapshot. Writes after pin are rejected."""
        with self._lock:
            if key not in self._data:
                return False, "key_not_found"
            self._pinned.add(key)
            self._last_touched = time.monotonic()
            return True, ""

    def read(self, key: str, default: Any = None) -> Any:
        with self._lock:
            self._last_touched = time.monotonic()
            return self._data.get(key, default)

    def keys(self) -> list[str]:
        with self._lock:
            self._last_touched = time.monotonic()
            return list(self._data.keys())

    def snapshot(self) -> dict[str, Any]:
        """Shallow copy · safe to read outside the lock."""
        with self._lock:
            return dict(self._data)

    def audit(self) -> dict[str, Any]:
        with self._lock:
            value_sizes = {key: _value_size(value) for key, value in self._data.items()}
            return {
                "key_count": len(self._data),
                "write_count": self._write_count,
                "overwrite_count": self._overwrite_count,
                "contested_keys": [
                    key for key, writers in self._key_writers.items() if len(writers) > 1
                ],
                "large_value_keys": [key for key, size in value_sizes.items() if size > 8_000],
                "value_sizes": value_sizes,
                "writers_by_key": {
                    key: sorted(writers) for key, writers in self._key_writers.items()
                },
                "claimed_keys": dict(self._claims),
                "pinned_keys": sorted(self._pinned),
            }


# ── Module-level registry · keyed on turn_id ──────────────
def _value_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return len(str(value))


_BOARDS: OrderedDict[str, Blackboard] = OrderedDict()
_BOARDS_LOCK = threading.Lock()


def get_blackboard(turn_id: str | None) -> Any:
    """Return the blackboard for ``turn_id``, creating one on demand.

    Returns ``None`` only when ``turn_id`` is empty / None — skills
    require a Session to share state, so callers should fall back to
    a "no shared scope" error message when this returns None.

    When ``ECHO_BLACKBOARD_DB`` names a file, returns a durable,
    cross-process SQLite-backed board (same interface) instead of the
    in-memory one, so agents in separate processes sharing the turn_id +
    DB file coordinate on one workspace. Falls back to in-memory on any
    backend error so coordination is never lost.
    """
    if not turn_id:
        return None
    db_path = os.environ.get("ECHO_BLACKBOARD_DB")
    if db_path:
        try:
            from runtime.memory.runtime_state.blackboard_store import (
                get_sqlite_blackboard,
            )

            return get_sqlite_blackboard(db_path, turn_id)
        except Exception:  # noqa: BLE001 — never lose coordination to a bad backend
            pass
    with _BOARDS_LOCK:
        bb = _BOARDS.get(turn_id)
        if bb is None:
            bb = Blackboard()
            _BOARDS[turn_id] = bb
            _evict_expired_locked()
        else:
            # touch the LRU position so this entry survives eviction
            _BOARDS.move_to_end(turn_id)
        return bb


def _evict_expired_locked() -> None:
    """Evict TTL-expired entries + cap by oldest. Caller holds lock."""
    now = time.monotonic()
    expired_keys = [
        k
        for k, v in _BOARDS.items()
        if (now - v._last_touched) > _TTL_SECONDS  # noqa: SLF001
    ]
    for k in expired_keys:
        _BOARDS.pop(k, None)
    while len(_BOARDS) > _MAX_BOARDS:
        _BOARDS.popitem(last=False)


def reset_for_tests() -> None:
    """Clear all boards · only for unit tests."""
    with _BOARDS_LOCK:
        _BOARDS.clear()


def list_active_turns() -> list[dict[str, Any]]:
    """Return a lightweight index of live blackboards for the UI.

    One entry per ``turn_id`` currently in the registry, youngest
    first (matches LRU ordering). Each entry carries only what the
    observability panel needs to render a picker:

        {
          "turn_id": "...",
          "key_count": int,
          "age_seconds": float,  # since last touch
        }

    Does NOT enumerate keys / values — use ``get_blackboard(turn_id)
    .snapshot()`` for that. Keeping this list cheap matters: the UI
    panel polls it on a heartbeat interval.
    """
    now = time.monotonic()
    with _BOARDS_LOCK:
        # LRU has oldest-first internally; reverse so UI default-picks
        # the most recently active turn.
        entries = list(_BOARDS.items())
    out: list[dict[str, Any]] = []
    for turn_id, bb in reversed(entries):
        with bb._lock:  # noqa: SLF001 · peeking at TTL metadata
            touched = bb._last_touched  # noqa: SLF001
            count = len(bb._data)  # noqa: SLF001
            write_count = bb._write_count  # noqa: SLF001
            overwrite_count = bb._overwrite_count  # noqa: SLF001
        out.append(
            {
                "turn_id": turn_id,
                "key_count": count,
                "write_count": write_count,
                "overwrite_count": overwrite_count,
                "age_seconds": max(0.0, now - touched),
            }
        )
    return out


__all__ = [
    "Blackboard",
    "get_blackboard",
    "list_active_turns",
    "reset_for_tests",
]
