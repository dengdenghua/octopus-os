from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.platform.process.paths import project_root as default_project_root
from runtime.safety.evolution._agent_competitor_scorecard_models import (
    BASELINE_CONTEXT,
    COMPETITORS,
    DEFAULT_TARGET_SCORE,
    DIMENSIONS,
    ECHO_COMPETITOR,
    EXTERNAL_COMPETITORS,
    SCORECARD_CALIBRATION_AS_OF,
    SCORECARD_CALIBRATION_MAX_AGE_DAYS,
    SCORECARD_CALIBRATION_SOURCE_REVISION,
    ScoreDimension,
)
from runtime.safety.evolution._agent_competitor_scorecard_scoring import (
    _dimension_row,
    _next_focus,
    _scorecard_verdict,
    _weighted_score,
)
from runtime.safety.evolution.agent_benchmark import compute_agent_benchmark
from runtime.safety.evolution.behavioral_surpass_evidence import (
    compute_behavioral_surpass_evidence,
)
from runtime.safety.evolution.codex_gap import compute_codex_gap_report
from runtime.safety.evolution.ecosystem_readiness import compute_ecosystem_readiness
from runtime.safety.evolution.parity_certification import compute_parity_certification


def compute_agent_competitor_scorecard(
    *,
    root: str | Path | None = None,
    target_score: int = DEFAULT_TARGET_SCORE,
    surpass_margin: int = 1,
) -> dict[str, Any]:
    base = Path(root) if root is not None else default_project_root(Path(__file__))
    gap_report = compute_codex_gap_report(root=base)
    ecosystem_readiness = compute_ecosystem_readiness(root=base)
    parity_certification = compute_parity_certification(root=base)
    agent_benchmark = compute_agent_benchmark(root=base)
    behavioral_evidence = compute_behavioral_surpass_evidence(root=base)
    evidence_by_id = {
        str(item.get("id")): item
        for item in gap_report.get("capabilities", [])
        if isinstance(item, dict)
    }
    dimensions = [
        _dimension_row(
            dimension,
            evidence_by_id,
            parity_certification=parity_certification,
            target_score=target_score,
            surpass_margin=surpass_margin,
        )
        for dimension in DIMENSIONS
    ]
    for row in dimensions:
        if row["id"] == "ecosystem_maturity":
            row["echo_ecosystem_readiness"] = ecosystem_readiness
    overall = {competitor: _weighted_score(dimensions, competitor) for competitor in COMPETITORS}
    evidence_adjusted_overall = dict(overall)
    evidence_adjusted_overall["echo"] = _weighted_score(
        dimensions,
        "echo",
        score_field="evidence_adjusted_scores",
    )
    ranking = sorted(
        [{"competitor": competitor, "score": score} for competitor, score in overall.items()],
        key=lambda row: (row["score"], row["competitor"]),
        reverse=True,
    )
    evidence_adjusted_ranking = sorted(
        [
            {"competitor": competitor, "score": score}
            for competitor, score in evidence_adjusted_overall.items()
        ],
        key=lambda row: (row["score"], row["competitor"]),
        reverse=True,
    )
    behavioral_systems = (
        behavioral_evidence.get("systems")
        if isinstance(behavioral_evidence.get("systems"), dict)
        else {}
    )
    behavioral_echo = (
        behavioral_systems.get("echo") if isinstance(behavioral_systems.get("echo"), dict) else {}
    )
    behavioral_codex = (
        behavioral_systems.get("codex") if isinstance(behavioral_systems.get("codex"), dict) else {}
    )
    behavioral_infrastructure = (
        behavioral_evidence.get("infrastructure")
        if isinstance(behavioral_evidence.get("infrastructure"), dict)
        else {}
    )
    echo_below_target = [row for row in dimensions if row["scores"][ECHO_COMPETITOR] < target_score]
    echo_strengths = [row for row in dimensions if row["echo_surpasses_best_external"] is True]
    external_leaders = sorted(
        [row for row in dimensions if not row["echo_surpasses_best_external"]],
        key=lambda row: (
            int(row.get("echo_gap_to_surpass") or 0),
            int(row.get("weight") or 0),
        ),
        reverse=True,
    )
    focus_gaps = sorted(
        [row for row in dimensions if int(row.get("echo_gap_to_effective_target") or 0) > 0],
        key=lambda row: (
            int(row.get("echo_gap_to_effective_target") or 0),
            int(row.get("echo_gap_to_surpass") or 0),
            int(row.get("weight") or 0),
        ),
        reverse=True,
    )
    return {
        "schema": "echo.agent_competitor_scorecard.v1",
        "target_score": target_score,
        "surpass_margin": surpass_margin,
        "competitors": list(COMPETITORS),
        "external_competitors": list(EXTERNAL_COMPETITORS),
        "baseline_context": dict(BASELINE_CONTEXT),
        "evidence_layers": {
            "schema": "echo.agent_score_evidence_layers.v1",
            "architecture": {
                "status": "estimated",
                "echo_score": overall["echo"],
                "codex_score": overall["codex"],
                "source": "version_controlled_architecture_calibration",
                "source_revision": SCORECARD_CALIBRATION_SOURCE_REVISION,
                "as_of": SCORECARD_CALIBRATION_AS_OF,
            },
            "static_certification": {
                "status": ("certified" if parity_certification.get("ready") else "not_certified"),
                "ready": bool(parity_certification.get("ready")),
                "passed": int(parity_certification.get("passed") or 0),
                "total": int(parity_certification.get("total") or 0),
            },
            "behavioral_head_to_head": {
                "status": ("certified" if behavioral_evidence.get("ready") else "not_certified"),
                "ready": bool(behavioral_evidence.get("ready")),
                "verdict": behavioral_evidence.get("verdict"),
                "blocker": (
                    "infrastructure"
                    if behavioral_infrastructure.get("active")
                    else "evidence"
                    if not behavioral_evidence.get("ready")
                    else None
                ),
                "infrastructure": behavioral_infrastructure,
                "echo_pass_pow_k": float(behavioral_echo.get("aggregate_pass_pow_k") or 0.0),
                "codex_pass_pow_k": float(behavioral_codex.get("aggregate_pass_pow_k") or 0.0),
            },
        },
        "overall": overall,
        "ranking": ranking,
        "verdict": _scorecard_verdict(overall),
        "evidence_adjusted_overall": evidence_adjusted_overall,
        "evidence_adjusted_ranking": evidence_adjusted_ranking,
        "evidence_adjusted_verdict": _scorecard_verdict(evidence_adjusted_overall),
        "scorecard_policy": {
            "schema": "echo.agent_scorecard_policy.v1",
            "overall": "external_calibrated_baseline",
            "evidence_adjusted_overall": "internal_certification_floor",
            "certification_floors_do_not_change_overall": True,
            "per_dimension_target": "max(user_target_score, best_external_score + surpass_margin)",
            "explicit_objective": "surpass_best_external_on_every_dimension",
        },
        "dimensions": dimensions,
        "echo_below_target": echo_below_target,
        "echo_strengths": echo_strengths,
        "echo_external_leaders": external_leaders,
        "echo_external_gap_dimensions": external_leaders,
        "echo_focus_gaps": focus_gaps,
        "surpass_summary": {
            "schema": "echo.agent_surpass_summary.v1",
            "total_dimensions": len(dimensions),
            "surpassed_dimensions": len(echo_strengths),
            "gap_dimensions": len(external_leaders),
            "target_gap_dimensions": len(echo_below_target),
            "focus_gap_dimensions": len(focus_gaps),
            "all_dimensions_surpassed": len(external_leaders) == 0,
            "largest_gap": max(
                (int(row.get("echo_gap_to_surpass") or 0) for row in dimensions),
                default=0,
            ),
            "largest_effective_gap": max(
                (int(row.get("echo_gap_to_effective_target") or 0) for row in dimensions),
                default=0,
            ),
        },
        "next_focus": _next_focus(focus_gaps),
        "ecosystem_readiness": ecosystem_readiness,
        "parity_certification": parity_certification,
        "agent_benchmark": {
            "schema": agent_benchmark.get("schema"),
            "score": agent_benchmark.get("score"),
            "passed": agent_benchmark.get("passed"),
            "total": agent_benchmark.get("total"),
            "ready": agent_benchmark.get("ready"),
            "by_dimension": agent_benchmark.get("by_dimension", {}),
        },
        "codex_gap": {
            "schema": gap_report.get("schema"),
            "combined_score": gap_report.get("combined_score"),
            "verdict": gap_report.get("verdict"),
            "next_focus": gap_report.get("next_focus", []),
        },
    }


__all__ = [
    "COMPETITORS",
    "DEFAULT_TARGET_SCORE",
    "DIMENSIONS",
    "SCORECARD_CALIBRATION_AS_OF",
    "SCORECARD_CALIBRATION_MAX_AGE_DAYS",
    "SCORECARD_CALIBRATION_SOURCE_REVISION",
    "ScoreDimension",
    "compute_agent_competitor_scorecard",
]
