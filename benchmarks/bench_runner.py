"""Production realtime smoke benchmark.

This replaces the retired SSE entry point. It intentionally runs a small,
outcome-graded WebSocket smoke; the fixed 14-case surpass suite uses the same
``RealtimeTrialRunner`` with fixture-specific graders.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from benchmarks.eval_harness import EvalCase, run_suite
from benchmarks.realtime_runner import RealtimeTrialRunner


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a repeated realtime agent smoke benchmark.")
    parser.add_argument("--url", default="ws://127.0.0.1:8000/api/realtime")
    parser.add_argument(
        "--prompt",
        default="Reply with exactly ECHO_EVAL_OK and no other text.",
    )
    parser.add_argument("--expected", default="ECHO_EVAL_OK")
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--model", default=None)
    parser.add_argument("--token-env", default="ECHO_API_TOKEN")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.k < 1:
        parser.error("--k must be at least 1")
    runner = RealtimeTrialRunner(
        url=args.url,
        token=os.environ.get(args.token_env) or None,
        approval_policy="never",
        approval_action="decline",
        model=args.model,
        timeout_seconds=args.timeout,
    )
    case = EvalCase(
        id="realtime.exact-output-smoke",
        prompt=args.prompt,
        grader=lambda trajectory: trajectory.last_text().strip() == args.expected,
    )
    report = run_suite([case], runner=runner, k=args.k)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        report.write_json(args.output)
    print(report.summary())
    return 0 if report.aggregate_pass_pow_k == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

