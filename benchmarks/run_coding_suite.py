"""Run the implemented coding slice of the fixed head-to-head suite."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from benchmarks.codex_cli_runner import CodexCliTrialRunner, codex_cli_version
from benchmarks.eval_harness import run_suite_by_case, write_behavioral_system_evidence
from benchmarks.fixed_suite_fixtures import prepare_coding_fixture_suite
from benchmarks.realtime_runner import RealtimeTrialRunner

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run two fixed coding cases against Echo or Codex.",
    )
    parser.add_argument("--system", choices=("echo", "codex"), required=True)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--runs-root", type=Path, default=REPO_ROOT / "benchmarks/results/runs")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-dir", default="benchmarks/results/behavioral-artifacts")
    parser.add_argument("--system-version", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--echo-url", default="ws://127.0.0.1:8000/api/realtime")
    parser.add_argument("--echo-token-env", default="ECHO_API_TOKEN")
    parser.add_argument(
        "--codex-executable",
        default="/Applications/ChatGPT.app/Contents/Resources/codex",
    )
    parser.add_argument("--codex-ignore-user-config", action="store_true")
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args(argv)
    if args.k < 1:
        parser.error("--k must be at least 1")
    prepared = prepare_coding_fixture_suite(
        repo_root=REPO_ROOT,
        runs_root=args.runs_root / args.system,
    )
    if args.system == "echo":
        version = args.system_version or "echo-local"

        def runner_factory(case):
            return RealtimeTrialRunner(
                url=args.echo_url,
                token=os.environ.get(args.echo_token_env) or None,
                model=args.model,
                workspace=lambda: prepared.workspace(case.id),
                approval_policy="never",
                approval_action="decline",
                timeout_seconds=args.timeout,
            )

    else:
        version = args.system_version or codex_cli_version(args.codex_executable)

        def runner_factory(case):
            return CodexCliTrialRunner(
                executable=args.codex_executable,
                workspace=lambda: prepared.workspace(case.id),
                model=args.model,
                timeout_seconds=args.timeout,
                ignore_user_config=args.codex_ignore_user_config,
            )

    report = run_suite_by_case(prepared.cases, runner_factory=runner_factory, k=args.k)
    system_evidence = write_behavioral_system_evidence(
        report,
        prepared.cases,
        root=REPO_ROOT,
        system_id=args.system,
        version=version,
        artifact_dir=args.artifact_dir,
    )
    payload = {
        "schema": "echo.behavioral_system_run.v1",
        "suite_id": "same-task-head-to-head-v1",
        "slice": "coding",
        "system_id": args.system,
        "system": system_evidence,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(report.summary())
    print(f"system evidence: {args.output}")
    return 0 if report.aggregate_pass_pow_k == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())


