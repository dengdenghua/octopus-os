"""Meta / MCP / config router wiring for ``create_app``.

Extracted from ``app.py`` during the god-file reduction (§2.7 of the
navigation map). Mounts the meta router (feedback + skills + auth
providers), the MCP server router, and the config router (identity-lock
+ providers + custom-models).
"""

from __future__ import annotations

from pathlib import Path

from ._app_context import AppContext


def mount_meta(
    ctx: AppContext,
    *,
    oct_config: object,
) -> None:
    """Mount the meta / mcp / config routers."""
    app = ctx.app
    state = ctx.state
    stack = ctx.stack

    # ─── Meta router · feedback + skills + auth providers ──
    # These three endpoint groups used to live inline here. Extracted
    # to runtime/sensing/siphon/meta_router.py mid-2026 · same split
    # pattern as config_router. Keeps app.py under 2000 lines and
    # makes the meta surface independently testable.
    from runtime.execution.arms.tool_registry import get_tool_registry
    from runtime.sensing.gateway.meta_router import create_meta_router

    _source_public_skills = Path(__file__).resolve().parents[3] / "skills" / "public"
    _skill_library_dirs = [
        p for p in (ctx.resources_root / "skills" / "public", _source_public_skills) if p.is_dir()
    ]

    app.include_router(
        create_meta_router(
            registry=state.registry,
            tool_registry=get_tool_registry(),
            skill_library_dirs=list(dict.fromkeys(_skill_library_dirs)),
            include_default_skill_library=(state.registry is None or stack is not None),
            oct_config=oct_config,
            local_auth_config=ctx.local_auth_runtime_config,
            identity_store=ctx.identity_store,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
            require_auth=ctx.require_auth,
        )
    )

    # ─── MCP router · declare/enable/disable MCP servers ─────────
    # The entire 220-line block of helpers + endpoints that used to
    # live here (preset dict, account bridge resolution,
    # _register_runtime_mcp, _unregister_runtime_mcp, GET+PUT
    # /api/mcp/config) is now ``runtime/sensing/siphon/mcp_router.py``.
    # The returned bundle carries the live state dicts so future
    # health-endpoint or admin-dashboard code can introspect what's
    # registered without re-doing the spawn bookkeeping.
    from runtime.sensing.gateway.mcp_router import create_mcp_router

    _mcp_bundle = create_mcp_router(
        registry=state.registry,
        initial_mcp_servers=ctx.stack_mcp_servers,
        identity_store=ctx.identity_store,
        require_auth=ctx.require_auth,
        jwt_secret=ctx.jwt_secret,
        jwt_issuer=ctx.jwt_issuer,
        jwt_audience=ctx.jwt_audience,
    )
    app.include_router(_mcp_bundle.router)

    # ─── Config router · identity-lock + providers + custom-models ─
    # These endpoints used to live inline here (~260 lines of nested
    # factories + handler defs). Extracted to
    # runtime/sensing/siphon/config_router.py to shrink this file
    # below the "nobody wants to open this" threshold.
    #
    # The wrapper's ``.custom_models`` attribute is a live reference
    # to the in-memory state the router maintains — app.py's
    # /api/llm-models merge endpoint below reads it directly rather
    # than duplicating persistence logic.
    from runtime.sensing.gateway.config_router import create_config_router

    _config_bundle = create_config_router(
        stack=stack,
        identity_store=ctx.identity_store,
        require_auth=ctx.require_auth,
        jwt_secret=ctx.jwt_secret,
        jwt_issuer=ctx.jwt_issuer,
        jwt_audience=ctx.jwt_audience,
    )
    app.state.codex_account_service = _config_bundle.codex_accounts
    app.state.model_provider_plugins = _config_bundle.model_provider_plugins
    app.include_router(_config_bundle.router)

    # ``/api/llm-models`` (Echo-native presets + custom models)
    # moved into config_router.py · it's registered via the
    # ``_config_bundle.router`` include above · FastAPI picks it
    # before the openai_gateway's /api/llm-models because the
    # config router mounts earlier.
