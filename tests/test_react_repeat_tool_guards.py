"""Tests for repeat tool call guards.

Validates DSH-style repeat-tool-reminder and consecutive-same-tool guards
that detect when the model is stuck in a loop calling the same tool repeatedly.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_repeat_tool_guards import (
    _consecutive_same_tool_guard,
    _extract_tool_calls,
    _normalize_tool_args,
    _repeat_tool_reminder_guard,
)
from runtime.core.cerebrum.react_types import ReActStep


class TestNormalizeToolArgs:
    """Test argument normalization for deduplication."""

    def test_simple_args(self):
        args = {"command": "ls -la", "timeout": 30}
        result = _normalize_tool_args(args)
        assert "command=" in result
        assert "timeout=" in result
        # Keys are sorted
        assert result.index("command=") < result.index("timeout=")

    def test_long_string_truncation(self):
        long_text = "x" * 500
        args = {"content": long_text}
        result = _normalize_tool_args(args)
        # Should be truncated to 200 chars
        assert len(result) < 250  # "content=" + 200 chars + quotes

    def test_stable_ordering(self):
        args1 = {"b": 2, "a": 1}
        args2 = {"a": 1, "b": 2}
        assert _normalize_tool_args(args1) == _normalize_tool_args(args2)

    def test_non_dict_args(self):
        result = _normalize_tool_args("string")
        assert result == "string"


class TestRepeatToolReminderGuard:
    """Test the windowed repeat-tool-reminder guard."""

    def test_no_repetition(self):
        steps = [
            {
                "action": "read_file(foo.py)",
                "observation": "content",
                "tool_input": {"path": "foo.py"},
            },
            {
                "action": "read_file(bar.py)",
                "observation": "content",
                "tool_input": {"path": "bar.py"},
            },
            {"action": "exec_shell(ls)", "observation": "output", "tool_input": {"command": "ls"}},
        ]
        result = _repeat_tool_reminder_guard(steps, None)
        assert result is None

    def test_three_identical_calls_fires(self):
        steps = [
            {
                "action": "read_file(foo.py)",
                "observation": "content",
                "tool_input": {"path": "foo.py"},
            },
            {
                "action": "read_file(foo.py)",
                "observation": "content",
                "tool_input": {"path": "foo.py"},
            },
            {
                "action": "read_file(foo.py)",
                "observation": "content",
                "tool_input": {"path": "foo.py"},
            },
        ]
        result = _repeat_tool_reminder_guard(steps, None, threshold=3)
        assert result is not None
        assert "read_file" in result
        assert "3 times" in result
        assert "approach" in result.lower()

    def test_window_limits_lookback(self):
        # 3 identical calls at the beginning, 5 different calls after
        steps = [
            {"action": "read_file(foo.py)", "observation": "x", "tool_input": {"path": "foo.py"}},
            {"action": "read_file(foo.py)", "observation": "x", "tool_input": {"path": "foo.py"}},
            {"action": "read_file(foo.py)", "observation": "x", "tool_input": {"path": "foo.py"}},
            {"action": "read_file(a.py)", "observation": "x", "tool_input": {"path": "a.py"}},
            {"action": "read_file(b.py)", "observation": "x", "tool_input": {"path": "b.py"}},
            {"action": "read_file(c.py)", "observation": "x", "tool_input": {"path": "c.py"}},
            {"action": "read_file(d.py)", "observation": "x", "tool_input": {"path": "d.py"}},
            {"action": "read_file(e.py)", "observation": "x", "tool_input": {"path": "e.py"}},
        ]
        # With window=5, the 3 repeated calls at start are outside the window
        result = _repeat_tool_reminder_guard(steps, None, threshold=3, window=5)
        assert result is None

    def test_threshold_tunable(self):
        steps = [
            {"action": "exec_shell(ls)", "observation": "x", "tool_input": {"command": "ls"}},
            {"action": "exec_shell(ls)", "observation": "x", "tool_input": {"command": "ls"}},
        ]
        # threshold=2 should fire
        result = _repeat_tool_reminder_guard(steps, None, threshold=2)
        assert result is not None
        assert "exec_shell" in result

        # threshold=3 should not fire
        result = _repeat_tool_reminder_guard(steps, None, threshold=3)
        assert result is None

    def test_ignores_steps_without_observation(self):
        steps = [
            {"action": "read_file(foo.py)", "observation": None},  # No observation = didn't execute
            {
                "action": "read_file(foo.py)",
                "observation": "content",
                "tool_input": {"path": "foo.py"},
            },
            {
                "action": "read_file(foo.py)",
                "observation": "content",
                "tool_input": {"path": "foo.py"},
            },
        ]
        # Only 2 actually executed
        result = _repeat_tool_reminder_guard(steps, None, threshold=3)
        assert result is None

    def test_similar_but_different_args_detected(self):
        # Long strings truncated to 200 chars, so if they only differ
        # after char 200, they'll normalize to the same signature
        long_base = "x" * 200  # Exactly at truncation boundary
        steps = [
            {
                "action": "write_file(f)",
                "observation": "ok",
                "tool_input": {"content": long_base + "aaa"},
            },
            {
                "action": "write_file(f)",
                "observation": "ok",
                "tool_input": {"content": long_base + "bbb"},
            },
            {
                "action": "write_file(f)",
                "observation": "ok",
                "tool_input": {"content": long_base + "ccc"},
            },
        ]
        result = _repeat_tool_reminder_guard(steps, None, threshold=3)
        # These should be detected as similar because content is truncated at 200 chars
        assert result is not None
        assert "write_file" in result


class TestConsecutiveSameToolGuard:
    """Test the stricter consecutive-repetition guard."""

    def test_no_consecutive_repetition(self):
        steps = [
            {"action": "read_file(a.py)", "observation": "x", "tool_input": {"path": "a.py"}},
            {"action": "exec_shell(ls)", "observation": "x", "tool_input": {"command": "ls"}},
            {"action": "read_file(a.py)", "observation": "x", "tool_input": {"path": "a.py"}},
        ]
        result = _consecutive_same_tool_guard(steps, None, threshold=3)
        assert result is None

    def test_three_consecutive_identical_fires(self):
        steps = [
            {"action": "exec_shell(ls)", "observation": "x", "tool_input": {"command": "ls"}},
            {"action": "exec_shell(ls)", "observation": "x", "tool_input": {"command": "ls"}},
            {"action": "exec_shell(ls)", "observation": "x", "tool_input": {"command": "ls"}},
        ]
        result = _consecutive_same_tool_guard(steps, None, threshold=3)
        assert result is not None
        assert "exec_shell" in result
        assert "3 times in a row" in result
        assert "MUST try something different" in result

    def test_interleaved_calls_not_consecutive(self):
        steps = [
            {"action": "read_file(a.py)", "observation": "x", "tool_input": {"path": "a.py"}},
            {"action": "read_file(a.py)", "observation": "x", "tool_input": {"path": "a.py"}},
            {
                "action": "exec_shell(ls)",
                "observation": "x",
                "tool_input": {"command": "ls"},
            },  # Breaks streak
            {"action": "read_file(a.py)", "observation": "x", "tool_input": {"path": "a.py"}},
        ]
        result = _consecutive_same_tool_guard(steps, None, threshold=3)
        assert result is None

    def test_threshold_configurable(self):
        steps = [
            {"action": "tool(x)", "observation": "y", "tool_input": {"arg": "x"}},
            {"action": "tool(x)", "observation": "y", "tool_input": {"arg": "x"}},
        ]
        # threshold=2 fires
        result = _consecutive_same_tool_guard(steps, None, threshold=2)
        assert result is not None

        # threshold=3 does not fire
        result = _consecutive_same_tool_guard(steps, None, threshold=3)
        assert result is None

    def test_empty_or_short_trajectory(self):
        assert _consecutive_same_tool_guard([], None, threshold=3) is None
        steps = [{"action": "read_file(a)", "observation": "x", "tool_input": {}}]
        assert _consecutive_same_tool_guard(steps, None, threshold=3) is None


class TestIntegration:
    """Integration tests combining both guards."""

    def test_consecutive_stricter_than_windowed(self):
        # 3 consecutive identical calls
        steps = [
            {"action": "tool(x)", "observation": "y", "tool_input": {"arg": "x"}},
            {"action": "tool(x)", "observation": "y", "tool_input": {"arg": "x"}},
            {"action": "tool(x)", "observation": "y", "tool_input": {"arg": "x"}},
        ]

        # Both should fire
        windowed = _repeat_tool_reminder_guard(steps, None, threshold=3, window=5)
        consecutive = _consecutive_same_tool_guard(steps, None, threshold=3)

        assert windowed is not None
        assert consecutive is not None
        # Consecutive has stricter wording
        assert "in a row" in consecutive
        assert "MUST" in consecutive

    def test_windowed_catches_non_consecutive_pattern(self):
        # Repeated but not consecutive
        steps = [
            {"action": "read_file(a)", "observation": "x", "tool_input": {"path": "a"}},
            {"action": "exec_shell(ls)", "observation": "x", "tool_input": {"command": "ls"}},
            {"action": "read_file(a)", "observation": "x", "tool_input": {"path": "a"}},
            {"action": "exec_shell(pwd)", "observation": "x", "tool_input": {"command": "pwd"}},
            {"action": "read_file(a)", "observation": "x", "tool_input": {"path": "a"}},
        ]

        # Windowed should fire (3 read_file(a) in window)
        windowed = _repeat_tool_reminder_guard(steps, None, threshold=3, window=5)
        assert windowed is not None
        assert "read_file" in windowed

        # Consecutive should NOT fire (interleaved)
        consecutive = _consecutive_same_tool_guard(steps, None, threshold=3)
        assert consecutive is None


class TestReActStepCompatibility:
    """Production trajectory steps are ReActStep objects, not dicts."""

    @staticmethod
    def _step(action: str, observation: str = "x") -> ReActStep:
        return ReActStep(
            iteration=0,
            thought="",
            action=action,
            observation=observation,
            actions=[action],
        )

    def test_react_step_repetition_detected(self):
        steps = [
            self._step("read_file(foo.py)"),
            self._step("read_file(foo.py)"),
            self._step("read_file(foo.py)"),
        ]
        result = _repeat_tool_reminder_guard(steps, None, threshold=3)
        assert result is not None
        assert "read_file" in result
        assert "3 times" in result

    def test_react_step_different_args_not_false_positive(self):
        steps = [
            self._step("read_file(a.py)"),
            self._step("read_file(b.py)"),
            self._step("read_file(c.py)"),
        ]
        result = _repeat_tool_reminder_guard(steps, None, threshold=3)
        assert result is None

    def test_react_step_consecutive_fires(self):
        steps = [
            self._step("exec_shell(ls)"),
            self._step("exec_shell(ls)"),
            self._step("exec_shell(ls)"),
        ]
        result = _consecutive_same_tool_guard(steps, None, threshold=3)
        assert result is not None
        assert "3 times in a row" in result

    def test_react_step_multi_action_blocks(self):
        steps = [
            ReActStep(
                iteration=0,
                thought="",
                action="read_file(a.py); exec_shell(ls)",
                observation="x",
                actions=["read_file(a.py)", "exec_shell(ls)"],
            ),
            ReActStep(
                iteration=1,
                thought="",
                action="read_file(a.py); exec_shell(ls)",
                observation="x",
                actions=["read_file(a.py)", "exec_shell(ls)"],
            ),
            ReActStep(
                iteration=2,
                thought="",
                action="read_file(a.py); exec_shell(ls)",
                observation="x",
                actions=["read_file(a.py)", "exec_shell(ls)"],
            ),
        ]
        calls = _extract_tool_calls(steps)
        assert ("read_file", "a.py") in calls
        assert ("exec_shell", "ls") in calls
        result = _repeat_tool_reminder_guard(steps, None, threshold=3)
        assert result is not None

