"""
Pure-function formatters for the OpenAI-compat gateway.

Extracted from ``openai_gateway.py`` as part of the file's gradual
decomposition. Hosts the 5 formatting helpers that turn Step /
ArmResult / Trajectory objects into the strings and dicts the
gateway streams to the client:

    _summarize_step_for_stream  · single-step human-readable line
    _pick_preview_keys          · prioritize input-arg keys for display
    _pick_output_keys           · prioritize output keys for display
    _short                      · string truncation with ellipsis
    _chat_completion_envelope   · non-streaming ChatCompletion shape

Zero runtime dependencies · zero I/O · testable in isolation. This
separation lets ``test_openai_formatting.py`` pin the display-layer
shape without spinning up a full FastAPI stack.

Why not collapse them into one big format() method:
    The 5 functions compose in different orders in different
    contexts (stream chunk line · non-stream envelope · fallback
    reply). Keeping them as tiny building blocks is what lets
    each call site pick what it needs. Inlining would duplicate
    the prioritization rules.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

# The pure Step → display formatters moved to platform.step_format (the
# channels adapter needs them too, and importing from the gateway made
# adapters depend upward on sensing). Re-exported here so the gateway's
# existing ``from ..openai_formatting import ...`` call sites keep working.
from runtime.platform.step_format import (
    _ARGS_PRIORITY,
    _OUTPUT_PRIORITY,
    _output_indicates_error,
    _pick_output_keys,
    _pick_preview_keys,
    _short,
    step_effective_success,
    summarize_step_for_stream,
)


def chat_completion_envelope(
    reply: str,
    *,
    model: str,
    actor: str | None = None,
    agent: Any = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap a plain assistant reply string in the OpenAI-compat
    ``chat.completion`` response shape.

    Used by the gateway when the planner can't build a TaskGraph
    and we fall back to direct LLM · the response needs to look
    like a "normal" openai completion so clients don't 400. The
    ``echo`` meta field flags that this was a fallback ·
    frontend debug panels read it to distinguish "plan was empty"
    from "plan ran but produced nothing".
    """
    meta: dict[str, Any] = {
        "strategy": "direct_llm_fallback",
        "step_count": 0,
        "success": True,
    }
    if actor is not None:
        meta["actor"] = actor
    if agent is not None:
        meta["agent"] = agent.agent_id
    if extra:
        meta.update(extra)
    return {
        "id": f"chatcmpl-{uuid4().hex[:16]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "echo": meta,
    }


__all__ = [
    "summarize_step_for_stream",
    "chat_completion_envelope",
    # Underscore-prefixed helpers exported so tests can exercise
    # them directly · callers outside this module should prefer
    # the two public names above.
    "_short",
    "_pick_preview_keys",
    "_pick_output_keys",
    "_output_indicates_error",
    "step_effective_success",
    "_ARGS_PRIORITY",
    "_OUTPUT_PRIORITY",
]
