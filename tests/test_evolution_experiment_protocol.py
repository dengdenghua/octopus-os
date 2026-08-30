from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.process.paths import AppPaths
from runtime.safety.evolution.candidate_registry import (
    CandidateRegistry,
    CandidateStatus,
    GeneType,
)
from runtime.safety.evolution.experiment_protocol import (
    ExperimentStore,
    ExperimentTrial,
    TaskSpec,
    TrialStatus,
    build_pair_evidence,
)
from runtime.sensing.gateway.evolution_router import create_evolution_router


def _spec(*, goal: str = "repair the fixture") -> TaskSpec:
    return TaskSpec(
        case_id="coding.path-boundary",
        goal=goal,
        domain="general_runtime_and_coding",
        environment_digest="env-sha256",
        workspace_fixture_digest="fixture-sha256",
        budget_policy={"timeout_s": 900, "max_tokens": 20_000},
        grader_version="fixture-tests-v2",
    )


def _trial(
    engine: str,
    *,
    trial_index: int = 0,
    status: TrialStatus = TrialStatus.COMPLETED,
    passed: bool | None = True,
    quality: float = 1.0,
    infrastructure_error: str | None = None,
) -> ExperimentTrial:
    return ExperimentTrial(
        experiment_id="exp-same-task",
        run_id=f"run-{engine}-{trial_index}",
        task_spec=_spec(),
        engine=engine,
        trial_index=trial_index,
        seed=trial_index + 7,
        status=status,
        outcome_passed=passed,
        hard_gates={"correctness": bool(passed), "security": True, "scope": True},
        metrics={"quality": quality, "latency_ms": 100.0},
        infrastructure_error=infrastructure_error,
    )


def test_task_identity_is_versioned_and_not_goal_fingerprint_pairing() -> None:
    first = _spec(goal="repair   the fixture")
    second = _spec(goal="repair the fixture")
    assert first.task_spec_hash != second.task_spec_hash
    assert first.to_wire()["schema"] == "echo.evolution.task_spec.v1"


def test_strict_pairs_require_same_experiment_case_environment_and_trial() -> None:
    echo = _trial("echo", quality=0.9)
    codex = _trial("codex", quality=0.8)
    unrelated = _trial("codex", trial_index=1, quality=1.0)

    report = build_pair_evidence([echo, codex, unrelated])

    assert report["paired_count"] == 1
    assert report["unpaired_key_count"] == 1
    assert report["echo_wins"] == 1
    assert report["pairs"][0]["case_id"] == "coding.path-boundary"


def test_infrastructure_failure_is_excluded_instead_of_scored_as_engine_loss() -> None:
    report = build_pair_evidence(
        [
            _trial(
                "echo",
                status=TrialStatus.INFRASTRUCTURE_FAILED,
                passed=None,
                infrastructure_error="ws timeout",
            ),
            _trial("codex", quality=1.0),
        ]
    )
    assert report["paired_count"] == 0
    assert report["excluded"]["infrastructure_failed"] == 1
    assert report["codex_wins"] == 0


def test_experiment_store_roundtrips_trials(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path / "experiments.jsonl")
    original = _trial("echo")
    store.append(original)
    loaded = store.list_trials(experiment_id="exp-same-task")
    assert len(loaded) == 1
    assert loaded[0].pair_key == original.pair_key
    assert loaded[0].task_spec.task_spec_hash == original.task_spec.task_spec_hash


def test_candidate_registry_enforces_hard_gate_and_lineage_transitions(tmp_path: Path) -> None:
    registry = CandidateRegistry(tmp_path / "candidates.jsonl")
    candidate = registry.propose(
        gene_type=GeneType.PROMPT,
        scope="planner.system",
        patch={"op": "replace", "value": "Use the fixture runner."},
        proposer="gepa",
        role_id="coder",
        task_domain="general_runtime_and_coding",
    )
    assert candidate.status == CandidateStatus.PROPOSED

    with pytest.raises(ValueError, match="hard-gate"):
        registry.transition(candidate.candidate_id, CandidateStatus.VALIDATED)

    validated = registry.transition(
        candidate.candidate_id,
        CandidateStatus.VALIDATED,
        hard_gate_results={"correctness": True, "security": True, "sealed_holdout": True},
        experiment_ids=["exp-same-task"],
    )
    assert validated.status == CandidateStatus.VALIDATED
    assert validated.deployment_key.startswith(candidate.candidate_id + ":coder:")
    assert registry.lineage(candidate.lineage_id)[0].candidate_id == candidate.candidate_id


def test_candidate_registry_rejects_skipping_validation(tmp_path: Path) -> None:
    registry = CandidateRegistry(tmp_path / "candidates.jsonl")
    candidate = registry.propose(
        gene_type="skill",
        scope="skill.browser-recovery",
        patch={"op": "add", "manifest": {"name": "browser-recovery"}},
        proposer="skill_forge",
    )
    with pytest.raises(ValueError, match="invalid candidate transition"):
        registry.transition(
            candidate.candidate_id,
            CandidateStatus.CANARY,
            hard_gate_results={"correctness": True},
        )


def test_app_paths_owns_evolution_control_plane_files(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path)
    assert paths.evolution_experiments_path == tmp_path / "data" / "evolution_experiments.jsonl"
    assert paths.evolution_candidates_path == tmp_path / "data" / "evolution_candidates.jsonl"
    assert paths.candidate_canary_state_dir == tmp_path / "data" / "candidate_canary_states"


def test_evolution_api_exposes_controlled_evidence_and_typed_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "runtime-data"
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    ExperimentStore(data_dir / "evolution_experiments.jsonl").append(_trial("echo"))
    ExperimentStore(data_dir / "evolution_experiments.jsonl").append(_trial("codex"))
    CandidateRegistry(data_dir / "evolution_candidates.jsonl").propose(
        gene_type="prompt",
        scope="planner.system",
        patch={"op": "replace", "value": "Verify with the fixture runner."},
        proposer="gepa",
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    evidence = client.get("/api/evolution/experiments/evidence").json()
    candidates = client.get("/api/evolution/candidates").json()

    assert evidence["schema"] == "echo.evolution.pair_evidence.v1"
    assert evidence["paired_count"] == 1
    assert candidates["total"] == 1
    assert candidates["by_status"] == {"proposed": 1}

