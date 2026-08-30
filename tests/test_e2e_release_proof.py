from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_e2e_release_proof_merges_readiness_and_full_stack(tmp_path: Path) -> None:
    readiness = tmp_path / "readiness.json"
    full_stack = tmp_path / "full_stack.json"
    output = tmp_path / "release.json"
    readiness.write_text(json.dumps(_readiness()), encoding="utf-8")
    full_stack.write_text(json.dumps(_full_stack(report_root=tmp_path)), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_release_proof.py",
            "--readiness",
            str(readiness),
            "--full-stack",
            str(full_stack),
            "--output",
            str(output),
            "--required-suite",
            "full-stack-desktop",
            "--required-suite",
            "full-stack-mobile",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    data = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 0, result.stderr
    assert data["schema"] == "echo.e2e_release_proof.v1"
    assert data["ready"] is True
    assert data["verdict"] == "release_ready"
    assert data["failed_checks"] == []
    assert data["summary"]["scorecard_score"] == 97
    assert data["summary"]["automation_score"] == 95
    assert data["summary"]["coverage_ready"] == 7
    assert data["summary"]["coverage_total"] == 7
    assert data["summary"]["coverage_gap_domains"] == 0
    assert data["summary"]["full_stack_run_id"] == "proof-run-1"
    assert data["summary"]["full_stack_suite_count"] == 2
    assert data["summary"]["full_stack_test_file_count"] == 5
    assert data["summary"]["full_stack_test_case_count"] == 17
    assert data["summary"]["full_stack_passed_test_count"] == 16
    assert data["summary"]["required_suite_test_file_counts"] == {
        "full-stack-desktop": 4,
        "full-stack-mobile": 1,
    }
    assert data["summary"]["required_suite_passed_test_counts"] == {
        "full-stack-desktop": 13,
        "full-stack-mobile": 3,
    }
    assert data["summary"]["required_suite_failed_test_counts"] == {
        "full-stack-desktop": 0,
        "full-stack-mobile": 0,
    }
    assert data["summary"]["required_suite_skipped_test_counts"] == {
        "full-stack-desktop": 1,
        "full-stack-mobile": 0,
    }
    assert data["summary"]["required_suite_skipped_tests"] == {
        "full-stack-desktop": [_allowed_skipped_test()],
        "full-stack-mobile": [],
    }
    assert data["summary"]["required_suite_run_ids"] == {
        "full-stack-desktop": "proof-run-1",
        "full-stack-mobile": "proof-run-1",
    }
    assert data["summary"]["required_suite_state_root_present"] == {
        "full-stack-desktop": True,
        "full-stack-mobile": True,
    }
    assert data["summary"]["required_suite_playwright_report_present"] == {
        "full-stack-desktop": True,
        "full-stack-mobile": True,
    }
    assert data["summary"]["required_suite_playwright_report_valid"] == {
        "full-stack-desktop": True,
        "full-stack-mobile": True,
    }
    assert (
        len(data["summary"]["required_suite_playwright_report_sha256"]["full-stack-desktop"]) == 64
    )


def test_e2e_release_proof_accepts_relocated_relative_ci_bundle(tmp_path: Path) -> None:
    producer = tmp_path / "producer"
    full_stack_data = _full_stack(report_root=producer)
    for row in full_stack_data["suites"]:
        row["state_root"] = str(Path(str(row["state_root"])).relative_to(producer))
        row["playwright_report"] = str(Path(str(row["playwright_report"])).relative_to(producer))
    (producer / "full_stack_smoke_proof.json").write_text(
        json.dumps(full_stack_data),
        encoding="utf-8",
    )

    consumer = tmp_path / "downloaded-artifact"
    shutil.copytree(producer, consumer)
    shutil.rmtree(producer)
    readiness = tmp_path / "readiness.json"
    readiness.write_text(json.dumps(_readiness()), encoding="utf-8")
    output = tmp_path / "release.json"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_release_proof.py",
            "--readiness",
            str(readiness),
            "--full-stack",
            str(consumer / "full_stack_smoke_proof.json"),
            "--output",
            str(output),
            "--required-suite",
            "full-stack-desktop",
            "--required-suite",
            "full-stack-mobile",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["ready"] is True


def test_e2e_release_proof_requires_all_named_full_stack_suites(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "readiness.json"
    full_stack = tmp_path / "full_stack.json"
    output = tmp_path / "release.json"
    readiness.write_text(json.dumps(_readiness()), encoding="utf-8")
    full_stack.write_text(
        json.dumps(_full_stack(report_root=tmp_path, suites=("full-stack-desktop",))),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_release_proof.py",
            "--readiness",
            str(readiness),
            "--full-stack",
            str(full_stack),
            "--output",
            str(output),
            "--required-suite",
            "full-stack-desktop",
            "--required-suite",
            "full-stack-mobile",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    data = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert data["ready"] is False
    assert data["verdict"] == "needs_work"
    assert "full_stack_required_suites_present" in data["failed_checks"]
    assert data["summary"]["missing_suites"] == ["full-stack-mobile"]


def test_e2e_release_proof_rejects_weak_readiness_artifact(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "readiness.json"
    full_stack = tmp_path / "full_stack.json"
    output = tmp_path / "release.json"
    readiness.write_text(
        json.dumps(
            _readiness(
                schema="echo.fake_readiness.v1",
                scorecard_score=94,
                automation_score=94,
                coverage_gap_domains=1,
            )
        ),
        encoding="utf-8",
    )
    full_stack.write_text(json.dumps(_full_stack(report_root=tmp_path)), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_release_proof.py",
            "--readiness",
            str(readiness),
            "--full-stack",
            str(full_stack),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    data = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert data["ready"] is False
    assert {
        "production_readiness_schema",
        "production_readiness_scores_clear_target",
        "production_readiness_coverage_has_no_gaps",
    } <= set(data["failed_checks"])


def test_e2e_release_proof_rejects_static_only_readiness_report(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "readiness.json"
    full_stack = tmp_path / "full_stack.json"
    output = tmp_path / "release.json"
    readiness.write_text(
        json.dumps(_readiness(release_proof=False, mode="static_only")),
        encoding="utf-8",
    )
    full_stack.write_text(json.dumps(_full_stack(report_root=tmp_path)), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_release_proof.py",
            "--readiness",
            str(readiness),
            "--full-stack",
            str(full_stack),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    data = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert data["ready"] is False
    assert "production_readiness_release_proof" in data["failed_checks"]


def test_e2e_release_proof_rejects_inconsistent_full_stack_counts(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "readiness.json"
    full_stack = tmp_path / "full_stack.json"
    output = tmp_path / "release.json"
    readiness.write_text(json.dumps(_readiness()), encoding="utf-8")
    full_stack.write_text(
        json.dumps(_full_stack(report_root=tmp_path, suite_count=5, passed_count=1)),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_release_proof.py",
            "--readiness",
            str(readiness),
            "--full-stack",
            str(full_stack),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    data = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert data["ready"] is False
    assert "full_stack_suite_counts_consistent" in data["failed_checks"]


def test_e2e_release_proof_rejects_weak_required_suite_coverage(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "readiness.json"
    full_stack = tmp_path / "full_stack.json"
    output = tmp_path / "release.json"
    readiness.write_text(json.dumps(_readiness()), encoding="utf-8")
    full_stack.write_text(
        json.dumps(
            _full_stack(
                report_root=tmp_path,
                suite_test_matches={
                    "full-stack-desktop": ("full-stack-smoke.spec.ts",),
                    "full-stack-mobile": ("mobile-smoke.spec.ts",),
                },
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_release_proof.py",
            "--readiness",
            str(readiness),
            "--full-stack",
            str(full_stack),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    data = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert data["ready"] is False
    assert "full_stack_required_suites_have_test_coverage" in data["failed_checks"]
    assert data["summary"]["weak_suite_test_coverage"] == ["full-stack-desktop"]


def test_e2e_release_proof_rejects_missing_required_playwright_report(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "readiness.json"
    full_stack = tmp_path / "full_stack.json"
    output = tmp_path / "release.json"
    readiness.write_text(json.dumps(_readiness()), encoding="utf-8")
    full_stack.write_text(
        json.dumps(
            _full_stack(
                report_root=tmp_path,
                suite_report_presence={
                    "full-stack-desktop": False,
                    "full-stack-mobile": True,
                },
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_release_proof.py",
            "--readiness",
            str(readiness),
            "--full-stack",
            str(full_stack),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    data = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert data["ready"] is False
    assert "full_stack_required_suites_have_playwright_reports" in data["failed_checks"]
    assert data["summary"]["suites_missing_playwright_reports"] == ["full-stack-desktop"]


def test_e2e_release_proof_rejects_missing_required_playwright_report_file(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "readiness.json"
    full_stack = tmp_path / "full_stack.json"
    output = tmp_path / "release.json"
    readiness.write_text(json.dumps(_readiness()), encoding="utf-8")
    full_stack.write_text(
        json.dumps(
            _full_stack(
                report_root=tmp_path,
                write_report_files={
                    "full-stack-desktop": False,
                    "full-stack-mobile": True,
                },
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_release_proof.py",
            "--readiness",
            str(readiness),
            "--full-stack",
            str(full_stack),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    data = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert data["ready"] is False
    assert "full_stack_required_suites_have_playwright_report_files" in data["failed_checks"]
    assert data["summary"]["suites_missing_playwright_report_files"] == ["full-stack-desktop"]


def test_e2e_release_proof_rejects_mismatched_playwright_report_counts(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "readiness.json"
    full_stack = tmp_path / "full_stack.json"
    output = tmp_path / "release.json"
    readiness.write_text(json.dumps(_readiness()), encoding="utf-8")
    full_stack.write_text(
        json.dumps(
            _full_stack(
                report_root=tmp_path,
                suite_report_stats={
                    "full-stack-desktop": {
                        "expected": 12,
                        "skipped": 1,
                        "unexpected": 0,
                        "flaky": 0,
                    },
                    "full-stack-mobile": {
                        "expected": 3,
                        "skipped": 0,
                        "unexpected": 0,
                        "flaky": 0,
                    },
                },
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_release_proof.py",
            "--readiness",
            str(readiness),
            "--full-stack",
            str(full_stack),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    data = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert data["ready"] is False
    assert "full_stack_playwright_report_counts_match" in data["failed_checks"]
    assert data["summary"]["suites_with_mismatched_playwright_report_counts"] == [
        "full-stack-desktop"
    ]


def test_e2e_release_proof_rejects_mismatched_playwright_report_hash(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "readiness.json"
    full_stack = tmp_path / "full_stack.json"
    output = tmp_path / "release.json"
    readiness.write_text(json.dumps(_readiness()), encoding="utf-8")
    full_stack.write_text(
        json.dumps(
            _full_stack(
                report_root=tmp_path,
                suite_report_hash_overrides={
                    "full-stack-desktop": "0" * 64,
                },
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_release_proof.py",
            "--readiness",
            str(readiness),
            "--full-stack",
            str(full_stack),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    data = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert data["ready"] is False
    assert "full_stack_playwright_report_hashes_match" in data["failed_checks"]
    assert data["summary"]["suites_with_mismatched_playwright_report_hashes"] == [
        "full-stack-desktop"
    ]


def test_e2e_release_proof_rejects_mismatched_suite_run_id(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "readiness.json"
    full_stack = tmp_path / "full_stack.json"
    output = tmp_path / "release.json"
    readiness.write_text(json.dumps(_readiness()), encoding="utf-8")
    full_stack.write_text(
        json.dumps(
            _full_stack(
                report_root=tmp_path,
                suite_run_ids={"full-stack-mobile": "old-run"},
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_release_proof.py",
            "--readiness",
            str(readiness),
            "--full-stack",
            str(full_stack),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    data = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert data["ready"] is False
    assert "full_stack_suite_run_ids_match" in data["failed_checks"]
    assert data["summary"]["suites_with_mismatched_run_ids"] == ["full-stack-mobile"]


def test_e2e_release_proof_rejects_incomplete_skipped_test_inventory(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "readiness.json"
    full_stack = tmp_path / "full_stack.json"
    output = tmp_path / "release.json"
    readiness.write_text(json.dumps(_readiness()), encoding="utf-8")
    full_stack.write_text(
        json.dumps(
            _full_stack(
                report_root=tmp_path,
                suite_skipped_counts={"full-stack-desktop": 1},
                suite_skipped_tests={"full-stack-desktop": []},
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_release_proof.py",
            "--readiness",
            str(readiness),
            "--full-stack",
            str(full_stack),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    data = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert data["ready"] is False
    assert "full_stack_skipped_test_inventory_complete" in data["failed_checks"]
    assert data["summary"]["suites_with_incomplete_skipped_test_inventory"] == [
        "full-stack-desktop"
    ]


def test_e2e_release_proof_rejects_unexpected_skipped_test(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "readiness.json"
    full_stack = tmp_path / "full_stack.json"
    output = tmp_path / "release.json"
    readiness.write_text(json.dumps(_readiness()), encoding="utf-8")
    unexpected = {
        "file": "workflow-editor.spec.ts",
        "title": "Workflow Editor › hidden broken editor path",
        "reason": "temporarily disabled",
        "line": 10,
    }
    full_stack.write_text(
        json.dumps(
            _full_stack(
                report_root=tmp_path,
                suite_skipped_counts={"full-stack-desktop": 2},
                suite_skipped_tests={
                    "full-stack-desktop": [_allowed_skipped_test(), unexpected],
                },
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_release_proof.py",
            "--readiness",
            str(readiness),
            "--full-stack",
            str(full_stack),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    data = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert data["ready"] is False
    assert "full_stack_skipped_tests_are_expected" in data["failed_checks"]
    assert data["summary"]["suites_with_unexpected_skipped_tests"] == ["full-stack-desktop"]
    assert data["summary"]["unexpected_skipped_tests_by_suite"] == {
        "full-stack-desktop": [unexpected]
    }


def test_e2e_release_proof_rejects_missing_required_state_root(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "readiness.json"
    full_stack = tmp_path / "full_stack.json"
    output = tmp_path / "release.json"
    readiness.write_text(json.dumps(_readiness()), encoding="utf-8")
    full_stack.write_text(
        json.dumps(
            _full_stack(
                report_root=tmp_path,
                create_state_roots={"full-stack-desktop": False},
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_release_proof.py",
            "--readiness",
            str(readiness),
            "--full-stack",
            str(full_stack),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    data = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert data["ready"] is False
    assert "full_stack_required_suites_have_state_roots" in data["failed_checks"]
    assert data["summary"]["suites_missing_state_roots"] == ["full-stack-desktop"]


def test_e2e_release_proof_rejects_weak_required_passed_test_count(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "readiness.json"
    full_stack = tmp_path / "full_stack.json"
    output = tmp_path / "release.json"
    readiness.write_text(json.dumps(_readiness()), encoding="utf-8")
    full_stack.write_text(
        json.dumps(
            _full_stack(
                report_root=tmp_path,
                suite_passed_counts={
                    "full-stack-desktop": 12,
                    "full-stack-mobile": 3,
                },
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_release_proof.py",
            "--readiness",
            str(readiness),
            "--full-stack",
            str(full_stack),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    data = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert data["ready"] is False
    assert "full_stack_required_suites_have_passed_tests" in data["failed_checks"]
    assert data["summary"]["weak_suite_passed_tests"] == ["full-stack-desktop"]


def test_e2e_release_proof_rejects_failed_required_tests(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "readiness.json"
    full_stack = tmp_path / "full_stack.json"
    output = tmp_path / "release.json"
    readiness.write_text(json.dumps(_readiness()), encoding="utf-8")
    full_stack.write_text(
        json.dumps(
            _full_stack(
                report_root=tmp_path,
                suite_failed_counts={
                    "full-stack-desktop": 1,
                    "full-stack-mobile": 0,
                },
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_release_proof.py",
            "--readiness",
            str(readiness),
            "--full-stack",
            str(full_stack),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    data = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert data["ready"] is False
    assert "full_stack_required_suites_have_no_failed_tests" in data["failed_checks"]
    assert data["summary"]["suites_with_failed_tests"] == ["full-stack-desktop"]


def _readiness(
    *,
    schema: str = "echo.production_readiness_gate.v1",
    scorecard_score: int = 97,
    automation_score: int = 95,
    coverage_gap_domains: int = 0,
    release_proof: bool = True,
    mode: str = "full",
) -> dict[str, object]:
    return {
        "schema": schema,
        "ready": True,
        "mode": mode,
        "release_proof": release_proof,
        "expected_revision": "test-revision",
        "scorecard_score": scorecard_score,
        "automation_score": automation_score,
        "e2e": {
            "ready": True,
            "verdict": "surpassed",
            "summary": {
                "coverage_ready": 7,
                "coverage_total": 7,
                "coverage_gap_domains": coverage_gap_domains,
            },
        },
    }


def _full_stack(
    *,
    report_root: Path,
    run_id: str = "proof-run-1",
    suites: tuple[str, ...] = ("full-stack-desktop", "full-stack-mobile"),
    suite_count: int | None = None,
    passed_count: int | None = None,
    suite_test_matches: dict[str, tuple[str, ...]] | None = None,
    suite_passed_counts: dict[str, int] | None = None,
    suite_failed_counts: dict[str, int] | None = None,
    suite_skipped_counts: dict[str, int] | None = None,
    suite_skipped_tests: dict[str, list[dict[str, object]]] | None = None,
    suite_row_skipped_tests: dict[str, list[dict[str, object]]] | None = None,
    suite_run_ids: dict[str, str] | None = None,
    suite_report_presence: dict[str, bool] | None = None,
    suite_report_stats: dict[str, dict[str, int]] | None = None,
    suite_report_hash_overrides: dict[str, str] | None = None,
    create_state_roots: dict[str, bool] | None = None,
    write_report_files: dict[str, bool] | None = None,
) -> dict[str, object]:
    default_test_matches = {
        "full-stack-desktop": (
            "full-stack-smoke.spec.ts",
            "chat.spec.ts",
            "regression.spec.ts",
            "workflow-editor.spec.ts",
        ),
        "full-stack-mobile": ("mobile-smoke.spec.ts",),
    }
    test_matches_by_suite = suite_test_matches or default_test_matches
    passed_counts = suite_passed_counts or {
        "full-stack-desktop": 13,
        "full-stack-mobile": 3,
    }
    failed_counts = suite_failed_counts or {
        "full-stack-desktop": 0,
        "full-stack-mobile": 0,
    }
    skipped_counts = suite_skipped_counts or {
        "full-stack-desktop": 1,
        "full-stack-mobile": 0,
    }
    skipped_tests_by_suite = suite_skipped_tests or {
        "full-stack-desktop": [_allowed_skipped_test()],
        "full-stack-mobile": [],
    }
    row_skipped_tests_by_suite = suite_row_skipped_tests or skipped_tests_by_suite
    run_ids = suite_run_ids or {}
    report_presence = suite_report_presence or {
        "full-stack-desktop": True,
        "full-stack-mobile": True,
    }
    report_stats = suite_report_stats or {}
    report_hash_overrides = suite_report_hash_overrides or {}
    state_roots = create_state_roots or {}
    write_reports = write_report_files or {}
    report_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for suite in suites:
        state_root = report_root / suite
        if state_roots.get(suite, True):
            state_root.mkdir(parents=True, exist_ok=True)
        report_path = report_root / f"{suite}-playwright-report.json"
        should_write_report = write_reports.get(
            suite,
            bool(report_presence.get(suite, False)),
        )
        skipped_tests = list(skipped_tests_by_suite.get(suite, []))
        if should_write_report:
            stats = report_stats.get(suite) or {
                "expected": passed_counts.get(suite, 0),
                "skipped": skipped_counts.get(suite, 0),
                "unexpected": failed_counts.get(suite, 0),
                "flaky": 0,
            }
            report_path.write_text(
                json.dumps({"stats": stats, "suites": _playwright_suites(skipped_tests)}),
                encoding="utf-8",
            )
        try:
            report_bytes = report_path.read_bytes()
        except OSError:
            report_bytes = b""
        report_sha = hashlib.sha256(report_bytes).hexdigest() if report_bytes else ""
        rows.append(
            {
                "suite": suite,
                "status": "passed",
                "state_root": str(state_root),
                "run_id": run_ids.get(suite, run_id),
                "playwright_report": str(report_path),
                "playwright_report_present": bool(report_presence.get(suite, False)),
                "playwright_report_sha256": report_hash_overrides.get(
                    suite,
                    report_sha,
                ),
                "playwright_report_bytes": len(report_bytes),
                "test_match": list(test_matches_by_suite.get(suite, ())),
                "test_file_count": len(test_matches_by_suite.get(suite, ())),
                "test_case_count": (
                    passed_counts.get(suite, 0)
                    + failed_counts.get(suite, 0)
                    + skipped_counts.get(suite, 0)
                ),
                "passed_test_count": passed_counts.get(suite, 0),
                "skipped_test_count": skipped_counts.get(suite, 0),
                "failed_test_count": failed_counts.get(suite, 0),
                "flaky_test_count": 0,
                "skipped_tests": list(row_skipped_tests_by_suite.get(suite, [])),
            }
        )
    return {
        "schema": "echo.full_stack_smoke_proof.v1",
        "run_id": run_id,
        "started_at": "2026-07-01T00:00:00+00:00",
        "updated_at": "2026-07-01T00:00:10+00:00",
        "ready": True,
        "suite_count": len(rows) if suite_count is None else suite_count,
        "passed_count": len(rows) if passed_count is None else passed_count,
        "test_file_count": sum(int(row["test_file_count"]) for row in rows),
        "test_case_count": sum(int(row["test_case_count"]) for row in rows),
        "passed_test_count": sum(int(row["passed_test_count"]) for row in rows),
        "skipped_test_count": sum(int(row["skipped_test_count"]) for row in rows),
        "failed_test_count": sum(int(row["failed_test_count"]) for row in rows),
        "flaky_test_count": sum(int(row["flaky_test_count"]) for row in rows),
        "failed_suites": [],
        "suites": rows,
    }


def _allowed_skipped_test() -> dict[str, object]:
    return {
        "file": "regression.spec.ts",
        "title": (
            "Bug#2 regression · Cost tab reflects real chat cost › "
            "chat then observability/cost shows non-zero tokens"
        ),
        "reason": "requires a real model/provider and writes non-zero budget commits",
        "line": 139,
    }


def _playwright_suites(skipped_tests: list[dict[str, object]]) -> list[dict[str, object]]:
    if not skipped_tests:
        return []
    suites_by_describe: dict[tuple[str, str], list[dict[str, object]]] = {}
    for skipped in skipped_tests:
        title_parts = str(skipped["title"]).split(" › ")
        describe = title_parts[0] if len(title_parts) > 1 else ""
        spec_title = title_parts[-1]
        key = (str(skipped["file"]), describe)
        suites_by_describe.setdefault(key, []).append(
            {
                "title": spec_title,
                "file": skipped["file"],
                "line": skipped["line"],
                "column": 3,
                "tests": [
                    {
                        "annotations": [
                            {
                                "type": "skip",
                                "description": skipped["reason"],
                            }
                        ],
                        "expectedStatus": "skipped",
                        "results": [{"status": "skipped", "annotations": []}],
                        "status": "skipped",
                    }
                ],
            }
        )
    file_suites: dict[str, list[dict[str, object]]] = {}
    for (file_name, describe), specs in suites_by_describe.items():
        file_suites.setdefault(file_name, []).append(
            {
                "title": describe,
                "file": file_name,
                "specs": specs,
            }
        )
    return [
        {
            "title": file_name,
            "file": file_name,
            "suites": child_suites,
            "specs": [],
        }
        for file_name, child_suites in file_suites.items()
    ]

