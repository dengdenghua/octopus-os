from __future__ import annotations

from runtime.safety.evolution.replay_latency_budget import (
    compute_replay_latency_budget,
)


def test_large_corpus_replay_stays_within_release_budget() -> None:
    report = compute_replay_latency_budget(
        corpus_size=500,
        candidate_count=8,
        max_latency_ms=2_000,
        max_evaluation_us=400,
    )

    assert report["schema"] == "echo.replay_latency_budget.v1"
    assert report["passed"] is True
    assert report["evaluations"] == 4_000
    assert report["throughput_per_second"] > 0
    assert all(check["passed"] for check in report["checks"])
    assert report["next_actions"] == []


def test_replay_latency_budget_fails_closed_when_budget_is_zero() -> None:
    report = compute_replay_latency_budget(
        corpus_size=10,
        candidate_count=2,
        max_latency_ms=0,
        max_evaluation_us=0,
    )

    assert report["passed"] is False
    assert any(check["passed"] is False for check in report["checks"])
    assert report["next_actions"]

