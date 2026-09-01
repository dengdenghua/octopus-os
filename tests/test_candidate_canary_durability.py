from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from pathlib import Path
from typing import Any

import pytest

from runtime.platform.io import TransactionalFileError
from runtime.safety.evolution.canary import (
    CanaryConfig,
    CanaryManager,
    CanaryOutcomeConflictError,
    CanaryPersistenceError,
    CanaryReceiptLimitError,
)
from runtime.safety.evolution.candidate_canary import CandidateCanaryManager
from runtime.safety.evolution.candidate_registry import (
    CandidateRegistry,
    CandidateRegistryError,
    CandidateStatus,
)
from runtime.safety.evolution.runtime_deployment import CandidateRuntimeSelector
from runtime.safety.evolution.runtime_outcomes import (
    active_runtime_candidates,
    record_runtime_candidate_activation,
    settle_runtime_candidate_outcomes,
)


def _shadow_candidate(registry: CandidateRegistry, *, suffix: str = "a") -> str:
    candidate = registry.propose(
        gene_type="prompt",
        scope=f"planner.prompt:durability-{suffix}",
        patch={"op": "replace", "value": f"Verify the sealed fixture {suffix}."},
        proposer="durability-test",
    )
    registry.transition(
        candidate.candidate_id,
        CandidateStatus.VALIDATED,
        hard_gate_results={"correctness": True, "safety": True},
    )
    registry.transition(candidate.candidate_id, CandidateStatus.SHADOW)
    return candidate.candidate_id


def _treatment_key(selector: CandidateRuntimeSelector, candidate_id: str) -> str:
    for index in range(10_000):
        key = f"durability-treatment-{index}"
        if selector.is_active(candidate_id, routing_key=key):
            return key
    raise AssertionError("failed to find deterministic treatment cohort")


def _concurrent_outcome_worker(
    registry_path: str,
    state_dir: str,
    candidate_id: str,
    barrier: Any,
    results: Any,
) -> None:
    try:
        barrier.wait(timeout=10)
        wire = CandidateCanaryManager(
            CandidateRegistry(registry_path),
            state_dir,
        ).record_outcome(
            candidate_id,
            True,
            outcome_id="turn-cross-process",
        )
        results.put(("ok", wire["canary"]["sample_count"]))
    except Exception as exc:  # pragma: no cover - asserted in parent process
        results.put(("error", f"{type(exc).__name__}: {exc}"))


def test_canary_fsync_failure_is_not_acknowledged_or_kept_in_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "canary"
    manager = CanaryManager(CanaryConfig(state_dir=str(state_dir)))

    def fail_fsync(_fd: int) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)
    with pytest.raises(CanaryPersistenceError, match="not durable"):
        manager.register("fsync_failure")

    assert manager.get_state("fsync_failure") is None
    assert not (state_dir / "fsync_failure.json").exists()


def test_canary_outcome_write_failure_leaves_last_durable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "canary"
    manager = CanaryManager(CanaryConfig(state_dir=str(state_dir)))
    manager.register("write_failure")

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise TransactionalFileError("injected write failure")

    monkeypatch.setattr("runtime.safety.evolution.canary.mutate_json_file", fail_write)
    with pytest.raises(CanaryPersistenceError, match="not durable"):
        manager.record_outcome("write_failure", True, outcome_id="turn-write-failure")

    assert manager.get_state("write_failure").sample_count == 0  # type: ignore[union-attr]
    reloaded = CanaryManager(CanaryConfig(state_dir=str(state_dir)))
    assert reloaded.get_state("write_failure").sample_count == 0  # type: ignore[union-attr]


def test_directory_fsync_failure_is_ambiguous_but_retry_recovers_committed_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "canary"
    manager = CanaryManager(CanaryConfig(state_dir=str(state_dir)))
    original_fsync = os.fsync
    calls = 0

    def fail_directory_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected directory fsync failure")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_directory_fsync)
    with pytest.raises(CanaryPersistenceError, match="not durable"):
        manager.register("ambiguous_commit")

    # The rename may already be visible even though durability was not
    # acknowledged.  A retry reads that state under the stable transaction
    # lock and converges instead of inventing a second lifecycle object.
    assert (state_dir / "ambiguous_commit.json").exists()
    monkeypatch.setattr(os, "fsync", original_fsync)
    recovered = manager.register("ambiguous_commit")
    assert recovered.skill_name == "ambiguous_commit"
    assert CanaryManager(CanaryConfig(state_dir=str(state_dir))).list_all() == [recovered]


def test_outcome_id_is_idempotent_across_manager_restart_and_conflicts_fail_closed(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "canary"
    first = CanaryManager(CanaryConfig(state_dir=str(state_dir)))
    first.register("receipt")
    first.record_outcome("receipt", True, outcome_id="turn-receipt")

    restarted = CanaryManager(CanaryConfig(state_dir=str(state_dir)))
    restarted.record_outcome("receipt", True, outcome_id="turn-receipt")
    assert restarted.get_state("receipt").sample_count == 1  # type: ignore[union-attr]

    with pytest.raises(CanaryOutcomeConflictError, match="different result"):
        restarted.record_outcome("receipt", False, outcome_id="turn-receipt")
    assert restarted.get_state("receipt").sample_count == 1  # type: ignore[union-attr]


def test_outcome_receipt_hard_limit_survives_restart_without_file_growth(
    tmp_path: Path,
) -> None:
    assert CanaryConfig().outcome_receipt_limit == 10_000
    state_dir = tmp_path / "canary"
    config = CanaryConfig(state_dir=str(state_dir), outcome_receipt_limit=2)
    manager = CanaryManager(config)
    manager.register("bounded_receipts")
    manager.record_outcome("bounded_receipts", True, outcome_id="turn-a")
    manager.record_outcome("bounded_receipts", False, outcome_id="turn-b")
    state_path = state_dir / "bounded_receipts.json"
    at_limit = state_path.read_bytes()

    restarted = CanaryManager(config)
    duplicate = restarted.record_outcome(
        "bounded_receipts",
        False,
        outcome_id="turn-b",
    )
    assert duplicate is not None and duplicate.sample_count == 2
    assert state_path.read_bytes() == at_limit

    with pytest.raises(CanaryOutcomeConflictError, match="different result"):
        restarted.record_outcome(
            "bounded_receipts",
            True,
            outcome_id="turn-b",
        )
    with pytest.raises(CanaryReceiptLimitError, match="new outcomes are frozen"):
        restarted.record_outcome(
            "bounded_receipts",
            True,
            outcome_id="turn-c",
        )

    assert state_path.read_bytes() == at_limit
    persisted = json.loads(at_limit)
    assert len(persisted["metadata"]["outcome_receipts"]) == 2


@pytest.mark.parametrize(
    "receipts",
    [
        {"not-a-sha256-digest": True},
        {hashlib.sha256(b"turn-a").hexdigest(): 1},
        {hashlib.sha256(f"turn-{index}".encode()).hexdigest(): True for index in range(3)},
    ],
)
def test_outcome_receipt_ledger_is_strictly_validated_on_restart(
    tmp_path: Path,
    receipts: dict[str, object],
) -> None:
    state_dir = tmp_path / "canary"
    config = CanaryConfig(state_dir=str(state_dir), outcome_receipt_limit=2)
    CanaryManager(config).register("strict_receipts")
    state_path = state_dir / "strict_receipts.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["metadata"]["outcome_receipts"] = receipts
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CanaryPersistenceError, match="unreadable"):
        CanaryManager(config)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_outcome_receipt_limit_configuration_must_be_positive_integer(
    tmp_path: Path,
    limit: object,
) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        CanaryManager(
            CanaryConfig(
                state_dir=str(tmp_path / "canary"),
                outcome_receipt_limit=limit,  # type: ignore[arg-type]
            )
        )


def test_receipt_ledger_cannot_be_overwritten_through_generic_metadata(
    tmp_path: Path,
) -> None:
    manager = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
    manager.register("reserved_receipts")
    with pytest.raises(ValueError, match="managed internally"):
        manager.update_metadata(
            "reserved_receipts",
            updates={"outcome_receipts": {}},
        )
    with pytest.raises(ValueError, match="managed internally"):
        manager.update_metadata(
            "reserved_receipts",
            remove=("outcome_receipts",),
        )


def test_candidate_settlement_retains_activation_when_receipt_ledger_is_full(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.safety.evolution import candidate_canary as candidate_canary_module

    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
    registry = CandidateRegistry(tmp_path / "candidates.jsonl")
    state_dir = tmp_path / "canary"
    candidate_id = _shadow_candidate(registry, suffix="receipt-limit")
    bounded_config = CanaryConfig(outcome_receipt_limit=1)
    manager = CandidateCanaryManager(registry, state_dir, config=bounded_config)
    manager.register(candidate_id)
    manager.record_outcome(candidate_id, True, outcome_id="already-received")
    state_path = next(state_dir.glob("candidate.*.json"))
    full_state = state_path.read_bytes()
    monkeypatch.setattr(
        candidate_canary_module,
        "CanaryConfig",
        lambda: CanaryConfig(outcome_receipt_limit=1),
    )

    turn_id = "turn-after-receipt-limit"
    assert record_runtime_candidate_activation(candidate_id, turn_id=turn_id)
    first = settle_runtime_candidate_outcomes(
        turn_id,
        success=True,
        registry=registry,
        state_dir=state_dir,
    )
    second = settle_runtime_candidate_outcomes(
        turn_id,
        success=True,
        registry=registry,
        state_dir=state_dir,
    )

    assert first[0]["recorded"] is False
    assert first[0]["reason"] == "CanaryReceiptLimitError"
    assert second[0]["reason"] == "CanaryReceiptLimitError"
    assert active_runtime_candidates(turn_id) == (candidate_id,)
    assert state_path.read_bytes() == full_state

    monkeypatch.setattr(
        candidate_canary_module,
        "CanaryConfig",
        lambda: CanaryConfig(outcome_receipt_limit=2),
    )
    recovered = settle_runtime_candidate_outcomes(
        turn_id,
        success=True,
        registry=registry,
        state_dir=state_dir,
    )
    assert recovered[0]["recorded"] is True
    assert active_runtime_candidates(turn_id) == ()
    assert (
        settle_runtime_candidate_outcomes(
            turn_id,
            success=True,
            registry=registry,
            state_dir=state_dir,
        )
        == []
    )
    status = CandidateCanaryManager(
        registry,
        state_dir,
        config=CanaryConfig(outcome_receipt_limit=2),
    ).status(candidate_id)
    assert status["canary"]["sample_count"] == 2


def test_phase_transition_registry_failure_retries_without_double_counting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = CandidateRegistry(tmp_path / "candidates.jsonl")
    state_dir = tmp_path / "canary"
    candidate_id = _shadow_candidate(registry)
    manager = CandidateCanaryManager(registry, state_dir)
    manager.register(candidate_id)
    for index in range(19):
        manager.record_outcome(candidate_id, True, outcome_id=f"turn-{index}")
    selector = CandidateRuntimeSelector(registry, state_dir)
    treatment_key = _treatment_key(selector, candidate_id)

    original_record_evidence = registry.record_evidence

    def fail_registry(*_args: object, **_kwargs: object) -> object:
        raise CandidateRegistryError("injected registry failure")

    monkeypatch.setattr(registry, "record_evidence", fail_registry)
    with pytest.raises(CandidateRegistryError, match="injected registry failure"):
        manager.record_outcome(candidate_id, True, outcome_id="turn-19")

    key = next(state_dir.glob("candidate.*.json")).stem
    durable_state = CanaryManager(CanaryConfig(state_dir=str(state_dir))).get_state(key)
    assert durable_state is not None
    assert durable_state.phase.value == "canary_25"
    assert durable_state.sample_count == 0
    assert durable_state.metadata["registry_sync_pending"]["operation"] == "outcome"
    assert registry.get(candidate_id).metadata["canary_phase"] == "canary_5"  # type: ignore[union-attr]
    assert selector.is_active(candidate_id, routing_key=treatment_key) is False

    monkeypatch.setattr(registry, "record_evidence", original_record_evidence)
    restarted = CandidateCanaryManager(registry, state_dir)
    wire = restarted.record_outcome(candidate_id, True, outcome_id="turn-19")

    assert wire["canary"]["phase"] == "canary_25"
    assert wire["canary"]["sample_count"] == 0
    assert "registry_sync_pending" not in wire["canary"]["metadata"]
    assert registry.get(candidate_id).metadata["canary_phase"] == "canary_25"  # type: ignore[union-attr]


def test_same_outcome_id_from_two_processes_counts_once(tmp_path: Path) -> None:
    registry_path = tmp_path / "candidates.jsonl"
    state_dir = tmp_path / "canary"
    registry = CandidateRegistry(registry_path)
    candidate_id = _shadow_candidate(registry, suffix="process")
    CandidateCanaryManager(registry, state_dir).register(candidate_id)
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    workers = [
        context.Process(
            target=_concurrent_outcome_worker,
            args=(str(registry_path), str(state_dir), candidate_id, barrier, results),
        )
        for _ in range(2)
    ]

    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)
        assert worker.exitcode == 0

    outcomes = [results.get(timeout=5) for _ in workers]
    assert {status for status, _value in outcomes} == {"ok"}
    state = CandidateCanaryManager(registry, state_dir).status(candidate_id)["canary"]
    assert state["sample_count"] == 1
    assert state["success_count"] == 1


def test_failed_pending_marker_clear_restores_activation_and_retry_settles_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
    registry = CandidateRegistry(tmp_path / "candidates.jsonl")
    state_dir = tmp_path / "canary"
    candidate_id = _shadow_candidate(registry, suffix="pending-clear")
    CandidateCanaryManager(registry, state_dir).register(candidate_id)
    turn_id = "turn-pending-clear"
    assert record_runtime_candidate_activation(candidate_id, turn_id=turn_id) is True

    original_update_metadata = CanaryManager.update_metadata
    calls = 0

    def fail_first_clear(
        self: CanaryManager,
        skill_name: str,
        *,
        updates: dict[str, object] | None = None,
        remove: tuple[str, ...] = (),
    ) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CanaryPersistenceError("injected pending-marker fsync failure")
        return original_update_metadata(self, skill_name, updates=updates, remove=remove)

    monkeypatch.setattr(CanaryManager, "update_metadata", fail_first_clear)
    failed = settle_runtime_candidate_outcomes(
        turn_id,
        success=True,
        registry=registry,
        state_dir=state_dir,
    )
    assert failed[0]["recorded"] is False
    assert failed[0]["reason"] == "CanaryPersistenceError"
    assert active_runtime_candidates(turn_id) == (candidate_id,)

    settled = settle_runtime_candidate_outcomes(
        turn_id,
        success=True,
        registry=registry,
        state_dir=state_dir,
    )
    assert settled[0]["recorded"] is True
    assert active_runtime_candidates(turn_id) == ()
    state = CandidateCanaryManager(registry, state_dir).status(candidate_id)["canary"]
    assert state["sample_count"] == 1
    assert "registry_sync_pending" not in state["metadata"]


def test_register_registry_failure_is_reconciled_by_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = CandidateRegistry(tmp_path / "candidates.jsonl")
    state_dir = tmp_path / "canary"
    candidate_id = _shadow_candidate(registry, suffix="register")
    manager = CandidateCanaryManager(registry, state_dir)
    original_transition = registry.transition

    def fail_transition(*_args: object, **_kwargs: object) -> object:
        raise CandidateRegistryError("injected transition failure")

    monkeypatch.setattr(registry, "transition", fail_transition)
    with pytest.raises(CandidateRegistryError, match="injected transition failure"):
        manager.register(candidate_id)

    assert registry.get(candidate_id).status == CandidateStatus.SHADOW  # type: ignore[union-attr]
    monkeypatch.setattr(registry, "transition", original_transition)
    wire = CandidateCanaryManager(registry, state_dir).register(candidate_id)
    assert wire["candidate"]["status"] == "canary"
    assert "registry_sync_pending" not in wire["canary"]["metadata"]


def test_promoted_candidate_stops_routing_when_rollback_registry_sync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = CandidateRegistry(tmp_path / "candidates.jsonl")
    state_dir = tmp_path / "canary"
    candidate_id = _shadow_candidate(registry, suffix="rollback")
    manager = CandidateCanaryManager(registry, state_dir)
    manager.register(candidate_id)
    for index in range(20 + 40 + 60):
        manager.record_outcome(candidate_id, True, outcome_id=f"promotion-{index}")
    selector = CandidateRuntimeSelector(registry, state_dir)
    assert selector.is_active(candidate_id, routing_key="all-promoted-traffic") is True

    original_transition = registry.transition

    def fail_transition(*_args: object, **_kwargs: object) -> object:
        raise CandidateRegistryError("injected rollback transition failure")

    monkeypatch.setattr(registry, "transition", fail_transition)
    with pytest.raises(CandidateRegistryError, match="rollback transition failure"):
        manager.force_rollback(candidate_id, reason="observed regression")

    assert registry.get(candidate_id).status == CandidateStatus.PROMOTED  # type: ignore[union-attr]
    assert selector.is_active(candidate_id, routing_key="all-promoted-traffic") is False

    monkeypatch.setattr(registry, "transition", original_transition)
    recovered = CandidateCanaryManager(registry, state_dir).force_rollback(
        candidate_id,
        reason="observed regression",
    )
    assert recovered["candidate"]["status"] == "rolled_back"
    assert "registry_sync_pending" not in recovered["canary"]["metadata"]


@pytest.mark.parametrize(
    ("metadata_update", "corrupt"),
    [
        ({"deployment_key": "wrong-deployment"}, False),
        ({"runtime_materialized": False}, False),
        ({}, True),
    ],
)
def test_runtime_selector_fails_closed_for_invalid_canary_state(
    tmp_path: Path,
    metadata_update: dict[str, object],
    corrupt: bool,
) -> None:
    registry = CandidateRegistry(tmp_path / "candidates.jsonl")
    state_dir = tmp_path / "canary"
    candidate_id = _shadow_candidate(registry, suffix=f"binding-{corrupt}")
    CandidateCanaryManager(registry, state_dir).register(candidate_id)
    selector = CandidateRuntimeSelector(registry, state_dir)
    treatment_key = _treatment_key(selector, candidate_id)
    state_path = next(state_dir.glob("candidate.*.json"))
    key = state_path.stem

    if corrupt:
        state_path.write_text("{truncated", encoding="utf-8")
    else:
        CanaryManager(CanaryConfig(state_dir=str(state_dir))).update_metadata(
            key,
            updates=metadata_update,
        )

    assert selector.is_active(candidate_id, routing_key=treatment_key) is False

