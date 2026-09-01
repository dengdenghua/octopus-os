from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.platform.process.paths import project_root as default_project_root
from runtime.safety.evolution.agent_benchmark import compute_agent_benchmark
from runtime.safety.evolution.browser_desktop_quality import (
    compute_browser_desktop_quality,
)
from runtime.safety.evolution.parity_certification import (
    compute_parity_certification,
)

COMPETITORS: tuple[str, ...] = ("codex", "claude_code", "cursor", "echo")


@dataclass(frozen=True)
class AutomationRadarDimension:
    id: str
    title: str
    weight: int
    why: str
    scores: dict[str, int]
    evidence_check_ids: tuple[str, ...]
    next_actions: tuple[str, ...]


DIMENSIONS: tuple[AutomationRadarDimension, ...] = (
    AutomationRadarDimension(
        id="browser_session_control",
        title="Browser session control",
        weight=16,
        why="Launch, recover, inspect, and replay browser sessions through a stable local bridge.",
        scores={"codex": 98, "claude_code": 88, "cursor": 82, "echo": 99},
        evidence_check_ids=(
            "browser_session_lifecycle",
            "browser_session_recovery_rerun",
        ),
        next_actions=(
            "Keep browser recovery proof, deterministic reruns, and relay policy persisted before every release.",
        ),
    ),
    AutomationRadarDimension(
        id="desktop_preview_execute",
        title="Desktop preview and execute",
        weight=15,
        why="Split observe, preview, lease, and execute so desktop control stays useful and reviewable.",
        scores={"codex": 94, "claude_code": 85, "cursor": 78, "echo": 95},
        evidence_check_ids=("desktop_preview_execute_lease",),
        next_actions=("Add more lease-conflict regression cases for parallel desktop runs.",),
    ),
    AutomationRadarDimension(
        id="desktop_semantic_grounding",
        title="Desktop semantic grounding",
        weight=12,
        why="Use accessibility trees and control metadata instead of relying on pixels alone.",
        scores={"codex": 93, "claude_code": 84, "cursor": 78, "echo": 95},
        evidence_check_ids=("desktop_uia_grounding",),
        next_actions=("Attach UIA match traces to every promoted desktop replay case.",),
    ),
    AutomationRadarDimension(
        id="visual_replay_validation",
        title="Visual replay validation",
        weight=16,
        why="Turn visual failures into replay-gated evidence with pixel assertions.",
        scores={"codex": 93, "claude_code": 84, "cursor": 80, "echo": 96},
        evidence_check_ids=("browser_pixel_replay_gate",),
        next_actions=("Track pixel-replay latency budgets in CI for larger replay corpora.",),
    ),
    AutomationRadarDimension(
        id="repair_recipe_learning",
        title="Repair recipe learning",
        weight=12,
        why="Cluster repeated browser and desktop failures into deterministic repair recipes.",
        scores={"codex": 88, "claude_code": 82, "cursor": 76, "echo": 95},
        evidence_check_ids=("browser_pixel_replay_gate",),
        next_actions=(
            "Promote high-confidence browser repair recipes only after replay evidence is attached.",
        ),
    ),
    AutomationRadarDimension(
        id="operator_visibility",
        title="Operator visibility",
        weight=10,
        why="Expose automation health, certification, and remediation links where operators work.",
        scores={"codex": 93, "claude_code": 86, "cursor": 83, "echo": 95},
        evidence_check_ids=("operator_visibility",),
        next_actions=(
            "Make every browser and desktop replay case reachable from the operator scorecard.",
        ),
    ),
    AutomationRadarDimension(
        id="thread_native_browser_mode",
        title="Thread-native browser mode",
        weight=3,
        why=(
            "Let the user invoke browser operation directly from a chat turn "
            "with @Browser, then route the same turn through browser tools, "
            "state evidence, and artifacts."
        ),
        scores={"codex": 96, "claude_code": 86, "cursor": 82, "echo": 97},
        evidence_check_ids=("thread_native_browser_activation",),
        next_actions=(
            "Keep @Browser activation wired through input, turn context, tool prompts, and quality gates.",
        ),
    ),
    AutomationRadarDimension(
        id="external_chrome_mode",
        title="External Chrome mode",
        weight=4,
        why=(
            "Let the user invoke signed-in external Google Chrome with @Chrome, "
            "then prefer the extension relay for current-tab state, actions, "
            "screenshots, and site-policy aware automation."
        ),
        scores={"codex": 96, "claude_code": 95, "cursor": 84, "echo": 97},
        evidence_check_ids=("thread_native_external_chrome_activation",),
        next_actions=(
            "Keep @Chrome activation distinct from @Browser and persist the requested/served track receipt for every fallback.",
        ),
    ),
    AutomationRadarDimension(
        id="automation_safety",
        title="Automation safety",
        weight=12,
        why="Persist allow/block policy and require explicit preview-to-execute boundaries.",
        scores={"codex": 95, "claude_code": 91, "cursor": 84, "echo": 96},
        evidence_check_ids=(
            "browser_session_lifecycle",
            "desktop_preview_execute_lease",
        ),
        next_actions=("Keep signed high-risk automation policy coverage release-gated.",),
    ),
    AutomationRadarDimension(
        id="productized_api_bridge",
        title="Productized API bridge",
        weight=7,
        why="Make browser and desktop automation callable from product UI, agents, tests, and repair queues.",
        scores={"codex": 92, "claude_code": 84, "cursor": 80, "echo": 95},
        evidence_check_ids=(
            "browser_session_lifecycle",
            "desktop_preview_execute_lease",
            "operator_visibility",
        ),
        next_actions=("Keep automation radar links wired into scorecard drill-downs.",),
    ),
)


def compute_automation_radar(
    *,
    root: str | Path | None = None,
    review_queue_path: str | Path | None = None,
    target_score: int = 95,
) -> dict[str, Any]:
    base = Path(root) if root is not None else default_project_root(Path(__file__))
    quality = compute_browser_desktop_quality(
        root=base,
        review_queue_path=review_queue_path,
    )
    agent_benchmark = compute_agent_benchmark(root=base)
    parity = compute_parity_certification(root=base)
    policy_rule_drafts = _automation_policy_rule_draft_summary()
    policy_coverage = _automation_policy_rule_coverage_summary()
    checks_by_id = {
        str(row.get("id")): row for row in quality.get("checks", []) if isinstance(row, dict)
    }
    dimensions = [
        _dimension_row(
            dimension,
            checks_by_id=checks_by_id,
            target_score=target_score,
        )
        for dimension in DIMENSIONS
    ]
    overall = {competitor: _weighted_score(dimensions, competitor) for competitor in COMPETITORS}
    gaps = [
        row
        for row in dimensions
        if row["scores"]["echo"] < target_score
        or row["scores"]["echo"] < row["scores"]["codex"]
        or not row["evidence_ready"]
    ]
    strengths = [
        row
        for row in dimensions
        if row["scores"]["echo"] >= row["scores"]["codex"] and row["evidence_ready"]
    ]
    return {
        "schema": "echo.automation_radar.v1",
        "target_score": target_score,
        "scope": "browser_desktop_visual_automation",
        "competitors": list(COMPETITORS),
        "overall": overall,
        "ranking": _ranking(overall),
        "verdict": _verdict(overall),
        "dimensions": dimensions,
        "echo_gaps": gaps,
        "echo_strengths": strengths,
        "browser_desktop_quality": {
            "schema": quality.get("schema"),
            "score": quality.get("score"),
            "passed": quality.get("passed"),
            "total": quality.get("total"),
            "ready": quality.get("ready"),
        },
        "agent_benchmark": {
            "schema": agent_benchmark.get("schema"),
            "score": agent_benchmark.get("score"),
            "passed": agent_benchmark.get("passed"),
            "total": agent_benchmark.get("total"),
            "ready": agent_benchmark.get("ready"),
            "by_dimension": agent_benchmark.get("by_dimension", {}),
        },
        "parity_certification": {
            "schema": parity.get("schema"),
            "passed": parity.get("passed"),
            "total": parity.get("total"),
            "ready": parity.get("ready"),
        },
        "policy_rule_drafts": policy_rule_drafts,
        "policy_rule_coverage": policy_coverage,
        "next_focus": _next_focus(gaps),
    }


def _dimension_row(
    dimension: AutomationRadarDimension,
    *,
    checks_by_id: dict[str, dict[str, Any]],
    target_score: int,
) -> dict[str, Any]:
    evidence = [
        checks_by_id[check_id]
        for check_id in dimension.evidence_check_ids
        if check_id in checks_by_id
    ]
    missing_check_ids = [
        check_id for check_id in dimension.evidence_check_ids if check_id not in checks_by_id
    ]
    evidence_ready = (
        not missing_check_ids
        and bool(evidence)
        and all(bool(row.get("passed")) for row in evidence)
    )
    scores = dict(dimension.scores)
    return {
        "id": dimension.id,
        "title": dimension.title,
        "weight": dimension.weight,
        "why": dimension.why,
        "scores": scores,
        "leader": max(scores, key=lambda key: scores[key]),
        "echo_gap_to_target": max(0, target_score - scores["echo"]),
        "echo_gap_to_codex": scores["codex"] - scores["echo"],
        "evidence_ready": evidence_ready,
        "evidence_checks": [
            {
                "id": row.get("id"),
                "title": row.get("title"),
                "passed": row.get("passed"),
                "missing_paths": row.get("missing_paths", []),
                "missing_terms": row.get("missing_terms", []),
            }
            for row in evidence
        ],
        "missing_check_ids": missing_check_ids,
        "operator_drilldown": {
            "schema": "echo.automation_radar_drilldown.v1",
            "dimension_id": dimension.id,
            "links": _drilldown_links(dimension.id),
        },
        "next_actions": list(dimension.next_actions),
    }


def _drilldown_links(dimension_id: str) -> list[dict[str, Any]]:
    links = [
        {
            "id": "browser_desktop_quality",
            "label": "Browser/desktop quality",
            "method": "GET",
            "href": "/api/evolution/browser-desktop-quality",
        },
        {
            "id": "automation_radar",
            "label": "Automation radar",
            "method": "GET",
            "href": "/api/evolution/automation-radar",
        },
    ]
    if dimension_id in {
        "visual_replay_validation",
        "repair_recipe_learning",
        "operator_visibility",
    }:
        links.append(
            {
                "id": "browser_desktop_repair_recipes",
                "label": "Browser repair recipes",
                "method": "GET",
                "href": "/api/evolution/browser-desktop-repair-recipes",
            }
        )
    if dimension_id in {
        "desktop_preview_execute",
        "desktop_semantic_grounding",
        "automation_safety",
        "productized_api_bridge",
    }:
        links.append(
            {
                "id": "computer_status",
                "label": "Computer automation status",
                "method": "GET",
                "href": "/api/computer/status",
            }
        )
    return links


def _weighted_score(
    dimensions: list[dict[str, Any]],
    competitor: str,
) -> int:
    total_weight = sum(int(row["weight"]) for row in dimensions)
    if total_weight <= 0:
        return 0
    weighted = sum(int(row["weight"]) * int(row["scores"][competitor]) for row in dimensions)
    return int(round(weighted / total_weight))


def _ranking(overall: dict[str, int]) -> list[dict[str, Any]]:
    return sorted(
        [{"competitor": competitor, "score": score} for competitor, score in overall.items()],
        key=lambda row: (row["score"], row["competitor"]),
        reverse=True,
    )


def _verdict(overall: dict[str, int]) -> str:
    echo = overall.get("echo", 0)
    best_other = max(score for competitor, score in overall.items() if competitor != "echo")
    if echo > best_other:
        return "leading"
    if echo >= best_other - 2:
        return "competitive"
    if echo >= best_other - 6:
        return "near_parity"
    return "behind"


def _next_focus(gaps: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    for row in sorted(
        gaps,
        key=lambda item: (
            int(item.get("echo_gap_to_target") or 0),
            int(item.get("weight") or 0),
        ),
        reverse=True,
    ):
        for action in row.get("next_actions") or []:
            if action and action not in actions:
                actions.append(str(action))
            if len(actions) >= 5:
                return actions
    return actions


def _automation_policy_rule_draft_summary() -> dict[str, Any]:
    try:
        from runtime.safety.evolution.policy_review_rules import (
            build_automation_policy_rule_drafts,
            verify_policy_review_rule_draft,
        )

        report = build_automation_policy_rule_drafts(limit=100)
        drafts = report.get("drafts") if isinstance(report.get("drafts"), list) else []
        verified = sum(
            1
            for draft in drafts
            if isinstance(draft, dict) and verify_policy_review_rule_draft(draft).get("ok") is True
        )
        return {
            "schema": report.get("schema"),
            "total": int(report.get("total") or 0),
            "verified": verified,
            "ready": bool(drafts) and verified == len(drafts),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "schema": "echo.automation_policy_rule_drafts.v1",
            "total": 0,
            "verified": 0,
            "ready": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _automation_policy_rule_coverage_summary() -> dict[str, Any]:
    try:
        from runtime.safety.evolution.policy_review_rules import (
            compute_automation_policy_rule_coverage,
        )

        return compute_automation_policy_rule_coverage(limit=100)
    except Exception as exc:  # noqa: BLE001
        return {
            "schema": "echo.automation_policy_rule_coverage.v1",
            "ready": False,
            "total": 0,
            "verified": 0,
            "installable_deny_count": 0,
            "required_tools": [],
            "covered_tools": [],
            "missing_tools": [],
            "invalid_draft_ids": [],
            "required_controls": [],
            "controls_by_tool": {},
            "missing_controls": {},
            "next_actions": [
                f"Automation policy rule coverage unavailable: {type(exc).__name__}: {exc}.",
            ],
        }


__all__ = [
    "AutomationRadarDimension",
    "DIMENSIONS",
    "compute_automation_radar",
]
