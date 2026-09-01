from __future__ import annotations

from pathlib import Path

import pytest

from runtime.platform.process.paths import app_paths
from runtime.safety.auth.scope import TenantScope, tenant_scoped_path
from runtime.safety.evolution.candidate_canary import CandidateCanaryManager
from runtime.safety.evolution.candidate_registry import CandidateRegistry, CandidateStatus
from runtime.safety.evolution.regression_rollback import (
    RegressionRollbackError,
    rollback_active_candidates_for_regression,
)


def _scope(tenant: str, actor: str, *, cross: bool = False) -> TenantScope:
    return TenantScope(tenant_id=tenant, actor_id=actor, allow_cross_tenant=cross)


def _active_candidate(
    registry: CandidateRegistry,
    state_dir: Path,
    *,
    role_id: str,
    suffix: str,
    materialize_runtime: bool,
) -> str:
    candidate = registry.propose(
        gene_type="prompt",
        scope=f"planner.regression:{suffix}",
        patch={"op": "replace", "value": f"candidate {suffix}"},
        proposer="regression-test",
        role_id=role_id,
    )
    registry.transition(
        candidate.candidate_id,
        CandidateStatus.VALIDATED,
        hard_gate_results={"correctness": True, "safety": True},
    )
    registry.transition(candidate.candidate_id, CandidateStatus.SHADOW)
    CandidateCanaryManager(
        registry,
        state_dir,
        materialize_runtime=materialize_runtime,
    ).register(candidate.candidate_id)
    return candidate.candidate_id


def test_score_regression_rolls_back_only_active_candidates_for_exact_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    paths = app_paths()
    registry = CandidateRegistry(paths.evolution_candidates_path)
    coder_a = _active_candidate(
        registry,
        paths.candidate_canary_state_dir,
        role_id="coder",
        suffix="coder-a",
        materialize_runtime=True,
    )
    coder_b = _active_candidate(
        registry,
        paths.candidate_canary_state_dir,
        role_id="coder",
        suffix="coder-b",
        materialize_runtime=True,
    )
    researcher = _active_candidate(
        registry,
        paths.candidate_canary_state_dir,
        role_id="researcher",
        suffix="researcher",
        materialize_runtime=True,
    )

    result = rollback_active_candidates_for_regression("coder")

    assert set(result.active_candidate_ids) == {coder_a, coder_b}
    assert set(result.rolled_back_candidate_ids) == {coder_a, coder_b}
    assert result.changed is True
    assert registry.get(coder_a).status == CandidateStatus.ROLLED_BACK  # type: ignore[union-attr]
    assert registry.get(coder_b).status == CandidateStatus.ROLLED_BACK  # type: ignore[union-attr]
    assert registry.get(researcher).status == CandidateStatus.CANARY  # type: ignore[union-attr]


def test_score_regression_isolated_by_tenant_and_owner_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    paths = app_paths()
    scope_a = _scope("tenant-a", "alice")
    scope_b = _scope("tenant-b", "bob")
    path_a = tenant_scoped_path(paths.evolution_candidates_path, scope_a)
    path_b = tenant_scoped_path(paths.evolution_candidates_path, scope_b)
    registry_a = CandidateRegistry(path_a, tenant_scope=scope_a)
    registry_b = CandidateRegistry(path_b, tenant_scope=scope_b)
    candidate_a = _active_candidate(
        registry_a,
        path_a.parent / paths.candidate_canary_state_dir.name,
        role_id="coder",
        suffix="a",
        materialize_runtime=False,
    )
    candidate_b = _active_candidate(
        registry_b,
        path_b.parent / paths.candidate_canary_state_dir.name,
        role_id="coder",
        suffix="b",
        materialize_runtime=False,
    )

    result = rollback_active_candidates_for_regression("coder", scope=scope_a)

    assert result.rolled_back_candidate_ids == (candidate_a,)
    assert registry_a.get(candidate_a).status == CandidateStatus.ROLLED_BACK  # type: ignore[union-attr]
    assert registry_b.get(candidate_b).status == CandidateStatus.CANARY  # type: ignore[union-attr]


def test_score_regression_with_no_active_candidate_is_a_durable_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    paths = app_paths()
    registry = CandidateRegistry(paths.evolution_candidates_path)
    candidate = registry.propose(
        gene_type="prompt",
        scope="planner.noop",
        patch={"op": "replace", "value": "not active"},
        proposer="regression-test",
        role_id="coder",
    )
    before = paths.evolution_candidates_path.read_bytes()

    result = rollback_active_candidates_for_regression("coder")

    assert result.active_candidate_ids == ()
    assert result.rolled_back_candidate_ids == ()
    assert paths.evolution_candidates_path.read_bytes() == before
    assert registry.get(candidate.candidate_id).status == CandidateStatus.PROPOSED  # type: ignore[union-attr]


def test_corrupt_registry_fails_closed_before_canary_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    paths = app_paths()
    registry = CandidateRegistry(paths.evolution_candidates_path)
    _active_candidate(
        registry,
        paths.candidate_canary_state_dir,
        role_id="coder",
        suffix="corrupt",
        materialize_runtime=True,
    )
    state_path = next(paths.candidate_canary_state_dir.glob("candidate.*.json"))
    state_before = state_path.read_bytes()
    with paths.evolution_candidates_path.open("ab") as handle:
        handle.write(b"{invalid json}\n")

    with pytest.raises(RegressionRollbackError, match="failed closed"):
        rollback_active_candidates_for_regression("coder")

    assert state_path.read_bytes() == state_before


def test_cross_tenant_aggregate_regression_never_guesses_a_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))

    with pytest.raises(RegressionRollbackError, match="cannot be attributed"):
        rollback_active_candidates_for_regression(
            "coder",
            scope=_scope("operator", "admin", cross=True),
        )

    assert not app_paths().evolution_candidates_path.exists()

