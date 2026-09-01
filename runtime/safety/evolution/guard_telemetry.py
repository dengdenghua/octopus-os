"""Guard-hit telemetry — the P1 evolution-loop feed.

Every time ``evaluate_guards`` blocks a Final Answer, the (label,
category) of the firing guard is recorded here. Over a week this
answers the question the evolution loop needs: *which guards fire
most, and in which categories is the agent most often wrong?*

Design mirrors ``runtime/safety/evolution/proposal_ledger`` and
``skill_usage``: a dataclass record, an append-only JSONL store with a
file lock, and a ``stats()`` aggregation. Kept deliberately tiny and
dependency-free so it can be called from the hot ReAct loop without
risk — a telemetry failure must NEVER break a turn, so ``record`` is
wrapped to swallow its own errors.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("echo.evolution.guard_telemetry")

_FILE_LOCK = threading.Lock()

# Monotonic per-process sequence so multiple records inside the same
# microsecond (or even the same nanosecond) get unique identity. The
# (label, ts, seq) tuple is the de-facto hit key for verdict joining.
_HIT_SEQ_LOCK = threading.Lock()
_HIT_SEQ = 0


def _next_seq() -> int:
    global _HIT_SEQ
    with _HIT_SEQ_LOCK:
        _HIT_SEQ += 1
        return _HIT_SEQ


@dataclass
class GuardHitRecord:
    """One guard firing against a candidate Final Answer."""

    label: str
    category: str
    ts: str
    # Optional context — kept loose so callers can enrich later without
    # a schema migration.
    goal_digest: str = ""
    iteration: int | None = None
    metadata: dict[str, Any] | None = None
    seq: int = 0  # monotonic per-process; disambiguates same-microsecond hits


@dataclass
class GuardVerdictRecord:
    """A judge's verdict on a previously-recorded guard hit.

    Stored as a separate jsonl line with ``kind="verdict"`` so old
    readers (just iterating GuardHitRecord) ignore them gracefully.
    Verdicts are matched back to hits by ``(label, hit_ts, hit_seq)``.
    Old verdicts written before the seq field landed have ``hit_seq=0``
    and still match against legacy hits with the same default.
    """

    label: str
    hit_ts: str  # the ts of the original hit
    action: str  # "true_positive" | "false_positive" | "uncertain"
    judged_ts: str  # when the judge ran
    reason: str = ""
    confidence: float = 0.0
    hit_seq: int = 0  # the seq of the original hit


class GuardTelemetry:
    """Append-only JSONL sink for guard hits, with category aggregation."""

    def __init__(self, path: str | Path = "data/guard_hits.jsonl") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        label: str,
        category: str,
        *,
        goal_digest: str = "",
        iteration: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append a hit. Swallows its own errors — telemetry must never
        break the ReAct loop."""
        try:
            record = GuardHitRecord(
                label=label,
                category=category,
                ts=datetime.now().isoformat(timespec="microseconds"),
                goal_digest=goal_digest,
                iteration=iteration,
                metadata=metadata,
                seq=_next_seq(),
            )
            payload = asdict(record)
            payload["kind"] = "hit"  # disambiguate from verdict lines
            line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
            with _FILE_LOCK, self._path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception as exc:  # noqa: BLE001 — telemetry must not raise
            _LOG.debug("guard telemetry record failed: %s", exc)

    def record_verdict(
        self,
        label: str,
        hit_ts: str,
        action: str,
        *,
        reason: str = "",
        confidence: float = 0.0,
        hit_seq: int = 0,
    ) -> None:
        """Append a judge verdict for a previously-recorded hit.

        Same fail-soft policy as ``record``: any error is swallowed.
        Verdicts are joined back to hits in ``digest()`` by
        ``(label, hit_ts, hit_seq)``.
        """
        try:
            record = GuardVerdictRecord(
                label=label,
                hit_ts=hit_ts,
                action=action,
                judged_ts=datetime.now().isoformat(timespec="seconds"),
                reason=reason,
                confidence=confidence,
                hit_seq=hit_seq,
            )
            payload = asdict(record)
            payload["kind"] = "verdict"
            line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
            with _FILE_LOCK, self._path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("guard telemetry verdict record failed: %s", exc)

    def _read_all(self) -> list[GuardHitRecord]:
        """Return only hit records — kept for backward compat.

        Verdicts are filtered out so existing callers don't break.
        Use ``_read_with_verdicts`` for the joined view.
        """
        hits, _verdicts = self._read_with_verdicts()
        return hits

    def _read_with_verdicts(
        self,
    ) -> tuple[list[GuardHitRecord], list[GuardVerdictRecord]]:
        if not self._path.exists():
            return ([], [])
        hits: list[GuardHitRecord] = []
        verdicts: list[GuardVerdictRecord] = []
        with _FILE_LOCK, self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception as exc:  # noqa: BLE001
                    _LOG.debug("guard telemetry parse failed: %s", exc)
                    continue
                kind = d.get("kind")
                if kind == "verdict":
                    verdicts.append(
                        GuardVerdictRecord(
                            label=str(d.get("label", "")),
                            hit_ts=str(d.get("hit_ts", "")),
                            action=str(d.get("action", "uncertain")),
                            judged_ts=str(d.get("judged_ts", "")),
                            reason=str(d.get("reason", "")),
                            confidence=float(d.get("confidence", 0.0) or 0.0),
                            hit_seq=int(d.get("hit_seq", 0) or 0),
                        )
                    )
                else:
                    # Default to "hit" — covers legacy lines without kind
                    # field, written before the verdict schema landed.
                    hits.append(
                        GuardHitRecord(
                            label=str(d.get("label", "")),
                            category=str(d.get("category", "")),
                            ts=str(d.get("ts", "")),
                            goal_digest=str(d.get("goal_digest", "")),
                            iteration=d.get("iteration"),
                            metadata=d.get("metadata"),
                            seq=int(d.get("seq", 0) or 0),
                        )
                    )
        return (hits, verdicts)

    def unjudged_hits(self) -> list[GuardHitRecord]:
        """Return hits that have no matching verdict yet.

        Match key is ``(label, ts, seq)``. ``seq`` disambiguates
        hits recorded inside the same microsecond — without it, all
        hits at the same timestamp share one slot and a single
        verdict marks them all as judged.
        """
        hits, verdicts = self._read_with_verdicts()
        judged_keys = {(v.label, v.hit_ts, v.hit_seq) for v in verdicts}
        return [h for h in hits if (h.label, h.ts, h.seq) not in judged_keys]

    def stats(self) -> dict[str, Any]:
        """Aggregate hits by label and by category.

        Returns ``{total, by_label: {...}, by_category: {...}}`` sorted
        most-frequent-first so the evolution loop can read the top
        offenders directly.
        """
        records = self._read_all()
        by_label: Counter[str] = Counter(r.label for r in records)
        by_category: Counter[str] = Counter(r.category for r in records)
        return {
            "total": len(records),
            "by_label": dict(by_label.most_common()),
            "by_category": dict(by_category.most_common()),
        }

    def top_labels(self, n: int = 10) -> list[tuple[str, int]]:
        """Return the ``n`` most-frequently-firing guards."""
        return Counter(r.label for r in self._read_all()).most_common(n)

    def digest(
        self,
        *,
        tuning_threshold: int = 20,
        min_precision_for_tuning: float = 0.5,
    ) -> dict[str, Any]:
        """Produce the evolution-loop digest from accumulated hits.

        This is the actionable artifact the P1 loop consumes. Beyond the
        raw counts it derives:

        * ``tuning_candidates`` — guards firing >= ``tuning_threshold``
          times AND with judged precision >= ``min_precision_for_tuning``
          (or no verdicts yet — uncertain stays in until proven noisy).
          A guard that's too noisy (precision < threshold) is filtered
          out so the prompt-evolver doesn't waste cycles training the
          model on false signals.
        * ``category_share`` — fraction of all hits per category.
        * ``dominant_category`` — the single category with the most hits.
        * ``label_precision`` — per-label ``{tp, fp, uncertain, judged,
          precision}`` so operators can see which guards are well-tuned
          vs noisy.

        Returns an empty-but-well-formed digest when there are no hits.
        """
        hits, verdicts = self._read_with_verdicts()
        total = len(hits)
        by_label = Counter(h.label for h in hits)
        by_category = Counter(h.category for h in hits)

        # Build per-label verdict tally. Verdicts are matched to hits
        # by (label, hit_ts); a hit without a matching verdict is just
        # "unjudged" and contributes nothing to the precision math.
        verdict_by_label: dict[str, dict[str, int]] = {}
        for v in verdicts:
            bucket = verdict_by_label.setdefault(
                v.label,
                {"true_positive": 0, "false_positive": 0, "uncertain": 0},
            )
            if v.action in bucket:
                bucket[v.action] += 1

        label_precision: dict[str, dict[str, Any]] = {}
        for label in by_label:
            bucket = verdict_by_label.get(
                label,
                {"true_positive": 0, "false_positive": 0, "uncertain": 0},
            )
            tp = bucket["true_positive"]
            fp = bucket["false_positive"]
            uncertain = bucket["uncertain"]
            judged = tp + fp + uncertain
            graded = tp + fp  # exclude uncertain from the denominator
            precision = round(tp / graded, 4) if graded > 0 else None
            label_precision[label] = {
                "tp": tp,
                "fp": fp,
                "uncertain": uncertain,
                "judged": judged,
                "unjudged": by_label[label] - judged,
                "precision": precision,
            }

        # Tuning candidates: high frequency AND not proven noisy.
        # ``precision is None`` (no graded verdicts) keeps the guard in
        # play — we don't filter on absence of evidence.
        tuning_candidates: list[dict[str, Any]] = []
        for label, count in by_label.most_common():
            if count < tuning_threshold:
                continue
            prec = label_precision[label]["precision"]
            if prec is not None and prec < min_precision_for_tuning:
                continue
            tuning_candidates.append(
                {
                    "label": label,
                    "count": count,
                    "precision": prec,
                }
            )

        category_share = (
            {cat: round(count / total, 4) for cat, count in by_category.most_common()}
            if total
            else {}
        )
        dominant_category = by_category.most_common(1)[0][0] if total else None

        return {
            "total_hits": total,
            "by_label": dict(by_label.most_common()),
            "by_category": dict(by_category.most_common()),
            "category_share": category_share,
            "dominant_category": dominant_category,
            "tuning_candidates": tuning_candidates,
            "tuning_threshold": tuning_threshold,
            "min_precision_for_tuning": min_precision_for_tuning,
            "label_precision": label_precision,
            "judged_total": sum(p["judged"] for p in label_precision.values()),
        }

    def render_digest(
        self,
        *,
        tuning_threshold: int = 20,
        min_precision_for_tuning: float = 0.5,
    ) -> str:
        """Human-readable one-screen digest — for logs / weekly report."""
        d = self.digest(
            tuning_threshold=tuning_threshold,
            min_precision_for_tuning=min_precision_for_tuning,
        )
        if d["total_hits"] == 0:
            return "Guard telemetry: no hits recorded yet."
        lines = [
            f"Guard telemetry digest — {d['total_hits']} total hits ({d['judged_total']} judged)",
            f"  dominant category: {d['dominant_category']}",
            "  by category:",
        ]
        for cat, count in d["by_category"].items():
            share = d["category_share"].get(cat, 0.0)
            lines.append(f"    {cat:14s} {count:5d}  ({share:.0%})")
        if d["tuning_candidates"]:
            lines.append(
                f"  tuning candidates (>= {tuning_threshold} hits, "
                f"precision >= {min_precision_for_tuning:.0%} or unjudged):"
            )
            for cand in d["tuning_candidates"]:
                prec = cand.get("precision")
                prec_str = f"{prec:.0%}" if prec is not None else "  ?"
                lines.append(f"    {cand['label']:32s} {cand['count']:5d}  (precision={prec_str})")
        else:
            lines.append("  no tuning candidates above threshold")
        return "\n".join(lines)


__all__ = [
    "GuardHitRecord",
    "GuardTelemetry",
    "GuardVerdictRecord",
    "default_guard_digest_provider",
]


def default_guard_digest_provider() -> dict[str, Any] | None:
    """Return the current GuardTelemetry digest, or None on any failure.

    Default factory used by ``PromptEvolver(guard_digest_provider=...)``.
    Reads from the singleton path (``data/guard_hits.jsonl``) the same
    sink the ReAct loop writes to, closing the P1 loop end-to-end.
    Errors are swallowed — the evolver must never break because
    telemetry is unavailable.
    """
    try:
        return GuardTelemetry().digest()
    except Exception:  # noqa: BLE001 — telemetry must not break evolution
        return None
