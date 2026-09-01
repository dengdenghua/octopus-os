"""
Tests for timeout enforcement and SSE event emission in call_subagent.

Coverage
--------
1. timeout_seconds=0.1 against a slow runner → status=timeout
2. timeout_seconds=10 against a fast runner → completes normally
3. timeout_seconds=None (default) → no limit enforced
4. event_emitter receives sub_tool_start before each tool call
5. event_emitter exceptions are swallowed (don't crash the runner)
6. Round counting: timeout at round 2 → rounds_completed=2
"""

from __future__ import annotations

import time

import pytest

# ─── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_bridge():
    """Ensure bridge module-level state is clean before and after each test."""
    from runtime.execution.subagents.bridge import (
        set_sub_agent_runner,
        set_subagent_registry,
    )

    set_sub_agent_runner(None)
    set_subagent_registry(None)
    yield
    set_sub_agent_runner(None)
    set_subagent_registry(None)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _install_runner(fn):
    """Register *fn* as the legacy _RUNNER and return it."""
    from runtime.execution.subagents.bridge import set_sub_agent_runner

    set_sub_agent_runner(fn)
    return fn


def _slow_runner(prompt, *, subagent_name, context):
    """Sleeps 2 s — long enough to trip a 0.1 s timeout."""
    time.sleep(2)
    return "should not reach here"


def _fast_runner(prompt, *, subagent_name, context):
    """Returns immediately."""
    return f"fast result for {subagent_name}"


# ─── test 1: timeout fires ────────────────────────────────────────────────────


def test_timeout_returns_timeout_status():
    """call_subagent with a short timeout against a slow runner returns
    a structured timeout result instead of hanging."""
    from runtime.execution.subagents.bridge import call_subagent

    _install_runner(_slow_runner)

    result = call_subagent(
        agent_id="coder",
        prompt="do something",
        timeout_seconds=0.1,
    )

    assert result["status"] == "timeout"
    assert result["success"] is False
    assert "timed out" in result["error"]
    assert "0.1s" in result["error"]
    assert result["agent_id"] == "coder"
    assert "rounds_completed" in result


# ─── test 2: fast runner completes normally ───────────────────────────────────


def test_no_timeout_with_generous_limit():
    """A fast runner completes well within a 10 s timeout."""
    from runtime.execution.subagents.bridge import call_subagent

    _install_runner(_fast_runner)

    result = call_subagent(
        agent_id="coder",
        prompt="do something",
        timeout_seconds=10,
    )

    assert result["success"] is True
    assert result["agent_id"] == "coder"
    assert "fast result for coder" in result["output"]
    assert result.get("status") != "timeout"


# ─── test 3: None timeout means no enforcement ───────────────────────────────


def test_none_timeout_does_not_enforce():
    """timeout_seconds=None (default) must not impose any limit."""
    from runtime.execution.subagents.bridge import call_subagent

    _install_runner(_fast_runner)

    # Call without timeout_seconds at all (default None path).
    result = call_subagent(agent_id="coder", prompt="do something")

    assert result["success"] is True
    assert result.get("status") != "timeout"


# ─── test 4: event_emitter receives sub_tool_start events ────────────────────


def test_event_emitter_receives_sub_tool_start():
    """event_emitter callable is called with sub_tool_start events
    before each tool invocation inside the runner."""
    from runtime.execution.subagents.bridge import call_subagent

    collected: list[dict] = []

    def _emitting_runner(prompt, *, subagent_name, context):
        emitter = context.get("event_emitter")
        # Simulate two tool calls across two rounds.
        for rnd in (1, 2):
            if emitter:
                emitter(
                    {
                        "type": "sub_tool_start",
                        "agent_id": subagent_name,
                        "round": rnd,
                        "skill": f"fetch_url_{rnd}",
                        "args_preview": f"https://example.com/{rnd}",
                    }
                )
        return "done"

    _install_runner(_emitting_runner)

    result = call_subagent(
        agent_id="coder",
        prompt="research something",
        event_emitter=collected.append,
        timeout_seconds=10,
    )

    assert result["success"] is True
    starts = [e for e in collected if e.get("type") == "sub_tool_start"]
    assert len(starts) == 2
    assert starts[0]["round"] == 1
    assert starts[0]["skill"] == "fetch_url_1"
    assert starts[1]["round"] == 2


# ─── test 5: emitter exceptions are swallowed ────────────────────────────────


def test_event_emitter_exception_does_not_crash_runner():
    """If the event_emitter raises, the runner must continue and return
    a normal result — the emitter is fire-and-forget."""
    from runtime.execution.subagents.bridge import call_subagent

    def _boom_emitter(event):
        raise RuntimeError("emitter exploded")

    def _emitting_runner(prompt, *, subagent_name, context):
        emitter = context.get("event_emitter")
        if emitter:
            emitter(
                {
                    "type": "sub_tool_start",
                    "agent_id": subagent_name,
                    "round": 1,
                    "skill": "test_skill",
                    "args_preview": "",
                }
            )
        return "survived"

    _install_runner(_emitting_runner)

    # Must not raise despite the emitter blowing up.
    result = call_subagent(
        agent_id="coder",
        prompt="do something",
        event_emitter=_boom_emitter,
        timeout_seconds=10,
    )

    assert result["success"] is True
    assert result["output"] == "survived"


# ─── test 6: round counting on timeout ───────────────────────────────────────


def test_rounds_completed_reflects_progress_before_timeout():
    """When a timeout fires mid-run, rounds_completed equals the highest
    round number that was emitted before the deadline."""
    from runtime.execution.subagents.bridge import call_subagent

    def _two_round_then_hang(prompt, *, subagent_name, context):
        emitter = context.get("event_emitter")
        # Round 1 completes quickly.
        if emitter:
            emitter(
                {
                    "type": "sub_tool_start",
                    "agent_id": subagent_name,
                    "round": 1,
                    "skill": "quick_tool",
                    "args_preview": "",
                }
            )
        # Round 2 starts but then the runner hangs.
        if emitter:
            emitter(
                {
                    "type": "sub_tool_start",
                    "agent_id": subagent_name,
                    "round": 2,
                    "skill": "slow_tool",
                    "args_preview": "",
                }
            )
        time.sleep(5)  # hang — will be interrupted by timeout
        return "never"

    _install_runner(_two_round_then_hang)

    result = call_subagent(
        agent_id="coder",
        prompt="do something",
        timeout_seconds=0.3,
    )

    assert result["status"] == "timeout"
    assert result["rounds_completed"] == 2
