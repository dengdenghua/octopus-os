"""Pure score aggregation and prioritization helpers."""

from __future__ import annotations

from typing import Any


def weighted_score(
    dimensions: list[dict[str, Any]],
    competitor: str,
    *,
    score_field: str = "scores",
) -> int:
    """Compute the integer weighted score for one competitor."""

    total_weight = sum(int(row["weight"]) for row in dimensions)
    if total_weight <= 0:
        return 0
    weighted = sum(int(row["weight"]) * int(row[score_field][competitor]) for row in dimensions)
    return int(round(weighted / total_weight))


def scorecard_verdict(
    overall: dict[str, int],
    *,
    focal_competitor: str = "echo",
) -> str:
    """Classify the focal competitor relative to the strongest peer."""

    focal_score = overall.get(focal_competitor, 0)
    best_other = max(
        score for competitor, score in overall.items() if competitor != focal_competitor
    )
    if focal_score > best_other:
        return "leading"
    if focal_score >= best_other - 3:
        return "competitive"
    if focal_score >= best_other - 8:
        return "near_parity"
    return "behind"


def next_focus(rows: list[dict[str, Any]], *, limit: int = 5) -> list[str]:
    """Select the highest-impact next action from each largest score gap."""

    ordered = sorted(
        rows,
        key=lambda row: (
            int(row.get("echo_gap_to_effective_target") or 0),
            int(row.get("echo_gap_to_surpass") or 0),
            int(row.get("echo_gap_to_target") or 0),
            int(row.get("weight") or 0),
        ),
        reverse=True,
    )
    out: list[str] = []
    for row in ordered:
        actions = row.get("echo_next_actions")
        if isinstance(actions, list) and actions:
            out.append(str(actions[0]))
        if len(out) >= limit:
            break
    return out


__all__ = ["next_focus", "scorecard_verdict", "weighted_score"]
