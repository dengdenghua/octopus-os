"""Factory for the observability router.

Pure structural extraction from ``observability_router.py`` (no logic
changes). Hoists the ``create_observability_router`` factory into its own
module so the public ``observability_router`` module stays a thin re-export.

The factory is now a thin orchestrator: it builds the shared
``ObservabilityContext``, wires the router-level auth dependency, and
delegates endpoint registration to the extracted builder submodules
(``_observability_journal``, ``_observability_kg``,
``_observability_progress_stream``, ``_observability_rollback_panels``).
"""

from __future__ import annotations

from typing import Any

from runtime.sensing._fastapi_guard import require_fastapi

from ._observability_auth import make_auth_dep
from ._observability_helpers import APIRouter, Depends
from ._observability_journal import register_journal_endpoints
from ._observability_kg import register_kg_endpoints
from ._observability_progress_stream import register_progress_stream_endpoints
from ._observability_rollback_panels import register_rollback_panels_endpoints
from ._observability_state import ObservabilityContext


def create_observability_router(
    *,
    journal: Any,
    registry: Any,
    planner: Any = None,
    effect_store: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    """Build the router.

    Parameters
    ----------
    journal :
        Must be the SAME Journal instance the rest of the runtime
        writes to · ``/api/stream`` subscribes to its append
        callback and ``/api/progress`` builds a TaskProgressTracker
        off it.
    registry :
        SkillRegistry · only touched by ``/api/run`` to execute a
        static probe skill and by ``_skill_forge_stub`` to enumerate
        skills during the reflect pass.
    planner :
        Optional LLMPlanner · enables the cheap
        ``/api/evolution/status`` endpoint that reads in-memory
        ``learned_rules_section`` + ``learned_memories_section`` and
        counts react_loop trajectories. None → endpoint returns
        ``{"enabled": False}`` (planner-less test harness).
    """
    require_fastapi(__name__)

    ctx = ObservabilityContext(
        journal=journal,
        registry=registry,
        planner=planner,
        effect_store=effect_store,
        identity_store=identity_store,
        require_auth=require_auth,
        jwt_secret=jwt_secret,
        jwt_issuer=jwt_issuer,
        jwt_audience=jwt_audience,
    )

    router = APIRouter(tags=["observability"], dependencies=[Depends(make_auth_dep(ctx))])

    register_journal_endpoints(router, ctx)
    register_kg_endpoints(router, ctx)
    register_progress_stream_endpoints(router, ctx)
    register_rollback_panels_endpoints(router, ctx)

    return router


__all__ = ["create_observability_router"]
