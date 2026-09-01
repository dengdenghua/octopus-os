from __future__ import annotations

import asyncio
import json
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Request
    from fastapi.responses import StreamingResponse

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment,misc]
    HTTPException = None  # type: ignore[assignment,misc]
    Request = None  # type: ignore[assignment,misc]
    StreamingResponse = None  # type: ignore[assignment,misc]

from runtime.execution.parallel_agents.models import DispatchRequest, SplitRequest
from runtime.execution.parallel_agents.orchestrator import ParallelAgentOrchestrator
from runtime.sensing._fastapi_guard import require_fastapi


def create_parallel_agents_router(
    *,
    orchestrator: ParallelAgentOrchestrator,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi(__name__)

    router = APIRouter(tags=["parallel-agents"])

    def _principal(request: Any) -> Any:
        from runtime.safety.auth.principal import resolve_principal

        return resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _auth(request: Any) -> str | None:
        """Authenticate the caller and return actor_id.

        The orchestrator now stamps ``owner_id`` on each batch (see
        ``ParallelAgentOrchestrator.dispatch(..., owner_id=...)``) and
        endpoints below filter / enforce ownership using it. In single-
        user dev mode (``require_auth=False``) ``_auth`` returns None
        and the orchestrator treats None-owner as visible-to-everyone,
        so dev workflows are unchanged.
        """
        principal = _principal(request)
        return principal.actor_id if principal is not None else None

    def _require_batch_owner(request: Any, batch_id: str) -> str | None:
        """Enforce that the caller owns ``batch_id`` (or no auth).

        Returns actor_id (None in dev mode). Raises 404 for missing
        batches, 403 for cross-user access.
        """
        principal = _principal(request)
        actor = principal.actor_id if principal is not None else None
        # In dev mode (no auth required) skip the check.
        if not require_auth:
            return actor
        if not actor:
            raise HTTPException(401, "authentication required")
        owner = orchestrator.get_batch_owner(batch_id)
        # Treat missing batch as 404 (not 403) so we don't leak
        # existence info to non-owners.
        if owner is None and orchestrator.get_batch(batch_id) is None:
            raise HTTPException(404, f"batch not found: {batch_id}")
        if owner is None:
            # Fail closed after an auth-on upgrade. Legacy/unmigrated batches
            # are visible only to administrators, not to every new tenant.
            if principal is None or "admin" not in principal.roles:
                raise HTTPException(404, f"batch not found: {batch_id}")
        elif owner != actor:
            raise HTTPException(403, "not the owner of this batch")
        return actor

    def _require_task_owner(request: Any, task_id: str) -> str | None:
        """Enforce that the caller owns the batch containing ``task_id``."""
        principal = _principal(request)
        actor = principal.actor_id if principal is not None else None
        if not require_auth:
            return actor
        if not actor:
            raise HTTPException(401, "authentication required")
        owner = orchestrator.get_task_owner(task_id)
        if owner is None:
            if principal is None or "admin" not in principal.roles:
                raise HTTPException(404, f"task not found: {task_id}")
            return actor
        if owner != actor:
            raise HTTPException(403, "not the owner of the task's batch")
        return actor

    @router.get("/api/agents/parallel/status")
    def status(request: Request) -> dict:
        _auth(request)  # AUTH-OK: actor-agnostic — returns aggregate counts only, no user data
        return orchestrator.status().model_dump()

    @router.get("/api/agents/parallel/batch/{batch_id}")
    def get_batch(request: Request, batch_id: str) -> dict:
        _require_batch_owner(request, batch_id)
        batch = orchestrator.get_batch(batch_id)
        if batch is None:
            raise HTTPException(404, f"batch not found: {batch_id}")
        return batch.model_dump()

    @router.get("/api/agents/parallel/batch/{batch_id}/recovery-snapshot")
    def get_batch_recovery_snapshot(request: Request, batch_id: str) -> dict:
        _require_batch_owner(request, batch_id)
        snapshot = orchestrator.recovery_snapshot(batch_id)
        if snapshot is None:
            raise HTTPException(404, f"batch not found: {batch_id}")
        return snapshot.model_dump()

    # ─── dispatch / split ────────────────────────────────

    @router.post("/api/agents/parallel/dispatch")
    def dispatch(request: Request, body: DispatchRequest) -> dict:
        actor = _auth(request)
        try:
            result = orchestrator.dispatch(
                body.tasks,
                max_concurrency=body.max_concurrency,
                aggregation_strategy=body.aggregation_strategy,
                execution_mode=body.execution_mode,
                thread_id=body.thread_id,
                model_name=body.model_name,
                owner_id=actor,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return result.model_dump()

    @router.post("/api/agents/parallel/split")
    def split(request: Request, body: SplitRequest) -> dict:
        _auth(
            request
        )  # AUTH-OK: actor-agnostic — split is a planning-only operation, doesn't create batches
        result = orchestrator.split(
            body.task,
            max_subtasks=body.max_subtasks,
            context=body.context,
            model_name=body.model_name,
        )
        return result.model_dump()

    # ─── cancel ──────────────────────────────────────────

    @router.post("/api/agents/parallel/cancel/{task_id}")
    def cancel_one(request: Request, task_id: str) -> dict:
        _require_task_owner(request, task_id)
        ok = orchestrator.cancel_task(task_id)
        if not ok:
            raise HTTPException(404, f"task not found or already terminal: {task_id}")
        return {"ok": True, "task_id": task_id}

    @router.post("/api/agents/parallel/cancel-all")
    def cancel_all(request: Request) -> dict:
        actor = _auth(request)
        orchestrator.cancel_all_for_owner(actor)
        return {"ok": True}

    # ─── SSE stream ──────────────────────────────────────

    @router.get("/api/agents/parallel/stream/{batch_id}")
    async def stream(
        request: Request,
        batch_id: str,
        after_sequence: int = 0,
    ) -> Any:
        _require_batch_owner(request, batch_id)
        if orchestrator.get_batch(batch_id) is None:
            raise HTTPException(404, f"batch not found: {batch_id}")

        async def event_stream() -> Any:
            try:
                async for ev in orchestrator.subscribe(
                    batch_id,
                    after_sequence=max(0, after_sequence),
                ):
                    payload = json.dumps(ev.model_dump(), ensure_ascii=False)
                    event_id = ev.sequence or 0
                    yield f"id: {event_id}\nevent: {ev.type}\ndata: {payload}\n\n"
                    if ev.type == "batch_complete":
                        return
            except asyncio.CancelledError:
                return

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return router
