"""Assemble two system runs into the release-gated behavioral bundle."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.eval_harness import write_behavioral_bundle
from runtime.safety.evolution.behavioral_surpass_evidence import (
    compute_behavioral_surpass_evidence,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SUITE_ID = "same-task-head-to-head-v1"
SYSTEM_RUN_SCHEMA = "echo.behavioral_system_run.v2"
INFRASTRUCTURE_STATUS_PATH = REPO_ROOT / "benchmarks/results/behavioral-infrastructure-latest.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble and validate head-to-head evidence.")
    parser.add_argument("--echo-run", type=Path, required=True)
    parser.add_argument("--codex-run", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "benchmarks/results/behavioral-surpass-latest.json",
    )
    parser.add_argument("--source-revision", default=None)
    parser.add_argument("--runner-version", default="behavioral-runner-v1")
    parser.add_argument("--generated-at", default=None)
    args = parser.parse_args(argv)
    source_revision = args.source_revision or _git_revision(REPO_ROOT)
    systems = {
        "echo": _load_system_run(args.echo_run, "echo"),
        "codex": _load_system_run(args.codex_run, "codex"),
    }
    write_behavioral_bundle(
        path=args.output,
        suite_manifest_path=REPO_ROOT / "benchmarks/behavioral-surpass-suite.json",
        suite_id=SUITE_ID,
        runner_version=args.runner_version,
        source_revision=source_revision,
        generated_at=args.generated_at or datetime.now(UTC).isoformat(),
        systems=systems,
    )
    report = compute_behavioral_surpass_evidence(
        root=REPO_ROOT,
        bundle_path=args.output,
    )
    INFRASTRUCTURE_STATUS_PATH.unlink(missing_ok=True)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ready"] else 1


def _load_system_run(path: Path, expected_system: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != SYSTEM_RUN_SCHEMA:
        raise ValueError(f"invalid system run schema: {path}")
    if payload.get("suite_id") != SUITE_ID or payload.get("system_id") != expected_system:
        raise ValueError(f"system run identity mismatch: {path}")
    system = payload.get("system")
    if not isinstance(system, dict):
        raise ValueError(f"system run payload is missing: {path}")
    return system


def _git_revision(root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise RuntimeError("cannot resolve source revision; pass --source-revision")
    return completed.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())


