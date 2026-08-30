"""Echo task projection and bounded recovery over Agent TaskSupervisor.

Echo Agent remains the task lifecycle authority. Echo OS only presents a
bounded, device-owner view and joins system capability activity through the
stable contract ``capability intentId == Agent task_id``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from appliance.agent_api.tasks import (
    TaskLeaseConflict,
    resume_checkpoint_metadata,
    task_lease_health,
)
from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.security import ApplianceAuthenticator, resolve_authenticator

TASK_PROJECTION_SCHEMA = "echo.task_projection.v1"
_ACTIVE = {"pending", "running", "verifying", "repairing"}
_TERMINAL = {"cancelled", "disconnected", "failed", "completed"}


class TaskTakeoverRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reason: str | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        return text[:300] or None


class TaskResumeExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    reason: str | None = None
    request_id: str | None = Field(
        default=None,
        alias="requestId",
        min_length=8,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        return text[:300] or None


def _text(value: Any, *, limit: int = 512) -> str:
    return str(value or "").strip()[:limit]


def _optional_text(value: Any, *, limit: int = 512) -> str | None:
    text = _text(value, limit=limit)
    return text or None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, number))


def _task_progress(metadata: dict[str, Any]) -> float | None:
    for key in ("progressPercent", "progress_percent", "progress"):
        progress = _number(metadata.get(key))
        if progress is not None:
            return progress
    completed = _number(metadata.get("completed_steps"))
    total = _number(metadata.get("total_steps"))
    if completed is None or total is None or total <= 0:
        return None
    return round(max(0.0, min(100.0, completed / total * 100)), 1)


def _runtime_groups(task: Any) -> list[str]:
    groups = getattr(getattr(task, "capabilities", None), "groups", {})
    if not isinstance(groups, dict):
        return []
    return sorted(str(key) for key, enabled in groups.items() if enabled)


def _audit_activity(audit: ApplianceAudit | None) -> tuple[dict[str, list[dict]], dict]:
    if audit is None:
        return {}, {"available": False, "ok": None, "entriesChecked": 0}
    report = audit.verify()
    integrity = {"available": True, **report.to_dict()}
    if not report.ok:
        raise HTTPException(status_code=503, detail="task projection audit unavailable")

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in audit.recent(200):
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        intent_id = _optional_text(metadata.get("intentId"), limit=128)
        if intent_id is None:
            continue
        action = _text(payload.get("action"), limit=128)
        target = _text(payload.get("target"), limit=512)
        outcome = _text(payload.get("outcome"), limit=64)
        if action == "capability.decision":
            kind = "capability-decision"
            capability_id = target
        elif action == "approval":
            kind = "approval"
            capability_id = _optional_text(metadata.get("capabilityId"), limit=128)
        else:
            kind = "execution"
            capability_id = _optional_text(metadata.get("capabilityId"), limit=128)
        grouped[intent_id].append(
            {
                "id": f"appliance-audit:{entry.get('seq', '')}",
                "at": _optional_text(entry.get("ts"), limit=64),
                "kind": kind,
                "action": action,
                "capabilityId": capability_id,
                "target": target,
                "outcome": outcome,
                "reasonCode": _optional_text(metadata.get("reasonCode"), limit=128),
                "risk": _optional_text(metadata.get("risk"), limit=32),
            }
        )
    return dict(grouped), integrity


def _execution_recovery(task: Any, realtime_gateway: Any) -> dict[str, Any]:
    status = _text(getattr(task, "status", ""), limit=32)
    metadata = getattr(task, "metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    health = task_lease_health(task)
    requires_takeover = bool(health.get("can_takeover"))
    base = {
        "checkpointAvailable": False,
        "canStart": False,
        "requiresTakeover": requires_takeover,
        "checkpointId": None,
        "iteration": None,
        "phase": None,
        "reason": "当前任务没有可恢复的执行检查点",
    }
    if status in {"completed", "waiting_approval", "pending"}:
        base["reason"] = (
            "任务正在等待原审批" if status == "waiting_approval" else "当前状态不能恢复执行"
        )
        return base
    if (
        status in {"running", "verifying", "repairing"}
        and not bool(metadata.get("takeover"))
        and not requires_takeover
    ):
        base["reason"] = "任务仍由 Agent 正常执行"
        return base
    try:
        UUID(str(getattr(task, "task_id", "")))
    except (TypeError, ValueError):
        base["reason"] = "该任务不是可恢复的 ReAct 目标"
        return base

    runtime = getattr(realtime_gateway, "_runtime", None)
    if runtime is None:
        base["reason"] = "Agent 实时恢复服务不可用"
        return base
    checkpoint = resume_checkpoint_metadata(runtime, str(task.task_id))
    if checkpoint is None:
        return base
    checkpoint_id = checkpoint.get("checkpoint_id") or getattr(task, "latest_checkpoint_id", None)
    base.update(
        {
            "checkpointAvailable": True,
            "checkpointId": checkpoint_id,
            "iteration": checkpoint.get("iteration"),
            "phase": _optional_text(checkpoint.get("phase"), limit=80),
        }
    )
    if requires_takeover:
        base["reason"] = "检查点可用；请先接管失效的 Agent 租约"
        return base
    thread_id = _text(getattr(task, "thread_id", ""), limit=128)
    active_threads = getattr(realtime_gateway, "_active_turn_threads", set())
    if thread_id and thread_id in active_threads:
        base["reason"] = "恢复回合已经在原线程运行"
        return base
    resume_state = _text(metadata.get("resume_execution_state"), limit=32)
    if resume_state == "queued":
        base["reason"] = "恢复回合已经排队"
        return base
    if resume_state == "turn_started" and status != "paused":
        base["reason"] = "恢复回合已经启动"
        return base
    base["canStart"] = True
    base["reason"] = "检查点已由 Agent 验证，可以在原线程恢复执行"
    return base


def _project_task(
    task: Any,
    activity: list[dict[str, Any]],
    realtime_gateway: Any = None,
) -> dict[str, Any]:
    metadata = getattr(task, "metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    status = _text(getattr(task, "status", "pending"), limit=32) or "pending"
    health = task_lease_health(task)
    lease_state = _text(health.get("state"), limit=32) or "unknown"
    recovery_needed = lease_state in {"expired", "missing_lease"}
    display_status = "disconnected" if recovery_needed else status
    title = _text(getattr(task, "title", ""), limit=240)
    goal = _text(getattr(task, "goal", ""), limit=800)
    approval = None
    if status == "waiting_approval":
        approval = {
            "required": True,
            "tool": _optional_text(metadata.get("approval_tool_name"), limit=128),
            "action": _optional_text(metadata.get("approval_action"), limit=128),
            "reason": _optional_text(
                metadata.get("approval_reason") or getattr(task, "terminal_reason", ""),
                limit=300,
            ),
        }
    capability_decisions = [item for item in activity if item["kind"] == "capability-decision"]
    return {
        "id": _text(getattr(task, "task_id", ""), limit=128),
        "source": "echo-agent",
        "threadId": _optional_text(getattr(task, "thread_id", None), limit=128),
        "parentTaskId": _optional_text(getattr(task, "parent_task_id", None), limit=128),
        "kind": _text(getattr(task, "kind", "task"), limit=64) or "task",
        "title": title or goal or "未命名任务",
        "summary": goal or _optional_text(getattr(task, "terminal_reason", ""), limit=800),
        "status": status,
        "displayStatus": display_status,
        "leaseHealth": {
            "state": lease_state,
            "recoveryNeeded": recovery_needed,
            "canTakeover": bool(health.get("can_takeover")),
            "canResume": bool(health.get("can_resume")),
            "recommendedAction": _optional_text(health.get("recommended_action"), limit=64),
            "reason": _optional_text(health.get("recovery_reason"), limit=300),
        },
        "progressPercent": _task_progress(metadata),
        "mode": _optional_text(getattr(task, "mode", None), limit=64),
        "agentId": _optional_text(metadata.get("agent_id"), limit=128),
        "runtimeCapabilityGroups": _runtime_groups(task),
        "capabilityDecisions": capability_decisions[-20:],
        "approval": approval,
        "activity": activity[-30:],
        "startedAt": _optional_text(getattr(task, "started_at", None), limit=64),
        "updatedAt": _optional_text(getattr(task, "updated_at", None), limit=64),
        "completedAt": _optional_text(getattr(task, "completed_at", None), limit=64),
        "terminalReason": _optional_text(getattr(task, "terminal_reason", None), limit=500),
        "latestCheckpointId": getattr(task, "latest_checkpoint_id", None),
        "executionRecovery": _execution_recovery(task, realtime_gateway),
    }


def _status_rank(task: dict[str, Any]) -> int:
    status = str(task.get("status") or "")
    if status == "waiting_approval":
        return 0
    if bool((task.get("leaseHealth") or {}).get("recoveryNeeded")):
        return 1
    if status in _ACTIVE:
        return 2
    if status == "paused":
        return 3
    return 4


def _counts(tasks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(tasks),
        "active": sum(
            task["status"] in _ACTIVE and not bool(task["leaseHealth"]["recoveryNeeded"])
            for task in tasks
        ),
        "waitingApproval": sum(task["status"] == "waiting_approval" for task in tasks),
        "paused": sum(task["status"] == "paused" for task in tasks),
        "recoveryNeeded": sum(bool(task["leaseHealth"]["recoveryNeeded"]) for task in tasks),
        "failed": sum(task["status"] in {"failed", "disconnected"} for task in tasks),
        "completed": sum(task["status"] == "completed" for task in tasks),
    }


def _require_task(supervisor: Any, task_id: str) -> Any:
    if supervisor is None or getattr(supervisor, "store", None) is None:
        raise HTTPException(status_code=503, detail="task supervisor unavailable")
    clean_task_id = _text(task_id, limit=128)
    if not clean_task_id or clean_task_id != task_id:
        raise HTTPException(status_code=404, detail="task not found")
    task = supervisor.store.get(clean_task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


def _record_task_action(
    audit: ApplianceAudit | None,
    *,
    actor: str,
    task_id: str,
    action: str,
    outcome: str,
    metadata: dict[str, Any],
) -> None:
    if audit is None:
        return
    try:
        audit.record(
            actor=actor,
            action=action,
            target=task_id,
            outcome=outcome,
            metadata={"intentId": task_id, **metadata},
        )
    except AuditIntegrityError as exc:
        raise HTTPException(
            status_code=503,
            detail="task action audit unavailable",
        ) from exc


def create_task_projection_router(
    *,
    supervisor: Any = None,
    realtime_gateway: Any = None,
    audit: ApplianceAudit | None = None,
    jwt_secret: str | None = None,
    authenticator: ApplianceAuthenticator | None = None,
) -> APIRouter:
    require_auth = resolve_authenticator(
        jwt_secret=jwt_secret, authenticator=authenticator
    ).dependency()
    router = APIRouter(
        prefix="/api/appliance/tasks",
        tags=["appliance", "tasks"],
        dependencies=[Depends(require_auth)],
    )

    @router.get("")
    def list_tasks(
        status: str | None = Query(default=None, max_length=32),
        limit: int = Query(default=100, ge=1, le=200),
    ) -> dict[str, Any]:
        activity_by_task, audit_integrity = _audit_activity(audit)
        if supervisor is None or getattr(supervisor, "store", None) is None:
            return {
                "schema": TASK_PROJECTION_SCHEMA,
                "available": False,
                "generatedAt": datetime.now(UTC).isoformat(),
                "counts": _counts([]),
                "auditIntegrity": audit_integrity,
                "tasks": [],
            }

        records = supervisor.store.list(limit=1_000, include_unowned=True)
        projected = [
            _project_task(
                record,
                activity_by_task.get(str(record.task_id), []),
                realtime_gateway,
            )
            for record in records
        ]
        if status:
            projected = [task for task in projected if task["status"] == status]
        projected.sort(
            key=lambda task: str(task.get("updatedAt") or task.get("startedAt") or ""),
            reverse=True,
        )
        projected.sort(key=_status_rank)
        projected = projected[:limit]
        return {
            "schema": TASK_PROJECTION_SCHEMA,
            "available": True,
            "generatedAt": datetime.now(UTC).isoformat(),
            "counts": _counts(projected),
            "auditIntegrity": audit_integrity,
            "tasks": projected,
        }

    @router.get("/{task_id}")
    def task_detail(task_id: str) -> dict[str, Any]:
        task = _require_task(supervisor, task_id)
        activity_by_task, audit_integrity = _audit_activity(audit)
        return {
            "schema": "echo.task_projection.detail.v1",
            "generatedAt": datetime.now(UTC).isoformat(),
            "auditIntegrity": audit_integrity,
            "task": _project_task(
                task,
                activity_by_task.get(str(task.task_id), []),
                realtime_gateway,
            ),
        }

    @router.post("/{task_id}/takeover")
    def takeover_task(
        task_id: str,
        request: Request,
        body: TaskTakeoverRequest | None = None,
    ) -> dict[str, Any]:
        task = _require_task(supervisor, task_id)
        health = task_lease_health(task)
        if not bool(health.get("can_takeover")):
            raise HTTPException(
                status_code=409,
                detail="task lease is not available for takeover",
            )

        actor = str(getattr(request.state, "appliance_actor", "local:development")).strip()
        reason = (
            body.reason if body is not None and body.reason else "Echo Task Space recovery takeover"
        )
        metadata = {
            "previousStatus": _text(getattr(task, "status", ""), limit=32),
            "previousLeaseState": _text(health.get("state"), limit=32),
            "reason": reason,
        }
        _record_task_action(
            audit,
            actor=actor,
            task_id=task_id,
            action="task.takeover",
            outcome="attempted",
            metadata=metadata,
        )
        try:
            updated = supervisor.takeover_task(
                task_id,
                by=actor,
                reason=reason,
            )
        except TaskLeaseConflict as exc:
            _record_task_action(
                audit,
                actor=actor,
                task_id=task_id,
                action="task.takeover",
                outcome="conflict",
                metadata={**metadata, "error": "lease_conflict"},
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            _record_task_action(
                audit,
                actor=actor,
                task_id=task_id,
                action="task.takeover",
                outcome="denied",
                metadata={**metadata, "error": "invalid_task_state"},
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

        _record_task_action(
            audit,
            actor=actor,
            task_id=task_id,
            action="task.takeover",
            outcome="succeeded",
            metadata={
                **metadata,
                "nextStatus": _text(getattr(updated, "status", ""), limit=32),
            },
        )
        activity_by_task, audit_integrity = _audit_activity(audit)
        return {
            "schema": "echo.task_action.v1",
            "action": "takeover",
            "requiresWorkspaceResume": True,
            "auditIntegrity": audit_integrity,
            "task": _project_task(
                updated,
                activity_by_task.get(str(updated.task_id), []),
                realtime_gateway,
            ),
        }

    @router.post("/{task_id}/resume-execution")
    async def resume_task_execution(
        task_id: str,
        request: Request,
        body: TaskResumeExecutionRequest | None = None,
    ) -> dict[str, Any]:
        task = _require_task(supervisor, task_id)
        recovery = _execution_recovery(task, realtime_gateway)
        if not bool(recovery.get("canStart")):
            raise HTTPException(
                status_code=409,
                detail=str(recovery.get("reason") or "task execution cannot be resumed"),
            )

        actor = str(getattr(request.state, "appliance_actor", "local:development")).strip()
        body = body or TaskResumeExecutionRequest()
        reason = body.reason or "设备管理员从 Echo 任务空间恢复检查点执行"
        request_id = body.request_id
        action_metadata = {
            "previousStatus": _text(getattr(task, "status", ""), limit=32),
            "checkpointId": recovery.get("checkpointId"),
            "threadId": _optional_text(getattr(task, "thread_id", None), limit=128),
            "requestId": request_id,
            "reason": reason,
        }
        _record_task_action(
            audit,
            actor=actor,
            task_id=task_id,
            action="task.resume_execution",
            outcome="attempted",
            metadata=action_metadata,
        )

        # Agent and Echo routes live in the same ASGI process in both the NAS
        # and native images. Re-enter the public Agent action so its auth,
        # owner isolation and lifecycle contract remain the only authority.
        from appliance.security import request_token

        token = request_token(request)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        payload: dict[str, Any] = {"reason": reason}
        if request_id:
            payload["requestId"] = request_id
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=request.app),
                base_url="http://echo-agent.internal",
                timeout=10.0,
            ) as client:
                response = await client.post(
                    f"/api/task-runs/{task_id}/resume-execution",
                    json=payload,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            _record_task_action(
                audit,
                actor=actor,
                task_id=task_id,
                action="task.resume_execution",
                outcome="failed",
                metadata={**action_metadata, "error": "agent_unreachable"},
            )
            raise HTTPException(
                status_code=503,
                detail="Agent recovery endpoint unavailable",
            ) from exc

        try:
            agent_result = response.json()
        except ValueError:
            agent_result = {}
        if response.status_code >= 400:
            detail = (
                _text(agent_result.get("detail"), limit=300)
                if isinstance(agent_result, dict)
                else ""
            )
            _record_task_action(
                audit,
                actor=actor,
                task_id=task_id,
                action="task.resume_execution",
                outcome="rejected" if response.status_code == 409 else "failed",
                metadata={
                    **action_metadata,
                    "agentStatus": response.status_code,
                    "error": detail or "agent_rejected",
                },
            )
            if response.status_code == 401:
                raise HTTPException(status_code=401, detail="authentication required")
            if response.status_code == 409:
                raise HTTPException(
                    status_code=409,
                    detail=detail or "Agent task state changed",
                )
            raise HTTPException(
                status_code=503,
                detail=detail or "Agent recovery endpoint unavailable",
            )
        if not isinstance(agent_result, dict) or agent_result.get("schema") != (
            "echo.task_run_resume_execution.v1"
        ):
            raise HTTPException(status_code=503, detail="Agent returned an incompatible response")

        _record_task_action(
            audit,
            actor=actor,
            task_id=task_id,
            action="task.resume_execution",
            outcome="accepted",
            metadata={
                **action_metadata,
                "turnId": _optional_text(agent_result.get("turn_id"), limit=128),
                "state": _text(agent_result.get("state"), limit=32),
            },
        )
        updated = _require_task(supervisor, task_id)
        activity_by_task, audit_integrity = _audit_activity(audit)
        thread_id = _optional_text(getattr(updated, "thread_id", None), limit=128)
        return {
            "schema": "echo.task_action.v1",
            "action": "resume_execution",
            "state": _text(agent_result.get("state"), limit=32),
            "turnId": _optional_text(agent_result.get("turn_id"), limit=128),
            "requestId": _optional_text(agent_result.get("request_id"), limit=96),
            "threadPath": (f"/workspace/realtime/{thread_id}" if thread_id is not None else None),
            "auditIntegrity": audit_integrity,
            "task": _project_task(
                updated,
                activity_by_task.get(str(updated.task_id), []),
                realtime_gateway,
            ),
        }

    return router


__all__ = [
    "TASK_PROJECTION_SCHEMA",
    "TaskResumeExecutionRequest",
    "TaskTakeoverRequest",
    "create_task_projection_router",
]
