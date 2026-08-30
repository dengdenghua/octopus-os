"""Reflex / gene-locks / forge admin endpoint definitions.

Extracted from ``reflex_admin_router.py`` so the router module stays
small. ``register_reflex_admin_endpoints`` registers every admin
endpoint (and the RecipeForge route aliases) on the given router.

The individual endpoint handlers live in ``_``-prefixed submodules
(``_reflex_admin_stats``, ``_reflex_admin_gene_locks``,
``_reflex_admin_editor``, ``_reflex_admin_gepa``) · this module is
just the orchestration entry point that delegates registration to
each group's builder function.
"""

from __future__ import annotations

from typing import Any

from runtime.platform.ui._reflex_admin_editor import (
    register_reflex_editor_endpoints,
)
from runtime.platform.ui._reflex_admin_gene_locks import (
    register_gene_lock_endpoints,
)
from runtime.platform.ui._reflex_admin_gepa import register_gepa_endpoints
from runtime.platform.ui._reflex_admin_helpers import new_reload_state
from runtime.platform.ui._reflex_admin_stats import (
    register_reflex_stats_endpoints,
)


def register_reflex_admin_endpoints(
    _reflex_admin: Any,
    *,
    stack: Any,
    reflex_router: Any,
    panel_html: str,
    editor_html: str,
) -> None:
    """Register all admin endpoints on ``_reflex_admin``."""
    _reflex_router = reflex_router

    # Module-scope mutable holding the most recent reload diff.
    # Survives across requests · cleared on process restart. The
    # admin panel reads this to render "last reload added X
    # removed Y modified Z" so operators can verify their yaml
    # edit landed correctly.
    _last_reload_state: dict = new_reload_state()

    register_reflex_stats_endpoints(
        _reflex_admin,
        _reflex_router=_reflex_router,
        stack=stack,
        last_reload_state=_last_reload_state,
    )
    register_gene_lock_endpoints(_reflex_admin)
    register_reflex_editor_endpoints(
        _reflex_admin,
        _reflex_router=_reflex_router,
        panel_html=panel_html,
        editor_html=editor_html,
    )
    register_gepa_endpoints(_reflex_admin, stack=stack)
