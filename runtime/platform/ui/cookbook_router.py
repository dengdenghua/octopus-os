"""Local-model cookbook router: hardware-aware recommendations + one-click pull.

``GET /api/cookbook/snapshot`` (public, read-only) returns detected hardware +
ranked model recommendations + in-flight pulls. ``POST /api/cookbook/pull`` is
auth-gated (it triggers a network download / disk write via ollama), mirroring
the SearXNG control router.
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel


class PullRequest(BaseModel):
    tag: str


class DeployRequest(BaseModel):
    plan_id: str


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

    @asynccontextmanager
    async def lifespan(_app):
        from runtime.sensing.model_router import local_ai_deployment

        await asyncio.to_thread(local_ai_deployment.resume_runtime)
        try:
            yield
        finally:
            await asyncio.to_thread(local_ai_deployment.shutdown)

    def _admin_dep(request: Request) -> None:
        from runtime.safety.auth.principal import require_roles

        require_roles(
            request,
            identity_store,
            require_auth,
            ("admin",),
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    router = APIRouter(tags=["cookbook"], lifespan=lifespan)

    @router.post(
        "/api/cookbook/deployment/plan", dependencies=[Depends(_auth_dep), Depends(_admin_dep)]
    )
    def deployment_plan(body: PullRequest) -> dict:
        from runtime.sensing.model_router.local_ai_deployment import plan

        try:
            return plan(body.tag)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post(
        "/api/cookbook/deployment/start", dependencies=[Depends(_auth_dep), Depends(_admin_dep)]
    )
    def deployment_start(body: DeployRequest) -> dict:
        from runtime.sensing.model_router.local_ai_deployment import start

        try:
            return start(body.plan_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/api/cookbook/deployment", dependencies=[Depends(_auth_dep), Depends(_admin_dep)])
    def deployment_status() -> dict:
        from runtime.sensing.model_router.local_ai_deployment import status

        return status()

    @router.get("/api/cookbook/snapshot")
    def snapshot(context_tokens: int = Query(4096, ge=4096, le=32768)) -> dict[str, Any]:
        """Detected hardware + ranked recommendations + ollama availability."""
        from runtime.sensing.model_router.hwfit import cookbook_snapshot

        with contextlib.suppress(Exception):
            return (
                cookbook_snapshot() if context_tokens == 4096 else cookbook_snapshot(context_tokens)
            )
        return {"hardware": None, "ollama_available": False, "recommendations": [], "pulls": {}}

    @router.post(
        "/api/cookbook/pull",
        dependencies=[Depends(_auth_dep), Depends(_operator_dep)],
    )
    def pull(body: PullRequest) -> dict[str, Any]:
        """One-click pull a recommended model via ollama (runs in the background)."""
        from runtime.sensing.model_router.hwfit import start_pull

        result = start_pull(body.tag)
        if result.get("status") == "error":
            raise HTTPException(400, result.get("error"))
        return result

    @router.post(
        "/api/cookbook/verify",
        dependencies=[Depends(_auth_dep), Depends(_operator_dep)],
    )
    def verify(body: PullRequest) -> dict[str, Any]:
        from runtime.sensing.model_router.hwfit import start_verify

        result = start_verify(body.tag)
        if result.get("status") == "error":
            raise HTTPException(400, result.get("error"))
        return result

    return router
