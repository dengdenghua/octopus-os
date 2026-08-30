from __future__ import annotations

import hashlib
import json
from typing import Any

from runtime.execution.loops.execution_policy import verifier_execution_policies
from runtime.execution.loops.models import LoopAttempt, LoopRun, VerifierResult


def _preview(value: Any, *, limit: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _verifier_tool_name(profile: str) -> str:
    name = str(profile or "").strip() or "unknown"
    return f"verifier:{name}"


def _failed_check_names(result: VerifierResult | None) -> list[str]:
    if result is None:
        return []
    names: list[str] = []
    for finding in result.findings:
        if finding.passed:
            continue
        name = str(finding.name or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _failed_check_categories(result: VerifierResult | None) -> list[str]:
    if result is None:
        return []
    categories: list[str] = []
    if str(result.failure_category or "").strip():
        categories.append(str(result.failure_category).strip())
    for finding in result.findings:
        if finding.passed:
            continue
        category = str(finding.category or "").strip()
        if category and category not in categories:
            categories.append(category)
    return categories


def _attempt_output_preview(attempt: LoopAttempt) -> str:
    if attempt.error:
        return _preview(attempt.error)
    if attempt.final_answer:
        return _preview(attempt.final_answer)
    if attempt.terminated_reason:
        return _preview(attempt.terminated_reason)
    return ""


def _loop_terminal_reason(run: LoopRun) -> str:
    if run.last_error:
        return _preview(run.last_error, limit=280)
    if run.last_verifier_result is not None and run.last_verifier_result.summary:
        return _preview(run.last_verifier_result.summary, limit=280)
    if run.attempts:
        latest = run.attempts[-1]
        if latest.error:
            return _preview(latest.error, limit=280)
        if latest.terminated_reason:
            return _preview(latest.terminated_reason, limit=280)
    return ""


def _loop_policy_summary(run: LoopRun) -> dict[str, Any]:
    policy = run.policy
    return {
        "max_attempts": int(policy.max_attempts),
        "max_iterations": int(policy.max_iterations),
        "verifier_profile": str(policy.verifier_profile or ""),
        "auto_approve": bool(policy.auto_approve),
        "sandbox_mode": str(policy.sandbox_mode or ""),
        "permission_mode": str(policy.permission_mode or ""),
        "execution_environment": str(policy.execution_environment or ""),
        "model": str(policy.model or ""),
    }


def build_loop_run_findings(run: LoopRun) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    status = run.status.value

    if status in {"failed", "cancelled"}:
        findings.append(
            {
                "type": "terminal_status",
                "severity": "high",
                "title": f"Loop ended as {status}",
                "evidence": {
                    "status": status,
                    "reason": _loop_terminal_reason(run),
                    "attempt_count": len(run.attempts),
                },
                "recommendation": (
                    "Keep the verifier output and retry sequence attached before changing "
                    "the loop prompt or repair policy."
                ),
            }
        )

    for attempt in run.attempts:
        if attempt.error:
            findings.append(
                {
                    "type": "tool_error",
                    "severity": "high",
                    "title": f"Loop attempt raised an execution error (attempt {attempt.attempt_index})",
                    "evidence": {
                        "attempt_index": attempt.attempt_index,
                        "status": attempt.status,
                        "error": _preview(attempt.error, limit=280),
                    },
                    "recommendation": (
                        "Capture the failing attempt prompt and exception so the retry policy "
                        "can be replayed deterministically."
                    ),
                }
            )
        verifier_result = attempt.verifier_result
        if verifier_result is None or verifier_result.passed:
            continue
        findings.append(
            {
                "type": "tool_error",
                "severity": "high",
                "title": f"Verifier failed after attempt {attempt.attempt_index}",
                "evidence": {
                    "attempt_index": attempt.attempt_index,
                    "profile": verifier_result.profile,
                    "kind": verifier_result.kind,
                    "failure_category": verifier_result.failure_category,
                    "summary": _preview(verifier_result.summary, limit=280),
                    "failed_checks": _failed_check_names(verifier_result),
                    "failed_categories": _failed_check_categories(verifier_result),
                    "execution_policies": verifier_execution_policies(verifier_result),
                },
                "recommendation": (
                    "Preserve the failing verifier output as replay evidence before tuning "
                    "repair prompts or acceptance criteria."
                ),
            }
        )

    if status == "completed" and len(run.attempts) > 1 and not findings:
        findings.append(
            {
                "type": "success_pattern",
                "severity": "info",
                "title": "Loop converged after verifier-guided repair",
                "evidence": {
                    "attempt_count": len(run.attempts),
                    "verifier_profile": run.policy.verifier_profile,
                },
                "recommendation": "Keep this as a positive replay example for future repair-policy tuning.",
            }
        )

    return findings


def build_loop_run_review_score(
    run: LoopRun,
    findings: list[dict[str, Any]],
) -> tuple[float, list[str]]:
    status = run.status.value
    score = {
        "pending": 0.3,
        "running": 0.4,
        "verifying": 0.45,
        "repairing": 0.35,
        "completed": 1.0,
        "failed": 0.2,
        "cancelled": 0.2,
    }.get(status, 0.3)
    reasons = [f"status:{status}"]
    penalties = {
        "tool_error": 0.18,
        "terminal_status": 0.05,
    }
    for finding in findings:
        finding_type = str(finding.get("type") or "")
        penalty = penalties.get(finding_type, 0.0)
        if penalty:
            score -= penalty
            reasons.append(f"{finding_type}:-{penalty:.2f}")
    return round(max(0.0, min(1.0, score)), 3), reasons


def build_loop_run_replay(run: LoopRun) -> dict[str, Any]:
    steps: list[dict[str, Any]] = [
        {
            "kind": "task_start",
            "ts": run.started_at or run.created_at,
            "goal": _preview(run.goal, limit=500),
            "mode": run.mode.value,
            "workspace_path": str(run.workspace_path or ""),
            "policy": _loop_policy_summary(run),
        }
    ]

    for attempt in run.attempts:
        attempt_call_id = f"{run.run_id}:attempt:{attempt.attempt_index}"
        steps.append(
            {
                "kind": "tool_start",
                "ts": attempt.started_at,
                "tool": "react_attempt",
                "tool_call_id": attempt_call_id,
                "input_preview": _preview(attempt.prompt, limit=500),
                "approval": {},
            }
        )
        steps.append(
            {
                "kind": "loop_attempt",
                "ts": attempt.completed_at or attempt.started_at,
                "attempt_index": attempt.attempt_index,
                "status": attempt.status,
                "success": bool(attempt.success) if attempt.success is not None else None,
                "terminated_reason": attempt.terminated_reason,
                "final_answer_preview": _preview(attempt.final_answer, limit=280),
            }
        )
        steps.append(
            {
                "kind": "tool_end",
                "ts": attempt.completed_at or attempt.started_at,
                "tool": "react_attempt",
                "tool_call_id": attempt_call_id,
                "status": "error" if bool(attempt.error) else "success",
                "is_error": bool(attempt.error),
                "output_preview": _attempt_output_preview(attempt),
            }
        )

        verifier_result = attempt.verifier_result
        if verifier_result is None:
            continue
        verifier_call_id = f"{run.run_id}:attempt:{attempt.attempt_index}:verifier"
        steps.append(
            {
                "kind": "tool_start",
                "ts": attempt.completed_at or attempt.started_at,
                "tool": _verifier_tool_name(verifier_result.profile),
                "tool_call_id": verifier_call_id,
                "input_preview": _preview(
                    f"attempt={attempt.attempt_index} workspace={run.workspace_path or ''}",
                    limit=500,
                ),
                "approval": {},
            }
        )
        steps.append(
            {
                "kind": "verifier_result",
                "ts": verifier_result.checked_at,
                "attempt_index": attempt.attempt_index,
                "profile": verifier_result.profile,
                "verifier_kind": verifier_result.kind,
                "failure_category": verifier_result.failure_category,
                "passed": verifier_result.passed,
                "summary": _preview(verifier_result.summary, limit=280),
                "failed_checks": _failed_check_names(verifier_result),
                "failed_categories": _failed_check_categories(verifier_result),
                "execution_policies": verifier_execution_policies(verifier_result),
            }
        )
        steps.append(
            {
                "kind": "tool_end",
                "ts": verifier_result.checked_at,
                "tool": _verifier_tool_name(verifier_result.profile),
                "tool_call_id": verifier_call_id,
                "status": "success" if verifier_result.passed else "error",
                "is_error": not verifier_result.passed,
                "output_preview": _preview(
                    verifier_result.summary
                    or ", ".join(_failed_check_names(verifier_result))
                    or verifier_result.kind,
                    limit=500,
                ),
            }
        )

    steps.append(
        {
            "kind": "task_event",
            "ts": run.completed_at or run.updated_at,
            "event_type": f"LOOP_RUN_{run.status.value.upper()}",
            "status": run.status.value,
            "reason": _loop_terminal_reason(run),
        }
    )

    fingerprint = _loop_run_replay_fingerprint(steps)
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


def _loop_run_replay_fingerprint(steps: list[dict[str, Any]]) -> str:
    normalized: list[dict[str, Any]] = []
    for step in steps:
        kind = str(step.get("kind") or "")
        item: dict[str, Any] = {"kind": kind}
        if kind in {"tool_start", "tool_end"}:
            item.update(
                {
                    "tool": str(step.get("tool") or ""),
                    "status": str(step.get("status") or ""),
                    "is_error": bool(step.get("is_error")),
                    "input_preview": str(step.get("input_preview") or ""),
                    "output_preview": str(step.get("output_preview") or ""),
                }
            )
            approval = step.get("approval") if isinstance(step.get("approval"), dict) else {}
            item["approval"] = {
                "decision": str(approval.get("decision") or ""),
                "risk_level": str(approval.get("risk_level") or ""),
            }
        elif kind == "task_start":
            policy = step.get("policy") if isinstance(step.get("policy"), dict) else {}
            item.update(
                {
                    "goal": str(step.get("goal") or ""),
                    "mode": str(step.get("mode") or ""),
                    "workspace_path": str(step.get("workspace_path") or ""),
                    "policy": {
                        "max_attempts": int(policy.get("max_attempts") or 0),
                        "max_iterations": int(policy.get("max_iterations") or 0),
                        "verifier_profile": str(policy.get("verifier_profile") or ""),
                        "auto_approve": bool(policy.get("auto_approve")),
                        "sandbox_mode": str(policy.get("sandbox_mode") or ""),
                        "permission_mode": str(policy.get("permission_mode") or ""),
                        "execution_environment": str(policy.get("execution_environment") or ""),
                        "model": str(policy.get("model") or ""),
                    },
                }
            )
        elif kind == "task_event":
            item.update(
                {
                    "event_type": str(step.get("event_type") or ""),
                    "status": str(step.get("status") or ""),
                    "reason": str(step.get("reason") or ""),
                }
            )
        elif kind == "loop_attempt":
            item.update(
                {
                    "attempt_index": int(step.get("attempt_index") or 0),
                    "status": str(step.get("status") or ""),
                    "success": step.get("success"),
                    "terminated_reason": str(step.get("terminated_reason") or ""),
                    "final_answer_preview": str(step.get("final_answer_preview") or ""),
                }
            )
        elif kind == "verifier_result":
            item.update(
                {
                    "attempt_index": int(step.get("attempt_index") or 0),
                    "profile": str(step.get("profile") or ""),
                    "verifier_kind": str(step.get("verifier_kind") or ""),
                    "failure_category": str(step.get("failure_category") or ""),
                    "passed": bool(step.get("passed")),
                    "summary": str(step.get("summary") or ""),
                    "failed_checks": [
                        str(name) for name in step.get("failed_checks") or [] if str(name or "")
                    ],
                    "failed_categories": [
                        str(name) for name in step.get("failed_categories") or [] if str(name or "")
                    ],
                    "execution_policies": [
                        policy
                        for policy in step.get("execution_policies") or []
                        if isinstance(policy, dict)
                    ],
                }
            )
        normalized.append(item)
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_loop_run_replay_case(review: dict[str, Any]) -> dict[str, Any]:
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


def evaluate_loop_run_replay_case(replay_case: dict[str, Any]) -> dict[str, Any]:
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


__all__ = [
    "build_loop_run_findings",
    "build_loop_run_replay",
    "build_loop_run_replay_case",
    "build_loop_run_review_score",
    "evaluate_loop_run_replay_case",
]
