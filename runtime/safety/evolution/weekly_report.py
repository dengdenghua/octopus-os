"""Weekly guard-telemetry report — the human-facing P1 artifact.

Run once a week (cron). Reads the GuardTelemetry sink, computes the
digest, compares against last week's report, and writes a Markdown
file under ``logs/weekly_guard_reports/YYYY-WW.md``.

The report is what the team reads on Monday morning to answer:

* How often did guards fire this week?
* Which guards have high precision (real catches) vs noisy?
* Did the agent get better or worse week-over-week?
* What should the prompt-evolver focus on next?

Designed to be safe under cron: never raises, never blocks the loop,
no-ops cleanly if telemetry is empty or last week's report is gone.

CLI usage::

    python -m runtime.safety.evolution.weekly_report

Programmatic usage::

    from runtime.safety.evolution.weekly_report import write_weekly_report
    path = write_weekly_report()
    # path: logs/weekly_guard_reports/2026-22.md  (or None if telemetry empty)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime.safety.evolution.guard_telemetry import GuardTelemetry

_LOG = logging.getLogger("echo.evolution.weekly_report")

DEFAULT_REPORT_DIR = Path("logs/weekly_guard_reports")
DEFAULT_TELEMETRY_PATH = Path("data/guard_hits.jsonl")
WEEK_TAG_RE = re.compile(r"^\d{4}-\d{2}$")


@dataclass
class WeeklyReport:
    """Header metadata for one weekly report — drives both the
    Markdown writer and machine-readable summary."""

    week_tag: str  # e.g. "2026-22" (ISO year-week)
    generated_at: str
    digest: dict[str, Any]
    delta: dict[str, Any]  # vs previous report — see _compute_delta
    path: Path | None = None


def _iso_week_tag(now: datetime | None = None) -> str:
    """Return ``YYYY-WW`` for the given moment (default: now)."""
    moment = now or datetime.now()
    iso = moment.isocalendar()
    return f"{iso[0]:04d}-{iso[1]:02d}"


def _previous_report_summary(report_dir: Path) -> dict[str, Any] | None:
    """Read the most recent prior report's machine-summary block, if
    any, for week-over-week delta. Returns None on any failure."""
    if not report_dir.exists():
        return None
    candidates = sorted(
        (p for p in report_dir.glob("*.md") if WEEK_TAG_RE.match(p.stem)),
        key=lambda p: p.stem,
    )
    if not candidates:
        return None
    # Skip the current week if it's already there (re-running same day).
    current = _iso_week_tag()
    candidates = [p for p in candidates if p.stem != current]
    if not candidates:
        return None
    last = candidates[-1]
    try:
        text = last.read_text(encoding="utf-8")
    except OSError:
        return None
    # Reports embed a trailing JSON fence ```json {...} ``` for machine reads.
    match = re.search(r"```json\n(.+?)\n```\s*$", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _compute_delta(
    current_digest: dict[str, Any],
    previous_summary: dict[str, Any] | None,
    *,
    current_trust_score: float | None = None,
) -> dict[str, Any]:
    """Compute week-over-week deltas. ``previous_summary`` is None on
    first run; returns a structured "no comparison" placeholder.
    """
    if not previous_summary:
        return {"available": False}
    prev_total = int(previous_summary.get("total_hits") or 0)
    cur_total = int(current_digest.get("total_hits") or 0)
    prev_judged = int(previous_summary.get("judged_total") or 0)
    cur_judged = int(current_digest.get("judged_total") or 0)

    def _direction(cur: int, prev: int) -> str:
        if cur > prev:
            return "up"
        if cur < prev:
            return "down"
        return "flat"

    out: dict[str, Any] = {
        "available": True,
        "previous_week": previous_summary.get("week_tag"),
        "total_hits": {
            "current": cur_total,
            "previous": prev_total,
            "delta": cur_total - prev_total,
            "direction": _direction(cur_total, prev_total),
        },
        "judged_total": {
            "current": cur_judged,
            "previous": prev_judged,
            "delta": cur_judged - prev_judged,
            "direction": _direction(cur_judged, prev_judged),
        },
    }

    # Trust score delta — only when both sides are available.
    prev_trust = previous_summary.get("trust_score")
    if isinstance(prev_trust, (int, float)) and isinstance(current_trust_score, (int, float)):
        delta_val = round(current_trust_score - float(prev_trust), 4)
        if delta_val > 0.005:
            direction = "up"
        elif delta_val < -0.005:
            direction = "down"
        else:
            direction = "flat"
        out["trust_score"] = {
            "current": float(current_trust_score),
            "previous": float(prev_trust),
            "delta": delta_val,
            "direction": direction,
        }
    return out


def _render_markdown(
    report: WeeklyReport,
    *,
    tuning_threshold: int,
    min_precision_for_tuning: float,
) -> str:
    """Compose the full Markdown body."""
    d = report.digest
    delta = report.delta
    lines = [
        f"# Guard Telemetry — Week {report.week_tag}",
        "",
        f"_Generated: {report.generated_at}_",
        "",
        "## Summary",
        "",
        f"- **Total hits**: {d.get('total_hits', 0)}",
        f"- **Judged**: {d.get('judged_total', 0)}",
        f"- **Dominant category**: `{d.get('dominant_category') or 'n/a'}`",
    ]

    # Trust score — computed from the same digest, no extra I/O.
    trust_score: float | None = None
    trust_bucket: str | None = None
    try:
        from runtime.safety.validation.trust_signal import (
            classify_trust_score,
            compute_guard_trust_score,
        )

        trust_score = compute_guard_trust_score(d)
        trust_bucket = classify_trust_score(trust_score)
        lines.append(
            f"- **Guard trust score**: {trust_score:.2f} (`{trust_bucket}`)",
        )
    except Exception:  # noqa: BLE001 — trust must not break the report
        lines.append("- **Guard trust score**: n/a")
    lines.append("")

    # Week-over-week.
    if delta.get("available"):
        prev_tag = delta.get("previous_week", "?")
        th = delta["total_hits"]
        jh = delta["judged_total"]
        lines += [
            "## Week-over-week",
            "",
            f"- vs **{prev_tag}**:",
            f"  - hits {th['previous']} → {th['current']} "
            f"(**{th['direction']}**, Δ {th['delta']:+d})",
            f"  - judged {jh['previous']} → {jh['current']} "
            f"(**{jh['direction']}**, Δ {jh['delta']:+d})",
        ]
        # Trust delta (only when both this and prev have a score).
        prev_trust = delta.get("trust_score")
        if (
            isinstance(prev_trust, dict)
            and prev_trust.get("previous") is not None
            and prev_trust.get("current") is not None
        ):
            lines.append(
                f"  - trust {prev_trust['previous']:.2f} → "
                f"{prev_trust['current']:.2f} "
                f"(**{prev_trust['direction']}**, Δ {prev_trust['delta']:+.2f})",
            )
        lines.append("")
    else:
        lines += [
            "## Week-over-week",
            "",
            "- No previous report — first run.",
            "",
        ]

    # By category table.
    by_category = d.get("by_category") or {}
    if by_category:
        lines += [
            "## By category",
            "",
            "| Category | Hits | Share |",
            "|---|---:|---:|",
        ]
        share_map = d.get("category_share") or {}
        for cat, count in by_category.items():
            share = share_map.get(cat, 0.0)
            lines.append(f"| `{cat}` | {count} | {share:.0%} |")
        lines.append("")

    # Tuning candidates.
    candidates = d.get("tuning_candidates") or []
    if candidates:
        lines += [
            "## Tuning candidates",
            "",
            f"_Guards firing >= {tuning_threshold} times "
            f"with precision >= {min_precision_for_tuning:.0%} "
            "(or unjudged). These feed the prompt evolver._",
            "",
            "| Label | Hits | Precision |",
            "|---|---:|---:|",
        ]
        for cand in candidates:
            prec = cand.get("precision")
            prec_str = f"{prec:.0%}" if prec is not None else "?"
            lines.append(
                f"| `{cand['label']}` | {cand['count']} | {prec_str} |",
            )
        lines.append("")
    else:
        lines += [
            "## Tuning candidates",
            "",
            "_None this week — no guard above threshold with sufficient signal._",
            "",
        ]

    # Per-label precision detail (sorted by hits descending).
    label_precision = d.get("label_precision") or {}
    by_label = d.get("by_label") or {}
    if label_precision:
        lines += [
            "## Per-label precision",
            "",
            "| Label | Hits | TP | FP | Uncertain | Unjudged | Precision |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for label in by_label:  # by_label is already sorted descending
            stats = label_precision.get(label, {})
            prec = stats.get("precision")
            prec_str = f"{prec:.0%}" if prec is not None else "?"
            lines.append(
                f"| `{label}` | {by_label[label]} "
                f"| {stats.get('tp', 0)} | {stats.get('fp', 0)} "
                f"| {stats.get('uncertain', 0)} | {stats.get('unjudged', 0)} "
                f"| {prec_str} |",
            )
        lines.append("")

    # Embed machine-readable summary as a trailing JSON fence so the
    # NEXT week's report can read this one for delta computation.
    summary = {
        "week_tag": report.week_tag,
        "generated_at": report.generated_at,
        "total_hits": d.get("total_hits", 0),
        "judged_total": d.get("judged_total", 0),
        "dominant_category": d.get("dominant_category"),
        "trust_score": trust_score,
        "trust_bucket": trust_bucket,
    }
    lines += [
        "<!-- machine-readable summary; do not edit -->",
        "```json",
        json.dumps(summary, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)


def write_weekly_report(
    *,
    sink: GuardTelemetry | None = None,
    report_dir: Path | str = DEFAULT_REPORT_DIR,
    tuning_threshold: int = 20,
    min_precision_for_tuning: float = 0.5,
    now: datetime | None = None,
    skip_if_empty: bool = True,
) -> Path | None:
    """Generate this week's guard-telemetry report and return the path.

    Returns ``None`` (without writing) when telemetry has no hits AND
    ``skip_if_empty=True``. Setting that flag False forces a written
    file even on empty weeks — useful for "yes, the cron ran" signal.

    Errors during read are recovered fail-open: an unreadable telemetry
    file produces an "empty" report. Errors during write propagate so
    the cron operator notices a real filesystem problem.
    """
    actual_sink = sink if sink is not None else GuardTelemetry()
    try:
        digest = actual_sink.digest(
            tuning_threshold=tuning_threshold,
            min_precision_for_tuning=min_precision_for_tuning,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("digest read failed: %s — using empty digest", exc)
        digest = {
            "total_hits": 0,
            "judged_total": 0,
            "by_label": {},
            "by_category": {},
            "category_share": {},
            "dominant_category": None,
            "tuning_candidates": [],
            "label_precision": {},
        }

    if skip_if_empty and (digest.get("total_hits") or 0) == 0:
        _LOG.info("weekly report skipped: no hits this week")
        return None

    report_dir_path = Path(report_dir)
    report_dir_path.mkdir(parents=True, exist_ok=True)

    week_tag = _iso_week_tag(now)
    out_path = report_dir_path / f"{week_tag}.md"

    previous_summary = _previous_report_summary(report_dir_path)
    # Pre-compute current trust so _compute_delta can produce a proper
    # week-over-week trust delta. Failure here is non-fatal — the
    # renderer recomputes (or falls back) on its own.
    current_trust: float | None = None
    try:
        from runtime.safety.validation.trust_signal import (
            compute_guard_trust_score,
        )

        current_trust = compute_guard_trust_score(digest)
    except Exception:  # noqa: BLE001
        current_trust = None
    delta = _compute_delta(
        digest,
        previous_summary,
        current_trust_score=current_trust,
    )

    moment = (now or datetime.now()).isoformat(timespec="seconds")
    report = WeeklyReport(
        week_tag=week_tag,
        generated_at=moment,
        digest=digest,
        delta=delta,
        path=out_path,
    )
    body = _render_markdown(
        report,
        tuning_threshold=tuning_threshold,
        min_precision_for_tuning=min_precision_for_tuning,
    )
    out_path.write_text(body, encoding="utf-8")
    _LOG.info("weekly report written to %s", out_path)
    return out_path


def _main() -> int:
    """Entry point for ``python -m runtime.safety.evolution.weekly_report``."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    path = write_weekly_report()
    if path is None:
        print("weekly report skipped (no hits)")
    else:
        print(f"weekly report written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "DEFAULT_REPORT_DIR",
    "DEFAULT_TELEMETRY_PATH",
    "WeeklyReport",
    "write_weekly_report",
]
