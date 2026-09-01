"""Conflict resolution — 6-strategy decision tree (protocols/conflict_resolution.md)."""

from __future__ import annotations

from .resolver import (
    Assertion,
    ConflictRecord,
    ConflictType,
    Resolution,
    Source,
    get_trust,
    resolve,
    strategy_confidence,
    strategy_escalate,
    strategy_evidence,
    strategy_recency,
    strategy_source_trust,
    strategy_temporal_split,
    update_trust,
)

__all__ = [
    "Assertion",
    "ConflictRecord",
    "ConflictType",
    "Resolution",
    "Source",
    "get_trust",
    "resolve",
    "strategy_confidence",
    "strategy_evidence",
    "strategy_escalate",
    "strategy_recency",
    "strategy_source_trust",
    "strategy_temporal_split",
    "update_trust",
]
