from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_e2e_smoke_proof_records_desktop_and_mobile_suites(tmp_path: Path) -> None:
    output = tmp_path / "proof" / "full_stack_smoke_proof.json"

    for suite, state_root, test_match, stats, skipped_tests in (
        (
            "full-stack-desktop",
            tmp_path / "desktop",
            "full-stack-smoke.spec.ts",
            {"expected": 13, "skipped": 1, "unexpected": 0, "flaky": 0},
            [
                {
                    "file": "regression.spec.ts",
                    "title": (
                        "Bug#2 regression · Cost tab reflects real chat cost › "
                        "chat then observability/cost shows non-zero tokens"
                    ),
                    "reason": ("requires a real model/provider and writes non-zero budget commits"),
                    "line": 139,
                }
            ],
        ),
        (
            "full-stack-mobile",
            tmp_path / "mobile",
            "mobile-smoke.spec.ts",
            {"expected": 3, "skipped": 0, "unexpected": 0, "flaky": 0},
            [],
        ),
    ):
        playwright_report = tmp_path / f"{suite}.json"
        _write_playwright_report(
            playwright_report,
            stats=stats,
            skipped_tests=skipped_tests,
        )
        result = subprocess.run(
            [
                sys.executable,
                "scripts/e2e_smoke_proof.py",
                "--output",
                str(output),
                "--suite",
                suite,
                "--status",
                "passed",
                "--state-root",
                str(state_root),
                "--frontend-port",
                "13000",
                "--backend-host",
                "127.0.0.1",
                "--backend-port",
                "18000",
                "--playwright-report",
                str(playwright_report),
                "--run-id",
                "proof-run-1",
                "--test-match",
                test_match,
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    data = json.loads(output.read_text(encoding="utf-8"))

    assert data["schema"] == "echo.full_stack_smoke_proof.v1"
    assert data["run_id"] == "proof-run-1"
    assert data["started_at"]
    assert data["updated_at"]
    assert data["ready"] is True
    assert data["suite_count"] == 2
    assert data["passed_count"] == 2
    assert data["test_file_count"] == 2
    assert data["test_case_count"] == 17
    assert data["passed_test_count"] == 16
    assert data["skipped_test_count"] == 1
    assert data["failed_test_count"] == 0
    assert data["failed_suites"] == []
    assert [suite["suite"] for suite in data["suites"]] == [
        "full-stack-desktop",
        "full-stack-mobile",
    ]
    assert data["suites"][0]["backend_host"] == "127.0.0.1"
    assert data["suites"][0]["run_id"] == "proof-run-1"
    assert data["suites"][0]["test_match"] == ["full-stack-smoke.spec.ts"]
    assert data["suites"][0]["test_file_count"] == 1
    assert data["suites"][0]["playwright_report_present"] is True
    assert data["suites"][0]["playwright_report_bytes"] > 0
    assert (
        data["suites"][0]["playwright_report_sha256"]
        == hashlib.sha256((tmp_path / "full-stack-desktop.json").read_bytes()).hexdigest()
    )
    assert data["suites"][0]["test_case_count"] == 14
    assert data["suites"][0]["passed_test_count"] == 13
    assert data["suites"][0]["skipped_tests"] == [
        {
            "file": "regression.spec.ts",
            "title": (
                "Bug#2 regression · Cost tab reflects real chat cost › "
                "chat then observability/cost shows non-zero tokens"
            ),
            "reason": "requires a real model/provider and writes non-zero budget commits",
            "line": 139,
        }
    ]


def test_e2e_smoke_proof_reports_failed_suite(tmp_path: Path) -> None:
    output = tmp_path / "full_stack_smoke_proof.json"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_smoke_proof.py",
            "--output",
            str(output),
            "--suite",
            "full-stack-desktop",
            "--status",
            "failed",
            "--state-root",
            str(tmp_path / "desktop"),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    data = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert data["ready"] is False
    assert data["failed_suites"] == ["full-stack-desktop"]


def test_e2e_smoke_proof_records_bundle_internal_paths_as_relative(tmp_path: Path) -> None:
    bundle = tmp_path / "artifact"
    state_root = bundle / "full-stack"
    report = bundle / "playwright.json"
    output = bundle / "full_stack_smoke_proof.json"
    state_root.mkdir(parents=True)
    _write_playwright_report(
        report,
        stats={"expected": 1, "skipped": 0, "unexpected": 0, "flaky": 0},
        skipped_tests=[],
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/e2e_smoke_proof.py",
            "--output",
            str(output),
            "--suite",
            "full-stack-mobile",
            "--status",
            "passed",
            "--state-root",
            str(state_root),
            "--playwright-report",
            str(report),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    row = json.loads(output.read_text(encoding="utf-8"))["suites"][0]
    assert row["state_root"] == "full-stack"
    assert row["playwright_report"] == "playwright.json"


def _write_playwright_report(
    path: Path,
    *,
    stats: dict[str, int],
    skipped_tests: list[dict[str, object]],
) -> None:
    path.write_text(
        json.dumps({"stats": stats, "suites": _playwright_suites(skipped_tests)}),
        encoding="utf-8",
    )


def _playwright_suites(skipped_tests: list[dict[str, object]]) -> list[dict[str, object]]:
    if not skipped_tests:
        return []
    specs = []
    for skipped in skipped_tests:
        specs.append(
            {
                "title": str(skipped["title"]).split(" › ")[-1],
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
    return [
        {
            "title": "regression.spec.ts",
            "file": "regression.spec.ts",
            "suites": [
                {
                    "title": "Bug#2 regression · Cost tab reflects real chat cost",
                    "file": "regression.spec.ts",
                    "specs": specs,
                }
            ],
            "specs": [],
        }
    ]

