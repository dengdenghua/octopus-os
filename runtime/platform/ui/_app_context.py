"""Shared wiring context for :func:`runtime.platform.ui.app.create_app`.

Extracted from ``app.py`` during the god-file reduction. ``create_app``
is broken into a sequence of responsibility-cohesive sub-functions
(``_app_*`` modules); each one reads the shared state it needs from an
:class:`AppContext` and writes back the derived values it produces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AppContext:
    app: Any = None
    state: Any = None
    stack: Any = None
    kernel: Any = None
    paths: Any = None
    project_root: Any = None
    resources_root: Any = None
    trace_store_path: Any = None
    identity_store: Any = None
    require_auth: bool = False
    allow_local_workspace_access: bool = False
    jwt_secret: Any = None
    jwt_issuer: Any = None
    jwt_audience: Any = None
    local_auth_runtime_config: Any = None
    agent_registry: Any = None
    group_registry: Any = None
    channel_manager: Any = None
    thread_store: Any = None
    thread_upload_root: Any = None
    thread_workspace_root: Any = None
    cowork_runtime: Any = None
    stack_mcp_servers: Any = None
    subagent_registry: Any = None
    subagent_runner: Any = None
    parallel_agent_orchestrator: Any = None
    project_store: Any = None
    project_model_router: Any = None
    reflex_router: Any = None
    team_rooms_router: Any = None
    team_tasks_router: Any = None
    realtime_runtime: Any = None
    allow_approval_bypass: bool = False
