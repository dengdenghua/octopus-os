"""Unit tests for the dsh repeat-tool-reminder advisory guard."""

from __future__ import annotations

from typing import Any

import pytest

from runtime.safety.guards.repeat_tool_reminder import (
    GENTLE_REMINDER,
    RepeatToolReminderConfig,
    RepeatToolReminderGuard,
    build_repeat_tool_reminder,
    canonicalize,
    detailed_reminder,
    preview_arguments,
    validate_thresholds,
)

GENTLE = GENTLE_REMINDER


def _call(
    guard: RepeatToolReminderGuard,
    tool: str,
    args: Any,
    agent: str = "a1",
) -> str | None:
    return guard.observe(tool, args, agent_key=agent)


# ─── thresholds & escalation ───────────────────────────────────


def test_gentle_then_detailed_escalation() -> None:
    guard = RepeatToolReminderGuard(RepeatToolReminderConfig())
    assert _call(guard, "read_file", {"path": "x"}) is None
    assert _call(guard, "read_file", {"path": "x"}) is None
    assert _call(guard, "read_file", {"path": "x"}) == GENTLE
    # The 4th call is not a configured threshold; the 5th is detailed.
    assert _call(guard, "read_file", {"path": "x"}) is None
    detailed = _call(guard, "read_file", {"path": "x"})
    assert detailed is not None
    assert detailed.startswith("[REPEAT-CALL REMINDER] Repeated tool call detected:")
    assert "tool: read_file" in detailed
    assert "consecutive_calls: 5" in detailed
    assert 'arguments: {"path": "x"}' in detailed
    assert "Do not call this tool with" in detailed
    # 6/7 are silent; the 8th is detailed again.
    assert _call(guard, "read_file", {"path": "x"}) is None
    assert _call(guard, "read_file", {"path": "x"}) is None
    assert _call(guard, "read_file", {"path": "x"}) is not None


def test_different_arguments_reset_the_chain() -> None:
    guard = RepeatToolReminderGuard(RepeatToolReminderConfig())
    assert _call(guard, "read_file", {"path": "a"}) is None
    assert _call(guard, "read_file", {"path": "a"}) is None
    # Different args reset to 1 — no reminder at the third call.
    assert _call(guard, "read_file", {"path": "b"}) is None
    assert _call(guard, "read_file", {"path": "b"}) is None
    assert _call(guard, "read_file", {"path": "b"}) == GENTLE


def test_different_tool_resets_the_chain() -> None:
    guard = RepeatToolReminderGuard(RepeatToolReminderConfig())
    assert _call(guard, "read_file", {"path": "a"}) is None
    assert _call(guard, "read_file", {"path": "a"}) is None
    assert _call(guard, "grep", {"pattern": "a"}) is None
    assert _call(guard, "read_file", {"path": "a"}) is None
    assert _call(guard, "read_file", {"path": "a"}) is None
    assert _call(guard, "read_file", {"path": "a"}) == GENTLE


# ─── canonicalization ──────────────────────────────────────────


def test_property_order_does_not_break_the_chain() -> None:
    guard = RepeatToolReminderGuard(RepeatToolReminderConfig())
    assert canonicalize({"b": 1, "a": {"d": [3, {"f": 4, "e": 5}], "c": 2}}) == (
        '{"a": {"c": 2, "d": [3, {"e": 5, "f": 4}]}, "b": 1}'
    )
    assert _call(guard, "t", {"b": 1, "a": 2}) is None
    assert _call(guard, "t", {"a": 2, "b": 1}) is None
    assert _call(guard, "t", {"a": 2, "b": 1}) == GENTLE


def test_non_json_arguments_do_not_crash() -> None:
    guard = RepeatToolReminderGuard(RepeatToolReminderConfig())
    assert _call(guard, "t", {"x": object()}) is None


# ─── include / exclude transparency ────────────────────────────


def test_excluded_tool_is_transparent_to_the_chain() -> None:
    guard = RepeatToolReminderGuard(RepeatToolReminderConfig(exclude=("todo_write",)))
    assert _call(guard, "read_file", {"path": "x"}) is None
    # Bookkeeping interleaved into a loop must not launder it.
    assert _call(guard, "todo_write", {"items": []}) is None
    assert _call(guard, "read_file", {"path": "x"}) is None
    assert _call(guard, "read_file", {"path": "x"}) == GENTLE


def test_include_limits_tracking() -> None:
    guard = RepeatToolReminderGuard(RepeatToolReminderConfig(include=("read_*",)))
    assert _call(guard, "grep", {"pattern": "a"}) is None
    assert _call(guard, "grep", {"pattern": "a"}) is None
    assert _call(guard, "read_file", {"path": "x"}) is None
    assert _call(guard, "read_file", {"path": "x"}) is None
    assert _call(guard, "read_file", {"path": "x"}) == GENTLE


def test_wildcard_metacharacters_are_literal() -> None:
    guard = RepeatToolReminderGuard(RepeatToolReminderConfig(exclude=("mcp.a(b)",)))
    assert guard._tracked("mcp.a(b)") is False  # noqa: SLF001 — unit surface
    assert guard._tracked("mcp.aXb") is True  # noqa: SLF001 — unit surface


# ─── per-agent isolation & reset ───────────────────────────────


def test_chains_are_per_agent() -> None:
    guard = RepeatToolReminderGuard(RepeatToolReminderConfig())
    assert _call(guard, "read_file", {"path": "x"}, agent="one") is None
    assert _call(guard, "read_file", {"path": "x"}, agent="one") is None
    assert _call(guard, "read_file", {"path": "x"}, agent="two") is None
    assert _call(guard, "read_file", {"path": "x"}, agent="one") == GENTLE
    assert guard.chain_counts["two"] == 1


def test_reset_clears_the_chain() -> None:
    guard = RepeatToolReminderGuard(RepeatToolReminderConfig())
    assert _call(guard, "read_file", {"path": "x"}) is None
    assert _call(guard, "read_file", {"path": "x"}) is None
    guard.reset(agent_key="a1")
    assert _call(guard, "read_file", {"path": "x"}) is None
    assert _call(guard, "read_file", {"path": "x"}) is None
    assert _call(guard, "read_file", {"path": "x"}) == GENTLE


# ─── preview cap ───────────────────────────────────────────────


def test_preview_cap_bounds_only_the_reminder_not_detection() -> None:
    guard = RepeatToolReminderGuard(RepeatToolReminderConfig(arguments_preview_chars=20))
    big = {"payload": "x" * 100}
    canonical = canonicalize(big)
    preview = preview_arguments(canonical, 20)
    assert preview == canonical[:20] + f"… (+{len(canonical) - 20} more chars)"
    assert _call(guard, "write", big) is None
    assert _call(guard, "write", big) is None
    assert _call(guard, "write", big) == GENTLE
    assert _call(guard, "write", big) is None
    detailed = _call(guard, "write", big)
    assert detailed is not None and "… (+" in detailed and "more chars)" in detailed
    # Two large payloads differing only after the cap are still distinct.
    guard2 = RepeatToolReminderGuard(RepeatToolReminderConfig(arguments_preview_chars=20))
    assert _call(guard2, "write", {"payload": "a" * 100}) is None
    assert _call(guard2, "write", {"payload": "b" * 100}) is None
    assert _call(guard2, "write", {"payload": "b" * 100}) is None
    assert _call(guard2, "write", {"payload": "b" * 100}) == GENTLE


# ─── config validation (fail-loud) ─────────────────────────────


def test_thresholds_fail_loud() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        validate_thresholds([])
    with pytest.raises(ValueError, match="integer >= 2"):
        validate_thresholds([1])
    with pytest.raises(ValueError, match="integer >= 2"):
        validate_thresholds([2.5])
    with pytest.raises(ValueError, match="must not contain duplicates"):
        validate_thresholds([3, 3])
    # Normalized ascending; the first threshold stays the gentle tier.
    assert validate_thresholds([8, 3, 5]) == [3, 5, 8]


def test_config_from_mapping_rejects_bad_values() -> None:
    with pytest.raises(ValueError, match="thresholds"):
        RepeatToolReminderConfig.from_mapping({"thresholds": "3"})
    with pytest.raises(ValueError, match="include"):
        RepeatToolReminderConfig.from_mapping({"include": [1]})
    with pytest.raises(ValueError, match="arguments_preview_chars"):
        RepeatToolReminderConfig.from_mapping({"arguments_preview_chars": 0})
    with pytest.raises(ValueError, match="arguments_preview_chars"):
        RepeatToolReminderConfig.from_mapping({"arguments_preview_chars": "big"})


def test_custom_first_threshold_keeps_gentle_then_detailed() -> None:
    guard = RepeatToolReminderGuard(RepeatToolReminderConfig(thresholds=(2, 4)))
    assert _call(guard, "t", {"a": 1}) is None
    assert _call(guard, "t", {"a": 1}) == GENTLE
    assert _call(guard, "t", {"a": 1}) is None
    detailed = _call(guard, "t", {"a": 1})
    assert detailed is not None and "consecutive_calls: 4" in detailed


# ─── builder (user_context + env kill-switch) ──────────────────


def test_builder_defaults_to_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECHO_REPEAT_TOOL_REMINDER", raising=False)
    assert build_repeat_tool_reminder({}) is not None
    assert build_repeat_tool_reminder(None) is not None


def test_builder_disables_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECHO_REPEAT_TOOL_REMINDER", raising=False)
    assert build_repeat_tool_reminder({"repeat_tool_reminder": {"enabled": False}}) is None
    monkeypatch.setenv("ECHO_REPEAT_TOOL_REMINDER", "0")
    assert build_repeat_tool_reminder({}) is None


def test_builder_invalid_client_config_degrades_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_REPEAT_TOOL_REMINDER", raising=False)
    guard = build_repeat_tool_reminder({"repeat_tool_reminder": {"thresholds": [0]}})
    assert guard is not None
    assert guard._thresholds == [3, 5, 8]  # noqa: SLF001 — unit surface


def test_detailed_reminder_shape() -> None:
    text = detailed_reminder("exec_shell", 8, '{"command": "ls"}')
    assert text.startswith("[REPEAT-CALL REMINDER] Repeated tool call detected:")
    assert "- tool: exec_shell" in text
    assert "- consecutive_calls: 8" in text

