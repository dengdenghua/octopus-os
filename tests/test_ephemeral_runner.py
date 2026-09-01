"""
Tests for the LLM-backed ephemeral sub-agent runner.

Verifies the bridge between ``EphemeralCall`` and ``ModelRouter`` ·
the last missing piece that makes ``call_agent("reviewer", ...)``
actually reach an LLM instead of returning a "not configured" stub.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_runner():
    from runtime.execution.suckers.ephemeral_agents import (
        set_ephemeral_role_runner,
    )

    set_ephemeral_role_runner(None)
    yield
    set_ephemeral_role_runner(None)


def _make_call(role_id: str = "reviewer", user_prompt: str = "review"):
    """Build a minimal ``EphemeralCall`` for runner testing."""
    from runtime.execution.suckers.ephemeral_agents import (
        BUILTIN_ROLES,
        EphemeralCall,
    )

    role = BUILTIN_ROLES[role_id]
    return EphemeralCall(
        role=role,
        user_prompt=user_prompt,
        composed_system_prompt=(
            f"{role.system_prompt}\n\n---\n\n## Caller conversation\n**User**: prior message"
        ),
        caller_thread_id="t-test",
        caller_agent_id="coder",
        context={},
    )


class TestFactory:
    def test_requires_model_when_router_has_none(self):
        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )

        class _NoDefault:
            pass

        with pytest.raises(ValueError, match="default_model"):
            make_llm_ephemeral_runner(_NoDefault())

    def test_accepts_explicit_default_model(self):
        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )
        from runtime.sensing.model_router import MockModelRouter

        router = MockModelRouter(response="fixed reply")
        runner = make_llm_ephemeral_runner(
            router,
            default_model="claude-haiku-4-5",
        )
        assert callable(runner)

    def test_pulls_default_from_router_attr(self):
        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )

        class _WithDefault:
            default_model = "m/from-attr"

            def call(self, _req):
                raise NotImplementedError

        runner = make_llm_ephemeral_runner(_WithDefault())
        # Factory must not raise · captures attr at factory time.
        assert callable(runner)


class TestDispatch:
    def test_runner_returns_router_text(self):
        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )
        from runtime.sensing.model_router import MockModelRouter

        router = MockModelRouter(response="the reviewer's verdict")
        runner = make_llm_ephemeral_runner(
            router,
            default_model="claude-haiku-4-5",
        )
        out = runner(_make_call())
        assert out == "the reviewer's verdict"

    def test_request_carries_system_and_user(self):
        """System = composed prompt · user = user_prompt."""
        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )
        from runtime.sensing.model_router import MockModelRouter

        router = MockModelRouter(response="ok")
        runner = make_llm_ephemeral_runner(
            router,
            default_model="claude-haiku-4-5",
        )
        call = _make_call(role_id="reviewer", user_prompt="look at auth.py")
        runner(call)

        # MockModelRouter records the request
        assert len(router.call_log) == 1
        req = router.call_log[0]
        assert req.model == "claude-haiku-4-5"
        assert len(req.messages) == 2
        assert req.messages[0].role == "system"
        assert "code reviewer" in req.messages[0].content.lower()
        assert "prior message" in req.messages[0].content  # conversation
        assert req.messages[1].role == "user"
        assert req.messages[1].content == "look at auth.py"

    def test_provider_error_propagates(self):
        """Runner doesn't swallow exceptions · ``run_ephemeral_role``
        in ephemeral_agents.py is the layer that converts them to
        structured ``{success: False}``."""
        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )

        class _Broken:
            default_model = "m"

            def call(self, _req):
                raise RuntimeError("upstream 503")

        runner = make_llm_ephemeral_runner(_Broken())
        with pytest.raises(RuntimeError, match="upstream 503"):
            runner(_make_call())


class TestEndToEnd:
    def test_call_agent_reviewer_via_live_runner(self):
        """Full loop · call_agent("reviewer", ...) → dispatch →
        ephemeral runner → MockModelRouter → structured result."""
        from runtime.execution.suckers.ephemeral_agents import (
            set_ephemeral_role_runner,
        )
        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )
        from runtime.execution.suckers.sub_agent import _call_agent
        from runtime.sensing.model_router import MockModelRouter

        router = MockModelRouter(response="- Critical: null deref at L42")
        set_ephemeral_role_runner(
            make_llm_ephemeral_runner(router, default_model="claude-haiku-4-5"),
        )

        result = _call_agent(
            agent_id="reviewer",
            prompt="review the auth patch",
        )
        assert result["success"] is True
        assert result["ephemeral"] is True
        assert "Critical: null deref at L42" in result["output"]
        assert result["agent_id"] == "reviewer"

    def test_call_agent_unknown_role_does_not_hit_runner(self):
        """Safety · unknown id routes to the legacy registered-agent
        path (which returns ``not configured`` in tests), NOT to the
        ephemeral runner."""
        from runtime.execution.suckers.ephemeral_agents import (
            set_ephemeral_role_runner,
        )
        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )
        from runtime.execution.suckers.sub_agent import _call_agent
        from runtime.sensing.model_router import MockModelRouter

        router = MockModelRouter(response="should not see this")
        set_ephemeral_role_runner(
            make_llm_ephemeral_runner(router, default_model="m"),
        )

        result = _call_agent(agent_id="coder", prompt="write code")
        # legacy path · no runner → not configured
        assert result["success"] is False
        assert "runner not configured" in (result["error"] or "")
        # Ephemeral router not consulted
        assert len(router.call_log) == 0


# ═══════════════════════════════════════════════════════════
# Agentic loop · token budget + sub-agent event dispatch
# ═══════════════════════════════════════════════════════════


class _ScriptedAgenticRouter:
    """Minimal test double that emits tool_calls then text.

    ``MockModelRouter`` only returns pure-text ``ModelResponse``s · we
    need a router that can produce ``ModelResponse.tool_calls`` to
    exercise the agentic loop. Constructor takes a list of responses;
    each ``call()`` pops the next one. Each response is either a
    plain string (= text, no tool_calls) or a list of tool-call
    specs ``[{"name": ..., "input": {...}}, ...]`` (= tool_calls,
    no text).
    """

    default_model = "mock/scripted"

    def __init__(
        self,
        script,
        input_tokens=500,
        output_tokens=250,
        finish_reasons=None,
    ):
        self._script = list(script)
        self._in = input_tokens
        self._out = output_tokens
        self._finish_reasons = list(finish_reasons or [])
        self.call_log = []

    def call(self, request):
        from runtime.sensing.model_router.models import (
            CostEntry,
            ModelResponse,
            ToolCall,
        )

        self.call_log.append(request)
        if not self._script:
            return ModelResponse(
                text="(end of script)",
                input_tokens=self._in,
                output_tokens=self._out,
                cost=CostEntry(),
            )
        step = self._script.pop(0)
        if isinstance(step, str):
            return ModelResponse(
                text=step,
                input_tokens=self._in,
                output_tokens=self._out,
                cost=CostEntry(),
                finish_reason=(self._finish_reasons.pop(0) if self._finish_reasons else "stop"),
            )
        # Tool-call step
        calls = []
        for i, spec in enumerate(step):
            calls.append(
                ToolCall(
                    id=f"toolu_{len(self.call_log)}_{i}",
                    name=spec["name"],
                    input=spec.get("input", {}),
                )
            )
        return ModelResponse(
            text="",
            tool_calls=calls,
            input_tokens=self._in,
            output_tokens=self._out,
            cost=CostEntry(),
            finish_reason=(self._finish_reasons.pop(0) if self._finish_reasons else "stop"),
        )


class _StubRegistry:
    """Minimal SkillRegistry with exactly the skills a test needs."""

    def __init__(self, handlers):
        self._handlers = handlers  # {name: callable}

    def all_names(self):
        return list(self._handlers.keys())

    def has(self, name):
        return name in self._handlers

    def get(self, name):
        class _Skill:
            def __init__(self, n, h):
                self.name = n
                self.description = f"stub {n}"
                self.handler = h

        h = self._handlers[name]
        return _Skill(name, h)


def test_agentic_runner_uses_effective_context_allowlist():
    from runtime.execution.suckers.ephemeral_runner import (
        make_llm_ephemeral_runner,
    )

    router = _ScriptedAgenticRouter(script=["done"])
    registry = _StubRegistry(
        {
            "read_file": lambda **kw: {"ok": True},
            "web_search": lambda **kw: {"ok": True},
            "bb_write": lambda **kw: {"ok": True},
        }
    )
    runner = make_llm_ephemeral_runner(
        router,
        registry=registry,
        default_model="m",
    )
    call = _make_call(role_id="reviewer")
    call.context["tool_allowlist"] = ["web_search", "bb_write"]

    assert runner(call) == "done"

    tool_names = [tool.name for tool in router.call_log[0].tools]
    assert tool_names == ["web_search", "bb_write"]


class TestTokenBudget:
    def test_length_limited_text_continues_in_next_round(self):
        """A sub-agent that hits the provider output cap should not
        silently return a half sentence as completed. The runner asks
        for continuation and concatenates the follow-up text."""
        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )

        router = _ScriptedAgenticRouter(
            script=["partial section ending at hardware+", " service model done"],
            finish_reasons=["length", "stop"],
        )
        registry = _StubRegistry(
            {
                "bb_read": lambda **kw: {"ok": True, "value": "notes"},
            }
        )
        runner = make_llm_ephemeral_runner(
            router,
            registry=registry,
            default_model="m",
            token_budget=0,
        )

        out = runner(_make_call())

        assert out == "partial section ending at hardware+ service model done"
        assert len(router.call_log) == 2
        assert "Continue exactly where it stopped" in (router.call_log[1].messages[-1].content)

    def test_near_output_cap_continues_even_without_finish_reason(self):
        """Some providers report ``stop`` while still cutting output near
        max_tokens. Guard against silently accepting that half-report."""
        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )

        partial = "long report stops mid " + ("section " * 220)
        router = _ScriptedAgenticRouter(
            script=[partial, "sentence and finishes."],
            output_tokens=2_000,
            finish_reasons=["stop", "stop"],
        )
        registry = _StubRegistry(
            {
                "bb_read": lambda **kw: {"ok": True, "value": "notes"},
            }
        )
        runner = make_llm_ephemeral_runner(
            router,
            registry=registry,
            default_model="m",
            token_budget=0,
        )

        out = runner(_make_call())

        assert out == partial + "sentence and finishes."
        assert len(router.call_log) == 2
        assert "Continue exactly where it stopped" in (router.call_log[1].messages[-1].content)

    def test_mid_sized_unfinished_report_continues(self):
        """A provider may return ``stop`` after a visibly dangling report
        fragment that is not near max_tokens. Continue once instead of
        showing the user a chopped final answer."""
        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )

        partial = "## report\n" + ("detail " * 260) + "product launch event"
        router = _ScriptedAgenticRouter(
            script=[partial, " notes are listed."],
            output_tokens=900,
            finish_reasons=["stop", "stop"],
        )
        registry = _StubRegistry(
            {
                "bb_read": lambda **kw: {"ok": True, "value": "notes"},
            }
        )
        runner = make_llm_ephemeral_runner(
            router,
            registry=registry,
            default_model="m",
            token_budget=0,
        )

        out = runner(_make_call())

        assert out == partial + " notes are listed."
        assert len(router.call_log) == 2

    def test_budget_no_longer_stops_loop_before_next_round(self):
        """token_budget remains accepted for compatibility but is ignored."""
        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )

        # Router emits a tool_use round, then (if asked) plain text.
        router = _ScriptedAgenticRouter(
            script=[
                [{"name": "bb_write", "input": {"key": "k", "value": "v"}}],
                "final text after tool",
            ],
            input_tokens=8_000,
            output_tokens=3_000,
        )
        registry = _StubRegistry(
            {
                "bb_write": lambda **kw: {"ok": True},
            }
        )
        runner = make_llm_ephemeral_runner(
            router,
            registry=registry,
            default_model="m",
            token_budget=10_000,
        )
        out = runner(_make_call())
        # Both rounds should run now; token_budget is no longer enforced.
        assert len(router.call_log) == 2
        assert out == "final text after tool"

    def test_budget_zero_disables_enforcement(self):
        """token_budget=0 is still accepted, but has no special effect."""
        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )

        router = _ScriptedAgenticRouter(
            script=[
                [{"name": "bb_write", "input": {"key": "k"}}],
                "done",
            ],
            input_tokens=9_999,
            output_tokens=9_999,  # huge
        )
        registry = _StubRegistry(
            {
                "bb_write": lambda **kw: {"ok": True},
            }
        )
        runner = make_llm_ephemeral_runner(
            router,
            registry=registry,
            default_model="m",
            token_budget=0,  # disable
        )
        out = runner(_make_call())
        # Both rounds fire: tool-use round + final text round.
        assert len(router.call_log) == 2
        assert out == "done"


class _StreamingRouter:
    """Test double that exercises the ``call_stream`` path.

    The ephemeral runner now prefers ``router.call_stream`` so role
    text streams to the parent emitter as ``sub_text_delta`` chunks
    instead of buffering the whole reply. This double splits a final
    text payload into N chunks, then emits ``done`` with token counts.
    """

    default_model = "mock/streaming"

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.call_log = []

    def call(self, request):
        # Required by the runner's fallback path; tests that drive
        # ``call_stream`` should never end up here.
        from runtime.sensing.model_router.models import CostEntry, ModelResponse

        return ModelResponse(
            text="".join(self._chunks),
            input_tokens=10,
            output_tokens=20,
            cost=CostEntry(),
        )

    def call_stream(self, request):
        from runtime.sensing.model_router.models import (
            CostEntry,
            ModelResponse,
            ModelStreamEvent,
        )

        self.call_log.append(request)
        full = ""
        for chunk in self._chunks:
            full += chunk
            yield ModelStreamEvent(type="text_delta", delta=chunk)
        yield ModelStreamEvent(
            type="done",
            final=ModelResponse(
                text=full,
                input_tokens=10,
                output_tokens=20,
                cost=CostEntry(),
            ),
        )


class TestSubTextDeltaStreaming:
    def test_streaming_router_emits_sub_text_delta_chunks(self):
        """Live observability: when the router can stream, the runner
        forwards each text chunk as ``sub_text_delta`` so swarm
        consumers can render role prose as it lands instead of waiting
        for the full role to finish (the original "30s blank then dump"
        UX bug)."""
        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )

        router = _StreamingRouter(
            chunks=["第一段。", "第二段。", "第三段最后。"],
        )
        captured: list[dict] = []

        def _emitter(event: dict) -> None:
            captured.append(event)

        runner = make_llm_ephemeral_runner(
            router,
            default_model="m",
            token_budget=0,
        )
        call = _make_call()
        # Inject the emitter through context like the bridge does.
        call.context["event_emitter"] = _emitter
        out = runner(call)
        assert out == "第一段。第二段。第三段最后。"
        deltas = [e for e in captured if e["type"] == "sub_text_delta"]
        assert [e["delta"] for e in deltas] == [
            "第一段。",
            "第二段。",
            "第三段最后。",
        ]
        assert all(e["agent_id"] == "reviewer" for e in deltas)

    def test_router_without_call_stream_falls_back_to_call(self):
        """Test routers without ``call_stream`` (e.g. the legacy
        ``_ScriptedAgenticRouter``) must still work — runner falls
        back to the non-streaming ``call()`` path. Regression guard
        for the test suite breaking when the streaming switch landed.
        """
        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )

        router = _ScriptedAgenticRouter(script=["plain text reply"])
        runner = make_llm_ephemeral_runner(
            router,
            default_model="m",
            token_budget=0,
        )
        out = runner(_make_call())
        assert out == "plain text reply"
        assert len(router.call_log) == 1


class TestSubToolEventDispatch:
    def test_events_pushed_to_session_queue(self):
        """Sub-agent tool events land on the queue wired through
        Session.metadata with the right payload shape."""
        import queue as _q

        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )
        from runtime.platform.process.session import Session, _current_session

        router = _ScriptedAgenticRouter(
            script=[
                [{"name": "bb_write", "input": {"key": "postgres", "value": "…"}}],
                "final synthesis",
            ],
        )
        registry = _StubRegistry(
            {
                "bb_write": lambda **kw: {"ok": True},
            }
        )
        runner = make_llm_ephemeral_runner(
            router,
            registry=registry,
            default_model="m",
        )

        q: _q.Queue = _q.Queue()
        sess = Session()
        sess.metadata["sub_tool_event_queue"] = q
        sess.metadata["_active_parent_tool_use_id"] = "toolu_parent_42"
        tok = _current_session.set(sess)
        try:
            out = runner(_make_call())
        finally:
            _current_session.reset(tok)

        assert out == "final synthesis"
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        # Expect exactly one start+end pair for the single tool call.
        kinds = [i[0] for i in items]
        assert kinds == ["sub_tool_start", "sub_tool_end"], kinds
        start_payload = items[0][1]
        assert start_payload["name"] == "bb_write"
        assert start_payload["parent_tool_use_id"] == "toolu_parent_42"
        assert start_payload["sub_agent_role"] == "reviewer"
        assert start_payload["input"] == {"key": "postgres", "value": "…"}
        end_payload = items[1][1]
        assert end_payload["is_error"] is False
        assert end_payload["parent_tool_use_id"] == "toolu_parent_42"

    def test_missing_queue_is_silent_noop(self):
        """No session / no queue → runner still completes normally,
        no events, no exceptions."""
        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )

        router = _ScriptedAgenticRouter(
            script=[
                [{"name": "bb_write", "input": {"key": "k"}}],
                "ok",
            ],
        )
        registry = _StubRegistry(
            {
                "bb_write": lambda **kw: {"ok": True},
            }
        )
        runner = make_llm_ephemeral_runner(
            router,
            registry=registry,
            default_model="m",
        )
        # No session bound · the event helper silently no-ops.
        out = runner(_make_call())
        assert out == "ok"
        assert len(router.call_log) == 2

    def test_is_error_flag_preserved(self):
        """Tool handler raising → end event is_error=True."""
        import queue as _q

        from runtime.execution.suckers.ephemeral_runner import (
            make_llm_ephemeral_runner,
        )
        from runtime.platform.process.session import Session, _current_session

        def _raising(**kw):
            raise RuntimeError("boom")

        router = _ScriptedAgenticRouter(
            script=[
                [{"name": "bb_write", "input": {}}],
                "recovered",
            ],
        )
        registry = _StubRegistry({"bb_write": _raising})
        runner = make_llm_ephemeral_runner(
            router,
            registry=registry,
            default_model="m",
        )
        q: _q.Queue = _q.Queue()
        sess = Session()
        sess.metadata["sub_tool_event_queue"] = q
        tok = _current_session.set(sess)
        try:
            runner(_make_call())
        finally:
            _current_session.reset(tok)
        items = [q.get_nowait() for _ in range(q.qsize())]
        end = next(i for i in items if i[0] == "sub_tool_end")
        assert end[1]["is_error"] is True


class TestToolBridgeParentIdTracking:
    def test_session_meta_flips_around_tool_call(self):
        """Parent agentic loop should stash/clear
        ``_active_parent_tool_use_id`` so sub-agents can correlate."""
        from runtime.sensing.gateway import _tool_bridge_loop

        # We validate the source directly · the integration path
        # requires a full stack, but the invariant we care about
        # (set before handler, pop after) is structural.
        src = _tool_bridge_loop.__file__
        assert src is not None
        with open(src, encoding="utf-8") as f:
            text = f.read()
        assert 'metadata["_active_parent_tool_use_id"] = call.id' in text
        assert "metadata.pop(" in text
        assert '"_active_parent_tool_use_id"' in text


class TestEphemeralInjectionTaintGate:
    """Ephemeral sub-agents run skills via a direct ``handler(**input)`` call
    — they bypass ``stack.executor`` and so the injection-taint chokepoint AND
    the approval channel. When the delegating parent's turn was
    injection-tainted (carried in ``call.context``), a risky tool here must be
    blocked fail-closed (there's no human to escalate to)."""

    @staticmethod
    def _call(name, args, *, context):
        from types import SimpleNamespace

        return SimpleNamespace(name=name, input=args, context=context, id="tu-1")

    @pytest.fixture(autouse=True)
    def _reset_taint(self):
        from runtime.safety.validation.prompt_injection import (
            reset_injection_taint,
        )

        reset_injection_taint()
        yield
        reset_injection_taint()

    def test_inherited_taint_blocks_risky_tool(self):
        from runtime.execution.suckers.ephemeral_runner import (
            _execute_tool_in_subagent,
        )

        ran = {"exec": False}

        def _shell(**_kw):
            ran["exec"] = True
            return {"exit_code": 0}

        registry = _StubRegistry({"exec_shell": _shell})
        call = self._call(
            "exec_shell",
            {"command": "echo hi"},
            context={"_inherited_injection_taint": "high"},
        )

        output, is_error = _execute_tool_in_subagent(registry, call)

        assert is_error is True
        assert "prompt_injection_taint" in output
        assert ran["exec"] is False, "tainted risky handler must NOT run"

    def test_inherited_taint_allows_low_risk_tool(self):
        """The gate is risk-scoped: a tainted parent can still delegate a
        read-only tool (low risk) — only medium+ tools are blocked."""
        from runtime.execution.suckers.ephemeral_runner import (
            _execute_tool_in_subagent,
        )

        registry = _StubRegistry({"read_file": lambda **_kw: {"content": "ok"}})
        call = self._call(
            "read_file",
            {"path": "x"},
            context={"_inherited_injection_taint": "high"},
        )

        output, is_error = _execute_tool_in_subagent(registry, call)

        assert is_error is False
        assert "prompt_injection_taint" not in output

    def test_clean_parent_lets_risky_tool_run(self):
        """Control: no inherited taint → the risky tool runs normally."""
        from runtime.execution.suckers.ephemeral_runner import (
            _execute_tool_in_subagent,
        )

        ran = {"exec": False}

        def _shell(**_kw):
            ran["exec"] = True
            return {"exit_code": 0, "stdout": "ok"}

        registry = _StubRegistry({"exec_shell": _shell})
        call = self._call("exec_shell", {"command": "echo hi"}, context={})

        output, is_error = _execute_tool_in_subagent(registry, call)

        assert is_error is False
        assert ran["exec"] is True
        assert "prompt_injection_taint" not in output
