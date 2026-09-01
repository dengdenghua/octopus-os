from __future__ import annotations

import contextlib
import threading
from dataclasses import replace
from typing import Any
from uuid import UUID

from .journal import (
    JournalEvent,
    NodeStartedEvent,
    StepEvent,
    TaskCheckpointEvent,
    TaskStartedEvent,
    TrajectoryEvent,
)
from .progress import TaskProgressSnapshot


class TaskProgressTracker:
    def __init__(self, streaming_journal: Any) -> None:
        self._journal = streaming_journal
        self._lock = threading.RLock()
        self._by_task: dict[str, TaskProgressSnapshot] = {}

        for ev in streaming_journal.read_all():
            if ev.task_id is None:
                continue
            self._ingest(ev)

        self._unsubscribe = streaming_journal.subscribe(self._on_event)

    def close(self) -> None:
        """Unsubscribe from the journal · release the callback ref.

        Tolerant of (a) a journal that never supported subscribe
        (returns a no-op unsub), (b) being called twice, and (c)
        the journal already being torn down. None of those should
        surface as a hard error to the caller since close() runs
        on teardown paths.
        """
        with contextlib.suppress(AttributeError, RuntimeError):
            self._unsubscribe()

    def __enter__(self) -> TaskProgressTracker:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def snapshots(self) -> list[TaskProgressSnapshot]:
        with self._lock:
            snaps = list(self._by_task.values())
        from datetime import datetime

        snaps.sort(
            key=lambda s: s.last_event_ts or datetime.min.replace(tzinfo=None),
            reverse=True,
        )
        return snaps

    def get(self, task_id: UUID | str) -> TaskProgressSnapshot | None:
        key = str(task_id)
        with self._lock:
            return self._by_task.get(key)

    def count(self) -> int:
        with self._lock:
            return len(self._by_task)

    def running_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._by_task.values() if s.status == "running")

    def _on_event(self, ev: JournalEvent) -> None:
        if ev.task_id is None:
            return
        try:
            self._ingest(ev)
        except Exception as e:  # noqa: BLE001 - see docstring
            # Unknown-event shape is the realistic failure. Don't
            # spam a logger on every journal append; one warning
            # per unique error type is enough to surface the bug
            # without drowning the signal. Implementing that is
            # follow-up; for now just preserve the swallow behavior
            # and let a future logging sweep narrow it.
            _ = e  # noqa: F841

    def _ingest(self, ev: JournalEvent) -> None:
        task_id = str(ev.task_id)
        with self._lock:
            snap = self._by_task.get(task_id)
            if snap is None:
                snap = _empty_snapshot(task_id)
                self._by_task[task_id] = snap

            if snap.last_event_ts is None or ev.ts > snap.last_event_ts:
                snap = _copy(snap, last_event_ts=ev.ts)

            if isinstance(ev, TaskStartedEvent):
                snap = _copy(
                    snap,
                    total_nodes=ev.total_nodes,
                    strategy=ev.strategy,
                    task_type=ev.task_type,
                    started_at=ev.ts,
                    status="running",
                )
            elif isinstance(ev, NodeStartedEvent):
                new_status = "running" if snap.status == "unknown" else snap.status
                snap = _copy(
                    snap,
                    current_node_id=ev.node_id,
                    current_node_index=ev.node_index,
                    status=new_status,
                )
            elif isinstance(ev, StepEvent):
                if ev.step.success:
                    snap = _copy(snap, nodes_completed=snap.nodes_completed + 1)
            elif isinstance(ev, TaskCheckpointEvent):
                snap = _copy(
                    snap,
                    tokens_spent=ev.tokens_spent,
                    usd_spent=round(ev.usd_spent, 6),
                )
            elif isinstance(ev, TrajectoryEvent):
                final_status = "completed" if ev.trajectory.outcome.success else "failed"
                nc = sum(1 for s in ev.trajectory.steps if s.success)
                cost = ev.trajectory.outcome.cost
                tokens = cost.tokens if cost.tokens > 0 else snap.tokens_spent
                usd = cost.usd if cost.usd > 0 else snap.usd_spent
                snap = _copy(
                    snap,
                    status=final_status,
                    nodes_completed=nc,
                    current_node_id=None,
                    current_node_index=None,
                    tokens_spent=tokens,
                    usd_spent=round(usd, 6),
                )

            self._by_task[task_id] = snap


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════


def _empty_snapshot(task_id: str) -> TaskProgressSnapshot:
    return TaskProgressSnapshot(
        task_id=task_id,
        total_nodes=0,
        nodes_completed=0,
        current_node_id=None,
        current_node_index=None,
        status="unknown",
        started_at=None,
        last_event_ts=None,
        strategy="",
        task_type="",
        tokens_spent=0,
        usd_spent=0.0,
    )


def _copy(snap: TaskProgressSnapshot, **updates: Any) -> TaskProgressSnapshot:
    return replace(snap, **updates)
