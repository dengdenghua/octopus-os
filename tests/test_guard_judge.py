"""Tests for guard_judge + telemetry verdict integration.

Three layers:

* Parser robustness — VERDICT/REASON/CONFIDENCE survive minor LLM
  formatting drift, and any malformed reply degrades to ``uncertain``.
* Telemetry verdict storage — record_verdict appends a separate
  jsonl line, _read_with_verdicts joins them back, and unjudged_hits
  returns the right delta.
* Digest precision — TP/FP counts feed per-label precision, and the
  tuning_candidates list filters out guards proven noisy.
"""

from __future__ import annotations

from pathlib import Path

from runtime.safety.evolution.guard_judge import (
    GuardJudgeVerdict,
    _parse_verdict,
    null_guard_judge,
)
from runtime.safety.evolution.guard_telemetry import GuardTelemetry

# ══════════════════════════════════════════════════════════════════
# Parser
# ══════════════════════════════════════════════════════════════════


class TestVerdictParser:
    def test_clean_true_positive(self) -> None:
        v = _parse_verdict(
            "VERDICT: true_positive\nREASON: real bug\nCONFIDENCE: 0.9",
        )
        assert v.action == "true_positive"
        assert v.reason == "real bug"
        assert v.confidence == 0.9

    def test_clean_false_positive(self) -> None:
        v = _parse_verdict(
            "VERDICT: false_positive\nREASON: misfire\nCONFIDENCE: 0.7",
        )
        assert v.action == "false_positive"
        assert v.reason == "misfire"

    def test_uncertain_default(self) -> None:
        v = _parse_verdict(
            "VERDICT: uncertain\nREASON: not enough context\nCONFIDENCE: 0.2",
        )
        assert v.action == "uncertain"

    def test_lowercase_keys(self) -> None:
        v = _parse_verdict("verdict: true_positive\nreason: x\nconfidence: 1")
        assert v.action == "true_positive"

    def test_invalid_action_falls_back_uncertain(self) -> None:
        v = _parse_verdict("VERDICT: maybe\nREASON: ?\nCONFIDENCE: 0.5")
        assert v.action == "uncertain"

    def test_garbage_input_uncertain(self) -> None:
        assert _parse_verdict("").action == "uncertain"
        assert _parse_verdict("\n\n").action == "uncertain"
        assert _parse_verdict("garbage with no verdict").action == "uncertain"

    def test_confidence_clamped(self) -> None:
        v = _parse_verdict("VERDICT: true_positive\nCONFIDENCE: 99.9")
        assert v.confidence == 1.0
        v = _parse_verdict("VERDICT: true_positive\nCONFIDENCE: -5")
        assert v.confidence == 0.0

    def test_confidence_bad_value(self) -> None:
        v = _parse_verdict("VERDICT: true_positive\nCONFIDENCE: nope")
        assert v.confidence == 0.0


class TestNullGuardJudge:
    def test_returns_uncertain(self) -> None:
        v = null_guard_judge("any-label", "any message", "any traj")
        assert v.action == "uncertain"
        assert "no_judge_configured" in v.reason


# ══════════════════════════════════════════════════════════════════
# Telemetry verdict storage
# ══════════════════════════════════════════════════════════════════


class TestVerdictStorage:
    def test_record_verdict_appends_line(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        sink.record("magic-number guard", "code-smell")
        sink.record_verdict(
            "magic-number guard",
            "1970-01-01T00:00:00",
            "false_positive",
            reason="misfire",
            confidence=0.8,
        )
        text = (tmp_path / "hits.jsonl").read_text(encoding="utf-8")
        assert "kind" in text
        assert "false_positive" in text
        assert "misfire" in text

    def test_read_with_verdicts_separates_kinds(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        sink.record("guard-a", "security")
        sink.record_verdict("guard-a", "1970-01-01T00:00:00", "true_positive")
        hits, verdicts = sink._read_with_verdicts()  # type: ignore[attr-defined]
        assert len(hits) == 1
        assert hits[0].label == "guard-a"
        assert len(verdicts) == 1
        assert verdicts[0].action == "true_positive"

    def test_read_all_excludes_verdicts(self, tmp_path: Path) -> None:
        # Backward compat — the old _read_all signature returns hits only.
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        sink.record("guard-a", "security")
        sink.record_verdict("guard-a", "1970-01-01T00:00:00", "true_positive")
        only_hits = sink._read_all()  # type: ignore[attr-defined]
        assert len(only_hits) == 1
        assert only_hits[0].label == "guard-a"

    def test_unjudged_hits_correctly_filters(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        sink.record("guard-a", "security")
        sink.record("guard-b", "code-smell")
        sink.record("guard-c", "test-quality")
        # Read back the actual ts AND seq of guard-b so we judge the right hit.
        hits = sink._read_all()  # type: ignore[attr-defined]
        b_hit = next(h for h in hits if h.label == "guard-b")
        sink.record_verdict(
            "guard-b",
            b_hit.ts,
            "false_positive",
            hit_seq=b_hit.seq,
        )
        unjudged = sink.unjudged_hits()
        # b is now judged; a and c remain.
        unjudged_labels = {h.label for h in unjudged}
        assert unjudged_labels == {"guard-a", "guard-c"}

    def test_legacy_lines_without_kind_treated_as_hits(self, tmp_path: Path) -> None:
        # Simulate old-format telemetry (pre-verdict schema).
        path = tmp_path / "hits.jsonl"
        path.write_text(
            '{"label":"old","category":"security","ts":"2026-01-01T00:00:00",'
            '"goal_digest":"","iteration":null,"metadata":null}\n',
            encoding="utf-8",
        )
        sink = GuardTelemetry(path=path)
        hits, verdicts = sink._read_with_verdicts()  # type: ignore[attr-defined]
        assert len(hits) == 1
        assert hits[0].label == "old"
        assert verdicts == []


# ══════════════════════════════════════════════════════════════════
# Digest precision integration
# ══════════════════════════════════════════════════════════════════


class TestDigestPrecision:
    def test_label_precision_with_no_verdicts(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(5):
            sink.record("guard-a", "security")
        d = sink.digest()
        prec = d["label_precision"]["guard-a"]
        assert prec["tp"] == 0
        assert prec["fp"] == 0
        assert prec["unjudged"] == 5
        assert prec["precision"] is None

    def test_label_precision_mixed_verdicts(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        # 4 hits, judge 3 of them: 2 TP, 1 FP, 1 unjudged.
        for _ in range(4):
            sink.record("guard-a", "security")
        hits = sink._read_all()  # type: ignore[attr-defined]
        sink.record_verdict("guard-a", hits[0].ts, "true_positive")
        sink.record_verdict("guard-a", hits[1].ts, "true_positive")
        sink.record_verdict("guard-a", hits[2].ts, "false_positive")
        d = sink.digest()
        prec = d["label_precision"]["guard-a"]
        assert prec["tp"] == 2
        assert prec["fp"] == 1
        assert prec["judged"] == 3
        assert prec["unjudged"] == 1
        assert prec["precision"] is not None
        assert abs(prec["precision"] - 2 / 3) < 1e-3

    def test_uncertain_excluded_from_precision_denominator(
        self,
        tmp_path: Path,
    ) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(3):
            sink.record("guard-a", "security")
        hits = sink._read_all()  # type: ignore[attr-defined]
        sink.record_verdict("guard-a", hits[0].ts, "true_positive")
        sink.record_verdict("guard-a", hits[1].ts, "uncertain")
        sink.record_verdict("guard-a", hits[2].ts, "uncertain")
        d = sink.digest()
        prec = d["label_precision"]["guard-a"]
        assert prec["tp"] == 1
        assert prec["fp"] == 0
        assert prec["uncertain"] == 2
        assert prec["precision"] == 1.0  # 1 TP out of 1 graded (TP+FP)

    def test_noisy_guard_filtered_from_tuning_candidates(
        self,
        tmp_path: Path,
    ) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        # Noisy guard fires 30 times, all judged false_positive.
        for _ in range(30):
            sink.record("noisy-guard", "code-smell")
        hits = sink._read_all()  # type: ignore[attr-defined]
        for h in hits:
            sink.record_verdict("noisy-guard", h.ts, "false_positive")
        # Reliable guard fires 25 times, all judged true_positive.
        for _ in range(25):
            sink.record("reliable-guard", "security")
        hits2 = [h for h in sink._read_all() if h.label == "reliable-guard"]  # type: ignore[attr-defined]
        for h in hits2:
            sink.record_verdict("reliable-guard", h.ts, "true_positive")
        d = sink.digest(tuning_threshold=20, min_precision_for_tuning=0.5)
        labels = [c["label"] for c in d["tuning_candidates"]]
        assert "reliable-guard" in labels
        assert "noisy-guard" not in labels  # filtered: precision 0.0

    def test_unjudged_high_freq_guard_kept_in_tuning(
        self,
        tmp_path: Path,
    ) -> None:
        # Absence of evidence != evidence of absence — precision=None
        # guards still appear in tuning_candidates so the evolver
        # doesn't ignore them just because no judge has run yet.
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(25):
            sink.record("unjudged-guard", "security")
        d = sink.digest(tuning_threshold=20)
        labels = [c["label"] for c in d["tuning_candidates"]]
        assert "unjudged-guard" in labels

    def test_render_digest_shows_precision(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(22):
            sink.record("guard-a", "code-smell")
        hits = sink._read_all()  # type: ignore[attr-defined]
        for h in hits[:20]:
            sink.record_verdict("guard-a", h.ts, "true_positive")
        out = sink.render_digest(tuning_threshold=20)
        assert "guard-a" in out
        assert "precision" in out.lower()
        assert "100%" in out  # 20 TP / 20 graded

    def test_judged_total_in_digest(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(5):
            sink.record("guard-a", "security")
        hits = sink._read_all()  # type: ignore[attr-defined]
        for h in hits[:3]:
            sink.record_verdict("guard-a", h.ts, "true_positive")
        d = sink.digest()
        assert d["total_hits"] == 5
        assert d["judged_total"] == 3


class TestVerdictRecordSwallowsErrors:
    def test_record_verdict_failure_silent(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        # Force an open() failure by pointing at a directory.
        bad_dir = tmp_path / "as_dir"
        bad_dir.mkdir()
        sink._path = bad_dir  # type: ignore[attr-defined]
        # Must not raise.
        sink.record_verdict("guard-a", "ts", "true_positive")


class TestGuardJudgeVerdictDataclass:
    def test_defaults(self) -> None:
        v = GuardJudgeVerdict(action="uncertain")
        assert v.reason == ""
        assert v.confidence == 0.0
