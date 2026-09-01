"""Integration test for agent auto-delegation in stream_react_loop.

Validates that when the user prompt has a single @agent: mention with
substantive instruction, and all heuristics pass, stream_react_loop
calls call_subagent directly before the first model turn and injects
the subagent's output as a synthetic Observation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch


@dataclass
class _MockRegistry:
    """Stub registry that claims all agents/skills exist."""

    def has(self, _name: str) -> bool:
        return True

    def is_enabled(self, _name: str) -> bool:
        return True

    def iter_skills(self):
        return []

    def iter_agents(self):
        return []


@dataclass
class _MockExecutor:
    """Stub executor that exposes a registry with call_agent."""

    registry: Any = field(default_factory=_MockRegistry)
    agent_registry: Any = field(default_factory=_MockRegistry)


@dataclass
class _MockPlanner:
    planner_model: str = "test-model"
    router: Any = None

    def __post_init__(self):
        # The router needs to exist for stream_react_loop to enter the
        # ReAct phase at all. Auto-delegation runs before any router
        # call, so a sentinel object is enough.
        if self.router is None:
            self.router = object()


@dataclass
class _MockStack:
    """Stub stack."""

    executor: Any = field(default_factory=_MockExecutor)
    planner: Any = field(default_factory=_MockPlanner)
    agent_registry: Any = field(default_factory=_MockRegistry)


@dataclass
class _MockAgent:
    agent_id: str = "lead"


@dataclass
class _MockIntent:
    normalized_goal: str
    user_context: dict[str, Any] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)


def _collect_until_event(gen, target_types: set[str], max_events: int = 50):
    """Drain the generator until a target event arrives or limit hit."""
    events: list[dict[str, Any]] = []
    try:
        for event in gen:
            events.append(event)
            if event.get("type") in target_types:
                break
            if len(events) >= max_events:
                break
    except (StopIteration, RuntimeError, AttributeError, TypeError):
        # The loop will eventually try to call the LLM and crash on our
        # stub stack — that's fine, we only care about pre-loop events.
        pass
    return events


def test_react_loop_auto_delegate_fires_on_single_agent_pin():
    from runtime.core.cerebrum.react_loop import stream_react_loop

    stack = _MockStack()
    agent = _MockAgent()
    intent = _MockIntent(
        normalized_goal="@agent:researcher please analyze the Q4 market trends",
    )

    mock_subagent_result = {
        "agent_id": "researcher",
        "output": "Market analysis: strong growth in Q4...",
        "success": True,
        "error": None,
    }

    with patch(
        "runtime.execution.subagents.bridge.call_subagent",
        return_value=mock_subagent_result,
    ) as mock_call:
        gen = stream_react_loop(
            stack=stack,
            intent=intent,
            agent=agent,
            enable_tools=True,
            planning_mode=False,
            max_iterations=1,
        )
        events = _collect_until_event(
            gen,
            {"auto_delegation_completed", "auto_delegation_skipped"},
        )

    assert mock_call.called, "call_subagent should have been invoked"
    call_kwargs = mock_call.call_args[1]
    assert call_kwargs["agent_id"] == "researcher"
    assert "analyze the Q4 market trends" in call_kwargs["prompt"]

    types = [e.get("type") for e in events]
    assert "auto_delegation_started" in types
    assert "auto_delegation_completed" in types


def test_react_loop_auto_delegate_skips_in_planning_mode():
    from runtime.core.cerebrum.react_loop import stream_react_loop

    stack = _MockStack()
    agent = _MockAgent()
    intent = _MockIntent(
        normalized_goal="@agent:researcher please draft the report carefully",
    )

    with patch(
        "runtime.execution.subagents.bridge.call_subagent",
    ) as mock_call:
        gen = stream_react_loop(
            stack=stack,
            intent=intent,
            agent=agent,
            enable_tools=True,
            planning_mode=True,  # blocks auto-delegate
            max_iterations=1,
        )
        _collect_until_event(gen, {"react_started"}, max_events=10)

    assert not mock_call.called, "planning_mode should block delegation"


def test_react_loop_auto_delegate_skips_when_tools_disabled():
    from runtime.core.cerebrum.react_loop import stream_react_loop

    stack = _MockStack()
    agent = _MockAgent()
    intent = _MockIntent(
        normalized_goal="@agent:researcher help me with this analysis task",
    )

    with patch(
        "runtime.execution.subagents.bridge.call_subagent",
    ) as mock_call:
        gen = stream_react_loop(
            stack=stack,
            intent=intent,
            agent=agent,
            enable_tools=False,
            planning_mode=False,
            max_iterations=1,
        )
        _collect_until_event(gen, {"react_started"}, max_events=10)

    assert not mock_call.called


def test_react_loop_auto_delegate_skips_when_multiple_agents():
    from runtime.core.cerebrum.react_loop import stream_react_loop

    stack = _MockStack()
    agent = _MockAgent()
    intent = _MockIntent(
        normalized_goal="@agent:a and @agent:b please both review this",
    )

    with patch(
        "runtime.execution.subagents.bridge.call_subagent",
    ) as mock_call:
        gen = stream_react_loop(
            stack=stack,
            intent=intent,
            agent=agent,
            enable_tools=True,
            planning_mode=False,
            max_iterations=1,
        )
        _collect_until_event(gen, {"react_started"}, max_events=10)

    assert not mock_call.called, "multiple agent mentions should require model orchestration"


def test_react_loop_auto_delegate_skips_when_no_mention():
    from runtime.core.cerebrum.react_loop import stream_react_loop

    stack = _MockStack()
    agent = _MockAgent()
    intent = _MockIntent(
        normalized_goal="just a regular question, nothing pinned",
    )

    with patch(
        "runtime.execution.subagents.bridge.call_subagent",
    ) as mock_call:
        gen = stream_react_loop(
            stack=stack,
            intent=intent,
            agent=agent,
            enable_tools=True,
            planning_mode=False,
            max_iterations=1,
        )
        _collect_until_event(gen, {"react_started"}, max_events=10)

    assert not mock_call.called
