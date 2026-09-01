"""Provider-neutral actor context for model-router calls.

The ``current_actor`` ContextVar carries the human / API identity into the
model call path so account-backed routers can attribute usage and enforce
per-actor access without coupling callers to a specific provider.
"""

from __future__ import annotations

import contextvars

current_actor: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "model_router_current_actor",
    default=None,
)
