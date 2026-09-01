"""Tests for the constitution trust signal — P1 → P0 bridge.

Covers:
* Score boundaries (0.0, 0.5, 1.0)
* Min-judged threshold gate (insufficient data → neutral)
* Empty / malformed digests degrade gracefully
* Category fallback when GUARD_REGISTRY is unavailable
* classify_trust_score buckets
* fetch_current_trust_score swallows telemetry failures
"""

from __future__ import annotations

from pathlib import Path

from runtime.safety.evolution.guard_telemetry import GuardTelemetry
from runtime.safety.validation.trust_signal import (
    HIGH_TRUST_FLOOR,
    LOW_TRUST_CEILING,
    NEUTRAL_SCORE,
    classify_trust_score,
    compute_guard_trust_score,
    fetch_current_trust_score,
    render_trust_summary,
)

# ══════════════════════════════════════════════════════════════════
# Compute trust score
# ══════════════════════════════════════════════════════════════════


class TestComputeTrustScore:
    def test_none_digest_neutral(self) -> None:
        assert compute_guard_trust_score(None) == NEUTRAL_SCORE

    def test_non_dict_neutral(self) -> None:
        assert compute_guard_trust_score("not a dict") == NEUTRAL_SCORE  # type: ignore[arg-type]

    def test_no_hits_perfect_trust(self) -> None:
        # Zero hits means clean slate — full trust.
        digest = {"total_hits": 0}
        assert compute_guard_trust_score(digest) == 1.0

    def test_under_min_judged_neutral(self, tmp_path: Path) -> None:
        # 3 hits, 3 judged TP — below default min_judged=5, so neutral.
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(3):
            sink.record("secret-leak guard", "security")
        hits = sink._read_all()  # type: ignore[attr-defined]
        for h in hits:
            sink.record_verdict(
                "secret-leak guard",
                h.ts,
                "true_positive",
                hit_seq=h.seq,
            )
        digest = sink.digest()
        # 3 TP < 5 → neutral.
        assert compute_guard_trust_score(digest) == NEUTRAL_SCORE

    def test_all_true_positive_low_trust(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(10):
            sink.record("secret-leak guard", "security")
        hits = sink._read_all()  # type: ignore[attr-defined]
        for h in hits:
            sink.record_verdict(
                "secret-leak guard",
                h.ts,
                "true_positive",
                hit_seq=h.seq,
            )
        digest = sink.digest()
        # 10/10 TP → trust = 1 - 1.0 = 0.0
        assert compute_guard_trust_score(digest) == 0.0

    def test_all_false_positive_high_trust(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(10):
            sink.record("secret-leak guard", "security")
        hits = sink._read_all()  # type: ignore[attr-defined]
        for h in hits:
            sink.record_verdict(
                "secret-leak guard",
                h.ts,
                "false_positive",
                hit_seq=h.seq,
            )
        digest = sink.digest()
        # 0/10 TP → trust = 1.0
        assert compute_guard_trust_score(digest) == 1.0

    def test_mixed_verdicts(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(10):
            sink.record("secret-leak guard", "security")
        hits = sink._read_all()  # type: ignore[attr-defined]
        for h in hits[:7]:
            sink.record_verdict(
                "secret-leak guard",
                h.ts,
                "true_positive",
                hit_seq=h.seq,
            )
        for h in hits[7:]:
            sink.record_verdict(
                "secret-leak guard",
                h.ts,
                "false_positive",
                hit_seq=h.seq,
            )
        digest = sink.digest()
        # 7/10 TP → trust = 1 - 0.7 = 0.3
        assert compute_guard_trust_score(digest) == 0.3

    def test_uncertain_excluded_from_grading(self, tmp_path: Path) -> None:
        # 5 TP + 0 FP + 5 uncertain → grading uses TP+FP only.
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(10):
            sink.record("secret-leak guard", "security")
        hits = sink._read_all()  # type: ignore[attr-defined]
        for h in hits[:5]:
            sink.record_verdict(
                "secret-leak guard",
                h.ts,
                "true_positive",
                hit_seq=h.seq,
            )
        for h in hits[5:]:
            sink.record_verdict(
                "secret-leak guard",
                h.ts,
                "uncertain",
                hit_seq=h.seq,
            )
        digest = sink.digest()
        # min_judged check uses tp+fp+uncertain (judged=10) so passes.
        # But TP rate uses tp/(tp+fp) = 5/5 = 1.0 → trust 0.0
        assert compute_guard_trust_score(digest) == 0.0

    def test_no_security_hits_neutral(self, tmp_path: Path) -> None:
        # All hits in a different category — no signal for security.
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(10):
            sink.record("magic-number guard", "code-smell")
        hits = sink._read_all()  # type: ignore[attr-defined]
        for h in hits:
            sink.record_verdict(
                "magic-number guard",
                h.ts,
                "true_positive",
                hit_seq=h.seq,
            )
        digest = sink.digest()
        # Default category=security; no security hits → neutral.
        assert compute_guard_trust_score(digest) == NEUTRAL_SCORE

    def test_can_target_other_category(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(10):
            sink.record("magic-number guard", "code-smell")
        hits = sink._read_all()  # type: ignore[attr-defined]
        for h in hits:
            sink.record_verdict(
                "magic-number guard",
                h.ts,
                "true_positive",
                hit_seq=h.seq,
            )
        digest = sink.digest()
        # Target code-smell category instead.
        assert compute_guard_trust_score(digest, category="code-smell") == 0.0


# ══════════════════════════════════════════════════════════════════
# classify_trust_score
# ══════════════════════════════════════════════════════════════════


class TestClassify:
    def test_high_trusted(self) -> None:
        assert classify_trust_score(0.95) == "trusted"
        assert classify_trust_score(HIGH_TRUST_FLOOR) == "trusted"

    def test_low_suspect(self) -> None:
        assert classify_trust_score(0.05) == "suspect"
        assert classify_trust_score(LOW_TRUST_CEILING) == "suspect"

    def test_neutral_band(self) -> None:
        assert classify_trust_score(0.5) == "neutral"
        assert classify_trust_score(0.7) == "neutral"
        assert classify_trust_score(0.3) == "neutral"


# ══════════════════════════════════════════════════════════════════
# render_trust_summary
# ══════════════════════════════════════════════════════════════════


class TestRender:
    def test_no_data(self) -> None:
        out = render_trust_summary(None)
        assert "no data" in out
        assert "0.50" in out

    def test_with_data(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(10):
            sink.record("secret-leak guard", "security")
        hits = sink._read_all()  # type: ignore[attr-defined]
        for h in hits:
            sink.record_verdict(
                "secret-leak guard",
                h.ts,
                "false_positive",
                hit_seq=h.seq,
            )
        digest = sink.digest()
        out = render_trust_summary(digest)
        assert "trusted" in out
        assert "1.00" in out


# ══════════════════════════════════════════════════════════════════
# fetch_current_trust_score — never raises
# ══════════════════════════════════════════════════════════════════


class TestFetchCurrent:
    def test_returns_float(self) -> None:
        # Whether the singleton sink has data or not, must return a
        # number in [0,1].
        result = fetch_current_trust_score()
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0

    def test_telemetry_failure_returns_neutral(self, monkeypatch) -> None:
        # Force GuardTelemetry constructor to fail.
        import runtime.safety.evolution.guard_telemetry as gt

        class _Boom:
            def __init__(self):
                raise RuntimeError("disk full")

        monkeypatch.setattr(gt, "GuardTelemetry", _Boom)
        assert fetch_current_trust_score() == NEUTRAL_SCORE
