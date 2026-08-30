from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from runtime.memory.learning.review_queue import ReviewQueue
from scripts import production_readiness_gate as gate

REPO_ROOT = Path(__file__).resolve().parents[1]

ACTIONS_SETUP_PYTHON = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
ACTIONS_DOWNLOAD_ARTIFACT = "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"
ACTIONS_UPLOAD_ARTIFACT = "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
ASTRAL_SETUP_UV = "astral-sh/setup-uv@d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86"
PYPA_PUBLISH = "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33"
SIGSTORE_COSIGN_INSTALLER = "sigstore/cosign-installer@7e8b541eb2e61bf99390e1afd4be13a184e9ebc5"


def test_all_github_actions_are_pinned_to_full_commit_shas() -> None:
    """A mutable major tag must never control a privileged release build."""

    unpinned: list[str] = []
    action_ref = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in workflow.get("jobs", {}).items():
            for index, step in enumerate(job.get("steps", [])):
                uses = step.get("uses")
                if uses is not None and not action_ref.fullmatch(uses):
                    unpinned.append(f"{path.name}:{job_name}:steps[{index}]={uses}")

    assert not unpinned, "GitHub Actions must use immutable full SHAs: " + ", ".join(unpinned)


def test_ci_python_jobs_use_exact_runners_and_locked_dependencies() -> None:
    path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    workflow_text = path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)

    assert "-latest" not in workflow_text
    assert "pip install" not in workflow_text
    assert workflow["jobs"]["lint-and-test"]["strategy"]["matrix"]["python-version"] == [
        "3.11.9",
        "3.12.11",
    ]
    assert workflow["jobs"]["pytest-cross-platform"]["strategy"]["matrix"]["os"] == [
        "windows-2025",
        "macos-15",
    ]

    missing_lock_install: list[str] = []
    for name, job in workflow["jobs"].items():
        steps = job.get("steps", [])
        if not any(step.get("uses") == ACTIONS_SETUP_PYTHON for step in steps):
            continue
        if not any(step.get("uses") == ASTRAL_SETUP_UV for step in steps):
            missing_lock_install.append(f"{name}:setup-uv")
        if not any("uv sync --locked" in str(step.get("run", "")) for step in steps):
            missing_lock_install.append(f"{name}:uv-sync")

    assert not missing_lock_install, "every Python CI job must install from uv.lock: " + ", ".join(
        missing_lock_install
    )


@pytest.fixture
def review_queue_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "review_queue.json"


def test_production_readiness_gate_requires_behavioral_release_evidence(
    monkeypatch,
    tmp_path: Path,
    review_queue_path: Path,
) -> None:
    monkeypatch.setenv(
        "ECHO_BEHAVIORAL_INFRASTRUCTURE_STATUS",
        str(tmp_path / "no-infrastructure-receipt.json"),
    )
    monkeypatch.setenv(
        "ECHO_BEHAVIORAL_EVAL_BUNDLE",
        str(tmp_path / "no-behavioral-bundle.json"),
    )
    result = gate.run_gate(review_queue_path=review_queue_path)

    assert result.failures
    assert any("e2e surpass certification is not ready" in item for item in result.failures)
    assert result.scorecard_score == 98
    assert result.scorecard_evidence_adjusted_score >= gate.MIN_SCORE
    assert result.automation_score >= gate.MIN_SCORE
    assert result.e2e_ready is False
    assert result.e2e_verdict == "needs_behavioral_evidence"
    assert result.e2e_summary["scorecard_echo"] == 98
    assert result.e2e_summary["scorecard_best_external"] == 97
    assert result.e2e_summary["automation_echo"] == 96
    assert result.e2e_summary["coverage_ready"] == 7
    assert result.e2e_summary["coverage_total"] == 7
    assert result.e2e_summary["coverage_gap_domains"] == 0
    assert result.e2e_summary["quality_ready"] == result.e2e_summary["quality_total"]
    assert result.e2e_coverage["summary"]["ready_domains"] == 7
    assert result.e2e_coverage["summary"]["gap_domain_ids"] == []
    assert "behavioral:bundle_present" in result.e2e_failed_checks
    assert result.e2e_behavioral["verdict"] == "missing_behavioral_evidence"
    assert result.e2e_summary_text == (
        "e2e_scorecard=98, e2e_best_external=97, "
        "e2e_automation=96, e2e_coverage=7/7, e2e_quality=7/7, "
        "e2e_behavioral=missing"
    )
    assert "echo.repo_context_quality.v1" in result.quality_summary
    assert "echo.product_experience_quality.v1" in result.quality_summary
    assert "echo.agent_loop_quality.v1" in result.quality_summary
    assert "echo.digital_employee_quality.v1" in result.quality_summary


def test_static_only_gate_passes_without_claiming_release_proof(
    monkeypatch,
    tmp_path: Path,
    capsys,
    review_queue_path: Path,
) -> None:
    monkeypatch.setenv(
        "ECHO_BEHAVIORAL_INFRASTRUCTURE_STATUS",
        str(tmp_path / "no-infrastructure-receipt.json"),
    )
    monkeypatch.setenv(
        "ECHO_BEHAVIORAL_EVAL_BUNDLE",
        str(tmp_path / "no-behavioral-bundle.json"),
    )

    code = gate.main(
        [
            "--review-queue-path",
            str(review_queue_path),
            "--static-only",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert code == 0
    assert data["mode"] == "static_only"
    assert data["gate_passed"] is True
    assert data["ready"] is False
    assert data["release_proof"] is False
    assert data["proof_scope"] == "static_only_non_release"
    assert data["notice"].startswith("NON-RELEASE PROOF")
    assert data["failures"] == []
    assert data["scorecard_calibration"]["ready"] is True
    assert data["scorecard_calibration"]["context"]["as_of"] == "2026-08-04"
    assert data["e2e"]["ready"] is False
    assert "behavioral:bundle_present" in data["e2e"]["failed_checks"]


def test_static_only_gate_still_blocks_static_score_regressions(
    monkeypatch,
    review_queue_path: Path,
) -> None:
    real_scorecard = gate.compute_agent_competitor_scorecard

    def degraded_scorecard(*, target_score: int):
        report = real_scorecard(target_score=target_score)
        report["evidence_adjusted_overall"]["echo"] = target_score - 1
        return report

    monkeypatch.setattr(gate, "compute_agent_competitor_scorecard", degraded_scorecard)

    result = gate.run_gate(
        min_score=95,
        review_queue_path=review_queue_path,
        static_only=True,
    )

    assert any(
        "agent scorecard echo evidence-adjusted overall is 94" in item for item in result.failures
    )
    assert result.to_dict()["gate_passed"] is False


@pytest.mark.parametrize("static_only", [False, True])
def test_readiness_gate_blocks_stale_scorecard_calibration_in_every_mode(
    monkeypatch,
    review_queue_path: Path,
    static_only: bool,
) -> None:
    stale_day = date.fromisoformat(gate.SCORECARD_CALIBRATION_AS_OF) + timedelta(
        days=gate.SCORECARD_CALIBRATION_MAX_AGE_DAYS + 1,
    )
    monkeypatch.setattr(gate, "_utc_today", lambda: stale_day)

    result = gate.run_gate(
        review_queue_path=review_queue_path,
        static_only=static_only,
    )

    assert any("agent scorecard calibration is stale" in item for item in result.failures)
    report = result.to_dict()
    assert report["gate_passed"] is False
    assert report["scorecard_calibration"]["ready"] is False
    assert report["scorecard_calibration"]["age_days"] == (
        gate.SCORECARD_CALIBRATION_MAX_AGE_DAYS + 1
    )


def test_static_readiness_gate_rejects_untrusted_scorecard_calibration_source(
    monkeypatch,
    review_queue_path: Path,
) -> None:
    real_scorecard = gate.compute_agent_competitor_scorecard

    def untrusted_scorecard(*, target_score: int):
        report = real_scorecard(target_score=target_score)
        report["baseline_context"] = {
            **report["baseline_context"],
            "source_revision": "",
        }
        return report

    monkeypatch.setattr(gate, "compute_agent_competitor_scorecard", untrusted_scorecard)

    result = gate.run_gate(
        review_queue_path=review_queue_path,
        static_only=True,
    )

    assert any(
        "calibration metadata does not match the version-controlled policy" in item
        for item in result.failures
    )
    report = result.to_dict()
    assert report["gate_passed"] is False
    assert report["scorecard_calibration"]["ready"] is False


def test_production_readiness_gate_passes_verified_behavioral_evidence(
    monkeypatch,
    review_queue_path: Path,
) -> None:
    real_e2e = gate.compute_e2e_surpass_certification

    def verified_e2e(**kwargs):
        report = real_e2e(**kwargs)
        behavior = {
            **report["behavioral"],
            "ready": True,
            "verdict": "surpassed",
            "systems": {
                "echo": {"aggregate_pass_pow_k": 1.0},
                "codex": {"aggregate_pass_pow_k": 0.96},
            },
            "checks": [],
            "next_actions": [],
        }
        report["behavioral"] = behavior
        report["summary"] = {
            **report["summary"],
            "behavioral_ready": True,
            "behavioral_echo_pass_pow_k": 1.0,
            "behavioral_codex_pass_pow_k": 0.96,
        }
        report["checks"] = [
            {**row, "passed": True} if str(row.get("id") or "").startswith("behavioral:") else row
            for row in report["checks"]
        ]
        report["ready"] = True
        report["verdict"] = "surpassed"
        report["next_actions"] = []
        return report

    monkeypatch.setattr(gate, "compute_e2e_surpass_certification", verified_e2e)

    result = gate.run_gate(review_queue_path=review_queue_path)

    assert result.failures == []
    assert not any("behavioral surpass evidence is not ready" in row for row in result.failures)
    assert result.e2e_ready is True
    assert result.e2e_verdict == "surpassed"
    assert result.e2e_behavioral["ready"] is True
    assert result.e2e_summary_text.endswith("e2e_behavioral=ready")


def test_production_readiness_gate_forwards_behavioral_bundle_path(
    monkeypatch,
    review_queue_path: Path,
    tmp_path: Path,
) -> None:
    bundle_path = tmp_path / "behavioral.json"
    captured: dict[str, object] = {}
    real_e2e = gate.compute_e2e_surpass_certification

    def capture_path(**kwargs):
        captured.update(kwargs)
        return real_e2e(**kwargs)

    monkeypatch.setattr(gate, "compute_e2e_surpass_certification", capture_path)

    gate.run_gate(
        review_queue_path=review_queue_path,
        behavioral_bundle_path=bundle_path,
    )

    assert captured["behavioral_bundle_path"] == bundle_path


def test_production_readiness_gate_prints_e2e_summary(
    monkeypatch,
    tmp_path: Path,
    capsys,
    review_queue_path: Path,
) -> None:
    monkeypatch.setenv(
        "ECHO_BEHAVIORAL_INFRASTRUCTURE_STATUS",
        str(tmp_path / "no-infrastructure-receipt.json"),
    )
    monkeypatch.setenv(
        "ECHO_BEHAVIORAL_EVAL_BUNDLE",
        str(tmp_path / "no-behavioral-bundle.json"),
    )
    code = gate.main(["--review-queue-path", str(review_queue_path)])

    captured = capsys.readouterr()

    assert code == 1
    assert "production readiness gate failed" in captured.err
    assert "behavioral:bundle_present" in captured.err


def test_production_readiness_gate_can_emit_json_summary(
    monkeypatch,
    tmp_path: Path,
    capsys,
    review_queue_path: Path,
) -> None:
    monkeypatch.setenv(
        "ECHO_BEHAVIORAL_INFRASTRUCTURE_STATUS",
        str(tmp_path / "no-infrastructure-receipt.json"),
    )
    monkeypatch.setenv(
        "ECHO_BEHAVIORAL_EVAL_BUNDLE",
        str(tmp_path / "no-behavioral-bundle.json"),
    )
    code = gate.main(
        [
            "--review-queue-path",
            str(review_queue_path),
            "--json",
        ]
    )

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert code == 1
    assert data["schema"] == "echo.production_readiness_gate.v1"
    assert data["ready"] is False
    assert data["failures"]
    assert data["scorecard_score"] == 98
    assert data["automation_score"] == 96
    assert data["e2e"]["ready"] is False
    assert data["e2e"]["verdict"] == "needs_behavioral_evidence"
    assert data["e2e"]["summary"]["scorecard_best_external"] == 97
    assert data["e2e"]["summary"]["coverage_ready"] == 7
    assert data["e2e"]["coverage"]["summary"]["gap_domain_ids"] == []
    assert "behavioral:bundle_present" in data["e2e"]["failed_checks"]
    assert data["e2e"]["behavioral"]["verdict"] == "missing_behavioral_evidence"


def test_production_readiness_gate_can_write_json_output(
    monkeypatch,
    capsys,
    review_queue_path: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "ECHO_BEHAVIORAL_INFRASTRUCTURE_STATUS",
        str(tmp_path / "no-infrastructure-receipt.json"),
    )
    monkeypatch.setenv(
        "ECHO_BEHAVIORAL_EVAL_BUNDLE",
        str(tmp_path / "no-behavioral-bundle.json"),
    )
    output_path = tmp_path / "reports" / "readiness.json"

    code = gate.main(
        [
            "--review-queue-path",
            str(review_queue_path),
            "--json-output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    data = json.loads(output_path.read_text(encoding="utf-8"))

    assert code == 1
    assert output_path.exists()
    assert "production readiness gate failed" in captured.err
    assert data["schema"] == "echo.production_readiness_gate.v1"
    assert data["ready"] is False
    assert data["e2e"]["summary"]["coverage_ready"] == 7
    assert data["e2e"]["coverage"]["summary"]["total_domains"] == 7


def test_production_readiness_gate_json_reports_failures(
    capsys,
    monkeypatch,
    review_queue_path: Path,
) -> None:
    real_e2e = gate.compute_e2e_surpass_certification

    def drifted_e2e(**kwargs):
        report = real_e2e(**kwargs)
        report["summary"] = {
            **report["summary"],
            "scorecard_best_external": 1,
        }
        return report

    monkeypatch.setattr(gate, "compute_e2e_surpass_certification", drifted_e2e)

    code = gate.main(
        [
            "--review-queue-path",
            str(review_queue_path),
            "--json",
        ]
    )

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert code == 1
    assert data["ready"] is False
    assert any(
        "e2e summary mismatch: scorecard_best_external=1, expected 97" in item
        for item in data["failures"]
    )


def test_production_readiness_gate_reports_not_ready_quality(
    monkeypatch,
    review_queue_path: Path,
) -> None:
    monkeypatch.setattr(
        gate,
        "compute_repo_context_quality",
        lambda: {
            "schema": "echo.repo_context_quality.v1",
            "ready": False,
            "score": 0.4,
            "passed": 2,
            "total": 5,
            "next_actions": ["Restore repo context evidence."],
        },
    )

    result = gate.run_gate(review_queue_path=review_queue_path)

    assert any(
        "echo.repo_context_quality.v1 is not ready" in failure for failure in result.failures
    )
    assert any(
        "echo.repo_context_quality.v1 score is 0.4" in failure for failure in result.failures
    )


def test_production_readiness_gate_blocks_scorecard_regression(
    monkeypatch,
    review_queue_path: Path,
) -> None:
    real_scorecard = gate.compute_agent_competitor_scorecard

    def degraded_scorecard(*, target_score: int):
        report = real_scorecard(target_score=target_score)
        report["evidence_adjusted_overall"]["echo"] = target_score - 1
        return report

    monkeypatch.setattr(
        gate,
        "compute_agent_competitor_scorecard",
        degraded_scorecard,
    )

    result = gate.run_gate(min_score=95, review_queue_path=review_queue_path)

    assert any(
        "agent scorecard echo evidence-adjusted overall is 94" in item for item in result.failures
    )


def test_production_readiness_gate_blocks_e2e_certification_regression(
    monkeypatch,
    review_queue_path: Path,
) -> None:
    monkeypatch.setattr(
        gate,
        "compute_e2e_surpass_certification",
        lambda **_: {
            "schema": "echo.e2e_surpass_certification.v1",
            "ready": False,
            "checks": [
                {
                    "id": "scorecard_all_dimensions_surpassed",
                    "passed": False,
                },
            ],
            "next_actions": ["Restore all-dimension surpass evidence."],
        },
    )

    result = gate.run_gate(min_score=95, review_queue_path=review_queue_path)

    assert any("e2e surpass certification is not ready" in item for item in result.failures)
    assert any(
        "e2e surpass certification checks: scorecard_all_dimensions_surpassed" in item
        for item in result.failures
    )


def test_production_readiness_gate_blocks_e2e_summary_drift(
    monkeypatch,
    review_queue_path: Path,
) -> None:
    real_e2e = gate.compute_e2e_surpass_certification

    def drifted_e2e(**kwargs):
        report = real_e2e(**kwargs)
        report["summary"] = {
            **report["summary"],
            "automation_echo": 94,
        }
        return report

    monkeypatch.setattr(gate, "compute_e2e_surpass_certification", drifted_e2e)

    result = gate.run_gate(min_score=95, review_queue_path=review_queue_path)

    assert any(
        "e2e summary mismatch: automation_echo=94, expected 96" in item for item in result.failures
    )


def test_production_readiness_gate_blocks_best_external_summary_drift(
    monkeypatch,
    review_queue_path: Path,
) -> None:
    real_e2e = gate.compute_e2e_surpass_certification

    def drifted_e2e(**kwargs):
        report = real_e2e(**kwargs)
        report["summary"] = {
            **report["summary"],
            "scorecard_best_external": 1,
        }
        return report

    monkeypatch.setattr(gate, "compute_e2e_surpass_certification", drifted_e2e)

    result = gate.run_gate(min_score=95, review_queue_path=review_queue_path)

    assert any(
        "e2e summary mismatch: scorecard_best_external=1, expected 97" in item
        for item in result.failures
    )


def test_production_readiness_gate_allows_scored_automation_focus_without_evidence_gap(
    monkeypatch,
    review_queue_path: Path,
) -> None:
    real_automation = gate.compute_automation_radar

    def automation_with_scored_focus(
        *,
        target_score: int,
        review_queue_path: str | Path | None = None,
    ):
        report = real_automation(
            target_score=target_score,
            review_queue_path=review_queue_path,
        )
        report["echo_gaps"] = [
            {
                "id": "desktop_preview_execute",
                "evidence_ready": True,
            }
        ]
        return report

    monkeypatch.setattr(gate, "compute_automation_radar", automation_with_scored_focus)

    result = gate.run_gate(min_score=95, review_queue_path=review_queue_path)

    assert not any("automation radar evidence gaps" in item for item in result.failures)


def test_production_readiness_gate_blocks_automation_evidence_gap(
    monkeypatch,
    review_queue_path: Path,
) -> None:
    real_automation = gate.compute_automation_radar

    def automation_with_missing_evidence(
        *,
        target_score: int,
        review_queue_path: str | Path | None = None,
    ):
        report = real_automation(
            target_score=target_score,
            review_queue_path=review_queue_path,
        )
        report["echo_gaps"] = [
            {
                "id": "desktop_preview_execute",
                "evidence_ready": False,
            }
        ]
        return report

    monkeypatch.setattr(gate, "compute_automation_radar", automation_with_missing_evidence)

    result = gate.run_gate(min_score=95, review_queue_path=review_queue_path)

    assert any(
        "automation radar evidence gaps: desktop_preview_execute" in item
        for item in result.failures
    )


def test_production_readiness_gate_blocks_stale_browser_replay_artifacts(
    monkeypatch,
    review_queue_path: Path,
) -> None:
    real_quality = gate.compute_browser_desktop_quality

    def browser_quality_with_stale_artifacts(
        *,
        review_queue_path: str | Path | None = None,
    ):
        report = real_quality(review_queue_path=review_queue_path)
        report["replay_trends"] = {
            **report["replay_trends"],
            "stale_source_artifact_count": 2,
            "repair_recipe_summary": {
                **report["replay_trends"]["repair_recipe_summary"],
                "total_pending_cases": 2,
                "recipe_count": 1,
            },
        }
        return report

    monkeypatch.setattr(
        gate,
        "compute_browser_desktop_quality",
        browser_quality_with_stale_artifacts,
    )

    result = gate.run_gate(min_score=95, review_queue_path=review_queue_path)

    assert any(
        "browser/desktop replay stale source artifacts: 2" in item for item in result.failures
    )
    assert any(
        "browser/desktop replay repair recipes pending: cases=2, recipes=1" in item
        for item in result.failures
    )


def test_production_readiness_gate_uses_explicit_review_queue_path(
    review_queue_path: Path,
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "missing" / "screenshot.png"
    ReviewQueue(review_queue_path).upsert_item(
        source="browser_pixel_replay_gate",
        source_kind="browser_desktop_replay",
        candidate_kind="browser_pixel_replay_gate_case",
        priority="P0",
        target_bucket="browser_desktop_replay",
        title="Review stale browser pixel replay gate",
        text="Browser pixel replay gate needs review.",
        metadata={"artifact": {"local_path": str(artifact)}},
    )

    result = gate.run_gate(review_queue_path=review_queue_path)

    assert any(
        "browser/desktop replay stale source artifacts: 1" in item for item in result.failures
    )


def test_ci_runs_production_readiness_gate_with_isolated_state() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8",
    )

    assert "Static-only readiness checks (not release proof)" in workflow
    assert "run: make production-readiness-static" in workflow
    assert "runner.temp" in workflow
    assert "ECHO_READINESS_DATA_DIR" in workflow
    assert "ECHO_READINESS_REPORT" in workflow
    assert "Upload static readiness report (not release proof)" in workflow
    assert "production-readiness-static-report" in workflow
    assert "readiness_static.json" in workflow
    assert "if-no-files-found: error" in workflow
    assert "Upload full-stack smoke proof" in workflow
    assert "full-stack-smoke-proof-${{ github.sha }}" in workflow
    assert "path: test-results/local-verify-state" in workflow
    assert 'ECHO_VERIFY_SKIP_PRODUCTION_GATE: "1"' in workflow
    assert "Upload E2E release proof" not in workflow
    assert "e2e-release-proof" not in workflow


def test_ci_audits_frontend_production_dependencies_fail_closed() -> None:
    path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["frontend"]["steps"]
    named = {step.get("name"): step for step in steps if step.get("name")}

    audit = named["Frontend production dependency audit"]
    assert audit["working-directory"] == "frontend"
    assert audit["run"] == "pnpm audit --prod"
    assert audit.get("continue-on-error") is not True
    assert next(
        i for i, step in enumerate(steps) if step.get("name") == "Install dependencies"
    ) < next(
        i
        for i, step in enumerate(steps)
        if step.get("name") == "Frontend production dependency audit"
    )


def test_makefile_exposes_isolated_production_readiness_target() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "production-readiness:" in makefile
    assert "production-readiness-static:" in makefile
    assert "ECHO_READINESS_HOME" in makefile
    assert "ECHO_READINESS_DATA_DIR" in makefile
    assert "ECHO_READINESS_REVIEW_QUEUE" in makefile
    assert "ECHO_READINESS_REPORT" in makefile
    assert ".venv/bin/python" in makefile
    assert "$${PYTHON:-" in makefile
    assert "-m scripts.production_readiness_gate" in makefile
    assert "--review-queue-path" in makefile
    assert "--json-output" in makefile
    assert "--static-only" in makefile


def test_tag_release_requires_same_sha_evidence_before_build_and_push() -> None:
    path = REPO_ROOT / ".github" / "workflows" / "release.yml"
    workflow_text = path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    jobs = workflow["jobs"]

    assert jobs["build-and-push-image"]["needs"] == [
        "build-python-distribution",
        "windows-release-proof",
    ]
    assert all(
        job["runs-on"] == "ubuntu-24.04"
        for name, job in jobs.items()
        if name != "windows-release-proof"
    )
    gate_job = jobs["release-readiness"]
    assert workflow["permissions"] == {"contents": "read"}
    assert gate_job["permissions"] == {"actions": "read", "contents": "read"}
    assert jobs["build-and-push-image"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "packages": "write",
    }
    assert jobs["create-release"]["permissions"] == {
        "actions": "read",
        "contents": "write",
    }
    assert jobs["create-release"]["needs"] == [
        "release-readiness",
        "build-and-push-image",
        "publish-python-distribution",
        "windows-release-proof",
    ]
    assert gate_job["env"]["ECHO_BEHAVIORAL_EXPECTED_REVISION"] == "${{ github.sha }}"
    assert gate_job["env"]["ECHO_BEHAVIORAL_EVAL_BUNDLE"] == (
        "benchmarks/results/behavioral-surpass-latest.json"
    )
    assert gate_job["env"]["PYTHON"] == ".venv/bin/python"
    assert gate_job["env"]["UV_PYTHON_DOWNLOADS"] == "never"
    assert all("runner.temp" not in str(value) for value in gate_job["env"].values())
    steps = {step.get("name"): step for step in gate_job["steps"] if step.get("name")}
    readiness_paths = steps["Configure isolated release readiness paths"]["run"]
    assert "${RUNNER_TEMP}/echo-release-readiness" in readiness_paths
    assert "${GITHUB_ENV}" in readiness_paths
    python_setup = next(
        step for step in gate_job["steps"] if step.get("uses") == ACTIONS_SETUP_PYTHON
    )
    assert python_setup["with"]["python-version"] == "3.11.9"
    uv_setup = steps["Set up pinned uv"]
    assert uv_setup["uses"] == ASTRAL_SETUP_UV
    assert uv_setup["with"]["version"] == "0.11.25"
    sync = steps["Sync locked release-gate dependencies"]["run"]
    assert "uv sync --locked --python 3.11.9" in sync
    assert "--extra dev --extra serve --extra web" in sync
    assert "python -m pip install" not in workflow_text
    assert "pip install -e" not in workflow_text
    version_check = steps["Verify release tag matches package versions"]
    assert version_check["env"]["RELEASE_TAG"] == "${{ github.ref_name }}"
    assert 'root / "pyproject.toml"' in version_check["run"]
    assert 'root / "frontend" / "package.json"' in version_check["run"]
    assert 'expected_tag = f"v{python_version}"' in version_check["run"]
    lookup = steps["Find successful same-SHA prerequisite runs"]["run"]
    assert "actions/workflows/behavioral-evidence.yml/runs" in lookup
    assert "actions/workflows/ci.yml/runs" in lookup
    assert "actions/workflows/build-win.yml/runs" in lookup
    assert "actions/workflows/build-mac.yml/runs" in lookup
    assert "actions/workflows/build-linux.yml/runs" in lookup
    assert 'head_sha="${GITHUB_SHA}"' in lookup
    assert ".head_sha == $sha" in lookup
    assert '[[ -z "${run_id}" ]]' in lookup
    assert '[[ -z "${ci_run_id}" ]]' in lookup
    assert '[[ -z "${windows_run_id}" ]]' in lookup
    assert '[[ -z "${macos_run_id}" ]]' in lookup
    assert '[[ -z "${linux_run_id}" ]]' in lookup
    assert "windows_run_id=${windows_run_id}" in lookup
    assert "exit 1" in lookup
    assert gate_job["outputs"]["windows-run-id"] == (
        "${{ steps.evidence-run.outputs.windows_run_id }}"
    )
    assert gate_job["outputs"]["macos-run-id"] == ("${{ steps.evidence-run.outputs.macos_run_id }}")
    assert gate_job["outputs"]["linux-run-id"] == ("${{ steps.evidence-run.outputs.linux_run_id }}")
    download = steps["Download same-SHA behavioral evidence"]
    assert download["uses"] == ACTIONS_DOWNLOAD_ARTIFACT
    assert download["with"]["name"] == "behavioral-surpass-evidence-${{ github.sha }}"
    assert download["with"]["run-id"] == "${{ steps.evidence-run.outputs.run_id }}"
    smoke_download = steps["Download same-SHA full-stack smoke evidence"]
    assert smoke_download["uses"] == ACTIONS_DOWNLOAD_ARTIFACT
    assert smoke_download["with"]["name"] == "full-stack-smoke-proof-${{ github.sha }}"
    assert smoke_download["with"]["run-id"] == "${{ steps.evidence-run.outputs.ci_run_id }}"
    full_gate = steps["Run full commit-bound production readiness gate"]["run"]
    assert "make production-readiness" in full_gate
    assert "--static-only" not in full_gate
    certificate = steps["Build release certificate from readiness and browser evidence"]["run"]
    assert ".venv/bin/python scripts/e2e_release_proof.py" in certificate
    assert "--required-suite full-stack-desktop" in certificate
    assert "--required-suite full-stack-mobile" in certificate
    windows_job = jobs["windows-release-proof"]
    assert windows_job["needs"] == "release-readiness"
    assert windows_job["runs-on"] == "windows-2025"
    assert windows_job["permissions"] == {"actions": "read", "contents": "read"}
    assert set(windows_job["env"]) == {"WINDOWS_BUILD_RUN_ID"}
    assert windows_job["env"]["WINDOWS_BUILD_RUN_ID"] == (
        "${{ needs.release-readiness.outputs.windows-run-id }}"
    )
    windows_steps = {step.get("name"): step for step in windows_job["steps"] if step.get("name")}
    installer_download = windows_steps["Download same-SHA signed installer"]
    assert installer_download["uses"] == ACTIONS_DOWNLOAD_ARTIFACT
    assert installer_download["with"]["name"] == ("Echo-Setup-Windows-${{ github.sha }}")
    assert installer_download["with"]["run-id"] == "${{ env.WINDOWS_BUILD_RUN_ID }}"
    portable_download = windows_steps["Download same-SHA signed unpacked application"]
    assert portable_download["with"]["name"] == ("Echo-Portable-Windows-${{ github.sha }}")
    assert portable_download["with"]["run-id"] == "${{ env.WINDOWS_BUILD_RUN_ID }}"
    windows_verify = windows_steps[
        "Verify checksums, Authenticode, timestamps, and source revision"
    ]["run"]
    assert "SHA256SUMS" in windows_verify
    assert "Get-FileHash" in windows_verify
    assert "Get-AuthenticodeSignature" in windows_verify
    assert "SignatureStatus]::Valid" in windows_verify
    assert "TimeStamperCertificate" in windows_verify
    assert "$buildProof.sourceRevision -ne $env:GITHUB_SHA" in windows_verify
    assert "windows-release-verification.json" in windows_verify
    verified_upload = windows_steps["Upload verified Windows release assets"]
    assert verified_upload["with"]["name"] == ("Echo-Windows-Release-${{ github.sha }}")
    changelog = {
        step.get("name"): step for step in jobs["create-release"]["steps"] if step.get("name")
    }["Extract changelog section for ${{ steps.tag.outputs.version }}"]
    assert changelog["env"]["RELEASE_VERSION"] == "${{ steps.tag.outputs.version }}"
    assert "${{ steps.tag.outputs.version }}" not in changelog["run"]
    release_steps = {
        step.get("name"): step for step in jobs["create-release"]["steps"] if step.get("name")
    }
    release_download = release_steps["Download verified Windows release assets"]
    assert release_download["uses"] == ACTIONS_DOWNLOAD_ARTIFACT
    assert release_download["with"]["name"] == ("Echo-Windows-Release-${{ github.sha }}")
    macos_download = release_steps["Download signed macOS release assets"]
    assert macos_download["with"]["name"] == "Echo-Setup-macOS-${{ github.sha }}"
    assert macos_download["with"]["run-id"] == (
        "${{ needs.release-readiness.outputs.macos-run-id }}"
    )
    linux_download = release_steps["Download Linux release assets"]
    assert linux_download["with"]["name"] == "Echo-Setup-Linux-${{ github.sha }}"
    assert linux_download["with"]["run-id"] == (
        "${{ needs.release-readiness.outputs.linux-run-id }}"
    )
    staging = release_steps["Stage three-platform commit-bound release assets"]["run"]
    assert '== *"${GITHUB_SHA}"*' in staging
    assert "sha256sum -c SHA256SUMS-macOS" in staging
    assert "sha256sum -c SHA256SUMS" in staging
    assert "SHA256SUMS-Windows" in staging
    assert "SHA256SUMS-macOS" in staging
    assert "SHA256SUMS-Linux" in staging
    draft = release_steps["Create draft GitHub Release"]
    assert draft["with"]["draft"] is True
    assert "release-assets/Echo-Setup-*.exe" in draft["with"]["files"]
    assert "release-assets/Echo-Setup-macOS-*.dmg" in draft["with"]["files"]
    assert "release-assets/Echo-Setup-Linux-*.AppImage" in draft["with"]["files"]
    assert "release-assets/SHA256SUMS-Windows" in draft["with"]["files"]
    assert "release-assets/SHA256SUMS-macOS" in draft["with"]["files"]
    assert "release-assets/SHA256SUMS-Linux" in draft["with"]["files"]
    assert draft["with"]["fail_on_unmatched_files"] is True
    assert "win-unpacked" not in draft["with"]["files"]
    image_steps = {
        step.get("name"): step for step in jobs["build-and-push-image"]["steps"] if step.get("name")
    }
    image_build = image_steps["Build and push image"]
    assert image_build["with"]["push"] is True
    assert image_build["with"]["platforms"] == "linux/amd64,linux/arm64"
    assert image_build["with"]["provenance"] == "mode=max"
    assert image_build["with"]["sbom"] is True
    assert image_build["with"]["tags"] == (
        "ghcr.io/${{ steps.repo.outputs.name }}:${{ steps.tag.outputs.name }}"
    )
    assert ":latest" not in str(image_build["with"]["tags"])
    cosign_install = image_steps["Install pinned cosign"]
    assert cosign_install["uses"] == SIGSTORE_COSIGN_INSTALLER
    assert cosign_install["with"] == {"cosign-release": "v2.6.1"}
    signature = image_steps["Sign and verify immutable image manifest"]
    assert signature["env"]["IMAGE_DIGEST"] == "${{ steps.image-build.outputs.digest }}"
    assert signature["env"]["CERTIFICATE_IDENTITY"] == (
        "https://github.com/${{ github.workflow_ref }}"
    )
    assert 'cosign sign --yes "${image}"' in signature["run"]
    assert "cosign verify" in signature["run"]
    assert '--certificate-identity "${CERTIFICATE_IDENTITY}"' in signature["run"]
    assert "https://token.actions.githubusercontent.com" in signature["run"]
    assert '--certificate-github-workflow-sha "${GITHUB_SHA}"' in signature["run"]
    assert 'docker-manifest-digest"] == $digest' in signature["run"]


def test_tag_release_builds_and_publishes_commit_bound_python_distribution() -> None:
    path = REPO_ROOT / ".github" / "workflows" / "release.yml"
    workflow_text = path.read_text(encoding="utf-8")
    jobs = yaml.safe_load(workflow_text)["jobs"]

    build = jobs["build-python-distribution"]
    assert build["needs"] == ["release-readiness", "windows-release-proof"]
    assert build["permissions"] == {"contents": "read"}
    build_steps = {step.get("name"): step for step in build["steps"] if step.get("name")}
    assert build_steps["Sync locked release validation tools"]["run"] == (
        "uv sync --locked --python 3.11.9 --extra release"
    )
    assert build_steps["Build wheel and source distribution"]["run"] == (
        "uv build --no-sources --out-dir dist"
    )
    validation = build_steps["Validate metadata and isolated wheel imports"]["run"]
    assert "twine check --strict dist/*" in validation
    assert 'metadata["Name"] != "echo-agent-runtime"' in validation
    assert 'tomllib.load(handle)["project"]["version"]' in validation
    assert "uv pip install" in validation
    assert "--no-deps" in validation
    assert ".venv/bin/python -P" in validation
    assert "module escaped isolated wheel" in validation
    upload = build_steps["Upload commit-bound Python distribution"]
    assert upload["uses"] == ACTIONS_UPLOAD_ARTIFACT
    assert upload["with"]["name"] == "python-distribution-${{ github.sha }}"
    assert upload["with"]["if-no-files-found"] == "error"

    publish = jobs["publish-python-distribution"]
    assert publish["needs"] == [
        "build-python-distribution",
        "build-and-push-image",
        "windows-release-proof",
    ]
    assert publish["environment"] == {
        "name": "pypi",
        "url": "https://pypi.org/p/echo-agent-runtime",
    }
    assert publish["permissions"] == {"contents": "read", "id-token": "write"}
    publish_steps = {step.get("name"): step for step in publish["steps"] if step.get("name")}
    download = publish_steps["Download commit-bound Python distribution"]
    assert download["uses"] == ACTIONS_DOWNLOAD_ARTIFACT
    assert download["with"] == {
        "name": "python-distribution-${{ github.sha }}",
        "path": "dist",
    }
    publisher = publish_steps["Publish signed attestations to PyPI"]
    assert publisher["uses"] == PYPA_PUBLISH
    assert publisher["with"] == {
        "packages-dir": "dist/",
        "verify-metadata": True,
        "attestations": True,
        "print-hash": True,
    }
    assert "password" not in publisher.get("with", {})


def test_windows_artifact_workflow_is_signed_and_commit_bound() -> None:
    workflow_path = REPO_ROOT / ".github" / "workflows" / "build-win.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)

    assert workflow["permissions"] == {"contents": "read"}
    assert "secrets.GITHUB_TOKEN" not in workflow_text
    job = workflow["jobs"]["build-win"]
    assert job["runs-on"] == "windows-2025"
    assert job["environment"] == "windows-code-signing"
    assert "CSC_LINK" not in job["env"]
    assert "CSC_KEY_PASSWORD" not in job["env"]
    steps = {step.get("name"): step for step in job["steps"] if step.get("name")}

    identity = steps["Validate protected Windows signing identity"]
    assert identity["env"]["CSC_LINK"] == ("${{ secrets.WINDOWS_CODE_SIGNING_CERTIFICATE_BASE64 }}")
    assert identity["env"]["CSC_KEY_PASSWORD"] == (
        "${{ secrets.WINDOWS_CODE_SIGNING_CERTIFICATE_PASSWORD }}"
    )
    assert "IsNullOrWhiteSpace" in identity["run"]
    assert "HasPrivateKey" in identity["run"]
    assert "1.3.6.1.5.5.7.3.3" in identity["run"]

    electron_build = steps["Build canonical Electron EXE"]
    assert electron_build["env"] == identity["env"]
    signature_check = steps["Verify Authenticode signatures and create commit-bound checksums"][
        "run"
    ]
    assert "win-unpacked/Echo.exe" in signature_check
    assert "win-unpacked/resources/backend/echo-backend.exe" in signature_check
    assert "win-unpacked/resources/codex/bin/codex.exe" in signature_check
    assert "Get-AuthenticodeSignature" in signature_check
    assert "SignatureStatus]::Valid" in signature_check
    assert "TimeStamperCertificate" in signature_check
    assert "Get-FileHash" in signature_check
    assert "SHA256SUMS" in signature_check
    assert "windows-signing-proof.json" in signature_check
    assert "$env:GITHUB_SHA" in signature_check

    installer_upload = steps["Upload EXE installer"]
    assert installer_upload["with"]["name"] == ("Echo-Setup-Windows-${{ github.sha }}")
    assert "frontend/release/SHA256SUMS" in installer_upload["with"]["path"]
    assert "frontend/release/windows-signing-proof.json" in installer_upload["with"]["path"]
    portable_upload = steps["Upload portable (unpacked)"]
    assert portable_upload["with"]["name"] == ("Echo-Portable-Windows-${{ github.sha }}")

    build_config = yaml.safe_load(
        (REPO_ROOT / "packaging" / "desktop" / "build.yml").read_text(encoding="utf-8")
    )
    assert build_config["win"]["forceCodeSigning"] is True
    assert build_config["win"]["signExts"] == [".exe"]
    assert build_config["win"]["artifactName"] == (
        "${productName}-Setup-${version}-${env.GITHUB_SHA}.${ext}"
    )
    assert build_config["win"]["signtoolOptions"] == {
        "signingHashAlgorithms": ["sha256"],
        "rfc3161TimeStampServer": "http://timestamp.digicert.com",
    }


def test_manual_behavioral_workflow_produces_commit_bound_release_artifact() -> None:
    path = REPO_ROOT / ".github" / "workflows" / "behavioral-evidence.yml"
    workflow_text = path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    job = workflow["jobs"]["behavioral-evidence"]

    assert "workflow_dispatch:" in workflow_text
    assert "inputs:" not in workflow_text
    assert job["runs-on"] == ["self-hosted", "macOS", "behavioral-evidence"]
    assert job["environment"] == "behavioral-evidence"
    assert job["env"]["ECHO_BEHAVIORAL_EXPECTED_REVISION"] == "${{ github.sha }}"
    assert job["env"]["ECHO_CODEX_EXECUTABLE"] == (
        "/Applications/ChatGPT.app/Contents/Resources/codex"
    )
    assert "ECHO_API_TOKEN" not in job["env"]
    assert "ECHO_EVAL_LOCAL_PASSWORD" not in job["env"]
    for name in (
        "ECHO_EVAL_CONFIG",
        "ECHO_EVAL_ECHO_MODEL",
        "ECHO_EVAL_CODEX_MODEL",
        "ECHO_EVAL_EXPECTED_CONFIG_SHA256",
        "ECHO_EVAL_EXPECTED_CODEX_SHA256",
        "ECHO_EVAL_EXPECTED_CODEX_TEAM_ID",
        "ECHO_EVAL_EXPECTED_CODEX_IDENTIFIER",
    ):
        assert "${{ vars." in job["env"][name]
    steps = {step.get("name"): step for step in job["steps"] if step.get("name")}
    python_setup = next(step for step in job["steps"] if step.get("uses") == ACTIONS_SETUP_PYTHON)
    assert python_setup["with"]["python-version"] == "3.11.9"
    uv_setup = steps["Set up pinned uv"]
    assert uv_setup["uses"] == ASTRAL_SETUP_UV
    assert uv_setup["with"]["version"] == "0.11.25"
    sync = steps["Sync locked evaluation dependencies"]["run"]
    assert "uv sync --locked --python 3.11.9" in sync
    assert "--extra dev --extra serve --extra web" in sync
    assert job["env"]["UV_PYTHON_DOWNLOADS"] == "never"
    assert "pip install" not in workflow_text
    identity = steps["Verify protected behavioral identities"]["run"]
    assert "codesign --verify --strict" in identity
    assert "TeamIdentifier=" in identity
    assert "Identifier=" in identity
    assert "shasum -a 256" in identity
    assert 'f"benchmarks/results/{name}-provenance.json"' in identity
    startup = steps["Start isolated Echo evaluation server"]["run"]
    assert "/readyz" in startup
    assert "/api/health" not in startup
    assert ".venv/bin/python -m runtime serve" in startup
    echo_run = steps["Run fixed suite against Echo"]["run"]
    codex_run = steps["Run identical fixed suite against Codex Desktop"]["run"]
    assert ".venv/bin/python -m benchmarks.run_behavioral_suite" in echo_run
    assert ".venv/bin/python -m benchmarks.run_behavioral_suite" in codex_run
    assert "--system echo" in echo_run
    assert "--system codex" in codex_run
    assert '--model "${ECHO_EVAL_ECHO_MODEL}"' in echo_run
    assert '--model "${ECHO_EVAL_CODEX_MODEL}"' in codex_run
    assert "--provenance-file benchmarks/results/echo-provenance.json" in echo_run
    assert "--provenance-file benchmarks/results/codex-provenance.json" in codex_run
    assert '--echo-config-path "${ECHO_EVAL_CONFIG}"' in echo_run
    assert "--codex-surface desktop" in codex_run
    assert "--k 3" in echo_run and "--k 3" in codex_run
    assert "status > 1" in echo_run and "status > 1" in codex_run
    assert steps["Run fixed suite against Echo"]["env"] == {
        "ECHO_API_TOKEN": "${{ secrets.ECHO_API_TOKEN }}",
        "ECHO_EVAL_LOCAL_PASSWORD": "${{ secrets.ECHO_EVAL_LOCAL_PASSWORD }}",
    }
    assert "env" not in steps["Run identical fixed suite against Codex Desktop"]
    assemble = steps["Assemble and validate commit-bound evidence"]["run"]
    assert ".venv/bin/python -m benchmarks.assemble_behavioral_bundle" in assemble
    assert '--source-revision "${GITHUB_SHA}"' in assemble
    assert steps["Run full production readiness gate"]["run"] == ("make production-readiness")
    assert steps["Run full production readiness gate"]["env"]["PYTHON"] == ".venv/bin/python"
    upload = steps["Upload commit-bound behavioral evidence"]
    assert upload["uses"] == ACTIONS_UPLOAD_ARTIFACT
    assert upload["with"]["name"] == "behavioral-surpass-evidence-${{ github.sha }}"
    assert "benchmarks/results/behavioral-artifacts" in upload["with"]["path"]
    assert "benchmarks/results/echo-provenance.json" in upload["with"]["path"]
    assert "benchmarks/results/codex-provenance.json" in upload["with"]["path"]


def test_verify_local_persists_production_readiness_report() -> None:
    script = (REPO_ROOT / "scripts" / "verify_local.sh").read_text(
        encoding="utf-8",
    )

    assert "VERIFY_READINESS_REPORT" in script
    assert "VERIFY_FULL_STACK_PROOF" in script
    assert "VERIFY_E2E_RELEASE_PROOF" in script
    assert "production_readiness_gate.json" in script
    assert "full_stack_smoke_proof.json" in script
    assert "e2e_release_proof.json" in script
    assert '--json-output "$VERIFY_READINESS_REPORT"' in script
    assert "readiness report: $VERIFY_READINESS_REPORT" in script
    assert "scripts/e2e_smoke_proof.py" in script
    assert "scripts/e2e_release_proof.py" in script
    assert "tests/test_e2e_smoke_proof.py" in script
    assert "tests/test_e2e_release_proof.py" in script
    assert "full-stack-desktop" in script
    assert "full-stack-mobile" in script
    assert "full-stack smoke proof: $VERIFY_FULL_STACK_PROOF" in script
    assert "e2e release proof: $VERIFY_E2E_RELEASE_PROOF" in script


def test_pr_template_points_reviewers_to_static_readiness() -> None:
    template = (REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text(
        encoding="utf-8",
    )

    assert "make production-readiness-static" in template
    assert "python scripts/production_readiness_gate.py" not in template
