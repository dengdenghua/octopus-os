"""Route-introspection helpers shared by API surface tests.

starlette >=1.3 wraps included routers in objects that expose ``routes``
but no ``path`` — a flat scan of ``app.routes`` misses everything nested,
so walk the tree instead of assuming every entry is a Route.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


def iter_routes(app_or_router: Any) -> Iterator[Any]:
    """Yield every route object reachable from ``app_or_router``, at any depth."""
    stack = list(getattr(app_or_router, "routes", []) or [])
    seen: set[int] = set()
    while stack:
        route = stack.pop()
        if id(route) in seen:
            continue
        seen.add(id(route))
        yield route
        stack.extend(getattr(route, "routes", []) or [])
        # fastapi >=0.139 include_router wrapper: children hang off
        # original_router, and Mount-style entries expose .app.
        for container_attr in ("original_router", "app"):
            container = getattr(route, container_attr, None)
            if container is not None:
                stack.extend(getattr(container, "routes", []) or [])


def route_paths(app_or_router: Any) -> set[str]:
    """All concrete route paths reachable from the app, nested includes included."""
    return {path for route in iter_routes(app_or_router) if (path := getattr(route, "path", None))}

