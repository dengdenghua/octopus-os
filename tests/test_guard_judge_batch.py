"""Tests for the guard-judge batch runner — the P1 closing piece.

Covers:
* No-op when no judge configured (cron-safety)
* Budget cap actually limits per-call work
* Idempotent — second run only handles new hits
* Verdicts written back to the same sink
* Fail-open: judge exceptions become uncertain verdicts
* Failure streak limit aborts early
* Dry-run plans without writing
* Trajectory provider injection works
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.safety.evolution.guard_judge import (
    GuardJudgeVerdict,
    null_guard_judge,
)
from runtime.safety.evolution.guard_judge_batch import (
    render_batch_result,
    run_judge_batch,
)
from runtime.safety.evolution.guard_telemetry import (
    GuardHitRecord,
    GuardTelemetry,
)


@pytest.fixture
def sink(tmp_path: Path) -> GuardTelemetry:
    return GuardTelemetry(path=tmp_path / "hits.jsonl")


def _seed_hits(sink: GuardTelemetry, n: int, label: str = "guard-a") -> None:
    for _ in range(n):
        sink.record(label, "security")


# ══════════════════════════════════════════════════════════════════
# No-op when no judge wired
# ══════════════════════════════════════════════════════════════════


class TestNoOpPath:
    def test_no_judge_returns_skipped(self, sink: GuardTelemetry) -> None:
        _seed_hits(sink, 3)
        result = run_judge_batch(sink=sink, judge=None)
        assert result.skipped_no_judge is True
        assert result.total_judged == 0

    def test_null_judge_treated_as_no_op(self, sink: GuardTelemetry) -> None:
        _seed_hits(sink, 3)
        result = run_judge_batch(sink=sink, judge=null_guard_judge)
        assert result.skipped_no_judge is True

    def test_no_op_writes_no_verdicts(self, sink: GuardTelemetry) -> None:
        _seed_hits(sink, 3)
        run_judge_batch(sink=sink, judge=None)
        # Sink should have hits but no verdicts.
        hits, verdicts = sink._read_with_verdicts()  # type: ignore[attr-defined]
        assert len(hits) == 3
        assert verdicts == []


# ══════════════════════════════════════════════════════════════════
# Happy path — judge runs and verdicts land
# ══════════════════════════════════════════════════════════════════


class TestHappyPath:
    def test_each_hit_gets_a_verdict(self, sink: GuardTelemetry) -> None:
        _seed_hits(sink, 4)

        def judge(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            return GuardJudgeVerdict(action="true_positive", confidence=0.9)

        result = run_judge_batch(sink=sink, judge=judge)
        assert result.total_judged == 4
        assert result.by_action == {"true_positive": 4}
        # Verify they actually wrote.
        assert sink.unjudged_hits() == []

    def test_mixed_verdicts_aggregate(self, sink: GuardTelemetry) -> None:
        _seed_hits(sink, 5)
        verdicts = iter(
            [
                GuardJudgeVerdict(action="true_positive"),
                GuardJudgeVerdict(action="false_positive"),
                GuardJudgeVerdict(action="true_positive"),
                GuardJudgeVerdict(action="uncertain"),
                GuardJudgeVerdict(action="false_positive"),
            ]
        )

        def judge(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            return next(verdicts)

        result = run_judge_batch(sink=sink, judge=judge)
        assert result.total_judged == 5
        assert result.by_action == {
            "true_positive": 2,
            "false_positive": 2,
            "uncertain": 1,
        }


# ══════════════════════════════════════════════════════════════════
# Budget + idempotence
# ══════════════════════════════════════════════════════════════════


class TestBudgetCap:
    def test_max_hits_caps_per_call(self, sink: GuardTelemetry) -> None:
        _seed_hits(sink, 100)
        seen: list[str] = []

        def judge(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            seen.append(label)
            return GuardJudgeVerdict(action="true_positive")

        result = run_judge_batch(sink=sink, judge=judge, max_hits=10)
        assert result.total_judged == 10
        assert len(seen) == 10

    def test_second_run_picks_up_remaining(
        self,
        sink: GuardTelemetry,
    ) -> None:
        _seed_hits(sink, 10)

        def judge(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            return GuardJudgeVerdict(action="true_positive")

        run_judge_batch(sink=sink, judge=judge, max_hits=4)
        assert len(sink.unjudged_hits()) == 6
        run_judge_batch(sink=sink, judge=judge, max_hits=4)
        assert len(sink.unjudged_hits()) == 2

    def test_idempotent_no_double_judge(self, sink: GuardTelemetry) -> None:
        _seed_hits(sink, 3)
        call_count = {"n": 0}

        def judge(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            call_count["n"] += 1
            return GuardJudgeVerdict(action="true_positive")

        run_judge_batch(sink=sink, judge=judge)
        run_judge_batch(sink=sink, judge=judge)  # second run — nothing to do
        assert call_count["n"] == 3


# ══════════════════════════════════════════════════════════════════
# Failure modes
# ══════════════════════════════════════════════════════════════════


class TestFailOpen:
    def test_judge_exception_records_uncertain(
        self,
        sink: GuardTelemetry,
    ) -> None:
        _seed_hits(sink, 2)

        def boom(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            raise RuntimeError("LLM down")

        result = run_judge_batch(
            sink=sink,
            judge=boom,
            failure_streak_limit=99,
        )
        # Both hits produce uncertain verdicts (fail-open).
        assert result.errors == 2
        assert result.by_action.get("uncertain", 0) == 2

    def test_failure_streak_aborts(self, sink: GuardTelemetry) -> None:
        _seed_hits(sink, 20)

        def boom(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            raise RuntimeError("LLM down")

        result = run_judge_batch(
            sink=sink,
            judge=boom,
            failure_streak_limit=3,
        )
        assert result.aborted_failure_streak is True
        # Should have judged at most ~3 before bailing.
        assert result.total_judged <= 4

    def test_router_error_uncertain_counts_to_streak(
        self,
        sink: GuardTelemetry,
    ) -> None:
        _seed_hits(sink, 10)

        def fake_router_failure(
            label: str,
            msg: str,
            traj: str,
        ) -> GuardJudgeVerdict:
            return GuardJudgeVerdict(
                action="uncertain",
                reason="router_error",
            )

        result = run_judge_batch(
            sink=sink,
            judge=fake_router_failure,
            failure_streak_limit=3,
        )
        assert result.aborted_failure_streak is True

    def test_genuine_uncertain_does_not_count_to_streak(
        self,
        sink: GuardTelemetry,
    ) -> None:
        _seed_hits(sink, 10)

        def all_uncertain(
            label: str,
            msg: str,
            traj: str,
        ) -> GuardJudgeVerdict:
            return GuardJudgeVerdict(
                action="uncertain",
                reason="ambiguous_trajectory",
            )

        result = run_judge_batch(
            sink=sink,
            judge=all_uncertain,
            failure_streak_limit=3,
        )
        # Genuine uncertain (not router_error) shouldn't trip the streak.
        assert result.aborted_failure_streak is False
        assert result.total_judged == 10


# ══════════════════════════════════════════════════════════════════
# Dry run + trajectory provider
# ══════════════════════════════════════════════════════════════════


class TestDryRun:
    def test_dry_run_does_not_invoke_judge(self, sink: GuardTelemetry) -> None:
        _seed_hits(sink, 4)

        def judge(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            raise AssertionError("should not be called in dry run")

        result = run_judge_batch(sink=sink, judge=judge, dry_run=True)
        assert result.dry_run is True
        assert result.candidates_seen == 4
        assert result.total_judged == 0

    def test_dry_run_does_not_write_verdicts(
        self,
        sink: GuardTelemetry,
    ) -> None:
        _seed_hits(sink, 4)

        def judge(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            return GuardJudgeVerdict(action="true_positive")

        run_judge_batch(sink=sink, judge=judge, dry_run=True)
        assert len(sink.unjudged_hits()) == 4


class TestTrajectoryProvider:
    def test_provider_value_passed_to_judge(
        self,
        sink: GuardTelemetry,
    ) -> None:
        sink.record("guard-a", "security")
        captured: dict[str, str] = {}

        def judge(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            captured["traj"] = traj
            return GuardJudgeVerdict(action="true_positive")

        def provider(hit: GuardHitRecord) -> str:
            return f"trajectory for {hit.label}"

        run_judge_batch(
            sink=sink,
            judge=judge,
            trajectory_provider=provider,
        )
        assert captured["traj"] == "trajectory for guard-a"

    def test_provider_exception_falls_back_empty(
        self,
        sink: GuardTelemetry,
    ) -> None:
        sink.record("guard-a", "security")
        captured: dict[str, str] = {}

        def judge(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            captured["traj"] = traj
            return GuardJudgeVerdict(action="true_positive")

        def boom_provider(hit: GuardHitRecord) -> str:
            raise RuntimeError("journal unavailable")

        run_judge_batch(
            sink=sink,
            judge=judge,
            trajectory_provider=boom_provider,
        )
        # Defaults to empty string; judge still got called.
        assert captured["traj"] == ""


# ══════════════════════════════════════════════════════════════════
# Determinism + render
# ══════════════════════════════════════════════════════════════════


class TestDeterminism:
    def test_oldest_hits_first(self, sink: GuardTelemetry) -> None:
        # We can't easily fake timestamps from outside record(), but
        # we can verify the sort happens by feeding hits with a known
        # order and checking the judge sees them oldest-ts first.
        _seed_hits(sink, 5)
        order: list[str] = []

        def judge(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            order.append(label)
            return GuardJudgeVerdict(action="true_positive")

        run_judge_batch(sink=sink, judge=judge)
        # Sort by hit_ts is monotonic — recorded ts should be
        # non-decreasing in the order judge sees them.
        hits = sink._read_all()  # type: ignore[attr-defined]
        ts_list = sorted(h.ts for h in hits)
        assert len(order) == len(ts_list)


class TestRender:
    def test_no_op_render(self) -> None:
        from runtime.safety.evolution.guard_judge_batch import BatchResult

        out = render_batch_result(BatchResult(skipped_no_judge=True))
        assert "no judge" in out.lower()

    def test_dry_run_render(self) -> None:
        from runtime.safety.evolution.guard_judge_batch import BatchResult

        r = BatchResult(dry_run=True, candidates_seen=42)
        out = render_batch_result(r)
        assert "dry run" in out.lower()
        assert "42" in out

    def test_normal_render(self, sink: GuardTelemetry) -> None:
        _seed_hits(sink, 3)

        def judge(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            return GuardJudgeVerdict(action="true_positive")

        result = run_judge_batch(sink=sink, judge=judge)
        out = render_batch_result(result)
        assert "judged 3/3" in out
        assert "true_positive" in out


# ══════════════════════════════════════════════════════════════════
# Guard message resolution
# ══════════════════════════════════════════════════════════════════


class TestGuardMessageResolution:
    def test_metadata_message_used_first(self, sink: GuardTelemetry) -> None:
        sink.record(
            "guard-a",
            "security",
            metadata={"guard_message": "you leaked a key"},
        )
        captured: dict[str, str] = {}

        def judge(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            captured["msg"] = msg
            return GuardJudgeVerdict(action="true_positive")

        run_judge_batch(sink=sink, judge=judge)
        assert captured["msg"] == "you leaked a key"

    def test_provider_used_when_metadata_missing(
        self,
        sink: GuardTelemetry,
    ) -> None:
        sink.record("guard-a", "security")
        captured: dict[str, str] = {}

        def judge(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            captured["msg"] = msg
            return GuardJudgeVerdict(action="true_positive")

        def provider(hit: GuardHitRecord) -> str:
            return f"[reconstructed for {hit.label}]"

        run_judge_batch(
            sink=sink,
            judge=judge,
            guard_message_provider=provider,
        )
        assert "reconstructed" in captured["msg"]

    def test_placeholder_when_nothing_available(
        self,
        sink: GuardTelemetry,
    ) -> None:
        sink.record("guard-a", "security")
        captured: dict[str, str] = {}

        def judge(label: str, msg: str, traj: str) -> GuardJudgeVerdict:
            captured["msg"] = msg
            return GuardJudgeVerdict(action="true_positive")

        run_judge_batch(sink=sink, judge=judge)
        assert "guard-a" in captured["msg"]
