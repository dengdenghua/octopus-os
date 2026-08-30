"""Tests for the weekly guard-telemetry report writer."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from runtime.safety.evolution.guard_telemetry import GuardTelemetry
from runtime.safety.evolution.weekly_report import (
    _compute_delta,
    _iso_week_tag,
    _previous_report_summary,
    write_weekly_report,
)

# ══════════════════════════════════════════════════════════════════
# ISO week tag
# ══════════════════════════════════════════════════════════════════


class TestWeekTag:
    def test_format(self) -> None:
        tag = _iso_week_tag(datetime(2026, 6, 1))
        # ISO week 23 of 2026 — June 1 is a Monday.
        assert tag == "2026-23"

    def test_year_boundary(self) -> None:
        # Jan 1 2026 is a Thursday — falls into ISO week 1 of 2026.
        tag = _iso_week_tag(datetime(2026, 1, 1))
        assert tag == "2026-01"


# ══════════════════════════════════════════════════════════════════
# Delta computation
# ══════════════════════════════════════════════════════════════════


class TestComputeDelta:
    def test_no_previous_returns_unavailable(self) -> None:
        d = _compute_delta({"total_hits": 50, "judged_total": 30}, None)
        assert d == {"available": False}

    def test_up_direction(self) -> None:
        prev = {
            "week_tag": "2026-21",
            "total_hits": 30,
            "judged_total": 10,
        }
        cur = {"total_hits": 50, "judged_total": 30}
        d = _compute_delta(cur, prev)
        assert d["available"] is True
        assert d["total_hits"]["direction"] == "up"
        assert d["total_hits"]["delta"] == 20
        assert d["judged_total"]["direction"] == "up"

    def test_down_direction(self) -> None:
        prev = {"week_tag": "x", "total_hits": 80, "judged_total": 60}
        cur = {"total_hits": 50, "judged_total": 30}
        d = _compute_delta(cur, prev)
        assert d["total_hits"]["direction"] == "down"
        assert d["total_hits"]["delta"] == -30

    def test_flat_direction(self) -> None:
        prev = {"week_tag": "x", "total_hits": 50, "judged_total": 30}
        cur = {"total_hits": 50, "judged_total": 30}
        d = _compute_delta(cur, prev)
        assert d["total_hits"]["direction"] == "flat"
        assert d["judged_total"]["direction"] == "flat"


# ══════════════════════════════════════════════════════════════════
# Previous report summary lookup
# ══════════════════════════════════════════════════════════════════


def _write_report_with_summary(
    report_dir: Path,
    week_tag: str,
    summary: dict,
) -> None:
    """Helper: drop a fake report file with embedded JSON fence."""
    report_dir.mkdir(parents=True, exist_ok=True)
    body = (
        f"# Guard Telemetry — Week {week_tag}\n\n"
        f"<!-- machine-readable summary; do not edit -->\n"
        f"```json\n{json.dumps(summary, indent=2)}\n```\n"
    )
    (report_dir / f"{week_tag}.md").write_text(body, encoding="utf-8")


class TestPreviousReportSummary:
    def test_no_dir_returns_none(self, tmp_path: Path) -> None:
        assert _previous_report_summary(tmp_path / "missing") is None

    def test_no_files_returns_none(self, tmp_path: Path) -> None:
        tmp_path.mkdir(exist_ok=True)
        assert _previous_report_summary(tmp_path) is None

    def test_picks_most_recent(self, tmp_path: Path) -> None:
        _write_report_with_summary(
            tmp_path,
            "2026-20",
            {"week_tag": "2026-20", "total_hits": 10},
        )
        _write_report_with_summary(
            tmp_path,
            "2026-21",
            {"week_tag": "2026-21", "total_hits": 30},
        )
        prev = _previous_report_summary(tmp_path)
        assert prev["week_tag"] == "2026-21"
        assert prev["total_hits"] == 30

    def test_skips_current_week(self, tmp_path: Path, monkeypatch) -> None:
        # If the current week's report already exists, don't read it.
        from runtime.safety.evolution import weekly_report

        monkeypatch.setattr(weekly_report, "_iso_week_tag", lambda *a, **k: "2026-22")
        _write_report_with_summary(
            tmp_path,
            "2026-21",
            {"week_tag": "2026-21", "total_hits": 30},
        )
        _write_report_with_summary(
            tmp_path,
            "2026-22",
            {"week_tag": "2026-22", "total_hits": 50},
        )
        prev = _previous_report_summary(tmp_path)
        assert prev["week_tag"] == "2026-21"

    def test_malformed_json_silent(self, tmp_path: Path) -> None:
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / "2026-21.md").write_text(
            "# Header\n\n```json\nnot valid json\n```\n",
            encoding="utf-8",
        )
        # Returns None, doesn't raise.
        assert _previous_report_summary(tmp_path) is None

    def test_no_json_fence_returns_none(self, tmp_path: Path) -> None:
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / "2026-21.md").write_text(
            "# Just markdown, no fence\n",
            encoding="utf-8",
        )
        assert _previous_report_summary(tmp_path) is None


# ══════════════════════════════════════════════════════════════════
# write_weekly_report end-to-end
# ══════════════════════════════════════════════════════════════════


class TestWriteWeeklyReport:
    def test_skip_when_empty_default(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        path = write_weekly_report(
            sink=sink,
            report_dir=tmp_path / "reports",
        )
        assert path is None
        assert not (tmp_path / "reports").exists() or not list((tmp_path / "reports").iterdir())

    def test_force_write_on_empty(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        path = write_weekly_report(
            sink=sink,
            report_dir=tmp_path / "reports",
            skip_if_empty=False,
        )
        assert path is not None
        assert path.exists()
        body = path.read_text(encoding="utf-8")
        assert "Total hits" in body
        # Empty digest still has the JSON fence trailer.
        assert "```json" in body

    def test_writes_full_content(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(25):
            sink.record("magic-number guard", "code-smell")
        for _ in range(10):
            sink.record("secret-leak guard", "security")
        path = write_weekly_report(
            sink=sink,
            report_dir=tmp_path / "reports",
        )
        assert path is not None
        body = path.read_text(encoding="utf-8")
        assert "Total hits" in body
        assert "magic-number guard" in body
        assert "secret-leak guard" in body
        assert "By category" in body
        assert "code-smell" in body

    def test_filename_is_week_tag(
        self,
        tmp_path: Path,
    ) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        sink.record("guard-a", "security")
        path = write_weekly_report(
            sink=sink,
            report_dir=tmp_path / "reports",
            now=datetime(2026, 6, 1),
        )
        assert path is not None
        assert path.name == "2026-23.md"

    def test_first_run_no_delta(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        sink.record("guard-a", "security")
        path = write_weekly_report(
            sink=sink,
            report_dir=tmp_path / "reports",
        )
        body = path.read_text(encoding="utf-8")
        assert "first run" in body.lower() or "no previous" in body.lower()

    def test_second_run_shows_delta(self, tmp_path: Path) -> None:
        report_dir = tmp_path / "reports"
        # Simulate last week's report.
        _write_report_with_summary(
            report_dir,
            "2026-22",
            {"week_tag": "2026-22", "total_hits": 10, "judged_total": 5},
        )
        # This week's data.
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(20):
            sink.record("guard-a", "security")
        path = write_weekly_report(
            sink=sink,
            report_dir=report_dir,
            now=datetime(2026, 6, 1),  # week 23
        )
        body = path.read_text(encoding="utf-8")
        assert "vs **2026-22**" in body
        assert "10 → 20" in body
        assert "**up**" in body

    def test_machine_summary_fence_well_formed(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(5):
            sink.record("guard-a", "security")
        path = write_weekly_report(
            sink=sink,
            report_dir=tmp_path / "reports",
            now=datetime(2026, 6, 1),
        )
        body = path.read_text(encoding="utf-8")
        # Ensure the trailing JSON parses.
        import re

        match = re.search(r"```json\n(.+?)\n```\s*$", body, re.DOTALL)
        assert match is not None
        summary = json.loads(match.group(1))
        assert summary["week_tag"] == "2026-23"
        assert summary["total_hits"] == 5

    def test_overwrites_existing_same_week(self, tmp_path: Path) -> None:
        # Re-running on the same day must overwrite cleanly, not append.
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        sink.record("guard-a", "security")
        report_dir = tmp_path / "reports"
        first = write_weekly_report(
            sink=sink,
            report_dir=report_dir,
            now=datetime(2026, 6, 1),
        )
        # Add more hits and rerun the same week.
        for _ in range(10):
            sink.record("guard-b", "code-smell")
        second = write_weekly_report(
            sink=sink,
            report_dir=report_dir,
            now=datetime(2026, 6, 1),
        )
        assert first == second  # same path
        body = second.read_text(encoding="utf-8")
        assert "guard-b" in body
        # Should not contain duplicated headers from append.
        assert body.count("# Guard Telemetry — Week 2026-23") == 1


class TestTuningCandidatesInReport:
    def test_high_precision_guard_listed(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        for _ in range(25):
            sink.record("good-guard", "security")
        hits = sink._read_all()  # type: ignore[attr-defined]
        for h in hits:
            sink.record_verdict("good-guard", h.ts, "true_positive")
        path = write_weekly_report(
            sink=sink,
            report_dir=tmp_path / "reports",
        )
        body = path.read_text(encoding="utf-8")
        assert "Tuning candidates" in body
        assert "good-guard" in body
        assert "100%" in body

    def test_noisy_guard_filtered_from_tuning(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        # Noisy: 25 hits all FP.
        for _ in range(25):
            sink.record("noisy-guard", "code-smell")
        hits = sink._read_all()  # type: ignore[attr-defined]
        for h in hits:
            sink.record_verdict("noisy-guard", h.ts, "false_positive")
        path = write_weekly_report(
            sink=sink,
            report_dir=tmp_path / "reports",
        )
        body = path.read_text(encoding="utf-8")
        # Per-label table still has it (operator visibility) but the
        # tuning_candidates section excludes it.
        # Find the tuning section bounds.
        assert "Tuning candidates" in body
        section_start = body.index("Tuning candidates")
        per_label_start = body.index("Per-label precision")
        tuning_section = body[section_start:per_label_start]
        # noisy-guard should NOT appear in the tuning section.
        assert "noisy-guard" not in tuning_section
        # But should appear in the per-label table.
        assert "noisy-guard" in body[per_label_start:]
