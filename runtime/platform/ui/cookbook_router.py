"""Local-model cookbook router: hardware-aware recommendations + one-click pull.

``GET /api/cookbook/snapshot`` (public, read-only) returns detected hardware +
ranked model recommendations + in-flight pulls. ``POST /api/cookbook/pull`` is
auth-gated (it triggers a network download / disk write via ollama), mirroring
the SearXNG control router.
"""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel


class PullRequest(BaseModel):
    tag: str


def create_cookbook_router(
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    """Create the ``/api/cookbook/*`` router. The snapshot is public; pull is
    behind the same actor dependency the other mutating routers use."""

    def _auth_dep(request: Request) -> None:
        from runtime.adapters.web_auth import _resolve_actor

        _resolve_actor(  # AUTH-OK: actor-agnostic
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _operator_dep(request: Request) -> None:
        from runtime.safety.auth.principal import require_roles

        require_roles(
            request,
            identity_store,
            require_auth,
            ("admin", "operator"),
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    router = APIRouter(tags=["cookbook"])

    @router.get("/api/cookbook/snapshot")
    def snapshot() -> dict[str, Any]:
        """Detected hardware + ranked recommendations + ollama availability."""
        from runtime.sensing.model_router.hwfit import cookbook_snapshot

        with contextlib.suppress(Exception):
            return cookbook_snapshot()
        return {"hardware": None, "ollama_available": False, "recommendations": [], "pulls": {}}

    @router.post(
        "/api/cookbook/pull",
        dependencies=[Depends(_auth_dep), Depends(_operator_dep)],
    )
    def pull(body: PullRequest) -> dict[str, Any]:
        """One-click pull a recommended model via ollama (runs in the background)."""
        from runtime.sensing.model_router.hwfit import start_pull

        return start_pull(body.tag)

    return router
