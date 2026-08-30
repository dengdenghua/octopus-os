from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.platform.process.paths import app_paths
from runtime.platform.process.paths import project_root as default_project_root


@dataclass(frozen=True)
class BrowserDesktopCheck:
    id: str
    title: str
    paths: tuple[str, ...]
    required_terms: tuple[str, ...]
    weight: int = 1


CHECKS: tuple[BrowserDesktopCheck, ...] = (
    BrowserDesktopCheck(
        id="browser_session_lifecycle",
        title="Browser session lifecycle",
        paths=(
            "runtime/platform/ui/browser_router.py",
            "runtime/platform/ui/_browser_helper_relay.py",
            "runtime/platform/process/paths.py",
            "runtime/platform/runtime_policy/browser_sessions.py",
            "runtime/safety/replay/browser_desktop_replay.py",
            "runtime/execution/suckers/browser_backends.py",
            "tests/test_browser_backends.py",
            "tests/test_browser_router.py",
            "tests/test_browser_sessions.py",
        ),
        required_terms=(
            "session/status",
            "session/ensure",
            "session/health",
            "echo.browser_relay_bridge.v1",
            "echo.browser_relay_site_policy.v1",
            "relay_allowed_hosts",
            "relay_blocked_hosts",
            "browser_policy_path",
            "browser_policy.json",
            "read_json_with_backup",
            "atomic_write_json",
            "ECHO_BROWSER_RELAY_BASE_URL",
            "session/replay-case",
            "session/replay-case/queue",
            "browser-session:",
            "fingerprint",
            "case_id",
            "recovered_from_crash",
            "echo.browser_session_recovery_proof.v1",
            "recovery_revalidated_at",
            "review_queue",
        ),
    ),
    BrowserDesktopCheck(
        id="browser_pixel_replay_gate",
        title="Browser pixel replay gate",
        paths=(
            "runtime/safety/replay/browser_pixel_assertions.py",
            "runtime/execution/suckers/browser_act_skills.py",
            "tests/test_browser_pixel_assertions.py",
            "tests/test_browser_artifact.py",
        ),
        required_terms=(
            "browser_pixel_replay_gate_case",
            "replay_gate_case",
            "browser_pixel_evidence_failed",
            "ReviewQueue",
            "browser_pixel_replay_gate",
        ),
    ),
    BrowserDesktopCheck(
        id="thread_native_browser_activation",
        title="Thread-native browser activation",
        paths=(
            "runtime/core/cerebrum/input_mentions.py",
            "runtime/core/cerebrum/capability_router.py",
            "runtime/sensing/gateway/realtime_turn_input.py",
            "runtime/sensing/gateway/realtime_turn_routing.py",
            "runtime/sensing/gateway/tool_bridge.py",
            "runtime/sensing/gateway/_tool_bridge_session.py",
            "runtime/core/cerebrum/react_loop.py",
            "frontend/src/components/workspace/chat-input-box.tsx",
            "frontend/src/components/workspace/chat-input-box/ChatComposer.tsx",
            "tests/test_input_mentions.py",
            "tests/test_realtime_native_tool_loop.py",
            "tests/test_tool_intent_heuristic.py",
            "frontend/src/components/workspace/chat-input-box.test.tsx",
        ),
        required_terms=(
            "@browser",
            "browser_operation_mode",
            "thread_native",
            "runtime_surfaces",
            "live_browser_state",
            "live_browser_current_url",
            "chat-insert-browser-surface",
            "insertBrowserSurface",
            "thread-native browser operation",
        ),
    ),
    BrowserDesktopCheck(
        id="thread_native_external_chrome_activation",
        title="Thread-native external Chrome activation",
        paths=(
            "runtime/core/cerebrum/input_mentions.py",
            "runtime/core/cerebrum/capability_router.py",
            "runtime/sensing/gateway/realtime_turn_input.py",
            "runtime/sensing/gateway/tool_bridge.py",
            "runtime/sensing/gateway/_tool_bridge_session.py",
            "runtime/core/cerebrum/react_loop.py",
            "runtime/execution/suckers/browser_backend.py",
            "runtime/execution/suckers/browser_backends.py",
            "runtime/execution/suckers/browser_skills.py",
            "runtime/platform/ui/browser_router.py",
            "runtime/platform/ui/_browser_helper_relay.py",
            "extensions/echo-browser-relay/background.js",
            "extensions/echo-browser-relay/content.js",
            "extensions/echo-browser-relay/manifest.json",
            "extensions/echo-browser-relay/sidepanel.html",
            "extensions/echo-browser-relay/sidepanel.css",
            "extensions/echo-browser-relay/sidepanel.js",
            "frontend/src/components/workspace/chat-input-box.tsx",
            "frontend/src/components/workspace/chat-input-box/ChatComposer.tsx",
            "tests/test_input_mentions.py",
            "tests/test_realtime_native_tool_loop.py",
            "tests/test_tool_intent_heuristic.py",
            "tests/test_automation_wiring.py",
            "tests/test_chrome_sidepanel_extension.py",
            "frontend/src/components/workspace/chat-input-box.test.tsx",
        ),
        required_terms=(
            "@chrome",
            "chrome_operation_mode",
            "thread_native_external_chrome",
            "browser_surface",
            "browser_track_preference",
            "browser_track_preference_satisfied",
            "browser_track_fallback",
            "extension_unavailable",
            "extension",
            "site_policy_required",
            "thread-native external chrome operation",
            "chat-insert-chrome-surface",
            "insertchromesurface",
            "capturevisibletab",
            "side_panel",
            "sidepanel.html",
            "chrome.sidepanel",
            "openpanelonactionclick",
            "connect-src",
            "ws://127.0.0.1:8000",
            "echoai browser relay",
            "turn/start",
            "item/agentmessage/delta",
            "request.params",
            "echo.browser_relay_control.v1",
            "echo.browser_relay_tab_lease.v1",
            "human_interrupt",
            "echo.control",
            "echo.useractivity",
            "echo.controlindicator",
            "echo-browser-control-indicator",
            "pointer-events: none",
            "prefers-reduced-motion",
            "browser_state",
            "browser_screenshot",
        ),
    ),
    BrowserDesktopCheck(
        id="desktop_preview_execute_lease",
        title="Desktop preview, execute, and lease safety",
        paths=(
            "runtime/sensing/gateway/computer_router.py",
            "runtime/sensing/gateway/computer_actions.py",
            "runtime/sensing/gateway/computer_runtime_readiness.py",
            "runtime/safety/replay/browser_desktop_replay.py",
            "runtime/execution/suckers/computer_api_skills.py",
            "tests/test_computer_router.py",
        ),
        required_terms=(
            "actions/preview",
            "actions/execute",
            "ECHO_COMPUTER_API_BASE_URL",
            "echo.computer_api_bridge.v1",
            "computer_activity",
            "activity/replay-case",
            "activity/replay-case/queue",
            "computer-activity:",
            "fingerprint",
            "case_id",
            "recent_activity",
            "lease_owner_id",
            "echo.computer_preview_contract.v1",
            "preview_contract",
            "echo.computer_execution_proof.v1",
            "execution_proof",
            "review_queue",
            "echo.computer_runtime_readiness.v1",
            "degraded_capabilities",
            "critical_blockers",
            "recommended_actions",
        ),
    ),
    BrowserDesktopCheck(
        id="desktop_uia_grounding",
        title="Desktop UIA grounding",
        paths=(
            "runtime/execution/suckers/computer_uia_skills.py",
            "runtime/sensing/gateway/computer_router.py",
            "tests/test_computer_uia_skills.py",
            "tests/test_computer_router.py",
        ),
        required_terms=(
            "uia",
            "matched_control",
            "automation_id",
            "computer_uia_replay_assertion",
            "replay_assertion",
            "echo.computer_uia_grounding_trace.v1",
            "source_trace",
            "trace_id",
        ),
    ),
    BrowserDesktopCheck(
        id="desktop_capture_path_repair",
        title="Desktop capture-path repair and rerun evidence",
        paths=(
            "runtime/execution/suckers/computer_use_loop.py",
            "runtime/safety/evolution/browser_desktop_repair_recipes.py",
            "tests/test_computer_use_loop.py",
            "tests/test_browser_desktop_repair_recipes.py",
            "frontend/src/components/workspace/replay-panel.tsx",
            "frontend/src/components/workspace/agent-operator-panel.tsx",
            "frontend/src/components/workspace/agent-operator-panel.test.tsx",
        ),
        required_terms=(
            "capture_screen",
            "captured_path_is_authoritative",
            "echo.computer_screenshot_path_contract.v1",
            "computer_screenshot_path_contract",
            "rerunBrowserDesktopRepairRecipeEvidenceBatch",
            "Rerun blocked browser and desktop repair evidence",
            "promoteSourceCases: false",
            "Source cases remain operator-gated",
        ),
    ),
    BrowserDesktopCheck(
        id="browser_session_recovery_rerun",
        title="Browser-session recovery rerun and operator control",
        paths=(
            "runtime/platform/ui/browser_router.py",
            "runtime/platform/runtime_policy/browser_sessions.py",
            "runtime/safety/evolution/browser_desktop_repair_recipes.py",
            "tests/test_browser_router.py",
            "tests/test_browser_desktop_repair_recipes.py",
            "frontend/src/components/workspace/replay-panel.tsx",
            "frontend/src/components/workspace/agent-operator-panel.tsx",
            "frontend/src/components/workspace/agent-operator/index.tsx",
        ),
        required_terms=(
            "echo.browser_session_recovery_proof.v1",
            "recovery_proof",
            "rerunBrowserDesktopRepairRecipeEvidenceBatch",
            "promoteSourceCases: false",
            "Source cases remain operator-gated",
        ),
    ),
    BrowserDesktopCheck(
        id="operator_visibility",
        title="Operator-visible browser and desktop health",
        paths=(
            "frontend/src/components/workspace/agent-operator-panel.tsx",
            "frontend/src/components/workspace/agent-operator-panel.test.tsx",
            "frontend/src/components/workspace/browser-preview-panel.tsx",
            "frontend/src/components/workspace/embedded-browser/browser-panel.tsx",
        ),
        required_terms=(
            "browser",
            "certified",
            "sessionHealth",
            "Plugin health",
        ),
    ),
)


def compute_browser_desktop_quality(
    *,
    root: str | Path | None = None,
    review_queue_path: str | Path | None = None,
) -> dict[str, Any]:
    base = Path(root) if root is not None else default_project_root(Path(__file__))
    checks = [_check_row(base, check) for check in CHECKS]
    total_weight = sum(int(row["weight"]) for row in checks)
    passed_weight = sum(int(row["weight"]) for row in checks if row["passed"])
    score = round(passed_weight / max(1, total_weight), 3)
    return {
        "schema": "echo.browser_desktop_quality.v1",
        "score": score,
        "passed": sum(1 for row in checks if row["passed"]),
        "total": len(checks),
        "ready": all(row["passed"] for row in checks),
        "checks": checks,
        "browser_relay_bridge": _browser_relay_bridge_diagnostics(),
        "computer_api_bridge": _computer_api_bridge_diagnostics(),
        "replay_trends": _browser_replay_trends(review_queue_path),
        "next_actions": [str(row["next_action"]) for row in checks if not row["passed"]],
    }


def _check_row(base: Path, check: BrowserDesktopCheck) -> dict[str, Any]:
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
        "next_action": f"Complete browser/desktop quality check: {check.title}.",
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _browser_relay_bridge_diagnostics() -> dict[str, Any]:
    try:
        from runtime.execution.suckers.browser_backends import (
            browser_relay_diagnostics,
        )

        diagnostics = browser_relay_diagnostics()
        if isinstance(diagnostics, dict):
            return diagnostics
    except Exception as exc:  # noqa: BLE001
        return {
            "schema": "echo.browser_relay_bridge.v1",
            "base_url": "",
            "configured_by": "",
            "env_keys": [],
            "default_gateway_base_url": "",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "schema": "echo.browser_relay_bridge.v1",
        "base_url": "",
        "configured_by": "",
        "env_keys": [],
        "default_gateway_base_url": "",
        "error": "browser relay diagnostics unavailable",
    }


def _computer_api_bridge_diagnostics() -> dict[str, Any]:
    try:
        from runtime.execution.suckers.computer_api_skills import (
            _computer_api_diagnostics,
        )

        diagnostics = _computer_api_diagnostics()
        if isinstance(diagnostics, dict):
            return diagnostics
    except Exception as exc:  # noqa: BLE001
        return {
            "schema": "echo.computer_api_bridge.v1",
            "base_url": "",
            "configured_by": "",
            "env_keys": [],
            "default_gateway_base_url": "",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "schema": "echo.computer_api_bridge.v1",
        "base_url": "",
        "configured_by": "",
        "env_keys": [],
        "default_gateway_base_url": "",
        "error": "computer api diagnostics unavailable",
    }


def _browser_replay_trends(
    review_queue_path: str | Path | None,
) -> dict[str, Any]:
    try:
        from runtime.memory.learning.review_queue import ReviewQueue

        queue = ReviewQueue(
            Path(review_queue_path)
            if review_queue_path is not None
            else app_paths().review_queue_path,
        )
        rows = queue.items(target_bucket="browser_desktop_replay", limit=1000)["items"]
    except Exception:  # noqa: BLE001
        rows = []
    by_status: dict[str, int] = {}
    by_candidate_kind: dict[str, int] = {}
    stale_source_artifact_count = 0
    for row in rows:
        status = str(row.get("status") or "pending")
        by_status[status] = by_status.get(status, 0) + 1
        kind = str(row.get("candidate_kind") or "unknown")
        by_candidate_kind[kind] = by_candidate_kind.get(kind, 0) + 1
        if status == "pending" and _has_stale_source_artifact(row):
            stale_source_artifact_count += 1
    pending = by_status.get("pending", 0)
    promoted = by_status.get("promoted", 0)
    rejected = by_status.get("rejected", 0)
    total = len(rows)
    reviewed = promoted + rejected + by_status.get("archived", 0)
    review_rate = round(reviewed / total, 3) if total else 0.0
    repair_recipe_summary = _browser_repair_recipe_summary(review_queue_path)
    return {
        "schema": "echo.browser_desktop_replay_trends.v1",
        "total": total,
        "pending_count": pending,
        "reviewed_count": reviewed,
        "promoted_count": promoted,
        "rejected_count": rejected,
        "review_rate": review_rate,
        "stale_source_artifact_count": stale_source_artifact_count,
        "by_status": dict(sorted(by_status.items())),
        "by_candidate_kind": dict(sorted(by_candidate_kind.items())),
        "repair_recipe_summary": repair_recipe_summary,
        "latest": [
            {
                "id": row.get("id"),
                "title": row.get("title"),
                "status": row.get("status"),
                "candidate_kind": row.get("candidate_kind"),
                "updated_at": row.get("updated_at"),
            }
            for row in rows[:5]
        ],
        "next_actions": _browser_replay_next_actions(
            pending=pending,
            total=total,
            review_rate=review_rate,
            stale_source_artifact_count=stale_source_artifact_count,
        ),
    }


def _has_stale_source_artifact(row: dict[str, Any]) -> bool:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    artifact = metadata.get("artifact") if isinstance(metadata.get("artifact"), dict) else {}
    local_path = str(artifact.get("local_path") or "").strip()
    return bool(local_path) and not Path(local_path).is_file()


def _browser_repair_recipe_summary(
    review_queue_path: str | Path | None,
) -> dict[str, Any]:
    try:
        from runtime.safety.evolution.browser_desktop_repair_recipes import (
            compute_browser_desktop_repair_recipes,
        )

        report = compute_browser_desktop_repair_recipes(
            review_queue_path=review_queue_path,
            limit=1000,
        )
        recipes = report.get("recipes") if isinstance(report.get("recipes"), list) else []
    except Exception:  # noqa: BLE001
        report = {}
        recipes = []
    return {
        "schema": "echo.browser_desktop_repair_recipe_summary.v1",
        "recipe_count": int(report.get("recipe_count") or 0),
        "total_pending_cases": int(report.get("total_pending_cases") or 0),
        "top_recipes": [
            {
                "recipe_id": recipe.get("recipe_id"),
                "title": recipe.get("title"),
                "priority": recipe.get("priority"),
                "occurrences": recipe.get("occurrences"),
                "candidate_kind": recipe.get("candidate_kind"),
            }
            for recipe in recipes[:3]
            if isinstance(recipe, dict)
        ],
    }


def _browser_replay_next_actions(
    *,
    pending: int,
    total: int,
    review_rate: float,
    stale_source_artifact_count: int,
) -> list[str]:
    if stale_source_artifact_count:
        return [
            f"Regenerate or reject {stale_source_artifact_count} stale browser/desktop replay artifact(s).",
        ]
    if pending:
        return [
            f"Review {pending} pending browser/desktop replay case(s) before promotion.",
        ]
    if total and review_rate >= 0.8:
        return ["Browser/desktop replay review trend is healthy."]
    return ["Capture browser/desktop replay cases from the next visual automation run."]


__all__ = [
    "BrowserDesktopCheck",
    "CHECKS",
    "compute_browser_desktop_quality",
]
