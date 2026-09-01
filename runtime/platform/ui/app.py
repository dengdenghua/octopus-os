"""FastAPI application factory for the echo Web UI.

``create_app`` was a 2600-line god function. It has been decomposed into
responsibility-cohesive ``_app_*`` submodules in this package; ``create_app``
here is a thin orchestrator that runs them in dependency order and re-exports
the module-level helper names that external callers still import.

Public API (unchanged):
- ``create_app`` — build the wired FastAPI app.
- ``_LEGACY_CONTROL_PLANE_PREFIXES`` / ``_path_matches_prefix`` /
  ``_install_legacy_control_plane_auth`` — legacy control-plane auth.
- ``_find_webui_dist`` — locate the built Vite bundle (re-export).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.execution.suckers import SkillRegistry
from runtime.memory.journal import Journal
from runtime.platform.ui._app_auth import (
    _LEGACY_CONTROL_PLANE_PREFIXES,
    _install_legacy_control_plane_auth,
    _path_matches_prefix,
)
from runtime.platform.ui.webui_static import _find_webui_dist

try:
    from fastapi import FastAPI  # noqa: F401  (re-exported for parity)

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    FastAPI = None  # type: ignore[assignment]

__all__ = [
    "create_app",
    "_LEGACY_CONTROL_PLANE_PREFIXES",
    "_install_legacy_control_plane_auth",
    "_path_matches_prefix",
    "_find_webui_dist",
]


def create_app(
    journal_path: Path | None = None,
    *,
    journal: Journal | None = None,
    registry: SkillRegistry | None = None,
    stack: Any = None,
    kernel: Any = None,
    cocoloop_install_dir: Path | None = None,
    cocoloop_identity_store: Any = None,
    cocoloop_require_auth: bool = False,
    allow_local_workspace_access: bool = False,
    agent_registry: Any = None,
    group_registry: Any = None,
    channel_manager: Any = None,
    oct_config: Any = None,
    oct_link_store: Any = None,
    oct_jwt_secret: str | None = None,
    local_auth_config: Any = None,
    default_arm: str = "code_arm",
    prompt_optimizer: Any = None,
    parallel_agent_orchestrator: Any = None,
    subagent_registry: Any = None,
    server_host: str | None = None,
    server_port: int | None = None,
    frontend_host: str | None = None,
    frontend_port: int | None = None,
    frontend_proxy_target: str | None = None,
    tentacle_enabled: bool = True,
    tentacle_ws_port: int = 8765,
) -> Any:
    """Build the FastAPI application with all routers wired in.

    The body is decomposed into ``_app_*`` submodules driven in dependency
    order through a shared :class:`runtime.platform.ui._app_context.AppContext`.
    ``cocoloop_install_dir`` is preserved for back-compat (no-op).
    """
    if not FASTAPI_AVAILABLE:
        raise RuntimeError(
            "fastapi not installed · `pip install 'fastapi[standard]'` 或 `pip install fastapi uvicorn`"
        )

    # Kernel-first hosts do not need to reach into ``BuiltStack``.  Keep the
    # explicit ``stack`` argument for existing callers while allowing a new
    # host to pass only the embeddable kernel facade.
    if stack is None and kernel is not None:
        stack = kernel.stack
    if kernel is not None:
        # Keep the kernel-only path genuinely self-contained.  These are the
        # same shared objects exposed by BuiltStack, but a new host should not
        # have to unpack the compatibility escape hatch just to create the
        # standard UI transport.
        if journal is None:
            journal = kernel.journal
        if registry is None:
            registry = kernel.registry

    from runtime.platform.ui._app_agents import mount_agents
    from runtime.platform.ui._app_auth_routers import mount_auth_routers
    from runtime.platform.ui._app_collab import mount_collaboration
    from runtime.platform.ui._app_health import mount_health
    from runtime.platform.ui._app_meta import mount_meta
    from runtime.platform.ui._app_pages import mount_pages
    from runtime.platform.ui._app_parallel import mount_parallel
    from runtime.platform.ui._app_reflex import mount_reflex
    from runtime.platform.ui._app_routers import mount_routers_a
    from runtime.platform.ui._app_routers_extra import mount_routers_b
    from runtime.platform.ui._app_setup import setup_app
    from runtime.platform.ui._app_stack import (
        wire_persistent_subagent_runner,
        wire_stack,
    )

    ctx = setup_app(
        journal_path=journal_path,
        journal=journal,
        registry=registry,
        stack=stack,
        kernel=kernel,
        cocoloop_identity_store=cocoloop_identity_store,
        cocoloop_require_auth=cocoloop_require_auth,
        allow_local_workspace_access=allow_local_workspace_access,
        oct_config=oct_config,
        oct_jwt_secret=oct_jwt_secret,
        local_auth_config=local_auth_config,
    )
    # Industry hooks.json bridges (dsh hook-protocol): load unmodified
    # Claude Code / Codex hooks.json files into the hook registry so
    # existing external shell hooks keep working. Best-effort — a missing
    # or malformed config never blocks startup.
    try:
        from runtime.safety.hooks.external_bridge import register_external_hooks

        register_external_hooks()
    except Exception:  # noqa: BLE001 — external hooks are optional
        import logging

        logging.getLogger("runtime.safety.hooks").debug(
            "external hooks registration skipped",
            exc_info=True,
        )
    wire_stack(
        ctx,
        journal_path=journal_path,
        subagent_registry=subagent_registry,
        agent_registry=agent_registry,
    )
    mount_health(
        ctx,
        agent_registry=agent_registry,
        channel_manager=channel_manager,
        group_registry=group_registry,
        server_host=server_host,
        server_port=server_port,
        frontend_host=frontend_host,
        frontend_port=frontend_port,
        frontend_proxy_target=frontend_proxy_target,
    )
    mount_agents(ctx, agent_registry=agent_registry, group_registry=group_registry)
    wire_persistent_subagent_runner(ctx)
    mount_parallel(ctx, parallel_agent_orchestrator=parallel_agent_orchestrator)
    mount_collaboration(
        ctx,
        channel_manager=channel_manager,
        tentacle_enabled=tentacle_enabled,
        tentacle_ws_port=tentacle_ws_port,
    )
    mount_reflex(ctx, default_arm=default_arm, prompt_optimizer=prompt_optimizer)
    mount_auth_routers(
        ctx,
        oct_config=oct_config,
        oct_link_store=oct_link_store,
    )
    mount_pages(ctx)
    mount_meta(ctx, oct_config=oct_config)
    mount_routers_a(ctx, journal_path=journal_path)
    mount_routers_b(ctx, journal_path=journal_path)
    return ctx.app
