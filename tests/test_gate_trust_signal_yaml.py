"""Tests for trust-score integration into weekly report + tri-state
``enable_trust_signal`` resolution (kwarg / env / yaml)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from runtime.safety.evolution.guard_telemetry import GuardTelemetry
from runtime.safety.evolution.weekly_report import write_weekly_report
from runtime.safety.validation import gate

# ══════════════════════════════════════════════════════════════════
# Trust score in weekly report
# ══════════════════════════════════════════════════════════════════


def _seed_security_hits(sink: GuardTelemetry, n_tp: int, n_fp: int) -> None:
    for _ in range(n_tp + n_fp):
        sink.record("secret-leak guard", "security")
    hits = sink._read_all()  # type: ignore[attr-defined]
    for h in hits[:n_tp]:
        sink.record_verdict(
            "secret-leak guard",
            h.ts,
            "true_positive",
            hit_seq=h.seq,
        )
    for h in hits[n_tp:]:
        sink.record_verdict(
            "secret-leak guard",
            h.ts,
            "false_positive",
            hit_seq=h.seq,
        )


class TestTrustInReport:
    def test_summary_shows_trust_score(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        _seed_security_hits(sink, n_tp=8, n_fp=2)  # trust 0.20, suspect
        path = write_weekly_report(
            sink=sink,
            report_dir=tmp_path / "reports",
            now=datetime(2026, 6, 1),
        )
        body = path.read_text(encoding="utf-8")
        assert "Guard trust score" in body
        # 8/10 TP → trust 0.20 → "suspect"
        assert "0.20" in body
        assert "suspect" in body

    def test_trust_in_machine_readable_summary(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        _seed_security_hits(sink, n_tp=2, n_fp=8)  # trust 0.80
        path = write_weekly_report(
            sink=sink,
            report_dir=tmp_path / "reports",
            now=datetime(2026, 6, 1),
        )
        body = path.read_text(encoding="utf-8")
        match = re.search(r"```json\n(.+?)\n```\s*$", body, re.DOTALL)
        assert match is not None
        summary = json.loads(match.group(1))
        assert "trust_score" in summary
        assert summary["trust_score"] == 0.8
        assert summary["trust_bucket"] == "neutral"

    def test_trust_delta_shown_when_previous_available(
        self,
        tmp_path: Path,
    ) -> None:
        report_dir = tmp_path / "reports"
        report_dir.mkdir(parents=True)
        # Fake prior week with trust 0.10
        prior_summary = {
            "week_tag": "2026-22",
            "total_hits": 10,
            "judged_total": 10,
            "trust_score": 0.10,
            "trust_bucket": "suspect",
        }
        (report_dir / "2026-22.md").write_text(
            f"# Guard Telemetry — Week 2026-22\n\n```json\n{json.dumps(prior_summary)}\n```\n",
            encoding="utf-8",
        )
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        _seed_security_hits(sink, n_tp=3, n_fp=7)  # trust 1 - 3/10 = 0.70
        path = write_weekly_report(
            sink=sink,
            report_dir=report_dir,
            now=datetime(2026, 6, 1),  # week 23
        )
        body = path.read_text(encoding="utf-8")
        # Direction up (0.10 → 0.70)
        assert "trust 0.10 → 0.70" in body
        assert "**up**" in body

    def test_no_data_shows_na(self, tmp_path: Path) -> None:
        sink = GuardTelemetry(path=tmp_path / "hits.jsonl")
        # No hits — but force write so we can inspect.
        path = write_weekly_report(
            sink=sink,
            report_dir=tmp_path / "reports",
            now=datetime(2026, 6, 1),
            skip_if_empty=False,
        )
        body = path.read_text(encoding="utf-8")
        # Empty digest → trust score is 1.0 (perfect) per compute_guard_trust_score
        # since total_hits == 0 returns 1.0 (clean slate).
        # The point is the line exists and doesn't crash.
        assert "Guard trust score" in body


# ══════════════════════════════════════════════════════════════════
# Tri-state enable_trust_signal resolution
# ══════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _isolated_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Run inside a tmp dir so yaml lookup hits only what we put there."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ECHO_ENABLE_TRUST_SIGNAL", raising=False)
    return tmp_path


class TestTrustSignalResolution:
    def test_kwarg_true_wins_over_yaml_false(
        self,
        _isolated_cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (_isolated_cwd / "config.local.yaml").write_text(
            "safety:\n  enable_trust_signal: false\n",
            encoding="utf-8",
        )
        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: 0.05)
        v = gate.check_outbound(
            "hi",
            destination="channels:slack:c1",
            enable_trust_signal=True,
        )
        assert v.action == "human_gate"

    def test_kwarg_false_wins_over_yaml_true(
        self,
        _isolated_cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (_isolated_cwd / "config.local.yaml").write_text(
            "safety:\n  enable_trust_signal: true\n",
            encoding="utf-8",
        )
        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: 0.05)
        v = gate.check_outbound(
            "hi",
            destination="channels:slack:c1",
            enable_trust_signal=False,
        )
        assert v.action == "allow"

    def test_env_on_when_no_kwarg(
        self,
        _isolated_cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ECHO_ENABLE_TRUST_SIGNAL", "1")
        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: 0.05)
        v = gate.check_outbound("hi", destination="channels:slack:c1")
        assert v.action == "human_gate"

    def test_env_off_when_no_kwarg(
        self,
        _isolated_cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # YAML says on, env says off — env wins because kwarg is None.
        (_isolated_cwd / "config.local.yaml").write_text(
            "safety:\n  enable_trust_signal: true\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("ECHO_ENABLE_TRUST_SIGNAL", "0")
        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: 0.05)
        v = gate.check_outbound("hi", destination="channels:slack:c1")
        assert v.action == "allow"

    def test_yaml_only_when_no_kwarg_or_env(
        self,
        _isolated_cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (_isolated_cwd / "config.local.yaml").write_text(
            "safety:\n  enable_trust_signal: true\n",
            encoding="utf-8",
        )
        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: 0.05)
        v = gate.check_outbound("hi", destination="channels:slack:c1")
        assert v.action == "human_gate"

    def test_default_off_when_nothing_set(
        self,
        _isolated_cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: 0.05)
        v = gate.check_outbound("hi", destination="channels:slack:c1")
        assert v.action == "allow"

    def test_yaml_malformed_falls_through_to_default_off(
        self,
        _isolated_cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (_isolated_cwd / "config.local.yaml").write_text(
            "{garbage: not: valid",
            encoding="utf-8",
        )
        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: 0.05)
        v = gate.check_outbound("hi", destination="channels:slack:c1")
        # Broken yaml → fall through to default off → no escalation
        assert v.action == "allow"

    def test_env_truthy_variants(
        self,
        _isolated_cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.safety.validation import trust_signal

        monkeypatch.setattr(trust_signal, "fetch_current_trust_score", lambda **_: 0.05)
        for value in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv("ECHO_ENABLE_TRUST_SIGNAL", value)
            v = gate.check_outbound("hi", destination="channels:slack:c1")
            assert v.action == "human_gate", f"value {value!r} should enable"
