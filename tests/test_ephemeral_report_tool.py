"""Child→parent ``report`` tool in the in-process ephemeral runner.

Ported from dsh ``tool-subagent-report``: a continuable in-process child gets
a ``report`` tool (plus usage guidance) so it can deliver self-contained
findings to its direct parent mid-round, instead of only via the transcript
the bridge pulls afterwards. Only present when a durable subagent session id
is stamped into the dispatch context; roots, one-shot children, and remote
providers never see it.
"""

from __future__ import annotations

from runtime.execution.subagents.sessions import (
    SubagentSessionStore,
    get_subagent_session_store,
    set_subagent_session_store,
)
from runtime.platform.process.session import Session
from tests.test_ephemeral_runner import (
    _ScriptedAgenticRouter,
    _StubRegistry,
)


def _store(tmp_path) -> SubagentSessionStore:
    store = SubagentSessionStore(base_dir=tmp_path)
    set_subagent_session_store(store)
    return store


def _make_runner(script, *, context=None):
    from runtime.execution.suckers.ephemeral_agents import (
        BUILTIN_ROLES,
        EphemeralCall,
    )
    from runtime.execution.suckers.ephemeral_runner import (
        make_llm_ephemeral_runner,
    )

    role = BUILTIN_ROLES["researcher"]
    router = _ScriptedAgenticRouter(script)
    runner = make_llm_ephemeral_runner(
        router,
        registry=_StubRegistry({"read_file": lambda **_: "file"}),
        default_model="mock/scripted",
    )
    call = EphemeralCall(
        role=role,
        user_prompt="find the answer",
        composed_system_prompt=role.system_prompt,
        caller_thread_id="t-test",
        caller_agent_id="coder",
        context=dict(context or {}),
    )
    return runner, router, call


def test_report_not_exposed_without_session() -> None:
    runner, router, call = _make_runner(["done"], context={})
    assert runner(call) == "done"
    tool_names = {t.name for t in router.call_log[0].tools}
    assert "report" not in tool_names
    system = router.call_log[0].messages[0].content
    assert "## report tool" not in system


def test_report_exposed_with_session_and_guidance() -> None:
    runner, router, call = _make_runner(
        ["done"],
        context={"subagent_session_id": "sess-1"},
    )
    assert runner(call) == "done"
    tool_names = {t.name for t in router.call_log[0].tools}
    assert "report" in tool_names
    spec = next(t for t in router.call_log[0].tools if t.name == "report")
    assert spec.input_schema["required"] == ["output"]
    assert "output" in spec.input_schema["properties"]
    system = router.call_log[0].messages[0].content
    assert "## report tool" in system
    assert "successful report ends this child run immediately" in system


def test_report_delivers_and_ends_child_run(tmp_path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="t-1")
    runner, router, call = _make_runner(
        [[{"name": "report", "input": {"output": "mid finding"}}], "final answer"],
        context={"subagent_session_id": session.session_id},
    )
    assert runner(call) == "mid finding"

    pending = store.pending_reports(session.session_id)
    assert len(pending) == 1
    index, report = pending[0]
    assert report.content == "mid finding"
    assert report.delivery == "wakeup"
    assert index == 0
    # A successful report is terminal; the scripted follow-up round is never
    # requested, so a model cannot repeatedly report the same result.
    assert len(router.call_log) == 1


def test_report_quiet_delivery_policy(tmp_path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="t-1")
    runner, router, call = _make_runner(
        [[{"name": "report", "input": {"output": "quiet note"}}], "done"],
        context={
            "subagent_session_id": session.session_id,
            "subagent_report_delivery": "quiet",
        },
    )
    assert runner(call) == "quiet note"
    assert len(router.call_log) == 1
    _index, report = store.pending_reports(session.session_id)[0]
    assert report.delivery == "quiet"


def test_report_queued_while_parent_busy(tmp_path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="t-1")
    store.mark_owner_busy(session.session_id)
    runner, router, call = _make_runner(
        [[{"name": "report", "input": {"output": "busy finding"}}], "done"],
        context={"subagent_session_id": session.session_id},
    )
    assert runner(call) == "busy finding"

    _index, report = store.pending_reports(session.session_id)[0]
    assert report.delivery == "queued"
    assert len(router.call_log) == 1


def test_report_ignores_later_tools_in_same_model_response(tmp_path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="t-1")
    runner, router, call = _make_runner(
        [
            [
                {"name": "report", "input": {"output": "final finding"}},
                {"name": "read_file", "input": {"path": "too-late.txt"}},
            ],
            "unreachable",
        ],
        context={"subagent_session_id": session.session_id},
    )

    assert runner(call) == "final finding"
    assert len(router.call_log) == 1
    assert [report.content for _, report in store.pending_reports(session.session_id)] == [
        "final finding"
    ]


def test_report_failure_is_error_but_round_continues(tmp_path) -> None:
    _store(tmp_path)  # store exists but the session id does not
    runner, router, call = _make_runner(
        [[{"name": "report", "input": {"output": "orphan"}}], "done"],
        context={"subagent_session_id": "no-such-session"},
    )
    assert runner(call) == "done"
    user_msg = router.call_log[1].messages[-1].content
    assert user_msg[0]["type"] == "tool_result"
    assert user_msg[0]["is_error"] is True
    assert "no subagent session" in user_msg[0]["content"]


def test_report_requires_nonempty_output(tmp_path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="t-1")
    runner, router, call = _make_runner(
        [[{"name": "report", "input": {}}], "done"],
        context={"subagent_session_id": session.session_id},
    )
    assert runner(call) == "done"
    user_msg = router.call_log[1].messages[-1].content
    assert user_msg[0]["is_error"] is True
    assert '"output" must be a non-empty string' in user_msg[0]["content"]
    assert store.pending_reports(session.session_id) == []


def test_bridge_stamps_session_id_into_ephemeral_context(tmp_path, monkeypatch) -> None:
    from runtime.execution.subagents import bridge
    from runtime.execution.suckers import ephemeral_agents

    store = _store(tmp_path)
    captured: dict[str, object] = {}

    def fake_runner(call) -> str:
        captured["session_id"] = call.context.get("subagent_session_id")
        return "ok"

    monkeypatch.setattr(ephemeral_agents, "_EPHEMERAL_RUNNER", fake_runner)
    result = bridge.call_subagent(
        "researcher",
        "hi",
        session=Session(thread_id="t-bridge"),
    )
    assert result["output"] == "ok"
    assert captured["session_id"] is not None
    # The stamped id belongs to a real durable session in the store.
    assert store.get(str(captured["session_id"])) is not None


def test_bridge_report_flows_to_parent_result(tmp_path, monkeypatch) -> None:
    """End-to-end: the child's report tool lands in the parent's pending lane."""
    from runtime.execution.subagents import bridge

    store = _store(tmp_path)

    def fake_runner(call) -> str:

        sid = call.context["subagent_session_id"]
        get_subagent_session_store().append_report(sid, content="parent, see this")
        return "child done"

    monkeypatch.setattr(
        "runtime.execution.suckers.ephemeral_agents._EPHEMERAL_RUNNER",
        fake_runner,
    )
    result = bridge.call_subagent(
        "researcher",
        "hi",
        session=Session(thread_id="t-bridge-2"),
    )
    assert result["output"] == "child done"
    pending = result.get("pending_reports")
    assert pending is not None
    assert pending[0]["content"] == "parent, see this"
    sid = str(result["session_id"])
    assert store.pending_reports(sid) == []

