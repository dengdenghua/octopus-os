from __future__ import annotations

from typing import Any


def _operator_drilldown(
    *,
    dimension_id: str,
    evidence_ids: tuple[str, ...],
    certified_floor: int,
) -> dict[str, Any]:
    links = [
        {
            "id": "queue_gap",
            "label": "Queue scorecard gap",
            "method": "POST",
            "href": "/api/evolution/agent-scorecard/gaps/queue",
            "body": {
                "target_score": 95,
                "limit": 1,
                "dimension_id": dimension_id,
                "reason": "operator scorecard drill-down remediation",
            },
        },
        {
            "id": "review_queue",
            "label": "Review queued remediation",
            "method": "GET",
            "href": (
                "/api/agent-trace/review-queue?"
                f"target_bucket=scorecard_gap_backlog&candidate_kind=scorecard_gap:{dimension_id}"
            ),
        },
        {
            "id": "promotion_audit",
            "label": "Promotion audit",
            "method": "GET",
            "href": "/api/agent-trace/review-queue/promotions/audit/summary",
        },
    ]
    evidence_links = _operator_evidence_links(dimension_id, evidence_ids)
    return {
        "schema": "echo.scorecard_operator_drilldown.v1",
        "dimension_id": dimension_id,
        "certified_floor": certified_floor,
        "links": links + evidence_links,
        "source_refs": [
            {
                "kind": "review_queue",
                "candidate_kind": f"scorecard_gap:{dimension_id}",
                "target_bucket": "scorecard_gap_backlog",
            },
            {
                "kind": "audit",
                "target": "approval_policy" if "approvals_sandbox_security" in evidence_ids else "",
            },
        ],
    }


def _operator_evidence_links(
    dimension_id: str,
    evidence_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    if dimension_id == "general_agent_loop":
        links.append(
            {
                "id": "agent_loop_quality",
                "label": "Agent loop quality",
                "method": "GET",
                "href": "/api/evolution/agent-loop-quality",
            }
        )
    if (
        dimension_id == "model_provider_plugin_interop"
        or "model_provider_plugin_interop" in evidence_ids
    ):
        links.extend(
            [
                {
                    "id": "model_provider_plugins",
                    "label": "Model-provider plugins",
                    "method": "GET",
                    "href": "/api/capabilities",
                },
                {
                    "id": "opencode_zen_status",
                    "label": "OpenCode Zen adapter status",
                    "method": "GET",
                    "href": "/api/capabilities/opencode-zen/status",
                },
                {
                    "id": "opencode_zen_connect",
                    "label": "Connect OpenCode Zen adapter",
                    "method": "POST",
                    "href": "/api/capabilities/opencode-zen/connect",
                },
            ]
        )
    if dimension_id == "repo_context" or "long_term_learning" in evidence_ids:
        links.append(
            {
                "id": "repo_context_quality",
                "label": "Repo context quality",
                "method": "GET",
                "href": "/api/evolution/repo-context-quality",
            }
        )
    if dimension_id == "permissions_sandbox" or "approvals_sandbox_security" in evidence_ids:
        links.append(
            {
                "id": "permission_sandbox_quality",
                "label": "Permission/sandbox quality",
                "method": "GET",
                "href": "/api/evolution/permission-sandbox-quality",
            }
        )
    if dimension_id == "product_experience":
        links.append(
            {
                "id": "product_experience_quality",
                "label": "Product experience quality",
                "method": "GET",
                "href": "/api/evolution/product-experience-quality",
            }
        )
    if "browser_computer_use" in evidence_ids or dimension_id == "browser_desktop":
        links.append(
            {
                "id": "automation_radar",
                "label": "Automation radar",
                "method": "GET",
                "href": "/api/evolution/automation-radar",
            }
        )
        links.append(
            {
                "id": "browser_desktop_quality",
                "label": "Browser/desktop quality",
                "method": "GET",
                "href": "/api/evolution/browser-desktop-quality",
            }
        )
        links.append(
            {
                "id": "browser_desktop_repair_recipes",
                "label": "Browser repair recipes",
                "method": "GET",
                "href": "/api/evolution/browser-desktop-repair-recipes",
            }
        )
    if "skills_plugins_hooks" in evidence_ids or dimension_id in {
        "extensions_hooks",
        "ecosystem_maturity",
        "permissions_sandbox",
    }:
        links.append(
            {
                "id": "plugin_smoke_summary",
                "label": "Plugin smoke summary",
                "method": "GET",
                "href": "/api/plugins/smoke-summary",
            }
        )
        links.append(
            {
                "id": "plugin_permission_rule_drafts",
                "label": "Plugin permission rule drafts",
                "method": "GET",
                "href": "/api/plugins/permission-rule-drafts",
            }
        )
        links.append(
            {
                "id": "plugin_migration_readiness",
                "label": "Plugin migration readiness",
                "method": "GET",
                "href": "/api/plugins/migration-readiness",
            }
        )
        links.append(
            {
                "id": "plugin_registry_updates",
                "label": "Verified plugin registry updates",
                "method": "GET",
                "href": "/api/plugins/registry/updates",
            }
        )
        links.append(
            {
                "id": "plugin_registry_install",
                "label": "Install verified registry plugin",
                "method": "POST",
                "href": "/api/plugins/registry/install",
            }
        )
        links.append(
            {
                "id": "plugin_publisher_trust",
                "label": "Plugin publisher trust",
                "method": "GET",
                "href": "/api/plugins/publisher-trust",
            }
        )
        links.append(
            {
                "id": "plugin_lifecycle_history",
                "label": "Plugin lifecycle history",
                "method": "GET",
                "href": "/api/plugins/lifecycle/history",
            }
        )
    if "agent_organization_os" in evidence_ids or dimension_id in {
        "subagents_parallelism",
        "differentiated_agent_os",
    }:
        links.append(
            {
                "id": "team_tasks",
                "label": "Team task list",
                "method": "GET",
                "href": "/api/team-tasks",
            }
        )
        links.append(
            {
                "id": "team_task_process_timeline",
                "label": "Team task process timeline",
                "method": "GET",
                "href_template": "/api/team-tasks/{task_id}/process-timeline",
            }
        )
        links.append(
            {
                "id": "projectos_process_timeline",
                "label": "Project OS process timeline",
                "method": "GET",
                "href_template": "/api/projects/{project_id}/process-timeline",
            }
        )
    if "record_replay_gate" in evidence_ids or "governance_audit" in evidence_ids:
        links.append(
            {
                "id": "governance_audit_export",
                "label": "Governance audit export",
                "method": "GET",
                "href": "/api/agent-trace/review-queue/promotions/audit/export",
            }
        )
        links.append(
            {
                "id": "governance_audit_rotation",
                "label": "Governance audit rotation",
                "method": "GET",
                "href": "/api/agent-trace/review-queue/promotions/audit/rotation",
            }
        )
    if "long_term_learning" in evidence_ids:
        links.append(
            {
                "id": "experience_ledger",
                "label": "Experience ledger",
                "method": "GET",
                "href": "/api/agent-trace/experience",
            }
        )
        links.append(
            {
                "id": "experience_ledger_recall",
                "label": "Experience recall",
                "method": "GET",
                "href": "/api/agent-trace/experience-ledger/recall",
            }
        )
    if dimension_id == "digital_employee_workflows":
        links.append(
            {
                "id": "digital_employee_quality",
                "label": "Digital employee quality",
                "method": "GET",
                "href": "/api/evolution/digital-employee-quality",
            }
        )
    return links
