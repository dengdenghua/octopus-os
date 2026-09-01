"""Integration tests for the full evolution rollback pipeline."""

from __future__ import annotations

from unittest.mock import patch

from runtime.safety.evolution.canary import CanaryConfig, CanaryManager, CanaryPhase
from runtime.safety.evolution.drift_monitor import DriftConfig, DriftMonitor
from runtime.safety.evolution.proposal_ledger import ProposalLedger, ProposalStatus
from runtime.safety.evolution.rollback_coordinator import RollbackCoordinator


class TestCanaryDeployAndRollbackE2E:
    def test_canary_shadow_to_rollback_e2e(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("shadow_skill")
        state = cm.get_state("shadow_skill")
        assert state.phase == CanaryPhase.SHADOW

        for _ in range(6):
            cm.record_outcome("shadow_skill", False)

        state = cm.get_state("shadow_skill")
        assert state.phase == CanaryPhase.ROLLED_BACK
        assert cm.should_route_to_skill("shadow_skill") is False

    def test_canary_force_rollback_e2e(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("force_skill")
        for _ in range(20):
            cm.record_outcome("force_skill", True)
        state = cm.get_state("force_skill")
        assert state.phase == CanaryPhase.CANARY_5

        rolled = cm.force_rollback("force_skill")
        assert rolled is not None
        assert rolled.phase == CanaryPhase.ROLLED_BACK
        assert cm.get_state("force_skill").phase == CanaryPhase.ROLLED_BACK

    def test_canary_rollback_then_reroute(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("reroute_skill")
        cm.force_rollback("reroute_skill")
        assert cm.should_route_to_skill("reroute_skill") is False

    def test_canary_threshold_breach_invokes_rollback_handler(self, tmp_path):
        coord = RollbackCoordinator(
            canary_config=CanaryConfig(state_dir=str(tmp_path / "canary")),
            ledger_path=str(tmp_path / "ledger.jsonl"),
            state_dir=str(tmp_path / "rollback"),
        )
        winner = coord._ledger.propose(
            kind="prompt_optimizer_winner",
            description="auto skill winner",
            metadata={
                "recipe_id": "auto_skill",
                "candidate_id": "cand-1",
            },
        )
        config = CanaryConfig(
            state_dir=str(tmp_path / "canary"),
            rollback_handler=lambda skill_name, state, reason: coord.execute_rollback(
                f"canary:{skill_name}",
                reason=reason,
                strategy="auto",
            ),
        )
        cm = CanaryManager(config)
        cm.register(
            "auto_skill",
            metadata={
                "proposal_id": winner.proposal_id,
                "proposal_kind": winner.kind,
                "recipe_id": "auto_skill",
                "candidate_id": "cand-1",
            },
        )

        for _ in range(6):
            cm.record_outcome("auto_skill", False)

        state = cm.get_state("auto_skill")
        assert state.phase == CanaryPhase.ROLLED_BACK
        records = coord._ledger.query(kind="canary_rollback")
        assert len(records) == 1
        assert records[0].metadata["target"] == "canary:auto_skill"
        winner_record = coord._ledger.query(kind="prompt_optimizer_winner", limit=10)[0]
        assert winner_record.status == ProposalStatus.ROLLED_BACK
        assert winner_record.metadata["last_rollback_reason"] == "canary threshold breached"


class TestProposalLedgerFullLifecycle:
    def test_propose_accept_apply_rollback(self, tmp_path):
        ledger = ProposalLedger(tmp_path / "ledger.jsonl")
        r = ledger.propose(kind="add_lesson", description="full lifecycle")
        assert r.status == ProposalStatus.PROPOSED

        accepted = ledger.accept(r.proposal_id)
        assert accepted is not None
        assert accepted.status == ProposalStatus.ACCEPTED

        applied = ledger.mark_applied(r.proposal_id)
        assert applied is not None
        assert applied.status == ProposalStatus.APPLIED
        assert applied.applied_ts is not None

        rolled = ledger.mark_rolled_back(r.proposal_id)
        assert rolled is not None
        assert rolled.status == ProposalStatus.ROLLED_BACK
        assert rolled.rolled_back_ts is not None

    def test_propose_accept_reject(self, tmp_path):
        ledger = ProposalLedger(tmp_path / "ledger.jsonl")
        r = ledger.propose(kind="add_lesson", description="reject flow")
        ledger.accept(r.proposal_id)
        rejected = ledger.reject(r.proposal_id, reason="unsafe pattern detected")
        assert rejected is not None
        assert rejected.status == ProposalStatus.REJECTED
        assert rejected.rejection_reason == "unsafe pattern detected"

    def test_multiple_proposals_query_by_status(self, tmp_path):
        ledger = ProposalLedger(tmp_path / "ledger.jsonl")
        r1 = ledger.propose(kind="add_lesson", description="first")
        r2 = ledger.propose(kind="add_lesson", description="second")
        r3 = ledger.propose(kind="add_lesson", description="third")

        ledger.accept(r1.proposal_id)
        ledger.mark_applied(r1.proposal_id)
        ledger.mark_rolled_back(r1.proposal_id)

        ledger.accept(r2.proposal_id)

        proposed = ledger.query(status=ProposalStatus.PROPOSED)
        assert len(proposed) == 1
        assert proposed[0].proposal_id == r3.proposal_id

        accepted = ledger.query(status=ProposalStatus.ACCEPTED)
        assert len(accepted) == 1
        assert accepted[0].proposal_id == r2.proposal_id

        rolled_back = ledger.query(status=ProposalStatus.ROLLED_BACK)
        assert len(rolled_back) == 1
        assert rolled_back[0].proposal_id == r1.proposal_id


class TestCanaryLedgerIntegration:
    def test_canary_rollback_creates_ledger_entry(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        ledger = ProposalLedger(tmp_path / "ledger.jsonl")

        cm.register("ledger_skill")
        for _ in range(6):
            cm.record_outcome("ledger_skill", False)

        state = cm.get_state("ledger_skill")
        assert state.phase == CanaryPhase.ROLLED_BACK

        ledger.propose(
            kind="canary_rollback",
            description=f"auto-rollback for ledger_skill at rate {state.current_rate:.2f}",
        )
        records = ledger.query(kind="canary_rollback")
        assert len(records) == 1
        assert records[0].status == ProposalStatus.PROPOSED

    def test_canary_force_rollback_with_fitness_tracking(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        ledger = ProposalLedger(tmp_path / "ledger.jsonl")

        cm.register("fitness_skill")
        for _ in range(8):
            cm.record_outcome("fitness_skill", True)
        for _ in range(2):
            cm.record_outcome("fitness_skill", False)

        state_before = cm.get_state("fitness_skill")
        fitness_before = state_before.current_rate

        cm.force_rollback("fitness_skill")

        ledger.propose(
            kind="canary_rollback",
            description="force rollback for fitness_skill",
            fitness_before=fitness_before,
        )
        records = ledger.query(kind="canary_rollback")
        assert len(records) == 1
        assert records[0].fitness_before is not None
        assert records[0].fitness_before == fitness_before


class TestDriftToRollbackPipeline:
    def test_score_regression_triggers_drift_detection(self, tmp_path):
        from runtime.memory.learning.turn_scoring import TurnScore

        monitor = DriftMonitor("test_agent", DriftConfig(score_drop_threshold=0.15))
        monitor._baseline_score = 0.85

        declining_scores = [
            TurnScore(
                ts="t",
                agent_id="test_agent",
                score=0.5,
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
                return_value=declining_scores,
            ),
        ):
            report = monitor.check()

        assert report.has_drift is True
        kinds = [e.kind for e in report.events]
        assert "score_regression" in kinds

    def test_drift_with_critical_severity(self, tmp_path):
        from runtime.memory.learning.turn_scoring import TurnScore

        monitor = DriftMonitor("test_agent", DriftConfig(score_drop_threshold=0.15))
        monitor._baseline_score = 0.90

        crashed_scores = [
            TurnScore(
                ts="t",
                agent_id="test_agent",
                score=0.4,
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
                return_value=crashed_scores,
            ),
        ):
            report = monitor.check()

        assert report.max_severity == "critical"
        regression_events = [e for e in report.events if e.kind == "score_regression"]
        assert len(regression_events) == 1
        assert regression_events[0].severity == "critical"
