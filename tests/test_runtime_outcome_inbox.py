from __future__ import annotations

import json
import multiprocessing
import os
import time
from pathlib import Path

import pytest

from runtime.platform.io import TransactionalFileError
from runtime.platform.process.session import Session, session_scope
from runtime.safety.evolution.candidate_canary import CandidateCanaryManager
from runtime.safety.evolution.candidate_registry import CandidateRegistry, CandidateStatus
from runtime.safety.evolution.runtime_deployment import CandidateRuntimeSelector
from runtime.safety.evolution.runtime_outcomes import (
    RuntimeOutcomeConflictError,
    RuntimeOutcomePersistenceError,
    active_runtime_candidates,
    record_runtime_candidate_activation,
    settle_runtime_candidate_outcomes,
)


def _record_worker(inbox_path: str, turn_id: str, candidate_id: str) -> None:
    if not record_runtime_candidate_activation(
        candidate_id,
        turn_id=turn_id,
        inbox_path=inbox_path,
    ):
        raise RuntimeError("activation was not persisted")


def _settle_worker(
    inbox_path: str,
    registry_path: str,
    state_dir: str,
    turn_id: str,
) -> None:
    settle_runtime_candidate_outcomes(
        turn_id,
        success=True,
        registry=CandidateRegistry(registry_path),
        state_dir=state_dir,
        inbox_path=inbox_path,
    )


def _registered_candidate(tmp_path: Path) -> tuple[CandidateRegistry, Path, str]:
    registry = CandidateRegistry(tmp_path / "evolution_candidates.jsonl")
    candidate = registry.propose(
        gene_type="prompt",
        scope="planner.prompt:durable-inbox",
        patch={"op": "replace", "value": "Use the durable verifier."},
        proposer="test",
    )
    registry.transition(
        candidate.candidate_id,
        CandidateStatus.VALIDATED,
        hard_gate_results={"correctness": True, "safety": True},
    )
    registry.transition(candidate.candidate_id, CandidateStatus.SHADOW)
    state_dir = tmp_path / "candidate_canary_states"
    CandidateCanaryManager(registry, state_dir).register(candidate.candidate_id)
    return registry, state_dir, candidate.candidate_id


def _sample_count(registry: CandidateRegistry, state_dir: Path, candidate_id: str) -> int:
    wire = CandidateCanaryManager(registry, state_dir).status(candidate_id)
    return int(wire["canary"]["sample_count"])


def test_activation_survives_process_exit_before_first_settlement(tmp_path: Path) -> None:
    registry, state_dir, candidate_id = _registered_candidate(tmp_path)
    inbox = tmp_path / "candidate_runtime_outcomes.json"
    turn_id = "tenant-secret/turn-secret-before-settlement"
    ctx = multiprocessing.get_context("spawn")
    process = ctx.Process(
        target=_record_worker,
        args=(str(inbox), turn_id, candidate_id),
    )
    process.start()
    process.join(timeout=20)

    assert process.exitcode == 0
    assert active_runtime_candidates(turn_id, inbox_path=inbox) == (candidate_id,)
    results = settle_runtime_candidate_outcomes(
        turn_id,
        success=True,
        registry=registry,
        state_dir=state_dir,
        inbox_path=inbox,
    )
    assert results[0]["recorded"] is True
    assert _sample_count(registry, state_dir, candidate_id) == 1
    assert active_runtime_candidates(turn_id, inbox_path=inbox) == ()

    payload = inbox.read_text(encoding="utf-8")
    assert turn_id not in payload
    assert all("tenant-secret" not in path.name for path in tmp_path.iterdir())
    if os.name == "posix":
        assert inbox.stat().st_mode & 0o777 == 0o600


def test_multiprocess_activation_merge_and_settlement_are_exactly_once(
    tmp_path: Path,
) -> None:
    registry, state_dir, candidate_id = _registered_candidate(tmp_path)
    inbox = tmp_path / "candidate_runtime_outcomes.json"
    turn_id = "multiprocess-turn"
    ctx = multiprocessing.get_context("spawn")

    writers = [
        ctx.Process(
            target=_record_worker,
            args=(str(inbox), turn_id, candidate_id),
        )
        for _ in range(5)
    ]
    for process in writers:
        process.start()
    for process in writers:
        process.join(timeout=20)
    assert all(process.exitcode == 0 for process in writers)
    assert active_runtime_candidates(turn_id, inbox_path=inbox) == (candidate_id,)

    settlers = [
        ctx.Process(
            target=_settle_worker,
            args=(str(inbox), str(registry.path), str(state_dir), turn_id),
        )
        for _ in range(2)
    ]
    for process in settlers:
        process.start()
    for process in settlers:
        process.join(timeout=30)
    assert all(process.exitcode == 0 for process in settlers)
    assert _sample_count(registry, state_dir, candidate_id) == 1
    assert active_runtime_candidates(turn_id, inbox_path=inbox) == ()


def test_crash_after_canary_commit_before_inbox_ack_retries_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.safety.evolution import runtime_outcomes

    registry, state_dir, candidate_id = _registered_candidate(tmp_path)
    inbox = tmp_path / "candidate_runtime_outcomes.json"
    turn_id = "crash-between-receipt-and-ack"
    assert record_runtime_candidate_activation(
        candidate_id,
        turn_id=turn_id,
        inbox_path=inbox,
    )
    real_ack = runtime_outcomes._acknowledge_candidates

    def _crash_before_ack(*_args, **_kwargs) -> None:
        raise RuntimeOutcomePersistenceError("injected acknowledgement crash")

    monkeypatch.setattr(runtime_outcomes, "_acknowledge_candidates", _crash_before_ack)
    with pytest.raises(RuntimeOutcomePersistenceError, match="injected"):
        settle_runtime_candidate_outcomes(
            turn_id,
            success=True,
            registry=registry,
            state_dir=state_dir,
            inbox_path=inbox,
        )
    assert _sample_count(registry, state_dir, candidate_id) == 1
    assert active_runtime_candidates(turn_id, inbox_path=inbox) == (candidate_id,)

    monkeypatch.setattr(runtime_outcomes, "_acknowledge_candidates", real_ack)
    settle_runtime_candidate_outcomes(
        turn_id,
        success=True,
        registry=registry,
        state_dir=state_dir,
        inbox_path=inbox,
    )
    assert _sample_count(registry, state_dir, candidate_id) == 1
    assert active_runtime_candidates(turn_id, inbox_path=inbox) == ()


def test_conflicting_terminal_outcome_fails_closed(tmp_path: Path) -> None:
    registry, state_dir, candidate_id = _registered_candidate(tmp_path)
    inbox = tmp_path / "candidate_runtime_outcomes.json"
    turn_id = "conflicting-terminal-turn"
    assert record_runtime_candidate_activation(
        candidate_id,
        turn_id=turn_id,
        inbox_path=inbox,
    )
    settle_runtime_candidate_outcomes(
        turn_id,
        success=True,
        registry=registry,
        state_dir=state_dir,
        inbox_path=inbox,
    )

    with pytest.raises(RuntimeOutcomeConflictError, match="different outcome"):
        settle_runtime_candidate_outcomes(
            turn_id,
            success=False,
            registry=registry,
            state_dir=state_dir,
            inbox_path=inbox,
        )
    assert _sample_count(registry, state_dir, candidate_id) == 1


def test_corrupt_inbox_blocks_activation_and_settlement(tmp_path: Path) -> None:
    inbox = tmp_path / "candidate_runtime_outcomes.json"
    inbox.write_text('{"schema":', encoding="utf-8")

    assert not record_runtime_candidate_activation(
        "candidate-a",
        turn_id="corrupt-turn",
        inbox_path=inbox,
    )
    assert active_runtime_candidates("corrupt-turn", inbox_path=inbox) == ()
    with pytest.raises(RuntimeOutcomePersistenceError):
        settle_runtime_candidate_outcomes(
            "corrupt-turn",
            success=True,
            inbox_path=inbox,
        )


def test_selector_fails_closed_when_activation_fsync_cannot_be_proven(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.safety.evolution import runtime_outcomes

    registry, state_dir, candidate_id = _registered_candidate(tmp_path)
    inbox = tmp_path / "candidate_runtime_outcomes.json"
    selector = CandidateRuntimeSelector(
        registry,
        state_dir,
        outcome_inbox_path=inbox,
    )
    selected = next(
        key
        for key in (f"thread-{index}" for index in range(20_000))
        if selector.is_active(candidate_id, routing_key=key)
    )

    assert selector.prompt_addendum(
        "planner.prompt:durable-inbox",
        routing_key=selected,
    ) == (None, "")

    def _fail_write(*_args, **_kwargs):
        raise TransactionalFileError("injected directory fsync failure")

    monkeypatch.setattr(runtime_outcomes, "mutate_json_file", _fail_write)
    with session_scope(Session(thread_id=selected, turn_id="fsync-failure-turn")):
        assert selector.prompt_addendum(
            "planner.prompt:durable-inbox",
            routing_key=selected,
        ) == (None, "")


def test_inbox_ttl_and_hard_bound_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.safety.evolution import runtime_outcomes

    inbox = tmp_path / "candidate_runtime_outcomes.json"
    started = time.time()
    monkeypatch.setattr(runtime_outcomes, "_MAX_TRACKED_TURNS", 1)
    monkeypatch.setattr(runtime_outcomes.time, "time", lambda: started)
    assert record_runtime_candidate_activation(
        "candidate-a",
        turn_id="bounded-turn-a",
        inbox_path=inbox,
    )
    assert not record_runtime_candidate_activation(
        "candidate-b",
        turn_id="bounded-turn-b",
        inbox_path=inbox,
    )

    monkeypatch.setattr(
        runtime_outcomes.time,
        "time",
        lambda: started + runtime_outcomes._ACTIVATION_TTL_S + 1,
    )
    # A pending activation is recovery evidence, not a cache entry: age alone
    # must neither erase it nor make room for a new canary turn.
    assert active_runtime_candidates("bounded-turn-a", inbox_path=inbox) == ("candidate-a",)
    assert not record_runtime_candidate_activation(
        "candidate-b",
        turn_id="bounded-turn-b",
        inbox_path=inbox,
    )
    settle_runtime_candidate_outcomes(
        "bounded-turn-a",
        success=None,
        inbox_path=inbox,
    )

    monkeypatch.setattr(
        runtime_outcomes.time,
        "time",
        lambda: started + (2 * runtime_outcomes._ACTIVATION_TTL_S) + 2,
    )
    assert active_runtime_candidates("bounded-turn-a", inbox_path=inbox) == ()
    assert record_runtime_candidate_activation(
        "candidate-b",
        turn_id="bounded-turn-b",
        inbox_path=inbox,
    )
    persisted = json.loads(inbox.read_text(encoding="utf-8"))
    assert len(persisted["entries"]) == 1

