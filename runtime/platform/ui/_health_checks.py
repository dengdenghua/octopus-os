"""Private surface-info builders for the health router.

Pure structural split of ``health_router``: the api/webui/model_compat/
orchestration/run_evidence/automation self-check surface builders. No logic
changes.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

from fastapi import Request

from ._health_contracts import (
    _automation_method_contracts,
    _contract_has_field,
    _contract_present,
    _iter_app_routes,
    _orchestration_model_contracts,
    _orchestrator_method_contracts,
    _route_surface_info,
    _run_evidence_method_contracts,
)


def _api_surface_info(request: Request | None) -> dict[str, Any]:
    app = getattr(request, "app", None) if request is not None else None
    routes = _iter_app_routes(app)
    route_paths = sorted(
        {
            str(getattr(route, "path", "") or "")
            for route in routes
            if str(getattr(route, "path", "") or "")
        }
    )
    required = (
        "/api/health",
        "/api/status",
        "/api/runtime/self-check",
    )
    missing = [path for path in required if path not in route_paths]
    return {
        "schema": "echo.api_surface.v1",
        "route_count": len(route_paths),
        "required_routes": list(required),
        "missing_required_routes": missing,
        "required_routes_present": not missing,
    }


def _webui_static_info(root: Path) -> dict[str, Any]:
    env_path = os.environ.get("ECHO_WEBUI_DIST") or ""
    candidates = _webui_dist_candidates(root, env_path)
    env_candidate = candidates[0] if env_path and candidates else None
    env_dist_invalid = bool(
        env_candidate and not (bool(env_candidate["exists"]) and bool(env_candidate["has_index"]))
    )
    selected = next(
        (row for row in candidates if row["exists"] and row["has_index"]),
        None,
    )
    assets_count = 0
    if selected is not None:
        assets_dir = Path(str(selected["path"])) / "assets"
        if assets_dir.is_dir():
            with contextlib.suppress(OSError):
                assets_count = sum(1 for item in assets_dir.iterdir() if item.is_file())
    dev_fallback_expected = not bool(env_path) and selected is None
    detail = (
        f"configured ECHO_WEBUI_DIST is invalid: {env_path}; "
        f"fallback={selected['path'] if selected is not None else 'none'}"
        if env_dist_invalid
        else f"dist={selected['path']} assets={assets_count}"
        if selected is not None
        else "frontend dist not found; dev server fallback expected"
        if dev_fallback_expected
        else f"configured ECHO_WEBUI_DIST is invalid: {env_path}"
    )
    return {
        "schema": "echo.webui_static.v1",
        "available": selected is not None,
        "selected_dist": str(selected["path"]) if selected is not None else "",
        "env_dist": env_path,
        "env_dist_invalid": env_dist_invalid,
        "assets_count": assets_count,
        "dev_fallback_expected": dev_fallback_expected,
        "candidates": candidates,
        "detail": detail,
    }


def _webui_dist_candidates(root: Path, env_path: str) -> list[dict[str, Any]]:
    raw_candidates: list[tuple[str, Path]] = []
    if env_path:
        raw_candidates.append(("env", Path(env_path)))
    raw_candidates.extend(
        [
            ("frontend_dist", root / "frontend" / "dist"),
            ("ui_package_dist", Path(__file__).resolve().parent / "dist"),
        ]
    )
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source, path in raw_candidates:
        text = str(path)
        if text in seen:
            continue
        seen.add(text)
        out.append(
            {
                "source": source,
                "path": text,
                "exists": path.is_dir(),
                "has_index": (path / "index.html").is_file(),
                "has_assets": (path / "assets").is_dir(),
            }
        )
    return out


def _model_compat_info() -> dict[str, Any]:
    try:
        from runtime.sensing.model_router.openai_compat_providers import (
            REQUIRED_DOMESTIC_PROFILE_IDS,
            audit_openai_compat_profile_catalog,
            describe_openai_compat_profile,
            known_openai_compat_profiles,
        )
        from runtime.sensing.model_router.openai_compat_smoke_matrix import (
            openai_compat_smoke_readiness,
        )

        required_profile_ids = list(REQUIRED_DOMESTIC_PROFILE_IDS)
        profiles = list(known_openai_compat_profiles())
        audit = audit_openai_compat_profile_catalog(REQUIRED_DOMESTIC_PROFILE_IDS)
        summaries = [describe_openai_compat_profile(profile) for profile in profiles]
        profile_ids = [str(summary.get("id") or "") for summary in summaries]
        by_id = {str(summary.get("id") or ""): summary for summary in summaries}
        missing = list(audit["missing_required_profile_ids"])
        return {
            "schema": "echo.openai_compat_profile_self_check.v1",
            "available": True,
            "profile_count": len(profiles),
            "profile_ids": profile_ids,
            "required_profile_ids": required_profile_ids,
            "missing_required_profile_ids": missing,
            "required_profiles_present": bool(audit["catalog_ready"]),
            "domestic_profile_count": len(required_profile_ids) - len(missing),
            "smoke_provider_ids": audit["smoke_provider_ids"],
            "missing_smoke_provider_ids": audit["missing_smoke_provider_ids"],
            "orphan_smoke_provider_ids": audit["orphan_smoke_provider_ids"],
            "resolver_mismatches": audit["resolver_mismatches"],
            "model_alias_mismatches": audit["model_alias_mismatches"],
            "request_contract_mismatches": audit["request_contract_mismatches"],
            "request_contract_count": len(audit["request_contract_probes"]),
            "request_contract_ready": not audit["request_contract_mismatches"],
            "request_contract_probes": audit["request_contract_probes"],
            "sample_probes": audit["sample_probes"],
            "live_smoke": openai_compat_smoke_readiness(),
            "domestic_profiles": [
                {
                    "id": profile_id,
                    "display_name": str(
                        by_id.get(profile_id, {}).get("display_name") or profile_id
                    ),
                    "compat_score": by_id.get(profile_id, {}).get("compat_score"),
                    "normalization_hints": by_id.get(profile_id, {}).get(
                        "normalization_hints",
                        [],
                    ),
                }
                for profile_id in required_profile_ids
                if profile_id in by_id
            ],
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001
        from runtime.sensing.model_router.openai_compat_providers import (
            REQUIRED_DOMESTIC_PROFILE_IDS,
        )

        required_profile_ids = list(REQUIRED_DOMESTIC_PROFILE_IDS)
        return {
            "schema": "echo.openai_compat_profile_self_check.v1",
            "available": False,
            "profile_count": 0,
            "profile_ids": [],
            "required_profile_ids": required_profile_ids,
            "missing_required_profile_ids": required_profile_ids,
            "required_profiles_present": False,
            "domestic_profile_count": 0,
            "smoke_provider_ids": [],
            "missing_smoke_provider_ids": required_profile_ids,
            "orphan_smoke_provider_ids": [],
            "resolver_mismatches": [],
            "model_alias_mismatches": [],
            "request_contract_mismatches": [],
            "request_contract_count": 0,
            "request_contract_ready": False,
            "request_contract_probes": [],
            "sample_probes": [],
            "domestic_profiles": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def _orchestration_surface_info(request: Request | None) -> dict[str, Any]:
    required_routes = {
        "/api/agents/parallel/status": ["GET"],
        "/api/agents/parallel/batch/{batch_id}": ["GET"],
        "/api/agents/parallel/batch/{batch_id}/recovery-snapshot": ["GET"],
        "/api/agents/parallel/dispatch": ["POST"],
        "/api/agents/parallel/split": ["POST"],
        "/api/agents/parallel/cancel/{task_id}": ["POST"],
        "/api/agents/parallel/cancel-all": ["POST"],
        "/api/agents/parallel/stream/{batch_id}": ["GET"],
    }
    route_surface = _route_surface_info(request, required_routes)
    model_contracts = _orchestration_model_contracts()
    method_contracts = _orchestrator_method_contracts()
    missing_model_fields = [
        {
            "model": row["model"],
            "missing_fields": row["missing_fields"],
        }
        for row in model_contracts
        if row["missing_fields"]
    ]
    missing_methods = [
        {
            "method": row["method"],
            "reason": row["reason"],
        }
        for row in method_contracts
        if not row["present"]
    ]
    replay_contract = next(
        (row for row in method_contracts if row["method"] == "subscribe.after_sequence"),
        {"present": False},
    )
    ready = (
        route_surface["required_routes_present"]
        and not missing_model_fields
        and not missing_methods
    )
    return {
        "schema": "echo.orchestration_surface_self_check.v1",
        "ready": ready,
        "route_count": route_surface["route_count"],
        "required_routes": list(required_routes),
        "missing_required_routes": route_surface["missing_required_routes"],
        "route_methods": route_surface["route_methods"],
        "missing_route_methods": route_surface["missing_route_methods"],
        "model_contracts": model_contracts,
        "missing_model_fields": missing_model_fields,
        "method_contracts": method_contracts,
        "missing_methods": missing_methods,
        "capabilities": {
            "parallel_dispatch": route_surface["has_required_route"][
                "/api/agents/parallel/dispatch"
            ],
            "split_planning": route_surface["has_required_route"]["/api/agents/parallel/split"],
            "recovery_snapshot": route_surface["has_required_route"][
                "/api/agents/parallel/batch/{batch_id}/recovery-snapshot"
            ],
            "sse_event_replay": bool(replay_contract.get("present")),
            "completion_receipt": _contract_has_field(
                model_contracts,
                "BatchResult",
                "completion_receipt",
            ),
            "file_write_observability": _contract_has_field(
                model_contracts,
                "BatchResult",
                "file_write_observability",
            ),
            "work_contracts": _contract_has_field(
                model_contracts,
                "BatchPlan",
                "contracts",
            ),
            "owner_scoping": all(
                row["present"]
                for row in method_contracts
                if row["method"]
                in {
                    "get_batch_owner",
                    "get_task_owner",
                    "cancel_all_for_owner",
                }
            ),
        },
        "error": "",
    }


def _run_evidence_surface_info(request: Request | None) -> dict[str, Any]:
    required_routes = {
        "/api/agent-trace/stats": ["GET"],
        "/api/agent-trace/events": ["GET"],
        "/api/agent-trace/task-runs": ["GET"],
        "/api/agent-trace/task-runs/{task_id}": ["GET"],
        "/api/agent-trace/task-runs/{task_id}/review": ["GET"],
        "/api/agent-trace/task-runs/{task_id}/replay-case": ["GET"],
        "/api/agent-trace/task-runs/{task_id}/replay-evaluation": ["GET"],
        "/api/agent-trace/task-runs/{task_id}/process-timeline": ["GET"],
        "/api/agent-trace/task-runs/{task_id}/review/commit": ["POST"],
        "/api/agent-trace/task-runs/{task_id}/review/queue": ["POST"],
        "/api/agent-trace/replay-cases": ["GET"],
        "/api/agent-trace/replay-evaluations": ["GET"],
        "/api/agent-trace/replay-gate": ["GET"],
        "/api/agent-trace/experience-ledger": ["GET"],
        "/api/agent-trace/experience-ledger/weekly-summary": ["GET"],
        "/api/agent-trace/experience-ledger/quality-summary": ["GET"],
        "/api/agent-trace/review-queue": ["GET"],
        "/api/agent-trace/review-queue/summary": ["GET"],
        "/api/agent-trace/review-queue/{item_id}/decision": ["POST"],
        "/api/agent-trace/review-queue/promotions/plan": ["POST"],
        "/api/agent-trace/review-queue/promotions/apply": ["POST"],
        "/api/agent-trace/review-queue/promotions/audit": ["GET"],
        "/api/agent-trace/review-queue/promotions/audit/summary": ["GET"],
        "/api/agent-trace/checkpoints": ["GET"],
        "/api/agent-trace/checkpoints/latest": ["GET"],
        "/api/agent-trace/checkpoints/{checkpoint_id}/resume-proposal": ["GET"],
        "/api/agent-trace/resume-proposals": ["GET"],
        "/api/agent-trace/resume-requests": ["GET"],
        "/api/loops/{run_id}/review": ["GET"],
        "/api/loops/{run_id}/resume-proposal": ["GET"],
        "/api/loops/{run_id}/replay-case": ["GET"],
        "/api/loops/{run_id}/replay-evaluation": ["GET"],
    }
    route_surface = _route_surface_info(request, required_routes)
    method_contracts = _run_evidence_method_contracts()
    missing_methods = [
        {
            "method": row["method"],
            "reason": row["reason"],
        }
        for row in method_contracts
        if not row["present"]
    ]
    ready = route_surface["required_routes_present"] and not missing_methods
    return {
        "schema": "echo.run_evidence_surface_self_check.v1",
        "ready": ready,
        "route_count": route_surface["route_count"],
        "required_routes": list(required_routes),
        "missing_required_routes": route_surface["missing_required_routes"],
        "route_methods": route_surface["route_methods"],
        "missing_route_methods": route_surface["missing_route_methods"],
        "method_contracts": method_contracts,
        "missing_methods": missing_methods,
        "capabilities": {
            "trace_stats": route_surface["has_required_route"]["/api/agent-trace/stats"],
            "task_run_review": route_surface["has_required_route"][
                "/api/agent-trace/task-runs/{task_id}/review"
            ],
            "task_run_replay_case": route_surface["has_required_route"][
                "/api/agent-trace/task-runs/{task_id}/replay-case"
            ],
            "task_run_replay_evaluation": route_surface["has_required_route"][
                "/api/agent-trace/task-runs/{task_id}/replay-evaluation"
            ],
            "replay_gate": route_surface["has_required_route"]["/api/agent-trace/replay-gate"],
            "process_timeline": route_surface["has_required_route"][
                "/api/agent-trace/task-runs/{task_id}/process-timeline"
            ],
            "experience_ledger": route_surface["has_required_route"][
                "/api/agent-trace/experience-ledger"
            ],
            "review_queue": route_surface["has_required_route"]["/api/agent-trace/review-queue"],
            "promotion_gate": route_surface["has_required_route"][
                "/api/agent-trace/review-queue/promotions/apply"
            ],
            "checkpoint_resume": (
                route_surface["has_required_route"]["/api/agent-trace/checkpoints"]
                and route_surface["has_required_route"]["/api/agent-trace/resume-proposals"]
            ),
            "loop_review": route_surface["has_required_route"]["/api/loops/{run_id}/review"],
            "loop_replay": (
                route_surface["has_required_route"]["/api/loops/{run_id}/replay-case"]
                and route_surface["has_required_route"]["/api/loops/{run_id}/replay-evaluation"]
            ),
            "loop_resume": route_surface["has_required_route"][
                "/api/loops/{run_id}/resume-proposal"
            ],
        },
        "error": "",
    }


def _automation_surface_info(request: Request | None) -> dict[str, Any]:
    required_routes = {
        "/api/browser/system-info": ["GET"],
        "/api/browser/session/status": ["GET"],
        "/api/browser/session/health": ["GET"],
        "/api/browser/session/ensure": ["POST"],
        "/api/browser/session/viewport": ["POST"],
        "/api/browser/session/reset": ["POST"],
        "/api/browser/navigate": ["POST"],
        "/api/browser/action": ["POST"],
        "/api/browser/screenshot/base64": ["GET"],
        "/api/browser/page-info": ["GET"],
        "/api/browser/action-log": ["GET"],
        "/api/browser/session/replay-case": ["GET"],
        "/api/browser/session/replay-case/queue": ["POST"],
        "/api/browser/relay/status": ["GET"],
        "/api/browser/relay/command": ["POST"],
        "/api/browser/relay/result": ["POST"],
        "/api/browser-artifacts/{filename}": ["GET"],
        "/api/computer/status": ["GET"],
        "/api/computer/activity": ["GET"],
        "/api/computer/activity/replay-case": ["GET"],
        "/api/computer/activity/replay-case/queue": ["POST"],
        "/api/computer/screenshot": ["POST"],
        "/api/computer/actions/preview": ["POST"],
        "/api/computer/actions/plan": ["POST"],
        "/api/computer/actions/ground": ["POST"],
        "/api/computer/actions/vision": ["POST"],
        "/api/computer/actions/execute": ["POST"],
        "/api/computer/lease/release": ["POST"],
        "/api/computer/uia/status": ["GET"],
        "/api/computer/uia/tree": ["GET"],
        "/api/computer/uia/find": ["GET"],
    }
    route_surface = _route_surface_info(request, required_routes)
    method_contracts = _automation_method_contracts()
    missing_methods = [
        {
            "method": row["method"],
            "reason": row["reason"],
        }
        for row in method_contracts
        if not row["present"]
    ]
    ready = route_surface["required_routes_present"] and not missing_methods
    return {
        "schema": "echo.automation_surface_self_check.v1",
        "ready": ready,
        "route_count": route_surface["route_count"],
        "required_routes": list(required_routes),
        "missing_required_routes": route_surface["missing_required_routes"],
        "route_methods": route_surface["route_methods"],
        "missing_route_methods": route_surface["missing_route_methods"],
        "method_contracts": method_contracts,
        "missing_methods": missing_methods,
        "capabilities": {
            "browser_session_lifecycle": (
                route_surface["has_required_route"]["/api/browser/session/status"]
                and route_surface["has_required_route"]["/api/browser/session/ensure"]
                and route_surface["has_required_route"]["/api/browser/session/reset"]
            ),
            "browser_health": route_surface["has_required_route"]["/api/browser/session/health"],
            "browser_navigation": (
                route_surface["has_required_route"]["/api/browser/navigate"]
                and route_surface["has_required_route"]["/api/browser/action"]
            ),
            "browser_screenshot_evidence": (
                route_surface["has_required_route"]["/api/browser/screenshot/base64"]
                and route_surface["has_required_route"]["/api/browser-artifacts/{filename}"]
            ),
            "browser_replay_queue": (
                route_surface["has_required_route"]["/api/browser/session/replay-case"]
                and route_surface["has_required_route"]["/api/browser/session/replay-case/queue"]
            ),
            "browser_relay": (
                route_surface["has_required_route"]["/api/browser/relay/status"]
                and route_surface["has_required_route"]["/api/browser/relay/command"]
                and route_surface["has_required_route"]["/api/browser/relay/result"]
            ),
            "computer_preview_execute": (
                route_surface["has_required_route"]["/api/computer/actions/preview"]
                and route_surface["has_required_route"]["/api/computer/actions/execute"]
            ),
            "computer_grounding": (
                route_surface["has_required_route"]["/api/computer/actions/plan"]
                and route_surface["has_required_route"]["/api/computer/actions/ground"]
                and route_surface["has_required_route"]["/api/computer/actions/vision"]
            ),
            "computer_activity_replay": (
                route_surface["has_required_route"]["/api/computer/activity/replay-case"]
                and route_surface["has_required_route"]["/api/computer/activity/replay-case/queue"]
            ),
            "computer_uia": (
                route_surface["has_required_route"]["/api/computer/uia/status"]
                and route_surface["has_required_route"]["/api/computer/uia/tree"]
                and route_surface["has_required_route"]["/api/computer/uia/find"]
            ),
            "computer_lease": route_surface["has_required_route"]["/api/computer/lease/release"],
            "pixel_replay_gate": _contract_present(
                method_contracts,
                "browser_pixel.browser_pixel_replay_gate_case",
            ),
        },
        "error": "",
    }
