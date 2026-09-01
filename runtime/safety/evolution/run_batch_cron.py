"""Cron-callable entry point for the guard-judge batch runner.

Run this on a regular cadence (nightly recommended) to drain the
unjudged-hits backlog through the configured LLM judge. Without this
step the digest's per-guard precision stays stuck at "?" and the
weekly report can't tell signal from noise.

Usage::

    python -m runtime.safety.evolution.run_batch_cron
    python -m runtime.safety.evolution.run_batch_cron --max-hits 200
    python -m runtime.safety.evolution.run_batch_cron --dry-run

Defaults
--------
* Hits per call: 50 (matches ``run_judge_batch`` default)
* Failure-streak abort: 5 consecutive router errors
* Judge: pulled from the service provider under key
  ``guard_judge`` (set by app bootstrap). Falls back to
  ``null_guard_judge`` — which makes the runner a no-op rather
  than crashing when no LLM is wired.

Exit codes
----------
* 0 — ran successfully (including no-op when no judge is configured)
* 1 — uncaught failure (telemetry I/O, etc.). Cron should alert.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys

from runtime.safety.evolution.guard_judge import (
    GuardJudge,
    null_guard_judge,
)
from runtime.safety.evolution.guard_judge_batch import (
    render_batch_result,
    run_judge_batch,
)

_LOG = logging.getLogger("echo.evolution.run_batch_cron")


def _resolve_judge() -> GuardJudge:
    """Pull the configured guard judge from the service provider.

    Returns ``null_guard_judge`` on any failure (provider missing,
    service not registered, etc.) — the runner then short-circuits
    cleanly instead of crashing the cron job.
    """
    with contextlib.suppress(Exception):
        from runtime.platform.process.service_provider import get_provider

        judge = get_provider().get("guard_judge", default=None)
        if callable(judge):
            return judge
    return null_guard_judge


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_batch_cron",
        description="Drain unjudged guard hits through the LLM judge.",
    )
    parser.add_argument(
        "--max-hits",
        type=int,
        default=50,
        help="Cap hits processed per run (default 50).",
    )
    parser.add_argument(
        "--failure-streak-limit",
        type=int,
        default=5,
        help="Abort after this many consecutive router errors (default 5).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan only — count candidates and exit without calling judge.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Log at INFO level instead of WARNING.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    judge = _resolve_judge()
    try:
        result = run_judge_batch(
            judge=judge,
            max_hits=args.max_hits,
            failure_streak_limit=args.failure_streak_limit,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001 — top-level cron guard
        _LOG.exception("run_batch_cron crashed: %s", exc)
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(render_batch_result(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
