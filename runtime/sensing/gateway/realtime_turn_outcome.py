"""Turn outcome inspection for the realtime runtime.

Split out of ``realtime_cerebrum.py``: decide whether a finished turn's
code changes are verified (test/lint/build evidence present and
matching), and build the structured metadata that failed / successful
turns contribute to the evolution proposal ledger.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import TYPE_CHECKING, Any, Literal, cast

from runtime.execution.tool_engine import (
    normalize_tool_lifecycle_event,
    tool_lifecycle_event_to_trace_payload,
)
from runtime.platform.models import ParsedIntent
from runtime.protocol import (
    ErrorItem,
    FileChangeItem,
    ItemStatus,
    Turn,
    TurnParams,
    VerificationItem,
)
from runtime.safety.auth.scope import TenantScope, tenant_scoped_path
from runtime.sensing.gateway.realtime_turn_input import (
    _agent_id_from_params,
    _preview_text,
    _turn_mode,
)

if TYPE_CHECKING:
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

_logger = logging.getLogger(__name__)

_CODE_CHANGE_SUFFIXES = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".kts",
        ".swift",
        ".cs",
        ".cpp",
        ".cc",
        ".cxx",
        ".c",
        ".h",
        ".hpp",
        ".rb",
        ".php",
        ".sh",
        ".ps1",
        ".sql",
        ".vue",
        ".svelte",
    }
)

_CODE_CHANGE_FILENAMES = frozenset(
    {
        "package.json",
        "pnpm-lock.yaml",
        "package-lock.json",
        "yarn.lock",
        "pyproject.toml",
        "pytest.ini",
        "ruff.toml",
        "tsconfig.json",
        "vite.config.ts",
        "vite.config.js",
        "next.config.js",
        "next.config.mjs",
        "go.mod",
        "go.sum",
        "cargo.toml",
        "cargo.lock",
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
    }
)


def _is_code_change_path(path: str) -> bool:
    normalized = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if normalized in _CODE_CHANGE_FILENAMES:
        return True
    _, ext = os.path.splitext(normalized)
    return ext.lower() in _CODE_CHANGE_SUFFIXES


def _file_change_items(turn: Turn) -> list[FileChangeItem]:
    return [item for item in turn.items if isinstance(item, FileChangeItem)]


def _verification_items(turn: Turn) -> list[VerificationItem]:
    return [item for item in turn.items if isinstance(item, VerificationItem)]


def _code_change_paths(turn: Turn) -> list[str]:
    paths: list[str] = []
    for item in _file_change_items(turn):
        for change in item.changes:
            if _is_code_change_path(change.path) and change.path not in paths:
                paths.append(change.path)
    return paths


def _verification_plan_for_code_paths(
    paths: list[str],
    intent: ParsedIntent | None,
) -> dict[str, Any]:
    """Build targeted verifier commands for code changes.

    The realtime failure path needs concrete next commands, not a generic
    "run tests" nudge. This reuses the post-write regression matrix so
    executor diagnostics, failed-turn metadata, and operator views converge
    on one verifier selection policy.
    """

    commands: list[dict[str, Any]] = []
    seen: set[str] = set()
    cwd = _intent_cwd(intent)
    for path in paths:
        try:
            from runtime.safety.hooks.tool_edge_hooks import regression_matrix_for_path

            matrix = regression_matrix_for_path(path, workspace_path=cwd)
        except Exception:  # noqa: BLE001 - verification hints must never fail turns
            continue
        for check in matrix.checks:
            if check.command in seen:
                continue
            seen.add(check.command)
            commands.append(
                {
                    "kind": check.kind,
                    "command": check.command,
                    "reason": check.reason,
                    "priority": check.priority,
                    "target": matrix.target,
                }
            )
    return {
        "schema": "echo.verification_plan.v1",
        "workspace": cwd,
        "targets": list(paths),
        "commands": commands,
    }


def _verification_plan_stdout_tail(plan: dict[str, Any]) -> str:
    commands = plan.get("commands") if isinstance(plan, dict) else None
    if not isinstance(commands, list) or not commands:
        return (
            "Run an appropriate test, lint, typecheck, or build command and retry the final answer."
        )
    lines = ["Recommended verification commands:"]
    for command in commands[:5]:
        if not isinstance(command, dict):
            continue
        kind = str(command.get("kind") or "check")
        cmd = str(command.get("command") or "").strip()
        reason = str(command.get("reason") or "").strip()
        if not cmd:
            continue
        suffix = f" :: {reason}" if reason else ""
        lines.append(f"- [{kind}] {cmd}{suffix}")
    if len(lines) == 1:
        return (
            "Run an appropriate test, lint, typecheck, or build command and retry the final answer."
        )
    return "\n".join(lines)


def _intent_cwd(intent: ParsedIntent | None) -> str:
    context = intent.user_context if intent is not None else {}
    if isinstance(context, dict):
        cwd = context.get("cwd") or context.get("workspace_path")
        if isinstance(cwd, str) and cwd.strip():
            return cwd.strip()
    return os.getcwd()


def _file_change_item_ids(turn: Turn) -> list[str]:
    return [item.id for item in _file_change_items(turn)]


def _normalized_path_key(path: str) -> str:
    return path.replace("\\", "/").lower()


def _verification_matches_code_changes(
    item: VerificationItem,
    *,
    code_paths: list[str],
    change_item_ids: list[str],
) -> bool:
    if not code_paths:
        return False

    code_path_keys = {_normalized_path_key(path) for path in code_paths}
    change_id_set = set(change_item_ids)
    related_ids = set(item.related_change_item_ids)
    if related_ids:
        return bool(related_ids & change_id_set)

    related_path_keys = {
        _normalized_path_key(path)
        for path in item.related_files
        if isinstance(path, str) and path.strip()
    }
    if related_path_keys:
        return bool(related_path_keys & code_path_keys)

    # No explicit relationship means a completed verification command is
    # a turn-level check. Once relationship metadata exists, it must match.
    return True


def _turn_has_passing_code_verification(turn: Turn) -> bool:
    code_paths = _code_change_paths(turn)
    if not code_paths:
        return True
    change_item_ids = _file_change_item_ids(turn)
    return any(
        item.status == ItemStatus.COMPLETED
        and _verification_matches_code_changes(
            item,
            code_paths=code_paths,
            change_item_ids=change_item_ids,
        )
        for item in _verification_items(turn)
    )


def _turn_has_failed_code_verification(turn: Turn) -> bool:
    code_paths = _code_change_paths(turn)
    change_item_ids = _file_change_item_ids(turn)
    for item in _verification_items(turn):
        if item.status != ItemStatus.FAILED:
            continue
        if _verification_matches_code_changes(
            item,
            code_paths=code_paths,
            change_item_ids=change_item_ids,
        ):
            return True
        if not code_paths and any(_is_code_change_path(path) for path in item.related_files):
            return True
    return False


# Environment-blocked verification markers: the verifier itself could not
# run because the sandbox blocks the toolchain at a level the agent cannot
# self-repair — no network, corepack unable to download itself, DNS/SSL
# failures, pnpm's strict node_modules layout breaking direct `node` calls.
# Deliberately EXCLUDES "command not found" / "npx: not found" / "winerror
# 2" / "no module named": a missing CLI is something the agent can usually
# fix by selecting an installed equivalent (the failure classifier routes
# those to environment_missing_tool + repair), so they keep hard-failing.
_ENV_BLOCKED_VERIFICATION_MARKERS = (
    # corepack / package-manager self-download
    "corepack is about to download",
    "error when performing the request",
    "err_module_not_found",
    "cannot find package",
    # network / DNS / proxy
    "network is unreachable",
    "getaddrinfo failed",
    "temporary failure in name resolution",
    "name or service not known",
    "failed to connect",
    "unable to access",
    "socket hang up",
    "proxyconnect",
    "connection refused",
    # TLS / cert
    "unexpected_eof_while_reading",
    "ssl: unexpected_eof",
    "certificate verify failed",
    "self-signed certificate",
    # fd exhaustion
    "emfile",
)


def _verification_item_is_environment_blocked(item: VerificationItem) -> bool:
    """True when a failed verification item failed because the environment
    (network / package-manager / sandbox) blocked the verifier at a level
    the agent cannot self-repair, not because the code under test is
    broken."""
    haystack = "\n".join(
        str(part)
        for part in (item.summary, item.stdout_tail, item.stderr_tail)
        if isinstance(part, str) and part.strip()
    )
    lowered = haystack.lower()
    return any(marker in lowered for marker in _ENV_BLOCKED_VERIFICATION_MARKERS)


def _command_execution_environment_blocked(item: Any) -> bool:
    """True when a failed shell command's output shows an environment-level
    blockage (network / package-manager self-download / TLS) rather than a
    code error. Used to catch verification attempts the agent ran via
    ``exec_shell`` that never produced a ``VerificationItem``."""
    output = str(getattr(item, "aggregated_output", "") or "")
    lowered = output.lower()
    return any(marker in lowered for marker in _ENV_BLOCKED_VERIFICATION_MARKERS)


def _turn_verification_environment_blocked(turn: Turn) -> bool:
    """True when every failed verification attempt for this turn is
    environment-blocked (no network / package-manager self-download / TLS),
    so the turn can degrade to a manual-confirmation state instead of a
    hard failure.

    Covers both ``VerificationItem`` failures AND failed shell commands the
    agent ran as verification attempts (their ``aggregated_output`` shows
    the environment blockage). A turn with at least one real code failure
    (test assertion, syntax error) is NOT treated as blocked.
    """
    failed_verifications = [
        item for item in _verification_items(turn) if item.status == ItemStatus.FAILED
    ]
    if failed_verifications:
        # At least one recorded verification failure is a real code error.
        if not all(
            _verification_item_is_environment_blocked(item) for item in failed_verifications
        ):
            return False
        # All recorded verification items are environment-blocked — only
        # degrade when no shell command carried a real code failure either.
        return not _turn_has_real_code_failure(turn)

    # No recorded VerificationItem failures — inspect failed shell commands
    # the agent ran as verification attempts.
    failed_commands = [
        item
        for item in turn.items
        if getattr(item, "type", None) == "commandExecution"
        and getattr(item, "status", None) == ItemStatus.FAILED
    ]
    if not failed_commands:
        return False
    if any(_command_is_real_code_failure(item) for item in failed_commands):
        return False
    return all(_command_execution_environment_blocked(item) for item in failed_commands)


def _command_is_real_code_failure(item: Any) -> bool:
    """True when a failed shell command's output shows a real code problem
    (test assertion, syntax error, lint violation, type error) rather than
    an environment blockage. Used to keep hard-failing turns whose
    verifier genuinely found broken code."""
    code_failure_markers = (
        "assert",
        "syntaxerror",
        "invalid syntax",
        "e999",
        "failed tests",
        "failures =",
        "error: cannot find",
        "type error",
        "tsc",
        "mypy",
    )
    output = str(getattr(item, "aggregated_output", "") or "").lower()
    return any(marker in output for marker in code_failure_markers)


def _turn_has_real_code_failure(turn: Turn) -> bool:
    """True when the turn carries any failed shell command whose output is a
    real code problem (test assertion, syntax error, lint violation) rather
    than an environment blockage."""
    return any(
        _command_is_real_code_failure(item)
        for item in turn.items
        if getattr(item, "type", None) == "commandExecution"
        and getattr(item, "status", None) == ItemStatus.FAILED
    )


def _turn_has_unverified_code_changes(turn: Turn) -> bool:
    return bool(_code_change_paths(turn)) and not _turn_has_passing_code_verification(turn)


def _turn_model(turn: Turn) -> str | None:
    model = getattr(turn.params, "model", None) if getattr(turn, "params", None) else None
    return model if isinstance(model, str) and model.strip() else None


def _turn_execution_engine(turn: Turn) -> str:
    value = str(getattr(turn, "execution_engine", None) or "").strip().lower()
    return value if value in {"codex", "echo"} else "echo"


def _goal_fingerprint(goal: str) -> str:
    normalized = " ".join(goal.strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _turn_goal_text(turn: Turn, intent: ParsedIntent | None) -> str:
    if intent is not None:
        goal = getattr(intent, "normalized_goal", None)
        if isinstance(goal, str) and goal.strip():
            return goal.strip()
    params = getattr(turn, "params", None)
    if params is not None:
        parts: list[str] = []
        for block in getattr(params, "input", []) or []:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        if parts:
            return "\n".join(parts).strip()
    return ""


def _turn_failure_text(turn: Turn) -> str:
    if isinstance(turn.error, dict):
        message = turn.error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    for item in reversed(turn.items):
        if isinstance(item, ErrorItem) and item.message.strip():
            return item.message.strip()
        if isinstance(item, VerificationItem) and item.status == ItemStatus.FAILED:
            for candidate in (item.summary, item.stderr_tail, item.stdout_tail):
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
    return "turn failed"


def _turn_item_counts(turn: Turn) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in turn.items:
        item_type = getattr(item.type, "value", str(item.type))
        counts[item_type] = counts.get(item_type, 0) + 1
    return counts


def _classify_verification_failure(item: VerificationItem) -> dict[str, Any]:
    text = "\n".join(
        str(part)
        for part in (item.summary, item.stderr_tail, item.stdout_tail)
        if isinstance(part, str) and part.strip()
    )
    lowered = text.lower()
    command = (item.command or "").lower()
    kind = item.kind

    if any(
        marker in lowered
        for marker in (
            "command not found",
            "not recognized as an internal or external command",
            "could not determine executable",
            "no module named",
            "npx: not found",
            "[winerror 2]",
        )
    ):
        diagnosis = {
            "category": "environment_missing_tool",
            "action": "install_or_select_available_verifier",
            "retryable": True,
            "evidence": _diagnosis_evidence(text),
        }
        diagnosis["repair_route"] = _repair_route_for_diagnosis(diagnosis)
        return diagnosis
    if "timeout" in lowered or "timed out" in lowered:
        diagnosis = {
            "category": "verification_timeout",
            "action": "rerun_targeted_or_increase_timeout",
            "retryable": True,
            "evidence": _diagnosis_evidence(text),
        }
        diagnosis["repair_route"] = _repair_route_for_diagnosis(diagnosis)
        return diagnosis
    if kind == "lint" or "ruff diagnostics" in lowered or "eslint" in command:
        if "syntaxerror" in lowered or "invalid syntax" in lowered or "e999" in lowered:
            category = "syntax_error"
            action = "fix_syntax"
        else:
            category = "lint_failure"
            action = "fix_lint"
        diagnosis = {
            "category": category,
            "action": action,
            "retryable": False,
            "evidence": _diagnosis_evidence(text),
        }
        diagnosis["repair_route"] = _repair_route_for_diagnosis(diagnosis)
        return diagnosis
    if kind == "typecheck" or "tsc" in command or "mypy" in command or "pyright" in command:
        diagnosis = {
            "category": "type_error",
            "action": "fix_types",
            "retryable": False,
            "evidence": _diagnosis_evidence(text),
        }
        diagnosis["repair_route"] = _repair_route_for_diagnosis(diagnosis)
        return diagnosis
    if kind == "build" or "build" in command:
        diagnosis = {
            "category": "build_failure",
            "action": "fix_build",
            "retryable": False,
            "evidence": _diagnosis_evidence(text),
        }
        diagnosis["repair_route"] = _repair_route_for_diagnosis(diagnosis)
        return diagnosis
    if kind == "test" or any(
        marker in command for marker in ("pytest", "vitest", "jest", "playwright", "test")
    ):
        diagnosis = {
            "category": "test_failure",
            "action": "fix_code_or_test_expectation",
            "retryable": False,
            "evidence": _diagnosis_evidence(text),
        }
        diagnosis["repair_route"] = _repair_route_for_diagnosis(diagnosis)
        return diagnosis
    diagnosis = {
        "category": "verification_failure",
        "action": "inspect_verification_output",
        "retryable": False,
        "evidence": _diagnosis_evidence(text),
    }
    diagnosis["repair_route"] = _repair_route_for_diagnosis(diagnosis)
    return diagnosis


def _repair_route_for_diagnosis(diagnosis: dict[str, Any]) -> dict[str, Any]:
    category = str(diagnosis.get("category") or "verification_failure")
    routes: dict[str, dict[str, Any]] = {
        "environment_missing_tool": {
            "route": "environment_repair",
            "strategy": "restore_verifier_toolchain",
            "next_actions": [
                "Confirm the verifier exists in the project environment.",
                "Use an installed equivalent before changing application code.",
            ],
        },
        "verification_timeout": {
            "route": "verification_scope_reduction",
            "strategy": "rerun_targeted_checks",
            "next_actions": [
                "Reduce to the narrowest related test command.",
                "Retry before editing unrelated code.",
            ],
        },
        "syntax_error": {
            "route": "syntax_patch",
            "strategy": "fix_parse_error_first",
            "next_actions": [
                "Fix the syntax error at the reported location.",
                "Run lint before broader tests.",
            ],
        },
        "lint_failure": {
            "route": "lint_patch",
            "strategy": "apply_style_or_static_fix",
            "next_actions": [
                "Fix the reported lint rule.",
                "Rerun the same lint command.",
            ],
        },
        "type_error": {
            "route": "type_repair",
            "strategy": "repair_type_contract",
            "next_actions": [
                "Inspect the failing symbol and type contract.",
                "Rerun the typecheck before tests.",
            ],
        },
        "build_failure": {
            "route": "build_repair",
            "strategy": "repair_bundle_or_compile_contract",
            "next_actions": [
                "Fix the first build error.",
                "Rerun build before broader validation.",
            ],
        },
        "test_failure": {
            "route": "test_driven_repair",
            "strategy": "reproduce_and_patch_behavior",
            "next_actions": [
                "Rerun the failing test command.",
                "Patch code or update invalid expectations with evidence.",
            ],
        },
    }
    route = routes.get(
        category,
        {
            "route": "verification_output_triage",
            "strategy": "inspect_output_then_choose_repair",
            "next_actions": [
                "Read the verifier output.",
                "Choose the narrowest repair path before broad tests.",
            ],
        },
    )
    return {
        **route,
        "retryable": bool(diagnosis.get("retryable")),
    }


def _diagnosis_evidence(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:240]
    return ""


def _repair_routes_from_failed_verifications(
    failed_verifications: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in failed_verifications:
        raw_diagnosis = item.get("diagnosis")
        diagnosis: dict[str, Any] = raw_diagnosis if isinstance(raw_diagnosis, dict) else {}
        raw_route = diagnosis.get("repair_route")
        route: dict[str, Any] = raw_route if isinstance(raw_route, dict) else {}
        route_id = str(route.get("route") or "").strip()
        if not route_id or route_id in seen:
            continue
        seen.add(route_id)
        routes.append(route)
    return routes


def _failed_turn_metadata(
    turn: Turn,
    *,
    intent: ParsedIntent | None,
    failure_source: str,
) -> dict[str, Any]:
    verification_items = _verification_items(turn)
    failed_verifications = [
        {
            "command": item.command,
            "kind": item.kind,
            "summary": item.summary,
            "exit_code": item.exit_code,
            "related_files": list(item.related_files),
            "related_change_item_ids": list(item.related_change_item_ids),
            "diagnosis": _classify_verification_failure(item),
        }
        for item in verification_items
        if item.status == ItemStatus.FAILED
    ]
    repair_routes = _repair_routes_from_failed_verifications(failed_verifications)
    code_change_paths = _code_change_paths(turn)
    verification_plan = _verification_plan_for_code_paths(code_change_paths, intent)
    goal = _turn_goal_text(turn, intent)
    return {
        "turn_id": turn.id,
        "thread_id": turn.thread_id,
        "objective_id": turn.objective_id or turn.id,
        "engine": _turn_execution_engine(turn),
        "goal_fingerprint": _goal_fingerprint(goal),
        "failure_source": failure_source,
        "goal": goal,
        "error": _turn_failure_text(turn),
        "status": str(turn.status.value if hasattr(turn.status, "value") else turn.status),
        "item_counts": _turn_item_counts(turn),
        "code_change_paths": code_change_paths,
        "verification_count": len(verification_items),
        "failed_verifications": failed_verifications,
        "verification_plan": verification_plan,
        "primary_repair_route": (str(repair_routes[0].get("route") or "") if repair_routes else ""),
        "repair_routes": repair_routes,
        "has_code_changes": bool(code_change_paths),
    }


def _failed_turn_description(metadata: dict[str, Any]) -> str:
    goal = str(metadata.get("goal") or "").strip()
    error = str(metadata.get("error") or "").strip()
    source = str(metadata.get("failure_source") or "turn_failure").strip()
    goal_part = goal[:120] if goal else "(no goal)"
    error_part = error[:120] if error else "turn failed"
    return f"{source}: {error_part} | goal={goal_part}"


def _successful_turn_metadata(
    turn: Turn,
    *,
    intent: ParsedIntent | None,
) -> dict[str, Any]:
    goal = _turn_goal_text(turn, intent)
    return {
        "turn_id": turn.id,
        "thread_id": turn.thread_id,
        "objective_id": turn.objective_id or turn.id,
        "engine": _turn_execution_engine(turn),
        "goal_fingerprint": _goal_fingerprint(goal),
        "goal": goal,
        "status": str(turn.status.value if hasattr(turn.status, "value") else turn.status),
        "item_counts": _turn_item_counts(turn),
        "code_change_paths": _code_change_paths(turn),
        "verification_count": len(_verification_items(turn)),
        "has_code_changes": bool(_code_change_paths(turn)),
    }


def _successful_turn_description(metadata: dict[str, Any]) -> str:
    goal = str(metadata.get("goal") or "").strip()
    goal_part = goal[:120] if goal else "(no goal)"
    return f"turn_success | goal={goal_part}"


# ── Turn telemetry / evolution records ─────────────────────────
# Moved from ``CerebrumRuntime``; each function takes the runtime as
# its first argument and best-effort writes to the trace store or the
# evolution proposal ledger. Failures must never fail the turn.


def _record_task_run_started(
    runtime: CerebrumRuntime,
    turn: Turn,
    *,
    text: str,
    params: TurnParams,
) -> None:
    if runtime._trace_store is None:
        return
    try:
        runtime._trace_store.record_task_run_started(
            task_id=turn.id,
            thread_id=turn.thread_id,
            turn_id=turn.id,
            agent_id=_agent_id_from_params(params),
            title=_preview_text(text, limit=80),
            goal=text,
            mode=_turn_mode(params) or "react",
            scope=_turn_scope(turn),
            metadata={
                "topology_id": getattr(params, "topology_id", None),
                "model": getattr(params, "model", None),
                "planning_mode": bool(getattr(params, "planning_mode", False)),
            },
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning("trace store start failed for %s: %s", turn.id, exc)


def _record_task_run_finished(
    runtime: CerebrumRuntime,
    turn: Turn,
    *,
    recover_stale_lease: bool = False,
) -> None:
    status_value = str(getattr(turn.status, "value", turn.status) or "").lower()
    if status_value in {"in_progress", "in-progress", "pending", ""}:
        return
    status = {
        "completed": "completed",
        "failed": "failed",
        "paused": "paused",
        "interrupted": "interrupted",
        "cancelled": "cancelled",
        "canceled": "cancelled",
    }.get(status_value, "unknown")
    supervisor = getattr(runtime, "_task_supervisor", None)
    if supervisor is not None:
        try:
            from runtime.platform.process._task_supervisor_models import TaskRunStatus

            supervisor_status = "disconnected" if status == "interrupted" else status
            supervisor_status = supervisor_status if supervisor_status != "unknown" else "failed"
            supervisor_task_id = turn.task_id or turn.id
            metadata = {
                "objective_id": turn.objective_id,
                "react_task_id": turn.task_id,
                "turn_id": turn.id,
                "error": turn.error,
            }
            if supervisor.store.get(supervisor_task_id) is None:
                params = cast(TurnParams, turn.params)
                supervisor.start_task(
                    task_id=supervisor_task_id,
                    kind="realtime_objective",
                    owner_id=getattr(params, "owner_actor_id", None),
                    thread_id=turn.thread_id,
                    mode=_turn_mode(params) or "direct",
                    origin_task_id=turn.id,
                    metadata=metadata,
                    status=TaskRunStatus(supervisor_status),
                )
            elif recover_stale_lease:
                supervisor.recover_stale_turn(
                    supervisor_task_id,
                    supervisor_status,
                    expected_turn_id=turn.id,
                    reason=turn.outcome_reason or status_value,
                    checkpoint_id=turn.checkpoint_id,
                    metadata_patch=metadata,
                )
            else:
                supervisor.transition(
                    supervisor_task_id,
                    supervisor_status,
                    reason=turn.outcome_reason or status_value,
                    checkpoint_id=turn.checkpoint_id,
                    metadata_patch=metadata,
                )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "task supervisor finish failed for %s: %s", turn.task_id or turn.id, exc
            )
    if runtime._trace_store is None:
        return
    try:
        runtime._trace_store.record_task_run_finished(
            # Trace task-run identity is the transport attempt (turn id).
            # The durable ReAct/objective id is carried in payload metadata
            # and on Turn.taskId; mixing both ids in one event stream creates
            # a phantom never-finished run for every turn.
            task_id=turn.id,
            thread_id=turn.thread_id,
            turn_id=turn.id,
            agent_id=_agent_id_from_params(cast(TurnParams, turn.params)),
            status=status,
            reason=status_value,
            scope=_turn_scope(turn),
            metadata={
                "item_count": len(getattr(turn, "items", []) or []),
                "error": getattr(turn, "error", None),
            },
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning("trace store finish failed for %s: %s", turn.id, exc)


def _record_react_trace_event(runtime: CerebrumRuntime, turn: Turn, evt: dict[str, Any]) -> None:
    kind = str(evt.get("type") or "")
    if kind == "react_started":
        supervisor = getattr(runtime, "_task_supervisor", None)
        supervisor_task_id = str(evt.get("task_id") or "").strip()
        if supervisor is not None and supervisor_task_id:
            try:
                params = cast(TurnParams, turn.params)
                goal = next(
                    (
                        str(getattr(item, "text", "") or "").strip()
                        for item in turn.items
                        if str(getattr(item, "type", ""))
                        in {"userMessage", "ItemType.USER_MESSAGE"}
                        and str(getattr(item, "text", "") or "").strip()
                    ),
                    "",
                )
                supervisor.start_task(
                    task_id=supervisor_task_id,
                    kind="realtime_objective",
                    owner_id=getattr(params, "owner_actor_id", None),
                    thread_id=turn.thread_id,
                    title=_preview_text(goal, limit=80),
                    goal=goal,
                    mode=_turn_mode(params) or "react",
                    workspace_path=(turn.execution_workspace_path or getattr(params, "cwd", None)),
                    origin_task_id=turn.id,
                    metadata={
                        "objective_id": supervisor_task_id,
                        "turn_id": turn.id,
                        "agent_id": _agent_id_from_params(params),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "task supervisor react start failed for %s: %s",
                    supervisor_task_id,
                    exc,
                )
    if runtime._trace_store is None:
        return
    event_type = {
        "react_started": "TASK_RUN_STARTED",
        "tool_start": "TOOL_CALL_START",
        "tool_end": "TOOL_CALL_END",
        "tool_background": "TOOL_CALL_BACKGROUND",
        "react_completed": "REACT_COMPLETED",
        "react_cancelled": "REACT_CANCELLED",
        "react_paused": "TASK_RUN_PAUSED",
        "react_error": "REACT_ERROR",
        "visibility": "VISIBILITY_TRACE",
    }.get(kind)
    if event_type is None:
        return
    payload = dict(evt)
    if kind in {"tool_start", "tool_end", "tool_background"}:
        lifecycle_kind: Literal["tool_start", "tool_end"] = (
            "tool_start" if kind == "tool_start" else "tool_end"
        )
        normalized = normalize_tool_lifecycle_event(
            lifecycle_kind,
            payload,
            origin="react_compat",
        )
        payload = tool_lifecycle_event_to_trace_payload(normalized)
        payload["event_kind"] = kind
    elif "tool_name" in payload and "tool" not in payload:
        payload["tool"] = payload.get("tool_name")
    try:
        runtime._trace_store.record_event(
            event_type=event_type,
            payload=payload,
            thread_id=turn.thread_id,
            turn_id=turn.id,
            task_id=turn.id,
            agent_id=_agent_id_from_params(cast(TurnParams, turn.params)),
            item_id=str(evt.get("tool_call_id") or evt.get("item_id") or "") or None,
            scope=_turn_scope(turn),
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning("trace event %s failed for %s: %s", event_type, turn.id, exc)


def _record_failed_turn_proposal(
    runtime: CerebrumRuntime,
    turn: Turn,
    *,
    intent: ParsedIntent | None,
    failure_source: str,
) -> None:
    """Persist a failed turn as evolution fuel.

    This is deliberately best-effort and non-blocking from the
    user's perspective: learning infrastructure must never make a
    failed turn fail harder. The record is small but carries enough
    structured metadata for GEPA/canary pipelines to sample real
    failures instead of synthetic anecdotes.
    """

    try:
        from runtime.safety.evolution.proposal_ledger import ProposalLedger

        scope = _turn_scope(turn)
        ledger = ProposalLedger(tenant_scoped_path(runtime._proposal_ledger_path, scope))
        metadata = _failed_turn_metadata(
            turn,
            intent=intent,
            failure_source=failure_source,
        )
        ledger.propose(
            kind="turn_failure",
            description=_failed_turn_description(metadata),
            proposer="realtime_cerebrum",
            model=_turn_model(turn),
            metadata=metadata,
            scope=scope,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.debug("failed-turn proposal record skipped: %s", exc, exc_info=True)


def _record_successful_turn_example(
    runtime: CerebrumRuntime,
    turn: Turn,
    *,
    intent: ParsedIntent | None,
) -> None:
    """Persist a compact successful turn as positive evolution fuel."""

    try:
        from runtime.safety.evolution.proposal_ledger import ProposalLedger

        scope = _turn_scope(turn)
        metadata = _successful_turn_metadata(turn, intent=intent)
        ProposalLedger(tenant_scoped_path(runtime._proposal_ledger_path, scope)).propose(
            kind="turn_success",
            description=_successful_turn_description(metadata),
            proposer="realtime_cerebrum",
            model=_turn_model(turn),
            metadata=metadata,
            scope=scope,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.debug("successful-turn example record skipped: %s", exc, exc_info=True)


def _turn_scope(turn: Turn) -> TenantScope | None:
    params = getattr(turn, "params", None)
    tenant_id = str(getattr(params, "tenant_id", None) or "").strip()
    actor_id = str(getattr(params, "owner_actor_id", None) or "").strip()
    if not tenant_id or not actor_id:
        return None
    return TenantScope(tenant_id=tenant_id, actor_id=actor_id)
