"""Unit tests for evolution rollback mechanisms."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from runtime.safety.evolution.canary import (
    CanaryConfig,
    CanaryManager,
    CanaryPhase,
)
from runtime.safety.evolution.drift_monitor import (
    DriftMonitor,
)
from runtime.safety.evolution.fitness import (
    L1Fitness,
    compute_fitness,
    compute_l1,
)
from runtime.safety.evolution.proposal_ledger import (
    ProposalLedger,
    ProposalStatus,
)


class TestCanaryRollback:
    def test_force_rollback_sets_phase_to_rolled_back(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("my_skill")
        state = cm.force_rollback("my_skill")
        assert state is not None
        assert state.phase == CanaryPhase.ROLLED_BACK

    def test_force_rollback_nonexistent_returns_none(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        result = cm.force_rollback("no_such_skill")
        assert result is None

    def test_auto_rollback_on_low_success_rate(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("flaky_skill")
        for _ in range(6):
            cm.record_outcome("flaky_skill", False)
        state = cm.get_state("flaky_skill")
        assert state.phase == CanaryPhase.ROLLED_BACK
        assert state.current_rate < 0.50

    def test_rolled_back_skill_not_routed(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("dead_skill")
        cm.force_rollback("dead_skill")
        assert cm.should_route_to_skill("dead_skill") is False

    def test_force_rollback_resets_entered_ts(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        state = cm.register("ts_skill")
        original_ts = state.entered_ts
        later = datetime(2099, 12, 31, 23, 59, 59)
        with patch("runtime.safety.evolution.canary.datetime") as mock_dt:
            mock_dt.now.return_value = later
            mock_dt.isoformat = datetime.isoformat
            cm.force_rollback("ts_skill")
        updated = cm.get_state("ts_skill")
        assert updated.entered_ts != original_ts


class TestCanaryPromotion:
    def test_shadow_promotion_on_high_success(self, tmp_path):
        cm = CanaryManager(
            CanaryConfig(
                state_dir=str(tmp_path / "canary"),
                shadow_pass_rate=0.70,
            )
        )
        cm.register("promo_skill")
        for _ in range(12):
            cm.record_outcome("promo_skill", True)
        state = cm.get_state("promo_skill")
        assert state.phase != CanaryPhase.SHADOW
        assert state.phase in (
            CanaryPhase.CANARY_5,
            CanaryPhase.CANARY_25,
            CanaryPhase.CANARY_50,
            CanaryPhase.FULL,
        )

    def test_promotion_resets_sample_count(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("reset_skill")
        for _ in range(10):
            cm.record_outcome("reset_skill", True)
        state = cm.get_state("reset_skill")
        if state.phase != CanaryPhase.SHADOW:
            assert state.sample_count == 0


class TestProposalLedgerRollback:
    def test_mark_rolled_back_updates_status(self, tmp_path):
        ledger = ProposalLedger(tmp_path / "ledger.jsonl")
        r = ledger.propose(kind="add_lesson", description="rollback test")
        ledger.accept(r.proposal_id)
        ledger.mark_applied(r.proposal_id)
        result = ledger.mark_rolled_back(r.proposal_id)
        assert result is not None
        assert result.status == ProposalStatus.ROLLED_BACK

    def test_mark_rolled_back_sets_timestamp(self, tmp_path):
        ledger = ProposalLedger(tmp_path / "ledger.jsonl")
        r = ledger.propose(kind="add_lesson", description="ts test")
        ledger.mark_applied(r.proposal_id)
        result = ledger.mark_rolled_back(r.proposal_id)
        assert result is not None
        assert result.rolled_back_ts is not None
        parsed = datetime.fromisoformat(result.rolled_back_ts)
        assert parsed.year >= 2025

    def test_mark_rolled_back_nonexistent_returns_none(self, tmp_path):
        ledger = ProposalLedger(tmp_path / "ledger.jsonl")
        result = ledger.mark_rolled_back("nonexistent_id_12345")
        assert result is None

    def test_query_by_status_rolled_back(self, tmp_path):
        ledger = ProposalLedger(tmp_path / "ledger.jsonl")
        r1 = ledger.propose(kind="add_lesson", description="first")
        r2 = ledger.propose(kind="add_lesson", description="second")
        ledger.mark_applied(r1.proposal_id)
        ledger.mark_rolled_back(r1.proposal_id)
        ledger.mark_applied(r2.proposal_id)
        ledger.mark_rolled_back(r2.proposal_id)
        rolled_back = ledger.query(status=ProposalStatus.ROLLED_BACK)
        assert len(rolled_back) == 2
        assert all(r.status == ProposalStatus.ROLLED_BACK for r in rolled_back)


class TestDriftMonitorTriggers:
    def test_no_drift_on_first_check(self):
        monitor = DriftMonitor("test_agent")
        with (
            patch.object(monitor, "_check_soul_drift", return_value=None),
            patch.object(monitor, "_check_genome_drift", return_value=None),
            patch.object(monitor, "_check_score_drift", return_value=None),
        ):
            report = monitor.check()
            assert report.has_drift is False

    def test_score_regression_detected(self):
        from runtime.memory.learning.turn_scoring import TurnScore

        monitor = DriftMonitor("test_agent")
        monitor._baseline_score = 0.85
        declining_scores = [
            TurnScore(
                ts="t", agent_id="test_agent", score=0.5, reason="fail", soul_hash="h", rounds=3
            )
            for _ in range(10)
        ]
        with (
            patch.object(monitor, "_check_soul_drift", return_value=None),
            patch.object(monitor, "_check_genome_drift", return_value=None),
            patch(
                "runtime.memory.learning.turn_scoring.read_recent_scores",
                return_value=declining_scores,
            ),
        ):
            report = monitor.check()
            assert report.has_drift is True
            kinds = [e.kind for e in report.events]
            assert "score_regression" in kinds

    def test_severity_critical_on_large_drop(self):
        from runtime.memory.learning.turn_scoring import TurnScore

        monitor = DriftMonitor("test_agent")
        monitor._baseline_score = 0.90
        crashed_scores = [
            TurnScore(
                ts="t", agent_id="test_agent", score=0.4, reason="fail", soul_hash="h", rounds=3
            )
            for _ in range(10)
        ]
        with (
            patch.object(monitor, "_check_soul_drift", return_value=None),
            patch.object(monitor, "_check_genome_drift", return_value=None),
            patch(
                "runtime.memory.learning.turn_scoring.read_recent_scores",
                return_value=crashed_scores,
            ),
        ):
            report = monitor.check()
            assert report.max_severity == "critical"
            regression_events = [e for e in report.events if e.kind == "score_regression"]
            assert len(regression_events) == 1
            assert regression_events[0].severity == "critical"


class TestFitnessVerdict:
    def test_healthy_verdict(self):
        with patch("runtime.safety.evolution.fitness.compute_l1") as mock_l1:
            mock_l1.return_value = L1Fitness(
                score=0.85,
                trend="stable",
                success_rate=0.85,
                avg_rounds=3.0,
                soul_impact={},
            )
            with patch("runtime.safety.evolution.fitness.compute_l2", return_value=None):
                report = compute_fitness("test_agent")
                assert report.verdict == "healthy"

    def test_degraded_verdict(self):
        with patch("runtime.safety.evolution.fitness.compute_l1") as mock_l1:
            mock_l1.return_value = L1Fitness(
                score=0.65,
                trend="stable",
                success_rate=0.65,
                avg_rounds=5.0,
                soul_impact={},
            )
            with patch("runtime.safety.evolution.fitness.compute_l2", return_value=None):
                report = compute_fitness("test_agent")
                assert report.verdict == "degraded"

    def test_critical_verdict(self):
        with patch("runtime.safety.evolution.fitness.compute_l1") as mock_l1:
            mock_l1.return_value = L1Fitness(
                score=0.2,
                trend="regressing",
                success_rate=0.2,
                avg_rounds=10.0,
                soul_impact={},
            )
            with patch("runtime.safety.evolution.fitness.compute_l2", return_value=None):
                report = compute_fitness("test_agent")
                assert report.verdict == "critical"

    def test_regressing_trend(self):
        from runtime.memory.learning.turn_scoring import TurnScore

        first_half = [
            TurnScore(ts="t", agent_id="a", score=0.9, reason="ok", soul_hash="h", rounds=3)
            for _ in range(5)
        ]
        second_half = [
            TurnScore(ts="t", agent_id="a", score=0.5, reason="fail", soul_hash="h", rounds=8)
            for _ in range(5)
        ]
        scores = first_half + second_half
        with (
            patch("runtime.safety.evolution.fitness.read_recent_scores", return_value=scores),
            patch("runtime.safety.evolution.fitness.analyze_soul_impact", return_value={}),
        ):
            l1 = compute_l1("test_agent", window=10)
            assert l1.trend == "regressing"
