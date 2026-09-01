"""The only global state: Project → Milestone → Task DAG.

Design rules baked into the shapes:
- A Task is bound to a Milestone (MS → tasks → agent), never user → agent.
- A Milestone carries the *spec* + *success_criteria* that the QA gate checks —
  the milestone, not the loop, is the stop condition.
- Tasks form a DAG via ``depends_on``; a task is *ready* only when its deps are done.

Pure dataclasses with dict round-trips so the store can be sqlite/JSON and the
engine stays I/O-free.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import isfinite
from typing import Any, Literal


def _is_finite_num(value: Any) -> bool:
    try:
        return isfinite(float(value))
    except (TypeError, ValueError):
        return False


TaskType = Literal["design", "code", "research", "analysis", "review"]
TaskStatus = Literal["pending", "ready", "running", "blocked", "done", "failed", "rejected"]
# How a project task node is executed: a single member (single), a leaderless
# parallel brainstorm (swarm, 蜂群), or an orchestrated role team (cluster, 集群).
# This is the seam that lets 项目模式 (the milestone engine) reuse the cluster /
# swarm engines as its per-task executors instead of always running single-agent.
TeamMode = Literal["single", "swarm", "cluster"]
MilestoneStatus = Literal["pending", "active", "in_progress", "blocked", "done", "failed"]
ProjectStatus = Literal["planning", "running", "blocked", "done", "failed"]

# Which role owns which kind of work (L1 routing).
ROLE_FOR_TASK: dict[str, str] = {
    "design": "engineer",
    "code": "engineer",
    "analysis": "engineer",
    "research": "research",
    "review": "qa",
}


@dataclass
class Task:
    """One DAG node of work, bound to a milestone and owned by a role."""

    id: str
    milestone_id: str
    type: TaskType
    goal: str
    assigned_role: str = "engineer"
    assigned_agent: str = ""  # concrete agent id, filled at dispatch
    team_mode: TeamMode = "single"  # single | swarm | cluster execution engine
    # ── 现实 PM 维度 ───────────────────────────────
    priority: str = "P2"  # P0 urgent / P1 high / P2 normal / P3 low
    estimate: float = 0.0  # person-days (or story points)
    due_at: str = ""  # ISO date this node should be done by
    acceptance_criteria: list[str] = field(default_factory=list)  # definition of done
    status: TaskStatus = "pending"
    depends_on: list[str] = field(default_factory=list)
    input: dict[str, Any] = field(default_factory=dict)
    output: Any = None
    qa_verdict: dict[str, Any] | None = None  # set by the QA gate
    attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Task:
        return cls(
            id=str(raw["id"]),
            milestone_id=str(raw.get("milestone_id") or ""),
            type=raw.get("type")
            if raw.get("type") in ("design", "code", "research", "analysis", "review")
            else "code",
            goal=str(raw.get("goal") or ""),
            assigned_role=str(raw.get("assigned_role") or "engineer"),
            assigned_agent=str(raw.get("assigned_agent") or ""),
            team_mode=raw.get("team_mode")
            if raw.get("team_mode") in ("single", "swarm", "cluster")
            else "single",
            priority=raw.get("priority")
            if raw.get("priority") in ("P0", "P1", "P2", "P3")
            else "P2",
            estimate=float(raw.get("estimate") or 0)
            if _is_finite_num(raw.get("estimate"))
            else 0.0,
            due_at=str(raw.get("due_at") or ""),
            acceptance_criteria=[str(c) for c in (raw.get("acceptance_criteria") or [])],
            status=raw.get("status")
            if raw.get("status")
            in ("pending", "ready", "running", "blocked", "done", "failed", "rejected")
            else "pending",
            depends_on=[str(d) for d in (raw.get("depends_on") or [])],
            input=dict(raw.get("input") or {}),
            output=raw.get("output"),
            qa_verdict=raw.get("qa_verdict"),
            attempts=int(raw.get("attempts") or 0),
        )


@dataclass
class Milestone:
    """A project phase with a spec + success criteria the QA gate enforces."""

    id: str
    name: str
    goal: str
    spec: dict[str, Any] = field(default_factory=dict)
    success_criteria: list[str] = field(default_factory=list)
    # ── 现实 PM 维度 ───────────────────────────────
    priority: str = "P2"  # P0-P3
    planned_start: str = ""  # ISO date the phase begins
    due_at: str = ""  # ISO date the phase should complete by
    status: MilestoneStatus = "pending"
    dependencies: list[str] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Milestone:
        status = raw.get("status")
        return cls(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            goal=str(raw.get("goal") or ""),
            spec=dict(raw.get("spec") or {}),
            success_criteria=[str(s) for s in (raw.get("success_criteria") or [])],
            priority=raw.get("priority")
            if raw.get("priority") in ("P0", "P1", "P2", "P3")
            else "P2",
            planned_start=str(raw.get("planned_start") or ""),
            due_at=str(raw.get("due_at") or ""),
            status=status
            if status in ("pending", "active", "in_progress", "blocked", "done", "failed")
            else "pending",
            dependencies=[str(d) for d in (raw.get("dependencies") or [])],
            task_ids=[str(t) for t in (raw.get("task_ids") or [])],
        )


@dataclass
class Project:
    """The top-level container; ``current_ms`` is the active milestone."""

    id: str
    name: str
    goal: str
    milestone_ids: list[str] = field(default_factory=list)
    current_ms: str | None = None
    status: ProjectStatus = "planning"
    owner_id: str = ""
    tenant_id: str = ""
    execution_thread_id: str = ""
    # ── 现实 PM 维度 ───────────────────────────────
    owner: str = ""  # project manager display name
    created_at: str = ""  # ISO timestamp
    started_at: str = ""  # ISO timestamp (first active milestone)
    finished_at: str = ""  # ISO timestamp (project done)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Project:
        status = raw.get("status")
        return cls(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            goal=str(raw.get("goal") or ""),
            milestone_ids=[str(m) for m in (raw.get("milestone_ids") or [])],
            current_ms=raw.get("current_ms"),
            status=status
            if status in ("planning", "running", "blocked", "done", "failed")
            else "planning",
            owner_id=str(raw.get("owner_id") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            execution_thread_id=str(raw.get("execution_thread_id") or ""),
            owner=str(raw.get("owner") or ""),
            created_at=str(raw.get("created_at") or ""),
            started_at=str(raw.get("started_at") or ""),
            finished_at=str(raw.get("finished_at") or ""),
        )


_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def ready_tasks(tasks: list[Task]) -> list[Task]:
    """Tasks whose dependencies are all done and aren't finished/running yet —
    the DAG frontier the engine can dispatch this tick.

    Ordered for PM: highest priority (P0 first) wins; ties break by earlier
    due date, then smaller estimate, so the most urgent work is dispatched
    first when concurrency is limited.
    """
    done = {t.id for t in tasks if t.status == "done"}
    out = [
        t
        for t in tasks
        if t.status in ("pending", "ready") and all(d in done for d in t.depends_on)
    ]
    out.sort(
        key=lambda t: (
            _PRIORITY_RANK.get(t.priority, 2),
            t.due_at or "",
            t.estimate,
            t.id,
        )
    )
    return out


def milestone_blocked_on(milestone: Milestone, all_done: set[str]) -> list[str]:
    """Which of a milestone's dependencies aren't satisfied yet."""
    return [d for d in milestone.dependencies if d not in all_done]
