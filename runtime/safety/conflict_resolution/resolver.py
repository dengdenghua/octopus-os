"""Conflict resolver — 6-strategy decision tree for KG / memory conflicts.

Implements ``protocols/conflict_resolution.md`` §3-4. Given a set of
contradictory assertions (same subject+predicate, different object), tries
six strategies in priority order:

    1. Temporal Split  — both true, different time windows
    2. Evidence Weight — evidence score 2× the runner-up
    3. Source Trust     — trust score gap ≥ 0.3
    4. Confidence       — winner ≥ 0.85 AND loser < 0.5
    5. Recency          — newest with baseline support
    6. Human Escalation — mark disputed, emit alert

The order is a hard constraint (Evidence → Trust → Confidence → Recency)
to resist Goodharting: recency-first would overwrite facts with weak new
claims; confidence-first would let hallucinated high-confidence LLM output
overwrite real evidence.

This module is the algorithm only. KG / memory_consolidation call into
``resolve()`` and act on the returned ``Resolution``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

# ─── Data models (mirror protocols/conflict_resolution.md §2) ───────


ConflictType = Literal["direct", "value", "type", "relational", "temporal", "inferred"]


@dataclass(frozen=True)
class Source:
    source_id: str
    source_type: str = "inference"
    trust_score: float = 0.5


@dataclass
class Assertion:
    assertion_id: UUID
    subject: str
    predicate: str
    object: Any
    confidence: float = 0.5
    source: Source = field(default_factory=Source)
    evidence_refs: list[str] = field(default_factory=list)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    ts: datetime = field(default_factory=datetime.utcnow)
    status: str = "active"
    superseded_by: UUID | None = None


@dataclass
class Resolution:
    strategy: str
    winner: UUID | None = None
    merged_assertion: UUID | None = None
    rationale: str = ""
    resolved_at: datetime = field(default_factory=datetime.utcnow)
    resolved_by: str = "auto"


@dataclass
class ConflictRecord:
    conflict_id: UUID
    assertions: list[Assertion]
    conflict_type: ConflictType
    detected_at: datetime = field(default_factory=datetime.utcnow)
    resolution: Resolution | None = None
    escalated: bool = False


# ─── Strategy implementations ────────────────────────────────────────


def _intervals_overlap(
    a: tuple[datetime | None, datetime | None],
    b: tuple[datetime | None, datetime | None],
) -> bool:
    a_start, a_end = a
    b_start, b_end = b
    if a_end is not None and b_start is not None and a_end < b_start:
        return False
    return not (b_end is not None and a_start is not None and b_end < a_start)


def strategy_temporal_split(
    assertions: list[Assertion],
) -> Resolution | None:
    """Strategy-1: both true if non-overlapping time windows."""
    with_intervals = [
        a for a in assertions if a.valid_from is not None or a.valid_until is not None
    ]
    if len(with_intervals) < len(assertions):
        return None  # Some assertions lack time data — can't decide.
    for i, a in enumerate(assertions):
        for b in assertions[i + 1 :]:
            if _intervals_overlap((a.valid_from, a.valid_until), (b.valid_from, b.valid_until)):
                return None  # Overlap → not a clean temporal split.
    return Resolution(
        strategy="temporal_split",
        rationale="different time windows, both assertions coexist",
    )


def strategy_evidence(
    assertions: list[Assertion],
) -> Resolution | None:
    """Strategy-2: winner's evidence count ≥ 2× runner-up."""
    if len(assertions) < 2:
        return None
    scored = sorted(assertions, key=lambda a: len(a.evidence_refs), reverse=True)
    best, second = scored[0], scored[1]
    if len(best.evidence_refs) >= 2 * max(len(second.evidence_refs), 1):
        return Resolution(
            strategy="evidence",
            winner=best.assertion_id,
            rationale=f"evidence {len(best.evidence_refs)} ≥ 2× {len(second.evidence_refs)}",
        )
    return None


def strategy_source_trust(
    assertions: list[Assertion],
) -> Resolution | None:
    """Strategy-3: trust score gap ≥ 0.3."""
    if len(assertions) < 2:
        return None
    scored = sorted(assertions, key=lambda a: a.source.trust_score, reverse=True)
    best, second = scored[0], scored[1]
    if best.source.trust_score >= second.source.trust_score + 0.3:
        return Resolution(
            strategy="source_trust",
            winner=best.assertion_id,
            rationale=f"trust {best.source.trust_score:.2f} ≥ {second.source.trust_score:.2f} + 0.3",
        )
    return None


def strategy_confidence(
    assertions: list[Assertion],
) -> Resolution | None:
    """Strategy-4: winner ≥ 0.85 AND runner-up < 0.5."""
    if len(assertions) < 2:
        return None
    scored = sorted(assertions, key=lambda a: a.confidence, reverse=True)
    best, second = scored[0], scored[1]
    if best.confidence >= 0.85 and second.confidence < 0.5:
        return Resolution(
            strategy="confidence",
            winner=best.assertion_id,
            rationale=f"confidence {best.confidence:.2f} ≥ 0.85, loser {second.confidence:.2f} < 0.5",
        )
    return None


def strategy_recency(
    assertions: list[Assertion],
) -> Resolution | None:
    """Strategy-5: newest assertion with baseline support."""
    newest = max(assertions, key=lambda a: a.ts)
    if newest.confidence >= 0.7 or newest.source.trust_score >= 0.7:
        return Resolution(
            strategy="recency",
            winner=newest.assertion_id,
            rationale="recency tiebreaker + baseline support",
        )
    return None


def strategy_escalate(
    assertions: list[Assertion],
    conflict: ConflictRecord,
) -> Resolution:
    """Strategy-6: mark disputed, escalate for human review."""
    conflict.escalated = True
    for a in assertions:
        a.status = "disputed"
    return Resolution(
        strategy="human_escalation",
        resolved_by="human",
        rationale="no auto-strategy applied; marked disputed for human review",
    )


# ─── Decision tree (protocols/conflict_resolution.md §4) ────────────


def resolve(conflict: ConflictRecord) -> Resolution:
    """Try strategies in priority order; first hit wins.

    Order is a hard constraint to resist Goodharting:
      Temporal → Evidence → Trust → Confidence → Recency → Escalate
    """
    assertions = conflict.assertions

    # 1. Temporal conflicts often aren't real conflicts.
    if conflict.conflict_type == "temporal":
        r = strategy_temporal_split(assertions)
        if r is not None:
            conflict.resolution = r
            return r

    # 2. Evidence > Trust > Confidence > Recency.
    for strategy in (
        strategy_evidence,
        strategy_source_trust,
        strategy_confidence,
        strategy_recency,
    ):
        r = strategy(assertions)
        if r is not None:
            conflict.resolution = r
            return r

    # 3. Fallback: human escalation.
    r = strategy_escalate(assertions, conflict)
    conflict.resolution = r
    return r


# ─── Source trust score maintenance (§5) ─────────────────────────────


_trust_scores: dict[str, float] = {}


def get_trust(source_id: str) -> float:
    return _trust_scores.get(source_id, 0.5)


def update_trust(source_id: str, outcome: str) -> float:
    """EMA update: accepted +0.005, rejected -0.01 (errors penalised 2×)."""
    current = _trust_scores.get(source_id, 0.5)
    alpha = 0.05
    delta = 0.1 if outcome == "accepted" else -0.2
    new_score = max(0.0, min(1.0, current + alpha * delta))
    _trust_scores[source_id] = new_score
    return new_score
