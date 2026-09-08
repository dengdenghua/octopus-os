"""Real persistent task leases fenced to one synthetic execution attempt."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from runtime.execution.subagents.threading import bind_subagent_session, forge_subagent_thread
from runtime.execution.tool_engine._executor_helpers import (
    _call_handler_with_transient_retry,
    _mark_task_waiting_approval,
)
from runtime.platform.process.session import Session, current_session, session_scope
from runtime.platform.process.session_executor import SessionExecutor
from runtime.platform.process.task_execution import TaskExecutionGuard
from runtime.platform.process.task_supervisor import (
    LostTaskLease,
    TaskLeaseConflict,
    TaskRunStatus,
    TaskSupervisor,
)


def _supervisor(tmp_path, *, holder="holder-a"):
    return TaskSupervisor.from_path(tmp_path / "task_runs.json", holder_id=holder)


def _expire(supervisor, task_id):
    supervisor.store.mutate(
        task_id,
        lambda record: record.model_copy(
            update={"lease": record.lease.model_copy(update={"expires_at": time.time() - 1})}
        ),
    )


def test_pending_guard_never_creates_a_task_from_metadata_or_a_dispatch(tmp_path):
    # Constructor remains inert; no task ID is inferred from a domain claim.
    supervisor = _supervisor(tmp_path)
    guard = TaskExecutionGuard(supervisor)
    session = Session(metadata={"task_id": "projectos-domain-task"}, execution_lease=guard)
    assert guard.bound_task_id is None
    with session_scope(session):
        for operation in (
            guard.assert_allowed,
            guard.heartbeat,
            lambda: guard.transition(TaskRunStatus.COMPLETED),
        ):
            with pytest.raises(LostTaskLease, match="pending"):
                operation()
    assert not supervisor.store.path.exists()


def test_duplicate_admit_is_read_only_and_different_task_cannot_replace_binding(tmp_path):
    supervisor = _supervisor(tmp_path)
    guard = TaskExecutionGuard(supervisor)
    first = guard.admit("real-runtime-task", title="Original")
    before = supervisor.store.path.read_bytes()
    second = guard.admit("real-runtime-task", title="Forged replacement")
    assert second.lease.token == first.lease.token
    assert second.title == "Original"
    assert supervisor.store.path.read_bytes() == before
    with pytest.raises(LostTaskLease, match="another task"):
        guard.admit("different-runtime-task")
    assert supervisor.store.path.read_bytes() == before
    assert guard.bound_task_id == "real-runtime-task"


def test_failed_admission_closes_guard_without_adopting_foreign_lease(tmp_path):
    owner = _supervisor(tmp_path)
    owner.start_task(task_id="task")
    other = _supervisor(tmp_path, holder="holder-b")
    guard = TaskExecutionGuard(other)
    before = owner.store.path.read_bytes()
    with pytest.raises(TaskLeaseConflict):
        guard.admit("task")
    assert guard.bound_task_id is None
    with pytest.raises(LostTaskLease, match="closed"):
        guard.admit("another-task")
    assert owner.store.path.read_bytes() == before


@pytest.mark.parametrize("operation", ["assert", "heartbeat", "transition"])
def test_same_holder_reissued_token_never_authorizes_the_old_attempt(tmp_path, operation):
    supervisor = _supervisor(tmp_path)
    old = TaskExecutionGuard(supervisor)
    old_record = old.admit("task")
    new_record = supervisor.start_task(task_id="task")
    assert new_record.lease.token != old_record.lease.token
    before = supervisor.store.path.read_bytes()
    callback = {
        "assert": old.assert_allowed,
        "heartbeat": old.heartbeat,
        "transition": lambda: old.transition(TaskRunStatus.COMPLETED, reason="stale attempt"),
    }[operation]
    with pytest.raises(LostTaskLease, match="token changed"):
        callback()
    assert supervisor.store.path.read_bytes() == before
    assert supervisor.assert_current_holder("task").lease.token == new_record.lease.token


@pytest.mark.parametrize("operation", ["heartbeat", "transition"])
def test_token_is_compared_inside_mutation_after_a_competing_restart(
    tmp_path, monkeypatch, operation
):
    supervisor = _supervisor(tmp_path)
    guard = TaskExecutionGuard(supervisor)
    first = guard.admit("task")
    assert guard.assert_allowed().lease.token == first.lease.token
    real_mutate = supervisor.store.mutate
    observed = {}

    def race(task_id, mutator):
        # Real intervening store mutation, not a fake lease verdict. The new
        # epoch arrives after any caller-side read and before the write lock.
        newer = supervisor.start_task(task_id=task_id)
        observed["token"] = newer.lease.token
        observed["bytes"] = supervisor.store.path.read_bytes()
        return real_mutate(task_id, mutator)

    monkeypatch.setattr(supervisor.store, "mutate", race)
    with pytest.raises(LostTaskLease, match="token changed"):
        if operation == "heartbeat":
            guard.heartbeat()
        else:
            guard.transition(TaskRunStatus.COMPLETED)
    assert observed["token"] != first.lease.token
    assert supervisor.store.path.read_bytes() == observed["bytes"]


def test_reconstructed_supervisor_must_check_explicit_token_without_cached_identity(tmp_path):
    supervisor = _supervisor(tmp_path)
    first = supervisor.start_task(task_id="task")
    second = supervisor.start_task(task_id="task")
    fresh = _supervisor(tmp_path)
    assert not fresh._lease_tokens
    before = supervisor.store.path.read_bytes()
    with pytest.raises(LostTaskLease, match="token changed"):
        fresh.heartbeat("task", expected_lease_token=first.lease.token)
    with pytest.raises(LostTaskLease, match="token changed"):
        fresh.transition("task", TaskRunStatus.COMPLETED, expected_lease_token=first.lease.token)
    assert supervisor.store.path.read_bytes() == before
    assert fresh.assert_current_holder("task", expected_lease_token=second.lease.token).lease


def test_approval_wait_cannot_revive_an_expired_and_taken_over_execution(tmp_path):
    supervisor = _supervisor(tmp_path)
    guard = TaskExecutionGuard(supervisor)
    guard.admit("task")
    guard.transition(TaskRunStatus.WAITING_APPROVAL)
    _expire(supervisor, "task")
    newer = _supervisor(tmp_path, holder="holder-b")
    taken = newer.takeover_task("task", by="synthetic-user", reason="explicit recovery")
    assert taken.status == TaskRunStatus.WAITING_APPROVAL
    before = newer.store.path.read_bytes()
    with pytest.raises(LostTaskLease):
        guard.assert_allowed()
    with pytest.raises(LostTaskLease):
        guard.heartbeat()
    guard.close()
    with pytest.raises(LostTaskLease):
        guard.transition(TaskRunStatus.COMPLETED)
    assert newer.store.path.read_bytes() == before


@pytest.mark.parametrize("status", [TaskRunStatus.PAUSED, TaskRunStatus.WAITING_APPROVAL])
def test_live_but_nonexecuting_state_can_renew_and_resume_without_allowing_tools(tmp_path, status):
    supervisor = _supervisor(tmp_path)
    guard = TaskExecutionGuard(supervisor)
    first = guard.admit("task")
    guard.transition(status)
    assert guard.heartbeat().lease.token == first.lease.token
    with pytest.raises(LostTaskLease, match=status.value):
        guard.assert_allowed()
    guard.transition(TaskRunStatus.RUNNING)
    assert guard.assert_allowed().lease.token == first.lease.token


def test_close_denies_late_work_but_allows_exact_epoch_finalization(tmp_path):
    supervisor = _supervisor(tmp_path)
    guard = TaskExecutionGuard(supervisor)
    guard.admit("task")
    guard.close()
    guard.close()
    for operation in (guard.assert_allowed, guard.heartbeat, lambda: guard.admit("task")):
        with pytest.raises(LostTaskLease, match="closed"):
            operation()
    completed = guard.transition(TaskRunStatus.COMPLETED, reason="verified")
    assert completed.status == TaskRunStatus.COMPLETED
    before = supervisor.store.path.read_bytes()
    with pytest.raises(LostTaskLease):
        guard.transition(TaskRunStatus.FAILED, metadata_patch={"stale": True})
    assert supervisor.store.path.read_bytes() == before


def test_missing_authority_record_fails_closed(tmp_path):
    supervisor = _supervisor(tmp_path)
    guard = TaskExecutionGuard(supervisor)
    guard.admit("task")
    supervisor.store.path.unlink()
    for operation in (
        guard.assert_allowed,
        guard.heartbeat,
        lambda: guard.transition(TaskRunStatus.COMPLETED),
    ):
        with pytest.raises(LostTaskLease, match="authority unavailable"):
            operation()
    assert not supervisor.store.path.exists()


def test_closed_scope_forks_without_new_token_or_reviving_old_worker(tmp_path):
    supervisor = _supervisor(tmp_path)
    old = TaskExecutionGuard(supervisor)
    original = old.admit("runtime-task")
    old.close()
    before = supervisor.store.path.read_bytes()
    new = old.fork()
    assert supervisor.store.path.read_bytes() == before
    assert new is not old
    assert new.bound_task_id == "runtime-task"
    assert new.assert_allowed().lease.token == original.lease.token
    with pytest.raises(LostTaskLease, match="closed"):
        old.assert_allowed()
    new.close()
    new.transition(TaskRunStatus.COMPLETED)
    with pytest.raises(LostTaskLease):
        old.fork()


def test_fork_cannot_adopt_a_new_same_holder_epoch_or_an_unadmitted_task(tmp_path):
    supervisor = _supervisor(tmp_path)
    old = TaskExecutionGuard(supervisor)
    with pytest.raises(LostTaskLease, match="pending"):
        old.fork()
    old.admit("task")
    old.close()
    supervisor.start_task(task_id="task")
    before = supervisor.store.path.read_bytes()
    with pytest.raises(LostTaskLease, match="token changed"):
        old.fork()
    assert supervisor.store.path.read_bytes() == before


@pytest.mark.parametrize("token", [0, -1, True, 1.5, "1"])
def test_explicit_invalid_tokens_cannot_fall_back_to_the_cached_holder(tmp_path, token):
    supervisor = _supervisor(tmp_path)
    supervisor.start_task(task_id="task")
    before = supervisor.store.path.read_bytes()
    with pytest.raises(LostTaskLease, match="invalid expected"):
        supervisor.heartbeat("task", expected_lease_token=token)
    assert supervisor.store.path.read_bytes() == before


def test_child_session_and_manual_worker_binding_keep_same_guard_despite_metadata(tmp_path):
    guard = TaskExecutionGuard(_supervisor(tmp_path))
    guard.admit("runtime-task")
    parent = Session(actor="synthetic-user", thread_id="parent", execution_lease=guard)
    child = bind_subagent_session(
        parent,
        forge_subagent_thread(parent, persist=False),
        flip_thread_id=True,
        extra_metadata={"execution_lease": "untrusted", "task_id": "projectos-claim-id"},
    )
    assert child.execution_lease is guard

    def worker():
        # The bridge and background producer manually bind a Session on their
        # raw worker threads; no ContextVar inheritance is assumed here.
        with session_scope(child):
            return current_session().execution_lease.assert_allowed().task_id

    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(worker).result(timeout=5) == "runtime-task"
        guard.close()
        with pytest.raises(LostTaskLease, match="closed"):
            pool.submit(worker).result(timeout=5)
    assert (
        Session(metadata={"task_id": "projectos-only", "execution_lease": guard}).execution_lease
        is None
    )


def test_session_executor_context_is_per_submit_and_timed_handler_keeps_guard(tmp_path):
    guards = [TaskExecutionGuard(_supervisor(tmp_path)) for _ in range(2)]
    for index, guard in enumerate(guards):
        guard.admit(f"task-{index}")
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait(timeout=5)
        return current_session().execution_lease.assert_allowed().task_id

    with SessionExecutor(max_workers=2) as pool:
        futures = []
        for guard in guards:
            with session_scope(Session(execution_lease=guard)):
                futures.append(pool.submit(worker))
        assert [future.result(timeout=5) for future in futures] == ["task-0", "task-1"]
    with session_scope(Session(execution_lease=guards[0])):
        result, tags = _call_handler_with_transient_retry(
            lambda: current_session().execution_lease.assert_allowed().task_id,
            {},
            timeout_s=5,
        )
        assert result == "task-0" and not tags
    assert current_session() is None


def test_approval_mark_uses_guard_identity_instead_of_metadata_store_or_domain_task(tmp_path):
    supervisor = _supervisor(tmp_path)
    guard = TaskExecutionGuard(supervisor)
    original = guard.admit("runtime-task")
    decoy = _supervisor(tmp_path / "decoy")
    decoy.start_task(task_id="domain-task")
    decoy_before = decoy.store.path.read_bytes()
    session = Session(
        execution_lease=guard,
        metadata={
            "task_id": "domain-task",
            "task_supervisor_store_path": str(decoy.store.path),
            "task_supervisor_holder_id": decoy.holder_id,
        },
    )
    with session_scope(session):
        _mark_task_waiting_approval(
            "synthetic-tool", "needs approval", metadata_patch={"approval_action": "approve"}
        )
    waiting = supervisor.store.get("runtime-task")
    assert waiting.status == TaskRunStatus.WAITING_APPROVAL
    assert waiting.lease.token == original.lease.token
    assert waiting.metadata["approval_tool_name"] == "synthetic-tool"
    assert waiting.metadata["approval_action"] == "approve"
    assert decoy.store.path.read_bytes() == decoy_before


@pytest.mark.parametrize("change", ["new_epoch", "closed_scope", "pending"])
def test_rejected_approval_mark_never_falls_back_to_unfenced_metadata(tmp_path, change):
    supervisor = _supervisor(tmp_path)
    guard = TaskExecutionGuard(supervisor)
    if change != "pending":
        guard.admit("runtime-task")
    else:
        supervisor.start_task(task_id="runtime-task")
    if change == "new_epoch":
        supervisor.start_task(task_id="runtime-task")
    elif change == "closed_scope":
        guard.close()
        assert guard.fork().assert_allowed().task_id == "runtime-task"
    before = supervisor.store.path.read_bytes()
    with session_scope(
        Session(
            execution_lease=guard,
            metadata={
                "task_id": "runtime-task",
                "task_supervisor_store_path": str(supervisor.store.path),
                "task_supervisor_holder_id": supervisor.holder_id,
            },
        )
    ):
        _mark_task_waiting_approval("late-tool", "late approval")
    assert supervisor.store.path.read_bytes() == before


def test_legacy_approval_metadata_still_works_but_domain_task_id_alone_is_inert(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.start_task(task_id="legacy-loop")
    before = supervisor.store.path.read_bytes()
    with session_scope(Session(metadata={"task_id": "legacy-loop"})):
        _mark_task_waiting_approval("tool", "reason")
    assert supervisor.store.path.read_bytes() == before
    with session_scope(
        Session(
            metadata={
                "task_id": "legacy-loop",
                "task_supervisor_store_path": str(supervisor.store.path),
                "task_supervisor_holder_id": supervisor.holder_id,
            }
        )
    ):
        _mark_task_waiting_approval("tool", "reason")
    assert supervisor.store.get("legacy-loop").status == TaskRunStatus.WAITING_APPROVAL


def _execution_stack(handler):
    from runtime.core.cerebrum import StaticPlanner
    from runtime.core.cerebrum.planner import Rule
    from runtime.core.graph_runtime import GraphRuntime
    from runtime.execution.suckers.registry import Skill, SkillRegistry
    from runtime.execution.tool_engine import ToolExecutor
    from runtime.memory.journal import InMemoryJournal
    from runtime.platform.models import BudgetSpec, SkillId
    from runtime.safety.auth import TrustEngine

    journal = InMemoryJournal()
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="execution_lease_probe",
            description="Read synthetic execution scope.",
            handler=handler,
            trusted_source="skill://public/execution_lease_probe",
            affinity=["read"],
        ),
        verify_tests=False,
    )
    executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
    )
    planner = StaticPlanner(
        rules=[
            Rule(
                name="synthetic-plan",
                intent_types=["task"],
                skill_sequence=[SkillId("execution_lease_probe")],
            )
        ],
        default_budget=BudgetSpec(tokens=10_000, usd=0.1),
        fallback_skill=SkillId("execution_lease_probe"),
    )
    return SimpleNamespace(
        executor=executor,
        registry=registry,
        planner=planner,
        runtime=GraphRuntime(executor=executor, journal=journal),
    )


@pytest.mark.parametrize("parent_source", ["ambient", "explicit", "bridge"])
def test_real_stack_runner_reconstruction_keeps_guard_and_child_trajectory_id(
    tmp_path, monkeypatch, parent_source
):
    from runtime.execution.parallel_agents.stack_runner import make_stack_subagent_runner

    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "runtime-data"))
    guard = TaskExecutionGuard(_supervisor(tmp_path))
    guard.admit("parent-runtime-task")
    parent = Session(execution_lease=guard, thread_id="parent")
    calls = []

    def probe(**_kwargs):
        calls.append(current_session().execution_lease)
        return {"ok": True}

    stack = _execution_stack(probe)
    trajectories = []
    runner = make_stack_subagent_runner(
        stack, summarizer=lambda trajectory: trajectories.append(trajectory) or "observed"
    )
    context = {
        "runtime_session_metadata": {"workspace_path": str(tmp_path), "mode": "code"},
        "thread_id": "child-thread",
    }
    if parent_source == "explicit":
        context["caller_session"] = parent
        scope = Session()  # no ambient authority; only the trusted Session object carries it
    else:
        scope = parent

    def dispatch(prompt):
        if parent_source == "bridge":
            from runtime.execution.subagents.bridge import call_subagent

            return call_subagent(
                "synthetic-lease-test-role",
                prompt,
                session=parent,
                runner=runner,
                timeout_s=5,
                context=context,
            )
        return runner(prompt, subagent_name="general-purpose", context=context)

    with session_scope(scope):
        dispatch("probe")
    assert calls == [guard], [
        (step.result.error_type, step.result.stderr_tags, step.action.args)
        for item in trajectories
        for step in item.steps
    ]
    assert str(trajectories[0].task_id) != guard.bound_task_id
    guard.close()
    with session_scope(scope):
        dispatch("probe again")
    assert calls == [guard], "late delegated work must not reach the real handler"
    assert not trajectories[-1].outcome.success


@pytest.mark.asyncio
async def test_real_dynamic_broker_reconstruction_keeps_captured_guard_after_approval(tmp_path):
    from runtime.execution.codex_backend.dynamic_tools import CodexDynamicToolBroker
    from runtime.execution.codex_backend.types import ApprovalRequest
    from runtime.safety.approval.approval_gate import AutoDenyProvider

    supervisor = _supervisor(tmp_path)
    guard = TaskExecutionGuard(supervisor)
    guard.admit("parent-runtime-task")
    parent = Session(execution_lease=guard)
    calls = []

    def probe():
        calls.append(current_session().execution_lease)
        return {"ok": True}

    stack = _execution_stack(probe)
    broker = CodexDynamicToolBroker(
        stack,
        SimpleNamespace(
            agent_id="synthetic-agent",
            arms=[SimpleNamespace(arm_id="read", allowed_skills=["execution_lease_probe"])],
            extra_skills=[],
        ),
        context={"caller_session": parent, "metadata": {"execution_lease": "forged"}},
        goal="Read synthetic execution scope",
        outer_thread_id="outer-thread",
        outer_turn_id="outer-turn",
        workspace=str(tmp_path),
        tenant_id="synthetic-tenant",
        principal_id="synthetic-actor",
        approval_provider=AutoDenyProvider(),
        is_interrupted=lambda: False,
    )
    broker.bind_inner_scope(thread_id="inner-thread", turn_id="inner-turn")

    def request(call_id):
        return ApprovalRequest(
            request_id=call_id,
            method="item/tool/call",
            params={
                "threadId": "inner-thread",
                "turnId": "inner-turn",
                "callId": call_id,
                "tool": broker.catalog.names[0],
                "arguments": {},
            },
        )

    with session_scope(Session()):
        first = await broker(request("first-call"))
        assert first["success"] is True, first
        assert calls == [guard]
        supervisor.start_task(task_id="parent-runtime-task")
        second = await broker(request("different-call"))
        assert second["success"] is False, second
        assert calls == [guard]


@pytest.mark.parametrize(
    "status",
    [
        TaskRunStatus.RUNNING,
        TaskRunStatus.VERIFYING,
        TaskRunStatus.REPAIRING,
        TaskRunStatus.WAITING_APPROVAL,
        TaskRunStatus.PENDING,
    ],
)
def test_new_same_holder_guard_cannot_replace_live_task_even_with_override_kwargs(tmp_path, status):
    supervisor = _supervisor(tmp_path)
    current = TaskExecutionGuard(supervisor)
    record = current.admit("task")
    current.transition(status)
    before = supervisor.store.path.read_bytes()
    replacement = TaskExecutionGuard(supervisor)
    with pytest.raises(TaskLeaseConflict):
        replacement.admit("task", reject_active_lease=False)
    assert replacement.bound_task_id is None
    assert supervisor.store.path.read_bytes() == before
    assert supervisor.assert_current_holder("task").lease.token == record.lease.token


def test_paused_same_holder_resume_gets_new_epoch_but_foreign_holder_cannot_take_live_lease(
    tmp_path,
):
    supervisor = _supervisor(tmp_path)
    previous = TaskExecutionGuard(supervisor)
    old = previous.admit("task")
    previous.transition(TaskRunStatus.PAUSED)
    foreign = TaskExecutionGuard(_supervisor(tmp_path, holder="other-process"))
    before = supervisor.store.path.read_bytes()
    with pytest.raises(TaskLeaseConflict):
        foreign.admit("task")
    assert supervisor.store.path.read_bytes() == before
    resumed = TaskExecutionGuard(supervisor)
    new = resumed.admit("task")
    assert new.lease.token != old.lease.token
    assert resumed.assert_allowed().status == TaskRunStatus.RUNNING
    with pytest.raises(LostTaskLease, match="token changed"):
        previous.assert_allowed()


def _approved_task(supervisor):
    previous = TaskExecutionGuard(supervisor)
    previous.admit("task", owner_id="alice", thread_id="thread-a")
    previous.transition(TaskRunStatus.WAITING_APPROVAL)
    approved = supervisor.record_approval_decision("task", approved=True, decided_by="alice")
    return previous, approved


def test_approved_resume_consumes_persisted_permit_once_and_fences_previous_scope(tmp_path):
    supervisor = _supervisor(tmp_path)
    previous, approved = _approved_task(supervisor)
    token = approved.lease.token
    assert approved.approval_resume_lease_token == token
    supervisor.heartbeat("task", expected_lease_token=token)
    reopened = _supervisor(tmp_path)
    assert reopened.store.get("task").approval_resume_lease_token == token

    resumed = TaskExecutionGuard(reopened)
    record = resumed.admit(
        "task", owner_id="alice", thread_id="thread-a", approved_resume_lease_token=token
    )
    assert record.lease.token > token
    assert record.approval_resume_lease_token is None
    assert resumed.assert_allowed().status == TaskRunStatus.RUNNING
    with pytest.raises(LostTaskLease, match="token changed"):
        previous.assert_allowed()
    previous.close()
    with pytest.raises(LostTaskLease, match="token changed"):
        previous.fork()

    before = reopened.store.path.read_bytes()
    for replay_token in (token, record.lease.token):
        with pytest.raises(LostTaskLease, match="approval resume"):
            TaskExecutionGuard(reopened).admit(
                "task",
                owner_id="alice",
                thread_id="thread-a",
                approved_resume_lease_token=replay_token,
            )
    assert reopened.store.path.read_bytes() == before
    with pytest.raises(ValueError, match="not waiting"):
        reopened.record_approval_decision("task", approved=True)


@pytest.mark.parametrize(
    ("owner_id", "thread_id", "holder"),
    [
        ("bob", "thread-a", "holder-a"),
        (None, "thread-a", "holder-a"),
        ("alice", "thread-b", "holder-a"),
        ("alice", None, "holder-a"),
        ("alice", "thread-a", "another-holder"),
    ],
)
def test_approved_resume_requires_exact_owner_thread_and_holder(
    tmp_path, owner_id, thread_id, holder
):
    supervisor = _supervisor(tmp_path)
    _, approved = _approved_task(supervisor)
    before = supervisor.store.path.read_bytes()
    with pytest.raises(LostTaskLease, match="approval resume"):
        TaskExecutionGuard(_supervisor(tmp_path, holder=holder)).admit(
            "task",
            owner_id=owner_id,
            thread_id=thread_id,
            approved_resume_lease_token=approved.lease.token,
        )
    assert supervisor.store.path.read_bytes() == before


@pytest.mark.parametrize("token", [0, -1, True, 1.5, "1"])
def test_approved_resume_rejects_noncanonical_token_without_writing(tmp_path, token):
    supervisor = _supervisor(tmp_path)
    _approved_task(supervisor)
    before = supervisor.store.path.read_bytes()
    with pytest.raises(ValueError, match="positive integer"):
        TaskExecutionGuard(supervisor).admit(
            "task", owner_id="alice", thread_id="thread-a", approved_resume_lease_token=token
        )
    assert supervisor.store.path.read_bytes() == before


def test_metadata_and_generic_transition_cannot_mint_approved_resume_permit(tmp_path):
    supervisor = _supervisor(tmp_path)
    original = TaskExecutionGuard(supervisor).admit(
        "task",
        owner_id="alice",
        thread_id="thread-a",
        metadata={"approval_resume_lease_token": 1, "approval_decision": "approved"},
    )
    token = original.lease.token
    current = supervisor.transition(
        "task",
        TaskRunStatus.RUNNING,
        metadata_patch={"approval_resume_lease_token": token, "approved_resume_lease_token": token},
    )
    assert current.approval_resume_lease_token is None
    before = supervisor.store.path.read_bytes()
    with pytest.raises(TypeError):
        supervisor.transition("task", TaskRunStatus.RUNNING, approval_resume_lease_token=token)
    with pytest.raises(LostTaskLease, match="approval resume"):
        TaskExecutionGuard(supervisor).admit(
            "task", owner_id="alice", thread_id="thread-a", approved_resume_lease_token=token
        )
    assert supervisor.store.path.read_bytes() == before


def test_wrong_approval_token_or_unknown_task_does_not_consume_real_permission(tmp_path):
    supervisor = _supervisor(tmp_path)
    _, approved = _approved_task(supervisor)
    token = approved.lease.token
    before = supervisor.store.path.read_bytes()
    for task_id, supplied in (("task", token + 1), ("unknown-task", token)):
        with pytest.raises(LostTaskLease, match="approval resume"):
            TaskExecutionGuard(supervisor).admit(
                task_id,
                owner_id="alice",
                thread_id="thread-a",
                approved_resume_lease_token=supplied,
            )
        assert supervisor.store.path.read_bytes() == before
    record = TaskExecutionGuard(supervisor).admit(
        "task", owner_id="alice", thread_id="thread-a", approved_resume_lease_token=token
    )
    assert record.approval_resume_lease_token is None


@pytest.mark.parametrize(
    "status",
    [
        TaskRunStatus.PAUSED,
        TaskRunStatus.VERIFYING,
        TaskRunStatus.WAITING_APPROVAL,
        TaskRunStatus.COMPLETED,
    ],
)
def test_approval_permit_is_invalidated_when_task_leaves_approved_running_state(tmp_path, status):
    supervisor = _supervisor(tmp_path)
    _, approved = _approved_task(supervisor)
    current = supervisor.transition("task", status)
    assert current.approval_resume_lease_token is None
    before = supervisor.store.path.read_bytes()
    with pytest.raises(LostTaskLease, match="approval resume"):
        TaskExecutionGuard(supervisor).admit(
            "task",
            owner_id="alice",
            thread_id="thread-a",
            approved_resume_lease_token=approved.lease.token,
        )
    assert supervisor.store.path.read_bytes() == before


@pytest.mark.parametrize("change", ["replace", "expire", "takeover", "reject"])
def test_approval_resume_cannot_adopt_replaced_expired_or_rejected_epoch(tmp_path, change):
    supervisor = _supervisor(tmp_path)
    _, approved = _approved_task(supervisor)
    if change == "replace":
        changed = supervisor.start_task(task_id="task")
    elif change == "expire":
        _expire(supervisor, "task")
        changed = supervisor.store.get("task")
    elif change == "takeover":
        _expire(supervisor, "task")
        changed = _supervisor(tmp_path, holder="new-owner").takeover_task("task")
    else:
        supervisor.transition("task", TaskRunStatus.WAITING_APPROVAL)
        changed = supervisor.record_approval_decision("task", approved=False)
    if change != "expire":
        assert changed.approval_resume_lease_token is None
    before = supervisor.store.path.read_bytes()
    with pytest.raises(LostTaskLease, match="approval resume"):
        TaskExecutionGuard(supervisor).admit(
            "task",
            owner_id="alice",
            thread_id="thread-a",
            approved_resume_lease_token=approved.lease.token,
        )
    assert supervisor.store.path.read_bytes() == before


def test_approved_resume_rechecks_epoch_inside_store_mutation(tmp_path, monkeypatch):
    supervisor = _supervisor(tmp_path)
    _, approved = _approved_task(supervisor)
    other = _supervisor(tmp_path)
    mutate = supervisor.store.upsert_mutate
    replacement = {}

    def replace_before_mutation(task_id, operation):
        replacement["record"] = other.start_task(task_id=task_id)
        replacement["bytes"] = other.store.path.read_bytes()
        return mutate(task_id, operation)

    monkeypatch.setattr(supervisor.store, "upsert_mutate", replace_before_mutation)
    with pytest.raises(LostTaskLease, match="approval resume"):
        TaskExecutionGuard(supervisor).admit(
            "task",
            owner_id="alice",
            thread_id="thread-a",
            approved_resume_lease_token=approved.lease.token,
        )
    assert supervisor.store.path.read_bytes() == replacement["bytes"]
    assert replacement["record"].approval_resume_lease_token is None


def test_failed_approved_resume_commit_keeps_permit_available_for_fresh_guard(
    tmp_path, monkeypatch
):
    supervisor = _supervisor(tmp_path)
    _, approved = _approved_task(supervisor)
    before = supervisor.store.path.read_bytes()
    write = supervisor.store._write_payload

    def fail_write(_payload):
        raise OSError("synthetic write failure")

    monkeypatch.setattr(supervisor.store, "_write_payload", fail_write)
    with pytest.raises(OSError, match="synthetic write failure"):
        TaskExecutionGuard(supervisor).admit(
            "task",
            owner_id="alice",
            thread_id="thread-a",
            approved_resume_lease_token=approved.lease.token,
        )
    assert supervisor.store.path.read_bytes() == before
    monkeypatch.setattr(supervisor.store, "_write_payload", write)
    resumed = TaskExecutionGuard(supervisor).admit(
        "task",
        owner_id="alice",
        thread_id="thread-a",
        approved_resume_lease_token=approved.lease.token,
    )
    assert resumed.approval_resume_lease_token is None


@pytest.mark.parametrize("backup_kind", ["none", "corrupt", "same-epoch", "older-epoch"])
def test_corrupt_primary_never_recovers_execution_authority_from_backup(tmp_path, backup_kind):
    supervisor = _supervisor(tmp_path)
    old = TaskExecutionGuard(supervisor)
    old.admit("task", owner_id="alice", thread_id="thread-a")
    current = old
    backup = supervisor.store.path.with_suffix(".json.bak")
    if backup_kind == "older-epoch":
        old.transition(TaskRunStatus.PAUSED)
        current = TaskExecutionGuard(supervisor)
        current.admit("task", owner_id="alice", thread_id="thread-a")
    elif backup_kind != "none":
        old.heartbeat()
    supervisor.store.path.write_bytes(b"{corrupt-primary")
    if backup_kind == "corrupt":
        backup.write_bytes(b"{corrupt-backup")
    primary_bytes = supervisor.store.path.read_bytes()
    backup_bytes = backup.read_bytes() if backup.exists() else None
    for guard in dict.fromkeys((old, current)):
        for action in (guard.assert_allowed, guard.assert_held, guard.heartbeat, guard.fork):
            with pytest.raises(LostTaskLease, match="authority unavailable"):
                action()
        guard.close()
        with pytest.raises(LostTaskLease, match="authority unavailable"):
            guard.transition(TaskRunStatus.COMPLETED)
    with pytest.raises(OSError, match="explicit recovery required"):
        TaskExecutionGuard(_supervisor(tmp_path)).admit("task")
    assert supervisor.store.path.read_bytes() == primary_bytes
    assert (backup.read_bytes() if backup.exists() else None) == backup_bytes


def test_deleting_primary_with_existing_backup_requires_explicit_recovery(tmp_path):
    supervisor = _supervisor(tmp_path)
    guard = TaskExecutionGuard(supervisor)
    guard.admit("task")
    guard.heartbeat()
    backup = supervisor.store.path.with_suffix(".json.bak")
    before = backup.read_bytes()
    supervisor.store.path.unlink()
    with pytest.raises(LostTaskLease, match="authority unavailable"):
        guard.assert_held()
    with pytest.raises(OSError, match="primary is missing"):
        TaskExecutionGuard(_supervisor(tmp_path)).admit("task")
    assert not supervisor.store.path.exists()
    assert backup.read_bytes() == before


def test_deleted_store_recreation_cannot_reuse_a_live_or_closed_guards_incarnation(tmp_path):
    supervisor = _supervisor(tmp_path)
    old = TaskExecutionGuard(supervisor)
    first = old.admit("task", owner_id="alice", thread_id="thread-a")
    assert not supervisor.store.path.with_suffix(".json.bak").exists()
    supervisor.store.path.unlink()
    with pytest.raises(LostTaskLease):
        old.assert_allowed()
    reopened = _supervisor(tmp_path)
    replacement = TaskExecutionGuard(reopened)
    second = replacement.admit("task", owner_id="bob", thread_id="thread-b")
    assert first.lease.token == second.lease.token == 1
    assert first.lease.incarnation != second.lease.incarnation
    before = reopened.store.path.read_bytes()
    for action in (old.assert_allowed, old.assert_held, old.heartbeat, old.fork):
        with pytest.raises(LostTaskLease, match="incarnation changed"):
            action()
    old.close()
    with pytest.raises(LostTaskLease, match="incarnation changed"):
        old.transition(TaskRunStatus.COMPLETED)
    assert reopened.store.path.read_bytes() == before
    assert replacement.assert_allowed().owner_id == "bob"


def test_old_record_without_incarnation_remains_legacy_readable_but_new_guard_gets_new_identity(
    tmp_path,
):
    supervisor = _supervisor(tmp_path)
    started = supervisor.start_task(task_id="task")
    payload = json.loads(supervisor.store.path.read_text(encoding="utf-8"))
    del payload["tasks"][0]["lease"]["incarnation"]
    supervisor.store.path.write_text(json.dumps(payload), encoding="utf-8")
    legacy = _supervisor(tmp_path)
    assert legacy.store.get("task").lease.incarnation is None
    assert legacy.assert_current_holder("task").lease.token == started.lease.token
    with pytest.raises(TaskLeaseConflict):
        TaskExecutionGuard(legacy).admit("task")
    legacy.transition("task", TaskRunStatus.PAUSED)
    guard = TaskExecutionGuard(legacy)
    new = guard.admit("task")
    assert new.lease.token > started.lease.token
    assert len(new.lease.incarnation) == 32
    assert guard.assert_allowed().lease.incarnation == new.lease.incarnation


@pytest.mark.parametrize("status", [TaskRunStatus.WAITING_APPROVAL, TaskRunStatus.PAUSED])
def test_assert_held_observes_waiting_authority_without_allowing_tools_or_mutating_store(
    tmp_path, status
):
    supervisor = _supervisor(tmp_path)
    guard = TaskExecutionGuard(supervisor)
    guard.admit("task")
    guard.transition(status)
    before = supervisor.store.path.read_bytes()
    assert guard.assert_held().status == status
    with pytest.raises(LostTaskLease, match=f"task is {status.value}"):
        guard.assert_allowed()
    guard.close()
    with pytest.raises(LostTaskLease, match="closed"):
        guard.assert_held()
    assert guard.assert_held(require_open=False).status == status
    assert supervisor.store.path.read_bytes() == before


@pytest.mark.parametrize("approved_before_settle", [False, True])
def test_blocked_turn_settlement_preserves_wait_or_concurrent_approval(
    tmp_path, approved_before_settle
):
    supervisor = _supervisor(tmp_path)
    guard = TaskExecutionGuard(supervisor)
    guard.admit("task", owner_id="alice", thread_id="thread-a")
    waiting = guard.transition(TaskRunStatus.WAITING_APPROVAL, reason="needs approval")
    if approved_before_settle:
        expected = supervisor.record_approval_decision("task", approved=True, decided_by="alice")
    else:
        expected = waiting
    guard.close()
    settled = guard.transition(
        TaskRunStatus.FAILED,
        reason="blocked_on_user",
        checkpoint_id="checkpoint-a",
        metadata_patch={"last_turn": "ended"},
        preserve_approval_wait=True,
    )
    assert settled.status == expected.status
    assert settled.terminal_reason == expected.terminal_reason
    assert settled.lease == expected.lease
    assert settled.approval_resume_lease_token == expected.approval_resume_lease_token
    assert settled.latest_checkpoint_id == "checkpoint-a"
    assert settled.metadata["last_turn"] == "ended"
    assert settled.completed_at is None
    if not approved_before_settle:
        supervisor.record_approval_decision("task", approved=True, decided_by="alice")
    token = supervisor.store.get("task").approval_resume_lease_token
    resumed = TaskExecutionGuard(supervisor).admit(
        "task", owner_id="alice", thread_id="thread-a", approved_resume_lease_token=token
    )
    assert resumed.lease.incarnation != waiting.lease.incarnation
    before = supervisor.store.path.read_bytes()
    with pytest.raises(LostTaskLease):
        guard.transition(TaskRunStatus.FAILED, preserve_approval_wait=True)
    assert supervisor.store.path.read_bytes() == before


def test_preserve_approval_wait_does_not_suppress_unrelated_terminal_outcomes(tmp_path):
    supervisor = _supervisor(tmp_path)
    guard = TaskExecutionGuard(supervisor)
    guard.admit("task")
    settled = guard.transition(
        TaskRunStatus.FAILED, reason="real failure", preserve_approval_wait=True
    )
    assert settled.status == TaskRunStatus.FAILED
    assert settled.terminal_reason == "real failure"
    assert settled.lease is None


@pytest.mark.parametrize("operation", ["heartbeat", "transition"])
def test_incarnation_is_checked_inside_mutation_after_whole_store_recreation(
    tmp_path, monkeypatch, operation
):
    supervisor = _supervisor(tmp_path)
    old = TaskExecutionGuard(supervisor)
    first = old.admit("task")
    mutate = supervisor.store.mutate
    fresh_bytes = {}

    def recreate_before_mutation(task_id, mutator):
        supervisor.store.path.unlink()
        replacement = _supervisor(tmp_path).start_task(task_id=task_id)
        assert replacement.lease.token == first.lease.token
        fresh_bytes["bytes"] = supervisor.store.path.read_bytes()
        return mutate(task_id, mutator)

    monkeypatch.setattr(supervisor.store, "mutate", recreate_before_mutation)
    with pytest.raises(LostTaskLease, match="incarnation changed"):
        if operation == "heartbeat":
            old.heartbeat()
        else:
            old.transition(TaskRunStatus.COMPLETED)
    assert supervisor.store.path.read_bytes() == fresh_bytes["bytes"]
