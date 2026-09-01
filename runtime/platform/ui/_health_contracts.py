"""Private contract-checking helpers for the health router.

Pure structural split of ``health_router``: route-surface inspection and
class/module/model contract checks. No logic changes.
"""

from __future__ import annotations

import contextlib
import inspect
from typing import Any

from fastapi import Request


def _iter_app_routes(app: Any) -> list[Any]:
    """Every route reachable from the app, nested includes included.

    starlette >=1.3 / fastapi >=0.139 wrap included routers in entries
    that expose children via ``original_router`` (or ``app`` for mounts)
    instead of flattening them — a plain ``app.routes`` scan then sees a
    couple dozen wrappers and every surface check reports its routes
    missing.
    """
    stack = list(getattr(app, "routes", []) or [])
    seen: set[int] = set()
    collected: list[Any] = []
    while stack:
        route = stack.pop()
        if id(route) in seen:
            continue
        seen.add(id(route))
        collected.append(route)
        stack.extend(getattr(route, "routes", []) or [])
        for container_attr in ("original_router", "app"):
            container = getattr(route, container_attr, None)
            if container is not None:
                stack.extend(getattr(container, "routes", []) or [])
    return collected


def _run_evidence_method_contracts() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    checks.extend(
        _class_method_contracts(
            "AgentTraceStore",
            "runtime.memory.diagnostics.trace_store",
            "AgentTraceStore",
            [
                "stats",
                "events",
                "task_runs",
                "task_run",
                "task_run_review",
                "task_run_replay_case",
                "evaluate_task_run_replay_case",
                "task_run_replay_cases",
                "evaluate_task_run_replay_cases",
                "replay_gate",
                "replay_gate_for_task_ids",
                "approvals",
                "checkpoints",
                "latest_checkpoint",
                "resume_proposal",
                "resume_proposals",
                "resume_requests",
            ],
        )
    )
    checks.extend(
        _class_method_contracts(
            "ExperienceLedger",
            "runtime.memory.learning.experience_ledger",
            "ExperienceLedger",
            [
                "add_from_task_run_review",
                "records",
                "records_for_task",
                "weekly_summary",
                "quality_summary",
            ],
        )
    )
    checks.extend(
        _class_method_contracts(
            "ReviewQueue",
            "runtime.memory.learning.review_queue",
            "ReviewQueue",
            [
                "add_from_task_run_review",
                "items",
                "summary",
                "decide",
            ],
        )
    )
    checks.extend(
        _class_method_contracts(
            "PromotionApplier",
            "runtime.memory.learning.promotion_applier",
            "PromotionApplier",
            [
                "plan",
                "apply",
                "audit",
                "audit_summary",
            ],
        )
    )
    checks.extend(
        _module_function_contracts(
            "process_timeline",
            "runtime.memory.runtime_state.process_timeline",
            ["build_task_run_process_timeline"],
        )
    )
    checks.extend(
        _module_function_contracts(
            "loop_replay",
            "runtime.execution.loops.replay",
            [
                "build_loop_run_replay",
                "build_loop_run_replay_case",
                "evaluate_loop_run_replay_case",
            ],
        )
    )
    checks.extend(
        _module_function_contracts(
            "loop_recovery",
            "runtime.execution.loops.recovery",
            ["build_loop_run_resume_proposal"],
        )
    )
    return checks


def _automation_method_contracts() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    checks.extend(
        _class_method_contracts(
            "BrowserSessionCenter",
            "runtime.platform.runtime_policy.browser_sessions",
            "BrowserSessionCenter",
            [
                "ensure",
                "get",
                "record_action",
                "health_report",
                "snapshot",
                "list_snapshots",
            ],
        )
    )
    checks.extend(
        _module_function_contracts(
            "browser_replay",
            "runtime.safety.replay.browser_desktop_replay",
            [
                "browser_session_replay_identity",
                "computer_activity_replay_identity",
            ],
        )
    )
    checks.extend(
        _module_function_contracts(
            "browser_pixel",
            "runtime.safety.replay.browser_pixel_assertions",
            [
                "assert_screenshot_pixels",
                "compare_screenshot_pixels",
                "browser_pixel_replay_gate_case",
            ],
        )
    )
    checks.extend(
        _module_function_contracts(
            "computer_skills",
            "runtime.execution.suckers.computer_skills",
            [
                "_screen_capture",
                "_screen_info",
                "_mouse_click",
                "_mouse_move",
                "_keyboard_type",
                "_keyboard_press",
                "register_computer_skills",
            ],
        )
    )
    checks.extend(
        _module_function_contracts(
            "computer_uia",
            "runtime.execution.suckers.computer_uia_skills",
            [
                "_check_uia",
                "uia_replay_assertion_for_action",
                "register_computer_uia_skills",
            ],
        )
    )
    return checks


def _contract_present(
    contracts: list[dict[str, Any]],
    method: str,
) -> bool:
    for row in contracts:
        if row.get("method") == method:
            return bool(row.get("present"))
    return False


def _class_method_contracts(
    label: str,
    module_name: str,
    class_name: str,
    method_names: list[str],
) -> list[dict[str, Any]]:
    try:
        module = __import__(module_name, fromlist=[class_name])
        cls = getattr(module, class_name)
    except Exception as exc:  # noqa: BLE001
        return [
            {
                "method": f"{label}.{method}",
                "present": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }
            for method in method_names
        ]
    return [
        {
            "method": f"{label}.{method}",
            "present": callable(getattr(cls, method, None)),
            "reason": "" if callable(getattr(cls, method, None)) else "missing",
        }
        for method in method_names
    ]


def _module_function_contracts(
    label: str,
    module_name: str,
    function_names: list[str],
) -> list[dict[str, Any]]:
    try:
        module = __import__(module_name, fromlist=function_names)
    except Exception as exc:  # noqa: BLE001
        return [
            {
                "method": f"{label}.{function}",
                "present": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }
            for function in function_names
        ]
    return [
        {
            "method": f"{label}.{function}",
            "present": callable(getattr(module, function, None)),
            "reason": "" if callable(getattr(module, function, None)) else "missing",
        }
        for function in function_names
    ]


def _route_surface_info(
    request: Request | None,
    required_routes: dict[str, list[str]],
) -> dict[str, Any]:
    app = getattr(request, "app", None) if request is not None else None
    routes = _iter_app_routes(app)
    route_methods: dict[str, list[str]] = {}
    for route in routes:
        path = str(getattr(route, "path", "") or "")
        if not path:
            continue
        methods = sorted(
            str(method)
            for method in (getattr(route, "methods", None) or [])
            if str(method) not in {"HEAD", "OPTIONS"}
        )
        route_methods[path] = methods
    missing_routes = [path for path in required_routes if path not in route_methods]
    missing_route_methods = [
        {
            "path": path,
            "missing_methods": [
                method for method in methods if method not in route_methods.get(path, [])
            ],
        }
        for path, methods in required_routes.items()
        if path in route_methods
        and any(method not in route_methods.get(path, []) for method in methods)
    ]
    return {
        "route_count": len(route_methods),
        "route_methods": {
            path: route_methods.get(path, []) for path in required_routes if path in route_methods
        },
        "missing_required_routes": missing_routes,
        "missing_route_methods": missing_route_methods,
        "required_routes_present": not missing_routes and not missing_route_methods,
        "has_required_route": {
            path: path in route_methods
            and not any(row["path"] == path for row in missing_route_methods)
            for path in required_routes
        },
    }


def _orchestration_model_contracts() -> list[dict[str, Any]]:
    try:
        from runtime.execution.parallel_agents.models import (
            BatchPlan,
            BatchRecoverySnapshot,
            BatchResult,
            BatchStreamEvent,
            OrchestratorStatus,
            WorkContract,
        )
    except Exception as exc:  # noqa: BLE001
        return [
            {
                "model": "parallel_agents.models",
                "required_fields": [],
                "present_fields": [],
                "missing_fields": ["import"],
                "error": f"{type(exc).__name__}: {exc}",
            }
        ]

    contracts = [
        (
            "BatchResult",
            BatchResult,
            [
                "plan",
                "event_log",
                "completion_receipt",
                "file_write_observability",
            ],
        ),
        (
            "BatchRecoverySnapshot",
            BatchRecoverySnapshot,
            [
                "schema",
                "dag",
                "plan",
                "event_sequence",
                "recovery_hints",
                "completion_receipt",
                "file_write_observability",
                "safety",
            ],
        ),
        (
            "BatchStreamEvent",
            BatchStreamEvent,
            [
                "type",
                "batch_id",
                "sequence",
                "created_at",
                "payload",
                "artifact_paths",
            ],
        ),
        (
            "BatchPlan",
            BatchPlan,
            [
                "phases",
                "contracts",
                "validation_issues",
                "validation_warnings",
            ],
        ),
        (
            "WorkContract",
            WorkContract,
            [
                "owned_scope",
                "forbidden_scope",
                "write_paths",
                "success_criteria",
            ],
        ),
        (
            "OrchestratorStatus",
            OrchestratorStatus,
            [
                "active_count",
                "pending_count",
                "completed_count",
                "failed_count",
                "cancelled_count",
                "max_concurrency",
                "batches",
            ],
        ),
    ]
    return [
        _model_contract_summary(name, model, required_fields)
        for name, model, required_fields in contracts
    ]


def _model_contract_summary(
    name: str,
    model: Any,
    required_fields: list[str],
) -> dict[str, Any]:
    raw_fields = getattr(model, "model_fields", None)
    if raw_fields is None:
        raw_fields = getattr(model, "__fields__", {})
    present_fields = set(str(key) for key in raw_fields)
    aliases = {
        str(getattr(field, "alias", "") or "")
        for field in raw_fields.values()
        if getattr(field, "alias", None)
    }
    all_fields = present_fields | aliases
    missing = [field for field in required_fields if field not in all_fields]
    return {
        "model": name,
        "required_fields": required_fields,
        "present_fields": sorted(all_fields),
        "missing_fields": missing,
        "error": "",
    }


def _orchestrator_method_contracts() -> list[dict[str, Any]]:
    try:
        from runtime.execution.parallel_agents import ParallelAgentOrchestrator
    except Exception as exc:  # noqa: BLE001
        return [
            {
                "method": "ParallelAgentOrchestrator",
                "present": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }
        ]

    required = [
        "dispatch",
        "split",
        "status",
        "get_batch",
        "recovery_snapshot",
        "subscribe",
        "cancel_task",
        "cancel_all",
        "get_batch_owner",
        "get_task_owner",
        "cancel_all_for_owner",
    ]
    out = [
        {
            "method": method,
            "present": callable(getattr(ParallelAgentOrchestrator, method, None)),
            "reason": (
                "" if callable(getattr(ParallelAgentOrchestrator, method, None)) else "missing"
            ),
        }
        for method in required
    ]
    subscribe = getattr(ParallelAgentOrchestrator, "subscribe", None)
    has_after_sequence = False
    if callable(subscribe):
        with contextlib.suppress(TypeError, ValueError):
            has_after_sequence = "after_sequence" in inspect.signature(subscribe).parameters
    out.append(
        {
            "method": "subscribe.after_sequence",
            "present": has_after_sequence,
            "reason": "" if has_after_sequence else "missing parameter",
        }
    )
    return out


def _contract_has_field(
    contracts: list[dict[str, Any]],
    model: str,
    field: str,
) -> bool:
    for row in contracts:
        if row.get("model") != model:
            continue
        return field in row.get("present_fields", []) and field not in row.get(
            "missing_fields",
            [],
        )
    return False
