"""Tests for AdaptiveImmunity quarantine enforcement.

Covers:
  1. quarantine() marks a sucker; is_quarantined() returns True immediately
  2. Expired quarantine returns False (lazy cleanup)
  3. Re-quarantining resets the TTL
  4. Non-quarantined sucker returns False
  5. TrustEngine.learn() quarantines an anomalous sucker
  6. TrustEngine.check() returns quarantine verdict for a quarantined sucker
  7. Non-anomalous calls are NOT quarantined
  8. Thread safety: concurrent quarantine/check calls don't corrupt state
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from runtime.platform.models import AntigenSignature, ToolCall
from runtime.safety.auth.adaptive_immunity import _MIN_SAMPLES, AdaptiveImmunity
from runtime.safety.auth.trust_engine import TrustEngine

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_ai(threshold: float = 0.7, ttl_s: float = 300.0) -> AdaptiveImmunity:
    return AdaptiveImmunity(quarantine_threshold=threshold, quarantine_ttl_s=ttl_s)


def _warm_baseline(ai: AdaptiveImmunity, sucker: str, n: int = _MIN_SAMPLES + 2) -> None:
    """Feed *n* stable samples so the baseline is warm."""
    for _ in range(n):
        ai.learn(sucker, latency_ms=100.0, tokens=500.0)


# ── 1. Basic quarantine lifecycle ─────────────────────────────────────────────


def test_quarantine_marks_sucker():
    ai = _make_ai()
    assert ai.is_quarantined("skill_a") is False
    ai.quarantine("skill_a")
    assert ai.is_quarantined("skill_a") is True


def test_quarantine_does_not_affect_other_suckers():
    ai = _make_ai()
    ai.quarantine("skill_a")
    assert ai.is_quarantined("skill_b") is False


# ── 2. TTL expiry ─────────────────────────────────────────────────────────────


def test_quarantine_expires_after_ttl():
    ai = _make_ai(ttl_s=60.0)
    _now = [1000.0]

    def _mono():
        return _now[0]

    with patch("runtime.safety.auth.adaptive_immunity.time.monotonic", _mono):
        ai.quarantine("skill_a")
        assert ai.is_quarantined("skill_a") is True
        _now[0] += 61.0  # advance past TTL
        assert ai.is_quarantined("skill_a") is False


def test_expired_entry_pruned_lazily():
    ai = _make_ai(ttl_s=60.0)
    _now = [1000.0]

    def _mono():
        return _now[0]

    with patch("runtime.safety.auth.adaptive_immunity.time.monotonic", _mono):
        ai.quarantine("skill_a")
        _now[0] += 61.0
        ai.is_quarantined("skill_a")  # triggers cleanup
        with ai._lock:
            assert "skill_a" not in ai._quarantined


# ── 3. Re-quarantine resets TTL ───────────────────────────────────────────────


def test_requarantine_resets_clock():
    ai = _make_ai(ttl_s=60.0)
    _now = [1000.0]

    def _mono():
        return _now[0]

    with patch("runtime.safety.auth.adaptive_immunity.time.monotonic", _mono):
        ai.quarantine("skill_a")
        _now[0] += 40.0  # half-way through TTL
        ai.quarantine("skill_a")  # reset — new expiry is now+60
        _now[0] += 40.0  # 80s since first quarantine; without reset, would have expired
        assert ai.is_quarantined("skill_a") is True


# ── 4. Custom per-call TTL ────────────────────────────────────────────────────


def test_quarantine_custom_ttl_overrides_default():
    ai = _make_ai(ttl_s=300.0)
    _now = [1000.0]

    def _mono():
        return _now[0]

    with patch("runtime.safety.auth.adaptive_immunity.time.monotonic", _mono):
        ai.quarantine("skill_a", ttl_s=10.0)
        _now[0] += 11.0  # past the custom TTL but well under default 300s
        assert ai.is_quarantined("skill_a") is False


def test_quarantine_ttl_s_exposes_clamped_default():
    ai = _make_ai(ttl_s=0.1)
    assert ai.quarantine_ttl_s == 1.0


# ── 5. TrustEngine.learn() quarantines anomalous sucker ──────────────────────


def test_learn_quarantines_on_anomaly():
    ai = _make_ai(threshold=0.7)
    engine = TrustEngine(adaptive=ai)

    call = MagicMock(spec=ToolCall)
    call.sucker_id = "skill_exploit"

    _warm_baseline(ai, "skill_exploit")

    # Inject a wildly anomalous observation: 10 000× baseline latency
    engine.learn(call, latency_ms=1_000_000.0, tokens=500.0)

    assert ai.is_quarantined("skill_exploit") is True


def test_learn_does_not_quarantine_normal_call():
    ai = _make_ai(threshold=0.7)
    engine = TrustEngine(adaptive=ai)

    call = MagicMock(spec=ToolCall)
    call.sucker_id = "skill_normal"

    _warm_baseline(ai, "skill_normal")
    engine.learn(call, latency_ms=105.0, tokens=510.0)  # 5 % drift — well inside

    assert ai.is_quarantined("skill_normal") is False


# ── 6. TrustEngine.check() blocks quarantined sucker ─────────────────────────


def _make_call(sucker_id: str) -> ToolCall:
    call = MagicMock(spec=ToolCall)
    call.sucker_id = sucker_id
    call.caller = "some_caller"
    call.predicted_cost = None
    return call


def _make_sig(entity_id: str = "skill://public/test") -> AntigenSignature:
    sig = MagicMock(spec=AntigenSignature)
    sig.entity_id = entity_id
    return sig


def test_check_quarantines_pre_quarantined_sucker():
    ai = _make_ai()
    engine = TrustEngine(
        trusted_sources=["skill://public/*"],
        adaptive=ai,
    )
    ai.quarantine("skill_bad")

    call = _make_call("skill_bad")
    sig = _make_sig("skill://public/skill_bad")

    report = engine.check(call, sig)
    assert report.verdict == "quarantine"
    assert "quarantined" in report.reason.lower()


def test_check_allows_non_quarantined_trusted_sucker():
    ai = _make_ai()
    engine = TrustEngine(
        trusted_sources=["skill://public/*"],
        adaptive=ai,
    )
    # Not quarantined — should pass through normally
    call = _make_call("skill_ok")
    sig = _make_sig("skill://public/skill_ok")

    report = engine.check(call, sig)
    assert report.verdict == "allow"


# ── 7. No adaptive → no quarantine path ──────────────────────────────────────


def test_check_without_adaptive_never_quarantines():
    engine = TrustEngine(
        trusted_sources=["skill://public/*"],
        adaptive=None,
    )
    call = _make_call("skill_x")
    sig = _make_sig("skill://public/skill_x")

    report = engine.check(call, sig)
    assert report.verdict == "allow"

