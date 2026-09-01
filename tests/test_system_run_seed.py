from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.eval_harness import EvalCase
from benchmarks.system_run_seed import load_system_run_seed, merge_seed_reports
from runtime.safety.evolution.behavioral_surpass_evidence import (
    behavioral_system_provenance_digest,
)


def _case() -> EvalCase:
    return EvalCase(
        id="coding.concurrent-cache",
        prompt="fixed prompt",
        grader=lambda _trajectory: True,
        metadata={
            "domain": "general_runtime_and_coding",
            "execution_mode": "real_provider",
            "outcome_grader": True,
            "isolated_state": True,
            "prompt_digest": "a" * 64,
            "rubric_digest": "b" * 64,
        },
    )


def _write_seed(root: Path, *, k: int = 3) -> Path:
    artifacts = []
    for index in range(k):
        payload = {
            "schema": "echo.behavioral_trajectory.v1",
            "system_id": "echo",
            "system_version": "echo-local",
            "case_id": "coding.concurrent-cache",
            "trial_index": index,
            "prompt_sha256": "a" * 64,
            "trajectory": {
                "trial_id": f"trial-{index}",
                "case_id": "coding.concurrent-cache",
                "started_at": float(index + 1),
                "ended_at": float(index + 2),
                "error": None,
                "failure_category": None,
                "steps": [],
            },
            "verdict": {
                "passed": True,
                "score": 1.0,
                "reason": "passed",
                "rubric": {"grader": "fixture_tests"},
            },
        }
        content = json.dumps(payload, sort_keys=True).encode()
        artifact = root / f"artifact-{index}.json"
        artifact.write_bytes(content)
        artifacts.append({"path": artifact.name, "sha256": hashlib.sha256(content).hexdigest()})
    run = root / "run.json"
    run.write_text(
        json.dumps(
            {
                "schema": "echo.behavioral_system_run.v1",
                "suite_id": "same-task-head-to-head-v1",
                "system_id": "echo",
                "system": {
                    "version": "echo-local",
                    "cases": [
                        {
                            "id": "coding.concurrent-cache",
                            "domain": "general_runtime_and_coding",
                            "execution_mode": "real_provider",
                            "outcome_grader": True,
                            "isolated_state": True,
                            "prompt_digest": "a" * 64,
                            "rubric_digest": "b" * 64,
                            "k": k,
                            "trajectory_count": k,
                            "passes": k,
                            "artifacts": artifacts,
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    return run


def _release_provenance(model: str = "approved-model") -> dict[str, object]:
    return {
        "schema": "echo.behavioral_system_provenance.v1",
        "system_id": "echo",
        "model": {"expected": model, "requested": model},
        "config": {"expected_sha256": "c" * 64, "observed_sha256": "c" * 64},
    }


def _write_release_seed(root: Path) -> Path:
    run = _write_seed(root)
    payload = json.loads(run.read_text(encoding="utf-8"))
    provenance = _release_provenance()
    provenance_digest = behavioral_system_provenance_digest(provenance)
    payload["schema"] = "echo.behavioral_system_run.v2"
    payload["system"]["provenance"] = provenance
    payload["system"]["provenance_sha256"] = provenance_digest
    for artifact_ref in payload["system"]["cases"][0]["artifacts"]:
        artifact_path = root / artifact_ref["path"]
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact["schema"] = "echo.behavioral_trajectory.v2"
        artifact["system_provenance_sha256"] = provenance_digest
        content = json.dumps(artifact, sort_keys=True).encode()
        artifact_path.write_bytes(content)
        artifact_ref["sha256"] = hashlib.sha256(content).hexdigest()
    run.write_text(json.dumps(payload), encoding="utf-8")
    return run


def test_load_system_run_seed_reconstructs_verified_report(tmp_path: Path) -> None:
    report = load_system_run_seed(
        _write_seed(tmp_path),
        root=tmp_path,
        expected_system="echo",
        expected_version="echo-local",
        expected_suite_id="same-task-head-to-head-v1",
        expected_k=3,
        cases=[_case()],
    )

    assert len(report.cases) == 1
    assert report.cases[0].passes == 3
    assert report.cases[0].pass_pow_k == 1.0
    assert [trajectory.trial_id for trajectory in report.cases[0].trajectories] == [
        "trial-0",
        "trial-1",
        "trial-2",
    ]


def test_load_system_run_seed_rejects_tampered_artifact(tmp_path: Path) -> None:
    run = _write_seed(tmp_path)
    (tmp_path / "artifact-1.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="digest mismatch"):
        load_system_run_seed(
            run,
            root=tmp_path,
            expected_system="echo",
            expected_version="echo-local",
            expected_suite_id="same-task-head-to-head-v1",
            expected_k=3,
            cases=[_case()],
        )


def test_load_system_run_seed_rejects_metadata_drift(tmp_path: Path) -> None:
    case = _case()
    case.metadata["rubric_digest"] = "c" * 64

    with pytest.raises(ValueError, match="rubric_digest"):
        load_system_run_seed(
            _write_seed(tmp_path),
            root=tmp_path,
            expected_system="echo",
            expected_version="echo-local",
            expected_suite_id="same-task-head-to-head-v1",
            expected_k=3,
            cases=[case],
        )


def test_release_seed_rejects_model_or_config_provenance_drift(tmp_path: Path) -> None:
    seed = _write_release_seed(tmp_path)

    with pytest.raises(ValueError, match="provenance does not match"):
        load_system_run_seed(
            seed,
            root=tmp_path,
            expected_system="echo",
            expected_version="echo-local",
            expected_suite_id="same-task-head-to-head-v1",
            expected_k=3,
            cases=[_case()],
            expected_provenance=_release_provenance("different-model"),
        )


def test_merge_seed_reports_rejects_duplicate_cases(tmp_path: Path) -> None:
    report = load_system_run_seed(
        _write_seed(tmp_path),
        root=tmp_path,
        expected_system="echo",
        expected_version="echo-local",
        expected_suite_id="same-task-head-to-head-v1",
        expected_k=3,
        cases=[_case()],
    )

    with pytest.raises(ValueError, match="duplicate seeded/checkpoint case"):
        merge_seed_reports(report, report)

