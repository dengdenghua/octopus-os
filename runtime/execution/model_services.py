"""Host policy for optional unattended model calls, separate from foreground work."""

from __future__ import annotations

from typing import Any


def background_model_calls_enabled(stack: Any) -> bool:
    missing = object()
    if getattr(stack, "background_router", missing) is None:
        return False
    execution = getattr(getattr(stack, "config", None), "execution", None)
    selected = getattr(execution, "background_model_calls", None)
    if isinstance(selected, bool):
        return selected
    # Retain legacy/native policy. Choosing an external member engine does
    # not implicitly authorize a second host model for unattended jobs.
    return getattr(execution, "member_engine", "native") not in {"codex", "opencode"}


def background_model_router(stack: Any) -> Any | None:
    if not background_model_calls_enabled(stack):
        return None
    missing = object()
    router = getattr(stack, "background_router", missing)
    if router is not missing:
        return router
    return getattr(getattr(stack, "planner", None), "router", None)
