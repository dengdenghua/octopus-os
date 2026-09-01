"""
Test role-specific delegation guidance (Phase 2).

Validates that sub-agents with subdelegation enabled receive role-specific
orchestration guidance in their system prompts.
"""

from __future__ import annotations


def test_delegation_guidance_injected_for_reviewer(monkeypatch):
    """Security reviewer gets delegation guidance when subdelegation enabled."""
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

    # Spawn reviewer with subdelegation enabled
    parent_context = {
        "delegation_depth": 0,
        "subdelegation_budget": 100000,
        "turn_id": "test-turn",
        "thread_id": "test-thread",
    }

    result = _call_agent_parallel(
        specs=[
            {
                "agent_id": "reviewer",
                "prompt": "Conduct security audit",
                "allow_subdelegation": True,
            },
        ],
        context=parent_context,
    )

    assert result["ok"] is True
    assert len(captured_contexts) == 1

    ctx = captured_contexts[0]
    # Should have delegation guidance
    assert "delegation_guidance" in ctx
    guidance = ctx["delegation_guidance"]
    assert "Security Reviewer" in guidance
    assert "call_agent_parallel" in guidance
    assert "Authentication & Authorization" in guidance
    assert "Injection Attacks" in guidance


def test_no_guidance_without_subdelegation():
    """Sub-agents without subdelegation don't get delegation guidance."""

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

    import runtime.execution.subagents

    original = runtime.execution.subagents.call_subagent
    runtime.execution.subagents.call_subagent = mock_call_subagent

    try:
        # Spawn reviewer WITHOUT subdelegation
        parent_context = {
            "delegation_depth": 0,
            "subdelegation_budget": 100000,
            "turn_id": "test-turn",
            "thread_id": "test-thread",
        }

        result = _call_agent_parallel(
            specs=[
                {
                    "agent_id": "reviewer",
                    "prompt": "Conduct security audit",
                    "allow_subdelegation": False,  # Explicitly disabled
                },
            ],
            context=parent_context,
        )

        assert result["ok"] is True
        assert len(captured_contexts) == 1

        ctx = captured_contexts[0]
        # Should NOT have delegation guidance
        assert "delegation_guidance" not in ctx
    finally:
        runtime.execution.subagents.call_subagent = original


def test_guidance_included_in_system_prompt():
    """Delegation guidance is included in composed system prompt."""
    from runtime.execution.suckers.ephemeral_agents import (
        EphemeralRoleDef,
        _compose_system_prompt,
    )

    role = EphemeralRoleDef(
        id="reviewer",
        display_name="Security Reviewer",
        description="Security audit specialist",
        system_prompt="You are a security reviewer.",
        tool_allowlist=["Read", "Bash"],
    )

    context = {
        "delegation_guidance": "**Test guidance:** Use call_agent_parallel to spawn audits.",
    }

    composed = _compose_system_prompt(role, session=None, context=context)

    # Should include the base system prompt
    assert "You are a security reviewer." in composed

    # Should include the delegation guidance section
    assert "## Hierarchical Orchestration" in composed
    assert "Test guidance:" in composed
    assert "call_agent_parallel" in composed


def test_multiple_roles_get_different_guidance(monkeypatch):
    """Different roles get role-specific guidance."""
    from runtime.execution.suckers._delegation_skills_parallel import (
        _call_agent_parallel,
    )

    captured_contexts = []

    def mock_call_subagent(*args, **kwargs):
        captured_contexts.append(
            {
                "agent_id": kwargs.get("agent_id"),
                "guidance": kwargs.get("context", {}).get("delegation_guidance", ""),
            }
        )
        return {
            "agent_id": kwargs.get("agent_id", "unknown"),
            "output": "mock output",
            "success": True,
        }

    monkeypatch.setattr(
        "runtime.execution.subagents.call_subagent",
        mock_call_subagent,
    )

    parent_context = {
        "delegation_depth": 0,
        "subdelegation_budget": 150000,
        "turn_id": "test-turn",
        "thread_id": "test-thread",
    }

    result = _call_agent_parallel(
        specs=[
            {"agent_id": "reviewer", "prompt": "Audit", "allow_subdelegation": True},
            {"agent_id": "architect", "prompt": "Design", "allow_subdelegation": True},
            {"agent_id": "researcher", "prompt": "Research", "allow_subdelegation": True},
        ],
        context=parent_context,
    )

    assert result["ok"] is True
    assert len(captured_contexts) == 3

    # Reviewer should mention security audit dimensions
    reviewer_ctx = next(c for c in captured_contexts if c["agent_id"] == "reviewer")
    assert "Security Reviewer" in reviewer_ctx["guidance"]
    assert "Authentication & Authorization" in reviewer_ctx["guidance"]

    # Architect should mention architectural concerns
    architect_ctx = next(c for c in captured_contexts if c["agent_id"] == "architect")
    assert "System Architect" in architect_ctx["guidance"]
    assert "Component Design" in architect_ctx["guidance"]

    # Researcher should mention research lanes
    researcher_ctx = next(c for c in captured_contexts if c["agent_id"] == "researcher")
    assert "Researcher" in researcher_ctx["guidance"]
    assert "Documentation Review" in researcher_ctx["guidance"]


def test_get_delegation_guidance_function():
    """get_delegation_guidance returns correct guidance for known roles."""
    from runtime.execution.suckers.role_delegation_guidance import (
        get_delegation_guidance,
    )

    # Known roles should return guidance
    reviewer_guidance = get_delegation_guidance("reviewer")
    assert reviewer_guidance is not None
    assert "Security Reviewer" in reviewer_guidance

    architect_guidance = get_delegation_guidance("architect")
    assert architect_guidance is not None
    assert "System Architect" in architect_guidance

    # Unknown role should return None
    unknown_guidance = get_delegation_guidance("unknown_role_xyz")
    assert unknown_guidance is None

