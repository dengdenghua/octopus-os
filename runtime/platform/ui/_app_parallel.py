"""Parallel-agent / deep-research / subagent router wiring for ``create_app``.

Extracted from ``app.py`` during the god-file reduction (§2.4 of the
navigation map). Mounts the parallel-agents orchestrator, the
deep-research router, and the subagents router.
"""

from __future__ import annotations

from typing import Any

from ._app_context import AppContext


def mount_parallel(
    ctx: AppContext,
    *,
    parallel_agent_orchestrator: Any,
) -> None:
    """Mount parallel-agents + deep-research + subagents routers.

    The orchestrator backs the ReAct auto-parallel short-circuit too, so the
    app wires it as the single shared instance (``set_auto_parallel_orchestrator``)
    instead of letting ``agent_auto_parallel`` lazily spawn a second one. When
    none is supplied, the shared real-runner orchestrator is used so HTTP-
    triggered parallel / deep-research tasks actually delegate to sub-agents
    rather than the stub runner.
    """
    app = ctx.app

    if parallel_agent_orchestrator is None:
        from runtime.core.cerebrum.agent_auto_parallel import (
            get_auto_parallel_orchestrator,
        )

        parallel_agent_orchestrator = get_auto_parallel_orchestrator()
    else:
        from runtime.core.cerebrum.agent_auto_parallel import (
            set_auto_parallel_orchestrator,
        )

        set_auto_parallel_orchestrator(parallel_agent_orchestrator)
    from runtime.sensing.gateway.parallel_agents_router import create_parallel_agents_router

    app.include_router(
        create_parallel_agents_router(
            orchestrator=parallel_agent_orchestrator,
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )
    app.state.parallel_agent_orchestrator = parallel_agent_orchestrator
    ctx.parallel_agent_orchestrator = parallel_agent_orchestrator

    from runtime.sensing.gateway.deep_research_router import (
        create_deep_research_router,
    )

    app.include_router(
        create_deep_research_router(
            orchestrator=parallel_agent_orchestrator,
            workspace_root=ctx.thread_workspace_root,
            upload_root=ctx.thread_upload_root,
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )

    from runtime.sensing.gateway.subagents_router import create_subagents_router

    app.include_router(
        create_subagents_router(
            registry=ctx.subagent_registry,
            thread_store=ctx.thread_store,
            workspace_root=ctx.thread_workspace_root,
            identity_store=ctx.identity_store,
            require_auth=ctx.require_auth,
            jwt_secret=ctx.jwt_secret,
            jwt_issuer=ctx.jwt_issuer,
            jwt_audience=ctx.jwt_audience,
        )
    )
    app.state.subagent_registry = ctx.subagent_registry
