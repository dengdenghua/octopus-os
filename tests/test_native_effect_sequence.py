"""Real SQLite/JSONL native sequencing; no model or external service calls."""

from __future__ import annotations

import multiprocessing
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest

from runtime.execution.tool_engine.effect_receipts import (
    NativeEffectRecoveryRequired,
    ToolEffectReceiptIndex,
    args_fingerprint,
    effect_key,
)
from runtime.execution.tool_engine.effect_store import SQLiteEffectStore
from runtime.memory.journal import InMemoryJournal, JSONLJournal, ToolEffectIntentEvent
from runtime.platform.models import ArmId, ExecutionResult, SkillId, Step, TaskId, ToolCall


def _step(number: int, *, uncertain: bool = False, success: bool = True) -> Step:
    call = ToolCall(caller="agentic", sucker_id=SkillId("native_test"), args={"value": number})
    return Step(
        step_id=number,
        node_id=f"native_{number}",
        action=call,
        result=ExecutionResult(
            call_id=call.call_id,
            status="success" if success else "failed",
            output={"value": number},
            effect_receipt=(
                {
                    "schema": "echo.tool.effect_receipt.v1",
                    "sealed": True,
                    "emitted_by": "tool_executor",
                    "state": "indeterminate",
                    "effect_class": "external_or_unknown",
                    "retry_safe": False,
                }
                if uncertain
                else {}
            ),
        ),
    )


def _intent(journal, task, step, *, side_effecting=True):
    event = ToolEffectIntentEvent(
        task_id=task,
        arm_id=ArmId("native"),
        effect_key=effect_key(task, step.step_id, step.action.sucker_id, step.action.args),
        call_id=str(step.action.call_id),
        step_id=step.step_id,
        sucker_id=str(step.action.sucker_id),
        args_fingerprint=args_fingerprint(step.action.args),
        side_effecting=side_effecting,
    )
    journal.write(event)
    return event


def _claim(store, task, step, *, side_effecting=True, holder="owner"):
    return store.claim(
        effect_key=effect_key(task, step.step_id, step.action.sucker_id, step.action.args),
        task_id=str(task),
        step_id=step.step_id,
        sucker_id=str(step.action.sucker_id),
        args_fingerprint=args_fingerprint(step.action.args),
        side_effecting=side_effecting,
        holder_id=holder,
        lease_ttl_s=60,
        observed_durable_intent=False,
    )


def _started(store, journal, task, step, *, side_effecting=True):
    claim = _claim(store, task, step, side_effecting=side_effecting)
    event = _intent(journal, task, step, side_effecting=side_effecting)
    assert store.mark_started(
        effect_key=event.effect_key,
        holder_id="owner",
        fencing_token=claim.fencing_token,
        call_id=event.call_id,
        lease_ttl_s=60,
    )
    return event, claim


def _allocate_child(db_path, journal_path, task, ready, start, results):
    """Each spawn owns a fresh journal/index/store and a real SQLite connection."""
    try:
        index = ToolEffectReceiptIndex(JSONLJournal(journal_path), store=SQLiteEffectStore(db_path))
        ready.put(True)
        if not start.wait(15):
            raise TimeoutError("test start signal missing")
        results.put(index.reserve_native_step_ids(TaskId(UUID(task)), 17))
    except BaseException as exc:
        results.put({"error": repr(exc)})


def test_persistent_sequence_seeds_legacy_step_intent_and_receipt(tmp_path):
    task = TaskId(uuid4())
    journal = JSONLJournal(tmp_path / "events.jsonl")
    journal.write_step(task, ArmId("native"), _step(40))
    _intent(journal, task, _step(47), side_effecting=False)
    store = SQLiteEffectStore(tmp_path / "effects.db")
    _claim(store, task, _step(51), side_effecting=False)
    index = ToolEffectReceiptIndex(journal, store=store)
    assert index.reserve_native_step_ids(task, 3) == [52, 53, 54]
    # Reserved but never executed IDs also survive restart.
    restarted = ToolEffectReceiptIndex(
        JSONLJournal(tmp_path / "events.jsonl"),
        store=SQLiteEffectStore(tmp_path / "effects.db"),
    )
    assert restarted.reserve_native_step_ids(task, 2) == [55, 56]
    journal.write_step(task, ArmId("native"), _step(80))
    assert restarted.reserve_native_step_ids(task, 1) == [81]
    assert restarted.reserve_native_step_ids(TaskId(uuid4()), 1) == [1]


def test_independent_processes_allocate_disjoint_persisted_ranges(tmp_path):
    task = TaskId(uuid4())
    path = tmp_path / "events.jsonl"
    JSONLJournal(path).write_step(task, ArmId("native"), _step(90))
    db_path = tmp_path / "effects.db"
    SQLiteEffectStore(db_path)
    context = multiprocessing.get_context("spawn")
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    children = [
        context.Process(
            target=_allocate_child,
            args=(str(db_path), str(path), str(task), ready, start, results),
        )
        for _ in range(4)
    ]
    try:
        for child in children:
            child.start()
        for _ in children:
            assert ready.get(timeout=20) is True
        start.set()
        ranges = [results.get(timeout=20) for _ in children]
        assert all(isinstance(values, list) for values in ranges), ranges
        assert sorted(number for values in ranges for number in values) == list(range(91, 159))
        for child in children:
            child.join(10)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
            child.join(5)
        ready.close()
        results.close()
    assert SQLiteEffectStore(db_path).reserve_native_step_ids(task_id=str(task), count=1) == [159]


@pytest.mark.parametrize("persistent", [False, True])
def test_independent_indexes_in_threads_never_reuse_ids(tmp_path, persistent):
    task = TaskId(uuid4())
    path = tmp_path / "events.jsonl"
    JSONLJournal(path).write_step(task, ArmId("native"), _step(10))
    gate = threading.Barrier(8)

    def allocate(_):
        store = SQLiteEffectStore(tmp_path / "effects.db") if persistent else None
        index = ToolEffectReceiptIndex(JSONLJournal(path), store=store)
        gate.wait(10)
        return index.reserve_native_step_ids(task, 4)

    with ThreadPoolExecutor(max_workers=8) as pool:
        ranges = list(pool.map(allocate, range(8)))
    assert sorted(value for group in ranges for value in group) == list(range(11, 43))


@pytest.mark.parametrize("count", [-1, 1.0, True, "1", None])
def test_reservation_rejects_invalid_count_without_consuming(tmp_path, count):
    task = TaskId(uuid4())
    store = SQLiteEffectStore(tmp_path / "effects.db")
    index = ToolEffectReceiptIndex(InMemoryJournal(), store=store)
    with pytest.raises(ValueError):
        index.reserve_native_step_ids(task, count)
    assert index.reserve_native_step_ids(task, 0) == []
    assert index.reserve_native_step_ids(task, 1) == [1]


def test_configured_legacy_store_never_silently_falls_back_to_memory():
    index = ToolEffectReceiptIndex(InMemoryJournal(), store=object())
    task = TaskId(uuid4())
    for action in (
        lambda: index.reserve_native_step_ids(task, 1),
        lambda: index.assert_native_task_recoverable(task),
    ):
        with pytest.raises(NativeEffectRecoveryRequired) as error:
            action()
        assert error.value.reason == "native_effect_sequence_unavailable"


@pytest.mark.parametrize("expired", [False, True])
def test_active_or_expired_started_side_effect_blocks_fresh_native_loop(tmp_path, expired):
    task, step = TaskId(uuid4()), _step(7)
    store, journal = SQLiteEffectStore(tmp_path / "effects.db"), InMemoryJournal()
    event, _ = _started(store, journal, task, step)
    if expired:
        with sqlite3.connect(store.path) as connection:
            connection.execute("UPDATE tool_effect_receipts SET lease_expires_at=0")
    # Even without the original JSONL, the receipt plane keeps the task blocked.
    index = ToolEffectReceiptIndex(InMemoryJournal(), store=store)
    assert index.reserve_native_step_ids(task, 1) == [8]
    with pytest.raises(NativeEffectRecoveryRequired) as error:
        index.assert_native_task_recoverable(task)
    assert error.value.effect_key == event.effect_key
    assert error.value.state == "started"
    assert error.value.reason == (
        "native_effect_recovery_required" if expired else "native_effect_inflight"
    )
    index.assert_native_task_recoverable(TaskId(uuid4()))


def test_safe_read_started_and_unstarted_write_claim_do_not_imply_effect(tmp_path):
    task = TaskId(uuid4())
    store, journal = SQLiteEffectStore(tmp_path / "effects.db"), InMemoryJournal()
    _started(store, journal, task, _step(3), side_effecting=False)
    _claim(store, task, _step(4), side_effecting=True)
    ToolEffectReceiptIndex(journal, store=store).assert_native_task_recoverable(task)


@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("success", [False, True])
def test_legacy_sealed_unknown_without_intent_is_not_replayed_or_bypassed(
    tmp_path, persistent, success
):
    task, step = TaskId(uuid4()), _step(9, uncertain=True, success=success)
    path = tmp_path / "events.jsonl"
    JSONLJournal(path).write_step(task, ArmId("native"), step)
    store = SQLiteEffectStore(tmp_path / "effects.db") if persistent else None
    index = ToolEffectReceiptIndex(JSONLJournal(path), store=store)
    with pytest.raises(NativeEffectRecoveryRequired):
        index.assert_native_task_recoverable(task)
    result = index.begin(
        task_id=task,
        step_id=9,
        sucker_id=step.action.sucker_id,
        args=step.action.args,
        side_effecting=False,
    )
    assert result.kind == "indeterminate"
    assert index.reserve_native_step_ids(task, 1) == [10]
    with pytest.raises(NativeEffectRecoveryRequired):
        index.assert_native_task_recoverable(task)


def test_native_recovery_rejects_an_unreadable_jsonl_tail(tmp_path):
    task, step = TaskId(uuid4()), _step(22)
    path = tmp_path / "events.jsonl"
    journal = JSONLJournal(path)
    journal.write_step(task, ArmId("native"), step)
    with path.open("a", encoding="utf-8") as stream:
        stream.write('{"event_type":"step","broken":\n')

    index = ToolEffectReceiptIndex(JSONLJournal(path))
    with pytest.raises(NativeEffectRecoveryRequired) as error:
        index.assert_native_task_recoverable(task)
    assert error.value.state == "unavailable"
    assert error.value.reason == "native_effect_recovery_required"


@pytest.mark.parametrize("persistent", [False, True])
def test_success_with_sealed_unknown_never_finishes_as_committed(tmp_path, persistent):
    task, step = TaskId(uuid4()), _step(1, uncertain=True)
    journal = JSONLJournal(tmp_path / "events.jsonl")
    store = SQLiteEffectStore(tmp_path / "effects.db") if persistent else None
    index = ToolEffectReceiptIndex(journal, store=store)
    result = index.begin(
        task_id=task,
        step_id=1,
        sucker_id=step.action.sucker_id,
        args=step.action.args,
        side_effecting=False,
    )
    index.mark_intent(_intent(journal, task, step, side_effecting=False), result)
    journal.write_step(task, ArmId("native"), step)
    index.finish(result, step)
    for current in (index, ToolEffectReceiptIndex(JSONLJournal(journal._path), store=store)):
        with pytest.raises(NativeEffectRecoveryRequired):
            current.assert_native_task_recoverable(task)
        replay = current.begin(
            task_id=task,
            step_id=1,
            sucker_id=step.action.sucker_id,
            args=step.action.args,
            side_effecting=False,
        )
        assert replay.kind == "indeterminate"
    if store is not None:
        receipt = store.list_receipts()[0]
        assert receipt.state == "indeterminate"
        assert receipt.side_effecting is True


def test_expired_started_receipt_with_confirmed_journal_step_repairs(tmp_path):
    task, step = TaskId(uuid4()), _step(12)
    journal = JSONLJournal(tmp_path / "events.jsonl")
    store = SQLiteEffectStore(tmp_path / "effects.db")
    _started(store, journal, task, step)
    journal.write_step(task, ArmId("native"), step)
    index = ToolEffectReceiptIndex(journal, store=store)
    with pytest.raises(NativeEffectRecoveryRequired) as error:
        index.assert_native_task_recoverable(task)
    assert error.value.reason == "native_effect_inflight"
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE tool_effect_receipts SET lease_expires_at=0")
    index.assert_native_task_recoverable(task)
    assert store.list_receipts()[0].state == "committed"


def test_manual_retry_authorization_remains_authoritative_over_old_journal(tmp_path):
    task, step = TaskId(uuid4()), _step(13, uncertain=True)
    journal = JSONLJournal(tmp_path / "events.jsonl")
    store = SQLiteEffectStore(tmp_path / "effects.db")
    event, claim = _started(store, journal, task, step)
    journal.write_step(task, ArmId("native"), step)
    store.finish_failed(
        effect_key=event.effect_key,
        holder_id="owner",
        fencing_token=claim.fencing_token,
        side_effecting=True,
        reason="unknown",
    )
    index = ToolEffectReceiptIndex(journal, store=store)
    with pytest.raises(NativeEffectRecoveryRequired):
        index.assert_native_task_recoverable(task)
    assert store.authorize_retry(
        effect_key=event.effect_key,
        expected_fencing_token=claim.fencing_token,
        actor="operator",
        reason="synthetic operator decision",
    )
    index.assert_native_task_recoverable(task)
    retry = index.begin(
        task_id=task,
        step_id=13,
        sucker_id=step.action.sucker_id,
        args=step.action.args,
        side_effecting=True,
    )
    assert retry.kind == "execute"
    index.abandon(retry)


@pytest.mark.parametrize("mode", ["commit", "repair", "legacy_corrupt_state"])
def test_store_itself_never_replays_sealed_unknown_success(tmp_path, mode):
    task, step = TaskId(uuid4()), _step(17, uncertain=True)
    store = SQLiteEffectStore(tmp_path / "effects.db")
    claim = _claim(store, task, step, side_effecting=False)
    key = effect_key(task, step.step_id, step.action.sucker_id, step.action.args)
    if mode == "repair":
        with sqlite3.connect(store.path) as connection:
            connection.execute("UPDATE tool_effect_receipts SET lease_expires_at=0")
        store.record_committed(effect_key=key, step=step)
    else:
        assert store.commit(
            effect_key=key,
            holder_id="owner",
            fencing_token=claim.fencing_token,
            step=step,
        )
    if mode == "legacy_corrupt_state":
        with sqlite3.connect(store.path) as connection:
            connection.execute("UPDATE tool_effect_receipts SET state='committed',side_effecting=0")
    with pytest.raises(NativeEffectRecoveryRequired):
        store.assert_native_task_recoverable(task_id=str(task))
    assert _claim(store, task, step, side_effecting=False).kind == "indeterminate"
    row = store.list_receipts()[0]
    assert row.state == "indeterminate"
    assert row.side_effecting is True
