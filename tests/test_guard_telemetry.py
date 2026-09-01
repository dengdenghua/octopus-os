"""Tests for guard-hit telemetry (P1 evolution-loop feed).

Covers the GuardTelemetry sink (record + stats + top_labels) and the
evaluate_guards recorder hook — that a firing guard records exactly one
(label, category) and that a recorder failure never breaks evaluation.
"""

from __future__ import annotations

from pathlib import Path

from runtime.core.cerebrum.react_guards import (
    GuardContext,
    evaluate_guards,
)
from runtime.core.cerebrum.react_types import ReActStep
from runtime.safety.evolution.guard_telemetry import (
    GuardHitRecord,
    GuardTelemetry,
)


def _step(iteration: int, *, action: str = "") -> ReActStep:
    return ReActStep(iteration=iteration, action=action)


# ══════════════════════════════════════════════════════════════════
# GuardTelemetry sink
# ══════════════════════════════════════════════════════════════════


class TestGuardTelemetrySink:
    def test_record_and_read(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        sink.record("secret-leak guard", "security")
        sink.record("magic-number guard", "code-smell")
        stats = sink.stats()
        assert stats["total"] == 2
        assert stats["by_category"]["security"] == 1
        assert stats["by_category"]["code-smell"] == 1

    def test_stats_aggregates_by_label(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        sink.record("secret-leak guard", "security")
        sink.record("secret-leak guard", "security")
        sink.record("magic-number guard", "code-smell")
        stats = sink.stats()
        assert stats["by_label"]["secret-leak guard"] == 2
        assert stats["by_label"]["magic-number guard"] == 1
        assert stats["by_category"]["security"] == 2

    def test_top_labels_sorted(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(3):
            sink.record("weak-test guard", "test-quality")
        sink.record("print-in-prod guard", "code-smell")
        top = sink.top_labels(n=5)
        assert top[0] == ("weak-test guard", 3)
        assert ("print-in-prod guard", 1) in top

    def test_empty_sink_stats(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "nope.jsonl")
        assert sink.stats() == {"total": 0, "by_label": {}, "by_category": {}}

    def test_record_swallows_errors(self, tmp_path: Path) -> None:
        # Point the sink at an un-writable location; record must not raise.
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        # Replace the path with a directory to force an open() failure.
        bad_dir = tmp_path / "as_dir"
        bad_dir.mkdir()
        sink._path = bad_dir  # type: ignore[attr-defined]
        # Should silently swallow — no exception.
        sink.record("x guard", "security")

    def test_metadata_roundtrip(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        sink.record(
            "secret-leak guard",
            "security",
            goal_digest="abc123",
            iteration=7,
            metadata={"path": "runtime/foo.py"},
        )
        records = sink._read_all()  # type: ignore[attr-defined]
        assert len(records) == 1
        assert records[0].iteration == 7
        assert records[0].metadata == {"path": "runtime/foo.py"}


class TestGuardHitRecord:
    def test_defaults(self) -> None:
        rec = GuardHitRecord(label="x", category="security", ts="2026-01-01T00:00:00")
        assert rec.goal_digest == ""
        assert rec.iteration is None
        assert rec.metadata is None


# ══════════════════════════════════════════════════════════════════
# digest — the evolution-loop artifact
# ══════════════════════════════════════════════════════════════════


class TestDigest:
    def test_empty_digest_well_formed(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        d = sink.digest()
        assert d["total_hits"] == 0
        assert d["by_label"] == {}
        assert d["dominant_category"] is None
        assert d["tuning_candidates"] == []

    def test_dominant_category(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(5):
            sink.record("weak-test guard", "test-quality")
        for _ in range(2):
            sink.record("secret-leak guard", "security")
        d = sink.digest()
        assert d["dominant_category"] == "test-quality"
        assert d["total_hits"] == 7

    def test_category_share_sums_to_one(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        sink.record("a guard", "security")
        sink.record("b guard", "code-smell")
        sink.record("c guard", "code-smell")
        d = sink.digest()
        assert abs(sum(d["category_share"].values()) - 1.0) < 1e-6
        assert d["category_share"]["code-smell"] == 0.6667

    def test_tuning_candidates_threshold(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(25):
            sink.record("magic-number guard", "code-smell")
        for _ in range(3):
            sink.record("secret-leak guard", "security")
        d = sink.digest(tuning_threshold=20)
        labels = [c["label"] for c in d["tuning_candidates"]]
        assert "magic-number guard" in labels
        assert "secret-leak guard" not in labels  # below threshold

    def test_render_digest_no_hits(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "nope.jsonl")
        assert "no hits" in sink.render_digest().lower()

    def test_render_digest_with_hits(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(22):
            sink.record("long-function guard", "code-smell")
        out = sink.render_digest(tuning_threshold=20)
        assert "long-function guard" in out
        assert "tuning candidates" in out.lower()
        assert "code-smell" in out


# ══════════════════════════════════════════════════════════════════
# evaluate_guards recorder hook
# ══════════════════════════════════════════════════════════════════


class TestEvaluateGuardsRecorder:
    def test_recorder_called_on_hit(self) -> None:
        sk = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "API_KEY = \\"' + sk + '\\""})'
                ),
            ),
        ]
        ctx = GuardContext(steps=steps, final_answer="done", is_code_mode=True)
        recorded: list[tuple[str, str]] = []
        hit = evaluate_guards(
            ctx,
            recorder=lambda label, cat, _msg: recorded.append((label, cat)),
        )
        assert hit is not None
        assert recorded == [("secret-leak guard", "security")]

    def test_recorder_not_called_when_clean(self) -> None:
        steps = [_step(1, action='read_file({"path": "runtime/foo.py"})')]
        ctx = GuardContext(steps=steps, final_answer="reviewed", is_code_mode=False)
        recorded: list[tuple[str, str]] = []
        hit = evaluate_guards(
            ctx,
            recorder=lambda label, cat, _msg: recorded.append((label, cat)),
        )
        assert hit is None
        assert recorded == []

    def test_recorder_failure_does_not_break(self) -> None:
        sk = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "API_KEY = \\"' + sk + '\\""})'
                ),
            ),
        ]
        ctx = GuardContext(steps=steps, final_answer="done", is_code_mode=True)

        def _boom(label: str, cat: str) -> None:
            raise RuntimeError("telemetry down")

        # Must still return the hit despite recorder raising.
        hit = evaluate_guards(ctx, recorder=_boom)
        assert hit is not None
        assert hit[0] == "secret-leak guard"

    def test_no_recorder_is_fine(self) -> None:
        steps = [_step(1, action='read_file({"path": "x.py"})')]
        ctx = GuardContext(steps=steps, final_answer="ok", is_code_mode=False)
        assert evaluate_guards(ctx) is None
