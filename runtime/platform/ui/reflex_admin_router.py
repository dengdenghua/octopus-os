"""Reflex, gene-locks, and forge admin routes for the UI app."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from runtime.platform.ui._reflex_admin_endpoints import (
    register_reflex_admin_endpoints,
)


def mount_reflex_admin_routes(
    app: Any,
    *,
    stack: Any,
    reflex_router: Any,
    panel_html: str,
    editor_html: str,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> None:
    """Mount optional Reflex admin routes when a reflex router exists."""
    if reflex_router is None:
        return

    def _operator_dep(request: Request) -> None:
        from runtime.safety.auth.principal import require_operator

        require_operator(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    _reflex_admin = APIRouter(
        tags=["reflex-admin"],
        dependencies=[Depends(_operator_dep)],
    )
    register_reflex_admin_endpoints(
        _reflex_admin,
        stack=stack,
        reflex_router=reflex_router,
        panel_html=panel_html,
        editor_html=editor_html,
    )
    app.include_router(_reflex_admin)
