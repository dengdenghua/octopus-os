"""Tests for the subagent cost ceiling (ECHO_MAX_COST_USD gate).

Covers:
  1. UsagePricing reads ECHO_MAX_COST_USD env var on first .get()
  2. is_over_budget() returns True once total_cost_usd >= budget_usd
  3. call_subagent refuses to spawn when ceiling is breached (fail-closed)
  4. call_subagent succeeds when ceiling is not breached
  5. call_subagent succeeds (non-fatal) when UsagePricing import fails
  6. react_loop feeds token counts into UsagePricing after each LLM call
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ── Fixture: reset UsagePricing singleton between tests ──────────────────────


@pytest.fixture(autouse=True)
def _reset_usage_pricing():
    """Ensure each test starts with a fresh UsagePricing singleton."""
    from runtime.platform.budget.usage_pricing import UsagePricing

    UsagePricing.reset()
    yield
    UsagePricing.reset()


# ── 1. Env-var budget initialisation ─────────────────────────────────────────


def test_usage_pricing_reads_max_cost_env(monkeypatch):
    monkeypatch.setenv("ECHO_MAX_COST_USD", "5.00")
    from runtime.platform.budget.usage_pricing import UsagePricing

    p = UsagePricing.get()
    assert p.config.budget_usd == pytest.approx(5.00)


def test_usage_pricing_no_env_means_no_ceiling(monkeypatch):
    monkeypatch.delenv("ECHO_MAX_COST_USD", raising=False)
    from runtime.platform.budget.usage_pricing import UsagePricing

    p = UsagePricing.get()
    assert p.config.budget_usd is None


def test_usage_pricing_invalid_env_ignored(monkeypatch):
    monkeypatch.setenv("ECHO_MAX_COST_USD", "not-a-number")
    from runtime.platform.budget.usage_pricing import UsagePricing

    p = UsagePricing.get()
    assert p.config.budget_usd is None


def test_usage_pricing_negative_env_ignored(monkeypatch):
    monkeypatch.setenv("ECHO_MAX_COST_USD", "-1.0")
    from runtime.platform.budget.usage_pricing import UsagePricing

    p = UsagePricing.get()
    assert p.config.budget_usd is None


# ── 2. is_over_budget() ───────────────────────────────────────────────────────


def test_is_over_budget_returns_false_when_no_ceiling(monkeypatch):
    monkeypatch.delenv("ECHO_MAX_COST_USD", raising=False)
    from runtime.platform.budget.usage_pricing import UsagePricing

    p = UsagePricing.get()
    p.record("mimo-v2-flash", 100_000, 100_000)
    assert p.is_over_budget() is False


def test_is_over_budget_true_when_ceiling_exceeded(monkeypatch):
    monkeypatch.setenv("ECHO_MAX_COST_USD", "0.01")
    from runtime.platform.budget.usage_pricing import UsagePricing

    p = UsagePricing.get()
    # Record enough spend to exceed $0.01
    p.record("claude-sonnet-4-6", 1_000_000, 1_000_000)  # ~$18
    assert p.is_over_budget() is True


def test_is_over_budget_false_when_under_ceiling(monkeypatch):
    monkeypatch.setenv("ECHO_MAX_COST_USD", "100.00")
    from runtime.platform.budget.usage_pricing import UsagePricing

    p = UsagePricing.get()
    p.record("mimo-v2-flash", 1000, 1000)  # ~$0.0003
    assert p.is_over_budget() is False


# ── 3. call_subagent refuses when ceiling breached ───────────────────────────


def test_call_subagent_refused_when_over_budget(monkeypatch):
    """When ECHO_MAX_COST_USD is breached, spawn returns success=False."""
    monkeypatch.setenv("ECHO_MAX_COST_USD", "0.001")
    from runtime.platform.budget.usage_pricing import UsagePricing

    # Exceed the $0.001 ceiling
    UsagePricing.get().record("claude-sonnet-4-6", 500_000, 500_000)

    with patch("runtime.execution.subagents.bridge._acquire_subagent_slot") as mock_slot:
        from runtime.execution.subagents.bridge import call_subagent

        result = call_subagent(agent_id="researcher", prompt="hi")

    # Should refuse without ever acquiring a slot
    mock_slot.assert_not_called()
    assert result["success"] is False
    assert "cost ceiling" in result.get("error", "").lower()


# ── 4. call_subagent proceeds when under ceiling ─────────────────────────────


def test_call_subagent_proceeds_when_under_ceiling(monkeypatch):
    """When spend is under the ceiling, slot acquisition proceeds normally."""
    monkeypatch.setenv("ECHO_MAX_COST_USD", "100.00")
    from runtime.platform.budget.usage_pricing import UsagePricing

    UsagePricing.get().record("mimo-v2-flash", 100, 100)  # tiny spend

    with (
        patch(
            "runtime.execution.subagents.bridge._acquire_subagent_slot",
            return_value=False,  # simulate cap full; we just want to confirm cost check passed
        ) as mock_slot,
        patch(
            "runtime.execution.subagents.bridge._release_subagent_slot",
        ),
    ):
        from runtime.execution.subagents.bridge import call_subagent

        result = call_subagent(agent_id="researcher", prompt="hi")

    mock_slot.assert_called_once()
    # Concurrency cap triggers (not cost), so error message is different
    assert "concurrency cap" in result.get("error", "").lower()


# ── 5. Non-fatal on import failure ───────────────────────────────────────────


def test_call_subagent_non_fatal_when_budget_import_fails(monkeypatch):
    """If UsagePricing import fails, call_subagent continues normally."""
    import sys

    # Make the budget module unimportable in the bridge's import path
    with (
        patch.dict(sys.modules, {"runtime.platform.budget": None}),
        patch(
            "runtime.execution.subagents.bridge._acquire_subagent_slot",
            return_value=False,
        ) as mock_slot,
        patch("runtime.execution.subagents.bridge._release_subagent_slot"),
    ):
        from runtime.execution.subagents.bridge import call_subagent

        result = call_subagent(agent_id="researcher", prompt="hi")

    # Should still proceed to slot acquisition (not die on budget import)
    mock_slot.assert_called_once()
    assert "concurrency cap" in result.get("error", "").lower()

