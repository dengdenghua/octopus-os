"""Teach & Repeat API.

This router backs the chat-header REC affordance. Recording is intentionally
opt-in: nothing starts until the user confirms in the UI and calls
``/record/start``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from runtime.sensing.gateway.recorder_store import RecorderStore

_TEMPLATES: dict[str, dict[str, Any]] = {}


class StartRecordingRequest(BaseModel):
    thread_id: str
    name: str
    description: str | None = None
    provider: str = "hybrid"


class AppendRecordingEventsRequest(BaseModel):
    thread_id: str
    events: list[dict[str, Any]] = Field(max_length=100)


class StopRecordingRequest(BaseModel):
    thread_id: str
    use_llm: bool = False
    model_name: str | None = None


class TemplateUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create_teach_repeat_router(
    *,
    journal: Any = None,
    registry: Any = None,
    auto_persist_dir: Path | str | None = None,
    capability_registry: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    recording_store: RecorderStore | None = None,
) -> APIRouter:
    store = recording_store or RecorderStore()
    forge_persist_dir = Path(auto_persist_dir) if auto_persist_dir is not None else None

    def _require_forge_dependencies() -> Path:
        missing = [
            name
            for name, value in (
                ("journal", journal),
                ("registry", registry),
                ("auto_persist_dir", forge_persist_dir),
            )
            if value is None
        ]
        if missing:
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "teach-repeat forge dependencies unavailable",
                    "missing": missing,
                },
            )
        assert forge_persist_dir is not None
        return forge_persist_dir

    def _operator_dep(request: Request) -> None:
        from runtime.safety.auth.principal import require_operator

        # Recordings and forged templates are process-global today.  Do not
        # expose unowned legacy objects across principals in shared mode.
        require_operator(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _recorder_plugin_dep() -> None:
        # Keep the route adapter in the base, but expose no recording surface
        # unless the remotely installable plugin owns and enables it.
        if capability_registry is None:
            return
        item = capability_registry.get("echo-recorder")
        surfaces = item.get("surface_capabilities") if item else []
        if not (
            item and item.get("installed") and item.get("enabled") and "chat.recorder" in surfaces
        ):
            raise HTTPException(404, "REC recorder plugin is not installed or enabled")

    router = APIRouter(dependencies=[Depends(_operator_dep), Depends(_recorder_plugin_dep)])

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

    def _capture_template(session: dict[str, Any]) -> str | None:
        """Materialise a reviewable workflow from semantic UI events."""

        if int(session.get("event_count") or 0) <= 0:
            return None
        path = Path(str(session.get("events_path") or ""))
        if not path.is_file():
            return None
        steps: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    if index >= 1000:
                        break
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    steps.append(
                        {
                            "id": str(event.get("event_id") or f"event-{index + 1}"),
                            "type": "system_event",
                            "content": str(event.get("kind") or "ui_event"),
                            "tool_name": None,
                            "tool_args": {},
                            "tool_result": None,
                            "expected_output_pattern": None,
                            "is_parameterised": False,
                            "metadata": event,
                            "timestamp": str(event.get("ts") or _now()),
                        }
                    )
        except OSError:
            return None
        if not steps:
            return None
        template_id = f"rec_{uuid4().hex[:12]}"
        created = _now()
        _TEMPLATES[template_id] = {
            "id": template_id,
            "name": str(session.get("name") or "REC demonstration"),
            "description": str(session.get("description") or ""),
            "steps": steps,
            "params": [],
            "tags": ["rec", "demonstration", str(session.get("provider") or "hybrid")],
            "use_count": 0,
            "last_used_at": None,
            "created_at": created,
            "updated_at": created,
            "recording_session_id": session.get("session_id"),
        }
        return template_id

    @router.post("/api/teach-repeat/record/start")
    def start_recording(request: Request, body: StartRecordingRequest) -> dict[str, Any]:
        _auth(request)
        thread_id = body.thread_id.strip()
        if not thread_id:
            raise HTTPException(400, "thread_id is required")
        name = body.name.strip() or "对话回放学习"
        provider = body.provider.strip().lower() or "hybrid"
        if provider not in {"agent", "human", "hybrid"}:
            raise HTTPException(400, "provider must be agent, human, or hybrid")
        rec = store.start(
            thread_id=thread_id,
            name=name,
            description=(body.description or "").strip(),
            provider=provider,
        )
        return {
            "recording": rec.get("status") == "recording",
            "thread_id": thread_id,
            "name": rec.get("name") or name,
            "provider": rec.get("provider"),
            "session_id": rec.get("session_id"),
            "started_at": rec.get("started_at"),
            "max_duration_seconds": rec.get("max_duration_seconds"),
        }

    @router.post("/api/teach-repeat/record/events")
    def append_recording_events(
        request: Request,
        body: AppendRecordingEventsRequest,
    ) -> dict[str, Any]:
        _auth(request)
        thread_id = body.thread_id.strip()
        if not thread_id:
            raise HTTPException(400, "thread_id is required")
        try:
            session = store.append(thread_id, body.events)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {
            "recording": True,
            "thread_id": thread_id,
            "session_id": session.get("session_id"),
            "accepted": session.get("accepted", 0),
            "event_count": session.get("event_count", 0),
            "step_count": session.get("step_count", 0),
        }

    @router.post("/api/teach-repeat/record/stop")
    def stop_recording(request: Request, body: StopRecordingRequest) -> dict[str, Any]:
        _auth(request)
        from runtime.safety.auth.scope import scope_from_request

        scope = scope_from_request(request)
        thread_id = body.thread_id.strip()
        if store.status(thread_id) is None:
            raise HTTPException(404, "No active recording for this thread")
        persist_dir = _require_forge_dependencies()
        rec = store.stop(thread_id)
        if rec is None:
            raise HTTPException(404, "No active recording for this thread")

        template_id = _capture_template(rec)
        recording_fields = {
            "thread_id": thread_id,
            "session_id": rec.get("session_id"),
            "provider": rec.get("provider"),
            "event_count": rec.get("event_count", 0),
            "events_path": rec.get("events_path"),
            "metadata_path": rec.get("metadata_path"),
            "template_id": template_id,
        }

        # The real "recording" is the journal Trajectory react_loop already
        # wrote for this conversation. Forge a reusable skill from it via the
        # active single-demo forge (no min_hits wait). The immune gate still
        # quarantines macros over dangerous primitives for human approval.
        try:
            from runtime.memory.journal.journal import TrajectoryEvent
            from runtime.safety.recovery.skill_forge import SkillForge
            from runtime.safety.recovery.tenant_scope import read_learning_events
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, f"forge unavailable: {exc}") from exc

        trajs = [
            e.trajectory
            for e in read_learning_events(
                journal,
                "trajectory",
                scope=scope,
            )
            if isinstance(e, TrajectoryEvent)
            and getattr(e.trajectory, "thread_id", None) == thread_id
            and e.trajectory.outcome.success
            and not e.trajectory.outcome.degraded
        ]
        if not trajs:
            return {
                "name": rec.get("name"),
                "status": "captured" if template_id else "no_successful_trajectory",
                "forged": [],
                "step_count": rec.get("step_count", 0),
                **recording_fields,
            }

        result = SkillForge(
            journal=journal,
            registry=registry,
            auto_persist_dir=persist_dir,
            scope=scope,
        ).forge_selected(trajs)
        status = (
            "promoted"
            if result.promoted
            else "governed"
            if result.governed
            else "quarantined"
            if result.quarantined
            else "shadow_failed"
            if result.shadow_failed
            else "no_candidate"
        )
        return {
            "name": rec.get("name"),
            "status": status,
            "forged": list(result.promoted),
            "quarantined": list(result.quarantined),
            "governed": list(result.governed),
            "evolution_candidates": list(result.evolution_candidates),
            "candidates_total": result.candidates_total,
            "step_count": sum(t.step_count for t in trajs) + int(rec.get("step_count") or 0),
            **recording_fields,
        }

    @router.get("/api/teach-repeat/record/status")
    def recording_status(request: Request, thread_id: str) -> dict[str, Any]:
        _auth(request)
        rec = store.status(thread_id)
        if rec is None:
            return {"recording": False, "step_count": 0, "name": ""}
        return {
            "recording": rec.get("status") == "recording",
            "step_count": rec.get("step_count", 0),
            "event_count": rec.get("event_count", 0),
            "name": rec.get("name", ""),
            "provider": rec.get("provider"),
            "session_id": rec.get("session_id"),
            "started_at": rec.get("started_at"),
            "max_duration_seconds": rec.get("max_duration_seconds"),
        }

    @router.get("/api/teach-repeat/templates")
    def list_templates(
        request: Request,
        skip: int = 0,
        limit: int = 100,
        search: str = "",
        tag: str = "",
    ) -> dict[str, Any]:
        _auth(request)
        items = list(_TEMPLATES.values())
        if search:
            needle = search.lower()
            items = [
                item
                for item in items
                if needle in str(item.get("name", "")).lower()
                or needle in str(item.get("description", "")).lower()
            ]
        if tag:
            items = [item for item in items if tag in (item.get("tags") or [])]
        total = len(items)
        page = items[skip : skip + limit]
        return {
            "templates": [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "description": item["description"],
                    "step_count": len(item.get("steps") or []),
                    "param_count": len(item.get("params") or []),
                    "tags": item.get("tags") or [],
                    "use_count": item.get("use_count") or 0,
                    "last_used_at": item.get("last_used_at"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "params": item.get("params") or [],
                }
                for item in page
            ],
            "total": total,
        }

    @router.get("/api/teach-repeat/templates/{template_id}")
    def get_template(request: Request, template_id: str) -> dict[str, Any]:
        _auth(request)
        template = _TEMPLATES.get(template_id)
        if template is None:
            raise HTTPException(404, "Template not found")
        return template

    @router.put("/api/teach-repeat/templates/{template_id}")
    def update_template(
        request: Request,
        template_id: str,
        body: TemplateUpdateRequest,
    ) -> dict[str, Any]:
        _auth(request)
        template = _TEMPLATES.get(template_id)
        if template is None:
            raise HTTPException(404, "Template not found")
        if body.name is not None:
            template["name"] = body.name
        if body.description is not None:
            template["description"] = body.description
        if body.tags is not None:
            template["tags"] = body.tags
        template["updated_at"] = _now()
        return template

    @router.delete("/api/teach-repeat/templates/{template_id}")
    def delete_template(request: Request, template_id: str) -> dict[str, Any]:
        _auth(request)
        _TEMPLATES.pop(template_id, None)
        return {"ok": True}

    @router.post("/api/teach-repeat/templates/{template_id}/replay")
    def replay_template(
        request: Request,
        template_id: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _auth(request)
        if template_id not in _TEMPLATES:
            raise HTTPException(404, "Template not found")
        _TEMPLATES[template_id]["use_count"] = (
            int(_TEMPLATES[template_id].get("use_count") or 0) + 1
        )
        _TEMPLATES[template_id]["last_used_at"] = _now()
        return {
            "workflow_id": template_id,
            "status": "completed",
            "step_results": [],
            "params_used": (body or {}).get("params") or {},
            "total_duration_ms": 0,
            "error": None,
            "started_at": _now(),
            "completed_at": _now(),
        }

    @router.post("/api/teach-repeat/templates/{template_id}/replay/adaptive")
    def replay_template_adaptive(
        request: Request,
        template_id: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return replay_template(request, template_id, body)

    @router.post("/api/teach-repeat/templates/{template_id}/duplicate")
    def duplicate_template(request: Request, template_id: str) -> dict[str, Any]:
        _auth(request)
        template = _TEMPLATES.get(template_id)
        if template is None:
            raise HTTPException(404, "Template not found")
        new_id = f"rec_{uuid4().hex[:12]}"
        clone = {
            **template,
            "id": new_id,
            "name": f"{template['name']} copy",
            "created_at": _now(),
            "updated_at": _now(),
        }
        _TEMPLATES[new_id] = clone
        return clone

    return router


__all__ = ["create_teach_repeat_router"]
