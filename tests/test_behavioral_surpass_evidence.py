from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from runtime.safety.evolution.behavioral_surpass_evidence import (
    ALLOWED_EXECUTION_MODES,
    BUNDLE_SCHEMA,
    CODEX_DESKTOP_EXECUTABLE,
    REQUIRED_DOMAINS,
    behavioral_system_provenance_digest,
    compute_behavioral_surpass_evidence,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_valid_bundle(root: Path, now: datetime) -> Path:
    artifact_root = root / "benchmarks" / "results" / "artifacts"
    artifact_root.mkdir(parents=True)
    manifest_cases = []
    case_metadata: dict[str, tuple[str, str]] = {}
    for domain in REQUIRED_DOMAINS:
        for case_index in range(2):
            case_id = f"{domain}-{case_index}"
            prompt = f"prompt:{case_id}"
            rubric = {"grader": "test", "expected": case_id}
            prompt_digest = _digest(prompt)
            rubric_digest = _digest(json.dumps(rubric, sort_keys=True, separators=(",", ":")))
            case_metadata[case_id] = (prompt_digest, rubric_digest)
            manifest_cases.append(
                {
                    "id": case_id,
                    "domain": domain,
                    "execution_mode": sorted(ALLOWED_EXECUTION_MODES[domain])[0],
                    "prompt": prompt,
                    "rubric": rubric,
                }
            )
    manifest = {
        "schema": "echo.behavioral_surpass_suite.v1",
        "suite_id": "same-task-head-to-head-v1",
        "cases": manifest_cases,
    }
    manifest_path = root / "benchmarks" / "behavioral-surpass-suite.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    systems: dict[str, object] = {}
    for system_id in ("echo", "codex"):
        if system_id == "echo":
            provenance = {
                "schema": "echo.behavioral_system_provenance.v1",
                "system_id": "echo",
                "model": {"expected": "echo-model", "requested": "echo-model"},
                "config": {
                    "expected_sha256": "a" * 64,
                    "observed_sha256": "a" * 64,
                },
            }
        else:
            provenance = {
                "schema": "echo.behavioral_system_provenance.v1",
                "system_id": "codex",
                "model": {"expected": "codex-model", "requested": "codex-model"},
                "executable": {
                    "path": CODEX_DESKTOP_EXECUTABLE,
                    "expected_sha256": "b" * 64,
                    "observed_sha256": "b" * 64,
                    "codesign": {
                        "expected_team_identifier": "OPENAI-TEAM",
                        "observed_team_identifier": "OPENAI-TEAM",
                        "expected_identifier": "com.openai.codex",
                        "observed_identifier": "com.openai.codex",
                    },
                },
            }
        provenance_digest = behavioral_system_provenance_digest(provenance)
        cases = []
        for domain in REQUIRED_DOMAINS:
            for case_index in range(2):
                case_id = f"{domain}-{case_index}"
                prompt_digest, rubric_digest = case_metadata[case_id]
                artifacts = []
                for trial_index in range(3):
                    relative = (
                        Path("benchmarks")
                        / "results"
                        / "artifacts"
                        / f"{system_id}-{case_id}-{trial_index}.json"
                    )
                    content = json.dumps(
                        {
                            "schema": "echo.behavioral_trajectory.v2",
                            "system_id": system_id,
                            "system_version": f"{system_id}-test",
                            "system_provenance_sha256": provenance_digest,
                            "case_id": case_id,
                            "trial_index": trial_index,
                            "prompt_sha256": prompt_digest,
                            "trajectory": {
                                "trial_id": f"{system_id}-{case_id}-{trial_index}",
                                "case_id": case_id,
                                "steps": [{"kind": "text_delta", "payload": {"delta": "ok"}}],
                            },
                            "verdict": {
                                "passed": True,
                                "score": 1.0,
                                "reason": "passed",
                            },
                        },
                        sort_keys=True,
                    )
                    (root / relative).write_text(content, encoding="utf-8")
                    artifacts.append({"path": str(relative), "sha256": _digest(content)})
                cases.append(
                    {
                        "id": case_id,
                        "domain": domain,
                        "k": 3,
                        "passes": 3,
                        "trajectory_count": 3,
                        "outcome_grader": True,
                        "isolated_state": True,
                        "execution_mode": sorted(ALLOWED_EXECUTION_MODES[domain])[0],
                        "rubric_digest": rubric_digest,
                        "prompt_digest": prompt_digest,
                        "artifacts": artifacts,
                    }
                )
        systems[system_id] = {
            "version": f"{system_id}-test",
            "provenance": provenance,
            "provenance_sha256": provenance_digest,
            "cases": cases,
        }
    bundle = {
        "schema": BUNDLE_SCHEMA,
        "suite_id": "same-task-head-to-head-v1",
        "runner_version": "test-runner-v1",
        "source_revision": "abc123",
        "suite_manifest_sha256": manifest_digest,
        "generated_at": now.isoformat(),
        "systems": systems,
    }
    path = root / "benchmarks" / "results" / "behavioral-surpass-latest.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return path


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_missing_bundle_never_claims_surpassed(tmp_path: Path) -> None:
    report = compute_behavioral_surpass_evidence(root=tmp_path)

    assert report["ready"] is False
    assert report["verdict"] == "missing_behavioral_evidence"
    assert next(row for row in report["checks"] if row["id"] == "bundle_present")["passed"] is False


def test_current_provider_failure_is_reported_as_unscored_blocker(tmp_path: Path) -> None:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    status_path = tmp_path / "benchmarks" / "results" / "behavioral-infrastructure-latest.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(
        json.dumps(
            {
                "schema": "echo.behavioral_infrastructure_failure.v1",
                "system_id": "echo",
                "generated_at": now.isoformat(),
                "scored": False,
                "failures": [
                    {
                        "case_id": "browser.rich-editor-upload",
                        "categories": ["infrastructure"],
                        "errors": ["sensitive provider message is not exposed"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = compute_behavioral_surpass_evidence(root=tmp_path, now=now)

    assert report["ready"] is False
    assert report["verdict"] == "infrastructure_blocked"
    assert report["infrastructure"] == {
        "active": True,
        "current": True,
        "path": str(status_path),
        "generated_at": now.isoformat(),
        "age_days": 0.0,
        "system_id": "echo",
        "failures": [
            {
                "case_id": "browser.rich-editor-upload",
                "categories": ["infrastructure"],
            }
        ],
    }
    assert "sensitive provider message" not in json.dumps(report)


def test_valid_fresh_same_task_bundle_certifies_behavior(tmp_path: Path) -> None:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    _write_valid_bundle(tmp_path, now)

    report = compute_behavioral_surpass_evidence(root=tmp_path, now=now)

    assert report["ready"] is True
    assert report["verdict"] == "surpassed"
    assert report["systems"]["echo"]["aggregate_pass_pow_k"] == 1.0
    assert report["systems"]["codex"]["aggregate_pass_pow_k"] == 1.0
    assert len(report["domains"]) == len(REQUIRED_DOMAINS)
    assert all(row["ready"] for row in report["domains"])
    assert all(row["passed"] for row in report["checks"])


def test_stale_bundle_is_not_release_evidence(tmp_path: Path) -> None:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    _write_valid_bundle(tmp_path, now - timedelta(days=31))

    report = compute_behavioral_surpass_evidence(root=tmp_path, now=now)

    assert report["ready"] is False
    assert report["verdict"] == "stale_behavioral_evidence"


def test_different_rubric_is_not_a_head_to_head_comparison(tmp_path: Path) -> None:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    path = _write_valid_bundle(tmp_path, now)
    bundle = _load(path)
    systems = bundle["systems"]
    assert isinstance(systems, dict)
    codex = systems["codex"]
    assert isinstance(codex, dict)
    cases = codex["cases"]
    assert isinstance(cases, list)
    cases[0]["rubric_digest"] = _digest("different-rubric")
    _write(path, bundle)

    report = compute_behavioral_surpass_evidence(root=tmp_path, now=now)

    assert report["ready"] is False
    assert next(row for row in report["checks"] if row["id"] == "same_cases")["passed"] is False


def test_different_prompt_is_not_a_head_to_head_comparison(tmp_path: Path) -> None:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    path = _write_valid_bundle(tmp_path, now)
    bundle = _load(path)
    systems = bundle["systems"]
    assert isinstance(systems, dict)
    codex = systems["codex"]
    assert isinstance(codex, dict)
    cases = codex["cases"]
    assert isinstance(cases, list)
    cases[0]["prompt_digest"] = _digest("different-prompt")
    _write(path, bundle)

    report = compute_behavioral_surpass_evidence(root=tmp_path, now=now)

    assert report["ready"] is False
    assert next(row for row in report["checks"] if row["id"] == "same_cases")["passed"] is False


def test_cherry_picked_cases_do_not_match_fixed_suite(tmp_path: Path) -> None:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    path = _write_valid_bundle(tmp_path, now)
    bundle = _load(path)
    systems = bundle["systems"]
    assert isinstance(systems, dict)
    for system in systems.values():
        assert isinstance(system, dict)
        cases = system["cases"]
        assert isinstance(cases, list)
        cases.pop()
    _write(path, bundle)

    report = compute_behavioral_surpass_evidence(root=tmp_path, now=now)

    assert report["ready"] is False
    assert next(row for row in report["checks"] if row["id"] == "same_cases")["passed"] is True
    assert (
        next(row for row in report["checks"] if row["id"] == "fixed_suite_cases")["passed"] is False
    )


def test_tampered_trajectory_artifact_fails_digest_gate(tmp_path: Path) -> None:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    path = _write_valid_bundle(tmp_path, now)
    bundle = _load(path)
    systems = bundle["systems"]
    assert isinstance(systems, dict)
    echo = systems["echo"]
    assert isinstance(echo, dict)
    cases = echo["cases"]
    assert isinstance(cases, list)
    artifact = cases[0]["artifacts"][0]
    (tmp_path / artifact["path"]).write_text("tampered", encoding="utf-8")

    report = compute_behavioral_surpass_evidence(root=tmp_path, now=now)

    assert report["ready"] is False
    assert (
        next(row for row in report["checks"] if row["id"] == "artifacts_verified")["passed"]
        is False
    )
    assert any("digest mismatch" in error for error in report["errors"])


def test_missing_system_provenance_fails_release_gate(tmp_path: Path) -> None:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    path = _write_valid_bundle(tmp_path, now)
    bundle = _load(path)
    del bundle["systems"]["echo"]["provenance"]
    _write(path, bundle)

    report = compute_behavioral_surpass_evidence(root=tmp_path, now=now)

    assert report["ready"] is False
    assert (
        next(row for row in report["checks"] if row["id"] == "system_provenance")["passed"] is False
    )


def test_substituted_codex_identity_fails_release_gate(tmp_path: Path) -> None:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    path = _write_valid_bundle(tmp_path, now)
    bundle = _load(path)
    codex = bundle["systems"]["codex"]
    codex["provenance"]["executable"]["observed_sha256"] = "c" * 64
    _write(path, bundle)

    report = compute_behavioral_surpass_evidence(root=tmp_path, now=now)

    assert report["ready"] is False
    assert (
        next(row for row in report["checks"] if row["id"] == "system_provenance")["passed"] is False
    )


def test_reused_trajectory_does_not_count_as_repeated_trials(tmp_path: Path) -> None:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    path = _write_valid_bundle(tmp_path, now)
    bundle = _load(path)
    systems = bundle["systems"]
    assert isinstance(systems, dict)
    echo = systems["echo"]
    assert isinstance(echo, dict)
    cases = echo["cases"]
    assert isinstance(cases, list)
    cases[0]["artifacts"][1] = dict(cases[0]["artifacts"][0])
    _write(path, bundle)

    report = compute_behavioral_surpass_evidence(root=tmp_path, now=now)

    assert report["ready"] is False
    assert (
        next(row for row in report["checks"] if row["id"] == "artifacts_verified")["passed"]
        is False
    )
    assert any("reuses trajectory artifact paths" in error for error in report["errors"])



