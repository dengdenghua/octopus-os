"""Immunity Adaptive tier — behavioural anomaly z-score scoring.

Pins the protocol invariants: I4 cold-start conservatism (0.5 with no
baseline), learned baselines, high z-score quarantine, and the
TrustEngine integration where adaptive only tightens (quarantines a
trusted source behaving anomalously) and self callers bypass it.
"""

from runtime.platform.models import (
    AntigenSignature,
    CostEntry,
    ToolCall,
)
from runtime.safety.auth.adaptive_immunity import AdaptiveImmunity
from runtime.safety.auth.trust_engine import TrustEngine


def _sig(entity_id: str) -> AntigenSignature:
    return AntigenSignature(
        entity_id=entity_id,
        entity_type="skill",
        content_hash="abc",
        origin="public",
    )


def _call(sucker: str = "run_sql", caller: str = "outsider", *, latency=0.0, tokens=0):
    return ToolCall(
        sucker_id=sucker,
        caller=caller,
        args={},
        predicted_cost=CostEntry(latency_ms=latency, tokens_in=tokens),
    )


class TestRiskScoring:
    def test_cold_start_is_conservative(self):
        a = AdaptiveImmunity()
        score = a.compute_risk("x", predicted_latency_ms=9999, predicted_tokens=9999)
        assert score.composite == 0.5
        assert "cold_start" in score.reason

    def test_normal_call_scores_low_after_baseline(self):
        a = AdaptiveImmunity()
        for _ in range(50):
            a.learn("x", latency_ms=100.0, tokens=200.0)
        score = a.compute_risk("x", predicted_latency_ms=105, predicted_tokens=205)
        assert score.composite < 0.2
        assert not a.is_anomalous(score)

    def test_outlier_call_scores_high(self):
        a = AdaptiveImmunity(quarantine_threshold=0.7)
        # Tight baseline around 100ms / 200 tokens with real variance.
        for i in range(50):
            a.learn("x", latency_ms=100.0 + (i % 5), tokens=200.0 + (i % 5))
        score = a.compute_risk("x", predicted_latency_ms=5000, predicted_tokens=9000)
        assert score.composite > 0.7
        assert a.is_anomalous(score)

    def test_zero_variance_baseline_flags_deviation(self):
        a = AdaptiveImmunity()
        for _ in range(20):
            a.learn("x", latency_ms=100.0, tokens=200.0)
        same = a.compute_risk("x", predicted_latency_ms=100, predicted_tokens=200)
        assert same.composite < 0.2  # identical to baseline → low
        off = a.compute_risk("x", predicted_latency_ms=999, predicted_tokens=999)
        assert off.composite > same.composite  # different → higher

    def test_window_caps_sample_count(self):
        a = AdaptiveImmunity(window_size=10)
        for _ in range(50):
            a.learn("x", latency_ms=1.0, tokens=1.0)
        assert a.sample_count("x") == 10

    def test_single_axis_anomaly_reaches_threshold(self):
        # A tool that hangs (latency spike) or exfiltrates (token spike)
        # is anomalous on ONE axis. Averaging two axes used to halve the
        # signal below threshold; max-of-axes must catch it.
        a = AdaptiveImmunity(quarantine_threshold=0.7)
        for i in range(50):
            a.learn("x", latency_ms=100.0 + (i % 5), tokens=200.0 + (i % 5))
        # Latency normal, tokens 100x (classic exfil signature).
        score = a.compute_risk("x", predicted_latency_ms=102, predicted_tokens=20000)
        assert a.is_anomalous(score), score.composite
        # And the mirror: latency spike, tokens normal.
        score2 = a.compute_risk("x", predicted_latency_ms=50000, predicted_tokens=201)
        assert a.is_anomalous(score2), score2.composite

    def test_absent_prediction_is_cold_start_not_anomaly(self):
        # The runtime doesn't populate predicted_cost (arrives 0/0).
        # That must NOT read as a 6σ anomaly against a real baseline —
        # otherwise wiring learn() would quarantine all normal traffic.
        a = AdaptiveImmunity(quarantine_threshold=0.7)
        for _ in range(50):
            a.learn("x", latency_ms=100.0, tokens=200.0)
        score = a.compute_risk("x", predicted_latency_ms=0.0, predicted_tokens=0.0)
        assert not a.is_anomalous(score)
        assert "no_prediction" in score.reason

    def test_threshold_floored_above_cold_start(self):
        # A threshold at/below cold_start would quarantine every
        # cold-start call with no recovery path.
        a = AdaptiveImmunity(quarantine_threshold=0.3, cold_start_score=0.5)
        assert a.quarantine_threshold > 0.5
        cold = a.compute_risk("never-seen", predicted_latency_ms=10, predicted_tokens=10)
        assert not a.is_anomalous(cold)

    def test_learn_builds_baseline_via_trust_engine(self):
        # TrustEngine.learn must reach the adaptive baseline (the
        # executor calls this post-execution).
        adaptive = AdaptiveImmunity()
        engine = TrustEngine(trusted_sources=["skill://public/*"], adaptive=adaptive)
        for _ in range(7):
            engine.learn(_call(sucker="run_sql"), latency_ms=120.0, tokens=240.0)
        assert adaptive.sample_count("run_sql") == 7


class TestTrustEngineIntegration:
    def _engine(self, **kw):
        return TrustEngine(
            trusted_sources=["skill://public/*"],
            adaptive=AdaptiveImmunity(**kw),
        )

    def test_disabled_by_default(self):
        engine = TrustEngine(trusted_sources=["skill://public/*"])
        assert engine.adaptive is None
        report = engine.check(_call(), _sig("skill://public/run_sql"))
        assert report.verdict == "allow"

    def test_trusted_but_anomalous_is_quarantined(self):
        engine = self._engine(quarantine_threshold=0.7)
        # Build a baseline, then probe with a wild outlier.
        for _ in range(30):
            engine.learn(_call(latency=100, tokens=200), latency_ms=100, tokens=200)
        report = engine.check(_call(latency=8000, tokens=9000), _sig("skill://public/run_sql"))
        assert report.verdict == "quarantine"
        assert report.strategy_used == "adaptive"
        assert report.risk is not None

    def test_trusted_and_normal_still_allowed(self):
        engine = self._engine()
        for _ in range(30):
            engine.learn(_call(latency=100, tokens=200), latency_ms=100, tokens=200)
        report = engine.check(_call(latency=102, tokens=201), _sig("skill://public/run_sql"))
        assert report.verdict == "allow"
        assert report.strategy_used == "innate"

    def test_self_caller_bypasses_adaptive(self):
        # I2: self-whitelisted callers never reach the adaptive tier,
        # even with a wild cost — no autoimmunity.
        engine = self._engine()
        for _ in range(30):
            engine.learn(_call(latency=100, tokens=200), latency_ms=100, tokens=200)
        report = engine.check(
            _call(caller="cerebrum", latency=99999, tokens=99999),
            _sig("skill://public/run_sql"),
        )
        assert report.verdict == "allow"
        assert report.strategy_used == "tolerance"

    def test_cold_start_does_not_quarantine(self):
        # I4: with no baseline (score 0.5 < 0.7), a trusted source is
        # allowed, not quarantined.
        engine = self._engine(quarantine_threshold=0.7)
        report = engine.check(_call(latency=9999, tokens=9999), _sig("skill://public/run_sql"))
        assert report.verdict == "allow"
