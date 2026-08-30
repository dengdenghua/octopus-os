"""Unified control-session API."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from runtime.memory.control_sessions import ControlSessionStore

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ControlSessionBody(BaseModel):
    session_id: str | None = None
    owner_id: str = "agent"
    owner_label: str = "Agent"
    surface: str = "browser"
    target_id: str = "default"
    status: str = "idle"
    metadata: dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: float | None = None
    takeover: bool = False


class ControlSessionStateBody(BaseModel):
    reason: str = ""
    owner_id: str | None = None
    owner_label: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ControlActionBody(BaseModel):
    action_id: str | None = None
    action_type: str = "action"
    descriptor: dict[str, Any] = Field(default_factory=dict)
    status: str = "queued"
    surface: str | None = None
    target_id: str | None = None
    ttl_seconds: float | None = None


class ControlActionUpdateBody(BaseModel):
    status: str
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class ControlEvidenceBody(BaseModel):
    evidence_id: str | None = None
    action_id: str = ""
    kind: str = "log"
    action: str = ""
    ok: bool | None = None
    summary: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: float | None = None


def create_control_sessions_router(
    *,
    store: ControlSessionStore | None = None,
    base_dir: Path | str | None = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    """Create the unified ``/api/control-sessions/*`` router.

    This is the control plane for browser / Chrome / webview / computer
    automation sessions — replay, takeover, pause/stop, and evidence for
    every recorded operator session. It previously had NO auth at all,
    unlike the sibling ``browser_router``/``computer_router`` it now
    fronts. The dependency below closes that gap the same way
    ``browser_router.create_browser_router`` does: a no-op when
    ``require_auth`` is off (default / single-user dev), enforced 401
    across every endpoint when auth is enabled.
    """
    session_store = store or ControlSessionStore(base_dir=base_dir)

    def _auth_dep(request: Request) -> str | None:
        from runtime.adapters.web_auth import _resolve_actor

        return _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    router = APIRouter(
        prefix="/api/control-sessions",
        tags=["control-sessions"],
        dependencies=[Depends(_auth_dep)],
    )

    def _bad_request(exc: ValueError) -> HTTPException:
        return HTTPException(400, str(exc))

    def _is_admin(actor: str | None) -> bool:
        if actor is None or identity_store is None:
            return False
        identity = identity_store.get(actor)
        roles = getattr(identity, "roles", ()) or ()
        return "admin" in {str(role).strip().lower() for role in roles}

    def _owned_or_404(session: dict[str, Any] | None, actor: str | None) -> dict[str, Any]:
        # Object-level ownership gate. A control session drives a real
        # browser/desktop and stores replay screenshots, so it may only be
        # read or driven by the authenticated principal that created it. We
        # raise 404 (not 403) so a non-owner can't even confirm the id exists.
        # Single-user/dev mode (require_auth off → actor is None) keeps the
        # legacy behavior. In shared mode, unowned upgrade rows are visible
        # only to administrators for audit/migration; ordinary users cannot
        # claim them by guessing an id.
        if session is None:
            raise HTTPException(404, "control session not found")
        creator = session.get("creator_actor")
        if actor is not None:
            if creator is None and not _is_admin(actor):
                raise HTTPException(404, "control session not found")
            if creator is not None and creator != actor:
                raise HTTPException(404, "control session not found")
        return session

    def _require_owned(session_id: str, actor: str | None) -> dict[str, Any]:
        try:
            session = session_store.get_session(session_id)
        except ValueError as exc:
            raise _bad_request(exc) from exc
        return _owned_or_404(session, actor)

    @router.get("")
    def list_sessions(
        surface: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        actor: str | None = Depends(_auth_dep),
    ) -> dict[str, Any]:
        try:
            sessions = session_store.list_sessions(
                surface=surface,
                limit=limit,
                creator_actor=actor,
                include_unowned=_is_admin(actor),
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc
        return {"sessions": sessions, "count": len(sessions)}

    @router.post("")
    def create_or_takeover_session(
        body: ControlSessionBody, actor: str | None = Depends(_auth_dep)
    ) -> dict[str, Any]:
        # An existing session may only be re-driven / taken over by its owner;
        # a fresh one records the caller as creator_actor (preserved on update).
        if body.session_id:
            try:
                existing = session_store.get_session(body.session_id)
            except ValueError as exc:
                raise _bad_request(exc) from exc
            if existing is not None:
                _owned_or_404(existing, actor)
        try:
            session = session_store.upsert_session(
                session_id=body.session_id,
                owner_id=body.owner_id,
                owner_label=body.owner_label,
                surface=body.surface,
                target_id=body.target_id,
                status=body.status,
                metadata=body.metadata,
                ttl_seconds=body.ttl_seconds,
                takeover=body.takeover,
                creator_actor=actor,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc
        return {"ok": True, "session": session}

    @router.get("/{session_id}")
    def get_session(session_id: str, actor: str | None = Depends(_auth_dep)) -> dict[str, Any]:
        session = _require_owned(session_id, actor)
        return {"session": session}

    @router.post("/{session_id}/actions")
    def append_action(
        session_id: str, body: ControlActionBody, actor: str | None = Depends(_auth_dep)
    ) -> dict[str, Any]:
        _require_owned(session_id, actor)
        try:
            action = session_store.append_action(
                session_id,
                action_id=body.action_id,
                action_type=body.action_type,
                descriptor=body.descriptor,
                status=body.status,
                surface=body.surface,
                target_id=body.target_id,
                ttl_seconds=body.ttl_seconds,
            )
        except KeyError as exc:
            raise HTTPException(404, "control session not found") from exc
        except ValueError as exc:
            raise _bad_request(exc) from exc
        return {"ok": True, "action": action}

    @router.patch("/{session_id}/actions/{action_id}")
    def update_action(
        session_id: str,
        action_id: str,
        body: ControlActionUpdateBody,
        actor: str | None = Depends(_auth_dep),
    ) -> dict[str, Any]:
        _require_owned(session_id, actor)
        try:
            action = session_store.update_action(
                session_id,
                action_id,
                status=body.status,
                result=body.result,
                error=body.error,
            )
        except KeyError as exc:
            raise HTTPException(404, "control action not found") from exc
        except ValueError as exc:
            raise _bad_request(exc) from exc
        return {"ok": True, "action": action}

    @router.post("/{session_id}/evidence")
    def append_evidence(
        session_id: str, body: ControlEvidenceBody, actor: str | None = Depends(_auth_dep)
    ) -> dict[str, Any]:
        _require_owned(session_id, actor)
        try:
            evidence = session_store.append_evidence(
                session_id,
                evidence_id=body.evidence_id,
                action_id=body.action_id,
                kind=body.kind,
                action=body.action,
                ok=body.ok,
                summary=body.summary,
                detail=body.detail,
                created_at=body.created_at,
            )
        except KeyError as exc:
            raise HTTPException(404, "control session not found") from exc
        except ValueError as exc:
            raise _bad_request(exc) from exc
        return {"ok": True, "evidence": evidence}

    @router.get("/{session_id}/evidence/{evidence_id}/detail")
    def evidence_detail(
        session_id: str, evidence_id: str, actor: str | None = Depends(_auth_dep)
    ) -> dict[str, Any]:
        _require_owned(session_id, actor)
        try:
            return session_store.evidence_detail(session_id, evidence_id)
        except KeyError as exc:
            raise HTTPException(404, "control evidence not found") from exc
        except ValueError as exc:
            raise _bad_request(exc) from exc

    def _set_state(
        session_id: str,
        *,
        status: str,
        body: ControlSessionStateBody,
        actor: str | None,
        takeover: bool = False,
    ) -> dict[str, Any]:
        _require_owned(session_id, actor)
        try:
            session = session_store.set_session_state(
                session_id,
                status=status,
                reason=body.reason,
                owner_id=body.owner_id,
                owner_label=body.owner_label,
                metadata=body.metadata,
                takeover=takeover,
            )
        except KeyError as exc:
            raise HTTPException(404, "control session not found") from exc
        except ValueError as exc:
            raise _bad_request(exc) from exc
        return {"ok": True, "session": session}

    @router.post("/{session_id}/pause")
    def pause_session(
        session_id: str,
        body: ControlSessionStateBody | None = None,
        actor: str | None = Depends(_auth_dep),
    ) -> dict[str, Any]:
        return _set_state(
            session_id, status="paused", body=body or ControlSessionStateBody(), actor=actor
        )

    @router.post("/{session_id}/resume")
    def resume_session(
        session_id: str,
        body: ControlSessionStateBody | None = None,
        actor: str | None = Depends(_auth_dep),
    ) -> dict[str, Any]:
        return _set_state(
            session_id, status="idle", body=body or ControlSessionStateBody(), actor=actor
        )

    @router.post("/{session_id}/stop")
    def stop_session(
        session_id: str,
        body: ControlSessionStateBody | None = None,
        actor: str | None = Depends(_auth_dep),
    ) -> dict[str, Any]:
        return _set_state(
            session_id, status="stopped", body=body or ControlSessionStateBody(), actor=actor
        )

    @router.post("/{session_id}/takeover")
    def takeover_session(
        session_id: str,
        body: ControlSessionStateBody | None = None,
        actor: str | None = Depends(_auth_dep),
    ) -> dict[str, Any]:
        return _set_state(
            session_id,
            status="paused",
            body=body or ControlSessionStateBody(reason="user takeover"),
            actor=actor,
            takeover=True,
        )

    @router.get("/{session_id}/replay")
    def replay_session(
        session_id: str,
        limit: int = Query(default=500, ge=1, le=5000),
        actor: str | None = Depends(_auth_dep),
    ) -> dict[str, Any]:
        _require_owned(session_id, actor)
        try:
            return session_store.replay(session_id, limit=limit)
        except KeyError as exc:
            raise HTTPException(404, "control session not found") from exc
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/{session_id}/timeline")
    def replay_timeline(
        session_id: str,
        limit: int = Query(default=500, ge=1, le=5000),
        after: float = Query(default=0.0, ge=0.0),
        after_cursor: str = "",
        actor: str | None = Depends(_auth_dep),
    ) -> dict[str, Any]:
        _require_owned(session_id, actor)
        try:
            return session_store.timeline(
                session_id,
                limit=limit,
                after=after,
                after_cursor=after_cursor,
            )
        except KeyError as exc:
            raise HTTPException(404, "control session not found") from exc
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/{session_id}/events")
    def session_events(
        session_id: str,
        request: Request,
        after: int = Query(default=0, ge=0),
        actor: str | None = Depends(_auth_dep),
    ) -> StreamingResponse:
        # Gate before the stream opens so a non-owner gets a clean 404 rather
        # than an SSE error frame that would confirm the session exists.
        _require_owned(session_id, actor)

        async def _gen():
            last = after
            try:
                if await asyncio.to_thread(session_store.get_session, session_id) is None:
                    yield 'event: error\ndata: {"error":"control session not found"}\n\n'
                    return
            except ValueError as exc:
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
                return
            for _ in range(120):
                if await request.is_disconnected():
                    break
                events = await asyncio.to_thread(
                    session_store.events_after, session_id, after=last, limit=100
                )
                for event in events:
                    last = int(event["seq"])
                    yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if not events:
                    yield f"event: heartbeat\ndata: {json.dumps({'after': last})}\n\n"
                await asyncio.sleep(1.0)

        return StreamingResponse(_gen(), media_type="text/event-stream", headers=_SSE_HEADERS)

    return router


__all__ = [
    "create_control_sessions_router",
]
