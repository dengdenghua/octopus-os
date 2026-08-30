"""Append-only item-oriented event log.

Each thread owns one ``.jsonl`` file. Every observable runtime fact —
turn started, item started, item delta, item completed, turn completed —
appends as one line. The file is the source of truth: process restart,
client reconnect, multi-window resume all rebuild from disk.

Wire format (one event per line)::

    {"event":"thread_started","threadId":"...","ts":"..."}
    {"event":"turn_started","threadId":"...","turnId":"...","ts":"...","params":{...}}
    {"event":"item_started","threadId":"...","turnId":"...","item":{...}}
    {"event":"item_delta","threadId":"...","turnId":"...","itemId":"...","kind":"agentMessage","delta":"..."}
    {"event":"item_completed","threadId":"...","turnId":"...","item":{...}}
    {"event":"turn_completed","threadId":"...","turnId":"...","status":"completed"}

Replay walks events in order, applying each to a ``Turn``. Idempotent —
duplicates are detected by ``itemId`` and silently merged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from runtime.platform.models.primitives import new_id, now_utc
from runtime.protocol.items import Item, Turn, TurnStatus

from ._event_log_helpers import (
    actor_id_from_turn_params,
    owner_actor_id_from_turns,
    thread_log_path,
    validate_thread_id,
)
from ._event_log_serialization import (
    _exclusive_file_lock,
    _thread_id_from_path,
)
from ._event_log_serialization import (
    coalesce_events as coalesce_events,
)
from ._replay import _apply_event

_logger = logging.getLogger(__name__)


EventKind = Literal[
    "thread_started",
    "thread_archived",
    "turn_started",
    "turn_updated",
    "turn_interrupt_requested",
    "turn_completed",
    "turn_diff_updated",
    "item_started",
    "item_delta",
    "item_completed",
    "turn_compacted",
]


class LoggedEvent(BaseModel):
    """Single line in the JSONL log.

    The schema is intentionally loose: ``payload`` is opaque JSON and
    new event kinds add fields without breaking older readers.
    """

    model_config = ConfigDict(populate_by_name=True)

    event: EventKind
    # Stable identity makes replay idempotent under at-least-once delivery,
    # log replication and crash recovery. Old JSONL lines omit it and receive
    # a deterministic content-derived ``legacy:<digest>`` identity.
    event_id: str | None = Field(default=None, alias="eventId")
    thread_id: str = Field(alias="threadId")
    ts: datetime = Field(default_factory=now_utc)
    turn_id: str | None = Field(default=None, alias="turnId")
    payload: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EventLogSnapshot:
    """Immutable, byte-bounded view of one event log.

    ``cursor`` and ``events`` always describe the same file prefix. Anything
    appended after the snapshot boundary is intentionally deferred to the
    next resume, eliminating the replay/cursor race that can otherwise skip a
    live delta forever.
    """

    events: tuple[tuple[int, LoggedEvent], ...]
    cursor: int
    stream_id: str | None = None

    def replay(self) -> list[Turn]:
        turns: list[Turn] = []
        by_id: dict[str, Turn] = {}
        for _sequence, event in self.events:
            _apply_event(event, turns, by_id)
        return turns

    def cursor_delta(self, after_sequence: int) -> tuple[list[str], bool]:
        """Return changed turn ids and whether a full reset is required."""
        after = max(0, int(after_sequence))
        if after > self.cursor:
            return [], True
        changed_turn_ids: list[str] = []
        seen_turn_ids: set[str] = set()
        requires_reset = False
        for sequence, event in self.events:
            if sequence <= after:
                continue
            if event.event == "turn_compacted":
                requires_reset = True
            if event.turn_id and event.turn_id not in seen_turn_ids:
                seen_turn_ids.add(event.turn_id)
                changed_turn_ids.append(event.turn_id)
        return changed_turn_ids, requires_reset


class EventLog:
    """Per-thread JSONL writer + reader.

    Concurrency: a process-local ``threading.Lock`` plus an OS file lock
    guards each append. Readers snapshot lock-free; a partial trailing line
    remains invisible until the next snapshot. This keeps one shared thread
    log valid under multiple Uvicorn workers and second-tab steering.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    # ── Writer side ──────────────────────────────────────────

    def append(
        self,
        event: LoggedEvent,
        *,
        durable: bool = False,
    ) -> LoggedEvent:
        """Append one event and return the stored copy (with its ``eventId``).

        Callers that fan the event out to live subscribers stamp the returned
        id onto the notification so clients can deduplicate live delivery
        against a later log replay (at-least-once on both paths).

        ``durable=True`` is reserved for execution/terminal boundaries where
        returning before the kernel has accepted the write for persistence
        could make an externally visible side effect outrun its audit record.
        High-frequency text deltas deliberately keep the cheaper flush-only
        path.
        """
        if not event.event_id:
            event = event.model_copy(update={"event_id": f"evt_{new_id().hex}"})
        line = event.model_dump_json(by_alias=True) + "\n"
        with (
            self._lock,
            self._path.open("a", encoding="utf-8") as stream,
            _exclusive_file_lock(stream, self._path),
        ):
            stream.seek(0, 2)
            stream.write(line)
            stream.flush()
            if durable:
                os.fsync(stream.fileno())
        return event

    def reserve_timeline_sequence(self, turn_id: str) -> int:
        """Atomically reserve the next 1-based item slot for one turn.

        The mutable sidecar is only an allocator; item coordinates remain in
        the append-only event log and are still the replay source of truth.
        A fresh sidecar seeds itself from replay so upgrades and restored logs
        preserve existing coordinates.
        """
        counter_path = self._path.with_suffix(self._path.suffix + ".timeline")
        counter_path.parent.mkdir(parents=True, exist_ok=True)
        with (
            counter_path.open("a+", encoding="utf-8") as stream,
            _exclusive_file_lock(stream, counter_path),
        ):
            stream.seek(0)
            try:
                counters = json.loads(stream.read() or "{}")
            except (json.JSONDecodeError, TypeError, ValueError):
                counters = {}
            if not isinstance(counters, dict):
                counters = {}
            raw_current = counters.get(turn_id)
            if isinstance(raw_current, int) and raw_current >= 0:
                current = raw_current
            else:
                replayed = next((turn for turn in self.replay() if turn.id == turn_id), None)
                current = (
                    max(
                        (
                            item.timeline_sequence or 0
                            for item in replayed.items
                            if item.timeline_sequence is not None
                        ),
                        default=0,
                    )
                    if replayed is not None
                    else 0
                )
            reserved = current + 1
            counters[turn_id] = reserved
            stream.seek(0)
            stream.truncate()
            stream.write(json.dumps(counters, separators=(",", ":")))
            stream.flush()
            return reserved

    def thread_started(self, thread_id: str) -> LoggedEvent:
        return self.append(
            LoggedEvent(
                event="thread_started",
                threadId=thread_id,
                payload={"streamId": f"stream_{new_id().hex}"},
            )
        )

    def turn_started(self, thread_id: str, turn: Turn) -> LoggedEvent:
        return self.append(
            LoggedEvent(
                event="turn_started",
                threadId=thread_id,
                turnId=turn.id,
                payload={
                    "params": (turn.params.model_dump(by_alias=True) if turn.params else None),
                    "startedAt": turn.started_at.isoformat(),
                    "objectiveId": turn.objective_id,
                    "taskId": turn.task_id,
                },
            )
        )

    def turn_completed(
        self,
        thread_id: str,
        turn_id: str,
        status: TurnStatus,
        error: dict[str, Any] | None = None,
    ) -> LoggedEvent:
        return self.append(
            LoggedEvent(
                event="turn_completed",
                threadId=thread_id,
                turnId=turn_id,
                payload={"status": status.value, "error": error},
            ),
            durable=True,
        )

    def turn_interrupt_requested(
        self,
        thread_id: str,
        turn_id: str,
        *,
        claim_epoch: str,
        requested_by_actor: str | None,
        tenant_id: str | None,
        request_id: str | None = None,
    ) -> LoggedEvent:
        """Durably address one cancellation request to one claim epoch.

        ``claim_epoch`` is minted by the OS-lock owner, never accepted from
        the client.  A later turn on the same thread therefore ignores a
        delayed request for an older owner (the classic validate/append ABA
        race).  Replay intentionally treats this control record as a no-op;
        it is an auditable signal consumed only by the resident claim owner.
        """

        payload: dict[str, Any] = {"claimEpoch": claim_epoch}
        if requested_by_actor is not None:
            payload["requestedByActor"] = requested_by_actor
        if tenant_id is not None:
            payload["tenantId"] = tenant_id
        if request_id is not None:
            payload["requestId"] = request_id
        return self.append(
            LoggedEvent(
                event="turn_interrupt_requested",
                threadId=thread_id,
                turnId=turn_id,
                payload=payload,
            ),
            durable=True,
        )

    def turn_updated(
        self,
        thread_id: str,
        turn_id: str,
        *,
        phases: list[dict[str, Any]] | None = None,
        workspace_focus: dict[str, Any] | None = None,
        workbench_snapshot: dict[str, Any] | None = None,
        grounding: list[dict[str, str]] | None = None,
        objective_id: str | None = None,
        task_id: str | None = None,
        checkpoint_id: int | None = None,
        outcome_reason: str | None = None,
    ) -> LoggedEvent | None:
        payload: dict[str, Any] = {}
        if phases is not None:
            payload["phases"] = phases
        if workspace_focus is not None:
            payload["workspaceFocus"] = workspace_focus
        if workbench_snapshot is not None:
            payload["workbenchSnapshot"] = workbench_snapshot
        if grounding is not None:
            payload["grounding"] = grounding
        if objective_id is not None:
            payload["objectiveId"] = objective_id
        if task_id is not None:
            payload["taskId"] = task_id
        if checkpoint_id is not None:
            payload["checkpointId"] = checkpoint_id
        if outcome_reason is not None:
            payload["outcomeReason"] = outcome_reason
        if not payload:
            return None
        return self.append(
            LoggedEvent(
                event="turn_updated",
                threadId=thread_id,
                turnId=turn_id,
                payload=payload,
            )
        )

    def turn_compacted(
        self,
        thread_id: str,
        summary_turn: Turn,
        superseded_turn_ids: list[str],
    ) -> LoggedEvent:
        """Record that ``superseded_turn_ids`` have been summarised into
        ``summary_turn``. Subsequent ``replay()`` calls will surface the
        summary in place of the old turns, keeping context bounded.

        The old events stay on disk — the log remains append-only, and
        audits can reconstruct the original sequence by ignoring
        ``turn_compacted`` events. Replay is the only reader affected.
        """
        return self.append(
            LoggedEvent(
                event="turn_compacted",
                threadId=thread_id,
                turnId=summary_turn.id,
                payload={
                    "summaryTurn": summary_turn.model_dump(by_alias=True, mode="json"),
                    "supersededTurnIds": list(superseded_turn_ids),
                },
            )
        )

    def item_started(
        self,
        thread_id: str,
        turn_id: str,
        item: Item,
        *,
        durable: bool = False,
    ) -> LoggedEvent:
        return self.append(
            LoggedEvent(
                event="item_started",
                threadId=thread_id,
                turnId=turn_id,
                payload={"item": item.model_dump(by_alias=True, mode="json")},
            ),
            durable=durable,
        )

    def item_delta(
        self,
        thread_id: str,
        turn_id: str,
        item_id: str,
        kind: str,
        delta: Any,
    ) -> LoggedEvent:
        return self.append(
            LoggedEvent(
                event="item_delta",
                threadId=thread_id,
                turnId=turn_id,
                payload={"itemId": item_id, "kind": kind, "delta": delta},
            )
        )

    def item_completed(self, thread_id: str, turn_id: str, item: Item) -> LoggedEvent:
        return self.append(
            LoggedEvent(
                event="item_completed",
                threadId=thread_id,
                turnId=turn_id,
                payload={"item": item.model_dump(by_alias=True, mode="json")},
            )
        )

    # ── Reader side ──────────────────────────────────────────

    def snapshot(self) -> EventLogSnapshot:
        """Capture one immutable prefix of the JSONL file.

        The byte length is fixed before reading. Concurrent appends beyond
        that boundary are never mixed into this snapshot, while a trailing
        partial line stays invisible until a later capture.
        """
        if not self._path.exists():
            return EventLogSnapshot(events=(), cursor=0, stream_id=None)
        with self._path.open("rb") as stream:
            stream.seek(0, 2)
            boundary = stream.tell()
            stream.seek(0)
            raw = stream.read(boundary)

        events: list[tuple[int, LoggedEvent]] = []
        seen_event_ids: set[str] = set()
        cursor = 0
        stream_id: str | None = None
        for sequence, raw_line in enumerate(raw.splitlines(keepends=True), start=1):
            if not raw_line.endswith(b"\n"):
                continue
            cursor = sequence
            try:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                event = LoggedEvent.model_validate(json.loads(line))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                continue
            event_id = event.event_id or (
                "legacy:" + hashlib.sha256(raw_line.removesuffix(b"\n")).hexdigest()[:24]
            )
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)
            if event.event_id is None:
                event = event.model_copy(update={"event_id": event_id})
            if stream_id is None and event.event == "thread_started":
                raw_stream_id = event.payload.get("streamId")
                stream_id = (
                    raw_stream_id if isinstance(raw_stream_id, str) and raw_stream_id else event_id
                )
            events.append((sequence, event))
        return EventLogSnapshot(
            events=tuple(events),
            cursor=cursor,
            stream_id=stream_id,
        )

    def tail_events(self, after_offset: int) -> tuple[list[LoggedEvent], int]:
        """Decode only complete events appended after a byte offset.

        Unlike the public replay cursor, this private polling cursor is a byte
        coordinate. It lets an active runtime notice cross-process steering
        several times per second without rereading a large conversation from
        byte zero. A truncated/replaced log safely falls back to the start;
        an incomplete trailing line remains pending for the next call.
        """
        if not self._path.exists():
            return [], 0
        with self._path.open("rb") as stream:
            stream.seek(0, 2)
            boundary = stream.tell()
            offset = max(0, int(after_offset))
            if offset > boundary:
                offset = 0
            stream.seek(offset)
            raw = stream.read(boundary - offset)
        complete_length = raw.rfind(b"\n") + 1
        if complete_length <= 0:
            return [], offset

        events: list[LoggedEvent] = []
        seen_event_ids: set[str] = set()
        for raw_line in raw[:complete_length].splitlines(keepends=True):
            try:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                event = LoggedEvent.model_validate(json.loads(line))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                continue
            event_id = event.event_id or (
                "legacy:" + hashlib.sha256(raw_line.removesuffix(b"\n")).hexdigest()[:24]
            )
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)
            if event.event_id is None:
                event = event.model_copy(update={"event_id": event_id})
            events.append(event)
        return events, offset + complete_length

    def iter_events_with_sequence(self) -> Iterator[tuple[int, LoggedEvent]]:
        """Yield ``(cursor, event)`` pairs in append order.

        The cursor is the one-based physical JSONL line number. That makes it
        stable for every existing log without a migration and monotonic across
        process restarts. Malformed lines consume a cursor but are not yielded;
        a later valid line still advances beyond them.
        """
        yield from self.snapshot().events

    def iter_events(self) -> Iterator[LoggedEvent]:
        """Yield events in append order. Skips malformed lines."""
        for _sequence, event in self.iter_events_with_sequence():
            yield event

    def cursor_delta(
        self,
        after_sequence: int,
        *,
        snapshot: EventLogSnapshot | None = None,
    ) -> tuple[list[str], int, bool]:
        """Return changed turn ids, latest cursor and whether a reset is needed.

        ``turn_compacted`` rewrites the visible turn set, so an incremental
        merge cannot represent it safely; callers receive ``requires_reset``
        and fall back to a normal window snapshot. A cursor beyond the current
        file also signals reset (log replacement/truncation).
        """
        captured = snapshot or self.snapshot()
        changed_turn_ids, requires_reset = captured.cursor_delta(after_sequence)
        return changed_turn_ids, captured.cursor, requires_reset

    def latest_sequence(self, *, snapshot: EventLogSnapshot | None = None) -> int:
        """Return the current append cursor without decoding event payloads."""
        return (snapshot or self.snapshot()).cursor

    def replay(self, snapshot: EventLogSnapshot | None = None) -> list[Turn]:
        """Reconstruct the full turn list from disk.

        After the call you have the same in-memory state the producing
        process held when it last wrote a ``turn_completed`` (or the
        last consistent ``item_*`` if the turn was still running).
        """
        return (snapshot or self.snapshot()).replay()

    @staticmethod
    def paginate_turns(
        turns: list[Turn],
        *,
        limit: int | None = None,
        before_turn_id: str | None = None,
    ) -> tuple[list[Turn], bool]:
        """Newest-window slice of an already-replayed turn list.

        ``before_turn_id`` (exclusive cursor) confines the window to
        turns strictly older than that id; ``limit`` keeps the newest
        N of the window. Returns ``(window, has_more)`` where
        ``has_more`` means turns older than the window exist —
        clients page backwards with ``before_turn_id = window[0].id``.

        No ``limit`` → the full window (back-compat: thread/resume
        without params behaves exactly as before). An unknown cursor
        falls back to the full list rather than guessing.

        Replay still walks the whole JSONL; what pagination saves is
        the model_dump + wire payload + client-side reduce, which is
        where large threads actually hurt.
        """
        window = turns
        if before_turn_id:
            idx = next((i for i, t in enumerate(turns) if t.id == before_turn_id), None)
            if idx is not None:
                window = turns[:idx]
        if limit is None or limit <= 0 or limit >= len(window):
            # Whole window returned — nothing older remains beyond it.
            return window, False
        return window[-limit:], True

    def summary(
        self,
        snapshot: EventLogSnapshot | None = None,
    ) -> ThreadSummary | None:
        """Lightweight metadata snapshot for thread/list responses.

        Walks the file once to compute counts; doesn't materialize
        every Turn. Returns ``None`` if the log file doesn't exist.
        """
        if not self._path.exists():
            return None
        first_ts: datetime | None = None
        last_ts: datetime | None = None
        turn_count = 0
        last_status: TurnStatus | None = None
        archived = False
        captured = snapshot or self.snapshot()
        for _sequence, evt in captured.events:
            if first_ts is None:
                first_ts = evt.ts
            last_ts = evt.ts
            if evt.event == "turn_started":
                turn_count += 1
                last_status = TurnStatus.IN_PROGRESS
            elif evt.event == "turn_completed":
                status_raw = evt.payload.get("status")
                if isinstance(status_raw, str):
                    try:
                        last_status = TurnStatus(status_raw)
                    except ValueError:
                        last_status = None
            elif evt.event == "thread_archived":
                archived = True
        if first_ts is None or last_ts is None:
            return None
        return ThreadSummary(
            thread_id=_thread_id_from_path(self._path),
            path=str(self._path),
            created_at=first_ts,
            updated_at=last_ts,
            turn_count=turn_count,
            last_turn_status=last_status,
            archived=archived,
        )


# ── Directory-level helpers ───────────────────────────────────


class ThreadSummary(BaseModel):
    """Lightweight thread metadata for list views.

    Server emits one ``ThreadSummary`` per thread on a ``thread/list``
    response; the full turn replay only happens when the client picks
    a thread and requests ``thread/resume``.
    """

    model_config = ConfigDict(populate_by_name=True)

    thread_id: str = Field(alias="threadId")
    path: str
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    turn_count: int = Field(alias="turnCount")
    last_turn_status: TurnStatus | None = Field(default=None, alias="lastTurnStatus")
    archived: bool = False


def list_threads(logs_root: Path | str) -> list[ThreadSummary]:
    """Enumerate every thread under ``logs_root`` ordered by recency.

    Skips files that don't parse as a valid thread log. Returns an
    empty list if the directory doesn't exist yet.
    """
    root = Path(logs_root)
    if not root.exists():
        return []
    summaries: list[ThreadSummary] = []
    for path in root.glob("*.jsonl"):
        log = EventLog(path)
        s = log.summary()
        if s is not None:
            summaries.append(s)
    summaries.sort(key=lambda s: s.updated_at, reverse=True)
    return summaries


def archive_thread(logs_root: Path | str, thread_id: str) -> bool:
    """Append a ``thread_archived`` event. Returns True if the thread
    log existed; False otherwise (caller decides whether that's a
    404 or silently OK)."""
    log = EventLog(thread_log_path(logs_root, thread_id))
    if not log.path.exists():
        return False
    log.append(LoggedEvent(event="thread_archived", threadId=thread_id))
    return True


__all__ = [
    "EventKind",
    "EventLog",
    "EventLogSnapshot",
    "LoggedEvent",
    "ThreadSummary",
    "actor_id_from_turn_params",
    "archive_thread",
    "list_threads",
    "owner_actor_id_from_turns",
    "thread_log_path",
    "validate_thread_id",
]
