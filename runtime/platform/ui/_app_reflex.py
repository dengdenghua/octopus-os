"""Reflex / thread-state / OpenAI-gateway / reflex-admin wiring.

Extracted from ``app.py`` during the god-file reduction (§2.7 of the
navigation map). Builds the shared SpinalCord reflex router, mounts the
thread-state CRUD routes, the OpenAI-compatible gateway, and the reflex
admin endpoints.
"""

from __future__ import annotations

from typing import Any

from runtime.platform.ui.pages import (
    _REFLEX_EDITOR_HTML,
    _REFLEX_PANEL_HTML,
)

from ._app_context import AppContext


def mount_reflex(
    ctx: AppContext,
    *,
    default_arm: str,
    prompt_optimizer: Any,
) -> None:
    """Mount the reflex / thread-state / openai-gateway / reflex-admin routers."""
    app = ctx.app
    stack = ctx.stack

    # ─── SpinalCord reflex layer · shared by both gateways ──────
    # Built once and threaded into both /v1/chat/completions AND the
    # Earlier this was only wired into the OpenAI-compat router, AND
    # even that wiring was missing the actual instance · so reflex
    # never fired in production. Building it here makes it a single
    # injection point for future rule additions.
    from runtime.platform.ui.thread_routes import (
        build_reflex_router,
        mount_thread_state_routes,
    )

    _reflex_router = build_reflex_router(stack)
    _realtime_logs_root = ctx.paths.data_dir / "threads"

    # Thread state CRUD for sidebars and scope settings. Live turns use
    # the realtime WebSocket mounted below.
    mount_thread_state_routes(
        app,
        thread_store=ctx.thread_store,
        logs_root=_realtime_logs_root,
        identity_store=ctx.identity_store,
        require_auth=ctx.require_auth,
        allow_local_workspace_access=ctx.allow_local_workspace_access,
        jwt_secret=ctx.jwt_secret,
        jwt_issuer=ctx.jwt_issuer,
        jwt_audience=ctx.jwt_audience,
        workspace_root=ctx.thread_workspace_root,
        group_store=(
            getattr(ctx.cowork_runtime, "group_store", None)
            if ctx.cowork_runtime is not None
            else None
        ),
        collaboration_store=(
            getattr(ctx.cowork_runtime, "collaboration_store", None)
            if ctx.cowork_runtime is not None
            else None
        ),
        team_rooms_router=ctx.team_rooms_router,
    )

    if stack is not None:
        from runtime.sensing.gateway.openai_gateway_router import create_openai_router

        app.include_router(
            create_openai_router(
                stack,
                default_arm=default_arm,
                identity_store=ctx.identity_store,
                require_auth=ctx.require_auth,
                jwt_secret=ctx.jwt_secret,
                jwt_issuer=ctx.jwt_issuer,
                jwt_audience=ctx.jwt_audience,
                agent_registry=ctx.agent_registry,
                reflex_router=_reflex_router,
                prompt_optimizer=prompt_optimizer,
            )
        )

    # Reflex admin endpoints: stats, hot-reload, gene-locks, and forge APIs.
    from runtime.platform.ui.reflex_admin_router import mount_reflex_admin_routes

    mount_reflex_admin_routes(
        app,
        stack=stack,
        reflex_router=_reflex_router,
        panel_html=_REFLEX_PANEL_HTML,
        editor_html=_REFLEX_EDITOR_HTML,
        identity_store=ctx.identity_store,
        require_auth=ctx.require_auth,
        jwt_secret=ctx.jwt_secret,
        jwt_issuer=ctx.jwt_issuer,
        jwt_audience=ctx.jwt_audience,
    )

    ctx.reflex_router = _reflex_router
