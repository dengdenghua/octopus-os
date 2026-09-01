"""
Agents router · public factory for the ``/api/agents`` surface.

A pure structural split of the former monolithic ``agents_router.py``
(no logic changes). The endpoint handlers live in ``_agents_endpoints.py``
(registered via ``_build_endpoints`` with an injected ``_AgentsCtx``), and
the stateless helpers (soul / avatar / visual resolution, wire-model
converters) live in ``_agents_helpers.py``. Pydantic wire models were
already split into ``agents_models.py``.

This module keeps the closure factory ``create_agents_router`` and
re-exports the public names moved into the submodules so existing
callers (``runtime/platform/ui/app.py``, tests) keep working unchanged.
"""

from __future__ import annotations

from typing import Any

try:
    from fastapi import APIRouter

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi

from ._agents_endpoints import _AgentsCtx, _build_endpoints
from ._agents_helpers import (
    _avatar_url_for,  # noqa: F401 — re-exported for realtime_team_stream / _team_stream_group_fanout
)
from .agents_models import (
    AgentDetailWire,
    AgentVisualsWire,
    AgentWire,
    ArmOptionWire,
    ArmWire,
    CapabilitiesWire,
    CreateAgentRequest,
    GenerateAgentVisualsRequest,
    GroupCreate,
    GroupUpdate,
    GroupWire,
    PauseTaskBody,
    ResumeTaskBody,
    ToolRegistryWire,
    UpdateAgentRequest,
)


def create_agents_router(
    *,
    registry: Any,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    journal: Any = None,
    group_registry: Any = None,
    runtime: Any = None,
    thread_store: Any = None,
    allow_local_workspace_access: bool = False,
) -> Any:
    require_fastapi(__name__)

    router = APIRouter(tags=["agents"])

    _build_endpoints(
        _AgentsCtx(
            router=router,
            registry=registry,
            identity_store=identity_store,
            require_auth=require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
            journal=journal,
            group_registry=group_registry,
            runtime=runtime,
            thread_store=thread_store,
            allow_local_workspace_access=allow_local_workspace_access,
        )
    )

    return router


__all__ = [
    "AgentDetailWire",
    "AgentVisualsWire",
    "AgentWire",
    "ArmOptionWire",
    "ArmWire",
    "CapabilitiesWire",
    "CreateAgentRequest",
    "GenerateAgentVisualsRequest",
    "GroupCreate",
    "GroupUpdate",
    "GroupWire",
    "PauseTaskBody",
    "ResumeTaskBody",
    "ToolRegistryWire",
    "UpdateAgentRequest",
    "create_agents_router",
    "_avatar_url_for",
]
