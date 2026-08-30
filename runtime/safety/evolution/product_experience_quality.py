from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.platform.process.paths import project_root as default_project_root


@dataclass(frozen=True)
class ProductExperienceCheck:
    id: str
    title: str
    paths: tuple[str, ...]
    required_terms: tuple[str, ...]
    weight: int = 1


CHECKS: tuple[ProductExperienceCheck, ...] = (
    ProductExperienceCheck(
        id="operator_scorecard_drilldown_ui",
        title="Operator scorecard drill-down UI",
        paths=(
            "frontend/src/components/workspace/agent-operator-panel.tsx",
            "frontend/src/components/workspace/agent-operator/index.tsx",
            "frontend/src/components/workspace/agent-operator/cards/ScorecardGapDrilldown.tsx",
            "frontend/src/components/workspace/agent-operator-panel.test.tsx",
            "frontend/src/core/agent-trace/api.ts",
        ),
        required_terms=(
            "operator_drilldown",
            "Evidence checklist",
            "queueAgentScorecardGaps",
            "E2E_SURPASS_TARGET_SCORE",
            "fetchAgentCompetitorScorecard",
            "scorecard_gap_backlog",
        ),
    ),
    ProductExperienceCheck(
        id="automation_operator_surface",
        title="Automation operator surface",
        paths=(
            "frontend/src/components/workspace/agent-operator-panel.tsx",
            "frontend/src/components/workspace/agent-operator/index.tsx",
            "frontend/src/components/workspace/agent-operator-panel.test.tsx",
            "frontend/src/core/agent-trace/api.ts",
            "runtime/safety/evolution/automation_radar.py",
        ),
        required_terms=(
            "Automation radar",
            "fetchAutomationRadar",
            "fetchBrowserDesktopQuality",
            "installAutomationPolicyRuleDraft",
            "echo.automation_radar.v1",
        ),
    ),
    ProductExperienceCheck(
        id="e2e_surpass_operator_surface",
        title="E2E surpass operator surface",
        paths=(
            "frontend/src/components/workspace/agent-operator-panel.tsx",
            "frontend/src/components/workspace/agent-operator/index.tsx",
            "frontend/src/components/workspace/agent-operator-panel.test.tsx",
            "frontend/src/core/agent-trace/api.ts",
            "runtime/safety/evolution/e2e_surpass_certification.py",
        ),
        required_terms=(
            "fetchE2ESurpassCertification",
            "E2E_SURPASS_TARGET_SCORE",
            "E2E surpass certification",
            "echo.e2e_surpass_certification.v1",
            "scorecard_best_external",
            "all checks passed",
        ),
    ),
    ProductExperienceCheck(
        id="ecosystem_operator_surface",
        title="Ecosystem operator surface",
        paths=(
            "frontend/src/components/workspace/agent-operator-panel.tsx",
            "frontend/src/core/plugins/types.ts",
            "frontend/src/core/plugins/api.ts",
            "runtime/sensing/gateway/plugins_router.py",
        ),
        required_terms=(
            "fetchPluginSmokeSummary",
            "permission_rule_drafts",
            "migration_readiness",
            "plugin_migration_readiness",
            "echo.plugin_migration_readiness.v1",
        ),
    ),
    ProductExperienceCheck(
        id="shared_quality_strip",
        title="Shared quality strip",
        paths=(
            "frontend/src/components/workspace/capability-quality-strip.tsx",
            "frontend/src/components/workspace/capability-quality-strip.test.tsx",
            "frontend/src/core/agent-trace/api.ts",
        ),
        required_terms=(
            "fetchAgentCompetitorScorecard",
            "E2E_SURPASS_TARGET_SCORE",
            "fetchBrowserDesktopQuality",
            "evidence_adjusted_overall",
            "echo_below_target",
        ),
    ),
    ProductExperienceCheck(
        id="browser_preview_product_loop",
        title="Browser preview product loop",
        paths=(
            "frontend/src/components/workspace/embedded-browser/browser-panel.tsx",
            "frontend/src/components/workspace/browser-preview-panel.tsx",
            "runtime/platform/ui/browser_router.py",
            "tests/test_browser_router.py",
        ),
        required_terms=(
            "aria-label",
            "session/status",
            "session/ensure",
            "session/replay-case",
            "browser_policy",
        ),
    ),
    ProductExperienceCheck(
        id="keyboard_replay_remediation",
        title="Keyboard-first replay remediation across operator surfaces",
        paths=(
            "frontend/src/components/workspace/replay-panel.tsx",
            "frontend/src/components/workspace/agent-operator-panel.tsx",
            "frontend/src/components/workspace/agent-operator/index.tsx",
            "frontend/src/components/workspace/agent-operator-panel.test.tsx",
            "frontend/src/core/agent-trace/api.ts",
        ),
        required_terms=(
            "Rerun blocked browser and desktop repair evidence",
            "rerunBrowserDesktopRepairRecipeEvidenceBatch",
            "promoteSourceCases: false",
            "Source cases remain operator-gated",
            "operator_panel",
        ),
    ),
)


def compute_product_experience_quality(
    *,
    root: str | Path | None = None,
) -> dict[str, Any]:
    base = Path(root) if root is not None else default_project_root(Path(__file__))
    checks = [_check_row(base, check) for check in CHECKS]
    total_weight = sum(int(row["weight"]) for row in checks)
    passed_weight = sum(int(row["weight"]) for row in checks if row["passed"])
    return {
        "schema": "echo.product_experience_quality.v1",
        "score": round(passed_weight / max(1, total_weight), 3),
        "passed": sum(1 for row in checks if row["passed"]),
        "total": len(checks),
        "ready": all(row["passed"] for row in checks),
        "checks": checks,
        "next_actions": [str(row["next_action"]) for row in checks if not row["passed"]],
    }


def _check_row(base: Path, check: ProductExperienceCheck) -> dict[str, Any]:
    paths = [{"path": path, "exists": (base / path).exists()} for path in check.paths]
    text = "\n".join(_read_text(base / row["path"]) for row in paths if row["exists"]).lower()
    missing_paths = [str(row["path"]) for row in paths if not row["exists"]]
    missing_terms = [term for term in check.required_terms if term.lower() not in text]
    return {
        "id": check.id,
        "title": check.title,
        "weight": check.weight,
        "passed": not missing_paths and not missing_terms,
        "paths": paths,
        "missing_paths": missing_paths,
        "required_terms": list(check.required_terms),
        "missing_terms": missing_terms,
        "next_action": f"Complete product-experience quality check: {check.title}.",
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


__all__ = [
    "CHECKS",
    "ProductExperienceCheck",
    "compute_product_experience_quality",
]
