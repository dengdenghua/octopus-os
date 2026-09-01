"""
Ambient Suggestions router · ``/api/ambient-suggestions/*``.

Endpoints
---------

    GET    /api/ambient-suggestions              list for a project
    POST   /api/ambient-suggestions/run          regenerate via LLM
    PATCH  /api/ambient-suggestions/{id}         mark accepted / dismissed
    DELETE /api/ambient-suggestions              clear (optional filter)

All endpoints gate on ``feature_flags.is_on("ui.ambient_suggestions")``.
When the flag is off, GET returns an empty bucket (so the frontend
can no-op render without a separate 404 path) and mutating endpoints
return ``403 ambient_suggestions_disabled``.

Design notes
------------
``project`` query / body parameter is required — the storage layout
is strictly per-project. We resolve the path before hitting storage
so a malformed value surfaces as 400, not a late IO error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request


def _resolve_project(raw: Any) -> str:
    if raw is None:
        raise HTTPException(400, "project is required")
    text = str(raw).strip()
    if not text:
        raise HTTPException(400, "project must be non-empty")
    return str(Path(text).resolve())


def _require_flag() -> None:
    """Block mutations when the flag is off. GET degrades gracefully
    instead of 403-ing because a cold frontend shouldn't break just
    because the flag hasn't been turned on yet.
    """
    from runtime.platform import feature_flags as _ff

    if not _ff.is_on("ui.ambient_suggestions"):
        raise HTTPException(
            403,
            detail={
                "error": "ambient_suggestions_disabled",
                "hint": "set feature flag 'ui.ambient_suggestions' to enable",
            },
        )


def create_ambient_suggestions_router(
    *,
    base_dir: Path | None = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    """Factory. ``base_dir`` override is for tests; production uses
    ``<data>/ambient_suggestions/`` via the module default."""

    def _operator_dep(request: Request) -> None:
        from runtime.safety.auth.principal import require_operator

        # Suggestions currently use process-global buckets keyed by a
        # caller-supplied host path.  Until storage is tenant-scoped, keep the
        # entire surface operational-only in shared deployments.
        require_operator(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    router = APIRouter(dependencies=[Depends(_operator_dep)])

    @router.get("/api/ambient-suggestions")
    def list_suggestions(project: str, locale: str | None = None) -> dict[str, Any]:
        from runtime.memory import ambient_suggestions as _amb
        from runtime.platform import feature_flags as _ff

        proj = _resolve_project(project)
        if not _ff.is_on("ui.ambient_suggestions"):
            # Return a well-formed empty bucket so the UI doesn't
            # have to special-case the off state.
            return {
                "project_root": proj,
                "generated_at": "",
                "suggestions": [],
                "enabled": False,
            }
        bucket = _amb.read_bucket(proj, base_dir=base_dir, locale=locale)
        bucket["enabled"] = True
        return bucket

    @router.post("/api/ambient-suggestions/run")
    def run_generator(
        request: Request,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _require_flag()
        payload = body or {}
        proj = _resolve_project(payload.get("project"))
        agent_id = str(payload.get("agent_id") or "").strip()
        if not agent_id:
            raise HTTPException(400, "agent_id is required")
        model = payload.get("model")
        turn_window = int(payload.get("turn_window") or 15)
        locale = str(payload.get("locale") or "en-US")
        title_lookup_raw = payload.get("title_lookup")
        title_lookup: dict[str, str] | None = None
        if isinstance(title_lookup_raw, dict):
            title_lookup = {str(k): str(v) for k, v in title_lookup_raw.items()}

        from runtime.memory import ambient_suggestions as _amb
        from runtime.safety.auth.scope import scope_from_request

        return _amb.generate_suggestions(
            proj,
            agent_id,
            turn_window=turn_window,
            model=model if isinstance(model, str) and model else None,
            base_dir=base_dir,
            title_lookup=title_lookup,
            locale=locale,
            scope=scope_from_request(request),
        )

    @router.patch("/api/ambient-suggestions/{suggestion_id}")
    def patch_status(
        suggestion_id: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _require_flag()
        payload = body or {}
        proj = _resolve_project(payload.get("project"))
        status = str(payload.get("status") or "").strip().lower()
        if status not in {"pending", "accepted", "dismissed"}:
            raise HTTPException(
                400,
                "status must be one of pending / accepted / dismissed",
            )

        from runtime.memory import ambient_suggestions as _amb

        updated = _amb.mark_status(
            proj,
            suggestion_id,
            status,
            base_dir=base_dir,
        )
        if updated is None:
            raise HTTPException(
                404,
                f"suggestion {suggestion_id!r} not found",
            )
        return {"suggestion": updated}

    @router.delete("/api/ambient-suggestions")
    def delete_suggestions(
        project: str,
        status: str | None = None,
    ) -> dict[str, Any]:
        _require_flag()
        proj = _resolve_project(project)
        only_status = status.strip().lower() if isinstance(status, str) and status else None
        if only_status is not None and only_status not in {
            "pending",
            "accepted",
            "dismissed",
        }:
            raise HTTPException(
                400,
                "status filter must be one of pending / accepted / dismissed",
            )
        from runtime.memory import ambient_suggestions as _amb

        removed = _amb.clear(proj, base_dir=base_dir, only_status=only_status)
        return {"removed": removed}

    return router


__all__ = ["create_ambient_suggestions_router"]
