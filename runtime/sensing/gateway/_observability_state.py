"""Shared state container for the observability router endpoint groups.

Pure structural extraction from ``_observability_router_factory.py`` (no
logic changes). Bundles the runtime state that the factory captured in its
closure — journal, registry, planner, effect store, identity store and the
auth parameters — so the extracted endpoint builder functions can receive
one coherent context instead of a long positional list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ObservabilityContext:
    """Shared runtime state threaded through the observability builders."""

    journal: Any
    registry: Any
    planner: Any = None
    effect_store: Any = None
    identity_store: Any = None
    require_auth: bool = False
    jwt_secret: str | None = None
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    thread_store: Any = None
    workspace_root: Any = None
    allow_local_workspace_access: bool = False
    task_supervisor: Any = None
    # Optional ``Callable[[str], str]``: takes the diagnosis prompt built by
    # ``runtime.platform.observability.crash_reporter.analyze`` and returns
    # free text. Supplied by the app when an LLM is available; the crash
    # endpoints degrade to the local rule-based verdict when it is absent,
    # so an offline appliance still gets a usable answer.
    crash_explainer: Any = None


__all__ = ["ObservabilityContext"]
