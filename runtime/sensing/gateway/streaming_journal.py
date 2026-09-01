from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Sized
from datetime import datetime
from typing import Any, cast

from runtime.adapters.instrumentation import trace_stage
from runtime.memory.journal import Journal
from runtime.memory.journal.journal import (
    JournalEvent,
    JournalEventType,
    TrajectoryEvent,
)
from runtime.platform.models import TaskId
from runtime.safety.auth.scope import TenantScope

Subscriber = Callable[[JournalEvent], None]


class StreamingJournal(Journal):
    def __init__(self, inner: Journal) -> None:
        self._inner = inner
        self._subscribers: list[Subscriber] = []
        self._lock = threading.RLock()

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def write(self, event: JournalEvent) -> None:
        self.write_canonical(event)

    def _write_inner(self, event: JournalEvent) -> JournalEvent:
        """Persist exactly once and return the backend's canonical event.

        Formal Journal implementations expose the combined hook, which lets
        storage apply context/redaction and prove persistence before fan-out.
        Older duck-typed backends remain compatible through one pre-write
        canonicalization (when available) followed by exactly one ``write``;
        exceptions are never retried through another path.
        """

        canonical_writer = getattr(self._inner, "write_canonical", None)
        if callable(canonical_writer):
            durable_event = canonical_writer(event)
            if not isinstance(durable_event, JournalEvent):
                raise TypeError("journal canonical writer returned an invalid event")
            return durable_event

        canonicalizer = getattr(self._inner, "canonicalize_event", None)
        durable_event = (
            canonicalizer(event) if callable(canonicalizer) else super().canonicalize_event(event)
        )
        if not isinstance(durable_event, JournalEvent):
            raise TypeError("journal canonicalizer returned an invalid event")
        writer = getattr(self._inner, "write", None)
        if not callable(writer):
            raise NotImplementedError("journal backend has no event append")
        writer(durable_event)
        return durable_event

    def canonicalize_event(self, event: JournalEvent) -> JournalEvent:
        canonicalizer = getattr(self._inner, "canonicalize_event", None)
        if callable(canonicalizer):
            return canonicalizer(event)
        return super().canonicalize_event(event)

    def write_canonical(self, event: JournalEvent) -> JournalEvent:
        durable_event = self._write_inner(event)
        # Storage has returned successfully and released its own lock before
        # callbacks run, so subscribers may safely perform re-entrant writes.
        self._broadcast(durable_event)
        return durable_event

    def _write_trajectory_once_inner(
        self,
        event: TrajectoryEvent,
    ) -> tuple[bool, TrajectoryEvent]:
        """Use the formal canonical hook, with an atomic legacy fallback.

        Older duck-typed journal doubles can implement the original
        ``write_trajectory_once`` contract without inheriting the newer
        combined hook.  They remain atomic here: the fallback invokes their
        one-shot writer directly and never degrades to a racy read/write pair.
        """

        canonical_writer = getattr(self._inner, "write_trajectory_once_canonical", None)
        if callable(canonical_writer):
            inserted, durable_event = canonical_writer(event)
            return bool(inserted), durable_event

        legacy_writer = getattr(self._inner, "write_trajectory_once", None)
        if not callable(legacy_writer):
            raise NotImplementedError("journal backend has no atomic trajectory append")
        durable_event = self.canonicalize_trajectory_event(event)
        return bool(legacy_writer(durable_event)), durable_event

    def write_trajectory_once(self, event: TrajectoryEvent) -> bool:
        inserted, durable_event = self._write_trajectory_once_inner(event)
        if inserted:
            # Broadcast only after the storage transaction and after releasing
            # the inner backend lock. Subscribers therefore observe exactly
            # the redacted/server-scoped durable event and may safely perform
            # re-entrant journal writes.
            self._broadcast(durable_event)
        return inserted

    def canonicalize_trajectory_event(self, event: TrajectoryEvent) -> TrajectoryEvent:
        canonicalizer = getattr(self._inner, "canonicalize_trajectory_event", None)
        if callable(canonicalizer):
            return canonicalizer(event)
        return super().canonicalize_trajectory_event(event)

    def write_trajectory_once_canonical(
        self,
        event: TrajectoryEvent,
    ) -> tuple[bool, TrajectoryEvent]:
        inserted, durable_event = self._write_trajectory_once_inner(event)
        if inserted:
            self._broadcast(durable_event)
        return inserted, durable_event

    def read_all(self, *, scope: TenantScope | None = None) -> list[JournalEvent]:
        return self._inner.read_all(scope=scope)

    def read_by_task(
        self,
        task_id: TaskId,
        *,
        scope: TenantScope | None = None,
    ) -> list[JournalEvent]:
        return self._inner.read_by_task(task_id, scope=scope)

    def read_by_type(
        self,
        event_type: JournalEventType,
        *,
        scope: TenantScope | None = None,
    ) -> list[JournalEvent]:
        return self._inner.read_by_type(event_type, scope=scope)

    def read_since(self, ts: datetime) -> list[JournalEvent]:
        return self._inner.read_since(ts)

    def __len__(self) -> int:
        return len(cast(Sized, self._inner))

    def _broadcast(self, event: JournalEvent) -> None:
        with self._lock:
            subs = list(self._subscribers)  # Implementation note.
        with trace_stage("siphon.broadcast") as span:
            span.set_attribute("echo.siphon.subscribers", len(subs))
            span.set_attribute("echo.siphon.event_type", event.event_type)
            for cb in subs:
                with contextlib.suppress(Exception):
                    cb(event)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
