"""Task (pause/resume) endpoints for the agents router.

Pure structural split of ``_agents_endpoints.py`` — no logic changes.
``_register_tasks`` attaches the task lifecycle endpoints (list / get /
pause / resume / delete) to the injected router.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, cast

try:
    from fastapi import HTTPException, Request

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    HTTPException = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]

from ._agents_endpoints_shared import _AuthActions
from .agents_models import PauseTaskBody, ResumeTaskBody

if TYPE_CHECKING:
    from ._agents_endpoints import _AgentsCtx


def _register_tasks(router: Any, ctx: _AgentsCtx, auth: _AuthActions) -> None:
    require_auth = ctx.require_auth
    thread_store = ctx.thread_store
    journal = ctx.journal
    _auth = auth.auth
    _resolve_identity = auth.resolve_identity
    _require_task_owner = auth.require_task_owner

    @router.get("/api/tasks")
    def list_tasks(
        request: Request,
        status: str | None = None,
    ) -> dict[str, Any]:
        actor = _auth(request)
        from runtime.core.cerebrum.pause_control import get_pause_controller

        ctrl = get_pause_controller()

        # Filter to tasks owned by the caller. In dev mode (no auth) or
        # when thread_store is not wired, we fall back to "show all" —
        # matching the legacy behavior so existing dashboards don't
        # silently empty out.
        owned_threads: set[str] | None = None
        is_admin = False
        if require_auth:
            identity = _resolve_identity(request)
            roles = {
                str(role).strip().lower()
                for role in (getattr(identity, "roles", ()) or ())
                if str(role).strip()
            }
            is_admin = "admin" in roles
            owned_threads = set()
        if require_auth and actor and thread_store is not None and not is_admin:
            try:
                all_threads = thread_store.search(limit=10000)
                for thread in all_threads:
                    metadata = thread.get("metadata") or {}
                    owner = metadata.get("owner_actor_id") or metadata.get("owner_id")
                    if owner == actor:
                        tid = thread.get("thread_id")
                        if tid and owned_threads is not None:
                            owned_threads.add(str(tid))
            except (TypeError, AttributeError):  # noqa: BLE001 — thread_store may be None or missing search() in dev mode
                pass
        elif is_admin:
            owned_threads = None

        def _is_owned(item: Any) -> bool:
            if owned_threads is None:
                return True  # dev mode or admin audit: show all
            tid = getattr(item, "thread_id", None) or ""
            if not tid:
                return False
            return tid in owned_threads

        def _to_dict(req: Any) -> dict[str, Any]:
            return req.to_dict()

        out: dict[str, Any] = {}
        if status in (None, "paused", "all"):
            out["paused"] = [_to_dict(r) for r in ctrl.list_paused() if _is_owned(r)]
        if status in (None, "pending", "all"):
            out["pending"] = [_to_dict(r) for r in ctrl.list_pending() if _is_owned(r)]
        if status in (None, "active", "all"):
            out["active"] = [t.to_dict() for t in ctrl.list_active() if _is_owned(t)]
        return out

    @router.get("/api/tasks/{task_id}")
    def get_task(request: Request, task_id: str) -> dict[str, Any]:
        _require_task_owner(request, task_id)
        from runtime.core.cerebrum.pause_control import get_pause_controller

        ctrl = get_pause_controller()
        req = ctrl.get_request(task_id)
        out: dict[str, Any] = {
            "task_id": task_id,
            "is_pending_pause": task_id in {r.task_id for r in ctrl.list_pending()},
            "is_paused": ctrl.is_paused(task_id),
            "pause_request": req.to_dict() if req else None,
        }
        # Enrich with latest checkpoint if journal is available
        if journal is not None:
            try:
                ckpts = [
                    e
                    for e in journal.read_by_type("react_checkpoint")
                    if str(getattr(e, "task_id", "")) == task_id
                ]
                if ckpts:
                    last = ckpts[-1]
                    out["last_checkpoint"] = {
                        "iteration_completed": last.iteration_completed,
                        "max_iterations": last.max_iterations,
                        "steps_count": len(last.steps_snapshot),
                        "has_final_answer": last.has_final_answer,
                    }
            except (AttributeError, TypeError, ValueError):  # noqa: BLE001 — best-effort field extract; skip on failure
                pass
            try:
                usage = [
                    {
                        "iteration": e.iteration,
                        "input_tokens": e.input_tokens,
                        "output_tokens": e.output_tokens,
                        "cost_usd": e.cost_usd,
                        "model": e.model,
                    }
                    for e in journal.read_by_type("token_usage")
                    if str(getattr(e, "task_id", "")) == task_id
                ]
                if usage:
                    usage.sort(key=lambda r: r["iteration"])
                    out["token_usage"] = usage
                    out["token_usage_total"] = {
                        "input_tokens": sum(u["input_tokens"] for u in usage),
                        "output_tokens": sum(u["output_tokens"] for u in usage),
                        "cost_usd": sum(u["cost_usd"] for u in usage),
                    }
            except (AttributeError, TypeError, ValueError):  # noqa: BLE001 — best-effort field extract; skip on failure
                pass
        return out

    @router.post("/api/tasks/{task_id}/pause")
    async def pause_task(
        request: Request,
        task_id: str,
        body: PauseTaskBody | None = None,
    ) -> dict[str, Any]:
        if body is None:
            body = PauseTaskBody()
        _require_task_owner(request, task_id)
        from runtime.core.cerebrum.pause_control import (
            PauseReason,
            get_pause_controller,
        )

        ctrl = get_pause_controller()
        active = next((item for item in ctrl.list_active() if item.task_id == task_id), None)
        req = ctrl.request_pause(
            task_id=task_id,
            reason=cast(PauseReason, body.reason),
            requested_by="",
            note=body.note,
            thread_id=active.thread_id if active is not None else "",
            agent_id=active.agent_id if active is not None else "",
        )
        return {"ok": True, "request": req.to_dict()}

    @router.delete("/api/tasks/{task_id}")
    def delete_task(request: Request, task_id: str) -> dict[str, Any]:
        _require_task_owner(request, task_id)
        from runtime.core.cerebrum.pause_control import get_pause_controller

        ctrl = get_pause_controller()
        ctrl.clear(task_id)
        ctrl.unregister_active(task_id)
        return {"ok": True, "task_id": task_id}

    @router.post("/api/tasks/{task_id}/resume")
    async def resume_task(
        request: Request,
        task_id: str,
        body: ResumeTaskBody | None = None,
    ) -> dict[str, Any]:
        if body is None:
            body = ResumeTaskBody()
        _require_task_owner(request, task_id)
        from runtime.core.cerebrum.pause_control import get_pause_controller

        ctrl = get_pause_controller()

        req = ctrl.get_request(task_id)
        thread_id = req.thread_id if req else ""
        if thread_id:
            ctrl.set_pending_resume(thread_id, task_id)
        if body.extra_iterations > 0 or body.extra_tokens > 0 or body.extra_usd > 0:
            ctrl.set_grant(
                task_id,
                extra_iterations=body.extra_iterations,
                extra_tokens=body.extra_tokens,
                extra_usd=body.extra_usd,
            )
        if journal is not None:
            with contextlib.suppress(Exception):
                journal.write_task_resumed(
                    task_id=task_id,
                    extra_tokens=body.extra_tokens,
                    extra_usd=body.extra_usd,
                    extra_iterations=body.extra_iterations,
                )
        return {
            "ok": True,
            "task_id": task_id,
            "thread_id": thread_id,
            "extra_tokens": body.extra_tokens,
            "extra_iterations": body.extra_iterations,
            "message": (
                f"登记 pending resume 到 thread {thread_id or '?'} · 下条消息 run_stream 会自动接续"
            ),
        }
