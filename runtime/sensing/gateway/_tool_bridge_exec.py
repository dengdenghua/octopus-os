"""Tool execution + semantic error + XML recovery helpers.

Extracted from ``tool_bridge.py`` (the Claude-native agentic loop). This
satellite owns:

* ``_execute_tool_call`` — run one native ``tool_use`` via the executor
  (``execute_step`` path with scope/cwd injection, fallback to direct
  handler invocation for lightweight test doubles);
* ``_is_semantic_error`` — detect a skill that reported failure via its
  return value (``{"ok": False, ...}`` / ``{"error": ...}``);
* ``_recover_named_xml_tool_calls`` — recover explicit ``<tool_call>``
  envelopes from non-compliant providers.

The parent ``tool_bridge`` module re-exports every name here so existing
importers and tests are unchanged.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from runtime.execution.tool_engine import NormalizedToolCall, output_signals_error
from runtime.execution.tool_engine.native_tool_execution import (
    TOOL_OUTPUT_MAX_CHARS,
    execute_native_tool_call,
)
from runtime.execution.tool_engine.tool_output_pruner import TOOL_RESULT_PRUNE_ENABLED
from runtime.execution.tool_engine.tool_output_spill import TOOL_RESULT_SPILL_ENABLED
from runtime.platform.models import ArmId, Budget, TaskId
from runtime.sensing.model_router.models import ToolCall


def _execute_tool_call(
    stack: Any,
    call: ToolCall | NormalizedToolCall | dict[str, Any],
    *,
    task_id: TaskId | None = None,
    step_id: int = 0,
    arm_id: ArmId | None = None,
    budget: Budget | None = None,
) -> tuple[str, bool]:
    """Run one tool_use via the existing executor.

    Returns ``(output_text, is_error)``. The output is shaped for
    direct use as a ``tool_result`` ``content`` field — always a
    string, always bounded in length.
    """
    return execute_native_tool_call(
        stack,
        call,
        max_chars=TOOL_OUTPUT_MAX_CHARS,
        prune_middle=TOOL_RESULT_PRUNE_ENABLED,
        spill_oversized=TOOL_RESULT_SPILL_ENABLED,
        task_id=task_id,
        step_id=step_id,
        arm_id=arm_id,
        budget=budget,
    )


def _is_semantic_error(output: Any) -> bool:
    """Return True when a skill's output structurally signals failure.

    Recognized conventions (dict only · strings / lists / scalars are
    never semantic errors — they're just "output"):

      1. ``{"ok": False, ...}`` · explicit failure flag (most common)
      2. ``{"error": "non-empty string", ...}`` when ``ok`` is absent
         or falsy · some skills skip ``ok`` and only set ``error``
      3. ``{"status": "error"}`` or ``{"status": "failed"}`` · used by
         shell / git wrappers

    Conservative on purpose: a dict with ``{"ok": True, "error": ""}``
    is NOT an error (empty error field). A dict with ``{"ok": True}``
    AND an explicit non-empty ``error`` IS treated as error — rare
    but possible signal of a warning the skill wants to surface.
    """
    return output_signals_error(output)


def _recover_named_xml_tool_calls(
    text: str,
    *,
    allowed_names: set[str],
) -> list[ToolCall]:
    """Recover explicit XML tool envelopes from non-compliant providers.

    This intentionally requires a ``<tool_call...>`` marker and filters every
    recovered name through the already-published tool catalog.  Markdown code
    blocks and ordinary prose are never treated as executable calls here.
    """
    if "<tool_call" not in text.lower():
        return []
    from runtime.core.cerebrum.react_parsing import (
        _extract_tool_actions_from_loose_output,
        _parse_action,
    )

    recovered: list[ToolCall] = []
    for action in _extract_tool_actions_from_loose_output(text):
        parsed = _parse_action(action)
        if parsed is None or parsed[0] not in allowed_names:
            continue
        recovered.append(
            ToolCall(
                id=f"text-tool-{uuid4().hex}",
                name=parsed[0],
                input=parsed[1],
            )
        )
    return recovered
