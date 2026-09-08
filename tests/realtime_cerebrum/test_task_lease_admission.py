"""Real realtime loops must own a persisted task lease before tool effects.

Only the model is scripted. Both loop implementations, WebSocket gateway,
ToolExecutor, task store, lease conflicts and synthetic file writes are real.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.core.cerebrum.pause_control import PauseController
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import JSONLJournal
from runtime.platform.models import ArmId, Budget, BudgetLimits, SkillId, TaskId
from runtime.platform.models.llm import ToolCall
from runtime.platform.process.service_provider import get_provider
from runtime.platform.process.session import current_session, session_scope
from runtime.platform.process.task_supervisor import TaskSupervisor
from runtime.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    Notification,
    decode_message,
    encode_message,
)
from runtime.safety.auth import TrustEngine
from runtime.safety.evolution.proposal_ledger import ProposalLedger
from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
from runtime.sensing.gateway.realtime_gateway import RealtimeGateway
from runtime.sensing.gateway.task_runs_router import create_task_runs_router
from runtime.sensing.model_router.models import CostEntry, ModelResponse, ModelStreamEvent
from tests.realtime_cerebrum import _helpers


@pytest.fixture(autouse=True)
def _patch_react_loop() -> None:
    """Override conftest's fake-loop fixture: do not replace either real loop."""


class _Model:
    def __init__(self, branch: str) -> None:
        self.branch = branch
        self.capabilities = SimpleNamespace(supports_tool_use=branch == "native")
        self.requests: list[Any] = []
        self.effects: list[str] = []
        self.sequence = [("lease_marker", "first"), ("lease_marker", "second")]

    def call(self, request: Any) -> ModelResponse:
        index = len(self.effects)
        self.requests.append(request)
        calls = []
        if index < len(self.sequence) and len(self.requests) < 12:
            tool_name, value = self.sequence[index]
            if self.branch == "native":
                text = f"Recording the {value} synthetic marker."
                args = {"value": value}
                for spec in request.tools or []:
                    if spec.name != tool_name:
                        continue
                    properties = spec.input_schema.get("properties", {})
                    for key, update in {
                        "public_update": "I will record two synthetic markers in sequence.",
                        "confirmed_fact": "The first synthetic marker was recorded.",
                        "next_action": "I will record the second synthetic marker.",
                    }.items():
                        if key in properties:
                            args[key] = update
                calls = [ToolCall(id=f"marker-{index}", name=tool_name, input=args)]
            else:
                text = (
                    f"Thought: record marker\nUpdate: I will record the {value} synthetic marker.\n"
                    f'Action: {tool_name}({{"value": "{value}"}})'
                )
        else:
            # A finite successful answer, also after an ordinary tool error.
            # Lease failure must not be downgraded into such a successful turn.
            text = "The synthetic marker results have been recorded."
            if self.branch == "react":
                text = "Final Answer: " + text
        return ModelResponse(
            text=text, model="test-model", tool_calls=calls, finish_reason="stop", cost=CostEntry()
        )

    def call_stream(self, request: Any) -> Iterator[ModelStreamEvent]:
        response = self.call(request)
        if response.text:
            yield ModelStreamEvent(type="text_delta", delta=response.text)
        for call in response.tool_calls:
            yield ModelStreamEvent(type="tool_use", tool_call=call)
        yield ModelStreamEvent(type="done", final=response)


@pytest.fixture(params=["react", "native"])
def lease_gateway(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SimpleNamespace]:
    branch = str(request.param)
    pause = PauseController(store_path=tmp_path / "pauses.json", autoload=False)
    monkeypatch.setitem(get_provider()._instances, "pause_controller", pause)
    monkeypatch.setenv("ECHO_NATIVE_TOOLUSE", "1")
    model = _Model(branch)
    store_path = tmp_path / "task_runs.json"
    supervisor = TaskSupervisor.from_path(store_path, holder_id="realtime", lease_ttl_seconds=600)
    other = TaskSupervisor.from_path(store_path, holder_id="other-worker", lease_ttl_seconds=600)
    case = SimpleNamespace(
        branch=branch,
        supervisor=supervisor,
        other=other,
        model=model,
        marker=tmp_path / "effects.txt",
        starts=[],
        admitted=[],
        calls=[],
        holders_at_effect=[],
        before_start=None,
        before_execute=None,
        after_effect=None,
        output_hook=None,
        sessions=[],
        pause=pause,
    )
    real_start = supervisor.start_task

    def start(**kwargs: Any) -> Any:
        case.starts.append(kwargs["task_id"])
        if case.before_start is not None:
            case.before_start(kwargs)
        record = real_start(**kwargs)
        case.admitted.append(record)
        return record

    monkeypatch.setattr(supervisor, "start_task", start)

    def marker(value: str) -> dict[str, Any]:
        assert value in {"first", "second", "verified", "late"}
        case.sessions.append(current_session())
        # Observe lease state without blocking a broken implementation ourselves.
        case.holders_at_effect.append(
            bool(case.admitted and supervisor.is_current_holder(case.admitted[-1].task_id))
        )
        with case.marker.open("a", encoding="utf-8") as output:
            output.write(value + "\n")
        model.effects.append(value)
        if case.after_effect is not None:
            case.after_effect(value)
        if case.output_hook is not None:
            return case.output_hook(value)
        return {"ok": True, "recorded": value}

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="lease_marker",
            description="Record a synthetic marker for the requested lease audit.",
            affinity=["write"],
            trusted_source="skill://public/lease-marker",
            handler=marker,
        ),
        verify_tests=False,
    )
    journal = JSONLJournal(tmp_path / "journal.jsonl")
    executor = ToolExecutor(
        registry, TrustEngine(trusted_sources=["skill://public/*"]), journal=journal
    )
    real_execute = executor.execute_step

    def execute(*args: Any, **kwargs: Any) -> Any:
        case.calls.append(kwargs)
        if case.before_execute is not None:
            case.before_execute()
        return real_execute(*args, **kwargs)

    monkeypatch.setattr(executor, "execute_step", execute)
    stack = SimpleNamespace(
        registry=registry,
        journal=journal,
        executor=executor,
        planner=SimpleNamespace(router=model, planner_model="test-model"),
    )
    runtime = CerebrumRuntime(
        stack=stack,
        agent=SimpleNamespace(
            agent_id="lease-audit", capabilities={}, soul="", extra_skills=["lease_marker"]
        ),
        logs_root=str(tmp_path / "threads"),
        workspace_root=tmp_path / "workspaces",
        task_supervisor=supervisor,
        max_iterations=5,
    )
    case.runtime = runtime
    case.journal = journal
    case.executor = executor
    case.marker_handler = marker
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=2)
    case.gateway = gateway
    app = FastAPI()
    app.include_router(gateway.router)
    app.include_router(create_task_runs_router(supervisor=supervisor))
    with TestClient(app) as client:
        case.client = client
        yield case


def _run(case: SimpleNamespace, **params: Any) -> dict[str, Any]:
    with case.client.websocket_connect("/api/realtime") as ws:
        return _helpers.drive(
            ws,
            params={
                "threadId": f"lease-admission-{case.branch}",
                "mode": "react",
                "input": [
                    {
                        "type": "text",
                        "text": "Use lease_marker to record first, then second, and report the results.",
                    }
                ],
                "approvalPolicy": "on-request",
                **params,
            },
        )


def _status(result: dict[str, Any]) -> str:
    response = result["response"]
    assert response.error is None, response
    return str(response.result["turn"]["status"])


def _assert_not_completed(result: dict[str, Any]) -> None:
    assert _status(result) != "completed", result["response"]
    # turn/completed is the protocol's terminal notification even for failed
    # turns. Its payload, not its method name, must report non-success.
    for notification in result["notifications"]:
        if notification.method == "turn/completed":
            assert notification.params["turn"]["status"] != "completed"


def _expire(case: SimpleNamespace, task_id: str) -> None:
    def expire(record: Any) -> Any:
        assert record.lease is not None
        return record.model_copy(
            update={"lease": record.lease.model_copy(update={"expires_at": time.time() - 1})},
            deep=True,
        )

    case.supervisor.store.mutate(task_id, expire)


def test_valid_lease_admits_both_real_tool_calls(lease_gateway: SimpleNamespace) -> None:
    case = lease_gateway
    result = _run(case, model="test-model")
    assert case.marker.read_text(encoding="utf-8").splitlines() == ["first", "second"]
    assert case.holders_at_effect == [True, True]
    assert _status(result) == "completed"
    assert case.admitted
    saved = case.supervisor.store.get(case.admitted[0].task_id)
    assert saved is not None and saved.status.value == "completed"
    assert saved.metadata["execution_engine"] == "echo"
    assert saved.metadata["model_name"] == "test-model"
    expected_caller = "react_loop" if case.branch == "react" else "agentic"
    assert {call["caller"] for call in case.calls} == {expected_caller}
    assert any(bool(req.tools) for req in case.model.requests) == (case.branch == "native")


def test_conflicting_live_holder_blocks_tools(lease_gateway: SimpleNamespace) -> None:
    case = lease_gateway
    original: list[Any] = []

    def occupy(kwargs: dict[str, Any]) -> None:
        if not original:
            original.append(case.other.start_task(**kwargs))

    case.before_start = occupy
    result = _run(case)
    assert original, "The actual turn must attempt task admission"
    assert not case.admitted
    assert not case.marker.exists(), "A rejected lease must never reach a side-effect handler"
    _assert_not_completed(result)
    saved = case.other.store.get(original[0].task_id)
    assert saved is not None and saved.lease == original[0].lease
    assert saved.status.value == "running"


def test_admission_store_failure_blocks_tools(
    lease_gateway: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = lease_gateway
    writes: list[Any] = []

    def fail_write(payload: Any) -> None:
        writes.append(payload)
        raise OSError("synthetic task store is unavailable")

    monkeypatch.setattr(case.supervisor.store, "_write_payload", fail_write)
    result = _run(case)
    assert writes, "The failure must originate from a real admission persistence attempt"
    assert not case.admitted
    assert not case.marker.exists()
    assert case.supervisor.store.list() == []
    _assert_not_completed(result)


@pytest.mark.parametrize("loss", ["expired", "takeover", "same_holder_restart"])
def test_loss_after_first_effect_blocks_next_tool(
    lease_gateway: SimpleNamespace, loss: str
) -> None:
    case = lease_gateway
    replacements: list[Any] = []

    def lose_after_first(value: str) -> None:
        if value != "first":
            return
        assert case.admitted, "The first effect must have an admitted task"
        task_id = case.admitted[-1].task_id

        if loss == "same_holder_restart":
            # A new attempt on the same supervisor replaces its remembered
            # token; the old turn must retain, and check, its original token.
            replacements.append(case.supervisor.start_task(task_id=task_id))
            return

        # Real persisted expiry, followed by a real foreign-holder takeover.
        # No fake is_current_holder result and no heartbeat-timing dependency.
        _expire(case, task_id)
        if loss == "takeover":
            replacements.append(case.other.takeover_task(task_id, reason="synthetic takeover"))

    case.after_effect = lose_after_first
    result = _run(case)
    assert case.marker.read_text(encoding="utf-8").splitlines() == ["first"]
    assert case.holders_at_effect == [True]
    _assert_not_completed(result)
    saved = case.supervisor.store.get(case.admitted[0].task_id)
    assert saved is not None and saved.status.value != "completed"
    if loss in {"takeover", "same_holder_restart"}:
        assert len(replacements) == 1
        assert saved.lease == replacements[0].lease
        assert saved.lease.token != case.admitted[0].lease.token


def test_takeover_after_tool_start_still_blocks_handler(lease_gateway: SimpleNamespace) -> None:
    """A stream event check cannot replace the executor's own dispatch fence."""
    case = lease_gateway
    replacements: list[Any] = []

    def before_second_execute() -> None:
        if len(case.calls) != 2:
            return
        assert case.marker.read_text(encoding="utf-8").splitlines() == ["first"]
        task_id = case.admitted[0].task_id
        # The real loop has already emitted/acknowledged its tool_start and
        # now invokes the real executor. Change only the persisted authority.
        _expire(case, task_id)
        replacements.append(case.other.takeover_task(task_id, reason="just before executor"))

    case.before_execute = before_second_execute
    result = _run(case)
    assert len(replacements) == 1, "Reach the second real executor dispatch"
    assert case.marker.read_text(encoding="utf-8").splitlines() == ["first"]
    assert case.holders_at_effect == [True]
    _assert_not_completed(result)
    saved = case.other.store.get(replacements[0].task_id)
    assert saved is not None and saved.lease == replacements[0].lease
    assert saved.status.value == "running"


@pytest.mark.parametrize("failure", ["same_holder_restart", "result_write_failure"])
def test_terminal_settlement_cannot_leave_success_after_losing_authority(
    lease_gateway: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    case = lease_gateway
    real_finish = case.runtime._record_task_run_finished
    replacements: list[Any] = []
    failed_writes: list[Any] = []
    real_write = case.supervisor.store._write_payload

    def fail_result_only(payload: Any) -> None:
        if any(task.get("status") == "completed" for task in payload.get("tasks", [])):
            failed_writes.append(payload)
            raise OSError("synthetic terminal task write failed")
        real_write(payload)

    def finish(turn: Any, **kwargs: Any) -> Any:
        # This hook changes authority/storage only after both real handlers
        # and the actual model/driver return, immediately before settlement.
        assert case.marker.read_text(encoding="utf-8").splitlines() == ["first", "second"]
        assert turn.status.value == "completed"
        if failure == "same_holder_restart":
            replacements.append(case.supervisor.start_task(task_id=case.admitted[0].task_id))
        else:
            monkeypatch.setattr(case.supervisor.store, "_write_payload", fail_result_only)
        return real_finish(turn, **kwargs)

    monkeypatch.setattr(case.runtime, "_record_task_run_finished", finish)
    result = _run(case)
    _assert_not_completed(result)
    assert case.marker.read_text(encoding="utf-8").splitlines() == ["first", "second"]
    saved = case.supervisor.store.get(case.admitted[0].task_id)
    assert saved is not None and saved.status.value == "running"
    if failure == "same_holder_restart":
        assert saved.lease == replacements[0].lease
        assert saved.lease.token != case.admitted[0].lease.token
    else:
        assert len(failed_writes) == 1
    replayed = case.runtime._log_for(f"lease-admission-{case.branch}").replay()
    assert replayed[-1].status.value == "failed"
    assert replayed[-1].error["code"] == "task_execution_finalization_failed"
    terminal_events = [
        event
        for event in case.runtime._log_for(f"lease-admission-{case.branch}").iter_events()
        if event.event == "turn_completed"
    ]
    assert terminal_events and all(event.payload["status"] == "failed" for event in terminal_events)
    assert ProposalLedger(case.runtime._proposal_ledger_path).query(kind="turn_success") == []


@pytest.mark.parametrize("lease_gateway", ["react"], indirect=True)
def test_real_verification_driver_reuses_epoch_but_not_closed_scope(
    lease_gateway: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    case = lease_gateway
    code_path = tmp_path / "workspaces" / f"lease-admission-{case.branch}" / "synthetic.py"
    driver_calls: list[Any] = []
    guards: list[Any] = []
    late_attempts: list[Any] = []
    real_drive = case.runtime._drive_react

    def output(value: str) -> dict[str, Any]:
        if value == "second":
            code_path.write_text("SYNTHETIC_VALUE =\n", encoding="utf-8")
            return {
                "ok": True,
                "diff": ("--- /dev/null\n+++ b/synthetic.py\n@@ -0,0 +1 @@\n+SYNTHETIC_VALUE =\n"),
            }
        if value == "verified":
            assert code_path.read_text(encoding="utf-8") == "SYNTHETIC_VALUE =\n"
            code_path.write_text("SYNTHETIC_VALUE = 1\n", encoding="utf-8")
            content = code_path.read_text(encoding="utf-8")
            assert content == "SYNTHETIC_VALUE = 1\n"
            compile(content, str(code_path), "exec")
            return {"success": True, "stdout": "Synthetic content verified", "exit_code": 0}
        return {"ok": True, "recorded": value}

    case.output_hook = output
    case.executor.registry.register(
        Skill(
            name="run_tests",
            description="Verify the synthetic Python file's exact content and syntax.",
            affinity=["write"],
            trusted_source="skill://public/synthetic-verifier",
            handler=case.marker_handler,
        ),
        verify_tests=False,
    )
    case.runtime._default_agent.extra_skills.append("run_tests")

    async def observed_drive(turn: Any, log: Any, emitter: Any, intent: Any, *args: Any, **kw: Any):
        driver_calls.append(intent)
        if len(driver_calls) == 2:
            assert intent.user_context.get("verification_repair")
            old_session = case.sessions[0]
            task_id = TaskId(UUID(guards[0].bound_task_id))
            with session_scope(old_session):
                late_attempts.append(
                    case.executor.execute_step(
                        step_id=999,
                        node_id="old-scope-late-attempt",
                        sucker_id=SkillId("lease_marker"),
                        args={"value": "late"},
                        caller="react_loop",
                        task_id=task_id,
                        arm_id=ArmId("lease-audit"),
                        budget=Budget(task_id=task_id, limits=BudgetLimits(tokens=10_000, usd=1.0)),
                    )
                )
            case.model.sequence.append(("run_tests", "verified"))
        await real_drive(turn, log, emitter, intent, *args, **kw)
        guards.append(case.runtime._task_execution_guards[turn.id])

    monkeypatch.setattr(case.runtime, "_drive_react", observed_drive)
    result = _run(case, sandboxPolicy={"type": "workspaceWrite", "networkAccess": False})
    assert len(driver_calls) == 2, "Actual unverified diff must trigger the verification driver"
    assert _status(result) == "completed"
    assert case.marker.read_text(encoding="utf-8").splitlines() == ["first", "second", "verified"]
    assert len(late_attempts) == 1 and not late_attempts[0].success
    assert guards[0] is not guards[1]
    assert guards[0].bound_task_id == guards[1].bound_task_id
    assert len(case.admitted) == 1, "Verification must fork the epoch, never start a new token"
    assert all(case.holders_at_effect)
    assert len({str(call["task_id"]) for call in case.calls}) == 1
    turn = result["response"].result["turn"]
    verifications = [item for item in turn["items"] if item["type"] == "verification"]
    assert verifications and verifications[-1]["status"] == "completed"


@pytest.mark.parametrize("lease_gateway", ["react"], indirect=True)
@pytest.mark.parametrize("resume_mode", ["paused", "approved"])
def test_real_pause_checkpoint_then_continue_keeps_task_identity(
    lease_gateway: SimpleNamespace, resume_mode: str
) -> None:
    case = lease_gateway

    def pause_after_first(value: str) -> None:
        if value == "first":
            case.pause.request_pause(
                case.admitted[0].task_id,
                reason="user_request",
                thread_id=f"lease-admission-{case.branch}",
            )

    case.after_effect = pause_after_first
    paused = _run(case)
    assert _status(paused) == "paused"
    assert case.marker.read_text(encoding="utf-8").splitlines() == ["first"]
    task_id = case.admitted[0].task_id
    saved = case.supervisor.store.get(task_id)
    assert saved is not None and saved.status.value == "paused"
    checkpoints = case.journal.read_by_type("react_checkpoint")
    assert any(str(event.task_id) == task_id for event in checkpoints)
    old_guard = case.sessions[0].execution_lease
    if resume_mode == "approved":
        # Exercise the real task approval route's WAITING -> RUNNING update,
        # then the same checkpoint Continue request used for a paused task.
        case.supervisor.transition(
            task_id,
            "waiting_approval",
            metadata_patch={"approval_required": True, "approval_tool_name": "lease_marker"},
        )
        approved = case.client.post(
            f"/api/task-runs/{task_id}/approval-decision", json={"approved": True}
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["task_run"]["status"] == "running"
    resumed = _run(case, input=[{"type": "text", "text": "继续"}])
    assert _status(resumed) == "completed"
    assert case.marker.read_text(encoding="utf-8").splitlines() == ["first", "second"]
    assert len(case.admitted) == 2
    assert {record.task_id for record in case.admitted} == {task_id}
    assert case.admitted[0].lease.token != case.admitted[1].lease.token
    assert case.sessions[-1].execution_lease is not old_guard
    assert {str(call["task_id"]) for call in case.calls} == {task_id}


def test_real_approval_wait_retains_lease_and_executes_only_after_accept(
    lease_gateway: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runtime.sensing.gateway._realtime_react_stream_drive as drive_rs

    case = lease_gateway
    # The real approval policy recognizes write_* as high risk. Its handler
    # only writes our synthetic marker; neither loop nor approval is replaced.
    case.executor.registry.register(
        Skill(
            name="write_lease_marker",
            description="Write a synthetic marker after explicit user approval.",
            affinity=["write"],
            trusted_source="skill://public/lease-marker",
            handler=case.marker_handler,
        ),
        verify_tests=False,
    )
    case.runtime._default_agent.extra_skills.append("write_lease_marker")
    case.model.sequence = [("write_lease_marker", "first")]
    held = Event()
    renewed_while_held = Event()
    renewals: list[Any] = []
    real_heartbeat = case.supervisor.heartbeat

    def heartbeat(
        task_id: str,
        *,
        expected_lease_token: int | None = None,
        expected_lease_incarnation: str | None = None,
    ) -> Any:
        record = real_heartbeat(
            task_id,
            expected_lease_token=expected_lease_token,
            expected_lease_incarnation=expected_lease_incarnation,
        )
        if held.is_set():
            renewals.append((record, expected_lease_token, expected_lease_incarnation))
            renewed_while_held.set()
        return record

    monkeypatch.setattr(case.supervisor, "heartbeat", heartbeat)
    monkeypatch.setattr(drive_rs, "_SINGLE_AGENT_HEARTBEAT_INTERVAL_S", 0.02)
    monkeypatch.setattr(drive_rs, "_lease_renewal_interval_s", lambda ttl: 0.02)
    requests = []
    notifications = []
    with case.client.websocket_connect("/api/realtime") as ws:
        ws.send_text(
            encode_message(
                JsonRpcRequest(
                    id=1,
                    method="turn/start",
                    params={
                        "threadId": f"lease-approval-{case.branch}",
                        "mode": "react",
                        "input": [{"type": "text", "text": "Use write_lease_marker once."}],
                        "approvalPolicy": "on-request",
                    },
                )
            )
        )
        while True:
            message = decode_message(ws.receive_text())
            if isinstance(message, JsonRpcRequest):
                requests.append(message)
                assert message.method == "item/commandExecution/requestApproval"
                assert message.params["tool"] == "write_lease_marker"
                assert not case.marker.exists()
                held.set()
                assert renewed_while_held.wait(2), (
                    "Lease must renew while real approval is unanswered"
                )
                saved = case.supervisor.store.get(case.admitted[0].task_id)
                assert saved is not None and saved.status.value == "running"
                assert not case.marker.exists(), "Waiting for approval cannot invoke the handler"
                held.clear()
                ws.send_text(
                    encode_message(JsonRpcResponse(id=message.id, result={"action": "accept"}))
                )
            elif isinstance(message, Notification):
                notifications.append(message)
            elif isinstance(message, JsonRpcResponse) and message.id == 1:
                result = {"response": message, "notifications": notifications}
                break
    assert len(requests) == 1, (
        len(requests),
        _status(result),
        case.marker.read_text(encoding="utf-8") if case.marker.exists() else None,
    )
    assert _status(result) == "completed"
    assert case.marker.read_text(encoding="utf-8").splitlines() == ["first"]
    assert renewals and all(
        record.status.value == "running"
        and token == case.admitted[0].lease.token
        and incarnation == case.admitted[0].lease.incarnation
        for record, token, incarnation in renewals
    )
    saved = case.supervisor.store.get(case.admitted[0].task_id)
    assert saved is not None and saved.status.value == "completed"


def test_app_state_cannot_silently_disable_supervisor_after_store_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from runtime.memory.journal import InMemoryJournal
    from runtime.platform.ui.state import AppState

    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "state"))
    attempted = []
    failure = OSError("synthetic task store initialization failure")

    def fail_from_path(cls: type, path: Path, **kwargs: Any) -> Any:
        attempted.append(path)
        raise failure

    monkeypatch.setattr(TaskSupervisor, "from_path", classmethod(fail_from_path))
    with pytest.raises(OSError) as caught:
        AppState(registry=SkillRegistry(), journal=InMemoryJournal())
    assert caught.value is failure
    assert attempted == [(tmp_path / "state" / "task_runs.json").resolve()]


@pytest.mark.parametrize("lease_gateway", ["react"], indirect=True)
def test_real_executor_approval_hold_is_not_misclassified_as_lost_lease(
    lease_gateway: SimpleNamespace,
) -> None:
    case = lease_gateway

    def enforce_server_approval() -> None:
        session = current_session()
        assert session is not None
        # This is the real executor's server policy, not a forged waiting
        # event. It produces the authoritative HOLD and waiting_user tags.
        session.metadata.update(
            enforce_executor_approval=True,
            auto_approve=False,
            approval_risk_policy={"low": "ask"},
        )
        case.model.sequence.clear()

    case.before_execute = enforce_server_approval
    result = _run(case)
    assert not case.marker.exists()
    saved = case.supervisor.store.get(case.admitted[0].task_id)
    assert saved is not None and saved.status.value == "waiting_approval", saved
    assert saved.metadata.get("approval_required") is True
    assert saved.lease is not None and saved.lease.token == case.admitted[0].lease.token
    turn = result["response"].result["turn"]
    assert turn["status"] != "completed"
    assert (turn.get("error") or {}).get("disposition") == "blocked_on_user", turn


@pytest.mark.parametrize("lease_gateway", ["native"], indirect=True)
@pytest.mark.parametrize(
    "decision",
    ["decline", "interrupt", "takeover", "lease_loss_no_reply", "connection_lost", "timeout"],
)
def test_native_approval_refusal_or_interruption_cannot_execute_or_complete(
    lease_gateway: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, decision: str
) -> None:
    case = lease_gateway
    if decision in {"connection_lost", "timeout"}:
        import runtime.execution.tool_engine._native_tool_approval as approval_gate

        monkeypatch.setattr(approval_gate, "NATIVE_TOOL_APPROVAL_TIMEOUT_S", 0.4)
    if decision == "lease_loss_no_reply":
        import runtime.sensing.gateway._realtime_react_stream_drive as drive_rs

        monkeypatch.setattr(drive_rs, "_SINGLE_AGENT_HEARTBEAT_INTERVAL_S", 0.02)
        monkeypatch.setattr(drive_rs, "_lease_renewal_interval_s", lambda ttl: 0.02)
    case.executor.registry.register(
        Skill(
            name="write_lease_marker",
            description="Write a synthetic marker only after approval.",
            affinity=["write"],
            trusted_source="skill://public/lease-marker",
            handler=case.marker_handler,
        ),
        verify_tests=False,
    )
    case.runtime._default_agent.extra_skills.append("write_lease_marker")
    case.model.sequence = [("write_lease_marker", "first")]
    finished = Event()
    real_finish = case.runtime._record_task_run_finished

    def finish(*args: Any, **kwargs: Any) -> Any:
        try:
            return real_finish(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(case.runtime, "_record_task_run_finished", finish)
    result = None
    foreign_lease = None
    requests = []
    notifications = []
    with case.client.websocket_connect("/api/realtime") as ws:
        ws.send_text(
            encode_message(
                JsonRpcRequest(
                    id=1,
                    method="turn/start",
                    params={
                        "threadId": "native-approval-negative",
                        "mode": "react",
                        "input": [{"type": "text", "text": "Use write_lease_marker once."}],
                        "approvalPolicy": "on-request",
                    },
                )
            )
        )
        while True:
            message = decode_message(ws.receive_text())
            if isinstance(message, JsonRpcRequest):
                requests.append(message)
                assert message.method == "item/commandExecution/requestApproval"
                assert not case.marker.exists()
                # A broken loop would now receive a successful final answer;
                # trusted refusal must determine the turn, not that prose.
                case.model.sequence.clear()
                if decision == "connection_lost":
                    ws.close()
                    break
                if decision == "timeout":
                    continue
                if decision == "interrupt":
                    ws.send_text(
                        encode_message(
                            JsonRpcRequest(
                                id=2,
                                method="turn/interrupt",
                                params={
                                    "threadId": message.params["threadId"],
                                    "turnId": message.params["turnId"],
                                },
                            )
                        )
                    )
                    continue
                if decision in {"takeover", "lease_loss_no_reply"}:
                    task_id = case.admitted[0].task_id
                    _expire(case, task_id)
                    foreign_lease = case.other.takeover_task(
                        task_id, reason="approval wait test"
                    ).lease
                    if decision == "lease_loss_no_reply":
                        continue
                ws.send_text(
                    encode_message(
                        JsonRpcResponse(
                            id=message.id,
                            result={"action": "accept" if decision == "takeover" else "decline"},
                        )
                    )
                )
            elif isinstance(message, Notification):
                notifications.append(message)
            elif isinstance(message, JsonRpcResponse) and message.id == 1:
                result = {"response": message, "notifications": notifications}
                break
    assert len(requests) == 1
    assert finished.wait(3), (
        "Disconnected or interrupted approval must finish without a worker leak"
    )
    assert not case.marker.exists()
    if result is not None:
        _assert_not_completed(result)
    else:
        assert decision == "connection_lost"
    assert all(not connection.approval._pending for connection in case.gateway._connections)
    saved = case.supervisor.store.get(case.admitted[0].task_id)
    assert saved is not None and saved.status.value != "completed"
    if foreign_lease is not None:
        assert saved.lease == foreign_lease
        assert saved.status.value == "running"


@pytest.mark.parametrize("lease_gateway", ["native"], indirect=True)
@pytest.mark.parametrize(
    "server_policy",
    ["deny_even_auto", "long_argument_deny", "unavailable", "bypass", "accept_edits"],
)
def test_native_approval_obeys_server_policy_without_mutating_the_session(
    lease_gateway: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, server_policy: str
) -> None:
    case = lease_gateway
    name = "write_text_file" if server_policy == "accept_edits" else "write_lease_marker"
    if server_policy == "long_argument_deny":
        name = "fetch_synthetic"
    case.executor.registry.register(
        Skill(
            name=name,
            description="Write a synthetic marker under the server approval policy.",
            affinity=["write"],
            trusted_source="skill://public/lease-marker",
            handler=case.marker_handler,
        ),
        verify_tests=False,
    )
    case.runtime._default_agent.extra_skills.append(name)
    case.model.sequence = [(name, "first")]
    if server_policy == "long_argument_deny":
        # Synthetic risk material beyond the UI's 500-character preview.
        case.model.sequence = [(name, "x" * 600 + "synthetic_secret_marker")]
    sessions = []

    def configure_policy(_kwargs: Any) -> None:
        session = current_session()
        assert session is not None
        sessions.append(session)
        if server_policy == "deny_even_auto":
            session.metadata.update(auto_approve=True, approval_risk_policy={"high": "deny"})
        elif server_policy == "long_argument_deny":
            session.metadata["approval_risk_policy"] = {"medium": "allow", "high": "deny"}
        elif server_policy == "unavailable":
            session.metadata.pop("_approval_provider", None)
        else:
            session.metadata["permission_mode"] = (
                "bypassPermissions" if server_policy == "bypass" else "acceptEdits"
            )
            session.metadata["enforce_executor_approval"] = True

    case.before_start = configure_policy
    result = _run(case)
    if server_policy in {"deny_even_auto", "long_argument_deny", "unavailable"}:
        _assert_not_completed(result)
        assert not case.marker.exists()
        assert not case.calls, "Approval must precede dispatch to the real executor"
    else:
        assert _status(result) == "completed"
        assert case.marker.read_text(encoding="utf-8").splitlines() == ["first"]
    assert sessions
    if server_policy != "deny_even_auto":
        assert all(not session.metadata.get("auto_approve") for session in sessions)
