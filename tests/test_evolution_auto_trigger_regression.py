from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.platform.process.paths import app_paths
from runtime.safety.auth.scope import TenantScope, tenant_scoped_path
from runtime.safety.evolution import auto_trigger
from runtime.safety.evolution.auto_trigger import AutoTriggerConfig, EvolutionAutoTrigger
from runtime.safety.evolution.candidate_canary import CandidateCanaryManager
from runtime.safety.evolution.candidate_registry import CandidateRegistry, CandidateStatus


class _Registry:
    def all_ids(self) -> list[str]:
        return ["coder"]


def _trigger() -> EvolutionAutoTrigger:
    trigger = EvolutionAutoTrigger()
    trigger._active = True
    trigger._config = AutoTriggerConfig(drift_critical_auto_rollback=True)
    trigger._agent_registry = _Registry()
    return trigger


def _active_candidate(
    registry: CandidateRegistry,
    state_dir: Path,
    *,
    materialize_runtime: bool,
) -> str:
    candidate = registry.propose(
        gene_type="prompt",
        scope="planner.regression-trigger",
        patch={"op": "replace", "value": "candidate under observation"},
        proposer="auto-trigger-test",
        role_id="coder",
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


def test_critical_score_event_rolls_back_governed_legacy_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    paths = app_paths()
    registry = CandidateRegistry(paths.evolution_candidates_path)
    candidate_id = _active_candidate(
        registry,
        paths.candidate_canary_state_dir,
        materialize_runtime=True,
    )

    _trigger()._on_drift_event(
        SimpleNamespace(
            agent_id="coder",
            severity="critical",
            drift_kind="score_regression",
            detail="score dropped 0.4",
            scope_mode="legacy",
            tenant_id="",
            owner_actor_id="",
        )
    )

    assert registry.get(candidate_id).status == CandidateStatus.ROLLED_BACK  # type: ignore[union-attr]


def test_critical_tenant_score_event_rolls_back_only_exact_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    paths = app_paths()
    scope_a = TenantScope("tenant-a", "alice")
    scope_b = TenantScope("tenant-b", "bob")
    path_a = tenant_scoped_path(paths.evolution_candidates_path, scope_a)
    path_b = tenant_scoped_path(paths.evolution_candidates_path, scope_b)
    registry_a = CandidateRegistry(path_a, tenant_scope=scope_a)
    registry_b = CandidateRegistry(path_b, tenant_scope=scope_b)
    candidate_a = _active_candidate(
        registry_a,
        path_a.parent / paths.candidate_canary_state_dir.name,
        materialize_runtime=False,
    )
    candidate_b = _active_candidate(
        registry_b,
        path_b.parent / paths.candidate_canary_state_dir.name,
        materialize_runtime=False,
    )

    _trigger()._on_drift_event(
        SimpleNamespace(
            agent_id="coder",
            severity="critical",
            drift_kind="score_regression",
            detail="tenant score dropped 0.5",
            scope_mode="tenant",
            tenant_id=scope_a.tenant_id,
            owner_actor_id=scope_a.actor_id,
        )
    )

    assert registry_a.get(candidate_a).status == CandidateStatus.ROLLED_BACK  # type: ignore[union-attr]
    assert registry_b.get(candidate_b).status == CandidateStatus.CANARY  # type: ignore[union-attr]


def test_partial_and_cross_tenant_event_scopes_are_non_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trigger = _trigger()
    calls: list[object] = []
    monkeypatch.setattr(trigger, "_trigger_rollback_from_event", calls.append)

    for event in (
        SimpleNamespace(
            agent_id="coder",
            severity="critical",
            scope_mode="tenant",
            tenant_id="tenant-a",
            owner_actor_id="",
        ),
        SimpleNamespace(
            agent_id="coder",
            severity="critical",
            scope_mode="cross_tenant",
            tenant_id="ops",
            owner_actor_id="admin",
        ),
        SimpleNamespace(
            agent_id="coder",
            severity="critical",
            scope_mode="legacy",
            tenant_id="spoofed",
            owner_actor_id="spoofed",
        ),
    ):
        trigger._on_drift_event(event)

    assert calls == []


def test_tenant_fitness_event_propagates_exact_scope_to_deep_evolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trigger = _trigger()
    trigger._config = AutoTriggerConfig(fitness_threshold=0.5)
    calls: list[tuple[str, TenantScope | None]] = []

    def _capture(agent_id: str, *, scope: TenantScope | None = None) -> None:
        calls.append((agent_id, scope))

    monkeypatch.setattr(trigger, "_trigger_evolve", _capture)
    trigger._on_fitness_event(
        SimpleNamespace(
            agent_id="coder",
            combined_score=0.1,
            scope_mode="tenant",
            tenant_id="tenant-a",
            owner_actor_id="alice",
        )
    )

    assert calls == [("coder", TenantScope("tenant-a", "alice"))]


def test_tenant_event_without_registry_checks_the_same_scoped_score_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trigger = EvolutionAutoTrigger()
    trigger._active = True
    trigger._config = AutoTriggerConfig(fitness_threshold=0.5)
    trigger._stack = SimpleNamespace(config=SimpleNamespace(name="coder"))
    trigger._agent_registry = None
    wanted = TenantScope("tenant-a", "alice")
    checked: list[tuple[str, TenantScope | None]] = []

    def _history(agent_id: str, *, scope: TenantScope | None = None) -> bool:
        checked.append((agent_id, scope))
        return scope == wanted

    evolved: list[tuple[str, TenantScope | None]] = []
    monkeypatch.setattr(auto_trigger, "_has_score_history", _history)
    monkeypatch.setattr(
        trigger,
        "_trigger_evolve",
        lambda agent_id, *, scope=None: evolved.append((agent_id, scope)),
    )

    trigger._on_fitness_event(
        SimpleNamespace(
            agent_id="coder",
            combined_score=0.1,
            scope_mode="tenant",
            tenant_id=wanted.tenant_id,
            owner_actor_id=wanted.actor_id,
        )
    )

    assert checked == [("coder", wanted)]
    assert evolved == [("coder", wanted)]


def test_registry_rebind_preserves_allowed_tenant_monitor() -> None:
    trigger = EvolutionAutoTrigger()
    scope = TenantScope("tenant-a", "alice")
    monitor = SimpleNamespace(agent_id="coder")
    key = auto_trigger._monitor_key("coder", scope)
    trigger._drift_monitors = {key: monitor}

    trigger.bind_agent_registry(_Registry())

    assert trigger._drift_monitors == {key: monitor}

