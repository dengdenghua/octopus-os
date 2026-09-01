from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from fastapi import APIRouter, HTTPException, Query, Request

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Query = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]

from runtime.platform.process.paths import app_paths
from runtime.platform.process.task_supervisor import (
    LostTaskLease,
    TaskLeaseConflict,
    TaskRunStatus,
    TaskSupervisor,
    TaskSupervisorStore,
    build_task_runs_overview,
    task_lease_health,
)
from runtime.sensing._fastapi_guard import require_fastapi

_logger = logging.getLogger(__name__)
_RESUME_START_TIMEOUT_SECONDS = 5.0
_RESUME_QUEUE_STALE_SECONDS = 300.0
_DEFAULT_RESUME_ITERATION_GRANT = 15
_DEFAULT_RESUME_TOKEN_GRANT = 100_000


class TaskApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    approved: bool
    reason: str | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None


class TaskTakeoverRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reason: str | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None


class TaskResumeExecutionRequest(BaseModel):
    """One explicit operator request to continue a checkpointed objective."""

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
        return text or None


class _RecoveryTurnConnection:
    """Minimal authenticated owner for a server-resident recovery turn.

    It captures the durable ``turn/started`` coordinate, forwards that first
    event to any real clients already watching the thread, and then marks
    itself closed.  The gateway's detached emitter consequently routes all
    later output and every approval request only to real live clients; this
    synthetic owner can never approve an action.
    """

    def __init__(
        self,
        *,
        gateway: Any,
        thread_id: str,
        actor_id: str | None,
        tenant_id: str | None,
        started: asyncio.Future[dict[str, Any]],
    ) -> None:
        self._gateway = gateway
        self._thread_id = thread_id
        self.actor_id = actor_id
        self.tenant_id = tenant_id
        self.watched_threads: set[str] = set()
        self._started = started
        self._closed = False

    async def notify(self, method: Any, params: dict[str, Any]) -> None:
        method_value = getattr(method, "value", method)
        if self._closed or method_value != "turn/started":
            return
        payload = dict(params)
        if not self._started.done():
            self._started.set_result(payload)

        # The detached emitter targets its live owner exclusively. Mirror the
        # one startup event to already-connected watchers before detaching so
        # the workbench does not miss the turn coordinate.
        can_access = getattr(self._gateway, "_connection_can_access_thread", None)
        targets = [
            conn
            for conn in list(getattr(self._gateway, "_connections", ()))
            if self._thread_id in getattr(conn, "watched_threads", ())
            and not getattr(conn, "_closed", False)
            and (not callable(can_access) or can_access(self._thread_id, conn))
        ]
        if targets:
            await asyncio.gather(
                *(self._notify_safely(conn, method, payload) for conn in targets),
            )
        self._closed = True

    @staticmethod
    async def _notify_safely(target: Any, method: Any, params: dict[str, Any]) -> None:
        with suppress(Exception):
            await target.notify(method, params)

    async def request_approval(self, *_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionError("recovery trigger is not an approval channel")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _is_recent_iso(value: Any, *, max_age_seconds: float) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()
        return 0 <= age <= max_age_seconds
    except (TypeError, ValueError):
        return False


def _resume_user_item_id(task_id: str, checkpoint_id: Any, request_id: str) -> str:
    coordinate = f"{task_id}\x00{checkpoint_id}\x00{request_id}".encode()
    return f"itm_resume_{hashlib.sha256(coordinate).hexdigest()[:32]}"


def _task_started_turn_id(payload: dict[str, Any]) -> str | None:
    turn = payload.get("turn")
    if not isinstance(turn, dict):
        return None
    value = str(turn.get("id") or "").strip()
    return value or None


def _track_resume_job(app: Any, job: asyncio.Task[Any]) -> None:
    jobs = getattr(app.state, "task_run_resume_jobs", None)
    if not isinstance(jobs, set):
        jobs = set()
        app.state.task_run_resume_jobs = jobs
    jobs.add(job)

    def _finished(done: asyncio.Task[Any]) -> None:
        jobs.discard(done)
        if done.cancelled():
            return
        with suppress(Exception):
            error = done.exception()
            if error is not None:
                _logger.warning(
                    "task recovery turn failed after HTTP handoff: %s",
                    error,
                )

    job.add_done_callback(_finished)


def _default_supervisor() -> TaskSupervisor:
    return TaskSupervisor(TaskSupervisorStore(app_paths().task_runs_path))


def create_task_runs_router(
    *,
    supervisor: TaskSupervisor | None = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi(__name__)

    router = APIRouter(tags=["task-runs"])

    def _store() -> TaskSupervisorStore:
        return (supervisor or _default_supervisor()).store

    def _auth(request: Request) -> str | None:
        if require_auth and identity_store is None:
            raise HTTPException(401, "auth required")
        from runtime.sensing.gateway.openai_gateway_router import _resolve_actor

        return _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _supervisor() -> TaskSupervisor:
        return supervisor or _default_supervisor()

    def _tenant_id(actor: str | None) -> str | None:
        if actor is None or identity_store is None:
            return None
        identity = identity_store.get(actor)
        metadata = getattr(identity, "metadata", None) or {}
        return str(metadata.get("tenant_id") or f"legacy:{actor}")

    def _is_admin(actor: str | None) -> bool:
        if actor is None or identity_store is None:
            return False
        identity = identity_store.get(actor)
        roles = getattr(identity, "roles", ()) or ()
        return "admin" in {str(role).strip().lower() for role in roles}

    def _require_task_access(task: Any, actor: str | None) -> None:
        if not require_auth:
            return
        owner = str(task.owner_id or "").strip()
        if not owner:
            if not _is_admin(actor):
                raise HTTPException(404, "task run not found")
            return
        if owner != actor:
            raise HTTPException(404, "task run not found")

    @router.get("/api/task-runs")
    def api_task_runs(
        request: Request,
        status: str | None = Query(default=None),
        kind: str | None = Query(default=None),
        owner_id: str | None = Query(default=None),
        thread_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        actor = _auth(request)
        effective_owner = actor if require_auth else owner_id
        page = _store().list_page(
            status=status,
            kind=kind,
            owner_id=effective_owner,
            thread_id=thread_id,
            limit=limit,
            offset=offset,
            include_unowned=not require_auth or _is_admin(actor),
        )
        tasks = page["items"]
        return {
            "schema": "echo.task_runs.v1",
            "tasks": [task.model_dump(mode="json") for task in tasks],
            "items": [_task_run_payload(task) for task in tasks],
            "total": page["total"],
            "count": len(tasks),
            "limit": page["limit"],
            "offset": page["offset"],
            "filters": {
                "status": status,
                "kind": kind,
                "owner_id": effective_owner,
                "thread_id": thread_id,
            },
        }

    @router.get("/api/task-runs/overview")
    def api_task_runs_overview(
        request: Request,
        owner_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        actor = _auth(request)
        effective_owner = actor if require_auth else owner_id
        if effective_owner:
            tasks = _store().list(
                owner_id=effective_owner,
                limit=1_000_000,
                include_unowned=not require_auth or _is_admin(actor),
            )
            overview = build_task_runs_overview(tasks)
        else:
            overview = _store().overview()
        return {
            **overview,
            "filters": {
                "owner_id": effective_owner,
            },
        }

    @router.get("/api/task-runs/recovery-queue")
    def api_task_runs_recovery_queue(
        request: Request,
        status: str | None = Query(default=None),
        kind: str | None = Query(default=None),
        owner_id: str | None = Query(default=None),
        thread_id: str | None = Query(default=None),
        include_monitor: bool = Query(default=False),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        actor = _auth(request)
        effective_owner = actor if require_auth else owner_id
        queue = _store().recovery_queue(
            status=status,
            kind=kind,
            owner_id=effective_owner,
            thread_id=thread_id,
            include_monitor=include_monitor,
            limit=limit,
            include_unowned=not require_auth or _is_admin(actor),
        )
        return {
            **queue,
            "filters": {
                "status": status,
                "kind": kind,
                "owner_id": effective_owner,
                "thread_id": thread_id,
                "include_monitor": include_monitor,
            },
        }

    @router.get("/api/task-runs/{task_id}")
    def api_task_run(task_id: str, request: Request) -> dict[str, Any]:
        actor = _auth(request)
        task = _store().get(task_id)
        if task is None:
            raise HTTPException(404, "task run not found")
        _require_task_access(task, actor)
        return {
            "schema": "echo.task_run.v1",
            "task_run": task.model_dump(mode="json"),
            "lease_health": task_lease_health(task),
        }

    @router.post("/api/task-runs/{task_id}/approval-decision")
    def api_task_run_approval_decision(
        task_id: str,
        body: TaskApprovalDecisionRequest,
        request: Request,
    ) -> dict[str, Any]:
        actor = _auth(request)
        task = _store().get(task_id)
        if task is None:
            raise HTTPException(404, "task run not found")
        _require_task_access(task, actor)
        try:
            updated = _supervisor().record_approval_decision(
                task_id,
                approved=body.approved,
                decided_by=actor,
                reason=body.reason or "",
            )
        except LostTaskLease as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "task run not found") from exc
        return {
            "schema": "echo.task_run_approval_decision.v1",
            "task_run": updated.model_dump(mode="json"),
            "lease_health": task_lease_health(updated),
        }

    @router.post("/api/task-runs/{task_id}/takeover")
    def api_task_run_takeover(
        task_id: str,
        request: Request,
        body: TaskTakeoverRequest | None = None,
    ) -> dict[str, Any]:
        actor = _auth(request)
        task = _store().get(task_id)
        if task is None:
            raise HTTPException(404, "task run not found")
        _require_task_access(task, actor)
        try:
            updated = _supervisor().takeover_task(
                task_id,
                by=actor,
                reason=(body.reason if body is not None else None) or "",
            )
        except TaskLeaseConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "task run not found") from exc
        return {
            "schema": "echo.task_run_takeover.v1",
            "task_run": updated.model_dump(mode="json"),
            "lease_health": task_lease_health(updated),
        }

    @router.post("/api/task-runs/{task_id}/resume-execution", status_code=202)
    async def api_task_run_resume_execution(
        task_id: str,
        request: Request,
        body: TaskResumeExecutionRequest | None = None,
    ) -> dict[str, Any]:
        """Continue one durable ReAct checkpoint on its original thread.

        This is deliberately an adapter into the existing realtime lifecycle,
        not a second executor.  A normal, server-resident ``turn/start`` with
        the strict input ``继续`` consumes the selected PauseController record;
        thread claims, task identity, approvals, interruption, checkpoint
        loading and the TaskSupervisor remain authoritative in their existing
        Agent code paths.
        """

        actor = _auth(request)
        task = _store().get(task_id)
        if task is None:
            raise HTTPException(404, "task run not found")
        _require_task_access(task, actor)

        try:
            UUID(task_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(409, "task id is not a resumable ReAct objective") from exc

        if task.status == TaskRunStatus.COMPLETED:
            raise HTTPException(409, "completed task cannot be resumed")
        if task.status == TaskRunStatus.WAITING_APPROVAL:
            raise HTTPException(
                409,
                "task is waiting for its original approval decision",
            )
        if task.status == TaskRunStatus.PENDING:
            raise HTTPException(409, "task has not started and has no execution to resume")

        health = task_lease_health(task)
        if bool(health.get("can_takeover")):
            raise HTTPException(409, "task lease must be taken over before execution can resume")
        if (
            task.status
            not in {TaskRunStatus.CANCELLED, TaskRunStatus.DISCONNECTED, TaskRunStatus.FAILED}
            and task.lease is not None
            and task.lease.holder_id != _supervisor().holder_id
        ):
            raise HTTPException(409, "task is actively held by another Agent worker")

        thread_id = str(task.thread_id or "").strip()
        if not thread_id:
            raise HTTPException(409, "task has no original thread to resume")
        gateway = getattr(request.app.state, "realtime_gateway", None)
        runtime = getattr(gateway, "_runtime", None)
        invoke_turn_start = getattr(gateway, "_invoke_turn_start", None)
        if runtime is None or not callable(invoke_turn_start):
            raise HTTPException(503, "Agent realtime recovery is unavailable")

        resume_state = str(task.metadata.get("resume_execution_state") or "")
        active_threads = getattr(gateway, "_active_turn_threads", set())
        has_active_turn = thread_id in active_threads
        queued_recently = _is_recent_iso(
            task.metadata.get("resume_execution_requested_at"),
            max_age_seconds=_RESUME_QUEUE_STALE_SECONDS,
        )
        if resume_state == "queued" and (has_active_turn or queued_recently):
            raise HTTPException(409, "task recovery is already queued or running")
        if resume_state == "turn_started" and (
            has_active_turn or task.status != TaskRunStatus.PAUSED
        ):
            raise HTTPException(409, "task recovery is already queued or running")
        if task.status in {
            TaskRunStatus.RUNNING,
            TaskRunStatus.VERIFYING,
            TaskRunStatus.REPAIRING,
        } and not bool(task.metadata.get("takeover")):
            raise HTTPException(409, "task is already running")

        from runtime.sensing.gateway._realtime_turn_lifecycle_resume import (
            _resume_checkpoint_metadata,
        )

        checkpoint = _resume_checkpoint_metadata(runtime, task_id)
        if checkpoint is None:
            raise HTTPException(409, "no durable ReAct checkpoint is available for this task")

        body = body or TaskResumeExecutionRequest()
        recovery_request_id = body.request_id or uuid4().hex
        checkpoint_id = checkpoint.get("checkpoint_id") or task.latest_checkpoint_id or 0
        user_item_id = _resume_user_item_id(task_id, checkpoint_id, recovery_request_id)
        reason = body.reason or "operator requested checkpoint recovery"

        from runtime.core.cerebrum.pause_control import get_pause_controller

        controller = getattr(request.app.state, "pause_controller", None)
        if controller is None:
            controller = get_pause_controller()
        prior_pause = controller.get_request(task_id)
        prior_reason = str(getattr(prior_pause, "reason", "") or "")

        # A just-taken-over task was provisionally RUNNING only because the
        # new worker owns its lease. Keep it truthfully PAUSED until the ReAct
        # loop emits react_started and starts the same objective id again.
        terminal_recovery_statuses = {
            TaskRunStatus.CANCELLED,
            TaskRunStatus.DISCONNECTED,
            TaskRunStatus.FAILED,
        }
        queued_metadata = {
            "resume_execution_state": "queued",
            "resume_execution_request_id": recovery_request_id,
            "resume_execution_requested_at": _now_iso(),
            "resume_execution_requested_by": actor,
        }
        if task.status not in terminal_recovery_statuses:
            try:
                task = _supervisor().transition(
                    task_id,
                    TaskRunStatus.PAUSED,
                    reason=reason,
                    checkpoint_id=task.latest_checkpoint_id or checkpoint_id,
                    metadata_patch=queued_metadata,
                )
            except LostTaskLease as exc:
                raise HTTPException(409, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            except KeyError as exc:
                raise HTTPException(404, "task run not found") from exc
        else:
            # Terminal recovery keeps the old failure/cancellation visible
            # until react_started restarts the same objective. Metadata still
            # records the in-flight handoff so concurrent clicks are deduped.
            try:
                task = _supervisor().transition(
                    task_id,
                    task.status,
                    checkpoint_id=task.latest_checkpoint_id or checkpoint_id,
                    metadata_patch=queued_metadata,
                )
            except KeyError as exc:
                raise HTTPException(404, "task run not found") from exc

        # Register the exact objective only after TaskSupervisor accepted the
        # state transition. A lease race must not leave a hidden pending
        # resume that a later ordinary "继续" message could consume.
        controller.request_pause(
            task_id=task_id,
            reason="external",
            requested_by=f"recovery:{actor or 'local'}",
            note=reason,
            thread_id=thread_id,
            agent_id=str(task.metadata.get("agent_id") or ""),
        )
        if not controller.is_paused(task_id):
            controller.mark_paused(task_id)
        controller.set_pending_resume(thread_id, task_id)
        if prior_reason == "budget_near_limit":
            controller.set_grant(task_id, extra_tokens=_DEFAULT_RESUME_TOKEN_GRANT)
        else:
            controller.set_grant(task_id, extra_iterations=_DEFAULT_RESUME_ITERATION_GRANT)

        def _record_trigger_state(
            state: str,
            *,
            turn_id: str | None = None,
            error: str | None = None,
        ) -> None:
            current = _store().get(task_id)
            if current is None:
                return
            metadata_patch: dict[str, Any] = {
                "resume_execution_state": state,
                "resume_execution_turn_id": turn_id,
                "resume_execution_user_item_id": user_item_id,
            }
            if error:
                metadata_patch["resume_execution_error"] = error[:240]
                metadata_patch["resume_execution_failed_at"] = _now_iso()
            if current.status in terminal_recovery_statuses:
                with suppress(KeyError, ValueError):
                    _supervisor().transition(
                        task_id,
                        current.status,
                        metadata_patch=metadata_patch,
                    )
                return
            if (
                current.status != TaskRunStatus.PAUSED
                or current.lease is None
                or current.lease.holder_id != _supervisor().holder_id
            ):
                return
            with suppress(LostTaskLease, KeyError, ValueError):
                _supervisor().transition(
                    task_id,
                    TaskRunStatus.PAUSED,
                    metadata_patch=metadata_patch,
                )

        loop = asyncio.get_running_loop()
        started: asyncio.Future[dict[str, Any]] = loop.create_future()
        connection = _RecoveryTurnConnection(
            gateway=gateway,
            thread_id=thread_id,
            actor_id=actor,
            tenant_id=_tenant_id(actor),
            started=started,
        )
        params = {
            "threadId": thread_id,
            "userItemId": user_item_id,
            "input": [{"type": "text", "text": "继续"}],
            "approvalPolicy": "on-request",
            "metadata": {
                "recoveryRequest": {
                    "schema": "echo.task_resume_execution.v1",
                    "taskId": task_id,
                    "checkpointId": checkpoint_id,
                    "requestId": recovery_request_id,
                    "requestedBy": actor,
                }
            },
        }

        def _detach_trigger() -> None:
            connection._closed = True
            if thread_id in connection.watched_threads:
                with suppress(Exception):
                    gateway._unwatch_thread(thread_id)
                connection.watched_threads.discard(thread_id)

        job = asyncio.create_task(
            invoke_turn_start(params, connection),
            name=f"task-resume:{task_id}",
        )

        def _trigger_finished(done: asyncio.Task[Any]) -> None:
            _detach_trigger()
            if done.cancelled():
                _record_trigger_state("schedule_failed", error="recovery trigger cancelled")
                return
            with suppress(Exception):
                failure = done.exception()
                if failure is not None:
                    _record_trigger_state("schedule_failed", error=str(failure))

        job.add_done_callback(_trigger_finished)
        _track_resume_job(request.app, job)

        done, _pending = await asyncio.wait(
            {job, started},
            timeout=_RESUME_START_TIMEOUT_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        turn_id: str | None = None
        state = "queued"
        replayed = False
        if started in done:
            turn_id = _task_started_turn_id(started.result())
            state = "turn_started"
        elif job in done:
            try:
                result = job.result()
            except Exception as exc:  # noqa: BLE001
                _detach_trigger()
                _record_trigger_state("schedule_failed", error=str(exc))
                from runtime.protocol import JsonRpcErrorCode
                from runtime.sensing.gateway._realtime_gateway_types import _RpcError

                if isinstance(exc, _RpcError):
                    if exc.code in {
                        JsonRpcErrorCode.SERVER_BUSY,
                        JsonRpcErrorCode.INTERNAL_ERROR,
                    }:
                        raise HTTPException(
                            503, "Agent could not schedule the recovery turn"
                        ) from exc
                    if exc.code == JsonRpcErrorCode.THREAD_NOT_FOUND:
                        raise HTTPException(409, "the original task thread is unavailable") from exc
                    raise HTTPException(409, exc.message) from exc
                raise HTTPException(503, "Agent could not schedule the recovery turn") from exc
            turn = result.get("turn") if isinstance(result, dict) else None
            turn_id = str(turn.get("id") or "").strip() or None if isinstance(turn, dict) else None
            state = "replayed"
            replayed = True

        _detach_trigger()
        _record_trigger_state(state, turn_id=turn_id)
        task = _store().get(task_id) or task

        return {
            "schema": "echo.task_run_resume_execution.v1",
            "accepted": True,
            "state": state,
            "replayed": replayed,
            "task_id": task_id,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "checkpoint": checkpoint,
            "request_id": recovery_request_id,
            "user_item_id": user_item_id,
            "task_run": task.model_dump(mode="json"),
            "lease_health": task_lease_health(task),
        }

    return router


def _task_run_payload(task: Any) -> dict[str, Any]:
    return {
        "task_run": task.model_dump(mode="json"),
        "lease_health": task_lease_health(task),
    }


__all__ = [
    "TaskApprovalDecisionRequest",
    "TaskResumeExecutionRequest",
    "TaskTakeoverRequest",
    "create_task_runs_router",
]
