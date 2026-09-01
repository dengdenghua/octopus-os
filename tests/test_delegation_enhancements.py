"""Regression tests for delegation_skills enhancements (2026-06).

Pins 3 operator-facing improvements:
  1. Timeout raised from 300s → 900s (gives ~15 rounds instead of 5)
  2. Custom agent_id fallback (sleep_researcher_eight → researcher)
  3. Retry-once on transient failures (timeout / connection errors)

These tests mock call_subagent so they run fast without spawning real
subagents. The contract is that delegation_skills calls call_subagent
with the right arguments and handles results correctly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest


@pytest.fixture
def mock_subagent():
    """Mock runtime.execution.subagents.call_subagent at the source."""
    with patch("runtime.execution.subagents.call_subagent") as m:
        yield m


@pytest.fixture
def mock_builtins():
    """Mock _allowed_agent_ids to return a known set."""
    allowed = {"general", "researcher", "debugger", "explorer", "reviewer"}
    with patch(
        "runtime.execution.suckers.delegation_skills._allowed_agent_ids",
        return_value=allowed,
    ):
        yield allowed


# ── Timeout default ───────────────────────────────────────


def test_call_agent_uses_900s_timeout_by_default(mock_subagent, mock_builtins):
    """_call_agent default timeout_s = 900 (was 300 before 2026-06)."""
    from runtime.execution.suckers.delegation_skills import _call_agent

    mock_subagent.return_value = {
        "agent_id": "researcher",
        "output": "done",
        "success": True,
    }

    result = _call_agent(agent_id="researcher", prompt="test task")

    assert result["success"] is True
    mock_subagent.assert_called_once()
    # Check the timeout_s kwarg passed to call_subagent
    call_kwargs = mock_subagent.call_args[1]
    assert call_kwargs["timeout_s"] == 900


def test_call_agent_parallel_uses_900s_timeout_by_default(mock_subagent, mock_builtins):
    """_call_agent_parallel default timeout_s = 900."""
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    mock_subagent.return_value = {
        "agent_id": "researcher",
        "output": "done",
        "success": True,
    }

    result = _call_agent_parallel(
        specs=[{"agent_id": "researcher", "prompt": "test"}],
    )

    assert result["ok"] is True
    assert result["success_count"] == 1
    # First call was the single spec
    call_kwargs = mock_subagent.call_args[1]
    assert call_kwargs["timeout_s"] == 900


def test_call_agent_respects_explicit_timeout(mock_subagent, mock_builtins):
    """User-provided timeout_s overrides the default."""
    from runtime.execution.suckers.delegation_skills import _call_agent

    mock_subagent.return_value = {
        "agent_id": "researcher",
        "output": "done",
        "success": True,
    }

    _call_agent(agent_id="researcher", prompt="test", timeout_s=1800)

    call_kwargs = mock_subagent.call_args[1]
    assert call_kwargs["timeout_s"] == 1800


# ── Custom agent_id fallback ──────────────────────────────


def test_custom_agent_id_resolves_to_researcher(mock_subagent, mock_builtins):
    """sleep_researcher_eight → researcher (research-shaped name)."""
    from runtime.execution.suckers.delegation_skills import _call_agent

    mock_subagent.return_value = {
        "agent_id": "researcher",
        "output": "done",
        "success": True,
    }

    result = _call_agent(
        agent_id="sleep_researcher_eight",
        prompt="Investigate sleep patterns",
    )

    assert result["success"] is True
    # Response should show the ORIGINAL custom name to the operator
    assert result["agent_id"] == "sleep_researcher_eight"
    assert result["resolved_to"] == "researcher"
    assert result["custom_role"] == "sleep_researcher_eight"
    # call_subagent was invoked with the RESOLVED builtin
    call_args = mock_subagent.call_args
    assert call_args[1]["agent_id"] == "researcher"
    # Prompt was wrapped with role label
    prompt = call_args[1]["prompt"]
    assert "sleep_researcher_eight" in prompt
    assert "Role:" in prompt


def test_custom_agent_id_debugger_shape(mock_subagent, mock_builtins):
    """kyc_debugger_v2 → debugger (debug-shaped name)."""
    from runtime.execution.suckers.delegation_skills import _call_agent

    mock_subagent.return_value = {
        "agent_id": "debugger",
        "output": "fixed",
        "success": True,
    }

    result = _call_agent(agent_id="kyc_debugger_v2", prompt="Fix KYC bug")

    assert result["success"] is True
    assert result["agent_id"] == "kyc_debugger_v2"
    assert result["resolved_to"] == "debugger"
    assert mock_subagent.call_args[1]["agent_id"] == "debugger"


def test_custom_agent_id_generic_fallback(mock_subagent, mock_builtins):
    """unknown_shape_foo → explorer/researcher/general (first available)."""
    from runtime.execution.suckers.delegation_skills import _call_agent

    mock_subagent.return_value = {
        "agent_id": "explorer",
        "output": "done",
        "success": True,
    }

    result = _call_agent(agent_id="custom_task_alpha", prompt="Do thing")

    assert result["success"] is True
    # Fallback order: explorer, researcher, general
    resolved = result.get("resolved_to")
    assert resolved in {"explorer", "researcher", "general"}


def test_custom_agent_id_parallel(mock_subagent, mock_builtins):
    """Custom names work in _call_agent_parallel too."""
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    mock_subagent.return_value = {
        "agent_id": "researcher",
        "output": "result",
        "success": True,
    }

    result = _call_agent_parallel(
        specs=[
            {"agent_id": "sleep_researcher_one", "prompt": "Task A"},
            {"agent_id": "sleep_researcher_two", "prompt": "Task B"},
        ],
    )

    assert result["ok"] is True
    assert result["success_count"] == 2
    # Both should have been resolved to researcher
    assert mock_subagent.call_count == 2


def test_builtin_agent_id_not_wrapped(mock_subagent, mock_builtins):
    """Real builtin names (researcher) skip the fallback logic."""
    from runtime.execution.suckers.delegation_skills import _call_agent

    mock_subagent.return_value = {
        "agent_id": "researcher",
        "output": "done",
        "success": True,
    }

    result = _call_agent(agent_id="researcher", prompt="Do research")

    assert result["success"] is True
    # No custom_role injected when the name is already a builtin
    assert "custom_role" not in result
    assert "resolved_to" not in result
    # Prompt NOT wrapped
    prompt = mock_subagent.call_args[1]["prompt"]
    assert "Role:" not in prompt


# ── Retry on transient failure ────────────────────────────


def test_retry_on_timeout_success(mock_subagent, mock_builtins):
    """Timeout on first call, success on retry → return success."""
    from runtime.execution.suckers.delegation_skills import _call_agent

    # First call times out
    # Second call (retry) succeeds
    mock_subagent.side_effect = [
        {
            "agent_id": "researcher",
            "output": "",
            "success": False,
            "error": "TimeoutError: timed out after 900s",
            "error_type": "timeout",
        },
        {
            "agent_id": "researcher",
            "output": "recovered",
            "success": True,
        },
    ]

    result = _call_agent(agent_id="researcher", prompt="test")

    assert result["success"] is True
    assert result["output"] == "recovered"
    assert result.get("retried") is True
    assert mock_subagent.call_count == 2


def test_retry_on_connection_error(mock_subagent, mock_builtins):
    """ConnectionError triggers retry."""
    from runtime.execution.suckers.delegation_skills import _call_agent

    mock_subagent.side_effect = [
        {
            "agent_id": "researcher",
            "output": "",
            "success": False,
            "error": "ConnectionError: connection refused",
            "error_type": "ConnectionError",
        },
        {
            "agent_id": "researcher",
            "output": "fixed",
            "success": True,
        },
    ]

    result = _call_agent(agent_id="researcher", prompt="test")

    assert result["success"] is True
    assert mock_subagent.call_count == 2


def test_retry_on_both_fail(mock_subagent, mock_builtins):
    """Both attempts timeout → return failure with retry note."""
    from runtime.execution.suckers.delegation_skills import _call_agent

    mock_subagent.side_effect = [
        {
            "agent_id": "researcher",
            "output": "",
            "success": False,
            "error": "timeout on first",
            "error_type": "timeout",
        },
        {
            "agent_id": "researcher",
            "output": "",
            "success": False,
            "error": "timeout on retry",
            "error_type": "timeout",
        },
    ]

    result = _call_agent(agent_id="researcher", prompt="test")

    assert result["success"] is False
    assert result.get("retried") is True
    # Error message combines both failures
    assert "timeout on first" in result["error"]
    assert "retry also failed" in result["error"]
    assert mock_subagent.call_count == 2


def test_no_retry_on_structural_failure(mock_subagent, mock_builtins):
    """Structural failures (bad prompt, unknown role) don't retry."""
    from runtime.execution.suckers.delegation_skills import _call_agent

    mock_subagent.return_value = {
        "agent_id": "researcher",
        "output": "",
        "success": False,
        "error": "ValueError: malformed prompt",
        "error_type": "ValueError",
    }

    result = _call_agent(agent_id="researcher", prompt="")

    assert result["success"] is False
    # No retry — only 1 call
    assert mock_subagent.call_count == 1
    assert "retried" not in result


def test_retry_parallel_per_spec(mock_subagent, mock_builtins):
    """Parallel: each spec retries independently."""
    from runtime.execution.suckers.delegation_skills import _call_agent_parallel

    # Spec A: timeout → success on retry
    # Spec B: success immediately
    call_count = {"n": 0}

    def side_effect_fn(*args: Any, **kwargs: Any) -> dict[str, Any]:
        call_count["n"] += 1
        aid = kwargs.get("agent_id")
        if aid == "researcher" and call_count["n"] == 1:
            # First call to researcher times out
            return {
                "agent_id": "researcher",
                "output": "",
                "success": False,
                "error": "timeout",
                "error_type": "timeout",
            }
        # All other calls succeed
        return {
            "agent_id": aid,
            "output": "done",
            "success": True,
        }

    mock_subagent.side_effect = side_effect_fn

    result = _call_agent_parallel(
        specs=[
            {"agent_id": "researcher", "prompt": "A"},
            {"agent_id": "debugger", "prompt": "B"},
        ],
    )

    assert result["ok"] is True
    assert result["success_count"] == 2
    # researcher called twice (timeout + retry), debugger called once
    assert mock_subagent.call_count == 3


# ── Backward compat / edge cases ──────────────────────────


def test_unknown_custom_name_no_fallback_available(mock_subagent):
    """If no builtins exist at all, custom name fails gracefully."""
    from runtime.execution.suckers.delegation_skills import _call_agent

    # Mock _allowed_agent_ids to return empty set
    with patch(
        "runtime.execution.suckers.delegation_skills._allowed_agent_ids",
        return_value=set(),
    ):
        result = _call_agent(agent_id="custom_foo", prompt="test")

    assert result["success"] is False
    # Either "no fallback" or "unknown subagent" depending on path
    err = result["error"].lower()
    assert "unknown" in err or "no fallback" in err


def test_budget_exhausted_no_retry(mock_subagent, mock_builtins):
    """Budget exhaustion doesn't trigger retry logic."""
    from runtime.execution.suckers.delegation_skills import _call_agent

    # Mock _check_absolute_cap to return exhausted budget
    # (5/5, within=False)
    with patch(
        "runtime.execution.suckers.delegation_skills._check_absolute_cap",
        return_value=(5, False),
    ):
        result = _call_agent(agent_id="researcher", prompt="test")

    assert result["success"] is False
    assert "budget exhausted" in result["error"]
    # call_subagent never invoked
    mock_subagent.assert_not_called()
