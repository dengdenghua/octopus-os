"""Unit tests for runtime.safety.evolution modules."""

from __future__ import annotations

from unittest.mock import patch

from runtime.platform.config.schema import EvolveConfig, PlannerConfig, _pick_cheaper
from runtime.safety.evolution.canary import (
    CanaryConfig,
    CanaryManager,
    CanaryPhase,
)
from runtime.safety.evolution.drift_monitor import (
    DriftEvent,
    DriftMonitor,
)
from runtime.safety.evolution.federation import (
    FederationConfig,
    FederationHub,
    SharedProposal,
)
from runtime.safety.evolution.fitness import (
    FitnessReport,
    L1Fitness,
    compute_l1,
)
from runtime.safety.evolution.proposal_ledger import (
    ProposalLedger,
    ProposalStatus,
)
from runtime.safety.evolution.strategy import (
    StrategyEngine,
)
from runtime.safety.recovery.gepa_runs import (
    GepaRunRecord,
    enrich_run_records,
    record_from_result,
)

# ═══════════════════════════════════════════════════════════
# EvolveConfig
# ═══════════════════════════════════════════════════════════


class TestPickCheaper:
    def test_known_mapping(self):
        assert _pick_cheaper("mimo-v2.5-pro") == "mimo-v2-flash"
        assert _pick_cheaper("claude-sonnet-4-6") == "claude-haiku-4-5-20251001"
        assert _pick_cheaper("gpt-4o") == "gpt-4o-mini"

    def test_unknown_model_returns_same(self):
        assert _pick_cheaper("unknown-model") == "unknown-model"

    def test_already_cheap_returns_same(self):
        assert _pick_cheaper("gpt-4o-mini") == "gpt-4o-mini"


class TestEvolveConfig:
    def test_default_is_inherit(self):
        cfg = EvolveConfig()
        assert cfg.strategy == "inherit"

    def test_inherit_resolves_to_planner(self):
        cfg = EvolveConfig()
        planner = PlannerConfig(
            type="llm", model="mimo-v2.5-pro", base_url="https://api.example.com/v1"
        )
        model, url = cfg.resolve(planner)
        assert model == "mimo-v2.5-pro"
        assert url == "https://api.example.com/v1"

    def test_cheaper_same_provider(self):
        cfg = EvolveConfig(strategy="cheaper_same_provider")
        planner = PlannerConfig(
            type="llm", model="mimo-v2.5-pro", base_url="https://api.example.com/v1"
        )
        model, url = cfg.resolve(planner)
        assert model == "mimo-v2-flash"
        assert url == "https://api.example.com/v1"

    def test_explicit_overrides(self):
        cfg = EvolveConfig(
            strategy="explicit",
            model="mimo-v2-flash",
            base_url="https://api.xiaomimimo.com/v1",
            api_key_env="MIMO_API_KEY",
        )
        planner = PlannerConfig(type="llm", model="gpt-4o")
        model, url = cfg.resolve(planner)
        assert model == "mimo-v2-flash"
        assert url == "https://api.xiaomimimo.com/v1"

    def test_explicit_falls_back_to_planner_when_unset(self):
        cfg = EvolveConfig(strategy="explicit")
        planner = PlannerConfig(type="llm", model="gpt-4o", base_url="https://api.openai.com/v1")
        model, url = cfg.resolve(planner)
        assert model == "gpt-4o"
        assert url == "https://api.openai.com/v1"


# ═══════════════════════════════════════════════════════════
# Fitness
# ═══════════════════════════════════════════════════════════


class TestComputeL1:
    def test_no_scores_returns_defaults(self):
        with patch("runtime.safety.evolution.fitness.read_recent_scores", return_value=[]):
            l1 = compute_l1("nonexistent_agent")
            assert l1.score == 0.5
            assert l1.trend == "stable"

    def test_all_success(self):
        from runtime.memory.learning.turn_scoring import TurnScore

        scores = [
            TurnScore(ts="t", agent_id="a", score=1.0, reason="success", soul_hash="abc", rounds=3)
        ]
        scores = scores * 10
        with (
            patch("runtime.safety.evolution.fitness.read_recent_scores", return_value=scores),
            patch("runtime.safety.evolution.fitness.analyze_soul_impact", return_value={}),
        ):
            l1 = compute_l1("test_agent", window=10)
            assert l1.score == 1.0
            assert l1.success_rate == 1.0


class TestFitnessReport:
    def test_verdict_healthy(self):
        report = FitnessReport(
            agent_id="test",
            ts="t",
            l1=L1Fitness(0.9, "stable", 0.9, 3.0, {}),
            l2=None,
            combined=0.9,
            verdict="healthy",
        )
        assert report.verdict == "healthy"

    def test_verdict_critical(self):
        report = FitnessReport(
            agent_id="test",
            ts="t",
            l1=L1Fitness(0.1, "regressing", 0.1, 20.0, {}),
            l2=None,
            combined=0.1,
            verdict="critical",
        )
        assert report.verdict == "critical"


# ═══════════════════════════════════════════════════════════
# DriftMonitor
# ═══════════════════════════════════════════════════════════


class TestDriftMonitor:
    def test_no_drift_on_first_check(self):
        monitor = DriftMonitor("test_agent")
        with (
            patch.object(monitor, "_check_soul_drift", return_value=None),
            patch.object(monitor, "_check_genome_drift", return_value=None),
            patch.object(monitor, "_check_score_drift", return_value=None),
        ):
            report = monitor.check()
            assert report.has_drift is False
            assert report.max_severity == "none"

    def test_soul_change_detected(self):
        monitor = DriftMonitor("test_agent")
        monitor._last_soul_hash = "old_hash"
        with patch.object(monitor, "_check_soul_drift") as mock_soul:
            mock_soul.return_value = DriftEvent(
                kind="soul_change",
                severity="info",
                detail="changed",
                ts="t",
            )
            with (
                patch.object(monitor, "_check_genome_drift", return_value=None),
                patch.object(monitor, "_check_score_drift", return_value=None),
            ):
                report = monitor.check()
                assert report.has_drift is True
                assert report.max_severity == "info"


# ═══════════════════════════════════════════════════════════
# ProposalLedger
# ═══════════════════════════════════════════════════════════


class TestProposalLedger:
    def test_propose_and_query(self, tmp_path):
        ledger = ProposalLedger(tmp_path / "ledger.jsonl")
        r = ledger.propose(kind="add_lesson", description="test", fitness_before=0.7)
        assert r.status == ProposalStatus.PROPOSED
        assert r.kind == "add_lesson"

        records = ledger.query(status=ProposalStatus.PROPOSED)
        assert len(records) == 1

    def test_mark_applied(self, tmp_path):
        ledger = ProposalLedger(tmp_path / "ledger.jsonl")
        r = ledger.propose(kind="add_lesson", description="test")
        ledger.mark_applied(r.proposal_id, fitness_after=0.85)
        records = ledger.query(status=ProposalStatus.APPLIED)
        assert len(records) == 1
        assert records[0].fitness_after == 0.85

    def test_reject_with_reason(self, tmp_path):
        ledger = ProposalLedger(tmp_path / "ledger.jsonl")
        r = ledger.propose(kind="add_lesson", description="bad")
        ledger.reject(r.proposal_id, reason="too risky")
        records = ledger.query(status=ProposalStatus.REJECTED)
        assert len(records) == 1

    def test_stats(self, tmp_path):
        ledger = ProposalLedger(tmp_path / "ledger.jsonl")
        ledger.propose(kind="a", description="1")
        ledger.propose(kind="b", description="2")
        stats = ledger.stats()
        assert stats["total"] == 2

    def test_rollback(self, tmp_path):
        ledger = ProposalLedger(tmp_path / "ledger.jsonl")
        r = ledger.propose(kind="add_lesson", description="test")
        ledger.mark_applied(r.proposal_id)
        ledger.mark_rolled_back(r.proposal_id)
        records = ledger.query(status=ProposalStatus.ROLLED_BACK)
        assert len(records) == 1

    def test_enrich_run_records_attaches_lifecycle(self, tmp_path):
        ledger_path = tmp_path / "ledger.jsonl"
        canary_config = CanaryConfig(state_dir=str(tmp_path / "canary"))
        ledger = ProposalLedger(ledger_path)
        proposal = ledger.propose(
            kind="prompt_optimizer_winner",
            description="test winner",
            metadata={"recipe_id": "planner", "candidate_id": "cand-1"},
        )
        ledger.mark_applied(proposal.proposal_id)
        cm = CanaryManager(canary_config)
        _state = cm.register(
            "planner__cand-1",
            metadata={
                "recipe_id": "planner",
                "candidate_id": "cand-1",
                "proposal_id": proposal.proposal_id,
                "last_rollback_reason": "bad canary",
            },
        )
        cm.force_rollback("planner__cand-1")

        run = GepaRunRecord(
            ts=123.0,
            trigger="manual",
            recipe_id="planner",
            iterations_run=3,
            elapsed_s=1.2,
            front_size=1,
            best_candidate_id="cand-1",
            best_avg_score=0.91,
            best_rationale="tighten verification",
            best_prompt="prompt",
            applied=True,
        )
        enriched = enrich_run_records([run], ledger_path=ledger_path, canary_config=canary_config)
        assert enriched[0]["winner_proposal_id"] == proposal.proposal_id
        assert enriched[0]["winner_proposal_status"] == ProposalStatus.APPLIED.value
        assert enriched[0]["winner_canary_phase"] == CanaryPhase.ROLLED_BACK.value
        assert enriched[0]["winner_lifecycle_state"] == CanaryPhase.ROLLED_BACK.value
        assert enriched[0]["winner_rollback_reason"] == "bad canary"

    def test_record_from_result_keeps_native_evidence(self):
        class Candidate:
            candidate_id = "cand-1"
            avg_score = 0.91
            rationale = "tighten verification"
            prompt = "Verify before completion."

        class Result:
            iterations_run = 2
            elapsed_s = 1.0
            final_front = [Candidate()]
            best_avg = Candidate()
            history = [{"iter": 0, "front_size": 1, "best_avg": 0.5}]
            native_evaluation = [
                {
                    "candidate_id": "cand-1",
                    "total": 0.82,
                    "verdict": "promote",
                    "task_score": 0.9,
                    "constraint_score": 1.0,
                    "failure_coverage": 0.75,
                    "positive_preservation": 0.8,
                    "efficiency": 0.95,
                    "reasons": ["balanced candidate"],
                    "constraint_results": [{"verbose": "omitted"}],
                }
            ]
            native_replay = {
                "cases": [{"case_id": "case-1"}, {"case_id": "case-2"}],
                "candidates": [
                    {
                        "candidate_id": "cand-1",
                        "total": 0.77,
                        "reasons": ["replay coverage is strong"],
                        "case_results": [
                            {
                                "case_id": "case-2",
                                "kind": "failure",
                                "score": 0.4,
                                "reason": "missing failure-specific guidance",
                                "missing_signals": ["continue", "checkpoint"],
                            }
                        ],
                    }
                ],
            }
            native_sandbox_replay = {
                "case_count": 2,
                "candidates": [
                    {
                        "candidate_id": "cand-1",
                        "total": 0.72,
                        "passed": False,
                        "reasons": ["sandbox replay weak cases: case-2"],
                        "case_results": [
                            {
                                "case_id": "case-2",
                                "kind": "failure",
                                "score": 0.5,
                                "sandbox_passed": True,
                                "reason": "missing failure-specific guidance",
                            }
                        ],
                    }
                ],
            }
            native_turn_replay = {
                "cases": [{"case_id": "turn-1"}, {"case_id": "turn-2"}],
                "candidates": [
                    {
                        "candidate_id": "cand-1",
                        "total": 0.69,
                        "passed": False,
                        "reasons": ["turn replay weak cases: turn-2"],
                        "case_results": [
                            {
                                "case_id": "turn-2",
                                "kind": "final_step_stuck",
                                "score": 0.4,
                                "passed": False,
                                "reason": "final_step_stuck missing: close-after-final",
                                "missing_signals": ["close-after-final"],
                            }
                        ],
                    }
                ],
            }
            native_llm_replay = {
                "cases": [{"case_id": "llm-1"}],
                "candidates": [
                    {
                        "candidate_id": "cand-1",
                        "total": 0.81,
                        "passed": False,
                        "reasons": ["llm replay weak cases: llm-1"],
                        "case_results": [
                            {
                                "case_id": "llm-1",
                                "kind": "report_truncation",
                                "score": 0.2,
                                "passed": False,
                                "reason": "model output was truncated",
                            }
                        ],
                    }
                ],
            }
            winner_proposal = {
                "ok": False,
                "reason": "native_replay_rejected",
                "candidate_id": "cand-1",
                "native_replay": {"too_large": "omitted"},
            }

        rec = record_from_result(Result(), trigger="manual", recipe_id="planner")

        assert rec.native_evaluation[0]["candidate_id"] == "cand-1"
        assert rec.native_evaluation[0]["verdict"] == "promote"
        assert "constraint_results" not in rec.native_evaluation[0]
        assert rec.native_replay["case_count"] == 2
        assert rec.native_replay["candidates"][0]["weak_cases"][0]["case_id"] == "case-2"
        assert rec.native_sandbox_replay["case_count"] == 2
        assert rec.native_sandbox_replay["candidates"][0]["weak_cases"][0]["case_id"] == "case-2"
        assert rec.native_turn_replay["case_count"] == 2
        assert rec.native_turn_replay["candidates"][0]["weak_cases"][0]["case_id"] == "turn-2"
        assert rec.native_llm_replay["case_count"] == 1
        assert rec.native_llm_replay["candidates"][0]["weak_cases"][0]["case_id"] == "llm-1"
        assert rec.winner_proposal == {
            "ok": False,
            "reason": "native_replay_rejected",
            "candidate_id": "cand-1",
        }

    def test_ledger_proposal_endpoint_returns_related_canary_and_rollback(
        self, tmp_path, monkeypatch
    ):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from runtime.sensing.gateway.evolution_router import create_evolution_router

        monkeypatch.chdir(tmp_path)
        ledger = ProposalLedger()
        proposal = ledger.propose(
            kind="prompt_optimizer_winner",
            description="test winner",
            metadata={"recipe_id": "planner", "candidate_id": "cand-1"},
        )
        ledger.mark_rolled_back(proposal.proposal_id)
        rollback = ledger.propose(
            kind="canary_rollback",
            description="rollback winner",
            metadata={"source_proposal_id": proposal.proposal_id},
        )
        ledger.mark_rolled_back(rollback.proposal_id)
        cm = CanaryManager()
        cm.register(
            "planner__cand-1",
            metadata={"proposal_id": proposal.proposal_id, "candidate_id": "cand-1"},
        )

        app = FastAPI()
        app.include_router(create_evolution_router())
        data = TestClient(app).get(f"/api/evolution/ledger/{proposal.proposal_id}").json()

        assert data["ok"] is True
        assert data["proposal"]["id"] == proposal.proposal_id
        assert data["proposal"]["status"] == ProposalStatus.ROLLED_BACK.value
        assert data["canaries"][0]["skill_name"] == "planner__cand-1"
        assert data["rollbacks"][0]["id"] == rollback.proposal_id


# ═══════════════════════════════════════════════════════════
# Canary
# ═══════════════════════════════════════════════════════════


class TestCanaryManager:
    def test_register_new_skill(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        state = cm.register("my_skill")
        assert state.phase == CanaryPhase.SHADOW
        assert state.skill_name == "my_skill"

    def test_register_idempotent(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        s1 = cm.register("skill_a")
        s2 = cm.register("skill_a")
        assert s1 is s2

    def test_promote_after_enough_successes(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("skill_b")
        for _ in range(15):
            cm.record_outcome("skill_b", True)
        state = cm.get_state("skill_b")
        assert state.phase == CanaryPhase.CANARY_5

    def test_rollback_on_high_failure(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("skill_c")
        for _ in range(8):
            cm.record_outcome("skill_c", False)
        state = cm.get_state("skill_c")
        assert state.phase == CanaryPhase.ROLLED_BACK

    def test_force_rollback(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("skill_d")
        state = cm.force_rollback("skill_d")
        assert state.phase == CanaryPhase.ROLLED_BACK

    def test_rolled_back_skill_not_routed(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("skill_e")
        cm.force_rollback("skill_e")
        assert cm.should_route_to_skill("skill_e") is False

    def test_full_skill_always_routed(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("skill_f")
        state = cm.get_state("skill_f")
        state.phase = CanaryPhase.FULL
        assert cm.should_route_to_skill("skill_f") is True

    def test_list_active(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("g1")
        cm.register("g2")
        cm.force_rollback("g2")
        active = cm.list_active()
        names = [s.skill_name for s in active]
        assert "g1" in names
        assert "g2" not in names

    def test_list_all_keeps_terminal_states_visible(self, tmp_path):
        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("active_skill")
        full = cm.register("full_skill")
        full.phase = CanaryPhase.FULL
        cm.register("rolled_skill")
        cm.force_rollback("rolled_skill")
        all_names = {s.skill_name for s in cm.list_all()}
        active_names = {s.skill_name for s in cm.list_active()}
        assert all_names == {"active_skill", "full_skill", "rolled_skill"}
        assert active_names == {"active_skill"}

    def test_canary_router_lists_full_and_rolled_back_states(self, tmp_path, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from runtime.sensing.gateway.evolution_router import create_evolution_router

        cm = CanaryManager(CanaryConfig(state_dir=str(tmp_path / "canary")))
        cm.register("active_skill", metadata={"candidate_id": "cand_1"})
        full = cm.register("full_skill", metadata={"proposal_id": "prop_1"})
        full.phase = CanaryPhase.FULL
        cm.register("rolled_skill", metadata={"last_rollback_reason": "bad canary"})
        cm.force_rollback("rolled_skill")
        monkeypatch.setattr("runtime.safety.evolution.canary.CanaryManager", lambda: cm)

        app = FastAPI()
        app.include_router(create_evolution_router())
        data = TestClient(app).get("/api/evolution/canary").json()

        phases = {row["skill_name"]: row["phase"] for row in data["canaries"]}
        assert data["ok"] is True
        assert data["active_count"] == 1
        assert data["full_count"] == 1
        assert data["rolled_back_count"] == 1
        assert phases["active_skill"] == CanaryPhase.SHADOW.value
        assert phases["full_skill"] == CanaryPhase.FULL.value
        assert phases["rolled_skill"] == CanaryPhase.ROLLED_BACK.value


# ═══════════════════════════════════════════════════════════
# Federation
# ═══════════════════════════════════════════════════════════


class TestFederationHub:
    def test_publish_and_discover(self, tmp_path):
        hub = FederationHub(FederationConfig(shared_dir=str(tmp_path / "fed")))
        proposal = SharedProposal(
            proposal_id="p1",
            source_agent="agent_a",
            kind="add_lesson",
            description="test lesson",
            fitness_delta=0.15,
            ts="2026-01-01T00:00:00",
        )
        hub.publish("agent_a", proposal)
        discovered = hub.discover("agent_b")
        assert len(discovered) == 1
        assert discovered[0].source_agent == "agent_a"

    def test_agent_does_not_discover_own(self, tmp_path):
        hub = FederationHub(FederationConfig(shared_dir=str(tmp_path / "fed")))
        proposal = SharedProposal(
            proposal_id="p2",
            source_agent="agent_a",
            kind="add_lesson",
            description="test",
            fitness_delta=0.1,
            ts="2026-01-01T00:00:00",
        )
        hub.publish("agent_a", proposal)
        discovered = hub.discover("agent_a")
        assert len(discovered) == 0

    def test_adopt_success(self, tmp_path):
        hub = FederationHub(FederationConfig(shared_dir=str(tmp_path / "fed")))
        proposal = SharedProposal(
            proposal_id="p3",
            source_agent="agent_a",
            kind="add_lesson",
            description="good lesson",
            fitness_delta=0.2,
            ts="2026-01-01T00:00:00",
        )
        hub.publish("agent_a", proposal)
        discovered = hub.discover("agent_b")
        result = hub.adopt("agent_b", discovered[0])
        assert result is True

    def test_adopt_rejects_low_fitness_delta(self, tmp_path):
        hub = FederationHub(
            FederationConfig(
                shared_dir=str(tmp_path / "fed"),
                adoption_threshold=0.5,
            )
        )
        proposal = SharedProposal(
            proposal_id="p4",
            source_agent="agent_a",
            kind="add_lesson",
            description="marginal",
            fitness_delta=0.05,
            ts="2026-01-01T00:00:00",
        )
        hub.publish("agent_a", proposal)
        discovered = hub.discover("agent_b")
        result = hub.adopt("agent_b", discovered[0])
        assert result is False

    def test_stats(self, tmp_path):
        hub = FederationHub(FederationConfig(shared_dir=str(tmp_path / "fed")))
        proposal = SharedProposal(
            proposal_id="p5",
            source_agent="agent_a",
            kind="add_lesson",
            description="test",
            fitness_delta=0.1,
            ts="2026-01-01T00:00:00",
        )
        hub.publish("agent_a", proposal)
        stats = hub.stats()
        assert stats["agents"] == 1
        assert stats["proposals"] == 1


# ═══════════════════════════════════════════════════════════
# StrategyEngine
# ═══════════════════════════════════════════════════════════


class TestStrategyEngine:
    def _make_report(self, combined: float, verdict: str) -> FitnessReport:
        return FitnessReport(
            agent_id="test",
            ts="t",
            l1=L1Fitness(
                score=combined,
                trend="stable",
                success_rate=combined,
                avg_rounds=5.0,
                soul_impact={},
            ),
            l2=None,
            combined=combined,
            verdict=verdict,
        )

    def test_critical_triggers_revert(self):
        engine = StrategyEngine()
        decision = engine.decide(self._make_report(0.1, "critical"))
        assert decision.action == "revert"
        assert decision.confidence == "high"

    def test_low_fitness_triggers_evolve(self):
        engine = StrategyEngine()
        decision = engine.decide(self._make_report(0.3, "unhealthy"))
        assert decision.action == "evolve"

    def test_high_stable_triggers_explore(self):
        engine = StrategyEngine()
        for _ in range(6):
            engine.decide(self._make_report(0.85, "healthy"))
        decision = engine.decide(self._make_report(0.85, "healthy"))
        assert decision.action == "explore"

    def test_medium_fitness_triggers_hold(self):
        engine = StrategyEngine()
        decision = engine.decide(self._make_report(0.65, "degraded"))
        assert decision.action == "hold"

    def test_regressing_trend_triggers_evolve(self):
        engine = StrategyEngine()
        engine._history = [
            self._make_report(0.80, "degraded"),
            self._make_report(0.72, "degraded"),
            self._make_report(0.64, "degraded"),
            self._make_report(0.56, "degraded"),
        ]
        decision = engine.decide(self._make_report(0.48, "unhealthy"))
        assert decision.action == "evolve"
