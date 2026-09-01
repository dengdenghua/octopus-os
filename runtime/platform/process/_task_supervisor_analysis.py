from __future__ import annotations

from typing import Any

from runtime.platform.process._task_supervisor_models import (
    TERMINAL_TASK_STATUSES,
    TaskRunRecord,
    TaskRunStatus,
    _now_iso,
)


def task_lease_health(record: TaskRunRecord) -> dict[str, Any]:
    lease = record.lease
    if record.status in TERMINAL_TASK_STATUSES:
        state = "terminal"
    elif lease is None:
        state = "missing_lease"
    elif lease.expired:
        state = "expired"
    else:
        state = "ok"
    recovery = task_recovery_advice(record, lease_state=state)
    return {
        "task_id": record.task_id,
        "status": record.status.value,
        "kind": record.kind,
        "state": state,
        "holder_id": lease.holder_id if lease is not None else None,
        "lease_token": lease.token if lease is not None else None,
        "lease_expires_at": lease.expires_at if lease is not None else None,
        "lease_heartbeat_at": lease.heartbeat_at if lease is not None else None,
        "task_heartbeat_at": record.heartbeat_at,
        "updated_at": record.updated_at,
        "can_takeover": recovery["can_takeover"],
        "can_resume": recovery["can_resume"],
        "has_checkpoint": recovery["has_checkpoint"],
        "recommended_action": recovery["recommended_action"],
        "recovery_reason": recovery["reason"],
        "recovery": recovery,
    }


def task_recovery_advice(
    record: TaskRunRecord,
    *,
    lease_state: str | None = None,
) -> dict[str, Any]:
    state = str(lease_state or "").strip()
    if not state:
        if record.status in TERMINAL_TASK_STATUSES:
            state = "terminal"
        elif record.lease is None:
            state = "missing_lease"
        elif record.lease.expired:
            state = "expired"
        else:
            state = "ok"
    has_checkpoint = bool(record.latest_checkpoint_id or record.resume_checkpoint_id)
    can_takeover = False
    can_resume = False
    action = "monitor"
    reason = "task is active with a healthy lease"

    if record.status in TERMINAL_TASK_STATUSES:
        action = "none"
        reason = "task is already terminal"
        if record.status in {
            TaskRunStatus.FAILED,
            TaskRunStatus.CANCELLED,
            TaskRunStatus.DISCONNECTED,
        }:
            can_resume = True
            action = "resume_from_checkpoint" if has_checkpoint else "restart"
            reason = f"task ended as {record.status.value}"
    elif record.status == TaskRunStatus.PENDING:
        action = "dispatch"
        reason = "task has not started"
    elif record.status == TaskRunStatus.WAITING_APPROVAL:
        if bool(record.metadata.get("capability_denied")):
            action = "capability_policy_denied"
            reason = "task is blocked by disabled capability"
        elif bool(record.metadata.get("approval_denied")) and not bool(
            record.metadata.get("approval_required")
        ):
            action = "approval_policy_denied"
            reason = "task is blocked by approval policy"
        elif state in {"expired", "missing_lease"}:
            can_takeover = True
            action = "takeover_for_approval"
            reason = "task is waiting for approval but has no live lease"
        else:
            action = "await_operator_approval"
            reason = "task is waiting for approval"
    elif state in {"expired", "missing_lease"}:
        can_takeover = True
        can_resume = has_checkpoint
        action = "takeover_and_resume" if has_checkpoint else "takeover"
        reason = "task has no live lease"
    elif record.status == TaskRunStatus.PAUSED:
        can_resume = has_checkpoint
        action = "resume_paused_task"
        reason = "task is paused"

    checkpoint_id = record.latest_checkpoint_id or record.resume_checkpoint_id
    operation, steps = _task_recovery_operation(action)
    return {
        "can_takeover": can_takeover,
        "can_resume": can_resume,
        "has_checkpoint": has_checkpoint,
        "recommended_action": action,
        "operation": operation,
        "steps": steps,
        "reason": reason,
        "latest_checkpoint_id": record.latest_checkpoint_id,
        "resume_checkpoint_id": record.resume_checkpoint_id,
        "checkpoint_id": checkpoint_id,
    }


def build_task_runs_overview(tasks: list[TaskRunRecord]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    by_recommended_action: dict[str, int] = {}
    active_task_ids: list[str] = []
    expired_lease_task_ids: list[str] = []
    stale_nonterminal_task_ids: list[str] = []
    leased_task_ids: list[str] = []
    takeover_task_ids: list[str] = []
    resumable_task_ids: list[str] = []
    lease_health: list[dict[str, Any]] = []
    for task in tasks:
        status = task.status.value
        by_status[status] = by_status.get(status, 0) + 1
        kind = str(task.kind or "task")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        health = task_lease_health(task)
        action = str(health.get("recommended_action") or "unknown")
        by_recommended_action[action] = by_recommended_action.get(action, 0) + 1
        if bool(health.get("can_takeover")):
            takeover_task_ids.append(task.task_id)
        if bool(health.get("can_resume")):
            resumable_task_ids.append(task.task_id)
        if task.status in TERMINAL_TASK_STATUSES:
            continue
        active_task_ids.append(task.task_id)
        lease_health.append(health)
        if task.lease is None:
            stale_nonterminal_task_ids.append(task.task_id)
            continue
        leased_task_ids.append(task.task_id)
        if task.lease.expired:
            expired_lease_task_ids.append(task.task_id)
            stale_nonterminal_task_ids.append(task.task_id)
    return {
        "schema": "echo.task_runs_overview.v1",
        "total": len(tasks),
        "active_count": len(active_task_ids),
        "terminal_count": len(tasks) - len(active_task_ids),
        "leased_count": len(leased_task_ids),
        "expired_lease_count": len(expired_lease_task_ids),
        "stale_nonterminal_count": len(stale_nonterminal_task_ids),
        "takeover_recommended_count": len(takeover_task_ids),
        "resumable_count": len(resumable_task_ids),
        "by_status": dict(sorted(by_status.items())),
        "by_kind": dict(sorted(by_kind.items())),
        "by_recommended_action": dict(sorted(by_recommended_action.items())),
        "active_task_ids": active_task_ids,
        "expired_lease_task_ids": expired_lease_task_ids,
        "stale_nonterminal_task_ids": stale_nonterminal_task_ids,
        "takeover_task_ids": takeover_task_ids,
        "resumable_task_ids": resumable_task_ids,
        "lease_health": lease_health,
        "generated_at": _now_iso(),
    }


def build_task_recovery_queue(
    tasks: list[TaskRunRecord],
    *,
    include_monitor: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    clean_limit = max(1, int(limit or 100))
    items: list[dict[str, Any]] = []
    for task in tasks:
        health = task_lease_health(task)
        action = str(health.get("recommended_action") or "monitor")
        actionable = bool(
            health.get("can_takeover")
            or health.get("can_resume")
            or action
            in {
                "dispatch",
                "await_operator_approval",
                "takeover_for_approval",
                "approval_policy_denied",
                "capability_policy_denied",
            }
        )
        if not include_monitor and not actionable:
            continue
        items.append(
            {
                "task_id": task.task_id,
                "status": task.status.value,
                "kind": task.kind,
                "title": task.title,
                "owner_id": task.owner_id,
                "thread_id": task.thread_id,
                "workspace_path": task.workspace_path,
                "recommended_action": action,
                "priority": _task_recovery_priority(action, health),
                "can_takeover": bool(health.get("can_takeover")),
                "can_resume": bool(health.get("can_resume")),
                "has_checkpoint": bool(health.get("has_checkpoint")),
                "latest_checkpoint_id": health.get("recovery", {}).get("latest_checkpoint_id"),
                "resume_checkpoint_id": health.get("recovery", {}).get("resume_checkpoint_id"),
                "checkpoint_id": health.get("recovery", {}).get("checkpoint_id"),
                "operation": health.get("recovery", {}).get("operation"),
                "steps": health.get("recovery", {}).get("steps", []),
                "recovery_plan": health.get("recovery"),
                "lease_health": health,
                "updated_at": task.updated_at,
                "created_at": task.created_at,
            }
        )
    items.sort(
        key=lambda item: (
            int(item["priority"]),
            str(item.get("updated_at") or ""),
            str(item.get("task_id") or ""),
        ),
        reverse=True,
    )
    return {
        "schema": "echo.task_recovery_queue.v1",
        "total": len(items),
        "count": min(len(items), clean_limit),
        "limit": clean_limit,
        "items": items[:clean_limit],
        "generated_at": _now_iso(),
    }


def _task_recovery_priority(action: str, health: dict[str, Any]) -> int:
    priorities = {
        "takeover_and_resume": 100,
        "takeover_for_approval": 95,
        "resume_from_checkpoint": 90,
        "restart": 80,
        "resume_paused_task": 75,
        "takeover": 70,
        "dispatch": 60,
        "await_operator_approval": 50,
        "approval_policy_denied": 40,
        "capability_policy_denied": 40,
        "monitor": 10,
        "none": 0,
    }
    score = priorities.get(action, 20)
    if bool(health.get("can_takeover")):
        score += 5
    if bool(health.get("can_resume")):
        score += 3
    return score


def _task_recovery_operation(action: str) -> tuple[str, list[str]]:
    plans = {
        "takeover_and_resume": (
            "takeover_then_resume",
            ["takeover_task", "resume_from_checkpoint"],
        ),
        "takeover_for_approval": (
            "takeover_then_approval",
            ["takeover_task", "approval_decision"],
        ),
        "resume_from_checkpoint": ("resume_from_checkpoint", ["resume_from_checkpoint"]),
        "restart": ("restart_task", ["restart_task"]),
        "resume_paused_task": ("resume_paused_task", ["resume_task"]),
        "takeover": ("takeover_task", ["takeover_task"]),
        "dispatch": ("dispatch_task", ["dispatch_task"]),
        "await_operator_approval": ("approval_decision", ["approval_decision"]),
        "approval_policy_denied": ("review_policy", ["review_policy"]),
        "capability_policy_denied": ("review_policy", ["review_policy"]),
        "monitor": ("monitor", []),
        "none": ("none", []),
    }
    return plans.get(action, ("inspect_task", ["inspect_task"]))
