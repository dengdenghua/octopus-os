from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.safety.evolution.agent_competitor_scorecard import (
    compute_agent_competitor_scorecard,
)
from runtime.safety.evolution.agent_loop_quality import compute_agent_loop_quality
from runtime.safety.evolution.automation_radar import compute_automation_radar
from runtime.safety.evolution.behavioral_surpass_evidence import (
    compute_behavioral_surpass_evidence,
)
from runtime.safety.evolution.browser_desktop_quality import (
    compute_browser_desktop_quality,
)
from runtime.safety.evolution.digital_employee_quality import (
    compute_digital_employee_quality,
)
from runtime.safety.evolution.ecosystem_readiness import compute_ecosystem_readiness
from runtime.safety.evolution.permission_sandbox_quality import (
    compute_permission_sandbox_quality,
)
from runtime.safety.evolution.product_experience_quality import (
    compute_product_experience_quality,
)
from runtime.safety.evolution.repo_context_quality import (
    compute_repo_context_quality,
)

QUALITY_REPORTS = (
    compute_repo_context_quality,
    compute_permission_sandbox_quality,
    compute_product_experience_quality,
    compute_agent_loop_quality,
    compute_digital_employee_quality,
    compute_browser_desktop_quality,
    compute_ecosystem_readiness,
)


@dataclass(frozen=True)
class E2ECoverageDomain:
    id: str
    title: str
    scorecard_dimension_ids: tuple[str, ...]
    quality_schemas: tuple[str, ...] = ()
    automation_dimension_ids: tuple[str, ...] = ()


REQUIRED_COVERAGE_DOMAINS: tuple[E2ECoverageDomain, ...] = (
    E2ECoverageDomain(
        id="general_runtime_and_coding",
        title="General runtime and coding loop",
        scorecard_dimension_ids=("general_agent_loop", "core_coding_loop"),
        quality_schemas=("echo.agent_loop_quality.v1",),
    ),
    E2ECoverageDomain(
        id="frontend_product_experience",
        title="Frontend product experience",
        scorecard_dimension_ids=("product_experience",),
        quality_schemas=("echo.product_experience_quality.v1",),
    ),
    E2ECoverageDomain(
        id="browser_desktop_automation",
        title="Browser and desktop automation",
        scorecard_dimension_ids=("browser_desktop",),
        quality_schemas=("echo.browser_desktop_quality.v1",),
        automation_dimension_ids=(
            "browser_session_control",
            "desktop_preview_execute",
            "desktop_semantic_grounding",
            "visual_replay_validation",
            "repair_recipe_learning",
            "operator_visibility",
            "thread_native_browser_mode",
            "external_chrome_mode",
            "automation_safety",
            "productized_api_bridge",
        ),
    ),
    E2ECoverageDomain(
        id="multi_agent_digital_employee",
        title="Multi-agent and digital employee execution",
        scorecard_dimension_ids=(
            "digital_employee_workflows",
            "subagents_parallelism",
            "model_provider_plugin_interop",
            "differentiated_agent_os",
        ),
        quality_schemas=("echo.digital_employee_quality.v1",),
    ),
    E2ECoverageDomain(
        id="repo_memory_knowledge",
        title="Repository context, memory, and knowledge",
        scorecard_dimension_ids=("repo_context", "long_term_learning"),
        quality_schemas=("echo.repo_context_quality.v1",),
    ),
    E2ECoverageDomain(
        id="security_governance",
        title="Security, sandbox, replay, and governance",
        scorecard_dimension_ids=(
            "permissions_sandbox",
            "record_replay_audit",
            "governance_operator",
        ),
        quality_schemas=("echo.permission_sandbox_quality.v1",),
    ),
    E2ECoverageDomain(
        id="extensions_ecosystem",
        title="Extensions, hooks, and ecosystem maturity",
        scorecard_dimension_ids=("extensions_hooks", "ecosystem_maturity"),
        quality_schemas=("echo.ecosystem_readiness.v1",),
    ),
)


def compute_e2e_surpass_certification(
    *,
    target_score: int = 95,
    review_queue_path: str | Path | None = None,
    behavioral_bundle_path: str | Path | None = None,
) -> dict[str, Any]:
    """One operator-facing proof that Echo clears the E2E Codex bar.

    The scorecard is the broad product/runtime comparison, automation radar is
    the browser/desktop slice, and quality reports are the release gates that
    keep each evidence surface from becoming a stale static number.
    """
    scorecard = compute_agent_competitor_scorecard(target_score=target_score)
    automation = compute_automation_radar(
        target_score=target_score,
        review_queue_path=review_queue_path,
    )
    behavioral = compute_behavioral_surpass_evidence(
        bundle_path=behavioral_bundle_path,
    )
    quality_reports = [
        _quality_report(compute, review_queue_path=review_queue_path) for compute in QUALITY_REPORTS
    ]
    scorecard_summary = scorecard.get("surpass_summary") or {}
    scorecard_echo = _nested_int(scorecard, "overall", "echo")
    scorecard_evidence_adjusted_echo = _nested_int(
        scorecard,
        "evidence_adjusted_overall",
        "echo",
    )
    automation_echo = _nested_int(automation, "overall", "echo")
    scorecard_target_score = int(scorecard.get("target_score") or 0)
    automation_target_score = int(automation.get("target_score") or 0)
    coverage_domains = _coverage_domains(
        scorecard=scorecard,
        automation=automation,
        quality_reports=quality_reports,
    )
    coverage_summary = _coverage_summary(coverage_domains)
    checks = [
        {
            "id": "scorecard_target_aligned",
            "title": "Agent scorecard target matches E2E target",
            "passed": scorecard_target_score == target_score,
            "score": scorecard_target_score,
            "target": target_score,
            "next_action": "Align agent scorecard target_score with E2E certification.",
        },
        {
            "id": "automation_target_aligned",
            "title": "Automation radar target matches E2E target",
            "passed": automation_target_score == target_score,
            "score": automation_target_score,
            "target": target_score,
            "next_action": "Align automation radar target_score with E2E certification.",
        },
        {
            "id": "scorecard_overall",
            "title": "Agent scorecard overall clears target",
            "passed": scorecard_echo >= target_score,
            "score": scorecard_echo,
            "target": target_score,
        },
        {
            "id": "scorecard_evidence_adjusted_overall",
            "title": "Evidence-adjusted scorecard clears target",
            "passed": scorecard_evidence_adjusted_echo >= target_score,
            "score": scorecard_evidence_adjusted_echo,
            "target": target_score,
        },
        {
            "id": "scorecard_all_dimensions_surpassed",
            "title": "All scorecard dimensions surpass best external baseline",
            "passed": bool(scorecard_summary.get("all_dimensions_surpassed")),
            "score": int(scorecard_summary.get("surpassed_dimensions") or 0),
            "target": int(scorecard_summary.get("total_dimensions") or 0),
        },
        {
            "id": "scorecard_no_focus_gaps",
            "title": "No effective scorecard focus gaps remain",
            "passed": not bool(scorecard.get("echo_focus_gaps")),
            "score": 0,
            "target": 0,
        },
        {
            "id": "automation_overall",
            "title": "Automation radar clears target",
            "passed": automation_echo >= target_score,
            "score": automation_echo,
            "target": target_score,
        },
        {
            "id": "automation_no_gaps",
            "title": "No automation evidence gaps remain",
            "passed": not bool(automation.get("echo_gaps")),
            "score": len(automation.get("echo_gaps") or []),
            "target": 0,
        },
        {
            "id": "e2e_required_domains_present",
            "title": "Required E2E domains are covered",
            "passed": (
                int(coverage_summary["present_domains"]) == int(coverage_summary["total_domains"])
            ),
            "score": int(coverage_summary["present_domains"]),
            "target": int(coverage_summary["total_domains"]),
            "next_action": "Restore missing scorecard, automation, or quality coverage.",
        },
        {
            "id": "e2e_required_domains_ready",
            "title": "Required E2E domains are ready",
            "passed": (
                int(coverage_summary["ready_domains"]) == int(coverage_summary["total_domains"])
            ),
            "score": int(coverage_summary["ready_domains"]),
            "target": int(coverage_summary["total_domains"]),
            "next_action": "Restore non-ready E2E coverage domains.",
        },
    ]
    checks.extend(_quality_checks(quality_reports))
    checks.extend(
        {
            **check,
            "id": f"behavioral:{check.get('id')}",
            "title": f"Behavioral evidence: {check.get('title')}",
        }
        for check in behavioral.get("checks") or []
        if isinstance(check, dict)
    )
    ready = all(bool(check.get("passed")) for check in checks)
    static_ready = all(
        bool(check.get("passed"))
        for check in checks
        if not str(check.get("id") or "").startswith("behavioral:")
    )
    if ready:
        verdict = "surpassed"
    elif static_ready:
        verdict = "needs_behavioral_evidence"
    else:
        verdict = "needs_work"
    return {
        "schema": "echo.e2e_surpass_certification.v1",
        "target_score": target_score,
        "ready": ready,
        "verdict": verdict,
        "summary": {
            "scorecard_echo": scorecard_echo,
            "scorecard_best_external": _best_external_score(scorecard),
            "scorecard_evidence_adjusted_echo": (scorecard_evidence_adjusted_echo),
            "automation_echo": automation_echo,
            "automation_codex": _nested_int(automation, "overall", "codex"),
            "coverage_ready": int(coverage_summary["ready_domains"]),
            "coverage_total": int(coverage_summary["total_domains"]),
            "coverage_gap_domains": int(coverage_summary["gap_domains"]),
            "quality_ready": sum(1 for report in quality_reports if bool(report.get("ready"))),
            "quality_total": len(quality_reports),
            "all_dimensions_surpassed": bool(
                scorecard_summary.get("all_dimensions_surpassed"),
            ),
            "scorecard_gap_dimensions": int(
                scorecard_summary.get("gap_dimensions") or 0,
            ),
            "automation_gap_dimensions": len(automation.get("echo_gaps") or []),
            "behavioral_ready": bool(behavioral.get("ready")),
            "behavioral_echo_pass_pow_k": _nested_float(
                behavioral,
                "systems",
                "echo",
                "aggregate_pass_pow_k",
            ),
            "behavioral_codex_pass_pow_k": _nested_float(
                behavioral,
                "systems",
                "codex",
                "aggregate_pass_pow_k",
            ),
        },
        "checks": checks,
        "scorecard": {
            "schema": scorecard.get("schema"),
            "target_score": scorecard.get("target_score"),
            "overall": scorecard.get("overall"),
            "evidence_adjusted_overall": scorecard.get(
                "evidence_adjusted_overall",
            ),
            "verdict": scorecard.get("verdict"),
            "evidence_adjusted_verdict": scorecard.get(
                "evidence_adjusted_verdict",
            ),
            "evidence_layers": scorecard.get("evidence_layers"),
            "surpass_summary": scorecard_summary,
            "next_focus": scorecard.get("next_focus") or [],
        },
        "automation": {
            "schema": automation.get("schema"),
            "target_score": automation.get("target_score"),
            "overall": automation.get("overall"),
            "verdict": automation.get("verdict"),
            "next_focus": automation.get("next_focus") or [],
            "gap_count": len(automation.get("echo_gaps") or []),
        },
        "coverage": {
            "schema": "echo.e2e_coverage.v1",
            "summary": coverage_summary,
            "domains": coverage_domains,
        },
        "quality": [
            {
                "schema": report.get("schema"),
                "ready": report.get("ready"),
                "score": report.get("score"),
                "passed": report.get("passed"),
                "total": report.get("total"),
                "next_actions": report.get("next_actions") or [],
            }
            for report in quality_reports
        ],
        "behavioral": behavioral,
        "next_actions": [
            str(check.get("next_action"))
            for check in checks
            if not check.get("passed") and check.get("next_action")
        ],
    }


def _quality_report(compute: Any, *, review_queue_path: str | Path | None) -> dict[str, Any]:
    if compute is compute_browser_desktop_quality:
        return compute(review_queue_path=review_queue_path)
    return compute()


def _quality_checks(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for report in reports:
        schema = str(report.get("schema") or "quality_report")
        ready = bool(report.get("ready"))
        score = float(report.get("score") or 0.0)
        checks.append(
            {
                "id": f"{schema}:ready",
                "title": f"{schema} is ready",
                "passed": ready,
                "score": int(report.get("passed") or 0),
                "target": int(report.get("total") or 0),
                "next_action": _first_next_action(report),
            }
        )
        checks.append(
            {
                "id": f"{schema}:score",
                "title": f"{schema} score is complete",
                "passed": score >= 1.0,
                "score": score,
                "target": 1.0,
                "next_action": _first_next_action(report),
            }
        )
    return checks


def _coverage_domains(
    *,
    scorecard: dict[str, Any],
    automation: dict[str, Any],
    quality_reports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scorecard_dimensions = {
        str(row.get("id")): row
        for row in scorecard.get("dimensions") or []
        if isinstance(row, dict)
    }
    automation_dimensions = {
        str(row.get("id")): row
        for row in automation.get("dimensions") or []
        if isinstance(row, dict)
    }
    quality_by_schema = {
        str(report.get("schema")): report for report in quality_reports if isinstance(report, dict)
    }
    return [
        _coverage_domain_row(
            domain,
            scorecard_dimensions=scorecard_dimensions,
            automation_dimensions=automation_dimensions,
            quality_by_schema=quality_by_schema,
        )
        for domain in REQUIRED_COVERAGE_DOMAINS
    ]


def _coverage_domain_row(
    domain: E2ECoverageDomain,
    *,
    scorecard_dimensions: dict[str, dict[str, Any]],
    automation_dimensions: dict[str, dict[str, Any]],
    quality_by_schema: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    scorecard_rows = [
        scorecard_dimensions[dimension_id]
        for dimension_id in domain.scorecard_dimension_ids
        if dimension_id in scorecard_dimensions
    ]
    missing_scorecard_dimensions = [
        dimension_id
        for dimension_id in domain.scorecard_dimension_ids
        if dimension_id not in scorecard_dimensions
    ]
    scorecard_ready = (
        not missing_scorecard_dimensions
        and bool(scorecard_rows)
        and all(_scorecard_dimension_ready(row) for row in scorecard_rows)
    )

    quality_reports = [
        quality_by_schema[schema]
        for schema in domain.quality_schemas
        if schema in quality_by_schema
    ]
    missing_quality_schemas = [
        schema for schema in domain.quality_schemas if schema not in quality_by_schema
    ]
    quality_ready = not missing_quality_schemas and all(
        _quality_report_ready(report) for report in quality_reports
    )

    automation_rows = [
        automation_dimensions[dimension_id]
        for dimension_id in domain.automation_dimension_ids
        if dimension_id in automation_dimensions
    ]
    missing_automation_dimensions = [
        dimension_id
        for dimension_id in domain.automation_dimension_ids
        if dimension_id not in automation_dimensions
    ]
    automation_ready = not missing_automation_dimensions and all(
        _automation_dimension_ready(row) for row in automation_rows
    )

    present = (
        not missing_scorecard_dimensions
        and not missing_quality_schemas
        and not missing_automation_dimensions
    )
    ready = present and scorecard_ready and quality_ready and automation_ready
    return {
        "id": domain.id,
        "title": domain.title,
        "present": present,
        "ready": ready,
        "scorecard_dimension_ids": list(domain.scorecard_dimension_ids),
        "scorecard_ready": scorecard_ready,
        "missing_scorecard_dimension_ids": missing_scorecard_dimensions,
        "quality_schemas": list(domain.quality_schemas),
        "quality_ready": quality_ready,
        "missing_quality_schemas": missing_quality_schemas,
        "automation_dimension_ids": list(domain.automation_dimension_ids),
        "automation_ready": automation_ready,
        "missing_automation_dimension_ids": missing_automation_dimensions,
    }


def _scorecard_dimension_ready(row: dict[str, Any]) -> bool:
    return (
        bool(row.get("echo_surpasses_best_external"))
        and int(row.get("echo_gap_to_effective_target") or 0) == 0
        and int(row.get("echo_evidence_adjusted_gap_to_effective_target") or 0) == 0
    )


def _quality_report_ready(report: dict[str, Any]) -> bool:
    return bool(report.get("ready")) and float(report.get("score") or 0.0) >= 1.0


def _automation_dimension_ready(row: dict[str, Any]) -> bool:
    return (
        bool(row.get("evidence_ready"))
        and int(row.get("echo_gap_to_target") or 0) == 0
        and int(row.get("echo_gap_to_codex") or 0) <= 0
    )


def _coverage_summary(domains: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(domains)
    ready = [row for row in domains if bool(row.get("ready"))]
    present = [row for row in domains if bool(row.get("present"))]
    gaps = [row for row in domains if not bool(row.get("ready"))]
    return {
        "schema": "echo.e2e_coverage_summary.v1",
        "total_domains": total,
        "present_domains": len(present),
        "ready_domains": len(ready),
        "gap_domains": len(gaps),
        "gap_domain_ids": [str(row.get("id")) for row in gaps],
    }


def _first_next_action(report: dict[str, Any]) -> str:
    actions = report.get("next_actions")
    if isinstance(actions, list) and actions:
        return str(actions[0])
    return ""


def _best_external_score(scorecard: dict[str, Any]) -> int:
    overall = scorecard.get("overall")
    external = scorecard.get("external_competitors")
    if not isinstance(overall, dict) or not isinstance(external, list):
        return 0
    return max((int(overall.get(name) or 0) for name in external), default=0)


def _nested_int(report: dict[str, Any], *keys: str) -> int:
    value: Any = report
    for key in keys:
        if not isinstance(value, dict):
            return 0
        value = value.get(key)
    return int(value or 0)


def _nested_float(report: dict[str, Any], *keys: str) -> float:
    value: Any = report
    for key in keys:
        if not isinstance(value, dict):
            return 0.0
        value = value.get(key)
    return float(value or 0.0)


__all__ = [
    "E2ECoverageDomain",
    "QUALITY_REPORTS",
    "REQUIRED_COVERAGE_DOMAINS",
    "compute_e2e_surpass_certification",
]
