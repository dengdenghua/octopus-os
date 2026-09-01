"""Persistent team tasks API.

A team task is the unit of "work" inside a team room — distinct from
chat messages or the AI roster. Each task represents a goal the team
(humans + agents) is collaborating on, optionally driven by a SOP /
meta-skill workflow.

This router intentionally mirrors ``team_rooms_router`` in shape:
JSON-on-disk persistence, a Lock-guarded in-memory dict, the same auth
hook, and matching field naming so the frontend can reuse client
patterns. WebSocket presence + invite are NOT included — task lifecycle
events will be broadcast through the existing team-room WS in a later
patch (P3) when TeamRunner is wired in.

Schema notes:
- ``room_id`` ties each task to exactly one team room. Listing scopes
  by room_id; cross-room queries are not supported by design.
- ``sop_template`` is the meta-skill name (matches a YAML in
  ``meta_skills/``); empty means "freeform task without a workflow".
- ``assignees`` is a list of {kind: "agent"|"participant", ref: name|id}
  so a task can be assigned to AI roster members AND human participants
  uniformly. The runner side will fan out per-kind in P3.
- ``status`` is the lifecycle: pending → running → done|failed|cancelled.
  No automatic transitions in M0 — explicit PATCH only. P3 will hook
  TeamRunner callbacks to drive these.
- ``produced_artifacts`` placeholder for future runner output (final
  text, file paths, etc.) — empty in M0.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from runtime.platform.process.paths import app_paths
from runtime.safety.approval.cancellation import CancellationSource, scoped_cancellation
from runtime.sensing.gateway._team_tasks_access import TeamTaskAccess
from runtime.sensing.gateway._team_tasks_helpers import (
    _LOG,
    _MAX_CONCURRENT_RUNS,
    _SOP_TEMPLATE_PATTERN,
    RoomMembershipResolver,
    RoomParticipantResolver,
    RunnerFactory,
    TaskProjection,
    TeamEventBroadcaster,
    _fallback_topology,  # noqa: F401 — compatibility re-export
    _jsonable,
    _load_state,
    _mobile_artifacts,
    _normalize_status,
    _now,
    _prepare_team_run,
    _runner_artifacts,
    _runner_metadata,
    _runner_result_error,
    _runner_result_success,
    _save_state,
    _task_input_text,  # noqa: F401 — compatibility re-export
    _team_task_process_timeline,
)
from runtime.sensing.gateway._team_tasks_models import (
    CreateTeamTaskRequest,
    TaskAssigneeWire,
    TeamTaskWire,
    UpdateTeamTaskRequest,
)

try:
    from fastapi import APIRouter, HTTPException, Request

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment,misc]
    HTTPException = None  # type: ignore[assignment,misc]
    Request = None  # type: ignore[assignment,misc]

from runtime.sensing._fastapi_guard import require_fastapi  # noqa: E402, I001 — after FASTAPI_AVAILABLE flag

# ── Router factory ──────────────────────────────────────────────────


def create_team_tasks_router(
    *,
    state_path: Path | None = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    reset_callback: Any = None,
    team_event_broadcaster: TeamEventBroadcaster | None = None,
    task_projection: TaskProjection | None = None,
    task_delete_projection: Callable[[str], None] | None = None,
    runner_factory: RunnerFactory | None = None,
    room_membership_resolver: RoomMembershipResolver | None = None,
    room_participant_resolver: RoomParticipantResolver | None = None,
    max_concurrent_runs: int = _MAX_CONCURRENT_RUNS,
) -> Any:
    """Create ``/api/team-tasks/*`` routes.

    ╔════════════════════════════════════════════════════════════════════╗
    ║ team_tasks_router.py · navigation map (with private helpers).      ║
    ║                                                                    ║
    ║   §1 Pydantic wires + state schemas              ~L1-138           ║
    ║   §2 create_team_tasks_router(...) factory       ~L139             ║
    ║       §2.1 _auth + _resolve_room_member helpers  ~L160-400         ║
    ║       §2.2 GET /api/team-tasks (list)             ~L410             ║
    ║       §2.3 POST /api/team-tasks (create)          ~L427             ║
    ║       §2.4 GET /api/team-tasks/{id}               ~L463             ║
    ║       §2.5 POST /api/team-tasks/{id}/run          ~L475             ║
    ║       §2.6 PATCH /api/team-tasks/{id}             ~L542             ║
    ║       §2.7 DELETE /api/team-tasks/{id}            ~L590             ║
    ║   §3 background runner + event broadcasting     ~L600-end          ║
    ╚════════════════════════════════════════════════════════════════════╝

    Mirrors the signature of ``create_team_rooms_router`` so the host
    app can wire both with the same identity / auth knobs.

    ``room_participant_resolver`` returns the current participant record and
    is authoritative when present: viewers may read tasks, while owners and
    members may create, run, update, or delete them. The legacy
    ``room_membership_resolver`` only proves membership and remains supported
    for older embedders that do not expose room roles yet. When
    ``require_auth`` is true, every per-task endpoint enforces membership.
    When ``require_auth`` is false (single-user dev mode) the check is
    skipped — matching the existing single-user bypass behavior.
    """
    require_fastapi(__name__)

    router: Any = APIRouter(tags=["team-tasks"])
    path = state_path or (app_paths().data_dir / "team_tasks.json")
    lock = Lock()
    tasks: dict[str, TeamTaskWire] = _load_state(path)
    running: dict[str, CancellationSource] = {}

    # ``running`` is intentionally in-memory, so a process restart cannot
    # reconstruct the worker thread that owned a persisted running task.
    # Leaving that record untouched creates a permanent false-positive: the
    # next /run call returns it as already running forever.  Until checkpoint
    # resume exists, converge such records to an explicit terminal failure so
    # operators can retry and the UI can explain what happened.
    orphaned = False
    for task_id, task in list(tasks.items()):
        if task.status != "running":
            continue
        metadata = dict(task.metadata)
        metadata["failure_code"] = "worker_lost_on_restart"
        metadata["error"] = "task worker was lost when the service restarted"
        metadata["recovered_at"] = _now()
        tasks[task_id] = task.model_copy(
            update={
                "status": "failed",
                "updated_at": _now(),
                "completed_at": _now(),
                "metadata": metadata,
            }
        )
        orphaned = True
    if orphaned:
        _save_state(path, tasks)
        _LOG.warning("team task startup reconciliation marked orphaned running tasks failed")

    access = TeamTaskAccess(
        identity_store=identity_store,
        require_auth=require_auth,
        jwt_secret=jwt_secret,
        jwt_issuer=jwt_issuer,
        jwt_audience=jwt_audience,
        room_membership_resolver=room_membership_resolver,
        room_participant_resolver=room_participant_resolver,
        http_exception=HTTPException,
    )
    _identity = access.identity
    _require_member = access.require_member
    _is_member = access.is_member

    def _validate_sop_template(value: str) -> str:
        """Normalize and reject sop_template values that could escape
        the meta_skills directory. Empty string means freeform task."""
        normalized = (value or "").strip()
        if not normalized:
            return ""
        if not _SOP_TEMPLATE_PATTERN.fullmatch(normalized):
            raise HTTPException(
                400,
                "sop_template must match [a-zA-Z0-9_.-]+ (no slashes, no traversal)",
            )
        return normalized

    def _save() -> None:
        _save_state(path, tasks)

    def _project_task(task: TeamTaskWire) -> None:
        if task_projection is None:
            return
        try:
            task_projection(task.room_id, task.model_dump())
        except Exception:  # noqa: BLE001 - projection must not block task writes
            _LOG.warning("team task projection failed for %s", task.id, exc_info=True)

    def _project_task_delete(task_id: str) -> None:
        if task_delete_projection is None:
            return
        try:
            task_delete_projection(task_id)
        except Exception:  # noqa: BLE001 - projection must not block task deletion
            _LOG.warning("team task delete projection failed for %s", task_id, exc_info=True)

    async def _broadcast_task_event(room_id: str, payload: dict[str, Any]) -> None:
        if team_event_broadcaster is None:
            return
        result = team_event_broadcaster(room_id, payload)
        if result is not None:
            await result

    def _broadcast_from_worker(
        _loop: asyncio.AbstractEventLoop | None,
        room_id: str,
        payload: dict[str, Any],
    ) -> None:
        if team_event_broadcaster is None:
            return

        coro = _broadcast_task_event(room_id, payload)
        try:
            asyncio.run(coro)
        except (
            RuntimeError,
            TimeoutError,
            concurrent.futures.CancelledError,
            concurrent.futures.TimeoutError,
            OSError,
        ):
            coro.close()
            _LOG.debug("team task broadcast failed", exc_info=True)

    def _log_broadcast_result(
        future: asyncio.Future[Any] | concurrent.futures.Future[Any],
    ) -> None:
        try:
            future.result()
        except (
            asyncio.CancelledError,
            RuntimeError,
            TimeoutError,
            concurrent.futures.TimeoutError,
            OSError,
        ):
            _LOG.debug("team task broadcast failed", exc_info=True)

    def _task_payload(
        task: TeamTaskWire,
        *,
        event: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "task:progress",
            "team_id": task.room_id,
            "room_id": task.room_id,
            "task_id": task.id,
            "task": task.model_dump(),
            "event": event,
            "server_time": _now(),
            **(extra or {}),
        }

    def _persist_task(
        task_id: str,
        updates: dict[str, Any],
    ) -> TeamTaskWire | None:
        with lock:
            current = tasks.get(task_id)
            if current is None:
                return None
            updated = current.model_copy(update={"updated_at": _now(), **updates})
            tasks[task_id] = updated
            _save()
            _project_task(updated)
            return updated

    def _build_terminal_task(
        task_id: str,
        updates: dict[str, Any],
        *,
        status: str,
        error: str = "",
    ) -> TeamTaskWire | None:
        with lock:
            current = tasks.get(task_id)
            if current is None:
                return None
            completed_at = str(updates.get("completed_at") or _now())
            raw_metadata = updates.get("metadata")
            metadata = (
                dict(raw_metadata) if isinstance(raw_metadata, dict) else dict(current.metadata)
            )
            events = [item for item in metadata.get("process_events", []) if isinstance(item, dict)]
            events.append(
                _jsonable(
                    {
                        "ts": completed_at,
                        "type": f"run_{status}",
                        "status": status,
                        "error": error,
                    }
                )
            )
            metadata["process_events"] = events[-300:]
            return current.model_copy(
                update={
                    "updated_at": _now(),
                    **updates,
                    "status": status,
                    "completed_at": completed_at,
                    "metadata": metadata,
                }
            )

    def _persist_prebuilt_task(task: TeamTaskWire) -> TeamTaskWire | None:
        with lock:
            if task.id not in tasks:
                return None
            tasks[task.id] = task
            _save()
            _project_task(task)
            return task

    def _append_process_event(task_id: str, event: dict[str, Any]) -> None:
        with lock:
            current = tasks.get(task_id)
            if current is None:
                return
            metadata = dict(current.metadata)
            events = [item for item in metadata.get("process_events", []) if isinstance(item, dict)]
            events.append(_jsonable(event))
            metadata["process_events"] = events[-300:]
            updated = current.model_copy(
                update={
                    "metadata": metadata,
                    "updated_at": _now(),
                }
            )
            tasks[task_id] = updated
            _save()
            _project_task(updated)

    def _current_metadata(
        task_id: str, *, fallback: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        with lock:
            current = tasks.get(task_id)
            if current is None:
                return dict(fallback or {})
            return dict(current.metadata)

    def _runner_instance(event_emitter: Callable[[dict[str, Any]], None]) -> Any:
        if runner_factory is None:
            from runtime.safety.organization.team_runner import TeamRunner

            return TeamRunner(timeout_seconds=900, event_emitter=event_emitter)
        try:
            return runner_factory(timeout_seconds=900, event_emitter=event_emitter)
        except TypeError:
            return runner_factory()

    def _run_task_worker(
        task: TeamTaskWire,
        prepared: dict[str, Any],
        source: CancellationSource,
        loop: asyncio.AbstractEventLoop | None,
    ) -> None:
        topology = prepared["topology"]
        completed_roles: set[str] = set()
        total_roles = max(1, len(getattr(topology, "agents", {}) or {}))

        def _current_task() -> TeamTaskWire:
            with lock:
                return tasks.get(task.id) or task

        def _emit_runner_event(event: dict[str, Any]) -> None:
            event_type = str(event.get("type") or "runner_event")
            role = str(event.get("role") or "")
            if event_type == "team_role_end" and role:
                completed_roles.add(role)
            status = str(event.get("status") or "")
            _append_process_event(
                task.id,
                {
                    "ts": _now(),
                    "type": event_type,
                    "role": role,
                    "agent_id": event.get("agent_id"),
                    "status": status,
                    "event": _jsonable(event),
                },
            )
            _broadcast_from_worker(
                loop,
                task.room_id,
                _task_payload(
                    _current_task(),
                    event="role_completed" if event_type == "team_role_end" else event_type,
                    extra={
                        "runner_event": event,
                        "role": role or None,
                        "role_status": status or None,
                        "completed_roles": len(completed_roles),
                        "total_roles": total_roles,
                        "progress": min(1.0, len(completed_roles) / total_roles),
                    },
                ),
            )

        def _record_terminal_event(
            updated: TeamTaskWire,
            *,
            status: str,
            error: str = "",
        ) -> None:
            event_type = f"run_{status}"
            _broadcast_from_worker(
                loop,
                updated.room_id,
                _task_payload(
                    updated,
                    event=event_type,
                    extra={"error": error} if error else None,
                ),
            )

        result: Any = None
        try:
            # ── Mobile route ────────────────────────────────────
            # Task assigned to connected phones (ref ``mobile_*``): run the goal
            # on each device through the in-process tentacle bridge and record
            # what it did. Falls through to CLI / topology when none assigned.
            mobile_refs = [
                a.ref.strip()
                for a in task.assignees
                if a.kind.strip().lower() == "agent" and a.ref.strip().startswith("mobile_")
            ]
            if mobile_refs:
                import asyncio as _asyncio

                from runtime.tentacle.team_bridge import (
                    device_id_from_ref,
                    get_active_coordinator,
                    run_device_task,
                )

                coordinator = get_active_coordinator()
                records: list[dict[str, Any]] = []
                if coordinator is None or loop is None:
                    records = [
                        {
                            "tentacle_id": device_id_from_ref(ref),
                            "ok": False,
                            "output": "",
                            "error": "mobile bridge not running",
                        }
                        for ref in mobile_refs
                    ]
                else:
                    for ref in mobile_refs:
                        tentacle_id = device_id_from_ref(ref)
                        try:
                            with scoped_cancellation(source.token):
                                future = _asyncio.run_coroutine_threadsafe(
                                    run_device_task(
                                        coordinator, tentacle_id, prepared["task_input"]
                                    ),
                                    loop,
                                )
                                records.append(future.result(timeout=260.0))
                        except Exception as exc:  # noqa: BLE001 — isolate per device
                            records.append(
                                {
                                    "tentacle_id": tentacle_id,
                                    "ok": False,
                                    "output": "",
                                    "error": f"{type(exc).__name__}: {exc}",
                                }
                            )
                succeeded = sum(1 for r in records if r.get("ok"))
                final_status = (
                    "cancelled" if source.is_cancelled else ("done" if succeeded else "failed")
                )
                metadata = {
                    **_current_metadata(task.id, fallback=task.metadata),
                    "runner": {
                        "engine": "mobile",
                        "devices": len(records),
                        "succeeded": succeeded,
                    },
                }
                if final_status == "failed":
                    metadata["error"] = "no connected device completed the task"
                mobile_updates: dict[str, Any] = {
                    "status": final_status,
                    "completed_at": _now(),
                    "metadata": metadata,
                }
                if final_status == "done":
                    mobile_updates["produced_artifacts"] = _mobile_artifacts(records)
                updated = _build_terminal_task(
                    task.id,
                    mobile_updates,
                    status=final_status,
                    error=str(metadata.get("error") or ""),
                )
                if updated is not None:
                    _record_terminal_event(
                        updated,
                        status=final_status,
                        error=str(metadata.get("error") or ""),
                    )
                    _persist_prebuilt_task(updated)
                return

            runner = _runner_instance(_emit_runner_event)
            with scoped_cancellation(source.token):
                result = runner.run(
                    topology,
                    prepared["task_input"],
                    context=prepared["context"],
                )
            final_status = (
                "cancelled"
                if source.is_cancelled
                else ("done" if _runner_result_success(result) else "failed")
            )
            metadata = {
                **_current_metadata(task.id, fallback=task.metadata),
                "runner": _runner_metadata(result, prepared),
            }
            if final_status == "failed":
                metadata["error"] = _runner_result_error(result) or "TeamRunner reported failure"
            updates: dict[str, Any] = {
                "status": final_status,
                "completed_at": _now(),
                "metadata": metadata,
            }
            if final_status == "done":
                updates["produced_artifacts"] = _runner_artifacts(result, prepared)
            updated = _build_terminal_task(
                task.id,
                updates,
                status=final_status,
                error=str(metadata.get("error") or ""),
            )
            if updated is not None:
                _record_terminal_event(
                    updated,
                    status=final_status,
                    error=str(metadata.get("error") or ""),
                )
                _persist_prebuilt_task(updated)
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("team task runner failed for %s", task.id)
            error = f"{type(exc).__name__}: {exc}"
            updated = _build_terminal_task(
                task.id,
                {
                    "status": "failed",
                    "completed_at": _now(),
                    "metadata": {
                        **_current_metadata(task.id, fallback=task.metadata),
                        "error": error,
                        "runner": {
                            "topology": getattr(topology, "name", ""),
                            "meta_skill": prepared.get("meta_skill"),
                        },
                    },
                },
                status="failed",
                error=error,
            )
            if updated is not None:
                _record_terminal_event(updated, status="failed", error=error)
                _persist_prebuilt_task(updated)
        finally:
            projected_after_exit: TeamTaskWire | None = None
            with lock:
                running.pop(task.id, None)
                # Safety net: if the task is still "running" after the
                # worker exits (e.g. BaseException bypassed the except
                # clause), mark it "failed" so it never gets stuck.
                current = tasks.get(task.id)
                if current is not None and current.status == "running":
                    projected_after_exit = current.model_copy(
                        update={
                            "status": "failed",
                            "completed_at": _now(),
                            "metadata": {
                                **dict(current.metadata),
                                "error": "worker exited without setting terminal status",
                            },
                        }
                    )
                    tasks[task.id] = projected_after_exit
                    _save()
            if projected_after_exit is not None:
                _project_task(projected_after_exit)

    def _reset_state() -> None:
        with lock:
            for source in running.values():
                source.cancel(reason="team task router reset")
            running.clear()
            tasks.clear()
        if callable(reset_callback):
            reset_callback()

    async def _create_task_for_actor(
        actor: str | None,
        tenant_id: str,
        body: CreateTeamTaskRequest,
    ) -> TeamTaskWire:
        title = body.title.strip()
        if not title:
            raise HTTPException(400, "title is required")
        room_id = body.room_id.strip()
        if not room_id:
            raise HTTPException(400, "room_id is required")
        _require_member(actor, room_id, tenant_id, write=True)
        sop_template = _validate_sop_template(body.sop_template)
        now = _now()
        task = TeamTaskWire(
            id=f"task-{uuid4().hex[:12]}",
            room_id=room_id,
            title=title,
            description=body.description.strip(),
            sop_template=sop_template,
            status="pending",
            assignees=list(body.assignees),
            created_by=actor,
            created_at=now,
            updated_at=now,
            metadata=dict(body.metadata),
        )
        with lock:
            tasks[task.id] = task
            _save()
        _project_task(task)
        await _broadcast_task_event(
            task.room_id,
            _task_payload(task, event="task_created"),
        )
        return task

    async def _run_task_for_actor(
        actor: str | None,
        tenant_id: str,
        task_id: str,
    ) -> TeamTaskWire:
        with lock:
            current = tasks.get(task_id)
            if current is None:
                raise HTTPException(404, f"task not found: {task_id}")
            room_id = current.room_id
        _require_member(actor, room_id, tenant_id, write=True)
        if current.status == "running":
            return current
        try:
            prepared = _prepare_team_run(current)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        now = _now()
        source = CancellationSource()
        metadata = {
            **dict(current.metadata),
            "runner": {
                "status": "running",
                "meta_skill": prepared.get("meta_skill"),
                "topology": prepared["topology"].name,
                "topology_fingerprint": prepared["topology"].fingerprint,
                "task_graph": prepared.get("task_graph"),
            },
        }
        with lock:
            # Concurrency cap checked inside lock — closing TOCTOU window
            # where two requests could both pass the check before either
            # inserts into running{}.
            if len(running) >= max_concurrent_runs:
                raise HTTPException(
                    429,
                    f"too many concurrent runs ({len(running)}/{max_concurrent_runs})",
                )
            latest = tasks.get(task_id)
            if latest is None:
                raise HTTPException(404, f"task not found: {task_id}")
            if latest.status == "running":
                return latest
            updated = latest.model_copy(
                update={
                    "status": "running",
                    "started_at": now,
                    "completed_at": None,
                    "updated_at": now,
                    "metadata": metadata,
                }
            )
            tasks[task_id] = updated
            running[task_id] = source
            _save()
        _project_task(updated)

        await _broadcast_task_event(
            updated.room_id,
            _task_payload(updated, event="run_started"),
        )
        _append_process_event(
            task_id,
            {
                "ts": now,
                "type": "run_started",
                "status": "running",
                "actor": actor,
                "topology": metadata["runner"].get("topology"),
                "topology_fingerprint": metadata["runner"].get("topology_fingerprint"),
                "task_graph": metadata["runner"].get("task_graph"),
            },
        )
        loop = asyncio.get_running_loop()
        thread = threading.Thread(
            target=_run_task_worker,
            args=(updated, prepared, source, loop),
            name=f"team-task-run-{task_id}",
            daemon=True,
        )
        thread.start()
        return updated

    async def _create_task_from_payload(
        request: Request,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        body = CreateTeamTaskRequest.model_validate(payload)
        actor, tenant_id = _identity(request)
        return (await _create_task_for_actor(actor, tenant_id, body)).model_dump()

    async def _run_task_from_request(
        request: Request,
        task_id: str,
    ) -> dict[str, Any]:
        actor, tenant_id = _identity(request)
        return (await _run_task_for_actor(actor, tenant_id, task_id)).model_dump()

    @router.get("/api/team-tasks")
    def list_tasks(request: Request, room_id: str | None = None) -> dict[str, Any]:
        """List tasks. ``room_id`` query param scopes to one room
        (omit to list all — useful for admin/debug, not the typical
        UI flow)."""
        actor, tenant_id = _identity(request)
        with lock:
            items = list(tasks.values())
        if room_id:
            _require_member(actor, room_id, tenant_id)
            items = [t for t in items if t.room_id == room_id]
        elif require_auth:
            # An omitted room filter is a convenience query, not an
            # authorization bypass. Return the union of rooms the caller can
            # actually enter instead of every persisted team's task list.
            if room_participant_resolver is None and room_membership_resolver is None:
                raise HTTPException(503, "room membership resolver unavailable")
            items = [task for task in items if _is_member(actor, task.room_id, tenant_id)]
        items.sort(key=lambda t: t.updated_at, reverse=True)
        return {
            "tasks": [t.model_dump() for t in items],
            "count": len(items),
        }

    @router.post("/api/team-tasks")
    async def create_task(
        request: Request,
        body: CreateTeamTaskRequest,
    ) -> dict[str, Any]:
        actor, tenant_id = _identity(request)
        task = await _create_task_for_actor(actor, tenant_id, body)
        return task.model_dump()

    @router.get("/api/team-tasks/{task_id}")
    def get_task(request: Request, task_id: str) -> dict[str, Any]:
        actor, tenant_id = _identity(request)
        with lock:
            task = tasks.get(task_id)
            if task is None:
                raise HTTPException(404, f"task not found: {task_id}")
            payload = task.model_dump()
            room_id = task.room_id
        _require_member(actor, room_id, tenant_id)
        return payload

    @router.get("/api/team-tasks/{task_id}/process-timeline")
    def get_task_process_timeline(request: Request, task_id: str) -> dict[str, Any]:
        actor, tenant_id = _identity(request)
        with lock:
            task = tasks.get(task_id)
            if task is None:
                raise HTTPException(404, f"task not found: {task_id}")
            room_id = task.room_id
            payload = task.model_dump()
        _require_member(actor, room_id, tenant_id)
        return {"timeline": _team_task_process_timeline(payload)}

    @router.post("/api/team-tasks/{task_id}/run")
    async def run_task(request: Request, task_id: str) -> dict[str, Any]:
        actor, tenant_id = _identity(request)
        updated = await _run_task_for_actor(actor, tenant_id, task_id)
        return updated.model_dump()

    @router.patch("/api/team-tasks/{task_id}")
    async def update_task(
        request: Request,
        task_id: str,
        body: UpdateTeamTaskRequest,
    ) -> dict[str, Any]:
        actor, tenant_id = _identity(request)
        with lock:
            current = tasks.get(task_id)
            if current is None:
                raise HTTPException(404, f"task not found: {task_id}")
            _require_member(actor, current.room_id, tenant_id, write=True)
            updates: dict[str, Any] = {"updated_at": _now()}
            if body.title is not None:
                title = body.title.strip()
                if not title:
                    raise HTTPException(400, "title cannot be empty")
                updates["title"] = title
            if body.description is not None:
                updates["description"] = body.description.strip()
            if body.sop_template is not None:
                updates["sop_template"] = _validate_sop_template(body.sop_template)
            if body.assignees is not None:
                updates["assignees"] = list(body.assignees)
            if body.status is not None:
                next_status = _normalize_status(body.status)
                updates["status"] = next_status
                # Stamp lifecycle timestamps when crossing the boundary.
                # Re-running a done task is allowed (status → running
                # again resets started_at), but completed_at only sets
                # when a task transitions INTO a terminal state.
                if next_status == "running" and current.status != "running":
                    updates["started_at"] = _now()
                if next_status in {"done", "failed", "cancelled"}:
                    updates["completed_at"] = _now()
                if next_status == "cancelled":
                    source = running.get(task_id)
                    if source is not None:
                        source.cancel(reason="team task cancelled")
            updated = current.model_copy(update=updates)
            tasks[task_id] = updated
            _save()
        _project_task(updated)
        await _broadcast_task_event(
            updated.room_id,
            _task_payload(updated, event="task_updated"),
        )
        return updated.model_dump()

    @router.delete("/api/team-tasks/{task_id}")
    async def delete_task(request: Request, task_id: str) -> dict[str, Any]:
        actor, tenant_id = _identity(request)
        with lock:
            existing = tasks.get(task_id)
            if existing is None:
                raise HTTPException(404, f"task not found: {task_id}")
            _require_member(actor, existing.room_id, tenant_id, write=True)
            existed = tasks.pop(task_id, None)
            source = running.pop(task_id, None)
            if source is not None:
                source.cancel(reason="team task deleted")
            if existed is not None:
                _save()
                _project_task_delete(existed.id)
        if existed is not None:
            await _broadcast_task_event(
                existed.room_id,
                _task_payload(
                    existed,
                    event="task_deleted",
                    extra={"deleted": True},
                ),
            )
        return {"ok": True, "deleted": existed is not None, "task_id": task_id}

    router.reset_state = _reset_state
    router.create_task_from_payload = _create_task_from_payload
    router.run_task_from_request = _run_task_from_request
    return router


__all__ = [
    "create_team_tasks_router",
    "TaskAssigneeWire",
    "TeamTaskWire",
    "CreateTeamTaskRequest",
    "UpdateTeamTaskRequest",
    "TeamEventBroadcaster",
    "TaskProjection",
    "RunnerFactory",
    "RoomMembershipResolver",
    "RoomParticipantResolver",
]
