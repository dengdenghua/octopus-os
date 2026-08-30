from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from runtime.memory.diagnostics.trace_store import AgentTraceStore
from runtime.memory.journal import JSONLJournal
from runtime.safety.approval.approval_gate import ApprovalRequest
from runtime.sensing.gateway.realtime_cerebrum import GatewayApprovalProvider


@pytest.fixture
def store(tmp_path: Path) -> AgentTraceStore:
    trace = AgentTraceStore(tmp_path / "trace.sqlite")
    yield trace
    trace.close()


def test_records_messages_events_approvals_checkpoints_and_token_usage(
    store: AgentTraceStore,
) -> None:
    message_id = store.record_message(
        thread_id="thread-1",
        role="user",
        content="build the report",
        turn_id="turn-1",
        agent_id="agent-a",
        metadata={"source": "chat"},
    )
    event_id = store.record_event(
        thread_id="thread-1",
        event_type="TOOL_CALL_START",
        payload={"tool": "web_search"},
        turn_id="turn-1",
        item_id="item-1",
        agent_id="agent-a",
    )
    approval_id = store.record_approval(
        thread_id="thread-1",
        tool_name="exec_shell",
        tool_call_id="call-1",
        decision="approved",
        reason="user accepted",
        args_preview="pytest tests/test_agent_trace_store.py",
        turn_id="turn-1",
        agent_id="agent-a",
    )
    checkpoint_id = store.record_checkpoint(
        task_id="task-1",
        checkpoint_type="react",
        state={"iteration": 3, "phase": "coding"},
        thread_id="thread-1",
        turn_id="turn-1",
        agent_id="agent-a",
        iteration=3,
        summary="implemented trace store",
    )
    token_id = store.record_token_usage(
        task_id="task-1",
        model="gpt-test",
        input_tokens=100,
        output_tokens=40,
        thinking_tokens=7,
        cached_tokens=20,
        cost_usd=0.0123,
        thread_id="thread-1",
        turn_id="turn-1",
        agent_id="agent-a",
        iteration=3,
        is_local=False,
    )

    assert message_id > 0
    assert event_id > 0
    assert approval_id > 0
    assert checkpoint_id > 0
    assert token_id > 0

    assert store.messages(thread_id="thread-1")[0]["content"] == "build the report"
    assert store.events(thread_id="thread-1", event_type="TOOL_CALL_START")[0]["payload"] == {
        "tool": "web_search",
    }
    assert store.approvals(thread_id="thread-1")[0]["decision"] == "approved"
    assert store.checkpoints(thread_id="thread-1")[0]["summary"] == "implemented trace store"
    assert store.latest_checkpoint(task_id="task-1")["state"]["iteration"] == 3
    assert store.token_usage(task_id="task-1")[0]["thinking_tokens"] == 7

    stats = store.stats()
    assert stats["messages"] == 1
    assert stats["events"] == 1
    assert stats["approvals"] == 1
    assert stats["checkpoints"] == 1
    assert stats["token_usage"] == 1
    assert stats["token_totals"]["input_tokens"] == 100
    assert stats["token_totals"]["output_tokens"] == 40
    assert stats["token_totals"]["thinking_tokens"] == 7
    assert stats["token_totals"]["cached_tokens"] == 20


def test_checkpoint_returns_newest_by_iteration_then_id(store: AgentTraceStore) -> None:
    store.record_checkpoint(
        task_id="task-1",
        checkpoint_type="react",
        state={"iteration": 1},
        iteration=1,
    )
    store.record_checkpoint(
        task_id="task-1",
        checkpoint_type="react",
        state={"iteration": 5},
        iteration=5,
    )
    store.record_checkpoint(
        task_id="task-1",
        checkpoint_type="task",
        state={"iteration": 99},
        iteration=99,
    )

    latest_react = store.latest_checkpoint(task_id="task-1", checkpoint_type="react")
    assert latest_react["state"] == {"iteration": 5}

    latest_any = store.latest_checkpoint(task_id="task-1")
    assert latest_any["checkpoint_type"] == "task"

    checkpoints = store.checkpoints(task_id="task-1", checkpoint_type="react")
    assert [checkpoint["iteration"] for checkpoint in checkpoints] == [1, 5]


def test_resume_proposal_is_sanitized(store: AgentTraceStore) -> None:
    checkpoint_id = store.record_checkpoint(
        task_id="task-1",
        checkpoint_type="react",
        thread_id="thread-1",
        agent_id="agent-a",
        iteration=3,
        summary="implemented trace store",
        state={
            "current_phase": "implementation",
            "progress_summary": "trace store wired",
            "messages_snapshot": [{"role": "user", "content": "secret message body"}],
            "steps_snapshot": [{"iteration": 1}, {"iteration": 2}],
            "working_set_snapshot": [
                {"path": "runtime/memory/trace_store.py"},
                {"path": "runtime/sensing/siphon/agent_trace_router.py"},
            ],
        },
    )

    proposal = store.resume_proposal(checkpoint_id)

    assert proposal is not None
    assert proposal["checkpoint"]["id"] == checkpoint_id
    assert proposal["checkpoint"]["type"] == "react"
    assert proposal["recovery_hints"]["phase"] == "implementation"
    assert proposal["recovery_hints"]["message_count"] == 1
    assert proposal["recovery_hints"]["step_count"] == 2
    assert proposal["resume_plan"]["steps"][1] == "Continue from iteration 4."
    assert proposal["safety"]["raw_state_included"] is False
    assert proposal["safety"]["raw_message_snapshots_included"] is False
    assert "secret message body" not in str(proposal)
    assert store.resume_proposal(99999) is None


def test_resume_proposals_returns_sanitized_candidates(store: AgentTraceStore) -> None:
    for iteration in range(1, 4):
        store.record_checkpoint(
            task_id="task-1",
            checkpoint_type="react",
            thread_id="thread-1",
            iteration=iteration,
            summary=f"checkpoint {iteration}",
            state={
                "current_phase": "implementation",
                "messages_snapshot": [{"content": f"secret {iteration}"}],
                "steps_snapshot": [{"iteration": iteration}],
            },
        )

    proposals = store.resume_proposals(thread_id="thread-1", limit=2, offset=1)

    assert [proposal["checkpoint"]["iteration"] for proposal in proposals] == [2, 3]
    assert proposals[0]["resume_plan"]["steps"][1] == "Continue from iteration 3."
    assert "secret" not in str(proposals)


def test_resume_requests_track_pending_confirmed_and_consumed_state(
    store: AgentTraceStore,
) -> None:
    request_id = store.record_resume_request(
        thread_id="thread-1",
        checkpoint_id=7,
        task_id="task-1",
        status="pending",
        intent={
            "schema": "echo.resume_intent.v1",
            "requires_confirmation": True,
            "checkpoint_id": 7,
            "progress": "private message body",
            "messages_snapshot": ["message body"],
        },
    )

    pending = store.latest_pending_resume_request(thread_id="thread-1")

    assert request_id > 0
    assert pending is not None
    assert pending["id"] == request_id
    assert pending["status"] == "pending"
    assert pending["intent"]["checkpoint_id"] == 7
    assert pending["intent"]["requires_confirmation"] is True
    assert "message body" not in str(pending)

    assert (
        store.confirm_resume_request(
            thread_id="thread-1",
            checkpoint_id=7,
            confirmation_text="确认恢复 checkpoint #7",
        )
        is not None
    )
    confirmed = store.resume_requests(thread_id="thread-1")[0]
    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmed_at"] is not None
    assert confirmed["intent"]["requires_confirmation"] is False
    assert confirmed["intent"]["confirmed"] is True
    assert "message body" not in str(confirmed)

    assert store.consume_resume_request(request_id) is not None
    consumed = store.resume_requests(thread_id="thread-1")[0]
    assert consumed["status"] == "consumed"
    assert consumed["consumed_at"] is not None
    assert store.latest_pending_resume_request(thread_id="thread-1") is None


def test_filters_by_thread_task_and_agent(store: AgentTraceStore) -> None:
    store.record_event(
        thread_id="thread-a",
        task_id="task-a",
        agent_id="agent-a",
        event_type="RUN_STARTED",
        payload={},
    )
    store.record_event(
        thread_id="thread-b",
        task_id="task-b",
        agent_id="agent-b",
        event_type="RUN_STARTED",
        payload={},
    )

    assert len(store.events(thread_id="thread-a")) == 1
    assert len(store.events(task_id="task-b")) == 1
    assert len(store.events(agent_id="agent-a")) == 1
    assert store.events(thread_id="missing") == []


def test_task_run_read_model_aggregates_events_tools_tokens_and_approvals(
    store: AgentTraceStore,
) -> None:
    store.record_task_run_started(
        task_id="turn-1",
        thread_id="thread-1",
        turn_id="turn-1",
        agent_id="agent-a",
        title="Build report",
        goal="Build the weekly report",
        mode="code",
        ts="2026-06-07T00:00:00+00:00",
    )
    store.record_event(
        thread_id="thread-1",
        turn_id="turn-1",
        task_id="turn-1",
        agent_id="agent-a",
        event_type="TOOL_CALL_START",
        payload={"tool": "read_file"},
        ts="2026-06-07T00:00:01+00:00",
    )
    store.record_event(
        thread_id="thread-1",
        turn_id="turn-1",
        task_id="turn-1",
        agent_id="agent-a",
        event_type="TOOL_CALL_END",
        payload={"tool": "read_file", "status": "success"},
        ts="2026-06-07T00:00:02+00:00",
    )
    store.record_event(
        thread_id="thread-1",
        turn_id="turn-1",
        task_id="turn-1",
        agent_id="agent-a",
        event_type="TOOL_CALL_END",
        payload={"tool": "exec_shell", "status": "error"},
        ts="2026-06-07T00:00:03+00:00",
    )
    store.record_approval(
        thread_id="thread-1",
        turn_id="turn-1",
        task_id="turn-1",
        agent_id="agent-a",
        tool_name="exec_shell",
        tool_call_id="call-1",
        decision="rejected",
    )
    store.record_checkpoint(
        task_id="turn-1",
        thread_id="thread-1",
        turn_id="turn-1",
        agent_id="agent-a",
        checkpoint_type="react",
        state={"phase": "verify"},
        iteration=2,
    )
    store.record_token_usage(
        task_id="turn-1",
        thread_id="thread-1",
        turn_id="turn-1",
        agent_id="agent-a",
        model="gpt-test",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.25,
    )
    store.record_task_run_finished(
        task_id="turn-1",
        thread_id="thread-1",
        turn_id="turn-1",
        agent_id="agent-a",
        status="failed",
        reason="verification_failed",
        summary="tests failed",
        ts="2026-06-07T00:00:04+00:00",
    )

    run = store.task_run("turn-1")

    assert run is not None
    assert run["status"] == "failed"
    assert run["title"] == "Build report"
    assert run["goal"] == "Build the weekly report"
    assert run["mode"] == "code"
    assert run["summary"] == "tests failed"
    assert run["reason"] == "verification_failed"
    assert run["tool_calls_started"] == 1
    assert run["tool_calls_finished"] == 2
    assert run["tool_errors"] == 1
    assert run["tool_names"] == ["exec_shell", "read_file"]
    assert run["approval_count"] == 1
    assert run["approval_rejections"] == 1
    assert run["checkpoint_count"] == 1
    assert run["token_totals"]["input_tokens"] == 100
    assert run["token_totals"]["output_tokens"] == 50
    assert run["token_totals"]["cost_usd"] == 0.25
    assert len(run["events"]) == 5


def test_connection_lost_approval_counts_as_rejection(store: AgentTraceStore) -> None:
    # The approval lifecycle change added 'connection_lost' as a
    # distinct decision label; the task-run rollup must count it among
    # rejections alongside rejected/timeout/error, not silently drop it.
    store.record_task_run_started(
        task_id="turn-cl",
        thread_id="thread-1",
        ts="2026-06-07T00:00:00+00:00",
    )
    for decision in ("rejected", "timeout", "connection_lost", "error", "approved"):
        store.record_approval(
            thread_id="thread-1",
            turn_id="turn-cl",
            task_id="turn-cl",
            agent_id="agent-a",
            tool_name="exec_shell",
            tool_call_id=f"call-{decision}",
            decision=decision,
        )
    store.record_task_run_finished(
        task_id="turn-cl",
        thread_id="thread-1",
        turn_id="turn-cl",
        agent_id="agent-a",
        status="failed",
        ts="2026-06-07T00:00:04+00:00",
    )

    run = store.task_run("turn-cl")
    assert run is not None
    assert run["approval_count"] == 5
    # rejected + timeout + connection_lost + error = 4 (approved excluded)
    assert run["approval_rejections"] == 4


def test_task_runs_lists_latest_runs_and_filters_status(store: AgentTraceStore) -> None:
    store.record_task_run_started(
        task_id="task-old",
        thread_id="thread-1",
        ts="2026-06-07T00:00:00+00:00",
    )
    store.record_task_run_started(
        task_id="task-new",
        thread_id="thread-1",
        ts="2026-06-07T00:01:00+00:00",
    )
    store.record_task_run_finished(
        task_id="task-new",
        thread_id="thread-1",
        status="completed",
        ts="2026-06-07T00:02:00+00:00",
    )

    runs = store.task_runs(thread_id="thread-1")
    completed = store.task_runs(thread_id="thread-1", status="completed")

    assert [run["task_id"] for run in runs] == ["task-new", "task-old"]
    assert [run["task_id"] for run in completed] == ["task-new"]


def test_task_run_review_extracts_findings_replay_and_learning_candidates(
    store: AgentTraceStore,
) -> None:
    store.record_task_run_started(
        task_id="turn-review",
        thread_id="thread-1",
        turn_id="turn-review",
        title="Fix failing test",
        goal="Fix the failing pytest case",
        mode="code",
        ts="2026-06-07T00:00:00+00:00",
    )
    store.record_event(
        thread_id="thread-1",
        turn_id="turn-review",
        task_id="turn-review",
        event_type="TOOL_CALL_START",
        item_id="call-1",
        payload={
            "tool_call_id": "call-1",
            "tool_name": "exec_shell",
            "input_preview": "pytest tests/test_x.py",
        },
        ts="2026-06-07T00:00:01+00:00",
    )
    store.record_approval(
        thread_id="thread-1",
        turn_id="turn-review",
        task_id="turn-review",
        tool_name="exec_shell",
        tool_call_id="call-1",
        decision="approved",
        reason="accept",
        metadata={
            "trust_gateway": {
                "schema": "echo.trust_decision.v1",
                "source": "risk_policy",
                "risk": {"level": "high", "categories": ["shell_execution"]},
                "action": "ask",
            }
        },
    )
    store.record_event(
        thread_id="thread-1",
        turn_id="turn-review",
        task_id="turn-review",
        event_type="TOOL_CALL_END",
        item_id="call-1",
        payload={
            "tool_call_id": "call-1",
            "tool_name": "exec_shell",
            "status": "error",
            "output_preview": "AssertionError: expected 1 got 2",
        },
        ts="2026-06-07T00:00:02+00:00",
    )
    store.record_task_run_finished(
        task_id="turn-review",
        thread_id="thread-1",
        turn_id="turn-review",
        status="failed",
        reason="tests_failed",
        ts="2026-06-07T00:00:03+00:00",
    )

    review = store.task_run_review("turn-review")

    assert review is not None
    assert review["schema"] == "echo.task_run_review.v1"
    assert review["status"] == "failed"
    assert review["score"] < 0.5
    finding_types = [finding["type"] for finding in review["findings"]]
    assert "terminal_status" in finding_types
    assert "tool_error" in finding_types
    assert "high_risk_approval" in finding_types
    assert review["replay"]["replayable"] is True
    assert review["replay"]["steps"][1]["approval"]["risk_level"] == "high"
    assert any(item["kind"] == "failure_pattern" for item in review["learning_candidates"])
    assert review["backlog_candidates"][0]["priority"] == "P0"


def test_stats_can_be_scoped_to_thread_task_and_agent(store: AgentTraceStore) -> None:
    store.record_message(thread_id="thread-a", role="user", content="a")
    store.record_message(thread_id="thread-b", role="user", content="b")
    store.record_event(
        thread_id="thread-a",
        task_id="task-a",
        agent_id="agent-a",
        event_type="RUN_STARTED",
        payload={},
    )
    store.record_event(
        thread_id="thread-b",
        task_id="task-b",
        agent_id="agent-b",
        event_type="RUN_STARTED",
        payload={},
    )
    store.record_approval(
        thread_id="thread-a",
        task_id="task-a",
        agent_id="agent-a",
        tool_name="exec_shell",
        tool_call_id="call-a",
        decision="approved",
    )
    store.record_checkpoint(
        task_id="task-a",
        checkpoint_type="react",
        thread_id="thread-a",
        agent_id="agent-a",
        state={},
    )
    store.record_token_usage(
        task_id="task-a",
        thread_id="thread-a",
        agent_id="agent-a",
        input_tokens=11,
        output_tokens=3,
    )
    store.record_token_usage(
        task_id="task-b",
        thread_id="thread-b",
        agent_id="agent-b",
        input_tokens=99,
        output_tokens=1,
    )

    stats = store.stats(thread_id="thread-a")

    assert stats["messages"] == 1
    assert stats["events"] == 1
    assert stats["approvals"] == 1
    assert stats["checkpoints"] == 1
    assert stats["token_usage"] == 1
    assert stats["token_totals"]["input_tokens"] == 11
    assert stats["token_totals"]["output_tokens"] == 3
    assert store.stats(task_id="task-b")["token_totals"]["input_tokens"] == 99
    assert store.stats(agent_id="missing")["events"] == 0


def test_wal_mode_is_enabled(tmp_path: Path) -> None:
    trace = AgentTraceStore(tmp_path / "trace.sqlite")
    try:
        row = trace._conn.execute("PRAGMA journal_mode;").fetchone()  # noqa: SLF001
        assert str(row[0]).lower() == "wal"
    finally:
        trace.close()


def test_jsonl_journal_mirrors_token_usage_and_checkpoint_to_trace_store(
    tmp_path: Path,
) -> None:
    trace = AgentTraceStore(tmp_path / "trace.sqlite")
    task_id = uuid4()
    try:
        journal = JSONLJournal(tmp_path / "events.jsonl", trace_store=trace)
        journal.write_token_usage(
            task_id=str(task_id),
            iteration=2,
            input_tokens=120,
            output_tokens=45,
            cost_usd=0.02,
            model="gpt-test",
        )
        journal.write_react_checkpoint(
            task_id=task_id,
            iteration_completed=2,
            max_iterations=8,
            messages_snapshot=[{"role": "user", "content": "continue"}],
            steps_snapshot=[{"iteration": 2, "action": "read_file"}],
            has_final_answer=False,
            working_set_snapshot=[{"path": "runtime/memory/trace_store.py"}],
            progress_summary="trace store added",
            current_phase="implementation",
        )

        tokens = trace.token_usage(task_id=str(task_id))
        assert len(tokens) == 1
        assert tokens[0]["input_tokens"] == 120
        assert tokens[0]["output_tokens"] == 45
        assert tokens[0]["model"] == "gpt-test"

        checkpoint = trace.latest_checkpoint(task_id=str(task_id), checkpoint_type="react")
        assert checkpoint is not None
        assert checkpoint["iteration"] == 2
        assert checkpoint["summary"] == "trace store added"
        assert checkpoint["state"]["current_phase"] == "implementation"
        assert checkpoint["state"]["messages_snapshot"][0]["content"] == "continue"

        event_types = [event["event_type"] for event in trace.events(task_id=str(task_id))]
        assert event_types == ["token_usage", "react_checkpoint"]
        assert (tmp_path / "events.jsonl").read_text(encoding="utf-8").count("\n") == 2
    finally:
        trace.close()


def test_jsonl_journal_trace_store_failure_does_not_block_jsonl(
    tmp_path: Path,
) -> None:
    class BrokenTraceStore:
        def record_event(self, **kwargs: object) -> int:
            raise RuntimeError("trace unavailable")

    journal = JSONLJournal(tmp_path / "events.jsonl", trace_store=BrokenTraceStore())
    journal.write_token_usage(
        task_id=str(uuid4()),
        iteration=1,
        input_tokens=1,
        output_tokens=2,
    )

    assert (tmp_path / "events.jsonl").read_text(encoding="utf-8").count("\n") == 1


def test_gateway_approval_provider_records_decision_to_trace_store(tmp_path: Path) -> None:
    import asyncio

    class FakeEmitter:
        async def request_approval(
            self,
            method: object,
            params: dict[str, object],
            *,
            timeout: float,
        ) -> dict[str, str]:
            assert params["tool"] == "exec_shell"
            return {"action": "accept"}

    async def run() -> None:
        trace = AgentTraceStore(tmp_path / "trace.sqlite")
        try:
            provider = GatewayApprovalProvider(
                FakeEmitter(),
                asyncio.get_running_loop(),
                thread_id="thread-1",
                turn_id="turn-1",
                trace_store=trace,
            )
            decision = await asyncio.to_thread(
                provider.request,
                ApprovalRequest(
                    thread_id="thread-1",
                    tool_name="exec_shell",
                    tool_call_id="call-1",
                    args_preview="rm -rf nope",
                    detail="dangerous command",
                ),
            )

            assert decision.approved is True
            approvals = trace.approvals(thread_id="thread-1")
            assert len(approvals) == 1
            assert approvals[0]["tool_name"] == "exec_shell"
            assert approvals[0]["tool_call_id"] == "call-1"
            assert approvals[0]["decision"] == "approved"
            assert approvals[0]["reason"] == "accept"
            assert approvals[0]["turn_id"] == "turn-1"
            assert approvals[0]["metadata"]["detail"] == "dangerous command"
            trust = approvals[0]["metadata"]["trust_gateway"]
            assert trust["schema"] == "echo.trust_decision.v1"
            assert trust["tool_name"] == "exec_shell"
            assert trust["risk"]["level"] == "critical"
        finally:
            trace.close()

    asyncio.run(run())


def test_gateway_approval_provider_converts_gateway_timeout_to_rejection(tmp_path: Path) -> None:
    import asyncio

    from runtime.protocol import JsonRpcError, JsonRpcErrorCode
    from runtime.sensing.gateway.realtime_gateway import _ApprovalError

    class TimeoutEmitter:
        async def request_approval(
            self,
            method: object,
            params: dict[str, object],
            *,
            timeout: float,
        ) -> dict[str, str]:
            raise _ApprovalError(
                JsonRpcError(
                    code=JsonRpcErrorCode.APPROVAL_TIMEOUT,
                    message="timed out waiting for item/commandExecution/requestApproval",
                )
            )

    async def run() -> None:
        trace = AgentTraceStore(tmp_path / "trace.sqlite")
        try:
            provider = GatewayApprovalProvider(
                TimeoutEmitter(),
                asyncio.get_running_loop(),
                thread_id="thread-1",
                turn_id="turn-1",
                trace_store=trace,
            )
            decision = await asyncio.to_thread(
                provider.request,
                ApprovalRequest(
                    thread_id="thread-1",
                    tool_name="write_text_file",
                    tool_call_id="call-1",
                    args_preview="plan.md",
                    detail="write_text_file wants to execute",
                ),
            )

            assert decision.approved is False
            # Machine-readable reason: the UI and journal must be able
            # to tell "nobody answered" apart from "user said no".
            assert decision.reason == "timeout"
            approvals = trace.approvals(thread_id="thread-1")
            assert approvals[0]["decision"] == "timeout"
            assert approvals[0]["reason"] == "timeout"
        finally:
            trace.close()

    asyncio.run(run())


def test_gateway_approval_provider_converts_connection_loss_to_rejection(tmp_path: Path) -> None:
    import asyncio

    class CancelledEmitter:
        async def request_approval(
            self,
            method: object,
            params: dict[str, object],
            *,
            timeout: float,
        ) -> dict[str, str]:
            # ApprovalManager.cancel_all() on connection close cancels
            # the pending future; awaiting it raises CancelledError.
            raise asyncio.CancelledError()

    async def run() -> None:
        trace = AgentTraceStore(tmp_path / "trace.sqlite")
        try:
            provider = GatewayApprovalProvider(
                CancelledEmitter(),
                asyncio.get_running_loop(),
                thread_id="thread-1",
                turn_id="turn-1",
                trace_store=trace,
            )
            decision = await asyncio.to_thread(
                provider.request,
                ApprovalRequest(
                    thread_id="thread-1",
                    tool_name="exec_shell",
                    tool_call_id="call-1",
                    args_preview="ls",
                    detail="exec_shell wants to execute",
                ),
            )

            assert decision.approved is False
            assert decision.reason == "connection_lost"
            approvals = trace.approvals(thread_id="thread-1")
            assert approvals[0]["decision"] == "connection_lost"
        finally:
            trace.close()

    asyncio.run(run())


def test_gateway_approval_provider_sends_timeout_to_client(tmp_path: Path) -> None:
    import asyncio

    captured: dict[str, object] = {}

    class CapturingEmitter:
        async def request_approval(
            self,
            method: object,
            params: dict[str, object],
            *,
            timeout: float,
        ) -> dict[str, str]:
            captured.update(params)
            return {"action": "accept"}

    async def run() -> None:
        provider = GatewayApprovalProvider(
            CapturingEmitter(),
            asyncio.get_running_loop(),
            thread_id="thread-1",
            turn_id="turn-1",
        )
        await asyncio.to_thread(
            provider.request,
            ApprovalRequest(
                thread_id="thread-1",
                tool_name="exec_shell",
                tool_call_id="call-1",
                args_preview="ls",
                detail="",
            ),
        )
        # The client mirrors the server timeout to expire its dialog in
        # lockstep instead of leaving a zombie prompt.
        assert captured["timeoutMs"] == 120_000

    asyncio.run(run())


def test_app_state_wires_trace_store_into_default_jsonl_journal(tmp_path: Path) -> None:
    from runtime.platform.ui.state import AppState

    state = AppState(
        journal_path=tmp_path / "events.jsonl",
        trace_store_path=tmp_path / "agent_trace.sqlite",
    )

    task_id = uuid4()
    state.journal.write_token_usage(
        task_id=str(task_id),
        iteration=1,
        input_tokens=9,
        output_tokens=4,
        model="gpt-test",
    )

    trace = AgentTraceStore(tmp_path / "agent_trace.sqlite")
    try:
        tokens = trace.token_usage(task_id=str(task_id))
        assert len(tokens) == 1
        assert tokens[0]["input_tokens"] == 9
    finally:
        trace.close()


def test_app_state_attaches_trace_store_to_injected_jsonl_journal(tmp_path: Path) -> None:
    from runtime.platform.ui.state import AppState

    injected = JSONLJournal(tmp_path / "injected.jsonl")
    state = AppState(
        journal=injected,
        trace_store_path=tmp_path / "agent_trace.sqlite",
    )

    task_id = uuid4()
    state.journal.write_token_usage(
        task_id=str(task_id),
        iteration=1,
        input_tokens=12,
        output_tokens=3,
        model="serve-model",
    )

    trace = AgentTraceStore(tmp_path / "agent_trace.sqlite")
    try:
        tokens = trace.token_usage(task_id=str(task_id))
        assert len(tokens) == 1
        assert tokens[0]["model"] == "serve-model"
    finally:
        trace.close()


def test_create_app_uses_default_agent_trace_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fastapi = pytest.importorskip("fastapi")
    assert fastapi is not None
    from runtime.platform.ui import create_app

    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))

    app = create_app(journal_path=tmp_path / "data" / "events.jsonl")
    state = app.state.echo_state

    assert state.trace_store_path == (tmp_path / "data" / "agent_trace.sqlite").resolve()
