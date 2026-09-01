"""Curriculum subsystem for evolution operators."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .utils import (
    _as_dt,
    _iso,
    _journal_events,
    _stable_int_id,
    _trajectory_rows,
    _utcnow,
)


def _curriculum_goal_rows(
    journal: Any,
    *,
    status: str | None = None,
) -> list[dict[str, Any]]:
    decisions = _curriculum_goal_decision_map(journal)
    clusters: dict[str, dict[str, Any]] = {}

    for event, traj in _trajectory_rows(journal):
        fallback_ts = (
            _as_dt(getattr(traj, "completed_at", None))
            or _as_dt(getattr(event, "ts", None))
            or _utcnow()
        )
        task_id = str(getattr(traj, "task_id", "") or "")
        strategy_id = str(getattr(traj, "strategy_id", "") or "default")
        failed_steps = [
            step
            for step in (getattr(traj, "steps", []) or [])
            if not bool(getattr(step, "success", False))
        ]

        if failed_steps:
            for step in failed_steps:
                action = getattr(step, "action", None)
                result = getattr(step, "result", None)
                skill_name = str(getattr(action, "sucker_id", "") or "unknown")
                result_status = str(getattr(result, "status", "") or "failed")
                error_type = str(getattr(result, "error_type", "") or result_status)
                cluster_key = f"skill:{skill_name}:{result_status}:{error_type}"
                _add_curriculum_cluster(
                    clusters,
                    cluster_key=cluster_key,
                    category="skill_failure",
                    title=f"Stabilize {skill_name}",
                    description=(
                        f"{skill_name} failed with {error_type}. Add recovery "
                        "rules, argument validation, or a narrower fallback path."
                    ),
                    keywords=[skill_name, result_status, error_type],
                    task_id=task_id,
                    ts=fallback_ts,
                )
            continue

        outcome = getattr(traj, "outcome", None)
        if outcome is not None and not bool(getattr(outcome, "success", True)):
            cluster_key = f"strategy:{strategy_id}:outcome_failed"
            _add_curriculum_cluster(
                clusters,
                cluster_key=cluster_key,
                category="strategy_failure",
                title=f"Improve {strategy_id}",
                description=(
                    f"{strategy_id} produced failed trajectories without a "
                    "single failed tool step. Review planning, stopping "
                    "criteria, and final answer checks."
                ),
                keywords=[strategy_id, "outcome_failed"],
                task_id=task_id,
                ts=fallback_ts,
            )

    rows: list[dict[str, Any]] = []
    for cluster_key, cluster in clusters.items():
        decision = decisions.get(cluster_key, {})
        current_status = str(decision.get("status") or "pending")
        if status and status != current_status:
            continue
        failure_count = len(cluster["task_ids"])
        goal_id = _stable_int_id(cluster_key)
        rows.append(
            {
                "id": goal_id,
                "cluster_key": cluster_key,
                "category": cluster["category"],
                "title": cluster["title"],
                "description": cluster["description"],
                "keywords": sorted(cluster["keywords"]),
                "failure_count": failure_count,
                "priority": round(min(100.0, failure_count * 10.0), 1),
                "status": current_status,
                "covered_by": decision.get("covered_by"),
                "last_seen": _iso(cluster["last_seen"]),
            }
        )

    rows.sort(
        key=lambda row: (
            -float(row["priority"]),
            -int(row["failure_count"]),
            str(row["title"]),
        )
    )
    return rows[:100]


def _add_curriculum_cluster(
    clusters: dict[str, dict[str, Any]],
    *,
    cluster_key: str,
    category: str,
    title: str,
    description: str,
    keywords: list[str],
    task_id: str,
    ts: datetime,
) -> None:
    cluster = clusters.setdefault(
        cluster_key,
        {
            "category": category,
            "title": title,
            "description": description,
            "keywords": set(),
            "task_ids": set(),
            "last_seen": ts,
        },
    )
    cluster["keywords"].update(k for k in keywords if k)
    if task_id:
        cluster["task_ids"].add(task_id)
    else:
        cluster["task_ids"].add(f"event:{len(cluster['task_ids'])}")
    if ts > cluster["last_seen"]:
        cluster["last_seen"] = ts


def _curriculum_goal_decision_map(journal: Any) -> dict[str, dict[str, Any]]:
    decisions: dict[str, tuple[datetime, dict[str, Any]]] = {}
    if journal is None:
        return {}
    try:
        events = list(journal.read_by_type("curriculum_goal_decision"))
    except (AttributeError, TypeError, OSError):
        events = [
            event
            for event in _journal_events(journal)
            if getattr(event, "event_type", "") == "curriculum_goal_decision"
        ]
    for event in events:
        cluster_key = str(getattr(event, "cluster_key", "") or "").strip()
        status = str(getattr(event, "status", "") or "").strip()
        if not cluster_key or not status:
            continue
        ts = _as_dt(getattr(event, "ts", None)) or _utcnow()
        payload = {
            "status": status,
            "covered_by": getattr(event, "covered_by", None),
        }
        existing = decisions.get(cluster_key)
        if existing is None or ts >= existing[0]:
            decisions[cluster_key] = (ts, payload)
    return {key: payload for key, (_ts, payload) in decisions.items()}


def _write_curriculum_goal_decision(
    journal: Any,
    *,
    goal_id: int,
    cluster_key: str,
    status: str,
    covered_by: Any = None,
    reason: str = "",
    details: dict[str, Any] | None = None,
) -> bool:
    if journal is None:
        return False
    try:
        if hasattr(journal, "write_curriculum_goal_decision"):
            journal.write_curriculum_goal_decision(
                goal_id=goal_id,
                cluster_key=cluster_key,
                status=status,
                covered_by=str(covered_by) if covered_by else None,
                reason=reason,
                details=details or {},
            )
        else:
            from runtime.memory.journal import CurriculumGoalDecisionEvent

            journal.write(
                CurriculumGoalDecisionEvent(
                    goal_id=goal_id,
                    cluster_key=cluster_key,
                    status=status,
                    covered_by=str(covered_by) if covered_by else None,
                    reason=reason,
                    details=details or {},
                )
            )
        return True
    except (AttributeError, TypeError, OSError):
        return False


def _section_line_count(section: str) -> int:
    return sum(1 for line in (section or "").splitlines() if line.lstrip().startswith("- ["))


def _learned_section_counts(planner: Any) -> dict[str, int]:
    if planner is None:
        return {"rules": 0, "memories": 0}
    return {
        "rules": _section_line_count(str(getattr(planner, "learned_rules_section", "") or "")),
        "memories": _section_line_count(
            str(getattr(planner, "learned_memories_section", "") or "")
        ),
    }
