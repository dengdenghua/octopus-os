from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from .journal import (
    Journal,
    JournalEvent,
    NodeStartedEvent,
    StepEvent,
    TaskCheckpointEvent,
    TaskStartedEvent,
    TrajectoryEvent,
)

ProgressStatus = Literal["running", "completed", "failed", "unknown"]


@dataclass
class TaskProgressSnapshot:
    task_id: str
    total_nodes: int
    nodes_completed: int
    current_node_id: str | None
    current_node_index: int | None
    status: ProgressStatus
    started_at: datetime | None
    last_event_ts: datetime | None
    strategy: str
    task_type: str
    tokens_spent: int
    usd_spent: float

    @property
    def progress_pct(self) -> float:
        if self.total_nodes <= 0:
            return 0.0
        return round(self.nodes_completed / self.total_nodes * 100, 1)


def task_progress_snapshot(
    journal: Journal,
    task_id: UUID | str,
) -> TaskProgressSnapshot | None:
    target = str(task_id)
    events = [e for e in journal.read_all() if str(e.task_id) == target]
    if not events:
        return None
    return _aggregate(target, events)


def all_task_progress(journal: Journal) -> list[TaskProgressSnapshot]:
    by_task: dict[str, list[JournalEvent]] = {}
    for e in journal.read_all():
        if e.task_id is None:
            continue
        by_task.setdefault(str(e.task_id), []).append(e)
    snaps = [_aggregate(tid, evs) for tid, evs in by_task.items()]
    snaps.sort(
        key=lambda s: s.last_event_ts or datetime.min.replace(tzinfo=None),
        reverse=True,
    )
    return snaps


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _aggregate(task_id: str, events: list[JournalEvent]) -> TaskProgressSnapshot:
    total_nodes = 0
    nodes_completed = 0
    current_node_id: str | None = None
    current_node_index: int | None = None
    status: ProgressStatus = "unknown"
    started_at: datetime | None = None
    last_event_ts: datetime | None = None
    strategy = ""
    task_type = ""
    tokens_spent = 0
    usd_spent = 0.0

    for e in events:
        if last_event_ts is None or e.ts > last_event_ts:
            last_event_ts = e.ts

        if isinstance(e, TaskStartedEvent):
            total_nodes = e.total_nodes
            strategy = e.strategy
            task_type = e.task_type
            started_at = e.ts
            status = "running"

        elif isinstance(e, NodeStartedEvent):
            current_node_id = e.node_id
            current_node_index = e.node_index
            if status == "unknown":
                status = "running"

        elif isinstance(e, StepEvent):
            if e.step.success:
                nodes_completed += 1

        elif isinstance(e, TaskCheckpointEvent):
            tokens_spent = e.tokens_spent
            usd_spent = e.usd_spent

        elif isinstance(e, TrajectoryEvent):
            status = "completed" if e.trajectory.outcome.success else "failed"
            if e.trajectory.step_count > 0:
                nodes_completed = sum(1 for s in e.trajectory.steps if s.success)
            current_node_id = None
            current_node_index = None
            cost = e.trajectory.outcome.cost
            if cost.tokens > 0:
                tokens_spent = cost.tokens
            if cost.usd > 0:
                usd_spent = cost.usd

    return TaskProgressSnapshot(
        task_id=task_id,
        total_nodes=total_nodes,
        nodes_completed=nodes_completed,
        current_node_id=current_node_id,
        current_node_index=current_node_index,
        status=status,
        started_at=started_at,
        last_event_ts=last_event_ts,
        strategy=strategy,
        task_type=task_type,
        tokens_spent=tokens_spent,
        usd_spent=round(usd_spent, 6),
    )
