"""Shared RecipeForge alias registration helper.

The prompt-evolution subsystem is branded ``RecipeForge``. The
original ``/api/evolution/gepa/...`` paths are kept as aliases so
existing clients / docs / scripts don't break, while the new
``/api/evolution/forge/...`` paths are the public, schema-exposed
ones. This helper centralises the "add alias route + hide the
legacy gepa path from the OpenAPI spec" logic so each GEPA
submodule can register its own aliases without duplicating it.
"""

from __future__ import annotations

from typing import Any


def register_aliases(
    _reflex_admin: Any,
    aliases: list[tuple[str, str, str, Any]],
) -> None:
    """Register ``forge`` route aliases and hide the legacy ``gepa``
    paths from the OpenAPI schema.

    ``aliases`` is a list of ``(method, old_path, new_path, handler)``
    tuples. Each new path is added as a route; the matching legacy
    path is then flipped so it no longer appears in schema docs (but
    stays resolvable by any existing client that hardcoded it).
    """
    for _method, _old, _new, _fn in aliases:
        _reflex_admin.add_api_route(
            _new,
            _fn,
            methods=[_method],
            include_in_schema=True,
        )
        # Hide the legacy ``gepa`` paths from the OpenAPI spec
        # so public docs / SDKs see only the product name.
        # Still resolvable by any existing client that
        # hardcoded them. Iterate over the router's routes
        # and flip include_in_schema on the matching path.
        for _r in _reflex_admin.routes:
            if getattr(_r, "path", None) == _old and _method in getattr(_r, "methods", set()):
                _r.include_in_schema = False
                break
