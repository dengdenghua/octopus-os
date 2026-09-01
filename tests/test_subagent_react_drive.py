"""Tests for running a sub-agent through the MAIN react loop."""

from dataclasses import dataclass

from runtime.execution.subagents.react_drive import (
    build_subagent_intent,
    run_subagent_react_loop,
)


@dataclass
class _FakeResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = "stop"


class _ScriptedRouter:
    def __init__(self, scripts: list[str]) -> None:
        self.scripts = list(scripts)
        self.calls = 0

    def call(self, req):  # noqa: ARG002
        text = self.scripts[self.calls]
        self.calls += 1
        return _FakeResponse(text=text)

    def call_stream(self, req):
        from runtime.sensing.model_router.models import (
            CostEntry,
            ModelResponse,
            ModelStreamEvent,
        )

        resp = self.call(req)
        if resp.text:
            yield ModelStreamEvent(type="text_delta", delta=resp.text)
        yield ModelStreamEvent(
            type="done",
            final=ModelResponse(
                text=resp.text,
                model="test-model",
                input_tokens=0,
                output_tokens=0,
                finish_reason="stop",
                cost=CostEntry(),
            ),
        )


class _FakePlanner:
    def __init__(self, router) -> None:
        self.router = router
        self.planner_model = "test-model"


class _FakeStack:
    def __init__(self, router) -> None:
        self.planner = _FakePlanner(router)


class _CaptureEmitter:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(dict(event))


def test_build_subagent_intent_carries_role_and_allowlist() -> None:
    intent = build_subagent_intent(
        "investigate",
        role_id="researcher",
        model="m1",
        thread_id="child-1",
        conversation_messages=[{"role": "user", "content": "prior"}],
        tool_allowlist=["web_search"],
        metadata={"workspace_path": "/ws"},
    )
    assert intent.raw == "investigate"
    assert intent.normalized_goal == "investigate"
    assert intent.user_context["model_name"] == "m1"
    assert intent.user_context["thread_id"] == "child-1"
    assert intent.user_context["tool_allowlist"] == ["web_search"]
    assert intent.user_context["conversation_messages"] == [
        {"role": "user", "content": "prior"},
    ]
    assert intent.user_context["auto_approve"] is True


def test_run_subagent_react_loop_streams_text_and_concludes() -> None:
    router = _ScriptedRouter(["Final Answer: 找到答案了"])
    stack = _FakeStack(router)
    emitter = _CaptureEmitter()

    # Bind a session carrying the coordination root so the typed bus receives
    # the conclusion event (mirrors the parent-turn session scoping).
    from runtime.platform.process.session import Session, _current_session

    sess = Session(
        thread_id="child-1",
        conversation_id="child-1",
        metadata={"root_thread_id": "root-1", "thread_id": "child-1"},
    )
    token = _current_session.set(sess)
    try:
        result = run_subagent_react_loop(
            stack,
            prompt="去调研一下",
            role_id="researcher",
            model="test-model",
            thread_id="child-1",
            session_id="sess-1",
            emitter=emitter,
        )
    finally:
        _current_session.reset(token)

    assert result is not None
    assert result.success
    assert result.final_answer == "找到答案了"

    deltas = [e for e in emitter.events if e.get("type") == "sub_text_delta"]
    assert deltas, "expected streamed text deltas on the emitter"
    # The react loop strips the "Final Answer:" marker before streaming the
    # final prose, so the streamed text equals the final answer itself.
    assert "".join(e["delta"] for e in deltas) == "找到答案了"

    from runtime.execution.subagents.event_bus import get_bus

    bus = get_bus("root-1")
    assert bus is not None
    kinds = [e.get("type") for e in bus.replay() if isinstance(e, dict)]
    assert "sub_concluded" in kinds
    concluded = [e for e in bus.replay() if e.get("type") == "sub_concluded"][-1]
    assert concluded["payload"].get("role") == "researcher"
    assert concluded["payload"].get("ok") is True


def test_run_subagent_react_loop_emits_failed_on_react_error(
    monkeypatch,
) -> None:
    import runtime.execution.subagents.react_drive as react_drive

    def _erroring_loop(stack, intent, agent, **kwargs):
        yield {"type": "react_error", "message": "upstream boom"}
        return None

    monkeypatch.setattr(react_drive, "stream_react_loop", _erroring_loop)

    from runtime.platform.process.session import Session, _current_session

    sess = Session(
        thread_id="child-2",
        conversation_id="child-2",
        metadata={"root_thread_id": "root-2", "thread_id": "child-2"},
    )
    token = _current_session.set(sess)
    try:
        run_subagent_react_loop(
            _FakeStack(None),
            prompt="试试",
            role_id="explorer",
            model="test-model",
            thread_id="child-2",
        )
    finally:
        _current_session.reset(token)

    from runtime.execution.subagents.event_bus import get_bus

    bus = get_bus("root-2")
    kinds = [e.get("type") for e in bus.replay() if isinstance(e, dict)]
    assert "sub_failed" in kinds
    failed = [e for e in bus.replay() if e.get("type") == "sub_failed"][-1]
    assert failed["payload"].get("ok") is False
    assert "upstream boom" in failed["payload"].get("error", "")


def test_runner_uses_react_loop_when_opted_in() -> None:
    from runtime.execution.suckers.ephemeral_agents import (
        BUILTIN_ROLES,
        EphemeralCall,
    )
    from runtime.execution.suckers.ephemeral_runner import make_llm_ephemeral_runner

    class _Router:
        default_model = "test-model"

        def call(self, req):
            return _FakeResponse(text="react-loop answer")

        def call_stream(self, req):
            from runtime.sensing.model_router.models import (
                CostEntry,
                ModelResponse,
                ModelStreamEvent,
            )

            yield ModelStreamEvent(type="text_delta", delta="react-loop answer")
            yield ModelStreamEvent(
                type="done",
                final=ModelResponse(
                    text="react-loop answer",
                    model="test-model",
                    finish_reason="stop",
                    cost=CostEntry(),
                ),
            )

    router = _Router()
    runner = make_llm_ephemeral_runner(
        router,
        registry=None,
        default_model="test-model",
    )
    call = EphemeralCall(
        role=BUILTIN_ROLES["reviewer"],
        user_prompt="review the diff",
        composed_system_prompt="reviewer persona",
        caller_thread_id="t-1",
        caller_agent_id="coder",
        context={
            "react_loop_subagent": True,
            "react_stack": _FakeStack(router),
        },
    )
    assert runner(call) == "react-loop answer"


def test_runner_keeps_mini_loop_when_react_not_opted_in() -> None:
    from runtime.execution.suckers.ephemeral_agents import (
        BUILTIN_ROLES,
        EphemeralCall,
    )
    from runtime.execution.suckers.ephemeral_runner import make_llm_ephemeral_runner

    class _Router:
        default_model = "test-model"

        def call(self, req):
            return _FakeResponse(text="mini-loop answer")

        def call_stream(self, req):
            from runtime.sensing.model_router.models import (
                CostEntry,
                ModelResponse,
                ModelStreamEvent,
            )

            yield ModelStreamEvent(type="text_delta", delta="mini-loop answer")
            yield ModelStreamEvent(
                type="done",
                final=ModelResponse(
                    text="mini-loop answer",
                    model="test-model",
                    finish_reason="stop",
                    cost=CostEntry(),
                ),
            )

    runner = make_llm_ephemeral_runner(
        _Router(),
        registry=None,
        default_model="test-model",
    )
    call = EphemeralCall(
        role=BUILTIN_ROLES["reviewer"],
        user_prompt="review",
        composed_system_prompt="reviewer persona",
        caller_thread_id="t-1",
        caller_agent_id="coder",
        context={},  # no react opt-in -> mini-loop single-shot
    )
    assert runner(call) == "mini-loop answer"


def test_react_loop_carries_role_persona_to_the_model() -> None:
    """The react-loop path must keep the role persona/context the mini-loop
    injected via ``composed_system_prompt`` (role + caller history + memory),
    otherwise the child loses its role after the flip to the main loop."""
    from runtime.execution.suckers.ephemeral_agents import (
        BUILTIN_ROLES,
        EphemeralCall,
    )
    from runtime.execution.suckers.ephemeral_runner import make_llm_ephemeral_runner

    class _CapturingRouter:
        def __init__(self):
            self.default_model = "test-model"
            self.requests: list = []

        def call(self, req):
            self.requests.append(req)
            return _FakeResponse(text="done")

        def call_stream(self, req):
            from runtime.sensing.model_router.models import (
                CostEntry,
                ModelResponse,
                ModelStreamEvent,
            )

            self.requests.append(req)
            yield ModelStreamEvent(type="text_delta", delta="done")
            yield ModelStreamEvent(
                type="done",
                final=ModelResponse(
                    text="done",
                    model="test-model",
                    finish_reason="stop",
                    cost=CostEntry(),
                ),
            )

    router = _CapturingRouter()
    runner = make_llm_ephemeral_runner(
        router,
        registry=None,
        default_model="test-model",
    )
    persona = "ROLE_SYSTEM: reviewer scans diffs for bugs"
    call = EphemeralCall(
        role=BUILTIN_ROLES["reviewer"],
        user_prompt="check this diff",
        composed_system_prompt=persona,
        caller_thread_id="t-1",
        caller_agent_id="coder",
        context={
            "react_loop_subagent": True,
            "react_stack": _FakeStack(router),
        },
    )
    assert runner(call) == "done"
    assert router.requests, "expected the react loop to call the model"
    joined = "\n".join(
        str(m.content) for req in router.requests for m in getattr(req, "messages", [])
    )
    assert persona in joined
    assert "check this diff" in joined


def test_bus_tool_and_conclude_events_carry_per_child_codename() -> None:
    """Parallel children sharing a role must stay distinct lanes on the bus.

    The typed event bus keys lanes by ``codename``; tool / conclude / fail
    events are stamped with the child's codename read off the bound run
    Session, so two same-role children render as two independent threads
    instead of merging into one lane in the workbench substream.
    """
    from runtime.execution.subagents.event_bus import get_bus
    from runtime.execution.subagents.react_drive import _SimpleToolCall
    from runtime.execution.suckers._ephemeral_events import (
        _emit_sub_tool_event,
    )
    from runtime.platform.process.session import Session, _current_session

    sess = Session(
        thread_id="child-1",
        conversation_id="child-1",
        metadata={
            "root_thread_id": "root-codename",
            "thread_id": "child-1",
            "subagent_codename": "Spark-9f2",
        },
    )
    token = _current_session.set(sess)
    try:
        _emit_sub_tool_event(
            "sub_tool_start",
            role_id="researcher",
            tool_call=_SimpleToolCall(call_id="c1", name="web_search"),
            iteration=1,
        )
        from runtime.execution.subagents.react_drive import run_subagent_react_loop

        run_subagent_react_loop(
            _FakeStack(_ScriptedRouter(["Final Answer: 完成了"])),
            prompt="go",
            role_id="researcher",
            model="test-model",
            thread_id="child-1",
        )
    finally:
        _current_session.reset(token)

    bus = get_bus("root-codename")
    assert bus is not None
    events = [e for e in bus.replay() if isinstance(e, dict)]
    assert events, "expected bus events"
    assert any(e.get("type") == "sub_concluded" for e in events), (
        "expected a concluded event so the codename assertion is non-vacuous"
    )
    for ev in events:
        payload = ev.get("payload") or {}
        if ev.get("type") in ("sub_tool_start", "sub_concluded"):
            assert payload.get("codename") == "Spark-9f2", f"{ev['type']} lost the child codename"


def test_bridge_flips_react_loop_default_inside_react_stack(monkeypatch) -> None:
    """A sub-agent dispatched inside the parent react loop drives through the
    MAIN react loop by default (no per-call opt-in needed), and an explicit
    ``react_loop_subagent=False`` is respected as an opt-out."""
    import runtime.execution.subagents.bridge as bridge
    from runtime.execution.subagents._ambient import react_stack_scope
    from runtime.platform.process.session import Session, _current_session

    captured: dict[str, dict] = {}

    def fake_dispatch(**kwargs):  # noqa: ARG001
        captured["context"] = dict(kwargs.get("context") or {})
        return {
            "agent_id": kwargs.get("agent_id") or "",
            "output": "done",
            "success": True,
        }

    monkeypatch.setattr(bridge, "_dispatch", fake_dispatch)

    sess = Session(thread_id="t-1", metadata={})
    with react_stack_scope(_FakeStack(_ScriptedRouter(["x"]))):
        _token = _current_session.set(sess)
        try:
            bridge.call_subagent(agent_id="researcher", prompt="go", session=sess)
        finally:
            _current_session.reset(_token)

    assert captured.get("context", {}).get("react_loop_subagent") is True
    assert captured.get("context", {}).get("flip_subagent_thread") is True
    assert captured.get("context", {}).get("react_stack") is not None

    # Explicit opt-out is honoured.
    captured.clear()
    with react_stack_scope(_FakeStack(_ScriptedRouter(["x"]))):
        _token = _current_session.set(sess)
        try:
            bridge.call_subagent(
                agent_id="researcher",
                prompt="go",
                session=sess,
                context={
                    "react_loop_subagent": False,
                    "flip_subagent_thread": False,
                },
            )
        finally:
            _current_session.reset(_token)

    assert captured.get("context", {}).get("react_loop_subagent") is False
    assert captured.get("context", {}).get("flip_subagent_thread") is False


def test_react_loop_forwards_tool_events_to_emitter(monkeypatch) -> None:
    """The react-loop path must mirror tool events onto the emitter (same
    shape as the mini-loop) so the bridge's round tracking populates the
    parent finish card's iteration count instead of reporting 0 rounds."""
    import runtime.execution.subagents.react_drive as react_drive

    def _scripted_loop(stack, intent, agent, **kwargs):  # noqa: ARG001
        yield {
            "type": "tool_start",
            "tool_name": "web_search",
            "tool_call_id": "c1",
            "input": {"q": "leaks"},
        }
        yield {
            "type": "tool_end",
            "tool_name": "edit_file",
            "tool_call_id": "c1",
            "status": "success",
            "duration_ms": 12,
            "input": {"path": "/ws/foo.py"},
            "output": "results",
        }
        yield {"type": "react_completed"}
        from runtime.core.cerebrum.react_loop import ReActResult

        return ReActResult(success=True, final_answer="done")

    monkeypatch.setattr(react_drive, "stream_react_loop", _scripted_loop)

    from runtime.platform.process.session import Session, _current_session

    emitter = _CaptureEmitter()
    sess = Session(
        thread_id="child-3",
        conversation_id="child-3",
        metadata={"root_thread_id": "root-3", "thread_id": "child-3"},
    )
    token = _current_session.set(sess)
    try:
        react_drive.run_subagent_react_loop(
            _FakeStack(None),
            prompt="search",
            role_id="researcher",
            model="test-model",
            thread_id="child-3",
            emitter=emitter,
        )
    finally:
        _current_session.reset(token)

    starts = [e for e in emitter.events if e.get("type") == "sub_tool_start"]
    ends = [e for e in emitter.events if e.get("type") == "sub_tool_end"]
    assert len(starts) == 1, "expected one tool_start mirrored to the emitter"
    assert starts[0]["round"] == 1
    assert starts[0]["skill"] == "web_search"
    assert len(ends) == 1
    assert ends[0]["status"] == "success"
    assert ends[0]["round"] == 1
    # ``args`` rides on the end event so the bridge's file-touch tracking can
    # list files the sub-agent wrote on the parent finish card.
    assert ends[0]["args"] == {"path": "/ws/foo.py"}


class TestRestrictedDispatchGate:
    """Audit F-01: the react-drive path runs a sub-agent on the MAIN react
    loop, which does not apply the mini-loop's security enforcements (the
    read-only intersection for judges, the locked-write-root confinement for
    isolated spawns). Restricted dispatches must be refused by the gate and
    fall back to the mini-loop where both are enforced."""

    def test_dispatch_is_restricted_matrix(self) -> None:
        from runtime.execution.subagents.react_drive import dispatch_is_restricted

        assert dispatch_is_restricted(None, None) is False
        assert dispatch_is_restricted({}, {}) is False
        assert dispatch_is_restricted({"tool_allowlist_read_only": True}, {}) is True
        assert dispatch_is_restricted({}, {"_locked_write_root": "/wt"}) is True
        assert (
            dispatch_is_restricted(
                {"tool_allowlist_read_only": True},
                {"_locked_write_root": "/wt"},
            )
            is True
        )

    def _spy_react_drive(self, monkeypatch) -> list:
        from types import SimpleNamespace

        import runtime.execution.subagents.react_drive as react_drive

        calls: list[dict] = []

        def _spy(stack, **kwargs):  # noqa: ARG001
            calls.append(kwargs)
            return SimpleNamespace(final_answer="react answer")

        monkeypatch.setattr(react_drive, "run_subagent_react_loop", _spy)
        return calls

    def _make_runner_and_call(self, context: dict):
        from runtime.execution.suckers.ephemeral_agents import (
            BUILTIN_ROLES,
            EphemeralCall,
        )
        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )

        class _Router:
            default_model = "test-model"

            def call(self, req):  # noqa: ARG002
                return _FakeResponse(text="mini-loop fallback answer")

        runner = make_llm_ephemeral_runner(_Router(), registry=None, default_model="test-model")
        call = EphemeralCall(
            role=BUILTIN_ROLES["reviewer"],
            user_prompt="review",
            composed_system_prompt="reviewer persona",
            caller_thread_id="t-1",
            caller_agent_id="coder",
            context=context,
        )
        return runner, call

    def test_read_only_judge_is_refused_by_react_drive(self, monkeypatch) -> None:
        calls = self._spy_react_drive(monkeypatch)
        runner, call = self._make_runner_and_call(
            {
                "react_loop_subagent": True,
                "react_stack": _FakeStack(_ScriptedRouter(["x"])),
                "tool_allowlist_read_only": True,
            }
        )
        assert runner(call) == "mini-loop fallback answer"
        assert calls == []

    def test_locked_write_root_is_refused_by_react_drive(self, monkeypatch) -> None:
        from runtime.platform.process.session import Session, session_scope

        calls = self._spy_react_drive(monkeypatch)
        runner, call = self._make_runner_and_call(
            {
                "react_loop_subagent": True,
                "react_stack": _FakeStack(_ScriptedRouter(["x"])),
            }
        )
        sess = Session(thread_id="t-iso", metadata={"_locked_write_root": "/tmp/wt"})
        with session_scope(sess):
            assert runner(call) == "mini-loop fallback answer"
        assert calls == []

    def test_durable_report_session_is_refused_by_react_drive(self, monkeypatch) -> None:
        calls = self._spy_react_drive(monkeypatch)
        runner, call = self._make_runner_and_call(
            {
                "react_loop_subagent": True,
                "react_stack": _FakeStack(_ScriptedRouter(["x"])),
                "subagent_session_id": "session-with-report-lane",
            }
        )
        assert runner(call) == "mini-loop fallback answer"
        assert calls == []

    def test_unrestricted_dispatch_still_uses_react_drive(self, monkeypatch) -> None:
        calls = self._spy_react_drive(monkeypatch)
        runner, call = self._make_runner_and_call(
            {
                "react_loop_subagent": True,
                "react_stack": _FakeStack(_ScriptedRouter(["x"])),
            }
        )
        assert runner(call) == "react answer"
        assert len(calls) == 1

