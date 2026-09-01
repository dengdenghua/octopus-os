from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.platform.process.paths import project_root as default_project_root


@dataclass(frozen=True)
class PermissionSandboxCheck:
    id: str
    title: str
    paths: tuple[str, ...]
    required_terms: tuple[str, ...]
    weight: int = 1


CHECKS: tuple[PermissionSandboxCheck, ...] = (
    PermissionSandboxCheck(
        id="dangerous_tool_catalog",
        title="Dangerous tool catalog",
        paths=(
            "runtime/safety/approval/approval_gate.py",
            "tests/test_approval_gate.py",
        ),
        required_terms=(
            "DANGEROUS_TOOLS",
            "DANGEROUS_PREFIXES",
            "is_dangerous_tool",
            "computer_plan_next",
            "computer_execute_token",
            "browser_",
            "live_browser_",
            "android_",
        ),
    ),
    PermissionSandboxCheck(
        id="risk_policy_matrix",
        title="Risk policy matrix",
        paths=(
            "runtime/safety/approval/approval_gate.py",
            "runtime/safety/audit/trust_gateway.py",
            "tests/test_trust_gateway.py",
            "tests/test_prompt_injection.py",
        ),
        required_terms=(
            "ApprovalRiskPolicy",
            "assess_approval_risk",
            "prompt_injection_taint",
            "critical",
            "destructive_command",
        ),
    ),
    PermissionSandboxCheck(
        id="sandbox_scope_enforcement",
        title="Sandbox and scope enforcement",
        paths=(
            "runtime/platform/process/scope.py",
            "runtime/safety/sandboxing/sandbox.py",
            "runtime/safety/auth/path_guard.py",
            "tests/test_scope.py",
            "tests/test_path_guard.py",
            "tests/test_write_skills.py",
        ),
        required_terms=(
            "mode-gated",
            "sandbox_dir",
            "allows_write",
            "escapes_sandbox",
            "sensitive",
            "env_for",
            "allow_network",
        ),
    ),
    PermissionSandboxCheck(
        id="signed_policy_review",
        title="Signed policy review",
        paths=(
            "runtime/safety/evolution/policy_review_rules.py",
            "tests/test_policy_review_rules.py",
            "runtime/sensing/gateway/evolution_router.py",
            "runtime/sensing/gateway/plugins_router.py",
        ),
        required_terms=(
            "echo.policy_review_rule_signature.v1",
            "verify_policy_review_rule_draft",
            "install_policy_review_rule_draft",
            "confirm_install",
            "plugin_permission_rule_install",
            "automation_policy_rule_install",
        ),
    ),
    PermissionSandboxCheck(
        id="publisher_provenance_verification",
        title="Trusted plugin publisher provenance",
        paths=(
            "runtime/platform/plugins/publisher_provenance.py",
            "runtime/platform/plugins/codex_discovery.py",
            "runtime/sensing/gateway/plugins_router.py",
            "tests/test_codex_plugin_smoke.py",
            "docs/guide/plugin-author-migration.md",
        ),
        required_terms=(
            "echo.plugin_publisher_signature.v1",
            "echo.plugin_publisher_trust_store.v1",
            "canonical_publisher_signature_payload",
            "verify_plugin_publisher_provenance",
            "ed25519",
            "revoked",
            "publisher_verified_count",
        ),
    ),
    PermissionSandboxCheck(
        id="high_risk_policy_coverage",
        title="High-risk policy coverage",
        paths=(
            "runtime/safety/evolution/policy_review_rules.py",
            "tests/test_policy_review_rules.py",
            "runtime/safety/evolution/automation_radar.py",
            "runtime/safety/evolution/permission_sandbox_quality.py",
        ),
        required_terms=(
            "echo.permission_sandbox_quality.v1",
            "compute_automation_policy_rule_coverage",
            "installable_deny_count",
            "required_controls",
            "echo.automation_policy_rule_drafts.v1",
            "echo.plugin_permission_rule_drafts.v1",
            "live_browser_*",
            "browser_*",
            "computer_execute_token",
            "mcp__",
            "use_capability",
        ),
    ),
    PermissionSandboxCheck(
        id="monotonic_delegation_context",
        title="Monotonic delegated permission context",
        paths=(
            "runtime/safety/auth/arg_guard.py",
            "runtime/execution/suckers/delegation_skills.py",
            "runtime/execution/suckers/_delegation_skills_common.py",
            "runtime/execution/tool_engine/executor.py",
            "tests/test_arg_guard_sec1.py",
            "tests/test_call_agent_parallel_partial.py",
        ),
        required_terms=(
            "echo.delegation_context_policy.v1",
            "is_model_protected_context_key",
            "_strip_delegation_context_overrides",
            "monotonic",
            "sandboxPolicy",
            "_inherited_injection_taint",
            "stripped_keys",
        ),
    ),
)


def compute_permission_sandbox_quality(
    *,
    root: str | Path | None = None,
) -> dict[str, Any]:
    base = Path(root) if root is not None else default_project_root(Path(__file__))
    checks = [_check_row(base, check) for check in CHECKS]
    total_weight = sum(int(row["weight"]) for row in checks)
    passed_weight = sum(int(row["weight"]) for row in checks if row["passed"])
    automation = _automation_policy_coverage()
    plugins = _plugin_policy_coverage(base)
    return {
        "schema": "echo.permission_sandbox_quality.v1",
        "score": round(passed_weight / max(1, total_weight), 3),
        "passed": sum(1 for row in checks if row["passed"]),
        "total": len(checks),
        "ready": all(row["passed"] for row in checks)
        and bool(automation.get("ready"))
        and bool(plugins.get("ready")),
        "checks": checks,
        "automation_policy_coverage": automation,
        "plugin_policy_coverage": plugins,
        "next_actions": [str(row["next_action"]) for row in checks if not row["passed"]]
        + list(automation.get("next_actions") or [])
        + list(plugins.get("next_actions") or []),
    }


def _automation_policy_coverage() -> dict[str, Any]:
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
            "required_tools": [],
            "covered_tools": [],
            "missing_tools": [],
            "next_actions": [f"Automation policy coverage unavailable: {exc}."],
        }


def _plugin_policy_coverage(base: Path) -> dict[str, Any]:
    try:
        from runtime.platform.plugins.codex_discovery import discover_codex_plugins
        from runtime.safety.evolution.policy_review_rules import (
            build_plugin_permission_rule_drafts,
            verify_policy_review_rule_draft,
        )

        plugins = discover_codex_plugins(
            [
                base / ".echo" / "plugins" / "codex",
            ]
        )
        report = build_plugin_permission_rule_drafts(plugins=plugins, limit=500)
        drafts = [draft for draft in report.get("drafts") or [] if isinstance(draft, dict)]
        verified = sum(
            1 for draft in drafts if verify_policy_review_rule_draft(draft).get("ok") is True
        )
        ready = bool(plugins) and verified == len(drafts)
        return {
            "schema": "echo.plugin_permission_rule_coverage.v1",
            # Metadata-only plugins deliberately produce no deny-rule drafts;
            # an empty draft set is valid when discovery found plugins and no
            # executable permission surface requires review.
            "ready": ready,
            "plugin_count": len(plugins),
            "total": len(drafts),
            "verified": verified,
            "next_actions": (
                []
                if ready
                else ["Add verified plugin permission rule drafts."]
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "schema": "echo.plugin_permission_rule_coverage.v1",
            "ready": False,
            "plugin_count": 0,
            "total": 0,
            "verified": 0,
            "next_actions": [f"Plugin policy coverage unavailable: {exc}."],
        }


def _check_row(base: Path, check: PermissionSandboxCheck) -> dict[str, Any]:
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
        "next_action": f"Complete permission/sandbox quality check: {check.title}.",
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


__all__ = [
    "CHECKS",
    "PermissionSandboxCheck",
    "compute_permission_sandbox_quality",
]
