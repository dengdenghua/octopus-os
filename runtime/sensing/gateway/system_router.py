"""System-level local maintenance endpoints."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Request

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi

FACTORY_RESET_CONFIRMATION = "RESET ECHO"


def create_system_router(
    *,
    project_root: str | Path | None = None,
    user_home: str | Path | None = None,
    identity_store: Any = None,
    memory_reset_callback: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi(__name__)

    router = APIRouter(tags=["system"])

    def _auth(request: Any) -> str | None:
        from runtime.sensing.gateway.openai_gateway import _resolve_actor

        return _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _resolve_identity(request: Any) -> Any:
        """Resolve full Identity for role checks. None when auth disabled."""
        if not require_auth or identity_store is None:
            return None
        auth = request.headers.get("Authorization") or ""
        if not auth.lower().startswith("bearer "):
            return None
        token = auth[7:].strip()
        if jwt_secret and token.count(".") == 2:
            with contextlib.suppress(Exception):
                identity = identity_store.verify_jwt(
                    token,
                    secret=jwt_secret,
                    required_issuer=jwt_issuer,
                    required_audience=jwt_audience,
                )
                if identity is not None:
                    return identity
        with contextlib.suppress(Exception):
            return identity_store.verify_api_key(token)
        return None

    def _require_admin(request: Any) -> None:
        """Gate for destructive global operations. No-op in dev mode
        (require_auth=False); demands ``admin`` role otherwise."""
        _auth(request)  # AUTH-OK: actor-agnostic — credential check; role check follows below
        if not require_auth:
            return
        identity = _resolve_identity(request)
        roles = getattr(identity, "roles", ()) or ()
        if "admin" not in {str(r).lower() for r in roles}:
            raise HTTPException(403, "admin role required")

    @router.post("/api/system/factory-reset")
    def factory_reset(request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
        # factory-reset is a destructive global operation. Admin only
        # (when require_auth=True); dev mode bypasses for local use.
        _require_admin(request)
        payload = body or {}
        if payload.get("confirm") != FACTORY_RESET_CONFIRMATION:
            raise HTTPException(
                status_code=400,
                detail=f'type "{FACTORY_RESET_CONFIRMATION}" to confirm',
            )

        from runtime.platform.lifecycle.factory_reset import perform_factory_reset
        from runtime.platform.process.paths import project_root as _project_root

        result = perform_factory_reset(
            project_root=project_root or _project_root(),
            user_home=user_home,
            clear_user_install_state=bool(
                payload.get("clear_user_install_state", True),
            ),
        )
        if callable(memory_reset_callback):
            with contextlib.suppress(Exception):
                memory_reset_callback()
        return {
            "ok": result.ok,
            "removed_paths": result.removed_paths,
            "skipped_paths": result.skipped_paths,
            "errors": result.errors,
            "restart_required": True,
        }

    return router


__all__ = ["FACTORY_RESET_CONFIRMATION", "create_system_router"]
