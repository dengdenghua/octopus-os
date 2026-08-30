"""Tests for tool timeout detection guards.

Validates timeout-policy and consecutive-timeout guards that detect when
tools are timing out repeatedly and suggest policy adjustments.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_timeout_guards import (
    _consecutive_timeout_guard,
    _extract_timeout_events,
    _timeout_policy_guard,
)


class TestTimeoutPolicyGuard:
    """Test the windowed timeout-policy guard."""

    def test_no_timeouts(self):
        steps = [
            {"action": "read_file(a.py)", "observation": "content"},
            {"action": "exec_shell(ls)", "observation": "file list"},
            {"action": "write_file(b.py)", "observation": "success"},
        ]
        result = _timeout_policy_guard(steps, None)
        assert result is None

    def test_single_timeout_below_threshold(self):
        steps = [
            {"action": "exec_shell(long_command)", "observation": "Command timed out after 30s"},
            {"action": "read_file(a.py)", "observation": "content"},
        ]
        result = _timeout_policy_guard(steps, None, threshold=2)
        assert result is None

    def test_two_timeouts_fires(self):
        steps = [
            {"action": "exec_shell(cmd1)", "observation": "Execution timed out"},
            {"action": "exec_shell(cmd2)", "observation": "Command timed out"},
        ]
        result = _timeout_policy_guard(steps, None, threshold=2)
        assert result is not None
        assert "exec_shell" in result
        assert "timed out 2 times" in result
        assert "timeout" in result.lower()

    def test_timeout_window_limits_lookback(self):
        steps = [
            {"action": "tool(x)", "observation": "timed out"},
            {"action": "tool(x)", "observation": "timed out"},
            {"action": "other(a)", "observation": "ok"},
            {"action": "other(b)", "observation": "ok"},
            {"action": "other(c)", "observation": "ok"},
            {"action": "other(d)", "observation": "ok"},
            {"action": "other(e)", "observation": "ok"},
        ]
        # With window=5, the 2 timeouts at start are outside the window
        result = _timeout_policy_guard(steps, None, threshold=2, window=5)
        assert result is None

    def test_different_timeout_indicators(self):
        steps = [
            {"action": "tool_a()", "observation": "timed out"},
            {"action": "tool_a()", "observation": "Operation timeout exceeded"},
            {"action": "tool_a()", "observation": "Exceeded time limit of 60s"},
        ]
        result = _timeout_policy_guard(steps, None, threshold=2, window=5)
        # Same tool timing out multiple times with different messages
        assert result is not None
        assert "tool_a" in result

    def test_threshold_tunable(self):
        steps = [
            {"action": "tool(x)", "observation": "timed out"},
            {"action": "tool(y)", "observation": "timed out"},
            {"action": "tool(z)", "observation": "timed out"},
        ]
        # threshold=2 fires
        result = _timeout_policy_guard(steps, None, threshold=2)
        assert result is not None

        # threshold=4 does not fire
        result = _timeout_policy_guard(steps, None, threshold=4)
        assert result is None

    def test_ignores_steps_without_observation(self):
        steps = [
            {"action": "tool(x)", "observation": None},  # No observation
            {"action": "tool(y)", "observation": "timed out"},
        ]
        # Only 1 actual timeout
        result = _timeout_policy_guard(steps, None, threshold=2)
        assert result is None

    def test_same_tool_multiple_timeouts(self):
        steps = [
            {"action": "compile_project()", "observation": "Compilation timed out after 120s"},
            {"action": "read_file(x)", "observation": "content"},
            {"action": "compile_project()", "observation": "timed out"},
        ]
        result = _timeout_policy_guard(steps, None, threshold=2, window=5)
        assert result is not None
        assert "compile_project" in result
        assert "2 times" in result

    def test_internal_recovery_note_does_not_fabricate_timeout(self):
        observation = (
            "(tool failed) No module named pytest\n\n"
            "[red-verification-recovery]\n"
            "For a concurrency-test timeout, audit lock ownership before retrying."
        )
        steps = [
            {"action": "exec_shell(pytest)", "observation": observation},
            {"action": "exec_shell(ruff)", "observation": observation},
        ]

        assert _timeout_policy_guard(steps, None, threshold=2) is None
        assert _consecutive_timeout_guard(steps, None, threshold=2) is None

    def test_non_timeout_execution_metadata_does_not_fabricate_timeout(self):
        observation = (
            '(tool failed) {"execution_policy": {"timeout_s": 60.0, '
            '"result": {"timed_out": false, "error_type": "file_not_found"}}}'
        )
        steps = [
            {"action": "exec_shell(pytest)", "observation": observation},
            {"action": "exec_shell(ruff)", "observation": observation},
        ]

        assert _timeout_policy_guard(steps, None, threshold=2) is None
        assert _consecutive_timeout_guard(steps, None, threshold=2) is None

    def test_parallel_timeout_is_scoped_to_its_own_lane_receipt(self):
        steps = [
            {
                "action": 'read_file({"path":"a"})',
                "actions": [
                    'read_file({"path":"a"})',
                    'read_file({"path":"b"})',
                ],
                "observation": "[1/2] timed out\n\n[2/2] success",
                "action_results": [
                    {"ok": False, "observation": "timed out"},
                    {"ok": True, "observation": "success"},
                ],
            }
        ]

        assert _extract_timeout_events(steps) == [
            ("read_file", True),
            ("read_file", False),
        ]
        assert _timeout_policy_guard(steps, None, threshold=2) is None
        assert _consecutive_timeout_guard(steps, None, threshold=2) is None


class TestConsecutiveTimeoutGuard:
    """Test the stricter consecutive-timeout guard."""

    def test_no_consecutive_timeouts(self):
        steps = [
            {"action": "tool(a)", "observation": "timed out"},
            {"action": "tool(b)", "observation": "success"},
            {"action": "tool(c)", "observation": "timed out"},
        ]
        result = _consecutive_timeout_guard(steps, None, threshold=2)
        assert result is None

    def test_two_consecutive_timeouts_fires(self):
        steps = [
            {"action": "heavy_tool(x)", "observation": "Operation timed out"},
            {"action": "heavy_tool(y)", "observation": "timed out"},
        ]
        result = _consecutive_timeout_guard(steps, None, threshold=2)
        assert result is not None
        assert "heavy_tool" in result
        assert "2 attempts" in result
        assert "MUST stop" in result

    def test_interleaved_success_breaks_streak(self):
        steps = [
            {"action": "tool(a)", "observation": "timed out"},
            {"action": "tool(b)", "observation": "success"},  # Breaks streak
            {"action": "tool(c)", "observation": "timed out"},
        ]
        result = _consecutive_timeout_guard(steps, None, threshold=2)
        assert result is None

    def test_different_tools_consecutive_timeouts(self):
        steps = [
            {"action": "tool_a()", "observation": "timed out"},
            {"action": "tool_b()", "observation": "timeout exceeded"},
        ]
        result = _consecutive_timeout_guard(steps, None, threshold=2)
        assert result is not None
        assert "2 tool calls all timed out" in result
        assert "tool_a" in result or "tool_b" in result

    def test_threshold_configurable(self):
        steps = [
            {"action": "tool(x)", "observation": "timed out"},
            {"action": "tool(y)", "observation": "timed out"},
            {"action": "tool(z)", "observation": "timed out"},
        ]
        # threshold=2 fires
        result = _consecutive_timeout_guard(steps, None, threshold=2)
        assert result is not None

        # threshold=4 does not fire
        result = _consecutive_timeout_guard(steps, None, threshold=4)
        assert result is None

    def test_empty_or_short_trajectory(self):
        assert _consecutive_timeout_guard([], None, threshold=2) is None
        steps = [{"action": "tool(a)", "observation": "timed out"}]
        assert _consecutive_timeout_guard(steps, None, threshold=2) is None


class TestIntegration:
    """Integration tests for both timeout guards."""

    def test_consecutive_stricter_than_windowed(self):
        # 2 consecutive timeouts
        steps = [
            {"action": "tool(x)", "observation": "timed out"},
            {"action": "tool(y)", "observation": "timeout"},
        ]

        # Both should fire
        windowed = _timeout_policy_guard(steps, None, threshold=2, window=5)
        consecutive = _consecutive_timeout_guard(steps, None, threshold=2)

        assert windowed is not None
        assert consecutive is not None
        # Consecutive has stronger wording
        assert "MUST" in consecutive

    def test_windowed_catches_non_consecutive_pattern(self):
        # Timeouts with successes interleaved
        steps = [
            {"action": "tool(a)", "observation": "timed out"},
            {"action": "other(b)", "observation": "ok"},
            {"action": "tool(c)", "observation": "timeout"},
            {"action": "other(d)", "observation": "ok"},
            {"action": "tool(e)", "observation": "timed out"},
        ]

        # Windowed should fire (3 timeouts in window)
        windowed = _timeout_policy_guard(steps, None, threshold=2, window=5)
        assert windowed is not None

        # Consecutive should NOT fire (interleaved successes)
        consecutive = _consecutive_timeout_guard(steps, None, threshold=2)
        assert consecutive is None

    def test_realistic_scenario(self):
        # Model tries compilation multiple times, all timeout
        steps = [
            {"action": "read_file(Makefile)", "observation": "content..."},
            {"action": "exec_shell(make build)", "observation": "Compilation timed out after 120s"},
            {"action": "read_file(config.mk)", "observation": "content..."},
            {"action": "exec_shell(make build --jobs=1)", "observation": "timed out"},
            {
                "action": "exec_shell(make build --quiet)",
                "observation": "Command exceeded time limit",
            },
        ]

        # Should detect the pattern (3 exec_shell timeouts in window of 5)
        windowed = _timeout_policy_guard(steps, None, threshold=2, window=5)
        assert windowed is not None
        assert "exec_shell" in windowed

        # Last 2 tool calls are both timeouts (not counting read_file between them)
        consecutive = _consecutive_timeout_guard(steps, None, threshold=2)
        assert consecutive is not None
        assert "exec_shell" in consecutive

