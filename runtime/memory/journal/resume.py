# ruff: noqa: E402 — module-level imports below are intentionally late

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


def _maybe_json_load(raw: Any) -> Any:
    """If ``raw`` is a JSON-ish string (starts with ``{`` or ``[``),
    attempt to decode it. Other values pass through unchanged. Used
    by resume to recover dict/list semantics from the str-coerced
    ``ExecutionResult.output``."""
    if not isinstance(raw, str):
        return raw
    s = raw.lstrip()
    if not s or s[0] not in "{[":
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


from .journal import (
    Journal,
    JournalEvent,
    StepEvent,
    TaskStartedEvent,
    TrajectoryEvent,
)


@dataclass
class CompletedNode:
    node_id: str
    step_id: int
    sucker_id: str
    output: Any
    args_template: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResumeInfo:
    task_id: str
    completed_nodes: list[CompletedNode] = field(default_factory=list)
    outputs_by_node: dict[str, Any] = field(default_factory=dict)
    resume_from_index: int = 0
    total_nodes: int = 0
    task_terminated: bool = False
    task_success: bool | None = None
    task_disposition: str | None = None
    strategy: str = ""
    tokens_spent_snapshot: int = 0
    usd_spent_snapshot: float = 0.0

    @property
    def is_resumable(self) -> bool:
        return (
            not self.task_terminated
            and self.total_nodes > 0
            and self.resume_from_index < self.total_nodes
        )


def resume_info(journal: Journal, task_id: UUID | str) -> ResumeInfo | None:
    target = str(task_id)
    events: list[JournalEvent] = [e for e in journal.read_all() if str(e.task_id) == target]
    if not events:
        return None

    info = ResumeInfo(task_id=target)

    # Collapse retries by ``step_id`` first, keeping the latest journal
    # event for each slot. Resume appends new StepEvents when a failed
    # task is re-run from a checkpoint, so naïvely sorting all StepEvents
    # can let an early failed copy of step 1 permanently mask the later
    # successful retry of step 1. We preserve journal order here (the
    # event list is append-only chronological) and only then sort the
    # deduped set by step_id for contiguous-prefix reconstruction.
    latest_step_by_id: dict[int, StepEvent] = {}
    for e in events:
        if isinstance(e, StepEvent):
            latest_step_by_id[e.step.step_id] = e
    step_events = sorted(latest_step_by_id.values(), key=lambda e: e.step.step_id)

    for e in events:
        if isinstance(e, TaskStartedEvent):
            info.total_nodes = e.total_nodes
            info.strategy = e.strategy
        elif isinstance(e, TrajectoryEvent):
            info.task_terminated = True
            info.task_success = e.trajectory.outcome.success
            info.task_disposition = e.trajectory.outcome.disposition
            if e.trajectory.outcome.cost.tokens > 0:
                info.tokens_spent_snapshot = e.trajectory.outcome.cost.tokens
            if e.trajectory.outcome.cost.usd > 0:
                info.usd_spent_snapshot = e.trajectory.outcome.cost.usd

    last_step_id = -1
    for expected_step_id, se in enumerate(step_events):
        if se.step.step_id != expected_step_id:
            break
        if not se.step.success:
            break
        cn = CompletedNode(
            node_id=se.step.node_id,
            step_id=se.step.step_id,
            sucker_id=str(se.step.action.sucker_id),
            args_template=dict(se.step.args_template),
            output=_maybe_json_load(se.step.result.output),
        )
        info.completed_nodes.append(cn)
        info.outputs_by_node[cn.node_id] = cn.output
        last_step_id = se.step.step_id

    info.resume_from_index = last_step_id + 1
    return info
