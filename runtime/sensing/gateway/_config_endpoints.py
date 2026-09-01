"""
Endpoint handlers for the config router.

Split out of config_router.py (pure structural refactor — no logic
changes). ``_build_endpoints`` registers every config endpoint on a
router, reading shared state through an injected context bundle so
the handlers stay free of module-level globals — the same
"own-your-state" principle the router factory already followed.

The per-domain handlers live in sibling ``_config_endpoints_*.py``
modules: ``_register_*`` functions that attach their endpoints to the
injected router and read shared state through the injected
``_ConfigCtx``. This module keeps ``_ConfigCtx`` and ``_build_endpoints``
and delegates to them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from runtime.sensing.gateway._config_endpoints_codex import _register_coder_codex
from runtime.sensing.gateway._config_endpoints_custom_models import (
    _register_custom_models,
)
from runtime.sensing.gateway._config_endpoints_local_models import (
    _register_local_models,
)
from runtime.sensing.gateway._config_endpoints_models import _register_models
from runtime.sensing.gateway._config_endpoints_security import (
    _register_security,
)
from runtime.sensing.gateway._config_endpoints_system import _register_system


@dataclass
class _ConfigCtx:
    """Shared state + helpers the handlers need, injected by the router.

    ``router`` carries the APIRouter the handlers attach to; the rest
    are the stateful helpers the router factory owns (persistence,
    dispatcher registration, auth gate) plus the execution ``stack``
    the judge endpoints read.
    """

    router: Any
    custom_models: dict[str, dict[str, Any]]
    custom_models_snapshot: Callable[[], dict[str, dict[str, Any]]]
    # ``save(*model_ids)`` merges over the file on disk rather than
    # overwriting it with our snapshot, so callers must name the ids they
    # mutated. An id held in memory but not named is left as-is on disk;
    # an id named but absent from memory is deleted from disk.
    save: Callable[..., None]
    load: Callable[[], None]
    register: Callable[[dict[str, Any]], dict[str, Any]]
    unregister_entry: Callable[..., bool]
    rebuild_routes: Callable[[], dict[str, dict[str, Any]]]
    serialize_custom_models: Callable[[Callable[..., Any]], Callable[..., Any]]
    require_admin: Callable[[Request], None]
    stack: Any
    codex_accounts: Any
    codex_preferences: Any
    codex_updates: Any


def _build_endpoints(ctx: _ConfigCtx) -> None:
    router = ctx.router

    # ═══ Endpoints ═══════════════════════════════════════════

    _register_security(router, ctx)
    _register_models(router, ctx)
    _register_custom_models(router, ctx)
    _register_local_models(router, ctx)
    _register_system(router, ctx)
    _register_coder_codex(router, ctx)


__all__ = ["_ConfigCtx", "_build_endpoints"]
