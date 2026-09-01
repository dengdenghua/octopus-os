"""Pure read-model and replay helpers for the agent trace store."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from ._trace_store_replay_fingerprint import task_run_replay_fingerprint

ApprovalDecision = Literal[
    "requested", "approved", "rejected", "timeout", "connection_lost", "error"
]
TaskRunStatus = Literal[
    "running",
    "paused",
    "completed",
    "failed",
    "interrupted",
    "cancelled",
    "unknown",
]

_TASK_RUN_TERMINAL_EVENTS: dict[str, TaskRunStatus] = {
    "TASK_RUN_COMPLETED": "completed",
    "TASK_RUN_FINISHED": "completed",
    "RUN_FINISHED": "completed",
    "TASK_RUN_FAILED": "failed",
    "RUN_FAILED": "failed",
    "REACT_ERROR": "failed",
    "TASK_RUN_INTERRUPTED": "interrupted",
    "TASK_RUN_PAUSED": "paused",
    "RUN_INTERRUPTED": "interrupted",
    "REACT_CANCELLED": "interrupted",
    "TASK_RUN_CANCELLED": "cancelled",
    "RUN_CANCELLED": "cancelled",
}
_TASK_RUN_START_EVENTS: frozenset[str] = frozenset(
    {
        "TASK_RUN_STARTED",
        "RUN_STARTED",
    }
)
_TOOL_START_EVENTS: frozenset[str] = frozenset(
    {
        "TOOL_CALL_START",
        "TOOL_START",
        "SUB_TOOL_START",
    }
)
_TOOL_END_EVENTS: frozenset[str] = frozenset(
    {
        "TOOL_CALL_END",
        "TOOL_CALL_FINISH",
        "TOOL_END",
        "SUB_TOOL_END",
    }
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _replay_gate_from_evaluations(
    evaluations: list[dict[str, Any]],
    *,
    min_cases: int,
    min_score: float,
    filters: dict[str, Any],
) -> dict[str, Any]:
    threshold_cases = max(0, int(min_cases or 0))
    threshold_score = max(0.0, min(1.0, float(min_score or 0.0)))
    failing = [
        item
        for item in evaluations
        if item.get("passed") is not True or float(item.get("score") or 0.0) < threshold_score
    ]
    total = len(evaluations)
    enough_cases = total >= threshold_cases
    passed = enough_cases and not failing
    reasons: list[str] = []
    if not enough_cases:
        reasons.append(f"insufficient_cases:{total}<{threshold_cases}")
    if failing:
        reasons.append(f"failing_cases:{len(failing)}")
    if passed:
        reasons.append("all_replay_evaluations_passed")
    return {
        "schema": "echo.replay_gate.v1",
        "passed": passed,
        "reason": ";".join(reasons),
        "thresholds": {
            "min_cases": threshold_cases,
            "min_score": threshold_score,
        },
        "summary": {
            "total": total,
            "passed": sum(1 for item in evaluations if item.get("passed") is True),
            "failed": sum(1 for item in evaluations if item.get("passed") is False),
            "below_min_score": sum(
                1 for item in evaluations if float(item.get("score") or 0.0) < threshold_score
            ),
        },
        "failing_cases": failing[:20],
        "filters": filters,
    }


def _event_key(event_type: Any) -> str:
    return _clean_str(event_type).replace("-", "_").upper()


def _event_status(event_type: Any, payload: Any | None = None) -> TaskRunStatus | None:
    key = _event_key(event_type)
    if key in _TASK_RUN_START_EVENTS:
        return "running"
    if key == "TASK_RUN_FINISHED" and isinstance(payload, dict):
        status = str(payload.get("status") or "").lower()
        if status in {
            "running",
            "paused",
            "completed",
            "failed",
            "interrupted",
            "cancelled",
            "unknown",
        }:
            return status  # type: ignore[return-value]
    return _TASK_RUN_TERMINAL_EVENTS.get(key)


def _ts_max(values: list[str]) -> str | None:
    clean = [value for value in values if isinstance(value, str) and value]
    return max(clean) if clean else None


def _tool_name_from_payload(payload: Any) -> str:
    raw = payload if isinstance(payload, dict) else {}
    for key in ("tool", "tool_name", "name"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _tool_call_id_from_payload(
    payload: Any,
    event: dict[str, Any] | None = None,
) -> str:
    raw = payload if isinstance(payload, dict) else {}
    for key in ("tool_call_id", "id", "tool_use_id", "call_id"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    item_id = event.get("item_id") if isinstance(event, dict) else None
    return item_id.strip() if isinstance(item_id, str) and item_id.strip() else ""


def _tool_event_failed(payload: Any) -> bool:
    raw = payload if isinstance(payload, dict) else {}
    if raw.get("is_error") is True:
        return True
    status = str(raw.get("status") or raw.get("decision") or "").lower()
    return status in {"error", "failed", "failure", "rejected", "cancelled"}


def _preview_from_payload(
    payload: Any,
    *,
    value_key: str,
    preview_key: str,
    limit: int,
) -> str:
    from ._trace_store_recovery import _truncate

    raw = payload if isinstance(payload, dict) else {}
    value = raw.get(preview_key)
    if value is None:
        value = raw.get(value_key)
    return _truncate(_render_preview(value), limit)


def _render_preview(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _task_run_from_rows(
    *,
    task_id: str,
    events: list[dict[str, Any]],
    checkpoints: list[dict[str, Any]],
    token_rows: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    include_events: bool,
) -> dict[str, Any]:
    from ._trace_store_recovery import _latest_checkpoint_review_summary

    sorted_events = sorted(
        events, key=lambda row: (str(row.get("ts") or ""), int(row.get("id") or 0))
    )
    start_event = next(
        (
            event
            for event in sorted_events
            if _event_key(event.get("event_type")) in _TASK_RUN_START_EVENTS
        ),
        sorted_events[0] if sorted_events else None,
    )
    terminal_events = [
        event
        for event in sorted_events
        if _event_key(event.get("event_type")) in _TASK_RUN_TERMINAL_EVENTS
    ]
    latest_terminal = terminal_events[-1] if terminal_events else None
    start_payload = start_event.get("payload") if isinstance(start_event, dict) else {}
    start_payload = start_payload if isinstance(start_payload, dict) else {}
    finish_payload = latest_terminal.get("payload") if isinstance(latest_terminal, dict) else {}
    finish_payload = finish_payload if isinstance(finish_payload, dict) else {}
    status = (
        _event_status(latest_terminal.get("event_type"), finish_payload)  # type: ignore[union-attr]
        if latest_terminal is not None
        else ("running" if sorted_events else "unknown")
    )
    status = status or "unknown"

    tool_starts = [
        event
        for event in sorted_events
        if _event_key(event.get("event_type")) in _TOOL_START_EVENTS
    ]
    tool_ends = [
        event for event in sorted_events if _event_key(event.get("event_type")) in _TOOL_END_EVENTS
    ]
    tool_names = sorted(
        {
            name
            for event in (*tool_starts, *tool_ends)
            if (name := _tool_name_from_payload(event.get("payload")))
        }
    )

    token_totals = {
        "input_tokens": sum(int(row.get("input_tokens") or 0) for row in token_rows),
        "output_tokens": sum(int(row.get("output_tokens") or 0) for row in token_rows),
        "thinking_tokens": sum(int(row.get("thinking_tokens") or 0) for row in token_rows),
        "cached_tokens": sum(int(row.get("cached_tokens") or 0) for row in token_rows),
        "cost_usd": sum(float(row.get("cost_usd") or 0.0) for row in token_rows),
    }
    ts_values = [str(row.get("ts") or "") for row in (*sorted_events, *checkpoints, *token_rows)]
    completed_at = str(latest_terminal.get("ts") or "") if latest_terminal is not None else None
    run = {
        "task_id": task_id,
        "thread_id": _first_nonempty(
            start_event,
            sorted_events,
            checkpoints,
            token_rows,
            "thread_id",
        ),
        "turn_id": _first_nonempty(
            start_event,
            sorted_events,
            checkpoints,
            token_rows,
            "turn_id",
        ),
        "agent_id": _first_nonempty(
            start_event,
            sorted_events,
            checkpoints,
            token_rows,
            "agent_id",
        ),
        "status": status,
        "title": str(start_payload.get("title") or ""),
        "goal": str(start_payload.get("goal") or ""),
        "mode": str(start_payload.get("mode") or ""),
        "summary": str(finish_payload.get("summary") or ""),
        "reason": str(finish_payload.get("reason") or ""),
        "started_at": str(start_event.get("ts") or "") if isinstance(start_event, dict) else None,
        "completed_at": completed_at or None,
        "updated_at": _ts_max(ts_values),
        "latest_event_type": sorted_events[-1].get("event_type") if sorted_events else None,
        "event_count": len(sorted_events),
        "tool_calls_started": len(tool_starts),
        "tool_calls_finished": len(tool_ends),
        "tool_errors": sum(1 for event in tool_ends if _tool_event_failed(event.get("payload"))),
        "tool_names": tool_names,
        "approval_count": len(approvals),
        "approval_rejections": sum(
            1
            for row in approvals
            if str(row.get("decision") or "").lower()
            in {"rejected", "timeout", "connection_lost", "error"}
        ),
        "checkpoint_count": len(checkpoints),
        "latest_checkpoint": _latest_checkpoint_review_summary(checkpoints),
        "token_usage_count": len(token_rows),
        "token_totals": token_totals,
    }
    if include_events:
        run["events"] = sorted_events
    return run


def _first_nonempty(
    start_event: dict[str, Any] | None,
    events: list[dict[str, Any]],
    checkpoints: list[dict[str, Any]],
    token_rows: list[dict[str, Any]],
    key: str,
) -> str | None:
    rows: list[dict[str, Any]] = []
    if isinstance(start_event, dict):
        rows.append(start_event)
    rows.extend(events)
    rows.extend(checkpoints)
    rows.extend(token_rows)
    for row in rows:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _task_run_review_from_loop_checkpoint(
    run: dict[str, Any],
    checkpoint: dict[str, Any],
) -> dict[str, Any]:
    from runtime.execution.loops.learning import build_loop_run_review
    from runtime.execution.loops.models import (
        LoopAttempt,
        LoopMode,
        LoopPolicy,
        LoopRun,
        LoopRunStatus,
        VerifierFinding,
        VerifierResult,
    )

    state = checkpoint.get("state") if isinstance(checkpoint.get("state"), dict) else {}
    raw_status = _clean_str(state.get("current_phase")) or _clean_str(run.get("status")) or "failed"
    try:
        status = LoopRunStatus(raw_status)
    except ValueError:
        status = LoopRunStatus.FAILED

    raw_mode = _clean_str(run.get("mode")) or "code"
    try:
        mode = LoopMode(raw_mode)
    except ValueError:
        mode = LoopMode.CODE

    attempts: list[LoopAttempt] = []
    attempt_rows = state.get("attempt_snapshots")
    attempt_rows = attempt_rows if isinstance(attempt_rows, list) else []
    goal = (
        _clean_str(run.get("goal"))
        or _clean_str(run.get("title"))
        or _clean_str(checkpoint.get("summary"))
    )
    for index, item in enumerate(attempt_rows, start=1):
        if not isinstance(item, dict):
            continue
        verifier_raw = item.get("verifier") if isinstance(item.get("verifier"), dict) else {}
        failed_checks = [
            str(name).strip()
            for name in verifier_raw.get("failed_checks") or []
            if str(name or "").strip()
        ]
        verifier_result: VerifierResult | None = None
        if verifier_raw:
            failure_category = _clean_str(verifier_raw.get("failure_category")) or ""
            findings = [
                VerifierFinding(
                    name=name,
                    passed=False,
                    category=failure_category,
                    stderr=str(verifier_raw.get("summary") or "") if idx == 0 else "",
                )
                for idx, name in enumerate(failed_checks)
            ]
            if not findings and bool(verifier_raw.get("passed")):
                findings = [VerifierFinding(name="verifier", passed=True, exit_code=0)]
            verifier_result = VerifierResult(
                profile=_clean_str(verifier_raw.get("profile")) or "auto",
                kind=_clean_str(verifier_raw.get("kind")) or "unknown",
                failure_category=failure_category,
                passed=bool(verifier_raw.get("passed")),
                summary=str(verifier_raw.get("summary") or ""),
                findings=findings,
            )
        attempts.append(
            LoopAttempt(
                attempt_index=int(item.get("attempt_index") or index),
                prompt=str(item.get("prompt_preview") or goal),
                started_at=_clean_str(item.get("started_at")) or str(checkpoint.get("ts") or ""),
                completed_at=_clean_str(item.get("completed_at")) or None,
                status=_clean_str(item.get("status")) or "completed",
                success=item.get("success"),
                terminated_reason=str(item.get("terminated_reason") or ""),
                final_answer=str(item.get("final_answer_preview") or ""),
                verifier_result=verifier_result,
                error=str(item.get("error_preview") or ""),
            )
        )

    attempt_count = max(
        int(state.get("attempt_count") or 0),
        int(checkpoint.get("iteration") or 0),
        len(attempts),
    )
    if not attempts and attempt_count > 0:
        for attempt_index in range(1, attempt_count + 1):
            attempts.append(
                LoopAttempt(
                    attempt_index=attempt_index,
                    prompt=goal,
                )
            )

    last_verifier_raw = (
        state.get("last_verifier") if isinstance(state.get("last_verifier"), dict) else {}
    )
    last_verifier_result: VerifierResult | None = None
    if attempts and attempts[-1].verifier_result is not None:
        last_verifier_result = attempts[-1].verifier_result
    elif last_verifier_raw:
        failed_checks = [
            str(name).strip()
            for name in last_verifier_raw.get("failed_checks") or []
            if str(name or "").strip()
        ]
        failure_category = _clean_str(last_verifier_raw.get("failure_category")) or ""
        last_verifier_result = VerifierResult(
            profile=_clean_str(last_verifier_raw.get("profile")) or "auto",
            kind=_clean_str(last_verifier_raw.get("kind")) or "unknown",
            failure_category=failure_category,
            passed=bool(last_verifier_raw.get("passed")),
            summary=str(last_verifier_raw.get("summary") or ""),
            findings=[
                VerifierFinding(name=name, passed=False, category=failure_category)
                for name in failed_checks
            ]
            or [VerifierFinding(name="verifier", passed=bool(last_verifier_raw.get("passed")))],
        )

    workspace_path = _clean_str(state.get("workspace_path"))
    loop_run = LoopRun(
        run_id=str(run.get("task_id") or checkpoint.get("task_id") or ""),
        parent_run_id=_clean_str(state.get("parent_run_id")) or None,
        origin_run_id=_clean_str(state.get("origin_run_id")) or None,
        resume_checkpoint_id=_clean_str(state.get("resume_checkpoint_id")) or None,
        goal=goal or "loop run",
        mode=mode,
        status=status,
        thread_id=_clean_str(run.get("thread_id"))
        or _clean_str(checkpoint.get("thread_id"))
        or None,
        workspace_path=workspace_path or None,
        policy=LoopPolicy(
            verifier_profile=(
                last_verifier_result.profile if last_verifier_result is not None else "auto"
            )
        ),
        attempts=attempts,
        last_verifier_result=last_verifier_result,
        last_error=""
        if status == LoopRunStatus.COMPLETED
        else (
            _clean_str(run.get("reason"))
            or _clean_str(checkpoint.get("summary"))
            or _clean_str(state.get("progress_summary"))
        ),
        created_at=_clean_str(run.get("started_at"))
        or _clean_str(checkpoint.get("ts"))
        or _now_iso(),
        updated_at=_clean_str(run.get("updated_at"))
        or _clean_str(checkpoint.get("ts"))
        or _now_iso(),
        started_at=_clean_str(run.get("started_at")) or _clean_str(checkpoint.get("ts")) or None,
        completed_at=_clean_str(run.get("completed_at")) or None,
    )
    review = build_loop_run_review(loop_run)
    summary = review.get("summary") if isinstance(review.get("summary"), dict) else {}
    review["thread_id"] = run.get("thread_id")
    review["turn_id"] = run.get("turn_id")
    review["agent_id"] = run.get("agent_id")
    review["summary"] = {
        **summary,
        "title": run.get("title") or "",
        "goal": run.get("goal") or "",
        "mode": run.get("mode") or "",
        "checkpoint_count": run.get("checkpoint_count") or 0,
        "token_totals": run.get("token_totals") or {},
        "trace_checkpoint_id": checkpoint.get("id"),
    }
    resume = review.get("resume") if isinstance(review.get("resume"), dict) else {}
    latest_checkpoint = (
        resume.get("latest_checkpoint") if isinstance(resume.get("latest_checkpoint"), dict) else {}
    )
    review["resume"] = {
        **resume,
        "source": "trace_store",
        "latest_checkpoint": {
            **latest_checkpoint,
            "trace_checkpoint_id": checkpoint.get("id"),
        },
    }
    return review


def _task_run_review_from_run(
    run: dict[str, Any],
    approvals: list[dict[str, Any]],
) -> dict[str, Any]:
    from ._trace_store_recovery import (
        _task_run_backlog_candidates,
        _task_run_learning_candidates,
        _task_run_resume_summary,
    )

    events = run.get("events") if isinstance(run.get("events"), list) else []
    findings = _task_run_findings(run, approvals, events)
    score, score_reasons = _task_run_review_score(run, findings)
    return {
        "schema": "echo.task_run_review.v1",
        "task_id": run.get("task_id"),
        "thread_id": run.get("thread_id"),
        "turn_id": run.get("turn_id"),
        "agent_id": run.get("agent_id"),
        "status": run.get("status"),
        "score": score,
        "score_reasons": score_reasons,
        "findings": findings,
        "replay": _task_run_replay(events, approvals),
        "resume": _task_run_resume_summary(run),
        "learning_candidates": _task_run_learning_candidates(run, findings),
        "backlog_candidates": _task_run_backlog_candidates(run, findings),
        "summary": {
            "title": run.get("title") or "",
            "goal": run.get("goal") or "",
            "mode": run.get("mode") or "",
            "tool_calls_started": run.get("tool_calls_started") or 0,
            "tool_calls_finished": run.get("tool_calls_finished") or 0,
            "tool_errors": run.get("tool_errors") or 0,
            "approval_count": run.get("approval_count") or 0,
            "approval_rejections": run.get("approval_rejections") or 0,
            "checkpoint_count": run.get("checkpoint_count") or 0,
            "token_totals": run.get("token_totals") or {},
        },
    }


def _task_run_replay_case_from_review(review: dict[str, Any]) -> dict[str, Any]:
    replay = review.get("replay") if isinstance(review.get("replay"), dict) else {}
    resume = review.get("resume") if isinstance(review.get("resume"), dict) else {}
    latest_checkpoint = (
        resume.get("latest_checkpoint") if isinstance(resume.get("latest_checkpoint"), dict) else {}
    )
    findings = [
        finding
        for finding in (review.get("findings") if isinstance(review.get("findings"), list) else [])
        if isinstance(finding, dict)
    ]
    return {
        "schema": "echo.task_run_replay_case.v1",
        "case_id": replay.get("case_id") or "",
        "fingerprint": replay.get("fingerprint") or "",
        "source": {
            "task_id": review.get("task_id"),
            "thread_id": review.get("thread_id"),
            "turn_id": review.get("turn_id"),
            "agent_id": review.get("agent_id"),
            "status": review.get("status"),
        },
        "replay": replay,
        "expectations": {
            "status": review.get("status"),
            "score": review.get("score"),
            "finding_types": [
                str(finding.get("type") or "") for finding in findings if finding.get("type")
            ],
            "tool_error_count": sum(
                1 for finding in findings if finding.get("type") == "tool_error"
            ),
        },
        "resume": {
            "available": bool(resume.get("available")) if isinstance(resume, dict) else False,
            "source": resume.get("source") if isinstance(resume, dict) else None,
            "latest_checkpoint_id": latest_checkpoint.get("id"),
        },
        "safety": {
            "raw_messages_included": False,
            "raw_checkpoint_state_included": False,
            "tool_outputs_truncated": True,
        },
    }


def _evaluate_task_run_replay_case(replay_case: dict[str, Any]) -> dict[str, Any]:
    replay = replay_case.get("replay") if isinstance(replay_case.get("replay"), dict) else {}
    expectations = (
        replay_case.get("expectations") if isinstance(replay_case.get("expectations"), dict) else {}
    )
    source = replay_case.get("source") if isinstance(replay_case.get("source"), dict) else {}
    safety = replay_case.get("safety") if isinstance(replay_case.get("safety"), dict) else {}
    steps = replay.get("steps") if isinstance(replay.get("steps"), list) else []
    checks = [
        _replay_check(
            "schema",
            replay_case.get("schema") == "echo.task_run_replay_case.v1",
            "Replay case schema is recognized.",
        ),
        _replay_check(
            "case_id",
            bool(str(replay_case.get("case_id") or "").startswith("task-run:")),
            "Replay case has a stable task-run case id.",
        ),
        _replay_check(
            "fingerprint",
            len(str(replay_case.get("fingerprint") or "")) == 16
            and replay_case.get("fingerprint") == replay.get("fingerprint"),
            "Replay fingerprint is present and matches the embedded replay.",
        ),
        _replay_check(
            "replayable",
            replay.get("replayable") is True and bool(steps),
            "Replay contains at least one step.",
        ),
        _replay_check(
            "step_count",
            int(replay.get("step_count") or 0) == len(steps),
            "Replay step_count matches the embedded steps.",
        ),
        _replay_check(
            "status_expectation",
            expectations.get("status") == source.get("status"),
            "Expected status matches the source task status.",
        ),
        _replay_check(
            "tool_error_count",
            int(expectations.get("tool_error_count") or 0)
            == sum(
                1
                for step in steps
                if isinstance(step, dict)
                and step.get("kind") == "tool_end"
                and step.get("is_error") is True
            ),
            "Expected tool error count matches replay tool_end errors.",
        ),
        _replay_check(
            "task_boundary",
            any(isinstance(step, dict) and step.get("kind") == "task_start" for step in steps)
            and any(isinstance(step, dict) and step.get("kind") == "task_event" for step in steps),
            "Replay contains task start and terminal/task event boundaries.",
        ),
        _replay_check(
            "safety",
            safety.get("raw_messages_included") is False
            and safety.get("raw_checkpoint_state_included") is False
            and safety.get("tool_outputs_truncated") is True,
            "Replay case does not include raw messages or checkpoint state.",
        ),
    ]
    passed = all(check["passed"] for check in checks)
    return {
        "schema": "echo.task_run_replay_evaluation.v1",
        "case_id": replay_case.get("case_id") or "",
        "fingerprint": replay_case.get("fingerprint") or "",
        "passed": passed,
        "score": round(
            sum(1 for check in checks if check["passed"]) / max(1, len(checks)),
            3,
        ),
        "checks": checks,
        "source": source,
    }


def _replay_check(name: str, passed: bool, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "description": description,
    }


def _task_run_findings(
    run: dict[str, Any],
    approvals: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from ._trace_store_recovery import (
        _approval_risk_level,
        _trust_gateway_from_approval,
    )

    findings: list[dict[str, Any]] = []
    status = str(run.get("status") or "unknown")
    if status == "paused":
        findings.append(
            {
                "type": "resumable_status",
                "severity": "info",
                "title": "Task is paused with resumable state",
                "evidence": {
                    "status": status,
                    "reason": run.get("reason") or "",
                    "checkpoint_count": run.get("checkpoint_count") or 0,
                },
                "recommendation": "Resume the same objective id; do not score this as a failed run.",
            }
        )
    if status in {"failed", "interrupted", "cancelled", "unknown"}:
        findings.append(
            {
                "type": "terminal_status",
                "severity": "high" if status in {"failed", "cancelled"} else "medium",
                "title": f"Task ended as {status}",
                "evidence": {
                    "status": status,
                    "reason": run.get("reason") or "",
                    "latest_event_type": run.get("latest_event_type"),
                },
                "recommendation": "Create a regression replay case from this run before changing prompts or tools.",
            }
        )

    failed_tool_events = [
        event
        for event in events
        if _event_key(event.get("event_type")) in _TOOL_END_EVENTS
        and _tool_event_failed(event.get("payload"))
    ]
    for event in failed_tool_events[:5]:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        findings.append(
            {
                "type": "tool_error",
                "severity": "high",
                "title": f"Tool failed: {_tool_name_from_payload(payload) or 'unknown'}",
                "evidence": {
                    "event_id": event.get("id"),
                    "tool_call_id": _tool_call_id_from_payload(payload, event),
                    "tool": _tool_name_from_payload(payload),
                    "status": payload.get("status"),
                    "is_error": payload.get("is_error"),
                    "output_preview": _preview_from_payload(
                        payload,
                        value_key="output",
                        preview_key="output_preview",
                        limit=280,
                    ),
                },
                "recommendation": "Capture this tool input/output pair as a replay fixture or add a preflight validation rule.",
            }
        )

    started = int(run.get("tool_calls_started") or 0)
    finished = int(run.get("tool_calls_finished") or 0)
    if started > finished:
        findings.append(
            {
                "type": "dangling_tool_call",
                "severity": "medium",
                "title": "Tool call started without matching completion",
                "evidence": {"started": started, "finished": finished},
                "recommendation": "Check cancellation, background task, or event bridge handling for unmatched tool calls.",
            }
        )

    rejected = [
        row
        for row in approvals
        if str(row.get("decision") or "").lower()
        in {"rejected", "timeout", "connection_lost", "error"}
    ]
    for row in rejected[:5]:
        findings.append(
            {
                "type": "permission_friction",
                "severity": "medium",
                "title": f"Permission blocked or failed: {row.get('tool_name')}",
                "evidence": {
                    "tool": row.get("tool_name"),
                    "decision": row.get("decision"),
                    "reason": row.get("reason") or "",
                    "trust_gateway": _trust_gateway_from_approval(row),
                },
                "recommendation": "Decide whether this should become a static policy rule, a safer alternative tool, or an agent planning constraint.",
            }
        )

    risky_approvals = [
        row
        for row in approvals
        if str(row.get("decision") or "").lower() == "approved"
        and _approval_risk_level(row) in {"high", "critical"}
    ]
    for row in risky_approvals[:5]:
        findings.append(
            {
                "type": "high_risk_approval",
                "severity": "medium",
                "title": f"High-risk tool approved: {row.get('tool_name')}",
                "evidence": {
                    "tool": row.get("tool_name"),
                    "risk_level": _approval_risk_level(row),
                    "trust_gateway": _trust_gateway_from_approval(row),
                },
                "recommendation": "Keep this approval visible in replay and require evidence that the action stayed within scope.",
            }
        )

    if status == "completed" and not findings and int(run.get("tool_calls_finished") or 0) > 0:
        findings.append(
            {
                "type": "success_pattern",
                "severity": "info",
                "title": "Completed with tools and no detected failures",
                "evidence": {
                    "tool_names": run.get("tool_names") or [],
                    "checkpoint_count": run.get("checkpoint_count") or 0,
                },
                "recommendation": "Store as a positive replay example if the user outcome was actually useful.",
            }
        )
    return findings


def _task_run_review_score(
    run: dict[str, Any],
    findings: list[dict[str, Any]],
) -> tuple[float, list[str]]:
    status = str(run.get("status") or "unknown")
    score = {
        "completed": 1.0,
        "paused": 0.5,
        "running": 0.4,
        "failed": 0.2,
        "interrupted": 0.25,
        "cancelled": 0.2,
        "unknown": 0.3,
    }.get(status, 0.3)
    reasons = [f"status:{status}"]
    penalties = {
        "tool_error": 0.2,
        "permission_friction": 0.12,
        "high_risk_approval": 0.06,
        "dangling_tool_call": 0.1,
        "terminal_status": 0.05,
    }
    for finding in findings:
        ftype = str(finding.get("type") or "")
        penalty = penalties.get(ftype, 0.0)
        if penalty:
            score -= penalty
            reasons.append(f"{ftype}:-{penalty:.2f}")
    score = round(max(0.0, min(1.0, score)), 3)
    return score, reasons


def _task_run_replay(
    events: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
) -> dict[str, Any]:
    from ._trace_store_recovery import _approval_replay_fragment

    approval_by_call = {
        str(row.get("tool_call_id") or ""): row for row in approvals if row.get("tool_call_id")
    }
    steps: list[dict[str, Any]] = []
    for event in events:
        event_type = _event_key(event.get("event_type"))
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event_type in _TASK_RUN_START_EVENTS:
            steps.append(
                {
                    "kind": "task_start",
                    "ts": event.get("ts"),
                    "goal": payload.get("goal") or "",
                    "mode": payload.get("mode") or "",
                }
            )
        elif event_type in _TOOL_START_EVENTS:
            tool_call_id = _tool_call_id_from_payload(payload, event)
            approval = approval_by_call.get(tool_call_id)
            steps.append(
                {
                    "kind": "tool_start",
                    "ts": event.get("ts"),
                    "tool": _tool_name_from_payload(payload),
                    "tool_call_id": tool_call_id,
                    "input_preview": _preview_from_payload(
                        payload,
                        value_key="input",
                        preview_key="input_preview",
                        limit=500,
                    ),
                    "approval": _approval_replay_fragment(approval),
                }
            )
        elif event_type in _TOOL_END_EVENTS:
            tool_call_id = _tool_call_id_from_payload(payload, event)
            steps.append(
                {
                    "kind": "tool_end",
                    "ts": event.get("ts"),
                    "tool": _tool_name_from_payload(payload),
                    "tool_call_id": tool_call_id,
                    "status": payload.get("status")
                    or ("error" if payload.get("is_error") else "success"),
                    "is_error": bool(_tool_event_failed(payload)),
                    "output_preview": _preview_from_payload(
                        payload,
                        value_key="output",
                        preview_key="output_preview",
                        limit=500,
                    ),
                }
            )
        elif event_type in _TASK_RUN_TERMINAL_EVENTS or event_type.startswith("REACT_"):
            steps.append(
                {
                    "kind": "task_event",
                    "ts": event.get("ts"),
                    "event_type": event.get("event_type"),
                    "status": payload.get("status"),
                    "reason": payload.get("reason") or payload.get("message") or "",
                }
            )
    fingerprint = task_run_replay_fingerprint(steps)
    return {
        "schema": "echo.task_run_replay.v1",
        "fingerprint": fingerprint,
        "case_id": f"task-run:{fingerprint}",
        "replayable": bool(steps),
        "step_count": len(steps),
        "steps": steps,
        "safety": {
            "raw_messages_included": False,
            "tool_outputs_truncated": True,
            "approval_args_are_previews": True,
        },
    }


__all__ = [name for name in globals() if name.startswith("_") and not name.startswith("__")] + [
    "ApprovalDecision",
    "TaskRunStatus",
]
