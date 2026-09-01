"""Repeat tool call detection guards.

Detects when the model calls the same tool repeatedly with identical or
near-identical arguments, indicating it may be stuck in a loop. Injects
a reminder to try a different approach.

DSH feature parity: point P1 repeat-tool-reminder (2026-08-14).
"""

from __future__ import annotations

from collections import Counter
from typing import Any


def _normalize_tool_args(args: dict[str, Any] | str) -> str:
    """Normalize tool arguments to a stable string for deduplication.

    Sorts keys, stringifies values, and truncates long strings to detect
    "essentially the same call" while allowing minor variations.
    """
    if not isinstance(args, dict):
        return str(args)

    parts = []
    for key in sorted(args.keys()):
        value = args[key]
        if isinstance(value, str) and len(value) > 200:
            # Truncate long strings to detect repeated pattern even if
            # the exact value varies slightly
            value = value[:200]
        parts.append(f"{key}={value!r}")
    return ", ".join(parts)


def _extract_tool_calls(steps: list[Any]) -> list[tuple[str, str]]:
    """Extract (tool_name, normalized_args) from trajectory steps.

    Accepts dict-style steps (unit-test fixtures) and ``ReActStep`` objects
    (production trajectory). Returns a list in chronological order of all
    tool calls that completed (successful or failed — both count as "the
    model tried this").
    """
    calls: list[tuple[str, str]] = []
    for step in steps:
        if isinstance(step, dict):
            action = str(step.get("action", "") or "")
            observation = step.get("observation")
            args_dict: Any = step.get("tool_input", {})
            action_blocks = [action] if action else []
        else:
            # Production ReActStep: use per-action blocks when present so
            # multi-action steps are attributed individually.
            action = getattr(step, "action", "") or ""
            observation = getattr(step, "observation", None)
            args_dict = None
            action_blocks = list(getattr(step, "actions", None) or ([action] if action else []))

        if not action_blocks or observation is None:
            continue

        for action_text in action_blocks:
            # Parse action for tool name + args
            # Format: "Action: tool_name(args...)" or "tool_name(args...)"
            action_text = action_text.strip()
            if action_text.startswith("Action:"):
                action_text = action_text[7:].strip()

            # Extract tool name (before first parenthesis)
            paren_idx = action_text.find("(")
            if paren_idx == -1:
                continue

            tool_name = action_text[:paren_idx].strip()

            if args_dict is not None:
                # Dict-style steps carry structured tool_input.
                normalized_args = _normalize_tool_args(args_dict)
            else:
                # ReActStep does not retain structured input; use the raw
                # argument text from the action string as the signature.
                closing = action_text.rfind(")")
                arg_text = action_text[paren_idx + 1 : closing] if closing > paren_idx else ""
                normalized_args = _normalize_tool_args(arg_text)
            calls.append((tool_name, normalized_args))

    return calls


def _repeat_tool_reminder_guard(
    steps: list[Any],
    final_answer: str | None,
    *,
    threshold: int = 3,
    window: int = 5,
) -> str | None:
    """Guard against repeated tool calls with identical arguments.

    Fires when the model has called the same tool with the same (or very
    similar) arguments ``threshold`` times within the last ``window`` steps.

    Args:
        steps: ReAct trajectory steps
        final_answer: The proposed final answer (unused, for signature compat)
        threshold: Number of identical calls to trigger (default 3)
        window: Look-back window in steps (default 5)

    Returns:
        Reminder message if repetition detected, None otherwise.
    """
    if not steps:
        return None

    tool_calls = _extract_tool_calls(steps)
    if len(tool_calls) < threshold:
        return None

    # Look at the last `window` calls
    recent_calls = tool_calls[-window:]

    # Count occurrences of each (tool, args) pair
    call_counts = Counter(recent_calls)

    # Find the most repeated call
    if not call_counts:
        return None

    most_common_call, count = call_counts.most_common(1)[0]

    if count < threshold:
        return None

    tool_name, args_preview = most_common_call

    # Truncate args preview for readability
    if len(args_preview) > 100:
        args_preview = args_preview[:100] + "..."

    return (
        f"You've called `{tool_name}` with similar arguments {count} times "
        f"in the last {len(recent_calls)} steps. This suggests the current "
        f"approach isn't working. Consider:\n"
        f"- Using a different tool to gather information\n"
        f"- Modifying your approach to the problem\n"
        f"- Examining the tool output more carefully for clues\n"
        f"- Asking the user for clarification if you're stuck\n\n"
        f"Last repeated call: {tool_name}({args_preview})"
    )


def _consecutive_same_tool_guard(
    steps: list[Any],
    final_answer: str | None,
    *,
    threshold: int = 3,
) -> str | None:
    """Guard against consecutive calls to the exact same tool+args.

    Stricter than repeat-tool-reminder: detects CONSECUTIVE identical calls,
    which is almost always a sign the model is stuck.

    Args:
        steps: ReAct trajectory steps
        final_answer: The proposed final answer (unused)
        threshold: Number of consecutive calls to trigger (default 3)

    Returns:
        Reminder message if consecutive repetition detected, None otherwise.
    """
    if not steps or len(steps) < threshold:
        return None

    tool_calls = _extract_tool_calls(steps)
    if len(tool_calls) < threshold:
        return None

    # Check last N calls for consecutiveness
    last_n = tool_calls[-threshold:]
    if len(set(last_n)) == 1:
        # All N calls are identical
        tool_name, args_preview = last_n[0]
        if len(args_preview) > 100:
            args_preview = args_preview[:100] + "..."

        return (
            f"You've called `{tool_name}` with identical arguments "
            f"{threshold} times in a row. This approach is clearly not working. "
            f"You MUST try something different:\n"
            f"- Read the error message carefully\n"
            f"- Use a different tool\n"
            f"- Change your arguments significantly\n"
            f"- Reconsider your overall approach\n\n"
            f"Repeated call: {tool_name}({args_preview})"
        )

    return None


__all__ = [
    "_repeat_tool_reminder_guard",
    "_consecutive_same_tool_guard",
]
