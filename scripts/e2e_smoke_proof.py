#!/usr/bin/env python3
"""Persist machine-readable proof for full-stack Playwright smoke runs."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "echo.full_stack_smoke_proof.v1"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Append a full-stack smoke suite result to a proof JSON file.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--status", choices=("passed", "failed"), required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--frontend-port", default="")
    parser.add_argument("--backend-host", default="")
    parser.add_argument("--backend-port", default="")
    parser.add_argument("--test-match", default="")
    parser.add_argument("--playwright-report", type=Path)
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()

    proof = _read_proof(args.output)
    proof_root = args.output.parent.resolve()
    now = datetime.now(UTC).isoformat()
    incoming_run_id = str(args.run_id or "").strip()
    existing_run_id = str(proof.get("run_id") or "").strip()
    if incoming_run_id and existing_run_id and incoming_run_id != existing_run_id:
        proof = {"schema": SCHEMA, "suites": []}
        existing_run_id = ""
    run_id = incoming_run_id or existing_run_id or _fallback_run_id(now)
    started_at = str(proof.get("started_at") or now)
    suites = [
        suite
        for suite in proof.get("suites", [])
        if isinstance(suite, dict) and suite.get("suite") != args.suite
    ]
    test_match = [item.strip() for item in str(args.test_match).split(",") if item.strip()]
    playwright = _read_playwright_report(args.playwright_report)
    suites.append(
        {
            "suite": args.suite,
            "status": args.status,
            "state_root": _portable_artifact_path(args.state_root, proof_root=proof_root),
            "frontend_port": str(args.frontend_port),
            "backend_host": str(args.backend_host),
            "backend_port": str(args.backend_port),
            "test_match": test_match,
            "test_file_count": len(test_match),
            "playwright_report": _portable_artifact_path(
                args.playwright_report,
                proof_root=proof_root,
            ),
            "playwright_report_present": bool(playwright.get("present")),
            "playwright_report_sha256": str(playwright.get("sha256") or ""),
            "playwright_report_bytes": int(playwright.get("bytes") or 0),
            "run_id": run_id,
            "test_case_count": int(playwright.get("test_case_count") or 0),
            "passed_test_count": int(playwright.get("passed_test_count") or 0),
            "skipped_test_count": int(playwright.get("skipped_test_count") or 0),
            "failed_test_count": int(playwright.get("failed_test_count") or 0),
            "flaky_test_count": int(playwright.get("flaky_test_count") or 0),
            "skipped_tests": playwright.get("skipped_tests") or [],
            "recorded_at": now,
        }
    )
    ready = bool(suites) and all(suite.get("status") == "passed" for suite in suites)
    total_test_files = sum(_test_file_count(suite) for suite in suites)
    total_test_cases = sum(_count_field(suite, "test_case_count") for suite in suites)
    total_passed_tests = sum(_count_field(suite, "passed_test_count") for suite in suites)
    total_skipped_tests = sum(_count_field(suite, "skipped_test_count") for suite in suites)
    total_failed_tests = sum(_count_field(suite, "failed_test_count") for suite in suites)
    total_flaky_tests = sum(_count_field(suite, "flaky_test_count") for suite in suites)
    report = {
        "schema": SCHEMA,
        "run_id": run_id,
        "started_at": started_at,
        "updated_at": now,
        "ready": ready,
        "suite_count": len(suites),
        "passed_count": sum(1 for suite in suites if suite.get("status") == "passed"),
        "test_file_count": total_test_files,
        "test_case_count": total_test_cases,
        "passed_test_count": total_passed_tests,
        "skipped_test_count": total_skipped_tests,
        "failed_test_count": total_failed_tests,
        "flaky_test_count": total_flaky_tests,
        "failed_suites": [
            str(suite.get("suite")) for suite in suites if suite.get("status") != "passed"
        ],
        "suites": sorted(suites, key=lambda suite: str(suite.get("suite"))),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if ready else 1


def _read_proof(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": SCHEMA, "suites": []}
    if not isinstance(data, dict):
        return {"schema": SCHEMA, "suites": []}
    suites = data.get("suites")
    if not isinstance(suites, list):
        data["suites"] = []
    return data


def _read_playwright_report(path: Path | None) -> dict[str, object]:
    if path is None:
        return _empty_playwright_report()
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _empty_playwright_report()
    stats = data.get("stats") if isinstance(data, dict) else {}
    if not isinstance(stats, dict):
        stats = {}
    passed = _nonnegative_int(stats.get("expected")) + _nonnegative_int(stats.get("flaky"))
    skipped = _nonnegative_int(stats.get("skipped"))
    failed = _nonnegative_int(stats.get("unexpected"))
    flaky = _nonnegative_int(stats.get("flaky"))
    total = passed + skipped + failed
    if total == 0:
        total, passed, skipped, failed, flaky = _count_playwright_tests(data)
    return {
        "present": True,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "test_case_count": total,
        "passed_test_count": passed,
        "skipped_test_count": skipped,
        "failed_test_count": failed,
        "flaky_test_count": flaky,
        "skipped_tests": _collect_playwright_skipped_tests(data),
    }


def _empty_playwright_report() -> dict[str, object]:
    return {
        "present": False,
        "sha256": "",
        "bytes": 0,
        "test_case_count": 0,
        "passed_test_count": 0,
        "skipped_test_count": 0,
        "failed_test_count": 0,
        "flaky_test_count": 0,
        "skipped_tests": [],
    }


def _portable_artifact_path(path: Path | None, *, proof_root: Path) -> str:
    """Record paths relative to the proof bundle whenever possible.

    CI artifacts are downloaded under a different absolute runner directory at
    release time.  Absolute producer paths make an otherwise valid proof
    unverifiable after that move, so paths inside the bundle root must be
    relocation-safe.  External paths remain absolute and are rejected by the
    release-proof boundary checks as before.
    """

    if path is None:
        return ""
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(proof_root))
    except ValueError:
        return str(resolved)


def _fallback_run_id(recorded_at: str) -> str:
    return "local-" + recorded_at.replace(":", "").replace("+", "Z")


def _count_playwright_tests(data: object) -> tuple[int, int, int, int, int]:
    total = passed = skipped = failed = flaky = 0
    stack: list[object] = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if isinstance(item.get("tests"), list):
                for test in item["tests"]:
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
    line = _nonnegative_int(spec.get("line"))
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


def _skipped_test_key(entry: dict[str, object]) -> tuple[str, str, str, int]:
    return (
        str(entry.get("file") or ""),
        str(entry.get("title") or ""),
        str(entry.get("reason") or ""),
        _nonnegative_int(entry.get("line")),
    )


def _test_file_count(suite: dict[str, Any]) -> int:
    count = suite.get("test_file_count")
    if isinstance(count, int):
        return max(0, count)
    test_match = suite.get("test_match")
    if isinstance(test_match, list):
        return len([item for item in test_match if str(item).strip()])
    return 0


def _count_field(suite: dict[str, Any], field: str) -> int:
    return _nonnegative_int(suite.get(field))


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())


