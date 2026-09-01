#!/usr/bin/env python3
"""Merge readiness and full-stack smoke proofs into one release certificate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA = "echo.e2e_release_proof.v1"
READINESS_SCHEMA = "echo.production_readiness_gate.v1"
FULL_STACK_SCHEMA = "echo.full_stack_smoke_proof.v1"
MIN_SCORE = 95
MIN_TEST_FILES_BY_SUITE = {
    "full-stack-desktop": 4,
    "full-stack-mobile": 1,
}
MIN_PASSED_TESTS_BY_SUITE = {
    "full-stack-desktop": 13,
    "full-stack-mobile": 3,
}
ALLOWED_SKIPPED_TESTS_BY_SUITE = {
    "full-stack-desktop": {
        (
            "regression.spec.ts",
            "Bug#2 regression · Cost tab reflects real chat cost › "
            "chat then observability/cost shows non-zero tokens",
            "requires a real model/provider and writes non-zero budget commits",
        ),
    },
    "full-stack-mobile": set(),
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a release-grade E2E proof from gate artifacts.",
    )
    parser.add_argument("--readiness", type=Path, required=True)
    parser.add_argument("--full-stack", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--required-suite",
        action="append",
        default=[],
        help=("Full-stack smoke suite that must be present and passed. May be repeated."),
    )
    args = parser.parse_args()

    report = build_release_proof(
        readiness_path=args.readiness,
        full_stack_path=args.full_stack,
        required_suites=tuple(args.required_suite),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if report["ready"] else 1


def build_release_proof(
    *,
    readiness_path: Path,
    full_stack_path: Path,
    required_suites: tuple[str, ...],
) -> dict[str, Any]:
    readiness = _read_json(readiness_path)
    full_stack = _read_json(full_stack_path)
    required = list(required_suites or ("full-stack-desktop", "full-stack-mobile"))
    suite_status = _suite_status(full_stack)
    missing_suites = [suite for suite in required if suite not in suite_status]
    failed_suites = [
        suite for suite in required if suite in suite_status and suite_status[suite] != "passed"
    ]
    suite_rows = _suite_rows(full_stack)
    proof_root = full_stack_path.parent.resolve()
    full_stack_run_id = str(full_stack.get("run_id") or "").strip()
    suite_run_ids = _suite_run_ids(suite_rows)
    suite_state_roots = _suite_state_roots(suite_rows, base_dir=proof_root)
    suite_report_presence = _suite_report_presence(suite_rows)
    suite_report_counts = _suite_playwright_report_counts(
        suite_rows,
        base_dir=proof_root,
    )
    suite_report_valid = {
        suite: bool(counts.get("valid")) for suite, counts in suite_report_counts.items()
    }
    suite_test_counts = _suite_test_counts(suite_rows)
    suite_passed_test_counts = _suite_counts(suite_rows, "passed_test_count")
    suite_failed_test_counts = _suite_counts(suite_rows, "failed_test_count")
    suites_missing_playwright_reports = [
        suite
        for suite in required
        if suite in suite_status and not suite_report_presence.get(suite, False)
    ]
    suites_missing_playwright_report_files = [
        suite
        for suite in required
        if suite in suite_status and not suite_report_valid.get(suite, False)
    ]
    suites_with_mismatched_playwright_report_counts = [
        suite
        for suite in required
        if suite in suite_report_counts
        and suite_report_valid.get(suite, False)
        and not _suite_counts_match_playwright_report(
            row=_suite_row_by_name(suite_rows, suite),
            report_counts=suite_report_counts[suite],
        )
    ]
    suites_with_mismatched_playwright_report_hashes = [
        suite
        for suite in required
        if suite in suite_report_counts
        and suite_report_valid.get(suite, False)
        and not _suite_hash_matches_playwright_report(
            row=_suite_row_by_name(suite_rows, suite),
            report_counts=suite_report_counts[suite],
        )
    ]
    suites_with_incomplete_skipped_test_inventory = [
        suite
        for suite in required
        if suite in suite_report_counts
        and suite_report_valid.get(suite, False)
        and not _skipped_test_inventory_is_complete(suite_report_counts[suite])
    ]
    suites_with_mismatched_skipped_test_inventory = [
        suite
        for suite in required
        if suite in suite_report_counts
        and suite_report_valid.get(suite, False)
        and not _suite_skipped_tests_match_playwright_report(
            row=_suite_row_by_name(suite_rows, suite),
            report_counts=suite_report_counts[suite],
        )
    ]
    unexpected_skipped_tests_by_suite = {
        suite: unexpected
        for suite in required
        if suite in suite_report_counts
        for unexpected in [
            _unexpected_skipped_tests(
                suite=suite,
                skipped_tests=suite_report_counts[suite].get("skipped_tests"),
            )
        ]
        if unexpected
    }
    suites_with_unexpected_skipped_tests = sorted(unexpected_skipped_tests_by_suite)
    weak_suite_test_coverage = [
        suite
        for suite in required
        if suite in suite_test_counts
        and suite_test_counts[suite] < MIN_TEST_FILES_BY_SUITE.get(suite, 1)
    ]
    weak_suite_passed_tests = [
        suite
        for suite in required
        if suite in suite_passed_test_counts
        and suite_passed_test_counts[suite] < MIN_PASSED_TESTS_BY_SUITE.get(suite, 1)
    ]
    suites_with_failed_tests = [
        suite
        for suite in required
        if suite in suite_failed_test_counts and suite_failed_test_counts[suite] > 0
    ]
    suites_with_mismatched_run_ids = [
        suite
        for suite in required
        if suite in suite_status and suite_run_ids.get(suite, "") != full_stack_run_id
    ]
    suites_missing_state_roots = [
        suite
        for suite in required
        if suite in suite_status and not _path_is_existing_dir(suite_state_roots.get(suite))
    ]
    suites_with_external_state_roots = [
        suite
        for suite in required
        if suite in suite_status
        and _path_is_existing_dir(suite_state_roots.get(suite))
        and not _path_is_relative_to(suite_state_roots[suite], proof_root)
    ]
    passed_suite_count = sum(1 for row in suite_rows if row.get("status") == "passed")
    declared_suite_count = _as_int(full_stack.get("suite_count"))
    declared_passed_count = _as_int(full_stack.get("passed_count"))
    declared_test_file_count = _as_int(full_stack.get("test_file_count"))
    observed_test_file_count = sum(suite_test_counts.values())
    declared_test_case_count = _as_int(full_stack.get("test_case_count"))
    observed_test_case_count = sum(_suite_counts(suite_rows, "test_case_count").values())
    declared_passed_test_count = _as_int(full_stack.get("passed_test_count"))
    observed_passed_test_count = sum(suite_passed_test_counts.values())
    scorecard_score = _as_int(readiness.get("scorecard_score"))
    automation_score = _as_int(readiness.get("automation_score"))
    checks = [
        {
            "id": "production_readiness_schema",
            "passed": readiness.get("schema") == READINESS_SCHEMA,
            "next_action": "Regenerate production readiness proof with the current gate.",
        },
        {
            "id": "production_readiness_ready",
            "passed": bool(readiness.get("ready")),
            "next_action": "Run production readiness gate and fix reported failures.",
        },
        {
            "id": "production_readiness_release_proof",
            "passed": readiness.get("release_proof") is True,
            "next_action": (
                "Run the full readiness gate with commit-bound behavioral evidence; "
                "static-only reports are not release proof."
            ),
        },
        {
            "id": "production_readiness_scores_clear_target",
            "passed": scorecard_score >= MIN_SCORE and automation_score >= MIN_SCORE,
            "next_action": "Restore scorecard and automation scores to the E2E target.",
        },
        {
            "id": "production_readiness_e2e_ready",
            "passed": bool(_nested(readiness, "e2e", "ready")),
            "next_action": "Restore E2E surpass certification readiness.",
        },
        {
            "id": "production_readiness_e2e_surpassed",
            "passed": _nested(readiness, "e2e", "verdict") == "surpassed",
            "next_action": "Restore E2E surpass certification.",
        },
        {
            "id": "production_readiness_coverage_complete",
            "passed": _coverage_complete(readiness),
            "next_action": "Restore all required E2E coverage domains.",
        },
        {
            "id": "production_readiness_coverage_has_no_gaps",
            "passed": _as_int(_nested(readiness, "e2e", "summary", "coverage_gap_domains")) == 0,
            "next_action": "Clear E2E coverage gap domains before release.",
        },
        {
            "id": "full_stack_smoke_schema",
            "passed": full_stack.get("schema") == FULL_STACK_SCHEMA,
            "next_action": "Regenerate full-stack smoke proof with the current script.",
        },
        {
            "id": "full_stack_smoke_ready",
            "passed": bool(full_stack.get("ready")),
            "next_action": "Run full-stack Playwright smoke and fix failures.",
        },
        {
            "id": "full_stack_run_identity_present",
            "passed": bool(full_stack_run_id),
            "next_action": "Regenerate full-stack smoke proof with a non-empty run_id.",
        },
        {
            "id": "full_stack_suite_run_ids_match",
            "passed": bool(full_stack_run_id) and not suites_with_mismatched_run_ids,
            "next_action": (
                "Regenerate full-stack smoke proof in one run; suite run_ids "
                f"do not match for suites: {', '.join(suites_with_mismatched_run_ids)}"
            ),
        },
        {
            "id": "full_stack_suite_counts_consistent",
            "passed": (
                declared_suite_count == len(suite_rows)
                and declared_passed_count == passed_suite_count
            ),
            "next_action": "Regenerate full-stack smoke proof; suite counts are inconsistent.",
        },
        {
            "id": "full_stack_required_suites_present",
            "passed": not missing_suites,
            "next_action": f"Run missing full-stack suites: {', '.join(missing_suites)}",
        },
        {
            "id": "full_stack_required_suites_passed",
            "passed": not failed_suites,
            "next_action": f"Fix failing full-stack suites: {', '.join(failed_suites)}",
        },
        {
            "id": "full_stack_required_suites_have_playwright_reports",
            "passed": not suites_missing_playwright_reports,
            "next_action": (
                "Regenerate full-stack smoke proof with Playwright JSON reports for suites: "
                f"{', '.join(suites_missing_playwright_reports)}"
            ),
        },
        {
            "id": "full_stack_required_suites_have_playwright_report_files",
            "passed": not suites_missing_playwright_report_files,
            "next_action": (
                "Restore readable Playwright JSON reports for suites: "
                f"{', '.join(suites_missing_playwright_report_files)}"
            ),
        },
        {
            "id": "full_stack_required_suites_have_state_roots",
            "passed": not suites_missing_state_roots,
            "next_action": (
                "Regenerate full-stack smoke proof with persisted state roots for suites: "
                f"{', '.join(suites_missing_state_roots)}"
            ),
        },
        {
            "id": "full_stack_required_suite_state_roots_scoped",
            "passed": not suites_with_external_state_roots,
            "next_action": (
                "Regenerate full-stack smoke proof in the same verify state root; "
                "external state roots found for suites: "
                f"{', '.join(suites_with_external_state_roots)}"
            ),
        },
        {
            "id": "full_stack_playwright_report_counts_match",
            "passed": not suites_with_mismatched_playwright_report_counts,
            "next_action": (
                "Regenerate full-stack smoke proof; Playwright report counts "
                "do not match proof rows for suites: "
                f"{', '.join(suites_with_mismatched_playwright_report_counts)}"
            ),
        },
        {
            "id": "full_stack_playwright_report_hashes_match",
            "passed": not suites_with_mismatched_playwright_report_hashes,
            "next_action": (
                "Regenerate full-stack smoke proof; Playwright report hashes "
                "do not match proof rows for suites: "
                f"{', '.join(suites_with_mismatched_playwright_report_hashes)}"
            ),
        },
        {
            "id": "full_stack_skipped_test_inventory_complete",
            "passed": not suites_with_incomplete_skipped_test_inventory,
            "next_action": (
                "Regenerate full-stack smoke proof with a test-level skipped inventory "
                "for suites: "
                f"{', '.join(suites_with_incomplete_skipped_test_inventory)}"
            ),
        },
        {
            "id": "full_stack_skipped_test_inventory_matches_report",
            "passed": not suites_with_mismatched_skipped_test_inventory,
            "next_action": (
                "Regenerate full-stack smoke proof; skipped test inventories "
                "do not match Playwright reports for suites: "
                f"{', '.join(suites_with_mismatched_skipped_test_inventory)}"
            ),
        },
        {
            "id": "full_stack_skipped_tests_are_expected",
            "passed": not suites_with_unexpected_skipped_tests,
            "next_action": (
                "Remove or explicitly approve unexpected skipped Playwright tests for suites: "
                f"{', '.join(suites_with_unexpected_skipped_tests)}"
            ),
        },
        {
            "id": "full_stack_test_file_counts_consistent",
            "passed": declared_test_file_count == observed_test_file_count,
            "next_action": (
                "Regenerate full-stack smoke proof; test file counts are inconsistent."
            ),
        },
        {
            "id": "full_stack_required_suites_have_test_coverage",
            "passed": not weak_suite_test_coverage,
            "next_action": (
                "Restore required full-stack test files for suites: "
                f"{', '.join(weak_suite_test_coverage)}"
            ),
        },
        {
            "id": "full_stack_test_case_counts_consistent",
            "passed": (
                declared_test_case_count == observed_test_case_count
                and declared_passed_test_count == observed_passed_test_count
            ),
            "next_action": (
                "Regenerate full-stack smoke proof; Playwright test counts are inconsistent."
            ),
        },
        {
            "id": "full_stack_required_suites_have_passed_tests",
            "passed": not weak_suite_passed_tests,
            "next_action": (
                "Restore required passed Playwright tests for suites: "
                f"{', '.join(weak_suite_passed_tests)}"
            ),
        },
        {
            "id": "full_stack_required_suites_have_no_failed_tests",
            "passed": not suites_with_failed_tests,
            "next_action": (
                f"Fix failed Playwright tests in suites: {', '.join(suites_with_failed_tests)}"
            ),
        },
    ]
    ready = all(bool(check["passed"]) for check in checks)
    return {
        "schema": SCHEMA,
        "ready": ready,
        "verdict": "release_ready" if ready else "needs_work",
        "checks": checks,
        "failed_checks": [str(check["id"]) for check in checks if not bool(check["passed"])],
        "summary": {
            "scorecard_score": scorecard_score,
            "automation_score": automation_score,
            "e2e_verdict": str(_nested(readiness, "e2e", "verdict") or "unknown"),
            "coverage_ready": _as_int(
                _nested(readiness, "e2e", "summary", "coverage_ready"),
            ),
            "coverage_total": _as_int(
                _nested(readiness, "e2e", "summary", "coverage_total"),
            ),
            "coverage_gap_domains": _as_int(
                _nested(readiness, "e2e", "summary", "coverage_gap_domains"),
            ),
            "full_stack_run_id": full_stack_run_id,
            "full_stack_suite_count": declared_suite_count,
            "full_stack_passed_count": declared_passed_count,
            "full_stack_test_file_count": declared_test_file_count,
            "full_stack_test_case_count": declared_test_case_count,
            "full_stack_passed_test_count": declared_passed_test_count,
            "required_suite_test_file_counts": {
                suite: suite_test_counts.get(suite, 0) for suite in required
            },
            "required_suite_passed_test_counts": {
                suite: suite_passed_test_counts.get(suite, 0) for suite in required
            },
            "required_suite_failed_test_counts": {
                suite: suite_failed_test_counts.get(suite, 0) for suite in required
            },
            "required_suite_skipped_test_counts": {
                suite: _as_int(
                    suite_report_counts.get(suite, {}).get("skipped_test_count"),
                )
                for suite in required
            },
            "required_suite_skipped_tests": {
                suite: _normalize_skipped_tests(
                    suite_report_counts.get(suite, {}).get("skipped_tests"),
                )
                for suite in required
            },
            "required_suite_run_ids": {suite: suite_run_ids.get(suite, "") for suite in required},
            "required_suite_state_roots": {
                suite: str(suite_state_roots.get(suite) or "") for suite in required
            },
            "required_suite_state_root_present": {
                suite: _path_is_existing_dir(suite_state_roots.get(suite)) for suite in required
            },
            "required_suite_playwright_report_present": {
                suite: bool(suite_report_presence.get(suite, False)) for suite in required
            },
            "required_suite_playwright_report_valid": {
                suite: bool(suite_report_valid.get(suite, False)) for suite in required
            },
            "required_suite_playwright_report_sha256": {
                suite: str(
                    suite_report_counts.get(suite, {}).get("sha256") or "",
                )
                for suite in required
            },
            "required_suites": required,
            "missing_suites": missing_suites,
            "failed_suites": failed_suites,
            "suites_with_mismatched_run_ids": suites_with_mismatched_run_ids,
            "suites_missing_state_roots": suites_missing_state_roots,
            "suites_with_external_state_roots": suites_with_external_state_roots,
            "suites_missing_playwright_reports": suites_missing_playwright_reports,
            "suites_missing_playwright_report_files": (suites_missing_playwright_report_files),
            "suites_with_mismatched_playwright_report_counts": (
                suites_with_mismatched_playwright_report_counts
            ),
            "suites_with_mismatched_playwright_report_hashes": (
                suites_with_mismatched_playwright_report_hashes
            ),
            "suites_with_incomplete_skipped_test_inventory": (
                suites_with_incomplete_skipped_test_inventory
            ),
            "suites_with_mismatched_skipped_test_inventory": (
                suites_with_mismatched_skipped_test_inventory
            ),
            "suites_with_unexpected_skipped_tests": suites_with_unexpected_skipped_tests,
            "unexpected_skipped_tests_by_suite": unexpected_skipped_tests_by_suite,
            "weak_suite_test_coverage": weak_suite_test_coverage,
            "weak_suite_passed_tests": weak_suite_passed_tests,
            "suites_with_failed_tests": suites_with_failed_tests,
        },
        "inputs": {
            "readiness": str(readiness_path),
            "full_stack": str(full_stack_path),
        },
        "readiness": {
            "schema": readiness.get("schema"),
            "ready": readiness.get("ready"),
            "mode": readiness.get("mode"),
            "release_proof": readiness.get("release_proof"),
            "expected_revision": readiness.get("expected_revision"),
            "scorecard_score": readiness.get("scorecard_score"),
            "automation_score": readiness.get("automation_score"),
            "e2e": readiness.get("e2e"),
        },
        "full_stack": {
            "schema": full_stack.get("schema"),
            "run_id": full_stack.get("run_id"),
            "started_at": full_stack.get("started_at"),
            "updated_at": full_stack.get("updated_at"),
            "ready": full_stack.get("ready"),
            "suite_count": full_stack.get("suite_count"),
            "passed_count": full_stack.get("passed_count"),
            "test_file_count": full_stack.get("test_file_count"),
            "test_case_count": full_stack.get("test_case_count"),
            "passed_test_count": full_stack.get("passed_test_count"),
            "skipped_test_count": full_stack.get("skipped_test_count"),
            "failed_test_count": full_stack.get("failed_test_count"),
            "failed_suites": full_stack.get("failed_suites"),
            "suites": full_stack.get("suites"),
        },
        "next_actions": [
            str(check["next_action"])
            for check in checks
            if not bool(check["passed"]) and check.get("next_action")
        ],
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _suite_status(full_stack: dict[str, Any]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for row in _suite_rows(full_stack):
        suite = str(row.get("suite") or "").strip()
        if suite:
            statuses[suite] = str(row.get("status") or "")
    return statuses


def _suite_rows(full_stack: dict[str, Any]) -> list[dict[str, Any]]:
    suites = full_stack.get("suites")
    if not isinstance(suites, list):
        return []
    return [row for row in suites if isinstance(row, dict)]


def _suite_row_by_name(suite_rows: list[dict[str, Any]], suite: str) -> dict[str, Any]:
    for row in suite_rows:
        if str(row.get("suite") or "").strip() == suite:
            return row
    return {}


def _suite_test_counts(suite_rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in suite_rows:
        suite = str(row.get("suite") or "").strip()
        if not suite:
            continue
        count = _as_int(row.get("test_file_count"))
        if count <= 0:
            test_match = row.get("test_match")
            if isinstance(test_match, list):
                count = len([item for item in test_match if str(item).strip()])
        counts[suite] = count
    return counts


def _suite_run_ids(suite_rows: list[dict[str, Any]]) -> dict[str, str]:
    run_ids: dict[str, str] = {}
    for row in suite_rows:
        suite = str(row.get("suite") or "").strip()
        if suite:
            run_ids[suite] = str(row.get("run_id") or "").strip()
    return run_ids


def _suite_state_roots(
    suite_rows: list[dict[str, Any]],
    *,
    base_dir: Path,
) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for row in suite_rows:
        suite = str(row.get("suite") or "").strip()
        if not suite:
            continue
        raw_path = str(row.get("state_root") or "").strip()
        path = _resolve_state_root(raw_path, base_dir=base_dir)
        if path is not None:
            roots[suite] = path
    return roots


def _suite_report_presence(suite_rows: list[dict[str, Any]]) -> dict[str, bool]:
    presence: dict[str, bool] = {}
    for row in suite_rows:
        suite = str(row.get("suite") or "").strip()
        if suite:
            presence[suite] = bool(row.get("playwright_report_present"))
    return presence


def _suite_playwright_report_counts(
    suite_rows: list[dict[str, Any]],
    *,
    base_dir: Path,
) -> dict[str, dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for row in suite_rows:
        suite = str(row.get("suite") or "").strip()
        if not suite:
            continue
        raw_path = str(row.get("playwright_report") or "").strip()
        path = _resolve_report_path(raw_path, base_dir=base_dir)
        counts[suite] = _read_playwright_report_counts(path)
    return counts


def _resolve_report_path(raw_path: str, *, base_dir: Path) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _resolve_state_root(raw_path: str, *, base_dir: Path) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    if path.is_absolute():
        return path.resolve()
    base_candidate = (base_dir / path).resolve()
    if base_candidate.exists():
        return base_candidate
    return path.resolve()


def _read_playwright_report_counts(path: Path | None) -> dict[str, Any]:
    empty = {
        "valid": False,
        "sha256": "",
        "bytes": 0,
        "test_case_count": 0,
        "passed_test_count": 0,
        "skipped_test_count": 0,
        "failed_test_count": 0,
        "flaky_test_count": 0,
        "skipped_tests": [],
    }
    if path is None:
        return empty
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    stats = data.get("stats")
    if not isinstance(stats, dict):
        stats = {}
    passed = _as_nonnegative_int(stats.get("expected")) + _as_nonnegative_int(
        stats.get("flaky"),
    )
    skipped = _as_nonnegative_int(stats.get("skipped"))
    failed = _as_nonnegative_int(stats.get("unexpected"))
    flaky = _as_nonnegative_int(stats.get("flaky"))
    total = passed + skipped + failed
    if total == 0:
        total, passed, skipped, failed, flaky = _count_playwright_tests(data)
    return {
        "valid": total > 0,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "test_case_count": total,
        "passed_test_count": passed,
        "skipped_test_count": skipped,
        "failed_test_count": failed,
        "flaky_test_count": flaky,
        "skipped_tests": _collect_playwright_skipped_tests(data),
    }


def _count_playwright_tests(data: object) -> tuple[int, int, int, int, int]:
    total = passed = skipped = failed = flaky = 0
    stack: list[object] = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            tests = item.get("tests")
            if isinstance(tests, list):
                for test in tests:
                    if not isinstance(test, dict):
                        continue
                    total += 1
                    status = str(test.get("status") or "")
                    if status in {"expected", "passed"}:
                        passed += 1
                    elif status == "skipped":
                        skipped += 1
                    elif status == "flaky":
                        passed += 1
                        flaky += 1
                    else:
                        failed += 1
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return total, passed, skipped, failed, flaky


def _suite_counts_match_playwright_report(
    *,
    row: dict[str, Any],
    report_counts: dict[str, Any],
) -> bool:
    return (
        _as_int(row.get("test_case_count")) == int(report_counts.get("test_case_count") or 0)
        and _as_int(row.get("passed_test_count"))
        == int(report_counts.get("passed_test_count") or 0)
        and _as_int(row.get("skipped_test_count"))
        == int(report_counts.get("skipped_test_count") or 0)
        and _as_int(row.get("failed_test_count"))
        == int(report_counts.get("failed_test_count") or 0)
    )


def _suite_hash_matches_playwright_report(
    *,
    row: dict[str, Any],
    report_counts: dict[str, Any],
) -> bool:
    expected_sha = str(row.get("playwright_report_sha256") or "").strip()
    expected_bytes = _as_int(row.get("playwright_report_bytes"))
    actual_sha = str(report_counts.get("sha256") or "").strip()
    actual_bytes = int(report_counts.get("bytes") or 0)
    return bool(expected_sha) and expected_sha == actual_sha and expected_bytes == actual_bytes


def _skipped_test_inventory_is_complete(report_counts: dict[str, Any]) -> bool:
    skipped_count = _as_int(report_counts.get("skipped_test_count"))
    skipped_tests = _normalize_skipped_tests(report_counts.get("skipped_tests"))
    return skipped_count == len(skipped_tests)


def _suite_skipped_tests_match_playwright_report(
    *,
    row: dict[str, Any],
    report_counts: dict[str, Any],
) -> bool:
    return _normalize_skipped_tests(row.get("skipped_tests")) == _normalize_skipped_tests(
        report_counts.get("skipped_tests"),
    )


def _unexpected_skipped_tests(
    *,
    suite: str,
    skipped_tests: object,
) -> list[dict[str, object]]:
    allowed = ALLOWED_SKIPPED_TESTS_BY_SUITE.get(suite, set())
    return [
        entry
        for entry in _normalize_skipped_tests(skipped_tests)
        if _skipped_approval_key(entry) not in allowed
    ]


def _normalize_skipped_tests(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, object]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        normalized.append(
            {
                "file": str(entry.get("file") or "").strip(),
                "title": str(entry.get("title") or "").strip(),
                "reason": str(entry.get("reason") or "").strip(),
                "line": _as_nonnegative_int(entry.get("line")),
            }
        )
    return sorted(normalized, key=_skipped_test_key)


def _collect_playwright_skipped_tests(data: object) -> list[dict[str, object]]:
    skipped: list[dict[str, object]] = []

    def walk_suite(suite: dict[str, object], title_path: list[str], file_hint: str) -> None:
        file_name = str(suite.get("file") or file_hint).strip()
        title = str(suite.get("title") or "").strip()
        next_path = list(title_path)
        if title and title != file_name:
            next_path.append(title)

        specs = suite.get("specs")
        if isinstance(specs, list):
            for spec in specs:
                if isinstance(spec, dict):
                    skipped.extend(_skipped_tests_from_spec(spec, next_path, file_name))

        child_suites = suite.get("suites")
        if isinstance(child_suites, list):
            for child in child_suites:
                if isinstance(child, dict):
                    walk_suite(child, next_path, file_name)

    if isinstance(data, dict):
        suites = data.get("suites")
        if isinstance(suites, list):
            for suite in suites:
                if isinstance(suite, dict):
                    walk_suite(suite, [], "")
    return sorted(skipped, key=_skipped_test_key)


def _skipped_tests_from_spec(
    spec: dict[str, object],
    title_path: list[str],
    file_hint: str,
) -> list[dict[str, object]]:
    tests = spec.get("tests")
    if not isinstance(tests, list):
        return []
    title = " › ".join(
        [*title_path, str(spec.get("title") or "").strip()],
    ).strip(" ›")
    file_name = str(spec.get("file") or file_hint).strip()
    line = _as_nonnegative_int(spec.get("line"))
    skipped: list[dict[str, object]] = []
    for test in tests:
        if not isinstance(test, dict) or not _playwright_test_is_skipped(test):
            continue
        skipped.append(
            {
                "file": file_name,
                "title": title,
                "reason": _skip_reason(test),
                "line": line,
            }
        )
    return skipped


def _playwright_test_is_skipped(test: dict[str, object]) -> bool:
    if str(test.get("status") or "") == "skipped":
        return True
    if str(test.get("expectedStatus") or "") == "skipped":
        return True
    results = test.get("results")
    return isinstance(results, list) and any(
        isinstance(result, dict) and str(result.get("status") or "") == "skipped"
        for result in results
    )


def _skip_reason(test: dict[str, object]) -> str:
    candidates: list[object] = []
    annotations = test.get("annotations")
    if isinstance(annotations, list):
        candidates.extend(annotations)
    results = test.get("results")
    if isinstance(results, list):
        for result in results:
            if isinstance(result, dict) and isinstance(result.get("annotations"), list):
                candidates.extend(result["annotations"])
    for annotation in candidates:
        if not isinstance(annotation, dict):
            continue
        if str(annotation.get("type") or "") != "skip":
            continue
        return str(annotation.get("description") or "").strip()
    return ""


def _skipped_approval_key(entry: dict[str, object]) -> tuple[str, str, str]:
    return (
        str(entry.get("file") or ""),
        str(entry.get("title") or ""),
        str(entry.get("reason") or ""),
    )


def _skipped_test_key(entry: dict[str, object]) -> tuple[str, str, str, int]:
    return (
        str(entry.get("file") or ""),
        str(entry.get("title") or ""),
        str(entry.get("reason") or ""),
        _as_nonnegative_int(entry.get("line")),
    )


def _suite_counts(suite_rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in suite_rows:
        suite = str(row.get("suite") or "").strip()
        if suite:
            counts[suite] = _as_int(row.get(field))
    return counts


def _coverage_complete(readiness: dict[str, Any]) -> bool:
    ready = _as_int(_nested(readiness, "e2e", "summary", "coverage_ready"))
    total = _as_int(_nested(readiness, "e2e", "summary", "coverage_total"))
    return total > 0 and ready == total


def _path_is_existing_dir(path: Path | None) -> bool:
    return path is not None and path.is_dir()


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_nonnegative_int(value: Any) -> int:
    return max(0, _as_int(value))


def _nested(data: dict[str, Any], *keys: str) -> Any:
    value: Any = data
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


if __name__ == "__main__":
    raise SystemExit(main())


