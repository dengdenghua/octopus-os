"""Tool-result / observation shaping for the ReAct loop.

Extracted from ``react_execution.py``. Classifies finished beak steps
(verification kind, command text, effective success), surfaces structured
metadata on realtime ``tool_end`` events, renders background-task
bookkeeping text, builds the completion receipt, checks executor skill
availability, and decides whether a write is a scoped artifact write.
Leaf module: imports only from react_* leaf modules and platform layers —
never imports react_loop or react_execution.
"""

from __future__ import annotations

import json
from typing import Any

from runtime.core.cerebrum.completion_receipt import build_completion_receipt
from runtime.execution.tool_engine.tool_protocol import output_signals_error
from runtime.platform.models import Step

_VERIFICATION_TOOL_KINDS: dict[str, str] = {
    "run_tests": "test",
    "lint_check": "lint",
    "format_code": "lint",
}

_SCOPED_ARTIFACT_WRITE_TOOLS = frozenset(
    {
        "write_text_file",
        "append_text_file",
        "edit_text_file",
        "edit_file",
        "multi_edit_file",
    }
)


def _background_task_info_from_observation(observation: str | None) -> dict[str, Any] | None:
    """Extract a background shell snapshot from a rendered tool observation."""

    if not isinstance(observation, str) or not observation.strip():
        return None
    payload = observation.split("\n", 1)[1] if "\n" in observation else observation
    try:
        data = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    task_id = data.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return None
    if data.get("running") is True or data.get("status") == "running":
        return data
    return None


def _verification_kind_from_command(command: str) -> str | None:
    """Classify shell commands that are actually verification steps."""

    text = f" {command.lower()} "
    test_markers = (
        " pytest",
        " -m pytest",
        " unittest",
        " vitest",
        " jest",
        " playwright test",
        " npm test",
        " npm run test",
        " pnpm test",
        " pnpm run test",
        " yarn test",
        " cargo test",
        " go test",
        " dotnet test",
    )
    lint_markers = (
        " eslint",
        " ruff check",
        " flake8",
        " biome lint",
        " npm run lint",
        " pnpm lint",
        " pnpm run lint",
        " yarn lint",
    )
    typecheck_markers = (
        " tsc",
        " vue-tsc",
        " pyright",
        " mypy",
        " py_compile",
        " npm run typecheck",
        " pnpm typecheck",
        " pnpm run typecheck",
        " yarn typecheck",
    )
    build_markers = (
        " npm run build",
        " pnpm build",
        " pnpm run build",
        " yarn build",
        " cargo build",
        " go build",
        " dotnet build",
        " mvn package",
        " gradle build",
    )
    if any(marker in text for marker in test_markers):
        return "test"
    if any(marker in text for marker in lint_markers):
        return "lint"
    if any(marker in text for marker in typecheck_markers):
        return "typecheck"
    if any(marker in text for marker in build_markers):
        return "build"
    return None


def _command_from_tool_step(beak_step: Step, output: dict[str, Any]) -> str:
    action_args = getattr(getattr(beak_step, "action", None), "args", {}) or {}
    raw = action_args.get("command") or action_args.get("cmd")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, list):
        return " ".join(str(part) for part in raw)
    argv = output.get("argv")
    if isinstance(argv, list):
        return " ".join(str(part) for part in argv)
    return ""


def _tool_event_extras_from_beak_step(
    beak_step: Step | None,
    tool_name: str,
) -> dict[str, Any]:
    """Surface structured beak metadata on realtime tool_end events."""

    if beak_step is None:
        return {}
    result = getattr(beak_step, "result", None)
    output = getattr(result, "output", None)
    if not isinstance(output, dict):
        return {}

    extras: dict[str, Any] = {}
    evidence = _structured_tool_evidence(beak_step, tool_name, output)
    if evidence:
        extras["evidence"] = evidence
    effect_receipt = output.get("effect_receipt")
    if isinstance(effect_receipt, dict):
        effect_key = effect_receipt.get("effect_key")
        call_id = effect_receipt.get("call_id")
        state = effect_receipt.get("state")
        reason = effect_receipt.get("reason")
        fencing_token = effect_receipt.get("fencing_token")
        if (
            isinstance(effect_key, str)
            and effect_key
            and isinstance(call_id, str)
            and call_id
            and state == "indeterminate"
            and isinstance(reason, str)
        ):
            extras["effect_receipt"] = {
                "effect_key": effect_key,
                "call_id": call_id,
                "state": "indeterminate",
                "reason": reason,
                "fencing_token": (
                    fencing_token
                    if isinstance(fencing_token, int) and not isinstance(fencing_token, bool)
                    else 0
                ),
            }
    diff = output.get("diff_preview") or output.get("diff")
    if isinstance(diff, str) and diff.strip():
        extras["diff"] = diff

    command = _command_from_tool_step(beak_step, output)
    kind = _VERIFICATION_TOOL_KINDS.get(tool_name)
    if kind is None and tool_name in {"exec_shell", "shell_command", "bash"}:
        kind = _verification_kind_from_command(command)
    if kind is not None:
        stdout = output.get("stdout")
        stderr = output.get("stderr")
        exit_code = output.get("exit_code")
        success = output.get("success")
        if not isinstance(success, bool) and isinstance(exit_code, int):
            success = exit_code == 0
        extras["verification"] = {
            "command": command or output.get("command") or tool_name,
            "kind": kind,
            "exit_code": exit_code if isinstance(exit_code, int) else None,
            "success": bool(success) if isinstance(success, bool) else None,
            "stdout_tail": stdout if isinstance(stdout, str) else None,
            "stderr_tail": stderr if isinstance(stderr, str) else None,
        }
    return extras


def _structured_tool_evidence(
    beak_step: Step,
    tool_name: str,
    output: dict[str, Any],
) -> list[dict[str, str]]:
    """Project successful file tools into lifecycle evidence at the source."""

    if getattr(getattr(beak_step, "result", None), "status", "success") != "success":
        return []
    if output.get("error") or output.get("success") is False:
        return []
    read_tools = {"read_file", "read_text_file", "read_file_range"}
    search_tools = {"grep", "grep_text", "glob", "glob_files", "search_files"}
    if tool_name not in read_tools | search_tools:
        return []

    paths: list[str] = []
    if tool_name in read_tools:
        action_args = getattr(getattr(beak_step, "action", None), "args", {}) or {}
        candidate = action_args.get("path") or action_args.get("file_path")
        if isinstance(candidate, str):
            paths.append(candidate)
    _collect_structured_paths(output, paths)

    evidence: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = raw_path.strip()
        if (
            not path
            or path in {".", ".."}
            or any(char in path for char in "*?[]{}\n\r")
            or path in seen
        ):
            continue
        normalized = path.replace("\\", "/")
        leaf = normalized.rsplit("/", 1)[-1]
        if not leaf or ("." not in leaf and "/" not in normalized):
            continue
        seen.add(path)
        evidence.append(
            {
                "kind": "file",
                "title": leaf,
                "uri": path,
                "status": "observed",
                "origin": "tool",
            }
        )
    return evidence


def _collect_structured_paths(value: Any, paths: list[str]) -> None:
    if isinstance(value, dict):
        path = value.get("path")
        if isinstance(path, str):
            paths.append(path)
        for key, nested in value.items():
            if key != "content":
                _collect_structured_paths(nested, paths)
    elif isinstance(value, list):
        for nested in value:
            _collect_structured_paths(nested, paths)


def _beak_step_effective_success(step: Any) -> bool:
    result = getattr(step, "result", None)
    if getattr(result, "status", "success") != "success":
        return False

    output = getattr(result, "output", None)
    if not isinstance(output, dict):
        return True
    if output_signals_error(output):
        return False

    success = output.get("success")
    if isinstance(success, bool):
        return success

    exit_code = output.get("exit_code")
    if isinstance(exit_code, int):
        return exit_code == 0

    return True


def _has_unrecovered_beak_failure(steps: list[Any]) -> bool:
    """Return True only when the last failed tool has no later recovery.

    A ReAct turn is allowed to recover by changing tools or arguments.  The
    former all-or-nothing aggregation marked the entire turn failed forever
    after one transient error, even when later verification succeeded and a
    guarded final answer was produced.  Checklist/blackboard bookkeeping does
    not count as recovery; a later substantive tool execution must succeed.
    """

    last_failure = -1
    for index, step in enumerate(steps):
        if not _beak_step_effective_success(step):
            last_failure = index
    if last_failure < 0:
        return False

    bookkeeping = {
        "todo_write",
        "bb_write",
        "bb_read",
        "bb_keys",
        "search_capabilities",
        "query_capability",
        "query_skill",
    }
    for step in steps[last_failure + 1 :]:
        action = getattr(step, "action", None)
        name = str(getattr(action, "name", "") or "").strip()
        if name not in bookkeeping and _beak_step_effective_success(step):
            return False
    return True


def _has_structured_user_block(steps: list[Any]) -> bool:
    """Return whether the latest unresolved tool step explicitly awaits approval.

    This consumes executor-authored protocol tags, never the model's final
    prose. A later substantive success clears the block through the same
    recovery rule used for tool failures.
    """

    if not _has_unrecovered_beak_failure(steps):
        return False
    for step in reversed(steps):
        result = getattr(step, "result", None)
        tags = {
            str(tag).strip().lower()
            for tag in (getattr(result, "stderr_tags", None) or [])
            if str(tag).strip()
        }
        if {"waiting_user", "approval_required"}.issubset(tags):
            return True
        if not _beak_step_effective_success(step):
            return False
    return False


def _format_background_task_heartbeat(task_ids: list[str]) -> str:
    """Render the periodic 'background tasks still running' nudge.

    Kept as a tiny helper so test_background_task_heartbeat can assert
    the exact wording without spinning up the full ReAct loop.
    """
    ids_str = ", ".join(task_ids)
    return (
        "[background-task-tracker]\n"
        f"Background processes still registered: {ids_str}.\n"
        "Use read_shell_output(task_id) to check progress, or "
        "kill_shell(task_id) to stop.\n"
        "If you've already finalised the task without checking, do so now."
    )


def _react_completion_receipt(
    *,
    final_answer: str | None,
    terminated_reason: str,
    effective_success: bool,
    executed_beak_steps: list[Any],
    completion_decision: dict[str, object] | None = None,
) -> dict[str, object]:
    decision_outcome = str((completion_decision or {}).get("outcome") or "")
    if decision_outcome in {"completed", "completed_with_warning"} or (
        terminated_reason in {"final_answer", "final_answer_with_warning"}
        and final_answer
        and effective_success
    ):
        run_status = "completed"
    elif decision_outcome in {"paused", "cancelled", "blocked_on_user"} or (
        terminated_reason in {"paused", "cancelled"}
    ):
        run_status = "pending"
    else:
        run_status = "failed"

    tool_statuses = [
        str(getattr(getattr(step, "result", None), "status", "") or "")
        for step in executed_beak_steps
    ]
    statuses = [
        ("completed" if status == "success" else status) for status in tool_statuses if status
    ] or [run_status]
    if run_status != "completed":
        statuses.append(run_status)

    artifact_count = 0
    for step in executed_beak_steps:
        files = getattr(getattr(step, "result", None), "files_modified", None)
        if isinstance(files, list):
            artifact_count += len(files)

    warnings: list[str] = []
    if terminated_reason not in {"final_answer", "final_answer_with_warning"}:
        warnings.append(f"terminated:{terminated_reason}")
    if decision_outcome == "completed_with_warning":
        warnings.append("completed_with_warning")

    receipt = build_completion_receipt(
        statuses,
        contract_warnings=warnings,
        artifact_count=artifact_count,
        output_present=bool(final_answer),
    ).to_dict()
    failure = _latest_failed_tool_message(executed_beak_steps)
    if run_status == "failed" and failure:
        receipt["message"] = failure
        receipt["code"] = "tool_execution_failed"
    elif run_status == "failed" and final_answer:
        # Guard impasses and model stalls already produce a scrubbed,
        # user-facing handoff that explains what happened and how to resume.
        # Preserve it in the structured failure receipt; otherwise the
        # gateway only sees the opaque terminal code (``guard_impasse`` /
        # ``model_stall``) and the UI falls back to “turn failed”.
        receipt["message"] = final_answer.strip()
        receipt["code"] = terminated_reason or "react_failed"
    # Structured classification rides alongside the raw message so the
    # gateway / UI can show a one-line human reason (``failure.readable``)
    # instead of the raw stderr when the cause is environmental.
    classified = classify_turn_failure(executed_beak_steps)
    if run_status == "failed" and classified:
        receipt["failure"] = classified
    return receipt


def _latest_failed_tool_info(steps: list[Any]) -> tuple[str, str] | None:
    """Return ``(tool_name, redacted detail)`` of the latest failed tool.

    Walks the trajectory backwards so a turn that recovered after a transient
    failure is only inspected for its *final* failure — the one the user
    actually hit. Bookkeeping writes (todo/blackboard) are skipped because a
    failed todo_write is never the reason a turn failed.
    """

    for step in reversed(steps):
        if _beak_step_effective_success(step):
            continue
        action = getattr(step, "action", None)
        result = getattr(step, "result", None)
        tool_name = str(
            getattr(action, "name", "")
            or getattr(action, "sucker_id", "")
            or getattr(result, "name", "")
            or "tool"
        ).strip()
        output = getattr(result, "output", None)
        candidates: list[Any] = []
        if isinstance(output, dict):
            candidates.extend(
                output.get(key) for key in ("stderr", "output", "stdout", "message", "error")
            )
        else:
            candidates.append(output)
        candidates.extend(
            (
                getattr(result, "rendered", None),
                getattr(result, "error_type", None),
            )
        )
        detail = next(
            (
                str(value).strip()
                for value in candidates
                if isinstance(value, str) and value.strip()
            ),
            "",
        )
        if not detail:
            continue
        try:
            from runtime.platform.observability.redactor import redact_text

            detail = redact_text(detail)
        except Exception:  # pragma: no cover - diagnostics must not mask failure
            detail = "tool execution failed"
        return tool_name, detail
    return None


def _latest_failed_tool_message(steps: list[Any], *, limit: int = 900) -> str:
    """Return the latest actionable tool failure for the terminal receipt.

    ReAct can emit a perfectly readable final answer after a command fails.
    The turn is still correctly marked failed, but without this detail the
    realtime layer only receives ``terminated_reason=final_answer`` and the UI
    has no choice but to show a generic retry banner.
    """

    info = _latest_failed_tool_info(steps)
    if info is None:
        return ""
    tool_name, detail = info
    detail = " ".join(detail.split())
    return f"{tool_name} failed: {detail}"[:limit]


def classify_turn_failure(steps: list[Any]) -> dict[str, Any] | None:
    """Classify the turn's latest tool failure into a readable taxonomy.

    Uses ``_latest_failed_tool_info`` (same walk as the receipt message) so
    the structured ``{kind, code, readable, tool, detail}`` attached to the
    completion receipt and the realtime ``turn.error`` always describes the
    failure the UI will actually blame. A trajectory that recovered (later
    substantive success) has nothing to classify — same contract as
    ``_has_unrecovered_beak_failure``. Returns ``None`` when the failure is
    not recognisably environmental / git-hook.
    """

    if not _has_unrecovered_beak_failure(steps):
        return None
    info = _latest_failed_tool_info(steps)
    if info is None:
        return None
    tool_name, detail = info
    from runtime.core.cerebrum._react_failure_classification import (
        classify_tool_failure,
    )

    classified = classify_tool_failure(tool_name, detail)
    if classified is None:
        return None
    return {
        "kind": classified["kind"],
        "code": classified["code"],
        "readable": classified["readable"],
        "tool": tool_name,
        "detail": detail,
    }


def _skill_available_in_executor(executor: Any, skill_name: str) -> bool:
    """Check if a skill is registered and available in the executor."""
    if executor is None:
        return False
    try:
        registry = getattr(executor, "registry", None)
        if registry is None:
            return False
        if hasattr(registry, "has") and callable(registry.has):
            return bool(registry.has(skill_name))
        if hasattr(registry, "is_enabled") and callable(registry.is_enabled):
            return bool(registry.is_enabled(skill_name))
        return False
    except (AttributeError, TypeError, ValueError):
        return False


def _is_scoped_artifact_write(tool_name: str, args: dict[str, Any] | None) -> bool:
    """Allow routine non-code deliverables without an approval round trip."""
    if tool_name not in _SCOPED_ARTIFACT_WRITE_TOOLS or not isinstance(args, dict):
        return False
    raw_path = args.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return False

    from pathlib import Path

    from runtime.platform.process.scope import resolve_write_scope, thread_artifact_root
    from runtime.platform.process.session import current_session

    session = current_session()
    if session is None:
        return False
    scope = resolve_write_scope(session)
    if scope.mode in {"code", "plan"}:
        return False

    artifact_root = thread_artifact_root(
        session.thread_id or "default",
        explicit_root=(
            session.metadata.get("_artifact_output_root")
            if isinstance(session.metadata.get("_artifact_output_root"), str)
            else None
        ),
    )
    supplied_sandbox = args.get("sandbox_dir")
    sandbox = (
        Path(supplied_sandbox).expanduser()
        if isinstance(supplied_sandbox, str) and supplied_sandbox.strip()
        else artifact_root
    )
    target = Path(raw_path).expanduser()
    if not target.is_absolute():
        target = sandbox / target
    try:
        target.resolve(strict=False).relative_to(artifact_root.resolve(strict=False))
    except (OSError, ValueError):
        return False
    return True
