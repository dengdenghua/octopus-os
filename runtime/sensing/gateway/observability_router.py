"""
Observability router · journal / reflect / kg / progress / stream / run.

Extracted from ``runtime/platform/ui/app.py`` as part of the
app.py-split campaign. Groups the 6 endpoints the UI's
Observability panel + `/api/stream` SSE feed rely on:

    GET  /api/journal    · event counts + recent tail
    GET  /api/reflect    · kick all 6 reflection producers
    GET  /api/kg         · query the knowledge graph
    GET  /api/progress   · per-task progress snapshots
    GET  /api/stream     · SSE feed of journal appends
    POST /api/run        · quick static-planner probe run

These are the debug / introspection surface. They expose the
journal — which carries file diffs, absolute paths, and task
history — over ``/api/stream`` and ``/api/files/stream``. So they
DO honour ``require_auth`` via a router-level dependency (see
``create_observability_router``): off by default for single-user
dev (no-op, frontend EventSource panel unchanged), enforced as 401
when auth is enabled so a deployed/multi-user server doesn't leak
the whole work log to any anonymous client.

This module is now a thin re-export. The implementation lives in
``_observability_router_factory.py`` (the factory + all endpoint
handlers) and ``_observability_helpers.py`` (module-level helpers,
SSE headers, and the optional-fastapi import guard).
"""

from ._observability_router_factory import create_observability_router

__all__ = ["create_observability_router"]
