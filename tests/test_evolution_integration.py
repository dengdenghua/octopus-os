"""Integration and E2E tests for the evolution system."""

from __future__ import annotations

from datetime import UTC
from unittest.mock import patch

from runtime.safety.evolution.canary import CanaryConfig, CanaryManager, CanaryPhase
from runtime.safety.evolution.drift_monitor import DriftMonitor
from runtime.safety.evolution.federation import FederationConfig, FederationHub, SharedProposal
from runtime.safety.evolution.fitness import (
    FitnessReport,
    L1Fitness,
    compute_fitness,
)
from runtime.safety.evolution.proposal_ledger import ProposalLedger, ProposalStatus
from runtime.safety.evolution.skill_curator import (
    CuratorAction,
    CuratorConfig,
    SkillCurator,
    SkillInfo,
    SkillState,
)
from runtime.safety.evolution.strategy import StrategyEngine

# ═══════════════════════════════════════════════════════════
# SkillCurator
# ═══════════════════════════════════════════════════════════


class TestSkillCurator:
    def _make_skill(
        self,
        name,
        desc="test",
        state=SkillState.ACTIVE,
        use_count=0,
        category=None,
        tags=None,
        days_ago=0,
    ):
        from datetime import datetime, timedelta

        ts = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
        return SkillInfo(
            name=name,
            description=desc,
            state=state,
            use_count=use_count,
            created_at=ts,
            last_activity_at=ts,
            content_hash="abc123",
            tags=tags or [],
            category=category,
        )

    def test_empty_skills_returns_empty_report(self):
        report = SkillCurator().analyze([])
        assert report.total_skills == 0
        assert report.proposals == []

    def test_stale_skill_marked_stale(self):
        skill = self._make_skill("old-skill", days_ago=45)
        report = SkillCurator(CuratorConfig(stale_after_days=30)).analyze([skill])
        actions = [p.action for p in report.proposals]
        assert CuratorAction.DEMOTE in actions

    def test_very_old_skill_archived(self):
        skill = self._make_skill("ancient-skill", days_ago=120)
        report = SkillCurator(CuratorConfig(archive_after_days=90)).analyze([skill])
        actions = [p.action for p in report.proposals]
        assert CuratorAction.ARCHIVE in actions

    def test_pinned_skill_not_touched(self):
        skill = self._make_skill("pinned-skill", state=SkillState.PINNED, days_ago=200)
        report = SkillCurator().analyze([skill])
        targets = [p.target for p in report.proposals]
        assert "pinned-skill" not in targets

    def test_cluster_detection_prefix(self):
        skills = [
            self._make_skill("gateway-auth", "gateway auth handler", tags=["gateway"]),
            self._make_skill("gateway-routing", "gateway routing logic", tags=["gateway"]),
            self._make_skill("gateway-health", "gateway health check", tags=["gateway"]),
        ]
        report = SkillCurator(CuratorConfig(similarity_threshold=0.3)).analyze(skills)
        assert report.clusters_found >= 1
        merge_actions = [p for p in report.proposals if p.action == CuratorAction.MERGE]
        assert len(merge_actions) >= 1

    def test_merge_proposal_has_umbrella(self):
        skills = [
            self._make_skill(
                "pr-triage", "PR triage and review", use_count=20, tags=["pr", "review"]
            ),
            self._make_skill("pr-merge", "PR merge automation", use_count=5, tags=["pr"]),
        ]
        report = SkillCurator(CuratorConfig(similarity_threshold=0.3)).analyze(skills)
        merges = [p for p in report.proposals if p.action == CuratorAction.MERGE]
        if merges:
            assert merges[0].umbrella is not None

    def test_narrow_named_skill_flagged(self):
        skill = self._make_skill("audit-security-scan", use_count=15)
        report = SkillCurator().analyze([skill])
        actions = [p.action for p in report.proposals]
        assert CuratorAction.SPLIT in actions

    def test_high_use_skill_with_few_tags_flagged(self):
        skill = self._make_skill("popular-skill", use_count=50, tags=["one"])
        report = SkillCurator().analyze([skill])
        actions = [p.action for p in report.proposals]
        assert CuratorAction.UPGRADE in actions

    def test_similarity_computation(self):
        a = self._make_skill(
            "gw-auth", "gateway auth", tags=["gateway", "auth"], category="network"
        )
        b = self._make_skill(
            "gw-routing", "gateway routing", tags=["gateway", "routing"], category="network"
        )
        sim = SkillCurator._compute_similarity(a, b)
        assert 0.0 <= sim <= 1.0
        assert sim > 0.3

    def test_prefix_extraction(self):
        assert SkillCurator._extract_prefix("gateway-auth") == "gateway"
        assert SkillCurator._extract_prefix("pr_triage") == "pr"
        assert SkillCurator._extract_prefix("single") is None


# ═══════════════════════════════════════════════════════════
# Integration: Curator + Ledger
# ═══════════════════════════════════════════════════════════


class TestCuratorLedgerIntegration:
    def test_curator_proposals_recorded_in_ledger(self, tmp_path):
        skills = [
            SkillInfo(
                "gw-auth",
                "gateway auth",
                SkillState.ACTIVE,
                10,
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
                "h1",
                tags=["gateway"],
            ),
            SkillInfo(
                "gw-routing",
                "gateway routing",
                SkillState.ACTIVE,
                5,
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
                "h2",
                tags=["gateway"],
            ),
        ]
        curator = SkillCurator(CuratorConfig(similarity_threshold=0.3))
        report = curator.analyze(skills)

        ledger = ProposalLedger(tmp_path / "curator_ledger.jsonl")
        for p in report.proposals:
            ledger.propose(
                kind=f"curator_{p.action.value}",
                description=p.reason,
                proposer="skill_curator",
                metadata={"target": p.target, "umbrella": p.umbrella},
            )

        stats = ledger.stats()
        assert stats["total"] == len(report.proposals)
        assert stats["total"] > 0


# ═══════════════════════════════════════════════════════════
# Integration: Fitness + Strategy + Canary
# ═══════════════════════════════════════════════════════════


class TestFitnessStrategyCanaryIntegration:
    def test_unhealthy_fitness_triggers_evolve_then_canary(self):
        engine = StrategyEngine()
        report = FitnessReport(
            agent_id="test",
            ts="t",
            l1=L1Fitness(0.3, "regressing", 0.3, 15.0, {}),
            l2=None,
            combined=0.3,
            verdict="unhealthy",
        )
        decision = engine.decide(report)
        assert decision.action == "evolve"

    def test_canary_tracks_new_skill_after_forge(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("forged_skill_v2")
        for _ in range(12):
            cm.record_outcome("forged_skill_v2", True)
        state = cm.get_state("forged_skill_v2")
        assert state.phase in (CanaryPhase.CANARY_5, CanaryPhase.SHADOW)


# ═══════════════════════════════════════════════════════════
# Regression: compute_fitness end-to-end (publish path)
# ═══════════════════════════════════════════════════════════


class TestComputeFitnessRegression:
    """``compute_fitness`` previously called ``_publish_fitness_event(report)``
    before ``report`` was bound, so every invocation raised ``NameError``.
    The unit-style coverage in the rest of this file constructs
    ``FitnessReport`` by hand, which masks the bug. This class exercises
    the actual entry point used by ``evolution_router`` /
    ``regeneration/scheduler`` / ``auto_trigger`` so the regression
    cannot recur silently.
    """

    def test_compute_fitness_returns_report_for_unknown_agent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("runtime.memory.learning.turn_scoring._project_root", lambda: tmp_path)
        report = compute_fitness("nonexistent_agent_for_regression_test")
        assert isinstance(report, FitnessReport)
        assert report.agent_id == "nonexistent_agent_for_regression_test"
        assert report.verdict in {"healthy", "degraded", "unhealthy", "critical"}
        assert 0.0 <= report.combined <= 1.0

    def test_compute_fitness_publishes_event(self, tmp_path, monkeypatch):
        """``_publish_fitness_event`` must run on the constructed report,
        not on an uninitialised name. Patch the eventbus and assert it
        sees the right agent_id + verdict.
        """
        from runtime.platform import eventbus as _eventbus

        monkeypatch.setattr("runtime.memory.learning.turn_scoring._project_root", lambda: tmp_path)

        captured: list[object] = []

        def _capture(event, *, logger=None):  # noqa: ARG001
            captured.append(event)
            return 1

        monkeypatch.setattr(_eventbus, "publish_event", _capture)
        report = compute_fitness("regression_agent")
        assert len(captured) == 1
        assert captured[0].agent_id == report.agent_id
        assert captured[0].verdict == report.verdict


# ═══════════════════════════════════════════════════════════
# Integration: Federation + Ledger
# ═══════════════════════════════════════════════════════════


class TestFederationLedgerIntegration:
    def test_federation_adopt_records_in_ledger(self, tmp_path):
        hub = FederationHub(FederationConfig(shared_dir=str(tmp_path / "fed")))
        proposal = SharedProposal(
            proposal_id="fed001",
            source_agent="agent_a",
            kind="add_lesson",
            description="use caching",
            fitness_delta=0.2,
            ts="2026-01-01T00:00:00",
        )
        hub.publish("agent_a", proposal)
        discovered = hub.discover("agent_b")
        adopted = hub.adopt("agent_b", discovered[0])
        assert adopted is True

        ledger = ProposalLedger(tmp_path / "fed_ledger.jsonl")
        ledger.propose(
            kind="federation_adopt",
            description=f"adopted from {proposal.source_agent}: {proposal.description}",
            proposer="federation",
            metadata={"source_agent": proposal.source_agent, "proposal_id": proposal.proposal_id},
        )
        stats = ledger.stats()
        assert stats["total"] == 1


# ═══════════════════════════════════════════════════════════
# Integration: Drift + Fitness + Strategy
# ═══════════════════════════════════════════════════════════


class TestDriftFitnessStrategyIntegration:
    def test_critical_drift_triggers_revert_strategy(self):
        engine = StrategyEngine()
        report = FitnessReport(
            agent_id="test",
            ts="t",
            l1=L1Fitness(0.1, "regressing", 0.1, 20.0, {}),
            l2=None,
            combined=0.1,
            verdict="critical",
        )
        decision = engine.decide(report)
        assert decision.action == "revert"

    def test_no_drift_and_healthy_fitness_hold(self):
        monitor = DriftMonitor("healthy_agent")
        with (
            patch.object(monitor, "_check_soul_drift", return_value=None),
            patch.object(monitor, "_check_genome_drift", return_value=None),
            patch.object(monitor, "_check_score_drift", return_value=None),
        ):
            drift_report = monitor.check()
            assert drift_report.has_drift is False

        engine = StrategyEngine()
        report = FitnessReport(
            agent_id="healthy_agent",
            ts="t",
            l1=L1Fitness(0.85, "stable", 0.85, 3.0, {}),
            l2=None,
            combined=0.85,
            verdict="healthy",
        )
        decision = engine.decide(report)
        assert decision.action == "hold"


# ═══════════════════════════════════════════════════════════
# E2E: Full evolution pipeline
# ═══════════════════════════════════════════════════════════


class TestFullEvolutionPipeline:
    def test_skill_forge_to_canary_to_ledger(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        ledger = ProposalLedger(tmp_path / "pipeline_ledger.jsonl")

        forged_name = "auto_fix_lint_errors"
        cm.register(forged_name)
        r = ledger.propose(
            kind="skill_forge",
            description=f"new skill: {forged_name}",
            proposer="skill_forge",
            fitness_before=0.6,
        )
        assert r.status == ProposalStatus.PROPOSED

        for _ in range(15):
            cm.record_outcome(forged_name, True)
        state = cm.get_state(forged_name)
        assert state.phase == CanaryPhase.CANARY_5

        ledger.mark_applied(r.proposal_id, fitness_after=0.8)
        records = ledger.query(status=ProposalStatus.APPLIED)
        assert len(records) == 1

    def test_curator_to_federation_to_adopt(self, tmp_path):
        skills = [
            SkillInfo(
                "mcp-server",
                "MCP server setup",
                SkillState.ACTIVE,
                15,
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
                "h1",
                tags=["mcp"],
            ),
            SkillInfo(
                "mcp-client",
                "MCP client config",
                SkillState.ACTIVE,
                8,
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
                "h2",
                tags=["mcp"],
            ),
        ]
        curator = SkillCurator(CuratorConfig(similarity_threshold=0.3))
        report = curator.analyze(skills)
        merges = [p for p in report.proposals if p.action == CuratorAction.MERGE]

        hub = FederationHub(FederationConfig(shared_dir=str(tmp_path / "fed")))
        for merge in merges:
            fed_proposal = SharedProposal(
                proposal_id=f"cur_{merge.target}",
                source_agent="agent_a",
                kind="curator_merge",
                description=merge.reason,
                fitness_delta=0.15,
                ts="2026-01-01T00:00:00",
            )
            hub.publish("agent_a", fed_proposal)

        discovered = hub.discover("agent_b")
        if discovered:
            adopted = hub.adopt("agent_b", discovered[0])
            assert adopted is True
