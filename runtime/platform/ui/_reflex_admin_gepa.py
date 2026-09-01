"""GEPA / RecipeForge (prompt-evolution) admin endpoints.

Extracted from ``_reflex_admin_endpoints.py`` so the router module
stays small. ``register_gepa_endpoints`` registers every GEPA /
RecipeForge endpoint (and the RecipeForge route aliases) on the
given router.

The implementation is split across responsibility-focused
submodules (run / apply / runs / variants / auto-tick) so this
module stays a thin dispatcher. Each submodule registers its own
endpoints plus the matching ``/api/evolution/forge/...`` aliases.
"""

from __future__ import annotations

from typing import Any

from runtime.platform.ui._reflex_admin_gepa_apply import register_gepa_apply
from runtime.platform.ui._reflex_admin_gepa_autotick import register_gepa_autotick
from runtime.platform.ui._reflex_admin_gepa_run import register_gepa_run
from runtime.platform.ui._reflex_admin_gepa_runs import register_gepa_runs
from runtime.platform.ui._reflex_admin_gepa_variants import register_gepa_variants


def register_gepa_endpoints(_reflex_admin: Any, *, stack: Any) -> None:
    """Register the GEPA / RecipeForge admin endpoints."""
    register_gepa_run(_reflex_admin, stack=stack)
    register_gepa_apply(_reflex_admin, stack=stack)
    register_gepa_runs(_reflex_admin, stack=stack)
    register_gepa_variants(_reflex_admin, stack=stack)
    register_gepa_autotick(_reflex_admin, stack=stack)
