"""Real persistent recovery failures must never authorize fresh execution."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from runtime.core.cerebrum import pause_control, react_loop, react_resume
from runtime.core.cerebrum.checkpoint_integrity import validate_checkpoint_state
from runtime.memory.diagnostics.trace_store import AgentTraceStore
from runtime.memory.journal import JSONLJournal
from runtime.memory.journal._journal_models import ReactCheckpointEvent
from runtime.platform.models.llm import Message
from runtime.platform.process.session import Session, session_scope
from runtime.sensing.gateway import _realtime_turn_lifecycle_resume as lifecycle


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    task = str(uuid4())
    thread = "synthetic-resume"
    journal = JSONLJournal(tmp_path / "journal.jsonl")
    trace = AgentTraceStore(tmp_path / "trace.sqlite")
    pause = pause_control.PauseController(store_path=tmp_path / "pause.json")
    pause.request_pause(task, reason="user_request", thread_id=thread)
    pause.mark_paused(task)
    pause.set_grant(task, extra_iterations=5)
    monkeypatch.setattr(pause_control, "get_pause_controller", lambda: pause)
    runtime = SimpleNamespace(
        _stack=SimpleNamespace(journal=journal),
        _trace_store=trace,
        _resume_intents_lock=asyncio.Lock(),
        _pending_resume_intents={},
    )
    yield SimpleNamespace(
        task=task,
        thread=thread,
        journal=journal,
        trace=trace,
        pause=pause,
        runtime=runtime,
        directory=tmp_path,
    )
    trace.close()


def state(**overrides):
    return {
        "iteration_completed": 2,
        "messages_snapshot": [{"role": "user", "content": "Synthetic original task"}],
        "steps_snapshot": [
            {"iteration": 2, "action": "write_text_file", "observation": "Already wrote a file"}
        ],
        **overrides,
    }


def intent_for(recovery, checkpoint_id=0):
    return SimpleNamespace(
        user_context={
            "resume_intent": {
                "task_id": recovery.task,
                "checkpoint_type": "react",
                "checkpoint_id": checkpoint_id,
                "confirmed": True,
            }
        }
    )


def register(recovery, intent):
    with session_scope(
        Session(thread_id=recovery.thread, metadata={"_trace_store": recovery.trace})
    ):
        return react_resume._resume_or_register_turn(
            recovery.runtime._stack,
            intent,
            SimpleNamespace(agent_id="synthetic"),
            resume_task_id=recovery.task,
            react_task_id=recovery.task,
            thread_id=recovery.thread,
            max_iterations=8,
            active_max_tokens_budget=1000,
            active_max_usd_budget=1,
            messages=[Message(role="user", content="Continue")],
        )


@pytest.mark.parametrize(
    "kind,code",
    [
        ("missing", "resume_checkpoint_missing"),
        ("invalid_json", "resume_checkpoint_missing"),
        ("bad_role", "resume_checkpoint_invalid"),
        ("future_journal", "resume_checkpoint_version_unsupported"),
        ("future_trace", "resume_checkpoint_version_unsupported"),
        ("bad_trace_shapes", "resume_checkpoint_invalid"),
        ("bad_trace_state", "resume_checkpoint_invalid"),
        ("wrong_task", "resume_checkpoint_missing"),
        ("missing_selected_with_valid_journal", "resume_checkpoint_missing"),
    ],
)
def test_persistent_rejection_keeps_pause_grant_and_never_bootstraps(
    recovery, monkeypatch, kind, code
):
    checkpoint_id = 0
    if kind in {"bad_role", "future_journal", "missing_selected_with_valid_journal"}:
        recovery.journal.write(
            ReactCheckpointEvent(
                task_id=recovery.task,
                **state(),
                schema_version=999 if kind == "future_journal" else 1,
            ).model_copy(update={"messages_snapshot": [{"role": "admin", "content": "invalid"}]})
            if kind == "bad_role"
            else ReactCheckpointEvent(
                task_id=recovery.task,
                **state(),
                schema_version=999 if kind == "future_journal" else 1,
            )
        )
        if kind == "missing_selected_with_valid_journal":
            checkpoint_id = 999
    elif kind == "invalid_json":
        (recovery.directory / "journal.jsonl").write_text('{"broken checkpoint"\n', encoding="utf8")
    elif kind not in {"missing"}:
        raw = state()
        if kind == "future_trace":
            raw["schema_version"] = 999
        elif kind == "bad_trace_shapes":
            raw.update(messages_snapshot="not-a-list", steps_snapshot="not-a-list")
        elif kind == "bad_trace_state":
            raw = ["not-an-object"]
        checkpoint_id = recovery.trace.record_checkpoint(
            task_id=str(uuid4()) if kind == "wrong_task" else recovery.task,
            thread_id=recovery.thread,
            checkpoint_type="react",
            iteration=2,
            state=raw,
        )
    intent = intent_for(recovery, checkpoint_id)
    pause_before = (recovery.directory / "pause.json").read_bytes()
    with pytest.raises(react_resume.ResumeCheckpointError) as rejected:
        register(recovery, intent)
    assert rejected.value.code == code
    assert rejected.value.task_id == recovery.task
    assert (recovery.directory / "pause.json").read_bytes() == pause_before
    assert recovery.pause.get_request(recovery.task) is not None

    # The actual public generator must reject before bootstrap/delegation,
    # not merely avoid the eventual first model iteration.
    def forbidden(*args, **kwargs):
        pytest.fail("resume rejection reached execution bootstrap")

    monkeypatch.setattr(react_loop, "_resolve_turn_bootstrap", forbidden)
    with (
        session_scope(
            Session(thread_id=recovery.thread, metadata={"_trace_store": recovery.trace})
        ),
        pytest.raises(react_resume.ResumeCheckpointError),
    ):
        next(
            react_loop.stream_react_loop(
                recovery.runtime._stack,
                intent,
                None,
                resume_task_id=recovery.task,
            )
        )
    assert recovery.pause.consume_grant(recovery.task)["extra_iterations"] == 5


@pytest.mark.parametrize("version", [None, 1])
def test_legacy_missing_version_and_current_version_restore_existing_steps(recovery, version):
    raw = state()
    if version is not None:
        raw["schema_version"] = version
    checkpoint_id = recovery.trace.record_checkpoint(
        task_id=recovery.task,
        thread_id=recovery.thread,
        checkpoint_type="react",
        iteration=2,
        state=raw,
    )
    result = register(recovery, intent_for(recovery, checkpoint_id))
    assert result.resume_from_iter == 2
    assert result.steps[0].observation == "Already wrote a file"
    assert result.max_iterations == 13
    assert result.react_task_id == recovery.task
    assert recovery.pause.get_request(recovery.task) is None


@pytest.mark.parametrize("version", [0, -1, 2, 999, "1", True, None])
def test_explicit_unsupported_versions_rejected(version):
    result = validate_checkpoint_state(state(schema_version=version), iteration=2)
    assert not result.resume_safe
    assert "unsupported_checkpoint_version" in result.errors


def test_bad_latest_trace_does_not_advertise_older_journal_as_resumable(recovery):
    recovery.journal.write(ReactCheckpointEvent(task_id=recovery.task, **state()))
    recovery.trace.record_checkpoint(
        task_id=recovery.task,
        thread_id=recovery.thread,
        checkpoint_type="react",
        iteration=2,
        state=state(messages_snapshot="bad"),
    )
    assert lifecycle._resume_checkpoint_metadata(recovery.runtime, recovery.task) is None


def test_banner_continue_missing_checkpoint_rejects_and_keeps_handoff(recovery):
    recovery.pause.set_pending_resume(recovery.thread, recovery.task)
    with pytest.raises(react_resume.ResumeCheckpointError):
        asyncio.run(
            lifecycle._consume_paused_task_resume_intent(recovery.runtime, recovery.thread, "继续")
        )
    assert recovery.pause.consume_pending_resume(recovery.thread) == recovery.task
    assert recovery.pause.get_request(recovery.task) is not None
    assert recovery.pause.consume_grant(recovery.task)["extra_iterations"] == 5


def test_confirmation_damaged_after_proposal_remains_pending_on_disk(recovery):
    checkpoint_id = recovery.trace.record_checkpoint(
        task_id=recovery.task,
        thread_id=recovery.thread,
        checkpoint_type="react",
        iteration=2,
        state=state(messages_snapshot="damaged"),
    )
    pending = intent_for(recovery, checkpoint_id).user_context["resume_intent"]
    pending.update(confirmed=False, requires_confirmation=True)
    asyncio.run(lifecycle._record_pending_resume_intent(recovery.runtime, recovery.thread, pending))
    # Read back via the durable request after losing the in-memory mirror.
    recovery.runtime._pending_resume_intents.clear()
    with pytest.raises(react_resume.ResumeCheckpointError):
        asyncio.run(
            lifecycle._consume_confirmed_resume_intent(
                recovery.runtime, recovery.thread, f"确认恢复 checkpoint #{checkpoint_id}"
            )
        )
    request = recovery.trace.latest_pending_resume_request(thread_id=recovery.thread)
    assert request["checkpoint_id"] == checkpoint_id
    assert request["status"] == "pending"
    assert recovery.pause.consume_grant(recovery.task)["extra_iterations"] == 5


def test_explicit_confirmation_without_request_rejects_but_new_tasks_stay_new(recovery):
    with pytest.raises(react_resume.ResumeCheckpointError) as failure:
        asyncio.run(
            lifecycle._consume_confirmed_resume_intent(
                recovery.runtime, recovery.thread, "确认恢复 checkpoint #99"
            )
        )
    assert failure.value.code == "resume_confirmation_unavailable"
    assert (
        asyncio.run(
            lifecycle._consume_confirmed_resume_intent(
                recovery.runtime, recovery.thread, "新建一个报告"
            )
        )
        is None
    )

    assert (
        asyncio.run(
            lifecycle._consume_paused_task_resume_intent(recovery.runtime, "new-thread", "继续")
        )
        is None
    )


def test_final_checkpoint_with_unused_grant_cannot_reenter_loop(recovery):
    recovery.journal.write(
        ReactCheckpointEvent(
            task_id=recovery.task,
            **state(has_final_answer=True, final_answer="Already finished"),
        )
    )
    result = register(recovery, intent_for(recovery))
    assert result.final_answer == "Already finished"
    assert result.resume_from_iter == result.max_iterations == 13


@pytest.mark.parametrize("backend", ["journal", "trace"])
@pytest.mark.parametrize("actor", ["owner", "other", None])
def test_recovery_obeys_server_scope_and_anonymous_cannot_read_owned_history(
    recovery, backend, actor
):
    from runtime.safety.auth.scope import TenantScope
    from runtime.safety.recovery.tenant_scope import (
        AUTHORITATIVE_SCOPE_CONTEXT_KEY,
        authoritative_scope_context,
    )

    checkpoint_id = 0
    if backend == "journal":
        recovery.journal.write(
            ReactCheckpointEvent(
                task_id=recovery.task,
                tenant_id="tenant",
                owner_actor_id="owner",
                **state(),
            )
        )
    else:
        checkpoint_id = recovery.trace.record_checkpoint(
            task_id=recovery.task,
            thread_id=recovery.thread,
            checkpoint_type="react",
            iteration=2,
            state=state(),
            tenant_id="tenant",
            owner_actor_id="owner",
        )
    intent = intent_for(recovery, checkpoint_id)
    scope = TenantScope(tenant_id="tenant", actor_id=actor) if actor else None
    if scope is not None:
        intent.user_context[AUTHORITATIVE_SCOPE_CONTEXT_KEY] = authoritative_scope_context(scope)
    if actor == "owner":
        lifecycle._validate_realtime_resume(
            recovery.runtime,
            intent.user_context["resume_intent"],
            thread_id=recovery.thread,
            scope=scope,
        )
        assert register(recovery, intent).resume_from_iter == 2
    else:
        with pytest.raises(react_resume.ResumeCheckpointError):
            lifecycle._validate_realtime_resume(
                recovery.runtime,
                intent.user_context["resume_intent"],
                thread_id=recovery.thread,
                scope=scope,
            )
        with pytest.raises(react_resume.ResumeCheckpointError):
            register(recovery, intent)
        assert recovery.pause.get_request(recovery.task) is not None


@pytest.mark.parametrize(
    "raw",
    [
        {"messages_snapshot": [{"role": ["user"], "content": "bad"}]},
        {"messages_snapshot": [{"role": "user", "content": {"bad": "shape"}}]},
        {"messages_snapshot": [{"role": "assistant", "content": "x", "phase": []}]},
        {"messages_snapshot": [{"role": "assistant", "content": "x", "tool_calls": "bad"}]},
        {"steps_snapshot": [{"iteration": 2, "action": ["write_text_file"]}]},
        {
            "steps_snapshot": [
                {"iteration": 2, "action": "write_text_file", "action_results": "bad"}
            ]
        },
    ],
)
def test_unrestorable_shapes_reject_before_message_rehydration(raw):
    assert not validate_checkpoint_state(state(**raw), iteration=2).resume_safe


def test_two_runtime_confirmations_cannot_consume_one_durable_request_twice(recovery):
    checkpoint_id = recovery.trace.record_checkpoint(
        task_id=recovery.task, thread_id=recovery.thread, checkpoint_type="react",
        iteration=2, state=state(),
    )
    pending = intent_for(recovery, checkpoint_id).user_context["resume_intent"]
    pending.update(confirmed=False, requires_confirmation=True)
    asyncio.run(lifecycle._record_pending_resume_intent(recovery.runtime, recovery.thread, pending))
    second = SimpleNamespace(**vars(recovery.runtime))
    second._resume_intents_lock = asyncio.Lock()
    second._pending_resume_intents = {recovery.thread: dict(pending)}
    text = f"确认恢复 checkpoint #{checkpoint_id}"
    first = asyncio.run(lifecycle._consume_confirmed_resume_intent(recovery.runtime, recovery.thread, text))
    assert first["confirmed"] is True
    with pytest.raises(react_resume.ResumeCheckpointError) as failure:
        asyncio.run(lifecycle._consume_confirmed_resume_intent(second, recovery.thread, text))
    assert failure.value.code == "resume_confirmation_unavailable"
    assert recovery.thread in second._pending_resume_intents
    assert recovery.pause.get_request(recovery.task) is not None
    assert recovery.pause.consume_grant(recovery.task)["extra_iterations"] == 5


def test_sqlite_read_failure_is_typed_and_retains_original_recovery(recovery):
    checkpoint_id = recovery.trace.record_checkpoint(
        task_id=recovery.task, thread_id=recovery.thread, checkpoint_type="react",
        iteration=2, state=state(),
    )
    recovery.trace.close()
    with pytest.raises(react_resume.ResumeCheckpointError) as failure:
        register(recovery, intent_for(recovery, checkpoint_id))
    assert failure.value.code == "resume_checkpoint_unavailable"
    assert recovery.pause.get_request(recovery.task) is not None
    assert recovery.pause.consume_grant(recovery.task)["extra_iterations"] == 5
