"""
Canonical model resolver for a chat turn.

Why this exists
---------------

Over the project's life the frontend has shipped model selection in at
least four different body locations, and each time a new code path
forgets to check the latest one. We just burned three rounds debugging
"the UI picked claude-mirror but the planner used a different fallback" because
the field it looked at was empty even though one of the *other* known
fields had the value.

The known candidates, highest precedence first:

    1. ``body.context.model_name``   — current React client (use-stream)
    2. ``body.context.model``        — older context field name
    3. ``body.model``                — OpenAI-compat clients hitting
                                       ``/v1/chat/completions``
    4. ``thread.metadata.model_name`` — sticky selection on the thread
    5. ``agent.model``               — agent's preferred model
    6. None                          — planner falls back to its default

Every turn-level consumer should call ``resolve_turn_model(...)``
instead of reading ``body.get("model")`` directly. Adding a new
candidate is a one-line change here and all consumers pick it up.

The function returns a plain model-id string (or None). Resolution is
deliberately cheap · no I/O, no side effects.
"""

from __future__ import annotations

from typing import Any


def _first_nonempty_string(*values: Any) -> str | None:
    """Return the first argument that is a non-empty string · else None."""
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def resolve_turn_model(
    body: dict[str, Any] | None = None,
    thread: dict[str, Any] | None = None,
    agent: Any = None,
) -> str | None:
    """Return the model-id for this turn, or None to let the planner
    fall back to its default.

    See module docstring for the search order.
    """
    body = body or {}
    thread = thread or {}
    ctx = body.get("context") if isinstance(body.get("context"), dict) else {}
    if not isinstance(ctx, dict):
        ctx = {}
    md = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
    if not isinstance(md, dict):
        md = {}
    agent_model = getattr(agent, "model", None) if agent is not None else None
    return _first_nonempty_string(
        ctx.get("model_name"),
        ctx.get("model"),
        body.get("model"),
        md.get("model_name"),
        md.get("model"),
        agent_model,
    )


__all__ = ["resolve_turn_model"]
