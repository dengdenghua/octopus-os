"""Tests for the lane-B parallel tool execution path in tool_bridge.

The native tool loop in ``runtime.sensing.gateway.tool_bridge`` now runs
multiple independent ``tool_use`` blocks concurrently in a single
agent message round, rather than serially. These tests pin:

  * ordering: tool_result blocks come back in the order the assistant
    emitted the tool_use blocks (not in completion order)
  * disabled by stack metadata: ``parallel_tool_use=False`` falls back
    to the serial path
  * serial-barrier tools (``todo_write`` / ``exit_plan_mode``) force
    serial execution even when otherwise parallelizable
  * single-tool rounds always go serial (no thread-pool overhead)
  * concurrency speedup: 3 calls each "sleeping" 80ms wall-clock should
    finish in roughly 80ms (parallel) rather than 240ms (serial)
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from runtime.execution.suckers.registry import Skill, SkillRegistry
from runtime.execution.tool_engine.executor import ToolExecutor
from runtime.platform.models import ParsedIntent
from runtime.safety.auth import TrustEngine
from runtime.sensing.gateway.tool_bridge import stream_agentic_fallback
from runtime.sensing.model_router.models import (
    ModelResponse,
    ModelStreamEvent,
    ToolCall,
)


def _agent():
    return SimpleNamespace(
        agent_id="coder",
        capabilities={"code_mode_unlock": True},
        soul="",
    )


def _slow_sum_handler(a: int = 0, b: int = 0, sleep_ms: int = 80) -> dict:
    time.sleep(sleep_ms / 1000.0)
    return {"sum": a + b}


def _make_stack(router, *, metadata: dict | None = None):
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="slow_sum",
            description="Add two numbers slowly.",
            affinity=["math"],
            trusted_source="skill://public/slow_sum",
            handler=_slow_sum_handler,
        ),
        verify_tests=False,
    )
    registry.register(
        Skill(
            name="todo_write",
            description="Update todo list.",
            affinity=["meta"],
            trusted_source="skill://public/todo_write",
            handler=lambda **kw: {"ok": True},
        ),
        verify_tests=False,
    )
    return SimpleNamespace(
        executor=ToolExecutor(registry, TrustEngine()),
        planner=SimpleNamespace(router=router, planner_model="mock"),
        metadata=metadata or {},
    )


class _RouterEmitting:
    """Mock router that emits a configurable list of tool_use blocks
    on the first round, then text on the second."""

    def __init__(self, calls_round1: list[ToolCall]):
        self._calls_round1 = calls_round1
        self.calls = 0

    def call_stream(self, req):
        self.calls += 1
        if self.calls == 1:
            for c in self._calls_round1:
                yield ModelStreamEvent(type="tool_use", tool_call=c)
            yield ModelStreamEvent(type="done", final=ModelResponse(text="", tool_calls=[]))
            return
        yield ModelStreamEvent(type="text_delta", delta="done")
        yield ModelStreamEvent(type="done", final=ModelResponse(text="done"))


def _intent():
    return ParsedIntent(
        raw="add three pairs in parallel",
        intent_type="task",
        normalized_goal="add three pairs",
        user_context={
            "conversation_id": "thread-parallel",
            "metadata": {"mode": "code"},
        },
    )


def test_parallel_path_preserves_emission_order() -> None:
    calls = [
        ToolCall(id=f"t-{i}", name="slow_sum", input={"a": i, "b": 1, "sleep_ms": 50})
        for i in range(3)
    ]
    router = _RouterEmitting(calls)
    events = list(stream_agentic_fallback(_make_stack(router), _intent(), _agent()))

    tool_end_events = [e for e in events if e[0] == "tool_end"]
    assert len(tool_end_events) == 3
    # Order must match the assistant's emission order, not whichever
    # finished first.
    assert [e[1]["id"] for e in tool_end_events] == ["t-0", "t-1", "t-2"]
    # And every tool_end on the parallel path carries the marker.
    assert all(e[1].get("parallel") is True for e in tool_end_events)


def test_parallel_path_yields_concurrency_speedup() -> None:
    calls = [
        ToolCall(id=f"t-{i}", name="slow_sum", input={"a": i, "b": 1, "sleep_ms": 80})
        for i in range(3)
    ]
    router = _RouterEmitting(calls)
    started = time.monotonic()
    events = list(stream_agentic_fallback(_make_stack(router), _intent(), _agent()))
    elapsed = time.monotonic() - started

    # Serial would take ~240ms (3 × 80ms). Parallel should be ~80ms +
    # overhead. Generous bound (180ms) to absorb thread-pool spin-up
    # and CI jitter while still proving real concurrency.
    tool_end_events = [e for e in events if e[0] == "tool_end"]
    assert len(tool_end_events) == 3
    assert elapsed < 0.18, f"parallel exec slower than expected: {elapsed:.3f}s"


def test_serial_when_only_one_tool() -> None:
    calls = [ToolCall(id="t-only", name="slow_sum", input={"a": 1, "b": 2, "sleep_ms": 10})]
    router = _RouterEmitting(calls)
    events = list(stream_agentic_fallback(_make_stack(router), _intent(), _agent()))
    tool_end_events = [e for e in events if e[0] == "tool_end"]
    assert len(tool_end_events) == 1
    # Single-tool rounds skip the parallel path → no marker.
    assert tool_end_events[0][1].get("parallel") is not True


def test_serial_when_todo_write_in_round() -> None:
    """todo_write in the round forces serial execution because state
    machine ops have ordered semantics the model expects."""
    calls = [
        ToolCall(id="t-0", name="slow_sum", input={"a": 1, "b": 1, "sleep_ms": 5}),
        ToolCall(
            id="t-todo",
            name="todo_write",
            input={"items": [{"content": "x", "status": "in_progress", "activeForm": "x"}]},
        ),
        ToolCall(id="t-2", name="slow_sum", input={"a": 2, "b": 2, "sleep_ms": 5}),
    ]
    router = _RouterEmitting(calls)
    events = list(stream_agentic_fallback(_make_stack(router), _intent(), _agent()))
    tool_end_events = [e for e in events if e[0] == "tool_end"]
    assert len(tool_end_events) == 3
    assert all(e[1].get("parallel") is not True for e in tool_end_events)


def test_serial_when_stack_metadata_disables_parallel() -> None:
    calls = [
        ToolCall(id=f"t-{i}", name="slow_sum", input={"a": i, "b": 1, "sleep_ms": 5})
        for i in range(3)
    ]
    router = _RouterEmitting(calls)
    stack = _make_stack(router, metadata={"parallel_tool_use": False})
    events = list(stream_agentic_fallback(stack, _intent(), _agent()))
    tool_end_events = [e for e in events if e[0] == "tool_end"]
    assert len(tool_end_events) == 3
    assert all(e[1].get("parallel") is not True for e in tool_end_events)
