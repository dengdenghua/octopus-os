"""Durable exactly-once persistence for native tool-loop trajectories."""

from __future__ import annotations

import json
import multiprocessing
import os
import stat
import threading
from contextlib import contextmanager
from pathlib import Path
from queue import Empty
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from runtime.memory.journal import (
    InMemoryJournal,
    JournalTransactionError,
    JSONLJournal,
    StepEvent,
    TrajectoryConflictError,
    TrajectoryEvent,
    journal_context,
)
from runtime.platform.models import (
    ArmId,
    ExecutionResult,
    ParsedIntent,
    SkillId,
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.platform.observability.redactor import Redactor as PIIRedactor
from runtime.sensing.gateway._tool_bridge_scoring import _persist_native_trajectory_safe
from runtime.sensing.gateway.streaming_journal import StreamingJournal


def _event(
    task_id: UUID,
    *,
    strategy_id: str = "native_tool_loop",
    tenant_id: str | None = "tenant-a",
    owner_actor_id: str | None = "owner-a",
    success: bool = True,
    disposition: str = "completed",
    agent_id: str | None = None,
) -> TrajectoryEvent:
    typed_task_id = TaskId(task_id)
    arm_id = ArmId("agentic")
    trajectory = Trajectory(
        task_id=typed_task_id,
        arm_id=arm_id,
        strategy_id=strategy_id,
        outcome=TrajectoryOutcome(success=success, disposition=disposition),
    )
    return TrajectoryEvent(
        task_id=typed_task_id,
        arm_id=arm_id,
        tenant_id=tenant_id,
        owner_actor_id=owner_actor_id,
        agent_id=agent_id,
        trajectory=trajectory,
    )


def _persist_worker(
    journal_path: str,
    raw_task_id: str,
    barrier: Any,
    result_queue: Any,
) -> None:
    """Race one independent worker through the production scoring helper."""

    try:
        task_id = TaskId(UUID(raw_task_id))
        journal = JSONLJournal(journal_path)
        stack = SimpleNamespace(journal=journal)
        agent = SimpleNamespace(agent_id="coder")
        intent = ParsedIntent(
            raw="run one native tool",
            intent_type="task",
            normalized_goal="run one native tool",
            user_context={},
        )
        barrier.wait(timeout=30)
        inserted = _persist_native_trajectory_safe(
            stack=stack,
            agent=agent,
            intent=intent,
            task_id=task_id,
            success=True,
            disposition="completed",
        )
        result_queue.put(("ok", inserted))
    except BaseException as exc:  # pragma: no cover - parent reports worker failures
        result_queue.put(("error", repr(exc)))


def _successful_step(task_id: TaskId) -> StepEvent:
    action = ToolCall(caller="agentic", sucker_id=SkillId("noop"), args={})
    return StepEvent(
        task_id=task_id,
        arm_id=ArmId("agentic"),
        actor="owner-a",
        tenant_id="tenant-a",
        owner_actor_id="owner-a",
        agent_id="coder",
        conversation_id="thread-a",
        step=Step(
            step_id=0,
            node_id="agentic:call-0",
            action=action,
            result=ExecutionResult(call_id=action.call_id, status="success", output="ok"),
        ),
    )


def test_in_memory_trajectory_append_is_atomic_and_scope_aware() -> None:
    journal = InMemoryJournal()
    task_id = uuid4()

    assert journal.write_trajectory_once(_event(task_id)) is True
    assert journal.write_trajectory_once(_event(task_id)) is False
    assert journal.write_trajectory_once(_event(task_id, strategy_id="other")) is True
    assert journal.write_trajectory_once(_event(task_id, owner_actor_id="owner-b")) is True
    assert journal.write_trajectory_once(_event(task_id, tenant_id="tenant-b")) is True
    assert len(journal.read_by_type("trajectory")) == 4


def test_streaming_journal_broadcasts_only_the_committed_winner(tmp_path: Path) -> None:
    journal = StreamingJournal(JSONLJournal(tmp_path / "events.jsonl"))
    received: list[TrajectoryEvent] = []
    journal.subscribe(lambda event: received.append(event))
    task_id = uuid4()

    assert journal.write_trajectory_once(_event(task_id)) is True
    assert journal.write_trajectory_once(_event(task_id)) is False

    assert len(received) == 1
    assert len(journal.read_by_type("trajectory")) == 1


def test_jsonl_atomic_append_does_not_reenter_sidecar_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = JSONLJournal(tmp_path / "events.jsonl")
    original_lock = journal._interprocess_lock
    depth = 0

    @contextmanager
    def checked_lock(*, required: bool = False):
        nonlocal depth
        depth += 1
        assert depth == 1, "atomic append attempted a nested sidecar flock"
        try:
            with original_lock(required=required) as acquired:
                yield acquired
        finally:
            depth -= 1

    monkeypatch.setattr(journal, "_interprocess_lock", checked_lock)

    assert journal.write_trajectory_once(_event(uuid4())) is True
    assert depth == 0


def test_jsonl_atomic_append_fails_closed_when_lock_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = JSONLJournal(tmp_path / "events.jsonl")

    @contextmanager
    def unavailable_lock(*, required: bool = False):
        assert required is True
        raise JournalTransactionError("lock unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(journal, "_interprocess_lock", unavailable_lock)

    with pytest.raises(JournalTransactionError, match="lock unavailable"):
        journal.write_trajectory_once(_event(uuid4()))
    assert journal.read_by_type("trajectory") == []


def test_atomic_data_fsync_failure_never_commits_or_returns_true(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "events.jsonl"
    event = _event(uuid4())
    journal = JSONLJournal(path)
    real_fsync = os.fsync
    regular_file_calls = 0

    def fail_journal_data_fsync(fd: int) -> None:
        nonlocal regular_file_calls
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            real_fsync(fd)
            return
        regular_file_calls += 1
        if regular_file_calls == 2:  # reservation ledger succeeds; journal data fails
            raise OSError("simulated journal fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_journal_data_fsync)

    with pytest.raises(JournalTransactionError, match="journal data"):
        journal.write_trajectory_once(event)

    ledger_path = path.with_suffix(path.suffix + ".trajectory-dedupe.jsonl")
    records = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert [record["state"] for record in records] == ["reserved"]

    # The append may be visible in the page cache even though durability was
    # not established. A later retry must fsync it before committing and must
    # not append a duplicate.
    monkeypatch.setattr(os, "fsync", real_fsync)
    recovered = JSONLJournal(path)
    assert recovered.write_trajectory_once(event) is False
    assert len(recovered.read_by_type("trajectory")) == 1


@pytest.mark.skipif(os.name != "posix", reason="directory fsync is a POSIX guarantee")
def test_reservation_directory_fsync_failure_fails_before_journal_and_power_loss_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "events.jsonl"
    event = _event(uuid4())
    journal = JSONLJournal(path)
    real_fsync = os.fsync

    def fail_first_directory_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("simulated reservation directory fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_first_directory_fsync)

    with pytest.raises(JournalTransactionError, match="trajectory dedupe ledger directory"):
        journal.write_trajectory_once(event)

    ledger_path = path.with_suffix(path.suffix + ".trajectory-dedupe.jsonl")
    assert ledger_path.exists()
    assert not path.exists(), "journal append must wait for the reservation directory barrier"

    # Emulate power loss discarding the uncommitted ledger directory entry.
    ledger_path.unlink()
    monkeypatch.setattr(os, "fsync", real_fsync)
    recovered = JSONLJournal(path)
    assert recovered.write_trajectory_once(event) is True
    assert len(recovered.read_by_type("trajectory")) == 1


@pytest.mark.skipif(os.name != "posix", reason="directory fsync is a POSIX guarantee")
def test_journal_directory_fsync_failure_never_commits_and_power_loss_retry_is_unique(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "events.jsonl"
    event = _event(uuid4())
    journal = JSONLJournal(path)
    real_fsync = os.fsync
    directory_calls = 0

    def fail_journal_directory_fsync(fd: int) -> None:
        nonlocal directory_calls
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directory_calls += 1
            if directory_calls == 2:
                raise OSError("simulated journal directory fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_journal_directory_fsync)

    with pytest.raises(JournalTransactionError, match="journal directory"):
        journal.write_trajectory_once(event)

    ledger_path = path.with_suffix(path.suffix + ".trajectory-dedupe.jsonl")
    records = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert [record["state"] for record in records] == ["reserved"]
    assert path.exists()

    # Emulate loss of the journal's uncommitted directory entry. The durable
    # reservation permits exactly one reconstruction and cannot be mistaken
    # for a committed sample.
    path.unlink()
    monkeypatch.setattr(os, "fsync", real_fsync)
    recovered = JSONLJournal(path)
    assert recovered.write_trajectory_once(event) is True
    assert len(recovered.read_by_type("trajectory")) == 1


@pytest.mark.skipif(os.name != "posix", reason="directory fsync is a POSIX guarantee")
def test_rotation_directory_fsync_failure_is_not_acknowledged_and_retry_is_unique(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "events.jsonl"
    event = _event(uuid4(), agent_id="agent-" + "x" * 512)
    journal = JSONLJournal(path, max_size_bytes=128, keep_ratio=0.5)
    real_fsync = os.fsync
    directory_calls = 0

    def fail_rotation_directory_fsync(fd: int) -> None:
        nonlocal directory_calls
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directory_calls += 1
            if directory_calls == 3:
                raise OSError("simulated rotation directory fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_rotation_directory_fsync)

    with pytest.raises(JournalTransactionError, match="journal rotation directory"):
        journal.write_trajectory_once(event)

    ledger_path = path.with_suffix(path.suffix + ".trajectory-dedupe.jsonl")
    records = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert [record["state"] for record in records] == ["reserved"]

    monkeypatch.setattr(os, "fsync", real_fsync)
    recovered = JSONLJournal(path, max_size_bytes=128, keep_ratio=0.5)
    assert recovered.write_trajectory_once(event) is False
    assert len(recovered.read_by_type("trajectory")) == 1


def test_ordinary_telemetry_keeps_best_effort_fsync_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "events.jsonl"
    journal = JSONLJournal(path)
    step = _successful_step(TaskId(uuid4()))

    monkeypatch.setattr(os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("no fsync")))

    journal.write(step)

    assert JSONLJournal(path).read_by_type("step")[0].event_id == step.event_id


def test_jsonl_atomic_append_fails_closed_on_crash_truncated_row(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    truncated = b'{"event_type":"trajectory"'
    path.write_bytes(truncated)
    journal = JSONLJournal(path)

    with pytest.raises(JournalTransactionError, match="incomplete trailing row"):
        journal.write_trajectory_once(_event(uuid4()))

    assert path.read_bytes() == truncated


def test_jsonl_atomic_append_never_exposes_serialized_scope_to_redactor(
    tmp_path: Path,
) -> None:
    class ScopeChangingRedactor:
        def redact(self, value: str) -> str:
            return value.replace('"tenant_id":"tenant-a"', '"tenant_id":"tenant-redacted"')

    path = tmp_path / "events.jsonl"
    journal = JSONLJournal(path, redactor=ScopeChangingRedactor())

    assert journal.write_trajectory_once(_event(uuid4())) is True

    stored = journal.read_by_type("trajectory")
    assert len(stored) == 1
    assert stored[0].tenant_id == "tenant-a"
    assert '"tenant_id":"tenant-a"' in path.read_text(encoding="utf-8")


def test_jsonl_atomic_trajectory_uses_pseudonymized_pii_scope_as_dedupe_key(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    journal = JSONLJournal(path, redactor=PIIRedactor())
    task_id = uuid4()
    alice = _event(
        task_id,
        tenant_id="legacy:oct:alice@example.com",
        owner_actor_id="oct:alice@example.com",
    )
    bob = _event(
        task_id,
        tenant_id="legacy:oct:bob@example.com",
        owner_actor_id="oct:bob@example.com",
    )

    assert journal.write_trajectory_once(alice) is True
    assert journal.write_trajectory_once(alice) is False
    assert journal.write_trajectory_once(bob) is True

    stored = journal.read_by_type("trajectory")
    assert len(stored) == 2
    assert all(event.tenant_id.startswith("echo-scope-tenant-vone-") for event in stored)
    assert all(event.owner_actor_id.startswith("echo-scope-owner-vone-") for event in stored)
    assert stored[0].tenant_id != stored[1].tenant_id
    assert "alice@example.com" not in path.read_text(encoding="utf-8")
    assert "bob@example.com" not in path.read_text(encoding="utf-8")


def test_jsonl_atomic_append_refreshes_an_existing_read_cache(tmp_path: Path) -> None:
    journal = JSONLJournal(tmp_path / "events.jsonl")
    assert journal.read_all() == []

    event = _event(uuid4())
    assert journal.write_trajectory_once(event) is True

    assert [stored.event_id for stored in journal.read_all()] == [event.event_id]


def test_jsonl_atomic_append_recovers_crash_after_event_fsync_without_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "events.jsonl"
    task_id = uuid4()
    journal = JSONLJournal(path)
    original_append = journal._append_trajectory_dedupe_record_locked

    def crash_before_commit(
        digest: str,
        state: str,
        payload_digest: str | None = None,
    ) -> None:
        if state == "committed":
            raise JournalTransactionError("simulated worker crash before commit")
        original_append(digest, state, payload_digest)

    monkeypatch.setattr(
        journal,
        "_append_trajectory_dedupe_record_locked",
        crash_before_commit,
    )

    with pytest.raises(JournalTransactionError, match="simulated worker crash"):
        journal.write_trajectory_once(_event(task_id))

    recovered = JSONLJournal(path)
    assert recovered.write_trajectory_once(_event(task_id)) is False
    assert len(recovered.read_by_type("trajectory")) == 1


def test_reserved_payload_retries_after_crash_before_event_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "events.jsonl"
    task_id = uuid4()
    event = _event(task_id)
    journal = JSONLJournal(path)

    def crash_before_event(
        _line: str,
        *,
        require_durability: bool = False,
    ) -> None:
        assert require_durability is True
        raise JournalTransactionError("simulated crash before event append")

    monkeypatch.setattr(
        journal,
        "_append_raw_with_interprocess_lock_locked",
        crash_before_event,
    )

    with pytest.raises(JournalTransactionError, match="before event append"):
        journal.write_trajectory_once(event)
    assert not path.exists()

    # Reconstructing the same semantic event produces new envelope IDs and
    # timestamps, but the canonical payload digest intentionally excludes
    # those worker-local values.
    recovered = JSONLJournal(path)
    assert recovered.write_trajectory_once(_event(task_id)) is True
    assert len(recovered.read_by_type("trajectory")) == 1


@pytest.mark.parametrize(
    ("failure_type", "skill_name"),
    [
        ("budget_exceeded", "native_model_response"),
        ("skill_not_found", "missing_skill"),
    ],
)
def test_synthetic_reserved_payload_is_stable_across_worker_reconstruction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_type: str,
    skill_name: str,
) -> None:
    path = tmp_path / f"{failure_type}.jsonl"
    task_id = TaskId(uuid4())
    journal = JSONLJournal(path)

    def crash_before_event(
        _line: str,
        *,
        require_durability: bool = False,
    ) -> None:
        assert require_durability is True
        raise JournalTransactionError("simulated crash before synthetic event append")

    monkeypatch.setattr(
        journal,
        "_append_raw_with_interprocess_lock_locked",
        crash_before_event,
    )
    intent = ParsedIntent(
        raw="one synthetic native failure",
        intent_type="task",
        normalized_goal="one synthetic native failure",
        user_context={},
    )

    def persist(
        target: JSONLJournal,
        *,
        args: dict[str, int],
        prepared: dict[str, Any] | None = None,
    ) -> bool:
        stack = SimpleNamespace(
            journal=target,
            executor=SimpleNamespace(journal=target),
        )
        return _persist_native_trajectory_safe(
            stack=stack,
            agent=SimpleNamespace(agent_id="coder"),
            intent=intent,
            task_id=task_id,
            success=False,
            disposition="failed",
            step_failures={0: failure_type},
            step_attempts={
                0: SimpleNamespace(
                    id="provider-attempt-0",
                    name=skill_name,
                    input=args,
                )
            },
            _prepared_event_cache=prepared,
        )

    assert persist(journal, args={"round": 1}) is False
    assert not path.exists()

    # A fresh worker reconstructs new local Step/ToolCall UUIDs and timestamps.
    # Those volatile receipt fields must not turn the same semantic attempt into
    # a false conflict against the durable reservation.
    recovered = JSONLJournal(path)
    assert persist(recovered, args={"round": 1}) is True
    stored = recovered.read_by_type("trajectory")
    assert len(stored) == 1
    assert stored[0].trajectory.steps[0].result.error_type == failure_type

    # Semantic changes remain payload conflicts even though volatile synthetic
    # receipt identity is normalised for crash reconstruction.
    conflicting: dict[str, Any] = {}
    assert persist(recovered, args={"round": 2}, prepared=conflicting) is False
    with pytest.raises(TrajectoryConflictError, match="conflicting payload"):
        recovered.write_trajectory_once(conflicting["event"])


def test_reserved_key_rejects_a_conflicting_terminal_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "events.jsonl"
    task_id = uuid4()
    journal = JSONLJournal(path)

    def crash_before_event(
        _line: str,
        *,
        require_durability: bool = False,
    ) -> None:
        assert require_durability is True
        raise JournalTransactionError("simulated crash before event append")

    monkeypatch.setattr(
        journal,
        "_append_raw_with_interprocess_lock_locked",
        crash_before_event,
    )
    with pytest.raises(JournalTransactionError):
        journal.write_trajectory_once(_event(task_id, success=True))

    recovered = JSONLJournal(path)
    with pytest.raises(TrajectoryConflictError, match="conflicting payload"):
        recovered.write_trajectory_once(_event(task_id, success=False, disposition="failed"))
    assert not path.exists()


def test_committed_key_rejects_a_conflicting_terminal_payload(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    task_id = uuid4()
    journal = JSONLJournal(path)
    assert journal.write_trajectory_once(_event(task_id, success=True)) is True

    with pytest.raises(TrajectoryConflictError, match="conflicting payload"):
        journal.write_trajectory_once(_event(task_id, success=False, disposition="failed"))

    stored = journal.read_by_type("trajectory")
    assert len(stored) == 1
    assert stored[0].trajectory.outcome.success is True


def test_v1_committed_ledger_upgrades_before_original_event_rotates(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    task_id = uuid4()
    event = _event(task_id)
    journal = JSONLJournal(path)
    assert journal.write_trajectory_once(event) is True

    ledger_path = path.with_suffix(path.suffix + ".trajectory-dedupe.jsonl")
    original_record = json.loads(ledger_path.read_text().splitlines()[0])
    ledger_path.write_text(
        json.dumps(
            {
                "version": 1,
                "key": original_record["key"],
                "state": "committed",
            },
            separators=(",", ":"),
        )
        + "\n"
    )

    assert journal.write_trajectory_once(_event(task_id)) is False
    upgraded = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert upgraded[-1]["version"] == 2
    assert upgraded[-1]["state"] == "committed"
    assert isinstance(upgraded[-1]["payload"], str)

    bounded = JSONLJournal(path, max_size_bytes=1_000, keep_ratio=0.5)
    for index in range(30):
        bounded.write(_event(uuid4(), strategy_id=f"filler-{index}"))
    assert all(stored.task_id != TaskId(task_id) for stored in bounded.read_by_type("trajectory"))

    assert bounded.write_trajectory_once(_event(task_id)) is False


def test_atomic_rotation_preserves_the_oversized_trajectory_that_triggered_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    task_id = uuid4()
    # A trajectory JSON row is much larger than this cap. Rotation must keep
    # the newest complete row instead of trimming from its middle to EOF.
    journal = JSONLJournal(path, max_size_bytes=128, keep_ratio=0.5)

    assert journal.write_trajectory_once(_event(task_id, agent_id="agent-" + "x" * 512))

    stored = journal.read_by_type("trajectory")
    assert len(stored) == 1
    assert stored[0].task_id == TaskId(task_id)
    assert path.stat().st_size > 128


def test_streaming_broadcast_matches_durable_redacted_scoped_event(
    tmp_path: Path,
) -> None:
    class Redactor:
        def redact(self, value: str) -> str:
            return value.replace("secret-agent", "redacted-agent")

    inner = JSONLJournal(tmp_path / "events.jsonl", redactor=Redactor())
    journal = StreamingJournal(inner)
    received: list[TrajectoryEvent] = []
    journal.subscribe(lambda event: received.append(event))
    original = _event(
        uuid4(),
        tenant_id=None,
        owner_actor_id=None,
        agent_id="secret-agent",
    )

    with journal_context(tenant_id="tenant-a", owner_actor_id="owner-a"):
        assert journal.write_trajectory_once(original) is True

    stored = inner.read_by_type("trajectory")
    assert len(received) == len(stored) == 1
    assert received[0].model_dump(mode="json") == stored[0].model_dump(mode="json")
    assert received[0].tenant_id == "tenant-a"
    assert received[0].owner_actor_id == "owner-a"
    assert received[0].agent_id == "redacted-agent"
    assert original.tenant_id is None
    assert original.agent_id == "secret-agent"


def test_streaming_subscriber_can_reenter_trajectory_persistence_without_deadlock() -> None:
    journal = StreamingJournal(InMemoryJournal())
    event = _event(uuid4())
    callback_results: list[bool] = []

    def reenter(committed: TrajectoryEvent) -> None:
        callback_results.append(journal.write_trajectory_once(committed))

    journal.subscribe(reenter)
    result: list[bool] = []
    worker = threading.Thread(
        target=lambda: result.append(journal.write_trajectory_once(event)),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=2)

    assert not worker.is_alive(), "subscriber re-entry deadlocked persistence"
    assert result == [True]
    assert callback_results == [False]
    assert len(journal.read_by_type("trajectory")) == 1


def test_streaming_supports_legacy_atomic_duck_without_combined_hook() -> None:
    class LegacyAtomicJournal:
        def __init__(self) -> None:
            self.events: list[TrajectoryEvent] = []
            self.keys: set[tuple[str, str, str, str]] = set()

        def write_trajectory_once(self, event: TrajectoryEvent) -> bool:
            key = (
                str(event.task_id),
                str(event.trajectory.strategy_id),
                str(event.tenant_id or ""),
                str(event.owner_actor_id or ""),
            )
            if key in self.keys:
                return False
            self.keys.add(key)
            self.events.append(event)
            return True

    inner = LegacyAtomicJournal()
    journal = StreamingJournal(inner)  # type: ignore[arg-type]
    received: list[TrajectoryEvent] = []
    journal.subscribe(received.append)
    original = _event(uuid4(), tenant_id=None, owner_actor_id=None)

    with journal_context(tenant_id="tenant-a", owner_actor_id="owner-a"):
        assert journal.write_trajectory_once(original) is True
        assert journal.write_trajectory_once(original) is False

    assert len(inner.events) == len(received) == 1
    assert received[0].model_dump(mode="json") == inner.events[0].model_dump(mode="json")
    assert received[0].tenant_id == "tenant-a"
    assert received[0].owner_actor_id == "owner-a"


def test_jsonl_dedupe_survives_rotation_of_original_trajectory(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    task_id = uuid4()
    journal = JSONLJournal(path, max_size_bytes=1_000, keep_ratio=0.5)
    event = _event(task_id)
    assert journal.write_trajectory_once(event) is True

    # Force subsequent journal rotations until the original row is no longer
    # in the bounded JSONL. The separate hashed ledger is intentionally not
    # rotated and remains the authoritative idempotency record.
    for index in range(30):
        other = _event(uuid4(), strategy_id=f"filler-{index}")
        journal.write(other)
    assert all(stored.event_id != event.event_id for stored in journal.read_all())

    assert journal.write_trajectory_once(_event(task_id)) is False


def test_two_processes_finalize_one_native_trajectory(tmp_path: Path) -> None:
    """Two separate process-local locks still yield one durable terminal row."""

    journal_path = tmp_path / "events.jsonl"
    task_id = TaskId(uuid4())
    JSONLJournal(journal_path).write(_successful_step(task_id))

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    result_queue = context.Queue()
    workers = [
        context.Process(
            target=_persist_worker,
            args=(str(journal_path), str(task_id), barrier, result_queue),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=45)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=5)
            pytest.fail("native trajectory race worker deadlocked")
        assert worker.exitcode == 0

    try:
        results = [result_queue.get(timeout=5) for _ in workers]
    except Empty:
        pytest.fail("native trajectory race worker returned no result")

    assert all(status == "ok" for status, _value in results), results
    assert sorted(value for _status, value in results) == [False, True]
    trajectories = JSONLJournal(journal_path).read_by_type("trajectory")
    assert len(trajectories) == 1
    assert trajectories[0].task_id == task_id
    assert trajectories[0].trajectory.strategy_id == "native_tool_loop"
    assert trajectories[0].tenant_id == "tenant-a"
    assert trajectories[0].owner_actor_id == "owner-a"

