"""End-to-end realtime chain: parent turn dispatches a sub-agent through the MAIN react loop.

Exercises the actual ``_drive_react`` producer path (session scope +
``react_stack_scope`` around ``stream_react_loop``) and confirms a synchronous
``call_subagent`` issued from inside the loop captures the ambient parent stack
and, by default, routes the child through ``stream_react_loop`` (the same
machinery as the main conversation) with its OWN thread identity
(``flip_subagent_thread``), while the blackboard stays continuous via
``blackboard_root_turn_id``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tests.realtime_cerebrum._helpers import drive as _drive


def _build_gateway(tmp_path: Path) -> tuple[Any, Any, Any]:
    """Build a realtime gateway with a scriptable planner/router stack."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway
    from runtime.sensing.model_router.models import ModelResponse, ModelStreamEvent

    class FakeRouter:
        def __init__(self) -> None:
            self.calls = 0

        def call_stream(self, _request: Any) -> Iterator[ModelStreamEvent]:
            self.calls += 1
            yield ModelStreamEvent(
                type="done",
                final=ModelResponse(text="ok", model="fake"),
            )

    class FakePlanner:
        planner_model = "fake"

        def __init__(self, router: FakeRouter) -> None:
            self.router = router

    class FakeStack:
        def __init__(self, router: FakeRouter) -> None:
            self.planner = FakePlanner(router)
            self.journal = None

    router = FakeRouter()
    runtime = CerebrumRuntime(
        stack=FakeStack(router),
        agent=None,
        logs_root=str(tmp_path / "threads"),
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)
    return TestClient(app), runtime, router


def test_parent_turn_dispatches_child_through_react_loop(monkeypatch, tmp_path: Path) -> None:
    """A sub-agent dispatched inside the realtime parent loop inherits the
    parent's react stack, flips to react-loop + independent-thread defaults,
    and is actually driven through ``stream_react_loop``."""
    import runtime.core.cerebrum.react_loop as rl
    import runtime.execution.subagents.bridge as bridge
    from runtime.core.cerebrum.react_loop import ReActResult
    from runtime.execution.subagents import react_drive
    from runtime.execution.subagents._ambient import current_react_stack

    captured: dict[str, Any] = {}
    child_loop_calls: list[dict[str, Any]] = []
    _seen_parent: list[Any] = []

    # Record the child react-loop invocation (thread id + role context) while
    # still running the REAL dispatch chain so the whole link is exercised.
    _real_run_subagent = react_drive.run_subagent_react_loop

    def _recording_run_subagent(stack, **kw):
        child_loop_calls.append(
            {
                "thread_id": kw.get("thread_id"),
                "role_id": kw.get("role_id"),
                "conversation_messages": list(kw.get("conversation_messages") or []),
                "tool_allowlist": tuple(kw.get("tool_allowlist") or ()),
            }
        )
        return _real_run_subagent(stack, **kw)

    monkeypatch.setattr(react_drive, "run_subagent_react_loop", _recording_run_subagent)

    # The child's react loop is scripted to complete immediately; the point is
    # the child is driven through the MAIN loop, not the bespoke mini-loop.
    def _real_child_loop(stack, intent, agent, **kwargs):  # noqa: ARG001
        yield {"type": "react_completed"}
        return ReActResult(success=True, final_answer="child done")

    monkeypatch.setattr(react_drive, "stream_react_loop", _real_child_loop)

    # Record the dispatch context/session while still running the real chain.
    _real_dispatch = bridge._dispatch

    def _recording_dispatch(**kw):
        captured["context"] = dict(kw.get("context") or {})
        captured["session"] = kw.get("session")
        return _real_dispatch(**kw)

    monkeypatch.setattr(bridge, "_dispatch", _recording_dispatch)

    def fake_stream(stack, intent, agent, **kwargs):
        # Runs inside the realtime producer thread, within session_scope +
        # react_stack_scope. Assert the ambient parent stack is live and then
        # simulate a model delegation by dispatching a sub-agent synchronously.
        _seen_parent.append(current_react_stack())
        result = bridge.call_subagent(
            agent_id="researcher",
            prompt="去调研一下这个市场",
        )
        yield {"type": "react_completed"}
        return ReActResult(success=True, final_answer=result.get("output", "ok"))

    monkeypatch.setattr(rl, "stream_react_loop", fake_stream)

    client, runtime, router = _build_gateway(tmp_path)  # noqa: F841
    from runtime.execution.suckers.ephemeral_agents import (
        set_ephemeral_role_runner,
    )
    from runtime.execution.suckers.ephemeral_runner import (
        make_llm_ephemeral_runner,
    )

    set_ephemeral_role_runner(make_llm_ephemeral_runner(router, default_model="fake"))
    with client, client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th-react-chain",
                "input": [{"type": "text", "text": "派一个研究员去调研"}],
                "approvalPolicy": "never",
                "model": "fake",
            },
        )

    assert _seen_parent, "parent react loop should see a live ambient react stack"
    assert _seen_parent[0] is runtime._stack

    # The bridge captured the ambient parent stack and defaulted the child to
    # react-loop driving + its own thread identity.
    ctx = captured.get("context") or {}
    assert ctx.get("react_stack") is runtime._stack
    assert ctx.get("react_loop_subagent") is True
    assert ctx.get("flip_subagent_thread") is True

    # The child run Session is a real, independent thread that still shares the
    # parent's blackboard root.
    child_session = captured.get("session")
    assert child_session is not None
    assert child_session.thread_id not in ("", "th-react-chain")
    assert child_session.metadata.get("blackboard_root_turn_id"), (
        "child should carry the parent's blackboard root turn id"
    )
    assert child_session.metadata.get("root_thread_id") == "th-react-chain"

    # And the child was actually driven through the MAIN react loop — same
    # machinery the main conversation uses — on its own thread id.
    assert child_loop_calls, "child should have been driven through the main react loop"
    assert child_loop_calls[0]["thread_id"] == child_session.thread_id
    assert child_loop_calls[0]["role_id"] == "researcher"
    # Role persona + caller context are carried into the loop as system history.
    roles = [m.get("role") for m in child_loop_calls[0]["conversation_messages"]]
    assert roles == ["system", "user"]

    # The parent turn itself still finalised normally.
    turn = out["response"].result["turn"]
    assert turn["threadId"] == "th-react-chain"


def _configure_runner(router: Any) -> None:
    from runtime.execution.suckers.ephemeral_agents import (
        set_ephemeral_role_runner,
    )
    from runtime.execution.suckers.ephemeral_runner import (
        make_llm_ephemeral_runner,
    )

    set_ephemeral_role_runner(make_llm_ephemeral_runner(router, default_model="fake"))


def test_parent_turn_respects_react_loop_opt_out(monkeypatch, tmp_path: Path) -> None:
    """Explicit ``react_loop_subagent=False`` / ``flip_subagent_thread=False``
    are honoured in the full realtime chain: the child skips the main react
    loop and keeps the parent's thread identity."""
    import runtime.core.cerebrum.react_loop as rl
    import runtime.execution.subagents.bridge as bridge
    from runtime.core.cerebrum.react_loop import ReActResult
    from runtime.execution.subagents import react_drive
    from runtime.execution.subagents._ambient import current_react_stack

    captured: dict[str, Any] = {}
    child_loop_calls: list[dict[str, Any]] = []
    _real_run_subagent = react_drive.run_subagent_react_loop

    def _recording_run_subagent(stack, **kw):
        child_loop_calls.append({"thread_id": kw.get("thread_id")})
        return _real_run_subagent(stack, **kw)

    monkeypatch.setattr(react_drive, "run_subagent_react_loop", _recording_run_subagent)

    def _real_child_loop(stack, intent, agent, **kwargs):  # noqa: ARG001
        yield {"type": "react_completed"}
        return ReActResult(success=True, final_answer="child done")

    monkeypatch.setattr(react_drive, "stream_react_loop", _real_child_loop)

    _real_dispatch = bridge._dispatch

    def _recording_dispatch(**kw):
        captured["context"] = dict(kw.get("context") or {})
        captured["session"] = kw.get("session")
        return _real_dispatch(**kw)

    monkeypatch.setattr(bridge, "_dispatch", _recording_dispatch)

    def fake_stream(stack, intent, agent, **kwargs):  # noqa: ARG001
        _ = current_react_stack()
        bridge.call_subagent(
            agent_id="researcher",
            prompt="去调研一下这个市场",
            context={
                "react_loop_subagent": False,
                "flip_subagent_thread": False,
            },
        )
        yield {"type": "react_completed"}
        return ReActResult(success=True, final_answer="ok")

    monkeypatch.setattr(rl, "stream_react_loop", fake_stream)

    client, runtime, router = _build_gateway(tmp_path)  # noqa: F841
    _configure_runner(router)
    with client, client.websocket_connect("/api/realtime") as ws:
        _drive(
            ws,
            {
                "threadId": "th-react-optout",
                "input": [{"type": "text", "text": "派研究员"}],
                "approvalPolicy": "never",
                "model": "fake",
            },
        )

    ctx = captured.get("context") or {}
    assert ctx.get("react_loop_subagent") is False
    assert ctx.get("flip_subagent_thread") is False
    # The child skipped the main react loop and kept the parent thread id.
    assert not child_loop_calls
    child_session = captured.get("session")
    assert child_session is not None
    assert child_session.thread_id == "th-react-optout"

