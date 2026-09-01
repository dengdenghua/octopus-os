"""Operator-facing timeline assembled from TaskRun review artifacts."""

from __future__ import annotations

from collections import Counter
from typing import Any

from runtime.safety.approval.approval_gate import approval_action_for_tool

_SCHEMA = "echo.process_timeline.v1"
_LANE_ORDER = {
    "execution": 0,
    "permission": 1,
    "verification": 2,
    "review": 3,
    "learning": 4,
}
_SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


def build_task_run_process_timeline(
    *,
    task_run: dict[str, Any],
    review: dict[str, Any],
    approvals: list[dict[str, Any]] | None = None,
    experience_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a compact process timeline for one TaskRun."""

    approval_rows = list(approvals or [])
    experience_rows = list(experience_records or [])
    diagnostic_nodes = _post_write_diagnostic_nodes(review)
    timeline: list[dict[str, Any]] = []
    timeline.extend(_execution_nodes(review))
    timeline.extend(_approval_nodes(approval_rows))
    timeline.extend(diagnostic_nodes)
    timeline.extend(_review_nodes(review, task_run))
    timeline.extend(_candidate_nodes(review, task_run))
    timeline.extend(_experience_nodes(experience_rows))
    timeline = sorted(timeline, key=_node_sort_key)
    return {
        "schema": _SCHEMA,
        "task_id": task_run.get("task_id"),
        "thread_id": task_run.get("thread_id"),
        "turn_id": task_run.get("turn_id"),
        "agent_id": task_run.get("agent_id"),
        "overview": _overview(
            task_run,
            review,
            approval_rows,
            experience_rows,
            diagnostic_nodes,
        ),
        "capabilities": _capability_summary(task_run, approval_rows),
        "timeline": timeline,
        "safety": {
            "raw_messages_included": False,
            "tool_outputs_truncated": True,
            "experience_records_are_summaries": True,
        },
    }


def _execution_nodes(review: dict[str, Any]) -> list[dict[str, Any]]:
    replay = review.get("replay") if isinstance(review.get("replay"), dict) else {}
    steps = replay.get("steps") if isinstance(replay.get("steps"), list) else []
    nodes: list[dict[str, Any]] = []
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        kind = str(step.get("kind") or "event")
        status = str(step.get("status") or "")
        is_error = bool(step.get("is_error"))
        tool = _clean(step.get("tool"))
        nodes.append(
            {
                "id": f"exec-{idx}",
                "lane": "execution",
                "kind": kind,
                "ts": step.get("ts"),
                "title": _execution_title(kind, tool, status, is_error),
                "status": status or ("error" if is_error else "ok"),
                "severity": "high" if is_error else "info",
                "tool": tool or None,
                "summary": _node_summary(step),
                "data": _safe_step_data(step),
            }
        )
    return nodes


def _approval_nodes(approvals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for idx, row in enumerate(approvals):
        trust = _trust(row)
        risk = trust.get("risk") if isinstance(trust.get("risk"), dict) else {}
        risk_level = _clean(risk.get("level")) or _default_risk_level(row.get("tool_name"))
        decision = _clean(row.get("decision")) or "unknown"
        tool = _clean(row.get("tool_name")) or "tool"
        nodes.append(
            {
                "id": f"approval-{row.get('id') or idx}",
                "lane": "permission",
                "kind": "approval",
                "ts": row.get("decided_at") or row.get("requested_at"),
                "title": f"Permission {decision}: {tool}",
                "status": decision,
                "severity": _severity_for_approval(decision, risk_level),
                "tool": tool,
                "summary": _clean(row.get("reason")) or _clean(trust.get("reason")),
                "data": {
                    "tool_call_id": row.get("tool_call_id"),
                    "risk_level": risk_level,
                    "risk_categories": risk.get("categories") if isinstance(risk, dict) else [],
                    "trust_source": trust.get("source"),
                    "static_decision": trust.get("static_decision"),
                },
            }
        )
    return nodes


def _post_write_diagnostic_nodes(review: dict[str, Any]) -> list[dict[str, Any]]:
    replay = review.get("replay") if isinstance(review.get("replay"), dict) else {}
    steps = replay.get("steps") if isinstance(replay.get("steps"), list) else []
    nodes: list[dict[str, Any]] = []
    for idx, step in enumerate(steps):
        if not isinstance(step, dict) or step.get("kind") != "tool_end":
            continue
        output = str(step.get("output_preview") or "")
        if not _looks_like_post_write_diagnostic(output):
            continue
        status = _post_write_diagnostic_status(output)
        nodes.append(
            {
                "id": f"post-write-diagnostic-{idx}",
                "lane": "verification",
                "kind": "post_write_diagnostic",
                "ts": step.get("ts"),
                "title": f"Post-write diagnostic {status}",
                "status": status,
                "severity": "high" if status == "failed" else "info",
                "tool": _clean(step.get("tool")) or None,
                "summary": _post_write_diagnostic_summary(output),
                "data": {
                    "tool_call_id": step.get("tool_call_id"),
                    "output_preview": output[:500],
                },
            }
        )
    return nodes


def _review_nodes(
    review: dict[str, Any],
    task_run: dict[str, Any],
) -> list[dict[str, Any]]:
    findings = review.get("findings") if isinstance(review.get("findings"), list) else []
    ts = task_run.get("completed_at") or task_run.get("updated_at")
    nodes: list[dict[str, Any]] = []
    for idx, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue
        severity = _clean(finding.get("severity")) or "info"
        nodes.append(
            {
                "id": f"finding-{idx}",
                "lane": "review",
                "kind": _clean(finding.get("type")) or "finding",
                "ts": ts,
                "title": _clean(finding.get("title")) or "Review finding",
                "status": "open",
                "severity": severity,
                "tool": _finding_tool(finding),
                "summary": _clean(finding.get("recommendation")),
                "data": {
                    "evidence": finding.get("evidence")
                    if isinstance(finding.get("evidence"), dict)
                    else {},
                },
            }
        )
    return nodes


def _candidate_nodes(
    review: dict[str, Any],
    task_run: dict[str, Any],
) -> list[dict[str, Any]]:
    ts = task_run.get("completed_at") or task_run.get("updated_at")
    nodes: list[dict[str, Any]] = []
    for idx, item in enumerate(review.get("learning_candidates") or []):
        if not isinstance(item, dict):
            continue
        nodes.append(
            {
                "id": f"learning-{idx}",
                "lane": "learning",
                "kind": _clean(item.get("kind")) or "learning_candidate",
                "ts": ts,
                "title": _clean(item.get("title")) or "Learning candidate",
                "status": "candidate",
                "severity": _severity_for_priority(item.get("priority")),
                "tool": None,
                "summary": _clean(item.get("text")),
                "data": {
                    "priority": _clean(item.get("priority")) or "P2",
                    "memory_bucket": _clean(item.get("memory_bucket")) or "experience",
                },
            }
        )
    for idx, item in enumerate(review.get("backlog_candidates") or []):
        if not isinstance(item, dict):
            continue
        nodes.append(
            {
                "id": f"backlog-{idx}",
                "lane": "learning",
                "kind": "backlog_candidate",
                "ts": ts,
                "title": _clean(item.get("experiment")) or "Experiment candidate",
                "status": "candidate",
                "severity": _severity_for_priority(item.get("priority")),
                "tool": None,
                "summary": _clean(item.get("hypothesis")),
                "data": {
                    "priority": _clean(item.get("priority")) or "P2",
                    "minimal_implementation": _clean(item.get("minimal_implementation")),
                    "validation_metric": _clean(item.get("validation_metric")),
                },
            }
        )
    return nodes


def _experience_nodes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for idx, row in enumerate(records):
        nodes.append(
            {
                "id": f"experience-{row.get('id') or idx}",
                "lane": "learning",
                "kind": "experience_record",
                "ts": row.get("last_seen_at") or row.get("created_at"),
                "title": _clean(row.get("title")) or "Experience record",
                "status": _clean(row.get("status")) or "active",
                "severity": _severity_for_priority(row.get("priority")),
                "tool": None,
                "summary": _clean(row.get("text")),
                "data": {
                    "record_id": row.get("id"),
                    "priority": row.get("priority"),
                    "memory_bucket": row.get("memory_bucket"),
                    "occurrences": row.get("occurrences"),
                },
            }
        )
    return nodes


def _overview(
    task_run: dict[str, Any],
    review: dict[str, Any],
    approvals: list[dict[str, Any]],
    experience_records: list[dict[str, Any]],
    post_write_diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    severity_counts = Counter()
    for finding in review.get("findings") or []:
        if isinstance(finding, dict):
            severity_counts[_clean(finding.get("severity")) or "info"] += 1
    return {
        "title": task_run.get("title") or "",
        "goal": task_run.get("goal") or "",
        "mode": task_run.get("mode") or "",
        "status": task_run.get("status") or review.get("status"),
        "score": review.get("score"),
        "score_reasons": review.get("score_reasons") or [],
        "started_at": task_run.get("started_at"),
        "completed_at": task_run.get("completed_at"),
        "updated_at": task_run.get("updated_at"),
        "tool_calls_started": task_run.get("tool_calls_started") or 0,
        "tool_calls_finished": task_run.get("tool_calls_finished") or 0,
        "tool_errors": task_run.get("tool_errors") or 0,
        "approval_count": len(approvals),
        "experience_record_count": len(experience_records),
        "post_write_diagnostic_count": len(post_write_diagnostics),
        "finding_counts": dict(sorted(severity_counts.items())),
        "token_totals": task_run.get("token_totals") or {},
    }


def _capability_summary(
    task_run: dict[str, Any],
    approvals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tool_names = {_clean(tool) for tool in task_run.get("tool_names") or [] if _clean(tool)}
    for row in approvals:
        tool = _clean(row.get("tool_name"))
        if tool:
            tool_names.add(tool)
    out: list[dict[str, Any]] = []
    for tool in sorted(tool_names):
        approval = next(
            (_trust(row) for row in approvals if _clean(row.get("tool_name")) == tool), {}
        )
        risk = approval.get("risk") if isinstance(approval.get("risk"), dict) else {}
        if not risk:
            default_risk, action, policy = approval_action_for_tool(tool)
            risk = default_risk.to_dict()
            trust_source = "risk_policy"
            default_action = action
            risk_policy = policy.to_dict()
        else:
            trust_source = approval.get("source")
            default_action = approval.get("action")
            risk_policy = approval.get("risk_policy")
        out.append(
            {
                "tool": tool,
                "risk": {
                    "level": risk.get("level"),
                    "categories": risk.get("categories") or [],
                    "reason": risk.get("reason") or "",
                    "requires_approval": bool(risk.get("requires_approval")),
                    "default_action": default_action,
                    "policy": risk_policy or {},
                    "source": trust_source,
                },
            }
        )
    return out


def _trust(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    trust = metadata.get("trust_gateway")
    return trust if isinstance(trust, dict) else {}


def _node_sort_key(row: dict[str, Any]) -> tuple[str, int, int, str]:
    return (
        _clean(row.get("ts")),
        _LANE_ORDER.get(_clean(row.get("lane")), 99),
        _SEVERITY_ORDER.get(_clean(row.get("severity")), 9),
        _clean(row.get("id")),
    )


def _execution_title(kind: str, tool: str, status: str, is_error: bool) -> str:
    if kind == "task_start":
        return "Task started"
    if kind == "tool_start":
        return f"Tool started: {tool or 'tool'}"
    if kind == "tool_end":
        label = "failed" if is_error else (status or "finished")
        return f"Tool {label}: {tool or 'tool'}"
    if kind == "task_event":
        return f"Task event: {status or 'event'}"
    return kind.replace("_", " ").title()


def _node_summary(step: dict[str, Any]) -> str:
    for key in ("reason", "input_preview", "output_preview", "goal", "mode"):
        value = _clean(step.get(key))
        if value:
            return value
    return ""


def _safe_step_data(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": step.get("event_type"),
        "tool_call_id": step.get("tool_call_id"),
        "input_preview": step.get("input_preview"),
        "output_preview": step.get("output_preview"),
        "approval": step.get("approval"),
    }


def _looks_like_post_write_diagnostic(output: str) -> bool:
    markers = (
        "[post-write diagnostics]",
        "[自动诊断结果]",
        "ruff diagnostics",
        "eslint diagnostics",
    )
    return any(marker in output for marker in markers)


def _post_write_diagnostic_status(output: str) -> str:
    lowered = output.lower()
    if "ruff diagnostics" in lowered or "eslint diagnostics" in lowered:
        return "failed"
    if "failed" in lowered or "error" in lowered:
        return "failed"
    if "passed" in lowered:
        return "passed"
    return "recorded"


def _post_write_diagnostic_summary(output: str) -> str:
    after_marker = output.split("[post-write diagnostics]", 1)[-1]
    for line in after_marker.splitlines():
        cleaned = _clean(line)
        if cleaned and cleaned not in {"[post-write diagnostics]", "[自动诊断结果]"}:
            return cleaned[:240]
    return _clean(output)[:240]


def _finding_tool(finding: dict[str, Any]) -> str | None:
    evidence = finding.get("evidence")
    if isinstance(evidence, dict):
        tool = _clean(evidence.get("tool"))
        if tool:
            return tool
    return None


def _severity_for_approval(decision: str, risk_level: str) -> str:
    if decision in {"rejected", "timeout", "error"}:
        return "high"
    if risk_level in {"critical", "high"}:
        return "medium"
    return "info"


def _severity_for_priority(value: Any) -> str:
    priority = _clean(value).upper()
    if priority == "P0":
        return "high"
    if priority == "P1":
        return "medium"
    return "info"


def _default_risk_level(tool_name: Any) -> str:
    risk, _, _ = approval_action_for_tool(_clean(tool_name))
    return risk.level


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


__all__ = ["build_task_run_process_timeline"]
