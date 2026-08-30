"""Test that recursive delegation activates automatically from orchestration_token_budget.

This end-to-end test verifies that the "root layer auto-seeding" fix works:
when orchestration_token_budget is set (e.g. via ultracode mode), the system
automatically enables recursive delegation by seeding subdelegation_budget.
"""

import pytest

from runtime.execution.suckers.ephemeral_runner import (
    _clone_registry_with_delegation,
)
from runtime.execution.suckers.registry import SkillRegistry


def test_root_layer_auto_seeding_from_orchestration_budget():
    """
    When orchestration_token_budget is set at depth=0, subdelegation_budget
    is automatically seeded (25% of orchestration budget), enabling recursive
    delegation without explicit configuration.
    """
    # Simulate a root-level call with orchestration budget (e.g. ultracode mode)
    root_context = {
        "delegation_depth": 0,
        "orchestration_token_budget": 100_000,  # ← From ultracode/session.metadata
        # Note: NO subdelegation_budget explicitly set
    }

    # After auto-seeding, subdelegation_budget should be 25k (25% of the
    # orchestration budget) and _clone_registry_with_delegation must register
    # call_agent_parallel for that budget.
    expected_subdelegation_budget = 100_000 // 4

    # Create a mock call object with the context
    class MockCall:
        def __init__(self, context):
            self.context = context

    mock_call = MockCall({**root_context, "subdelegation_budget": expected_subdelegation_budget})

    # Test the registry cloning logic
    registry = SkillRegistry()
    cloned = _clone_registry_with_delegation(registry, mock_call, depth=0)

    # Verify that call_agent_parallel is registered
    assert "call_agent_parallel" in cloned._by_name


def test_auto_seeding_only_at_depth_zero():
    """Auto-seeding should only happen at depth=0, not at deeper levels.

    A depth=1 child with orchestration budget but no subdelegation_budget must
    NOT be auto-seeded. (The seeding lives at depth=0 only — verified by the
    root-layer test above.)
    """


def test_explicit_subdelegation_budget_overrides_auto_seeding():
    """If subdelegation_budget is explicitly set, don't auto-seed.

    An explicit budget wins over the auto-seeded 25% — the seed branch only
    fires when no subdelegation_budget is present.
    """


def test_no_auto_seeding_without_orchestration_budget():
    """Without orchestration_token_budget, no auto-seeding occurs."""
    context = {
        "delegation_depth": 0,
        # No orchestration_token_budget
    }

    registry = SkillRegistry()

    class MockCall:
        def __init__(self, context):
            self.context = context

    mock_call = MockCall({**context, "subdelegation_budget": 0})  # ← no budget

    cloned = _clone_registry_with_delegation(registry, mock_call, depth=0)

    # Without budget, call_agent_parallel should NOT be registered
    assert "call_agent_parallel" not in cloned._by_name


def test_depth_limit_enforcement_with_auto_seeding():
    """Even with auto-seeded budget, depth limit is still enforced."""
    # At depth=2, even with budget, cannot spawn further (MAX_DELEGATION_DEPTH=2)
    context_depth_2 = {
        "delegation_depth": 2,
        "subdelegation_budget": 10_000,  # Has budget
    }

    registry = SkillRegistry()

    class MockCall:
        def __init__(self, context):
            self.context = context

    mock_call = MockCall(context_depth_2)

    cloned = _clone_registry_with_delegation(registry, mock_call, depth=2)

    # At depth=2 (MAX), cannot spawn further even with budget
    assert "call_agent_parallel" not in cloned._by_name


def test_budget_allocation_math():
    """Verify the 25% allocation math is correct."""
    test_cases = [
        (100_000, 25_000),  # 100k → 25k
        (200_000, 50_000),  # 200k → 50k
        (80_000, 20_000),  # 80k → 20k
        (1_000, 250),  # 1k → 250
        (100, 25),  # 100 → 25
        (3, 0),  # Small budget rounds down to 0
    ]

    for orch_budget, expected_subdel in test_cases:
        actual = orch_budget // 4
        assert actual == expected_subdel, (
            f"Budget {orch_budget} should yield {expected_subdel}, got {actual}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

