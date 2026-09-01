from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.safety.recovery.native_turn_replay import (
    TurnReplayCase,
    replay_turn_candidates,
)


@dataclass(frozen=True, slots=True)
class _ReplayCandidate:
    candidate_id: str
    prompt: str


def compute_replay_latency_budget(
    *,
    corpus_size: int = 2_000,
    candidate_count: int = 16,
    max_latency_ms: float = 3_000.0,
    max_evaluation_us: float = 250.0,
) -> dict[str, Any]:
    """Measure the deterministic turn-replay path against a release budget."""

    cases = _build_corpus(max(1, int(corpus_size)))
    candidates = _build_candidates(max(1, int(candidate_count)))
    started = time.perf_counter()
    report = replay_turn_candidates(candidates, cases=cases)
    elapsed_ms = (time.perf_counter() - started) * 1_000.0
    evaluations = len(cases) * len(candidates)
    evaluation_us = elapsed_ms * 1_000.0 / max(1, evaluations)
    latency_limit = max(0.0, float(max_latency_ms))
    evaluation_limit = max(0.0, float(max_evaluation_us))
    result_complete = (
        len(report.cases) == len(cases)
        and len(report.candidates) == len(candidates)
        and all(len(candidate.case_results) == len(cases) for candidate in report.candidates)
    )
    checks = [
        {
            "id": "wall_clock_budget",
            "passed": elapsed_ms <= latency_limit,
            "actual": round(elapsed_ms, 3),
            "limit": latency_limit,
            "unit": "ms",
        },
        {
            "id": "per_evaluation_budget",
            "passed": evaluation_us <= evaluation_limit,
            "actual": round(evaluation_us, 3),
            "limit": evaluation_limit,
            "unit": "us",
        },
        {
            "id": "corpus_completeness",
            "passed": result_complete,
            "actual": evaluations if result_complete else 0,
            "limit": evaluations,
            "unit": "evaluations",
        },
    ]
    passed = all(check["passed"] for check in checks)
    return {
        "schema": "echo.replay_latency_budget.v1",
        "passed": passed,
        "corpus_size": len(cases),
        "candidate_count": len(candidates),
        "evaluations": evaluations,
        "elapsed_ms": round(elapsed_ms, 3),
        "evaluation_us": round(evaluation_us, 3),
        "throughput_per_second": round(evaluations / max(elapsed_ms / 1_000.0, 1e-9), 1),
        "checks": checks,
        "next_actions": (
            []
            if passed
            else ["Reduce replay latency or explicitly recalibrate the measured CI budget."]
        ),
    }


def _build_corpus(size: int) -> list[TurnReplayCase]:
    kinds = ("report_truncation", "tool_permission_confusion", "final_step_stuck")
    return [
        TurnReplayCase(
            case_id=f"latency-{index:05d}",
            kind=kinds[index % len(kinds)],
            task_input=f"deterministic replay corpus item {index}",
            expected_behavior="preserve the verified recovery behavior",
            weight=1.0 + ((index % 5) * 0.1),
        )
        for index in range(size)
    ]


def _build_candidates(count: int) -> list[_ReplayCandidate]:
    prompt = (
        "If finish_reason is length or output is truncated, continue from the last "
        "checkpoint until the complete final answer is delivered. Default agent mode "
        "may use tools and skills unless discussion mode is requested; do not claim "
        "tools are unavailable. After the final answer, mark todo progress complete "
        "and stop the active step."
    )
    return [
        _ReplayCandidate(candidate_id=f"latency-candidate-{index:03d}", prompt=prompt)
        for index in range(count)
    ]


def _main() -> int:
    parser = argparse.ArgumentParser(description="Gate large-corpus replay latency.")
    parser.add_argument("--corpus-size", type=int, default=2_000)
    parser.add_argument("--candidate-count", type=int, default=16)
    parser.add_argument("--max-latency-ms", type=float, default=3_000.0)
    parser.add_argument("--max-evaluation-us", type=float, default=250.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = compute_replay_latency_budget(
        corpus_size=args.corpus_size,
        candidate_count=args.candidate_count,
        max_latency_ms=args.max_latency_ms,
        max_evaluation_us=args.max_evaluation_us,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(f"{args.output.suffix}.tmp")
        temporary.write_text(f"{rendered}\n", encoding="utf-8")
        temporary.replace(args.output)
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ["compute_replay_latency_budget"]
