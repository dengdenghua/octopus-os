from __future__ import annotations

from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Query, Request

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Query = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]

from runtime.execution.loops.models import (
    CancelLoopRunRequest,
    CreateLoopRunRequest,
    LoopRun,
    LoopRunListResponse,
    LoopRunRuntimeStateResponse,
    LoopRunsOverviewResponse,
    LoopRunStatus,
    RestartLoopRunRequest,
)
from runtime.execution.loops.recovery import (
    build_loop_run_checkpoint,
    build_loop_run_resume_proposal,
)
from runtime.execution.loops.replay import (
    build_loop_run_replay,
    build_loop_run_replay_case,
    evaluate_loop_run_replay_case,
)
from runtime.execution.loops.store import LoopRunStore
from runtime.platform.process.task_supervisor import TaskSupervisor, task_lease_health
from runtime.safety.auth.scope import scope_from_request
from runtime.sensing._fastapi_guard import require_fastapi


def create_loop_router(
    *,
    store: LoopRunStore,
    controller: Any = None,
    dispatcher: Any = None,
    task_supervisor: TaskSupervisor | None = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi(__name__)

    router = APIRouter(tags=["loops"])

    def _task_record(run_id: str) -> Any:
        if task_supervisor is None:
            return None
        return task_supervisor.store.get(run_id)

    def _task_health_payload(run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        task = _task_record(run_id)
        if task is None:
            return {}, {}
        return task.model_dump(mode="json"), task_lease_health(task)

    def _recovery_audit(run: LoopRun) -> dict[str, Any]:
        checkpoint: dict[str, Any] = {}
        replay: dict[str, Any] = {}
        try:
            raw_checkpoint = build_loop_run_checkpoint(run)
            checkpoint = {
                "id": raw_checkpoint.get("id"),
                "iteration": raw_checkpoint.get("iteration"),
                "timestamp": raw_checkpoint.get("ts"),
                "summary": raw_checkpoint.get("summary"),
                "available": bool(raw_checkpoint.get("id")),
            }
        except Exception as exc:  # noqa: BLE001
            checkpoint = {"available": False, "error": str(exc)}
        try:
            raw_replay = build_loop_run_replay(run)
            replay = {
                "case_id": raw_replay.get("case_id"),
                "fingerprint": raw_replay.get("fingerprint"),
                "replayable": bool(raw_replay.get("replayable")),
                "step_count": int(raw_replay.get("step_count") or 0),
            }
        except Exception as exc:  # noqa: BLE001
            replay = {"replayable": False, "step_count": 0, "error": str(exc)}
        review = run.last_review if isinstance(run.last_review, dict) else {}
        raw_resume = review.get("resume")
        resume = raw_resume if isinstance(raw_resume, dict) else {}
        raw_review_replay = review.get("replay")
        review_replay = raw_review_replay if isinstance(raw_review_replay, dict) else {}
        resume_available = run.status in {
            LoopRunStatus.FAILED,
            LoopRunStatus.CANCELLED,
            LoopRunStatus.INTERRUPTED,
        }
        resume_checkpoint_id = checkpoint.get("id")
        if isinstance(resume, dict):
            raw_latest = resume.get("latest_checkpoint")
            latest = raw_latest if isinstance(raw_latest, dict) else {}
            resume_available = bool(resume.get("available")) or resume_available
            resume_checkpoint_id = latest.get("id") or resume_checkpoint_id
        resumed_from_available = bool(run.parent_run_id or run.resume_checkpoint_id)
        return {
            "schema": "echo.loop_recovery_audit.v1",
            "checkpoint": checkpoint,
            "resume": {
                "available": bool(resume_available),
                "latest_checkpoint_id": resume_checkpoint_id,
                "source": resume.get("source") if isinstance(resume, dict) else None,
            },
            "review": {
                "available": bool(review),
                "score": review.get("score") if isinstance(review, dict) else None,
                "status": review.get("status") if isinstance(review, dict) else None,
                "finding_count": len(review.get("findings") or [])
                if isinstance(review.get("findings"), list)
                else 0,
            },
            "resumed_from": {
                "available": resumed_from_available,
                "parent_run_id": run.parent_run_id if resumed_from_available else None,
                "origin_run_id": run.origin_run_id if resumed_from_available else None,
                "checkpoint_id": (run.resume_checkpoint_id if resumed_from_available else None),
            },
            "replay": {
                **replay,
                "case_id": review_replay.get("case_id") or replay.get("case_id"),
                "fingerprint": review_replay.get("fingerprint") or replay.get("fingerprint"),
            },
            "safety": {
                "raw_checkpoint_state_included": False,
                "raw_replay_steps_included": False,
            },
        }

    def _runtime_state(run: LoopRun) -> dict[str, Any]:
        is_running = False
        is_running_fn = getattr(dispatcher, "is_running", None)
        if callable(is_running_fn):
            is_running = bool(is_running_fn(run.run_id))
        task_run, lease_health = _task_health_payload(run.run_id)
        recovery_audit = _recovery_audit(run)
        return LoopRunRuntimeStateResponse(
            run_id=run.run_id,
            parent_run_id=run.parent_run_id,
            origin_run_id=run.origin_run_id,
            resume_checkpoint_id=run.resume_checkpoint_id,
            status=run.status,
            is_running=is_running,
            attempt_count=len(run.attempts),
            last_error=run.last_error,
            workspace_path=run.workspace_path,
            started_at=run.started_at,
            completed_at=run.completed_at,
            updated_at=run.updated_at,
            review_available=run.last_review is not None,
            cancel_requested=bool(run.cancel_requested_at),
            cancel_requested_at=run.cancel_requested_at,
            cancel_reason=run.cancel_reason,
            task_run=task_run,
            task_lease_health=lease_health,
            task_recovery=lease_health.get("recovery", {})
            if isinstance(lease_health, dict)
            else {},
            recovery_audit=recovery_audit,
        ).model_dump(mode="json")

    def _overview(actor_id: str | None) -> dict[str, Any]:
        runs = store.list(
            owner_id=actor_id if require_auth else None,
            limit=1_000_000,
            include_unowned=not require_auth or _is_admin(actor_id),
        )
        by_status: dict[str, int] = {}
        by_mode: dict[str, int] = {}
        active_run_ids: list[str] = []
        reviewed_runs = 0
        task_health_items: list[dict[str, Any]] = []
        takeover_task_ids: list[str] = []
        resumable_task_ids: list[str] = []
        by_recommended_action: dict[str, int] = {}
        checkpoint_available_count = 0
        resume_available_count = 0
        replay_available_count = 0
        for run in runs:
            by_status[run.status.value] = by_status.get(run.status.value, 0) + 1
            by_mode[run.mode.value] = by_mode.get(run.mode.value, 0) + 1
            if run.last_review is not None:
                reviewed_runs += 1
            state = _runtime_state(run)
            if state["is_running"]:
                active_run_ids.append(run.run_id)
            recovery_audit = state.get("recovery_audit")
            if isinstance(recovery_audit, dict):
                checkpoint = recovery_audit.get("checkpoint")
                resume = recovery_audit.get("resume")
                replay = recovery_audit.get("replay")
                if isinstance(checkpoint, dict) and checkpoint.get("available"):
                    checkpoint_available_count += 1
                if isinstance(resume, dict) and resume.get("available"):
                    resume_available_count += 1
                if isinstance(replay, dict) and replay.get("replayable"):
                    replay_available_count += 1
            task_health = state.get("task_lease_health")
            if isinstance(task_health, dict) and task_health:
                task_health_items.append(task_health)
                action = str(task_health.get("recommended_action") or "unknown")
                by_recommended_action[action] = by_recommended_action.get(action, 0) + 1
                if bool(task_health.get("can_takeover")):
                    takeover_task_ids.append(run.run_id)
                if bool(task_health.get("can_resume")):
                    resumable_task_ids.append(run.run_id)
        active_run_ids.sort()
        takeover_task_ids.sort()
        resumable_task_ids.sort()
        unhealthy = [
            item
            for item in task_health_items
            if str(item.get("state") or "") not in {"ok", "terminal"}
        ]
        return LoopRunsOverviewResponse(
            total=len(runs),
            active_dispatches=len(active_run_ids),
            active_run_ids=active_run_ids,
            by_status=by_status,
            by_mode=by_mode,
            reviewed_runs=reviewed_runs,
            task_health={
                "tracked_count": len(task_health_items),
                "unhealthy_count": len(unhealthy),
                "unhealthy_task_ids": [str(item.get("task_id") or "") for item in unhealthy],
                "takeover_recommended_count": len(takeover_task_ids),
                "resumable_count": len(resumable_task_ids),
                "takeover_task_ids": takeover_task_ids,
                "resumable_task_ids": resumable_task_ids,
                "by_recommended_action": dict(sorted(by_recommended_action.items())),
                "items": task_health_items,
            },
            recovery_audit={
                "checkpoint_available_count": checkpoint_available_count,
                "resume_available_count": resume_available_count,
                "replay_available_count": replay_available_count,
            },
        ).model_dump(mode="json")

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

    def _is_admin(actor_id: str | None) -> bool:
        if actor_id is None or identity_store is None:
            return False
        identity = identity_store.get(actor_id)
        roles = getattr(identity, "roles", ()) or ()
        return "admin" in {str(role).strip().lower() for role in roles}

    def _operator(request: Request) -> None:
        from runtime.safety.auth.principal import require_operator

        # Loop execution accepts workspace/thread inputs and can run tools.
        # Until it is bound to a verified managed thread workspace, keep its
        # mutating/execution surface operational-only in shared deployments.
        require_operator(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _can_access(run: LoopRun | None, actor_id: str | None) -> bool:
        if run is None:
            return False
        owner = str(run.owner_id or "").strip()
        if not require_auth:
            return True
        if not owner:
            return _is_admin(actor_id)
        return owner == str(actor_id or "").strip()

    def _owned_run(run_id: str, actor_id: str | None) -> LoopRun:
        run = store.get(run_id)
        if not _can_access(run, actor_id):
            raise HTTPException(404, f"loop run not found: {run_id}")
        if run is None:
            raise HTTPException(404, f"loop run not found: {run_id}")
        return run

    def _review_or_raise(run: LoopRun) -> dict[str, Any]:
        review = run.last_review if isinstance(run.last_review, dict) else None
        if review is None:
            raise HTTPException(409, "loop run review not available")
        return review

    def _resumable_or_raise(run: LoopRun) -> LoopRun:
        # INTERRUPTED (audit R-02): reconciled from an active status at
        # startup after the process died mid-run — terminal, so resumable.
        if run.status not in {
            LoopRunStatus.FAILED,
            LoopRunStatus.CANCELLED,
            LoopRunStatus.INTERRUPTED,
        }:
            raise HTTPException(409, "loop run is not resumable")
        return run

    def _maybe_execute_or_dispatch(
        run: LoopRun,
        actor_id: str | None,
        *,
        execute: bool,
        background: bool,
    ) -> LoopRun:
        if not execute:
            return run
        if background:
            if dispatcher is None:
                raise HTTPException(503, "loop dispatcher unavailable")
            if not dispatcher.submit(run.run_id):
                raise HTTPException(429, "loop dispatcher queue full; retry later")
            return _owned_run(run.run_id, actor_id)
        if controller is None:
            raise HTTPException(503, "loop controller unavailable")
        return controller.execute(run.run_id)

    def _cancel_without_controller(run_id: str, reason: str) -> LoopRun:
        latest = store.get(run_id)
        if latest is None:
            raise KeyError(run_id)
        if latest.status in {
            LoopRunStatus.COMPLETED,
            LoopRunStatus.FAILED,
            LoopRunStatus.CANCELLED,
        }:
            return latest
        from datetime import UTC, datetime

        cancel_at = datetime.now(UTC).isoformat()

        def _cancel_run(
            current: LoopRun, _cancel_at: str = cancel_at, _reason: str = reason
        ) -> LoopRun:
            return current.model_copy(
                update={
                    "status": LoopRunStatus.CANCELLED,
                    "completed_at": current.completed_at or _cancel_at,
                    "cancel_requested_at": current.cancel_requested_at or _cancel_at,
                    "cancel_reason": _reason,
                    "last_error": _reason,
                }
            )

        return store.mutate(run_id, _cancel_run)

    @router.post(
        "/api/loops/start",
        response_model=LoopRun,
    )
    def api_loop_start(
        request: Request,
        body: CreateLoopRunRequest,
    ) -> dict[str, Any]:
        _operator(request)
        actor = _auth(request)
        tenant_scope = scope_from_request(request)
        if body.execute and controller is None:
            raise HTTPException(503, "loop controller unavailable")
        run = LoopRun(
            tenant_id=(tenant_scope.tenant_id if tenant_scope is not None else None),
            owner_id=actor,
            goal=body.goal.strip(),
            mode=body.mode,
            thread_id=body.thread_id,
            workspace_path=body.workspace_path,
            policy=body.policy,
        )
        created = store.create(run)
        created = _maybe_execute_or_dispatch(
            created,
            actor,
            execute=body.execute,
            background=body.background,
        )
        return created.model_dump(mode="json")

    @router.post(
        "/api/loops/{run_id}/execute",
        response_model=LoopRun,
    )
    def api_loop_execute(request: Request, run_id: str) -> dict[str, Any]:
        _operator(request)
        actor = _auth(request)
        _owned_run(run_id, actor)
        if controller is None:
            raise HTTPException(503, "loop controller unavailable")
        try:
            run = controller.execute(run_id)
        except KeyError as exc:
            raise HTTPException(404, f"loop run not found: {run_id}") from exc
        return run.model_dump(mode="json")

    @router.post(
        "/api/loops/{run_id}/dispatch",
        response_model=LoopRun,
    )
    def api_loop_dispatch(request: Request, run_id: str) -> dict[str, Any]:
        _operator(request)
        actor = _auth(request)
        _owned_run(run_id, actor)
        if dispatcher is None:
            raise HTTPException(503, "loop dispatcher unavailable")
        if not dispatcher.submit(run_id):
            raise HTTPException(429, "loop dispatcher queue full; retry later")
        latest = _owned_run(run_id, actor)
        return latest.model_dump(mode="json")

    @router.post(
        "/api/loops/{run_id}/cancel",
        response_model=LoopRun,
    )
    def api_loop_cancel(
        request: Request,
        run_id: str,
        body: CancelLoopRunRequest | None = None,
    ) -> dict[str, Any]:
        actor = _auth(request)
        _owned_run(run_id, actor)
        reason = (
            str(body.reason or "").strip()
            if body is not None and body.reason is not None
            else "cancelled by operator"
        ) or "cancelled by operator"
        if dispatcher is not None and hasattr(dispatcher, "cancel"):
            result = dispatcher.cancel(run_id, reason=reason)
            run = result.get("run") if isinstance(result, dict) else None
            if isinstance(run, LoopRun):
                return run.model_dump(mode="json")
        elif controller is not None and hasattr(controller, "request_cancel"):
            return controller.request_cancel(run_id, reason=reason).model_dump(mode="json")
        try:
            run = _cancel_without_controller(run_id, reason)
        except KeyError as exc:
            raise HTTPException(404, f"loop run not found: {run_id}") from exc
        return run.model_dump(mode="json")

    @router.post(
        "/api/loops/{run_id}/restart",
        response_model=LoopRun,
    )
    def api_loop_restart(
        request: Request,
        run_id: str,
        body: RestartLoopRunRequest | None = None,
    ) -> dict[str, Any]:
        _operator(request)
        actor = _auth(request)
        _owned_run(run_id, actor)
        if controller is None:
            raise HTTPException(503, "loop controller unavailable")
        payload = body or RestartLoopRunRequest()
        try:
            restarted = controller.restart(
                run_id,
                goal=payload.goal,
                thread_id=payload.thread_id,
                workspace_path=payload.workspace_path,
                reuse_workspace=payload.reuse_workspace,
                policy=payload.policy,
            )
        except KeyError as exc:
            raise HTTPException(404, f"loop run not found: {run_id}") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        restarted = _maybe_execute_or_dispatch(
            restarted,
            actor,
            execute=payload.execute,
            background=payload.background,
        )
        return restarted.model_dump(mode="json")

    @router.post(
        "/api/loops/{run_id}/resume",
        response_model=LoopRun,
    )
    def api_loop_resume(
        request: Request,
        run_id: str,
        body: RestartLoopRunRequest | None = None,
    ) -> dict[str, Any]:
        _operator(request)
        actor = _auth(request)
        _owned_run(run_id, actor)
        if controller is None:
            raise HTTPException(503, "loop controller unavailable")
        payload = body or RestartLoopRunRequest()
        try:
            resumed = controller.resume(
                run_id,
                goal=payload.goal,
                thread_id=payload.thread_id,
                workspace_path=payload.workspace_path,
                reuse_workspace=payload.reuse_workspace,
                policy=payload.policy,
            )
        except KeyError as exc:
            raise HTTPException(404, f"loop run not found: {run_id}") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        resumed = _maybe_execute_or_dispatch(
            resumed,
            actor,
            execute=payload.execute,
            background=payload.background,
        )
        return resumed.model_dump(mode="json")

    @router.get(
        "/api/loops",
        response_model=LoopRunListResponse,
    )
    def api_loop_list(
        request: Request,
        status: str | None = None,
        mode: str | None = None,
        limit: int = Query(50, ge=1, le=500),  # noqa: B008
        offset: int = Query(0, ge=0),  # noqa: B008
    ) -> dict[str, Any]:
        actor = _auth(request)
        runs = store.list(
            owner_id=actor if require_auth else None,
            status=status,
            mode=mode,
            limit=limit,
            offset=offset,
            include_unowned=not require_auth or _is_admin(actor),
        )
        total = store.count(
            owner_id=actor if require_auth else None,
            status=status,
            mode=mode,
            include_unowned=not require_auth or _is_admin(actor),
        )
        return {
            "runs": [run.model_dump(mode="json") for run in runs],
            "total": total,
        }

    @router.get(
        "/api/loops/overview",
        response_model=LoopRunsOverviewResponse,
    )
    def api_loop_overview(request: Request) -> dict[str, Any]:
        actor = _auth(request)
        return _overview(actor)

    @router.get(
        "/api/loops/{run_id}",
        response_model=LoopRun,
    )
    def api_loop_get(request: Request, run_id: str) -> dict[str, Any]:
        actor = _auth(request)
        run = _owned_run(run_id, actor)
        return run.model_dump(mode="json")

    @router.get(
        "/api/loops/{run_id}/status",
        response_model=LoopRunRuntimeStateResponse,
    )
    def api_loop_status(request: Request, run_id: str) -> dict[str, Any]:
        actor = _auth(request)
        run = _owned_run(run_id, actor)
        return _runtime_state(run)

    @router.get("/api/loops/{run_id}/review")
    def api_loop_review(request: Request, run_id: str) -> dict[str, Any]:
        actor = _auth(request)
        run = _owned_run(run_id, actor)
        return {
            "run_id": run.run_id,
            "review": run.last_review,
            "queue_result": run.last_review_queue_result,
        }

    @router.get("/api/loops/{run_id}/resume-proposal")
    def api_loop_resume_proposal(request: Request, run_id: str) -> dict[str, Any]:
        actor = _auth(request)
        run = _resumable_or_raise(_owned_run(run_id, actor))
        return {
            "run_id": run.run_id,
            "proposal": build_loop_run_resume_proposal(run),
        }

    @router.get("/api/loops/{run_id}/replay-case")
    def api_loop_replay_case(request: Request, run_id: str) -> dict[str, Any]:
        actor = _auth(request)
        run = _owned_run(run_id, actor)
        review = _review_or_raise(run)
        return {"replay_case": build_loop_run_replay_case(review)}

    @router.get("/api/loops/{run_id}/replay-evaluation")
    def api_loop_replay_evaluation(request: Request, run_id: str) -> dict[str, Any]:
        actor = _auth(request)
        run = _owned_run(run_id, actor)
        review = _review_or_raise(run)
        replay_case = build_loop_run_replay_case(review)
        return {"evaluation": evaluate_loop_run_replay_case(replay_case)}

    return router


__all__ = ["create_loop_router"]
