"""Read-only API for the durable agent trace store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ._agent_trace_router_approvals import register_approvals_endpoints
from ._agent_trace_router_promotion import register_promotion_endpoints
from ._agent_trace_router_review import register_review_endpoints
from ._agent_trace_router_stores import RouterDeps
from ._agent_trace_router_trace import register_trace_endpoints


def create_agent_trace_router(
    *,
    store: Any = None,
    db_path: Path | None = None,
    experience_ledger: Any = None,
    experience_ledger_path: Path | None = None,
    review_queue: Any = None,
    review_queue_path: Path | None = None,
    promotion_audit_path: Path | None = None,
    proposal_ledger_path: Path | None = None,
    approval_policy_path: Path | None = None,
    journal: Any = None,
    registry: Any = None,
    auto_persist_dir: Path | str | None = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:

    def _auth(request: Request, *, force: bool = False) -> str | None:
        from runtime.safety.auth.principal import require_operator, resolve_principal

        effective_require_auth = bool(require_auth or (force and identity_store is not None))
        if effective_require_auth and identity_store is None:
            raise HTTPException(401, "identity store required for agent-trace auth")

        principal = resolve_principal(
            request,
            identity_store,
            effective_require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        if effective_require_auth:
            require_operator(
                request,
                identity_store,
                True,
                jwt_secret=jwt_secret,
                jwt_issuer=jwt_issuer,
                jwt_audience=jwt_audience,
            )
        return principal.actor_id if principal is not None else None

    def _auth_dep(request: Request) -> None:
        _auth(request)

    router = APIRouter(tags=["agent-trace"], dependencies=[Depends(_auth_dep)])

    deps = RouterDeps(
        store=store,
        db_path=db_path,
        experience_ledger=experience_ledger,
        experience_ledger_path=experience_ledger_path,
        review_queue=review_queue,
        review_queue_path=review_queue_path,
        promotion_audit_path=promotion_audit_path,
        proposal_ledger_path=proposal_ledger_path,
        approval_policy_path=approval_policy_path,
        journal=journal,
        registry=registry,
        auto_persist_dir=auto_persist_dir,
        identity_store=identity_store,
        require_auth=require_auth,
        jwt_secret=jwt_secret,
        jwt_issuer=jwt_issuer,
        jwt_audience=jwt_audience,
        auth=_auth,
    )

    register_trace_endpoints(router, deps)
    register_review_endpoints(router, deps)
    register_promotion_endpoints(router, deps)
    register_approvals_endpoints(router, deps)

    return router


__all__ = ["create_agent_trace_router"]
