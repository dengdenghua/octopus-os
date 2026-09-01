"""
Typed sub-agent lifecycle event bus.

Why this exists
---------------

Sub-agents currently report progress through three ad-hoc channels:

* a fire-and-forget ``event_emitter`` callable (bridge path),
* a ``sub_tool_event_queue`` stashed on ``session.metadata`` (agentic
  fallback path),
* journal events (OpenAI-gateway worker path).

Each carries a slightly different shape and none are keyed by the
sub-agent's own thread. The threaded design ("sub-agent = its own
thread, parent keeps a progress card, the workbench streams the full
run") needs ONE typed stream a consumer can subscribe to, replay from
any point, and render independently of the parent conversation.

This module provides that stream: a per-coordination-root event bus.

Terminology
-----------

* ``root_thread_id`` — the lineage root. The parent conversation thread
  (or the first thread in a fork chain). All events for one coordinated
  run share this id.
* ``thread_id`` — the thread that produced the event. For the parent it
  is the root; for a sub-agent it is the sub-thread's own id.
* ``parent_thread_id`` — the direct parent thread id (empty for the root).

Semantics
---------

* Events are **append-only**: once published, an event is never mutated.
* ``seq`` is a strictly monotonic per-root counter; consumers can gap-fill
  or order by it.
* A bounded replay buffer lets late subscribers render history instead of
  only "from now on".
* Publishing is fire-and-forget and **never raises**: losing telemetry is
  preferable to crashing the runner. Subscribers run on the publisher's
  thread and must not block.

Lifecycle event types
---------------------

``sub_started`` / ``sub_thinking`` / ``sub_tool_start`` / ``sub_tool_end``
/ ``sub_check`` / ``sub_artifacts`` / ``sub_concluded`` /
``sub_incomplete`` / ``sub_failed``. See ``EVENT_TYPES``.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

# ── Event types ─────────────────────────────────────────────
EVT_SUB_STARTED = "sub_started"
EVT_SUB_THINKING = "sub_thinking"
EVT_SUB_TOOL_START = "sub_tool_start"
EVT_SUB_TOOL_END = "sub_tool_end"
EVT_SUB_CHECK = "sub_check"
EVT_SUB_ARTIFACTS = "sub_artifacts"
EVT_SUB_CONCLUDED = "sub_concluded"
EVT_SUB_INCOMPLETE = "sub_incomplete"
EVT_SUB_FAILED = "sub_failed"

EVENT_TYPES = frozenset(
    {
        EVT_SUB_STARTED,
        EVT_SUB_THINKING,
        EVT_SUB_TOOL_START,
        EVT_SUB_TOOL_END,
        EVT_SUB_CHECK,
        EVT_SUB_ARTIFACTS,
        EVT_SUB_CONCLUDED,
        EVT_SUB_INCOMPLETE,
        EVT_SUB_FAILED,
    }
)

# ── Tuning ──────────────────────────────────────────────────
_REPLAY_CAP: int = 500  # bounded per-root replay buffer
_MAX_BUSES: int = 512  # cap on live coordination roots
_TTL_SECONDS: float = 60 * 60  # idle eviction, like the blackboard

# Type alias for a subscriber callback. Receives the full event dict.
Subscriber = Callable[[dict[str, Any]], None]


class SubAgentEventBus:
    """One typed, append-only, replayable event stream per coordination root."""

    __slots__ = ("_events", "_subs", "_counter", "_lock", "_last_touched")

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._subs: list[Subscriber] = []
        self._counter = itertools.count(1)
        self._lock = threading.Lock()
        self._last_touched: float = time.monotonic()

    def publish(self, event: dict[str, Any]) -> dict[str, Any]:
        """Append an event, stamp seq/ts, fan out to subscribers.

        Returns the fully-stamped event. Rejects unknown event types.
        Never raises on subscriber failure — a misbehaving subscriber is
        dropped, not fatal.
        """
        if event.get("type") not in EVENT_TYPES:
            raise ValueError(f"unknown sub-agent event type {event.get('type')!r}")
        with self._lock:
            event = dict(event)
            event.setdefault("seq", next(self._counter))
            event.setdefault("ts", time.time())
            self._events.append(event)
            if len(self._events) > _REPLAY_CAP:
                del self._events[: len(self._events) - _REPLAY_CAP]
            self._last_touched = time.monotonic()
            subs = list(self._subs)
        for sub in subs:
            try:
                sub(event)
            except Exception:  # noqa: BLE001 · never crash the publisher
                with self._lock:
                    if sub in self._subs:
                        self._subs.remove(sub)
        return event

    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        """Register a subscriber; returns an unsubscribe callable."""
        with self._lock:
            self._subs.append(subscriber)
        return lambda: self.unsubscribe(subscriber)

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            with_sub = self._subs
            if subscriber in with_sub:
                with_sub.remove(subscriber)

    def replay(self, after_seq: int = 0) -> list[dict[str, Any]]:
        """Return events with ``seq`` strictly greater than ``after_seq``.

        Ordered by seq. Used by late/offline subscribers to backfill.
        """
        with self._lock:
            self._last_touched = time.monotonic()
            return [e for e in self._events if e.get("seq", 0) > after_seq]

    def seq(self) -> int:
        """Highest seq published so far (0 when empty)."""
        with self._lock:
            return self._events[-1]["seq"] if self._events else 0

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._subs.clear()

    @property
    def last_touched(self) -> float:
        return self._last_touched


# ── Module-level registry ───────────────────────────────────
_BUSES: OrderedDict[str, SubAgentEventBus] = OrderedDict()
_BUSES_LOCK = threading.Lock()


def get_bus(root_thread_id: str | None) -> SubAgentEventBus | None:
    """Return (or lazily create) the event bus for a coordination root.

    Returns ``None`` only when ``root_thread_id`` is empty/None — callers
    should silently skip bus publication in that case.
    """
    if not root_thread_id:
        return None
    with _BUSES_LOCK:
        bus = _BUSES.get(root_thread_id)
        if bus is None:
            bus = SubAgentEventBus()
            _BUSES[root_thread_id] = bus
            _evict_expired_locked()
        else:
            _BUSES.move_to_end(root_thread_id)
        return bus


def _evict_expired_locked() -> None:
    now = time.monotonic()
    expired = [k for k, v in _BUSES.items() if (now - v.last_touched) > _TTL_SECONDS]
    for k in expired:
        _BUSES.pop(k, None)
    while len(_BUSES) > _MAX_BUSES:
        _BUSES.popitem(last=False)


def reset_for_tests() -> None:
    """Clear all buses. Unit tests only."""
    with _BUSES_LOCK:
        _BUSES.clear()


def list_active_buses() -> list[dict[str, Any]]:
    """Lightweight index of live buses (for observability)."""
    now = time.monotonic()
    with _BUSES_LOCK:
        items = list(_BUSES.items())
    out = []
    for root, bus in reversed(items):
        out.append(
            {
                "root_thread_id": root,
                "seq": bus.seq(),
                "age_seconds": max(0.0, now - bus.last_touched),
            }
        )
    return out


# ── Public publish helper ───────────────────────────────────
def publish_subagent_event(
    type: str,
    payload: dict[str, Any] | None,
    *,
    thread_id: str = "",
    root_thread_id: str = "",
    parent_thread_id: str = "",
) -> dict[str, Any] | None:
    """Publish one typed event to the coordination-root bus.

    Derives ``root_thread_id`` from ``session.metadata`` when not given
    (see :func:`_resolve_coordination_ids`). Returns the stamped event,
    or ``None`` when there is no resolvable root (no session / no id) —
    callers must treat that as a silent no-op, never an error.
    """
    if type not in EVENT_TYPES:
        raise ValueError(f"unknown sub-agent event type {type!r}")
    if root_thread_id:
        root = root_thread_id
    else:
        meta, sess_thread = _resolve_session_meta()
        root = (meta or {}).get("root_thread_id") or (meta or {}).get("thread_id") or sess_thread
    if not root:
        return None
    if not thread_id:
        meta, sess_thread = _resolve_session_meta()
        thread_id = (meta or {}).get("thread_id") or sess_thread or ""
    if not parent_thread_id:
        meta, _ = _resolve_session_meta()
        parent_thread_id = (meta or {}).get("parent_thread_id") or ""
    bus = get_bus(root)
    if bus is None:
        return None
    return bus.publish(
        {
            "type": type,
            "thread_id": thread_id,
            "root_thread_id": root,
            "parent_thread_id": parent_thread_id,
            "payload": payload or {},
        }
    )


def _resolve_session_meta() -> tuple[dict[str, Any] | None, str | None]:
    try:
        from runtime.platform.process.session import current_session

        sess = current_session()
    except Exception:  # noqa: BLE001
        return None, None
    if sess is None:
        return None, None
    meta = getattr(sess, "metadata", None) or {}
    if not isinstance(meta, dict):
        meta = {}
    sess_thread = getattr(sess, "thread_id", None)
    return meta, sess_thread


__all__ = [
    "EVT_SUB_ARTIFACTS",
    "EVT_SUB_CHECK",
    "EVT_SUB_CONCLUDED",
    "EVT_SUB_FAILED",
    "EVT_SUB_INCOMPLETE",
    "EVT_SUB_STARTED",
    "EVT_SUB_THINKING",
    "EVT_SUB_TOOL_END",
    "EVT_SUB_TOOL_START",
    "EVENT_TYPES",
    "SubAgentEventBus",
    "get_bus",
    "list_active_buses",
    "publish_subagent_event",
    "reset_for_tests",
]
