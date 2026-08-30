from __future__ import annotations

from typing import Any

from runtime.safety.evolution._agent_competitor_scorecard_drilldown import _operator_drilldown
from runtime.safety.evolution._agent_competitor_scorecard_evidence import (
    _evidence_checklist_item,
    _evidence_readiness,
)
from runtime.safety.evolution._agent_competitor_scorecard_models import (
    ECHO_COMPETITOR,
    EXTERNAL_COMPETITORS,
    ScoreDimension,
)


def _dimension_row(
    dimension: ScoreDimension,
    evidence_by_id: dict[str, dict[str, Any]],
    *,
    parity_certification: dict[str, Any],
    target_score: int,
    surpass_margin: int,
) -> dict[str, Any]:
    evidence = [
        evidence_by_id[evidence_id]
        for evidence_id in dimension.echo_evidence_ids
        if evidence_id in evidence_by_id
    ]
    readiness = _evidence_readiness(evidence)
    checklist = [_evidence_checklist_item(item) for item in evidence]
    baseline_scores = dict(dimension.scores)
    scores = dict(dimension.scores)
    evidence_adjusted_scores = dict(dimension.scores)
    best_external_competitor = max(
        EXTERNAL_COMPETITORS,
        key=lambda competitor: scores[competitor],
    )
    best_external_score = int(scores[best_external_competitor])
    surpass_target_score = min(100, best_external_score + max(1, int(surpass_margin)))
    effective_target_score = max(target_score, surpass_target_score)
    floors = (
        parity_certification.get("dimension_score_floors")
        if isinstance(parity_certification.get("dimension_score_floors"), dict)
        else {}
    )
    certified_floor = int(floors.get(dimension.id) or 0)
    applies_certified_floor = (
        scores[ECHO_COMPETITOR] < effective_target_score
        and certified_floor >= effective_target_score
    )
    if applies_certified_floor:
        evidence_adjusted_scores[ECHO_COMPETITOR] = max(
            evidence_adjusted_scores[ECHO_COMPETITOR],
            certified_floor,
        )
    certification_evidence = (
        parity_certification.get("dimension_evidence")
        if isinstance(parity_certification.get("dimension_evidence"), dict)
        else {}
    )
    echo_gap_to_target = max(0, target_score - scores[ECHO_COMPETITOR])
    echo_gap_to_effective_target = max(
        0,
        effective_target_score - scores[ECHO_COMPETITOR],
    )
    evidence_adjusted_gap_to_target = max(
        0,
        target_score - evidence_adjusted_scores[ECHO_COMPETITOR],
    )
    evidence_adjusted_gap_to_effective_target = max(
        0,
        effective_target_score - evidence_adjusted_scores[ECHO_COMPETITOR],
    )
    return {
        "id": dimension.id,
        "title": dimension.title,
        "weight": dimension.weight,
        "why": dimension.why,
        "scores": scores,
        "evidence_adjusted_scores": evidence_adjusted_scores,
        "leader": max(scores, key=lambda key: scores[key]),
        "target_score": target_score,
        "best_external_competitor": best_external_competitor,
        "best_external_score": best_external_score,
        "surpass_target_score": surpass_target_score,
        "effective_target_score": effective_target_score,
        "echo_surpasses_best_external": (scores[ECHO_COMPETITOR] >= surpass_target_score),
        "echo_gap_to_surpass": max(
            0,
            surpass_target_score - scores[ECHO_COMPETITOR],
        ),
        "echo_gap_to_target": echo_gap_to_target,
        "echo_gap_to_effective_target": echo_gap_to_effective_target,
        "echo_baseline_score": baseline_scores[ECHO_COMPETITOR],
        "echo_score_source": "external_calibrated_baseline",
        "echo_evidence_adjusted_score": evidence_adjusted_scores[ECHO_COMPETITOR],
        "echo_evidence_adjusted_gap_to_target": evidence_adjusted_gap_to_target,
        "echo_evidence_adjusted_gap_to_effective_target": (
            evidence_adjusted_gap_to_effective_target
        ),
        "echo_evidence_adjusted_score_source": (
            "certified_floor" if applies_certified_floor else "baseline"
        ),
        "echo_certified_score_floor": certified_floor,
        "echo_certification_score_applied": False,
        "echo_certification_adjustment_available": applies_certified_floor,
        "echo_certification_evidence": list(
            certification_evidence.get(dimension.id, []),
        ),
        "echo_evidence_readiness": readiness,
        "echo_evidence": [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "score": item.get("score"),
                "status": item.get("status"),
            }
            for item in evidence
        ],
        "echo_evidence_checklist": checklist,
        "echo_missing_evidence_count": sum(
            int(item["implementation"]["missing_count"]) + int(item["tests"]["missing_count"])
            for item in checklist
        ),
        "operator_drilldown": _operator_drilldown(
            dimension_id=dimension.id,
            evidence_ids=dimension.echo_evidence_ids,
            certified_floor=certified_floor,
        ),
        "echo_next_actions": list(dimension.echo_next_actions),
    }


def _weighted_score(
    dimensions: list[dict[str, Any]],
    competitor: str,
    *,
    score_field: str = "scores",
) -> int:
    total_weight = sum(int(row["weight"]) for row in dimensions)
    if total_weight <= 0:
        return 0
    weighted = sum(int(row["weight"]) * int(row[score_field][competitor]) for row in dimensions)
    return int(round(weighted / total_weight))


def _scorecard_verdict(overall: dict[str, int]) -> str:
    echo = overall.get(ECHO_COMPETITOR, 0)
    best_other = max(
        score for competitor, score in overall.items() if competitor != ECHO_COMPETITOR
    )
    if echo > best_other:
        return "leading"
    if echo >= best_other - 3:
        return "competitive"
    if echo >= best_other - 8:
        return "near_parity"
    return "behind"


def _next_focus(rows: list[dict[str, Any]]) -> list[str]:
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
        if len(out) >= 5:
            break
    return out
