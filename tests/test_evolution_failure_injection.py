"""Failure injection tests for the evolution system."""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest
from runtime.safety.evolution.canary import CanaryConfig, CanaryManager, CanaryPhase
from runtime.safety.evolution.drift_monitor import DriftConfig, DriftMonitor
from runtime.safety.evolution.proposal_ledger import ProposalLedger, ProposalStatus
from runtime.safety.evolution.rollback_coordinator import RollbackCoordinator


class TestCanaryFailureInjection:
    def test_inject_skill_failure_triggers_rollback(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("fail_skill")
        for _ in range(6):
            cm.record_outcome("fail_skill", False)
        state = cm.get_state("fail_skill")
        assert state.phase == CanaryPhase.ROLLED_BACK
        assert state.current_rate == 0.0
        assert state.sample_count >= 5

    def test_inject_mixed_results_no_rollback(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("mixed_skill")
        outcomes = [True, True, False, True, False, True, False, True, False, True]
        for o in outcomes:
            cm.record_outcome("mixed_skill", o)
        state = cm.get_state("mixed_skill")
        assert state.phase != CanaryPhase.ROLLED_BACK
        assert state.current_rate == pytest.approx(0.60, abs=0.01)

    def test_inject_failure_after_promotion(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("promo_fail_skill")
        for _ in range(10):
            cm.record_outcome("promo_fail_skill", True)
        state_after_promo = cm.get_state("promo_fail_skill")
        assert state_after_promo.phase == CanaryPhase.CANARY_5
        for _ in range(5):
            cm.record_outcome("promo_fail_skill", False)
        state = cm.get_state("promo_fail_skill")
        assert state.phase == CanaryPhase.ROLLED_BACK


class TestLedgerFailureScenarios:
    def test_concurrent_proposal_writes(self, tmp_path):
        ledger = ProposalLedger(tmp_path / "ledger.jsonl")
        n_threads = 10
        barrier = threading.Barrier(n_threads)
        results: list[str | None] = [None] * n_threads
        errors: list[Exception | None] = [None] * n_threads

        def writer(idx: int) -> None:
            barrier.wait(timeout=5)
            try:
                r = ledger.propose(
                    kind="concurrent_test",
                    description=f"proposal_{idx}",
                    proposer=f"thread_{idx}",
                )
                results[idx] = r.proposal_id
            except Exception as exc:
                errors[idx] = exc

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        for i, exc in enumerate(errors):
            assert exc is None, f"thread {i} raised {exc}"

        all_records = ledger.query(kind="concurrent_test", limit=n_threads + 5)
        written_ids = {r for r in results if r is not None}
        found_ids = {r.proposal_id for r in all_records}
        assert written_ids == found_ids
        assert len(all_records) == n_threads

    def test_corrupted_ledger_line(self, tmp_path):
        ledger = ProposalLedger(tmp_path / "ledger.jsonl")
        r1 = ledger.propose(kind="good", description="before corruption")
        with (tmp_path / "ledger.jsonl").open("a", encoding="utf-8") as fh:
            fh.write("THIS IS NOT VALID JSON{{{}}}\n")
        r2 = ledger.propose(kind="good", description="after corruption")
        records = ledger.query(kind="good", limit=10)
        ids = {r.proposal_id for r in records}
        assert r1.proposal_id in ids
        assert r2.proposal_id in ids
        assert len(records) == 2

    def test_rollback_after_partial_apply(self, tmp_path):
        ledger = ProposalLedger(tmp_path / "ledger.jsonl")
        r = ledger.propose(
            kind="partial_apply",
            description="apply without fitness_after",
            fitness_before=0.75,
        )
        ledger.accept(r.proposal_id)
        applied = ledger.mark_applied(r.proposal_id)
        assert applied is not None
        assert applied.fitness_after is None
        rolled = ledger.mark_rolled_back(r.proposal_id)
        assert rolled is not None
        assert rolled.status == ProposalStatus.ROLLED_BACK
        assert rolled.fitness_after is None


class TestDriftFailureScenarios:
    def test_rapid_score_fluctuation(self):
        from runtime.memory.learning.turn_scoring import TurnScore

        monitor = DriftMonitor("fluctuation_agent", DriftConfig(score_drop_threshold=0.15))
        monitor._baseline_score = 0.80

        score_levels = [0.50, 0.90, 0.50, 0.90]
        reports = []
        for level in score_levels:
            scores = [
                TurnScore(
                    ts="t",
                    agent_id="fluctuation_agent",
                    score=level,
                    reason="test",
                    soul_hash="h",
                    rounds=3,
                )
                for _ in range(10)
            ]
            with (
                patch.object(monitor, "_check_soul_drift", return_value=None),
                patch.object(monitor, "_check_genome_drift", return_value=None),
                patch(
                    "runtime.memory.learning.turn_scoring.read_recent_scores",
                    return_value=scores,
                ),
            ):
                report = monitor.check()
                reports.append(report)

        for report in reports:
            assert report.agent_id == "fluctuation_agent"
            assert report.max_severity in ("critical", "warning", "info", "none")

        drift_count = sum(1 for r in reports if r.has_drift)
        assert drift_count >= 2

    def test_score_recovery_after_drift(self):
        from runtime.memory.learning.turn_scoring import TurnScore

        monitor = DriftMonitor("recovery_agent", DriftConfig(score_drop_threshold=0.15))
        monitor._baseline_score = 0.85

        low_scores = [
            TurnScore(
                ts="t",
                agent_id="recovery_agent",
                score=0.60,
                reason="fail",
                soul_hash="h",
                rounds=3,
            )
            for _ in range(10)
        ]
        with (
            patch.object(monitor, "_check_soul_drift", return_value=None),
            patch.object(monitor, "_check_genome_drift", return_value=None),
            patch(
                "runtime.memory.learning.turn_scoring.read_recent_scores",
                return_value=low_scores,
            ),
        ):
            drift_report = monitor.check()
        assert drift_report.has_drift is True
        assert monitor._baseline_score == pytest.approx(0.60, abs=0.01)

        recovered_scores = [
            TurnScore(
                ts="t",
                agent_id="recovery_agent",
                score=0.85,
                reason="ok",
                soul_hash="h",
                rounds=3,
            )
            for _ in range(10)
        ]
        with (
            patch.object(monitor, "_check_soul_drift", return_value=None),
            patch.object(monitor, "_check_genome_drift", return_value=None),
            patch(
                "runtime.memory.learning.turn_scoring.read_recent_scores",
                return_value=recovered_scores,
            ),
        ):
            _recovery_report = monitor.check()
        assert monitor._baseline_score == pytest.approx(0.85, abs=0.01)

        with (
            patch.object(monitor, "_check_soul_drift", return_value=None),
            patch.object(monitor, "_check_genome_drift", return_value=None),
            patch(
                "runtime.memory.learning.turn_scoring.read_recent_scores",
                return_value=recovered_scores,
            ),
        ):
            stable_report = monitor.check()
        assert stable_report.has_drift is False


class TestCanaryBoundaryConditions:
    def test_exact_rollback_threshold(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("boundary_skill")
        outcomes = [True, False, True, False, True, False, True, False, True, False]
        for o in outcomes:
            cm.record_outcome("boundary_skill", o)
        state = cm.get_state("boundary_skill")
        assert state.current_rate == pytest.approx(0.50)
        assert state.phase != CanaryPhase.ROLLED_BACK

    def test_minimum_samples_for_rollback(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("min_sample_skill")
        for _ in range(4):
            cm.record_outcome("min_sample_skill", False)
        state = cm.get_state("min_sample_skill")
        assert state.current_rate == 0.0
        assert state.sample_count == 4
        assert state.phase != CanaryPhase.ROLLED_BACK

    def test_rollback_threshold_custom(self, tmp_path):
        cm = CanaryManager(
            CanaryConfig(
                state_dir=str(tmp_path / "canary"),
                rollback_threshold=0.30,
            )
        )
        cm.register("custom_threshold_skill")
        outcomes = [True, True, False, False, False]
        for o in outcomes:
            cm.record_outcome("custom_threshold_skill", o)
        state_at_40pct = cm.get_state("custom_threshold_skill")
        assert state_at_40pct.current_rate == pytest.approx(0.40)
        assert state_at_40pct.phase != CanaryPhase.ROLLED_BACK

        cm2 = CanaryManager(
            CanaryConfig(
                state_dir=str(tmp_path / "canary2"),
                rollback_threshold=0.30,
            )
        )
        cm2.register("custom_threshold_skill2")
        outcomes2 = [True, True, False, False, False, False, False]
        for o in outcomes2:
            cm2.record_outcome("custom_threshold_skill2", o)
        state_below = cm2.get_state("custom_threshold_skill2")
        assert state_below.current_rate < 0.30
        assert state_below.phase == CanaryPhase.ROLLED_BACK


class TestRollbackCoordinatorVerification:
    def test_verify_canary_rollback_checks_live_canary_state(self, tmp_path):
        coord = RollbackCoordinator(
            canary_config=CanaryConfig(state_dir=str(tmp_path / "canary")),
            ledger_path=str(tmp_path / "ledger.jsonl"),
            state_dir=str(tmp_path / "rollback"),
        )
        coord._canary.register("verify_skill")

        result = coord.execute_rollback("canary:verify_skill", reason="test")
        assert result.success is True
        assert coord.verify_rollback(result.rollback_id).complete is True

        state = coord._canary.get_state("verify_skill")
        assert state is not None
        state.phase = CanaryPhase.SHADOW

        verification = coord.verify_rollback(result.rollback_id)
        assert verification.complete is False

    def test_canary_rollback_records_rolled_back_ledger_entry(self, tmp_path):
        coord = RollbackCoordinator(
            canary_config=CanaryConfig(state_dir=str(tmp_path / "canary")),
            ledger_path=str(tmp_path / "ledger.jsonl"),
            state_dir=str(tmp_path / "rollback"),
        )
        coord._canary.register("ledger_skill")

        result = coord.execute_rollback("canary:ledger_skill", reason="test")
        assert result.success is True

        records = coord._ledger.query(status=ProposalStatus.ROLLED_BACK, kind="canary_rollback")
        assert len(records) == 1
        assert records[0].metadata["rollback_id"] == result.rollback_id

        history = coord.rollback_history()
        assert len(history) == 1
        assert history[0].rollback_id == result.rollback_id
        assert history[0].target == "canary:ledger_skill"
