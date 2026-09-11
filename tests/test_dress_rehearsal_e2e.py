"""End-to-end integration test — P0/P1/P2/P3 dress rehearsal.

This test wires the actual modules together (no per-module mocks):

ReAct turn produces guard hits (simulated via direct GuardTelemetry
writes) → judge batch turns hits into verdicts → digest computes
per-label precision → trust_signal derives a trust score → gate's
trust gate consults that score → prompt_evolver's trust gate also
consults it.

The simulated trajectory is deliberately tiny — the goal is to prove
the modules' INTEGRATION is healthy, not to re-cover their unit
behaviour. If this test passes, we have high confidence the full
chain wires up correctly when an operator turns on every knob.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.safety.evolution.guard_judge import GuardJudgeVerdict
from runtime.safety.evolution.guard_judge_batch import run_judge_batch
from runtime.safety.evolution.guard_telemetry import GuardTelemetry
from runtime.safety.validation import gate
from runtime.safety.validation.trust_signal import (
    classify_trust_score,
    compute_guard_trust_score,
)


def _seed_security_hits(
    sink: GuardTelemetry,
    label: str,
    n: int,
) -> None:
    for _ in range(n):
        sink.record(label, "security")


def _judge_all(
    sink: GuardTelemetry,
    *,
    verdict: str,
) -> None:
    """Convenience: judge every unjudged hit with a fixed verdict."""

    def fixed(_label, _msg, _traj):
        return GuardJudgeVerdict(action=verdict)

    run_judge_batch(sink=sink, judge=fixed, max_hits=1000)


@pytest.fixture
def empty_sink(tmp_path: Path) -> GuardTelemetry:
    return GuardTelemetry(path=tmp_path / "hits.jsonl")


# ══════════════════════════════════════════════════════════════════
# Path 1 — agent has been "good" recently → trust = high
# ══════════════════════════════════════════════════════════════════


class TestHappyAgentPath:
    def test_low_tp_high_trust_gate_passes_clean_message(
        self,
        empty_sink: GuardTelemetry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Seed 10 secret-leak hits, all judged FALSE positive
        # (i.e. detector noisy, agent fine).
        _seed_security_hits(empty_sink, "secret-leak guard", 10)
        _judge_all(empty_sink, verdict="false_positive")

        digest = empty_sink.digest()
        score = compute_guard_trust_score(digest)
        # 0/10 TP rate → trust = 1.0
        assert score == 1.0
        assert classify_trust_score(score) == "trusted"

        # Wire trust into the gate (mirrors what production wiring does).
        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: score)

        verdict = gate.check_outbound(
            "Hello team, here's the status update.",
            destination="channels:slack:c1",
            enable_trust_signal=True,
        )
        # Trusted agent → no escalation, message passes.
        assert verdict.action == "allow"


# ══════════════════════════════════════════════════════════════════
# Path 2 — agent has been "suspect" → trust = low → gate escalates
# ══════════════════════════════════════════════════════════════════


class TestSuspectAgentPath:
    def test_high_tp_low_trust_gate_escalates_clean_message(
        self,
        empty_sink: GuardTelemetry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Seed 10 secret-leak hits, all judged TRUE positive
        # (i.e. agent really tried to leak secrets).
        _seed_security_hits(empty_sink, "secret-leak guard", 10)
        _judge_all(empty_sink, verdict="true_positive")

        digest = empty_sink.digest()
        score = compute_guard_trust_score(digest)
        # 10/10 TP rate → trust = 0.0 → suspect
        assert score == 0.0
        assert classify_trust_score(score) == "suspect"

        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: score)

        verdict = gate.check_outbound(
            "Status update.",
            destination="channels:slack:c1",
            enable_trust_signal=True,
        )
        # Suspect agent + clean message → human_gate escalation.
        assert verdict.action == "human_gate"
        assert "trust_signal_escalate" in verdict.reason

    def test_suspect_blocks_relaxing_evolver_mutation(
        self,
        empty_sink: GuardTelemetry,
    ) -> None:
        # Same setup: agent is suspect.
        _seed_security_hits(empty_sink, "secret-leak guard", 10)
        _judge_all(empty_sink, verdict="true_positive")

        digest = empty_sink.digest()
        score = compute_guard_trust_score(digest)

        from runtime.safety.experiments.prompt_evolver import (
            EvolutionPolicy,
            PromptEvolver,
        )

        ev = PromptEvolver.__new__(PromptEvolver)
        ev._guard_digest_provider = lambda: digest
        ev._trust_score_provider = lambda: score
        ev.policy = EvolutionPolicy()
        ev.history = []

        # Suspect + relaxing mutation → blocked.
        allow, reason = ev._trust_gate_decision("Bypass the verification step.")
        assert allow is False
        assert "trust_gate_block" in reason

        # Suspect + tightening mutation → allowed.
        allow, _ = ev._trust_gate_decision(
            "Always re-read each edited file.",
        )
        assert allow is True


# ══════════════════════════════════════════════════════════════════
# Path 3 — full chain: hits → judge batch → digest → trust → gate
# ══════════════════════════════════════════════════════════════════


class TestFullChainIntegration:
    def test_end_to_end_chain_no_mocks(
        self,
        empty_sink: GuardTelemetry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Step 1: ReAct loop (simulated) records hits.
        _seed_security_hits(empty_sink, "secret-leak guard", 7)
        for _ in range(3):
            empty_sink.record("secret-leak guard", "security")

        # Step 2: judge batch grades them. 5 TP, 5 FP.
        verdicts = iter(
            [GuardJudgeVerdict(action="true_positive")] * 5
            + [GuardJudgeVerdict(action="false_positive")] * 5,
        )
        result = run_judge_batch(
            sink=empty_sink,
            judge=lambda *_a: next(verdicts),
            max_hits=20,
        )
        assert result.total_judged == 10
        assert result.by_action == {
            "true_positive": 5,
            "false_positive": 5,
        }

        # Step 3: digest computes per-label precision.
        digest = empty_sink.digest()
        precision = digest["label_precision"]["secret-leak guard"]["precision"]
        assert precision == 0.5

        # Step 4: trust_signal derives a score (5/10 TP = 0.5 trust).
        score = compute_guard_trust_score(digest)
        assert score == 0.5
        assert classify_trust_score(score) == "neutral"

        # Step 5: gate consults trust signal — neutral → no escalation.
        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: score)

        verdict = gate.check_outbound(
            "ok",
            destination="channels:slack:c1",
            enable_trust_signal=True,
        )
        assert verdict.action == "allow"

        # Step 6: evolver consults trust signal too.
        from runtime.safety.experiments.prompt_evolver import (
            EvolutionPolicy,
            PromptEvolver,
        )

        ev = PromptEvolver.__new__(PromptEvolver)
        ev._guard_digest_provider = lambda: digest
        ev._trust_score_provider = lambda: score
        ev.policy = EvolutionPolicy()
        ev.history = []
        # Neutral score: trust gate is OFF (only suspect blocks).
        allow, _ = ev._trust_gate_decision("Bypass everything.")
        assert allow is True
