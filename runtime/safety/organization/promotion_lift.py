"""Measure lift after topology promotion.

The forge can promote a proposal into a new ``TeamTopology``. This module
answers the follow-up question: did the promoted topology outperform the base?
It compares performance rows for ``metadata.derived_from`` against the promoted
fingerprint and keeps the result query-only.
"""

from __future__ import annotations

from pathlib import Path
from statistics import mean
from typing import Any

from runtime.safety.organization.forge import load_registry
from runtime.safety.organization.performance_log import read_runs
from runtime.safety.organization.topology import TeamTopology

_SCHEMA = "echo.topology_promotion_lift.v1"


def compute_topology_promotion_lift(
    *,
    registry: dict[str, TeamTopology] | None = None,
    performance_path: str | Path | None = None,
    limit: int = 2000,
) -> dict[str, Any]:
    active_registry = registry if registry is not None else load_registry()
    rows = read_runs(path=performance_path, limit=max(1, min(int(limit), 10000)))
    reports: list[dict[str, Any]] = []
    for topology in sorted(active_registry.values(), key=lambda item: item.name):
        base_fingerprint = str(topology.metadata.get("derived_from") or "")
        mutation = str(topology.metadata.get("mutation") or "")
        if not base_fingerprint or not mutation:
            continue
        before = _stats(
            [row for row in rows if str(row.get("fingerprint") or "") == base_fingerprint]
        )
        after = _stats(
            [row for row in rows if str(row.get("fingerprint") or "") == topology.fingerprint]
        )
        reports.append(
            {
                "topology": topology.name,
                "fingerprint": topology.fingerprint,
                "base_fingerprint": base_fingerprint,
                "bucket": topology.task_bucket,
                "mutation": mutation,
                "promotion_source": str(topology.metadata.get("promotion_source") or ""),
                "promotion_detail": (
                    topology.metadata.get("promotion_detail")
                    if isinstance(topology.metadata.get("promotion_detail"), dict)
                    else {}
                ),
                "before": before,
                "after": after,
                "lift": _lift(before, after),
                "verdict": _verdict(before, after),
            }
        )
    return {
        "schema": _SCHEMA,
        "count": len(reports),
        "reports": reports,
    }


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [
        float(row.get("quality_score"))
        for row in rows
        if isinstance(row.get("quality_score"), int | float)
    ]
    durations = [
        float(row.get("total_duration_ms") or 0.0)
        for row in rows
        if isinstance(row.get("total_duration_ms"), int | float)
    ]
    return {
        "runs": len(rows),
        "success_rate": round(
            sum(1 for row in rows if row.get("success")) / len(rows),
            3,
        )
        if rows
        else None,
        "avg_quality_score": round(mean(scored), 3) if scored else None,
        "avg_duration_ms": round(mean(durations), 1) if durations else None,
    }


def _lift(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "success_rate_delta": _delta(before.get("success_rate"), after.get("success_rate")),
        "quality_score_delta": _delta(
            before.get("avg_quality_score"),
            after.get("avg_quality_score"),
        ),
        "duration_ms_delta": _delta(
            before.get("avg_duration_ms"),
            after.get("avg_duration_ms"),
        ),
    }


def _delta(before: Any, after: Any) -> float | None:
    if not isinstance(before, int | float) or not isinstance(after, int | float):
        return None
    return round(float(after) - float(before), 3)


def _verdict(before: dict[str, Any], after: dict[str, Any]) -> str:
    if int(after.get("runs") or 0) == 0:
        return "pending_after_runs"
    if int(before.get("runs") or 0) == 0:
        return "no_baseline"
    success_delta = _delta(before.get("success_rate"), after.get("success_rate"))
    score_delta = _delta(before.get("avg_quality_score"), after.get("avg_quality_score"))
    if (success_delta is not None and success_delta >= 0.05) or (
        score_delta is not None and score_delta >= 0.05
    ):
        return "improved"
    if (success_delta is not None and success_delta <= -0.05) or (
        score_delta is not None and score_delta <= -0.05
    ):
        return "regressed"
    return "flat"


__all__ = ["compute_topology_promotion_lift"]
