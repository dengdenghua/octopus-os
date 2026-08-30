"""
Proof-of-concept test for recursive delegation (Phase 1).

Validates that ephemeral sub-agents can spawn their own sub-agents when
explicitly allowed, with proper depth and budget propagation.
"""

from __future__ import annotations

import pytest


def test_ephemeral_runner_registers_delegation_when_allowed(monkeypatch):
    """Ephemeral runner conditionally registers call_agent_parallel."""
    from runtime.execution.suckers.ephemeral_runner import (
        _clone_registry_with_delegation,
    )
    from runtime.execution.suckers.registry import SkillRegistry

    # Mock call with subdelegation enabled
    class MockCall:
        role_id = "security_reviewer"
        context = {
            "delegation_depth": 1,
            "allow_subdelegation": True,
            "subdelegation_budget": 50000,
        }

    registry = SkillRegistry()
    # Base registry has no delegation skills
    assert "call_agent_parallel" not in registry._by_name

    # Clone with delegation
    cloned = _clone_registry_with_delegation(
        registry,
        call=MockCall(),
        depth=1,
    )

    # Cloned registry should have delegation skill
    assert "call_agent_parallel" in cloned._by_name
    assert cloned._by_name["call_agent_parallel"].name == "call_agent_parallel"


def test_depth_propagation_in_parallel_spawn(monkeypatch):
    """call_agent_parallel propagates depth+1 to spawned sub-agents."""
    from runtime.execution.suckers._delegation_skills_parallel import (
        _call_agent_parallel,
    )

    captured_contexts = []

    def mock_call_subagent(*args, **kwargs):
        captured_contexts.append(kwargs.get("context", {}))
        return {
            "agent_id": kwargs.get("agent_id", "unknown"),
            "output": "mock output",
            "success": True,
        }

    monkeypatch.setattr(
        "runtime.execution.subagents.call_subagent",
        mock_call_subagent,
    )

    # Parent context at depth=1
    parent_context = {
        "delegation_depth": 1,
        "subdelegation_budget": 60000,
        "turn_id": "test-turn",
        "thread_id": "test-thread",
    }

    # Spawn 3 parallel sub-agents
    result = _call_agent_parallel(
        specs=[
            {"agent_id": "researcher", "prompt": "Task A"},
            {"agent_id": "code_reviewer", "prompt": "Task B"},
            {"agent_id": "architect", "prompt": "Task C"},
        ],
        context=parent_context,
    )

    # Should succeed
    assert result["ok"] is True
    assert result["success_count"] == 3

    # Each spawned sub-agent should be at depth=2
    assert len(captured_contexts) == 3
    for ctx in captured_contexts:
        assert ctx["delegation_depth"] == 2
        # Budget split among 3 siblings: 60000 // 3 = 20000 each
        assert ctx["orchestration_token_budget"] == 20000
        # Subdelegation budget is half: 20000 // 2 = 10000
        assert ctx["subdelegation_budget"] == 10000


def test_depth_limit_prevents_infinite_recursion(monkeypatch):
    """Sub-agents at depth=2 cannot spawn further (MAX_DELEGATION_DEPTH=2)."""
    from runtime.execution.suckers._delegation_skills_parallel import (
        _call_agent_parallel,
    )

    captured_contexts = []

    def mock_call_subagent(*args, **kwargs):
        captured_contexts.append(kwargs.get("context", {}))
        return {
            "agent_id": kwargs.get("agent_id", "unknown"),
            "output": "mock output",
            "success": True,
        }

    monkeypatch.setattr(
        "runtime.execution.subagents.call_subagent",
        mock_call_subagent,
    )

    # Parent at depth=1 (max depth=2, so children at depth=2 are terminal)
    parent_context = {
        "delegation_depth": 1,
        "subdelegation_budget": 30000,
        "turn_id": "test-turn",
        "thread_id": "test-thread",
    }

    result = _call_agent_parallel(
        specs=[
            {"agent_id": "researcher", "prompt": "Task", "allow_subdelegation": True},
        ],
        context=parent_context,
    )

    assert result["ok"] is True
    assert len(captured_contexts) == 1

    # Child is at depth=2, allow_subdelegation should be False
    child_ctx = captured_contexts[0]
    assert child_ctx["delegation_depth"] == 2
    # Hardcoded depth limit check: depth+1 < 2 → 2 < 2 → False
    assert child_ctx.get("allow_subdelegation") is False


def test_no_subdelegation_without_budget():
    """Sub-agents with zero subdelegation_budget cannot spawn children."""
    from runtime.execution.suckers.ephemeral_runner import (
        _clone_registry_with_delegation,
    )
    from runtime.execution.suckers.registry import SkillRegistry

    class MockCall:
        role_id = "researcher"
        context = {
            "delegation_depth": 1,
            "allow_subdelegation": True,
            "subdelegation_budget": 0,  # No budget
        }

    registry = SkillRegistry()
    cloned = _clone_registry_with_delegation(
        registry,
        call=MockCall(),
        depth=1,
    )

    # Should NOT register delegation skills when budget=0
    assert "call_agent_parallel" not in cloned._by_name


def test_register_call_agent_parallel_function():
    """register_call_agent_parallel creates the skill correctly."""
    from runtime.execution.suckers.delegation_skills import (
        register_call_agent_parallel,
    )
    from runtime.execution.suckers.registry import SkillRegistry

    registry = SkillRegistry()
    count = register_call_agent_parallel(registry, max_spawns=3, depth=1)

    assert count == 1
    assert "call_agent_parallel" in registry._by_name

    skill = registry._by_name["call_agent_parallel"]
    assert skill.name == "call_agent_parallel"
    assert "depth 1" in skill.description
    assert "up to 3" in skill.description


@pytest.mark.slow
def test_end_to_end_hierarchical_spawn(monkeypatch):
    """End-to-end: planner → Security Reviewer → 3 parallel sub-audits.

    This simulates the desired behavior:
    1. Main agent spawns Security Reviewer with allow_subdelegation=True
    2. Security Reviewer spawns 3 parallel sub-audits (auth, injection, keys)
    3. Budget and depth propagate correctly at each level
    """
    from runtime.execution.suckers._delegation_skills_parallel import (
        _call_agent_parallel,
    )

    spawn_tree = []

    def mock_call_subagent(*args, **kwargs):
        ctx = kwargs.get("context", {})
        spawn_tree.append(
            {
                "agent_id": kwargs.get("agent_id"),
                "depth": ctx.get("delegation_depth", 0),
                "budget": ctx.get("orchestration_token_budget", 0),
                "sub_budget": ctx.get("subdelegation_budget", 0),
                "allow_sub": ctx.get("allow_subdelegation", False),
            }
        )
        return {
            "agent_id": kwargs.get("agent_id", "unknown"),
            "output": f"audit result from {kwargs.get('agent_id')}",
            "success": True,
        }

    monkeypatch.setattr(
        "runtime.execution.subagents.call_subagent",
        mock_call_subagent,
    )

    # Step 1: Main agent (depth=0) spawns Security Reviewer
    # (This would normally be a single call_agent, but we simulate with parallel)
    main_context = {
        "delegation_depth": 0,
        "subdelegation_budget": 100000,
        "turn_id": "test-turn",
        "thread_id": "test-thread",
    }

    result_level1 = _call_agent_parallel(
        specs=[
            {
                "agent_id": "reviewer",  # Use actual role name
                "prompt": "Audit security, self-organize",
                "allow_subdelegation": True,
            },
        ],
        context=main_context,
    )

    assert result_level1["ok"] is True
    assert len(spawn_tree) == 1
    security_reviewer = spawn_tree[0]

    # Debug: print actual values
    print(f"Security reviewer context: {security_reviewer}")

    assert security_reviewer["agent_id"] == "reviewer"  # Match actual role
    assert security_reviewer["depth"] == 1
    assert security_reviewer["budget"] == 100000  # Gets full parent budget (1 node)
    assert security_reviewer["sub_budget"] == 50000  # Half for sub-delegation
    # NOTE: allow_sub comes from spec.allow_subdelegation, not context
    # The test passes allow_subdelegation=True in the spec, so this should work
    assert security_reviewer["allow_sub"] is True  # depth 1 < 2

    # Step 2: Security Reviewer spawns 3 parallel sub-audits
    # (Simulating what Security Reviewer would do with its call_agent_parallel)
    spawn_tree.clear()

    reviewer_context = {
        "delegation_depth": 1,
        "subdelegation_budget": 50000,
        "turn_id": "test-turn",
        "thread_id": "test-thread",
    }

    result_level2 = _call_agent_parallel(
        specs=[
            {"agent_id": "researcher", "prompt": "Audit auth module"},
            {"agent_id": "code_reviewer", "prompt": "Scan injection points"},
            {"agent_id": "architect", "prompt": "Review key management"},
        ],
        context=reviewer_context,
    )

    assert result_level2["ok"] is True
    assert len(spawn_tree) == 3

    # Each sub-audit is at depth=2 (terminal)
    for node in spawn_tree:
        assert node["depth"] == 2
        # Budget split 3 ways: 50000 // 3 = 16666 each
        assert node["budget"] == 16666
        assert node["sub_budget"] == 8333  # Half of 16666
        # depth=2, cannot delegate further (2 < 2 = False)
        assert node["allow_sub"] is False

