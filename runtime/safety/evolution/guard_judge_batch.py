"""Batch runner — drains unjudged guard hits through the LLM judge.

The piece that closes the P1 loop end-to-end. Each ReAct turn that
trips a guard records a hit to ``GuardTelemetry``. This runner reads
hits without verdicts, asks the configured ``GuardJudge`` to grade
them, and writes the verdicts back. After it runs, ``digest()`` has
real precision data the prompt evolver can trust.

Designed for offline batch execution (cron, weekly task, manual
ops command). NOT for the ReAct hot path.

Invariants
----------
* Idempotent — a second run only processes hits that gained no
  verdict yet. Re-running is always safe.
* Budget-bounded — caps ``max_hits`` per call so a backlog of 10k
  hits doesn't burn an API quota in one shot.
* Fail-open — judge errors degrade to ``uncertain`` verdicts. Repeated
  failures (configurable ``failure_streak_limit``) abort the batch
  to prevent thundering herd against a degraded model.
* Deterministic order — hits processed oldest-first by ``ts``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from runtime.safety.evolution.guard_judge import (
    GuardJudge,
    GuardJudgeVerdict,
    null_guard_judge,
)
from runtime.safety.evolution.guard_telemetry import (
    GuardHitRecord,
    GuardTelemetry,
)

_LOG = logging.getLogger("echo.evolution.guard_judge_batch")


# Optional injector for retrieving a trajectory excerpt for a given
# hit. Production wiring would index by (hit.label, hit.ts) into the
# ReAct journal store. Default returns empty string — judge will then
# output ``uncertain`` because it has no context to grade against.
TrajectoryProvider = Callable[[GuardHitRecord], str]


def _empty_trajectory_provider(_hit: GuardHitRecord) -> str:
    return ""


@dataclass
class BatchResult:
    """Summary of one ``run_judge_batch`` call."""

    total_judged: int = 0
    by_action: dict[str, int] = field(default_factory=dict)
    errors: int = 0
    skipped_no_judge: bool = False
    aborted_failure_streak: bool = False
    elapsed_s: float = 0.0
    dry_run: bool = False
    candidates_seen: int = 0


def run_judge_batch(
    *,
    sink: GuardTelemetry | None = None,
    judge: GuardJudge | None = None,
    trajectory_provider: TrajectoryProvider | None = None,
    max_hits: int = 50,
    failure_streak_limit: int = 5,
    guard_message_provider: Callable[[GuardHitRecord], str] | None = None,
    dry_run: bool = False,
) -> BatchResult:
    """Drain unjudged hits through the configured judge.

    Parameters
    ----------
    sink :
        GuardTelemetry instance to read hits from / write verdicts to.
        Defaults to a fresh one (singleton path).
    judge :
        Callable returning ``GuardJudgeVerdict``. Defaults to
        ``null_guard_judge`` — when no judge is wired the runner is a
        no-op (returns immediately, ``skipped_no_judge=True``).
    trajectory_provider :
        Optional callable mapping hit → trajectory excerpt string. The
        runner can't see the journal directly; the caller must inject
        a lookup. Defaults to empty-string provider (judge will return
        uncertain when context is missing).
    max_hits :
        Hard cap per call. Prevents one batch from eating an API
        quota when there's a long backlog. ``0`` disables.
    failure_streak_limit :
        If the judge raises / returns ``uncertain`` with reason
        ``router_error`` this many times in a row, abort early.
    guard_message_provider :
        Optional way to recover the guard message text for a hit. If
        the hit's ``metadata`` already includes a ``guard_message``
        the provider isn't called. Defaults to None.
    dry_run :
        Plan only — count candidates and return without calling the
        judge or writing verdicts. Useful for "how big is the
        backlog" inspection.

    Returns
    -------
    BatchResult — totals + per-action breakdown.
    """
    started = time.monotonic()
    result = BatchResult(dry_run=dry_run)

    if judge is None or judge is null_guard_judge:
        # No-op short-circuit — keeps the cron-callable contract
        # safe even when no LLM is configured.
        result.skipped_no_judge = True
        result.elapsed_s = round(time.monotonic() - started, 4)
        return result

    actual_sink = sink if sink is not None else GuardTelemetry()
    provider = trajectory_provider or _empty_trajectory_provider

    try:
        unjudged = actual_sink.unjudged_hits()
    except Exception as exc:  # noqa: BLE001 — fail-open
        _LOG.warning("unjudged_hits read failed: %s", exc)
        result.errors += 1
        result.elapsed_s = round(time.monotonic() - started, 4)
        return result

    # Oldest-first deterministic order.
    unjudged.sort(key=lambda h: h.ts)
    if max_hits and max_hits > 0:
        unjudged = unjudged[:max_hits]
    result.candidates_seen = len(unjudged)

    if dry_run:
        result.elapsed_s = round(time.monotonic() - started, 4)
        return result

    failure_streak = 0
    for hit in unjudged:
        guard_msg = _resolve_guard_message(hit, guard_message_provider)
        try:
            excerpt = provider(hit) or ""
        except Exception as exc:  # noqa: BLE001 — fail-open
            _LOG.debug("trajectory_provider failed for %s: %s", hit.label, exc)
            excerpt = ""

        try:
            verdict = judge(hit.label, guard_msg, excerpt)
        except Exception as exc:  # noqa: BLE001 — fail-open
            _LOG.warning(
                "judge raised for %s: %s — recording uncertain",
                hit.label,
                exc,
            )
            verdict = GuardJudgeVerdict(
                action="uncertain",
                reason="judge_exception",
            )
            result.errors += 1
            failure_streak += 1
        else:
            if verdict.action == "uncertain" and verdict.reason == "router_error":
                failure_streak += 1
            else:
                failure_streak = 0

        try:
            actual_sink.record_verdict(
                hit.label,
                hit.ts,
                verdict.action,
                reason=verdict.reason,
                confidence=verdict.confidence,
                hit_seq=hit.seq,
            )
        except Exception as exc:  # noqa: BLE001 — fail-open
            _LOG.warning("record_verdict failed for %s: %s", hit.label, exc)
            result.errors += 1
            continue

        result.total_judged += 1
        result.by_action[verdict.action] = result.by_action.get(verdict.action, 0) + 1

        if failure_streak >= failure_streak_limit:
            _LOG.warning(
                "judge failure streak %d hit — aborting batch early",
                failure_streak,
            )
            result.aborted_failure_streak = True
            break

    result.elapsed_s = round(time.monotonic() - started, 4)
    return result


def _resolve_guard_message(
    hit: GuardHitRecord,
    provider: Callable[[GuardHitRecord], str] | None,
) -> str:
    """Try metadata['guard_message'] first, then provider, then a
    placeholder. Judge needs SOMETHING to read."""
    if isinstance(hit.metadata, dict):
        msg = hit.metadata.get("guard_message")
        if isinstance(msg, str) and msg.strip():
            return msg
    if provider is not None:
        try:
            msg = provider(hit)
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("guard_message_provider failed: %s", exc)
            return f"[guard fired: {hit.label}]"
        if isinstance(msg, str) and msg.strip():
            return msg
    return f"[guard fired: {hit.label}]"


def render_batch_result(result: BatchResult) -> str:
    """Human-readable one-screen summary — for cron logs."""
    if result.skipped_no_judge:
        return "Guard judge batch: no judge configured — skipped."
    if result.dry_run:
        return (
            f"Guard judge batch (dry run): {result.candidates_seen} "
            f"unjudged hits would be processed."
        )
    lines = [
        f"Guard judge batch: judged {result.total_judged}/"
        f"{result.candidates_seen} hits in {result.elapsed_s}s",
    ]
    for action, count in sorted(result.by_action.items()):
        lines.append(f"  {action:18s} {count:5d}")
    if result.errors:
        lines.append(f"  errors: {result.errors}")
    if result.aborted_failure_streak:
        lines.append("  aborted due to judge failure streak")
    return "\n".join(lines)


def batch_result_to_dict(result: BatchResult) -> dict[str, Any]:
    """Used by the upcoming weekly digest writer."""
    return asdict(result)


__all__ = [
    "BatchResult",
    "TrajectoryProvider",
    "batch_result_to_dict",
    "render_batch_result",
    "run_judge_batch",
]
