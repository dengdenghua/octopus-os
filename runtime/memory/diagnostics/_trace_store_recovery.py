"""Checkpoint recovery and sanitization helpers for the trace store."""

from __future__ import annotations

from typing import Any

from ._trace_store_models import (
    _clean_str,
    _render_preview,
)


def _latest_checkpoint_review_summary(
    checkpoints: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not checkpoints:
        return None
    latest = max(
        checkpoints,
        key=lambda row: (int(row.get("iteration") or 0), int(row.get("id") or 0)),
    )
    return _checkpoint_review_summary(latest)


def _checkpoint_review_summary(checkpoint: dict[str, Any]) -> dict[str, Any]:
    from runtime.core.cerebrum.checkpoint_integrity import validate_trace_checkpoint

    hints = _recovery_hints(checkpoint)
    return {
        "id": checkpoint.get("id"),
        "task_id": checkpoint.get("task_id"),
        "thread_id": checkpoint.get("thread_id"),
        "agent_id": checkpoint.get("agent_id"),
        "type": checkpoint.get("checkpoint_type"),
        "iteration": int(checkpoint.get("iteration") or 0),
        "timestamp": checkpoint.get("ts"),
        "summary": str(checkpoint.get("summary") or ""),
        "recovery_hints": {
            "phase": hints["phase"] or None,
            "progress": hints["progress"] or None,
            "message_count": hints["messages"],
            "step_count": hints["steps"],
            "working_set": hints["working_set"],
            "recent_tool_calls": hints["recent_tool_calls"],
        },
        "integrity": validate_trace_checkpoint(checkpoint).to_dict(),
        "safety": {
            "raw_state_included": False,
            "raw_message_snapshots_included": False,
        },
    }


def _task_run_resume_summary(run: dict[str, Any]) -> dict[str, Any]:
    latest = (
        run.get("latest_checkpoint") if isinstance(run.get("latest_checkpoint"), dict) else None
    )
    integrity = latest.get("integrity") if isinstance(latest, dict) else {}
    return {
        "available": bool(isinstance(integrity, dict) and integrity.get("resume_safe") is True),
        "source": "trace_store" if latest else None,
        "latest_checkpoint": latest,
        "safety": {
            "raw_state_included": False,
            "raw_message_snapshots_included": False,
        },
    }


def _task_run_learning_candidates(
    run: dict[str, Any],
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for finding in findings:
        ftype = str(finding.get("type") or "")
        if ftype == "tool_error":
            tool = (
                (finding.get("evidence") or {}).get("tool")
                if isinstance(finding.get("evidence"), dict)
                else ""
            ) or "tool"
            out.append(
                {
                    "kind": "failure_pattern",
                    "priority": "P0",
                    "memory_bucket": "experience",
                    "title": f"Tool failure pattern: {tool}",
                    "text": f"When `{tool}` fails in task `{run.get('task_id')}`, add preflight validation or fallback planning before retrying.",
                }
            )
        elif ftype == "permission_friction":
            tool = (
                (finding.get("evidence") or {}).get("tool")
                if isinstance(finding.get("evidence"), dict)
                else ""
            ) or "tool"
            out.append(
                {
                    "kind": "permission_pattern",
                    "priority": "P1",
                    "memory_bucket": "project_knowledge",
                    "title": f"Permission friction: {tool}",
                    "text": f"Review whether `{tool}` should be governed by a static allow/deny rule or replaced by a safer workflow.",
                }
            )
        elif ftype == "success_pattern":
            out.append(
                {
                    "kind": "success_pattern",
                    "priority": "P2",
                    "memory_bucket": "experience",
                    "title": "Positive tool-use run",
                    "text": f"Task `{run.get('task_id')}` completed using {', '.join(run.get('tool_names') or [])}; consider using it as a positive replay example.",
                }
            )
    return out


def _task_run_backlog_candidates(
    run: dict[str, Any],
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if any(f.get("type") in {"terminal_status", "tool_error"} for f in findings):
        out.append(
            {
                "priority": "P0",
                "experiment": "Create deterministic replay case",
                "hypothesis": "A replay case from this TaskRun will prevent repeating the same failure mode.",
                "minimal_implementation": "Convert replay.steps into a fixture and assert expected planning/tool behavior.",
                "validation_metric": "Replay passes before prompt/tool changes are accepted.",
            }
        )
    if any(f.get("type") == "permission_friction" for f in findings):
        out.append(
            {
                "priority": "P1",
                "experiment": "Permission policy tuning",
                "hypothesis": "Explicit policy or safer alternative tools reduce repeated approval friction.",
                "minimal_implementation": "Review trust_gateway evidence and add one narrow rule or planning constraint.",
                "validation_metric": "Future runs show fewer rejected approvals for the same tool category.",
            }
        )
    if any(f.get("type") == "success_pattern" for f in findings):
        out.append(
            {
                "priority": "P2",
                "experiment": "Positive replay seed",
                "hypothesis": "Successful TaskRuns can protect useful behavior during self-evolution.",
                "minimal_implementation": "Add this run to a positive replay dataset with its tool sequence and outcome.",
                "validation_metric": "Candidate prompt/tool changes preserve the success pattern.",
            }
        )
    return out


def _approval_replay_fragment(approval: dict[str, Any] | None) -> dict[str, Any] | None:
    if not approval:
        return None
    return {
        "decision": approval.get("decision"),
        "reason": approval.get("reason") or "",
        "risk_level": _approval_risk_level(approval),
        "source": (_trust_gateway_from_approval(approval) or {}).get("source"),
    }


def _trust_gateway_from_approval(row: dict[str, Any]) -> dict[str, Any] | None:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return None
    trust = metadata.get("trust_gateway")
    return trust if isinstance(trust, dict) else None


def _approval_risk_level(row: dict[str, Any]) -> str:
    trust = _trust_gateway_from_approval(row) or {}
    risk = trust.get("risk") if isinstance(trust.get("risk"), dict) else {}
    level = risk.get("level") if isinstance(risk, dict) else None
    return str(level or "").lower()


def _truncate(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _state_str(state: dict[str, Any], key: str) -> str:
    value = state.get(key)
    return value if isinstance(value, str) else ""


def _state_len(state: dict[str, Any], key: str) -> int:
    value = state.get(key)
    return len(value) if isinstance(value, list) else 0


def _recent_tool_calls_from_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    steps = state.get("steps_snapshot")
    if not isinstance(steps, list):
        return []
    try:
        from runtime.core.cerebrum.react_parsing import _parse_action
    except Exception:  # noqa: BLE001
        _parse_action = None
    out: list[dict[str, Any]] = []
    for step in steps[-8:]:
        if not isinstance(step, dict):
            continue
        action = step.get("action")
        if not isinstance(action, str) or not action.strip():
            continue
        parsed = _parse_action(action) if _parse_action is not None else None
        if parsed is None:
            continue
        tool, args = parsed
        out.append(
            {
                "iteration": int(step.get("iteration") or 0),
                "tool": str(tool or ""),
                "input_preview": _sanitize_preview_text(_render_preview(args), 240),
                "observation_preview": _sanitize_preview_text(
                    _render_preview(step.get("observation")),
                    280,
                ),
            }
        )
    return out[-5:]


def _sanitize_recent_tool_calls(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        tool = _clean_str(item.get("tool"))
        if not tool:
            continue
        out.append(
            {
                "iteration": int(item.get("iteration") or 0),
                "tool": tool,
                "input_preview": _sanitize_preview_text(item.get("input_preview"), 240),
                "observation_preview": _sanitize_preview_text(
                    item.get("observation_preview"),
                    280,
                ),
            }
        )
        if len(out) >= 8:
            break
    return out


def _sanitize_preview_text(value: Any, limit: int) -> str:
    return _truncate(_redact_preview_text(value), limit)


def _redact_preview_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return text
    try:
        from runtime.platform.observability.redactor import redact_text

        return redact_text(text)
    except Exception:  # pragma: no cover - trace reads must stay best-effort
        return text


def _recovery_hints(checkpoint: dict[str, Any]) -> dict[str, Any]:
    state = checkpoint.get("state")
    state = state if isinstance(state, dict) else {}
    working_set = state.get("working_set_snapshot")
    working_set = working_set if isinstance(working_set, list) else []
    paths: list[str] = []
    for item in working_set:
        if isinstance(item, str):
            path = item
        elif isinstance(item, dict):
            path = str(item.get("path") or "")
        else:
            path = ""
        if path:
            paths.append(path)
        if len(paths) >= 4:
            break
    return {
        "phase": _state_str(state, "current_phase"),
        "progress": _state_str(state, "progress_summary") or str(checkpoint.get("summary") or ""),
        "messages": _state_len(state, "messages_snapshot"),
        "steps": _state_len(state, "steps_snapshot"),
        "working_set": paths,
        "recent_tool_calls": _recent_tool_calls_from_state(state),
    }


def _resume_proposal_from_checkpoint(checkpoint: dict[str, Any]) -> dict[str, Any]:
    from runtime.core.cerebrum.checkpoint_integrity import validate_trace_checkpoint

    hints = _recovery_hints(checkpoint)
    integrity = validate_trace_checkpoint(checkpoint)
    working_set_count = len(hints["working_set"])
    title = f"Resume from {hints['phase']}" if hints["phase"] else "Resume from latest checkpoint"
    steps = [
        (
            f"Restore the agent into phase {hints['phase']}."
            if hints["phase"]
            else "Restore the agent into the latest recorded phase."
        ),
        f"Continue from iteration {int(checkpoint.get('iteration') or 0) + 1}.",
        (
            f"Rehydrate {working_set_count} working-set file"
            f"{'' if working_set_count == 1 else 's'} and ignore raw message snapshots."
        ),
        (
            f"Use the last progress summary: {hints['progress']}"
            if hints["progress"]
            else "Review the latest progress summary before resuming."
        ),
    ]
    return {
        "checkpoint": {
            "id": checkpoint["id"],
            "task_id": checkpoint["task_id"],
            "thread_id": checkpoint.get("thread_id"),
            "agent_id": checkpoint.get("agent_id"),
            "type": checkpoint["checkpoint_type"],
            "iteration": checkpoint["iteration"],
            "timestamp": checkpoint["ts"],
        },
        "recovery_hints": {
            "phase": hints["phase"] or None,
            "progress": hints["progress"] or None,
            "message_count": hints["messages"],
            "step_count": hints["steps"],
            "working_set": hints["working_set"],
            "recent_tool_calls": hints["recent_tool_calls"],
        },
        "resume_plan": {
            "title": title,
            "steps": steps,
        },
        "safety": {
            "raw_state_included": False,
            "raw_message_snapshots_included": False,
            "integrity": integrity.to_dict(),
        },
    }


def _sanitize_resume_intent(intent: Any) -> dict[str, Any]:
    raw = intent if isinstance(intent, dict) else {}
    safety = raw.get("safety") if isinstance(raw.get("safety"), dict) else {}
    return {
        "schema": "echo.resume_intent.v1",
        "requires_confirmation": bool(raw.get("requires_confirmation", False)),
        "confirmed": bool(raw.get("confirmed", False)),
        "source": _clean_str(raw.get("source")) or "resume_proposal_block",
        "checkpoint_id": int(raw.get("checkpoint_id") or 0),
        "task_id": _clean_str(raw.get("task_id")) or None,
        "checkpoint_type": _clean_str(raw.get("checkpoint_type")) or "unknown",
        "iteration": int(raw.get("iteration") or 0),
        "continue_from_iteration": int(raw.get("continue_from_iteration") or 0),
        "phase": _clean_str(raw.get("phase")) or None,
        "working_set": [
            str(path).strip()
            for path in (raw.get("working_set") if isinstance(raw.get("working_set"), list) else [])
            if isinstance(path, str) and path.strip()
        ][:32],
        "recent_tool_calls": _sanitize_recent_tool_calls(raw.get("recent_tool_calls")),
        "safety": {
            "raw_state_included": bool(safety.get("raw_state_included") is True),
            "raw_message_snapshots_included": bool(
                safety.get("raw_message_snapshots_included") is True,
            ),
        },
    }


__all__ = [name for name in globals() if name.startswith("_") and not name.startswith("__")]
