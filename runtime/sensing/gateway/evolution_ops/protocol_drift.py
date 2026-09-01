"""Protocol drift subsystem for evolution operators."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .utils import (
    _as_dt,
    _iso,
    _journal_events,
    _shorten_text,
    _stable_int_id,
    _trajectory_rows,
    _utcnow,
)

_PROTOCOL_DRIFT_RULES: tuple[dict[str, Any], ...] = (
    {
        "protocol_id": "http_api_contract",
        "markers": (
            "404",
            "not found",
            "http 400",
            "http 404",
            "endpoint",
            "route",
            "/api/",
        ),
        "summary": (
            "HTTP/API contract drift: a route or request shape appears to "
            "no longer match the backend."
        ),
        "suggested_diff": (
            "1. Locate the failing frontend/backend endpoint pair.\n"
            "2. Verify path, method, query/body schema, and response shape.\n"
            "3. Add a regression test for the endpoint contract.\n"
            "4. Update the caller or router, then rerun typecheck and endpoint smoke."
        ),
        "rationale": (
            "Repeated HTTP contract failures usually mean the UI and API changed independently."
        ),
    },
    {
        "protocol_id": "schema_contract",
        "markers": (
            "validationerror",
            "missing field",
            "keyerror",
            "typeerror",
            "unexpected keyword",
            "unexpected field",
            "pydantic",
            "schema",
            "json decode",
            "jsondecodeerror",
        ),
        "summary": (
            "Schema contract drift: a producer and consumer disagree on "
            "required fields or JSON structure."
        ),
        "suggested_diff": (
            "1. Capture the actual payload from the failed step.\n"
            "2. Compare it with the consuming pydantic/TypeScript schema.\n"
            "3. Add a tolerant migration or update the producer contract.\n"
            "4. Cover the old and new shapes in tests."
        ),
        "rationale": (
            "Schema drift commonly appears as missing fields, KeyError, "
            "ValidationError, or JSON parse failures."
        ),
    },
    {
        "protocol_id": "sse_stream_contract",
        "markers": (
            "sse",
            "eventsource",
            "stream failed",
            "stream error",
            "chunk",
            "delta",
            "event:",
        ),
        "summary": (
            "Streaming/SSE contract drift: stream events, chunk format, or "
            "error handling appear out of sync."
        ),
        "suggested_diff": (
            "1. Record the failing stream event sequence.\n"
            "2. Align event names and payload fields between server and client.\n"
            "3. Ensure terminal/error events are emitted consistently.\n"
            "4. Add an SSE parser regression test."
        ),
        "rationale": (
            "SSE failures are easy to miss because they often surface as "
            "generic stream errors in the UI."
        ),
    },
    {
        "protocol_id": "tool_call_contract",
        "markers": (
            "tool call",
            "toolcall",
            "args_template",
            "template resolution",
            "templateresolutionerror",
            "argument",
            "parameter",
            "call_agent",
            "skill args",
        ),
        "summary": (
            "Tool-call contract drift: a skill, planner, or template chain "
            "disagrees about arguments."
        ),
        "suggested_diff": (
            "1. Compare planned args with the target skill signature.\n"
            "2. Preserve args_template data dependencies where needed.\n"
            "3. Add validation before execution and a focused skill test.\n"
            "4. Regenerate affected forged skills if templates changed."
        ),
        "rationale": (
            "Planner/skill argument drift causes TypeError, unresolved "
            "templates, or failed composite skills."
        ),
    },
)


def _protocol_drift_rows(
    journal: Any,
    *,
    acknowledged: bool | None = None,
) -> list[dict[str, Any]]:
    decisions = _protocol_drift_decision_map(journal)
    clusters = _protocol_drift_clusters(journal)
    rows: list[dict[str, Any]] = []
    for key, cluster in clusters.items():
        drift_id = _stable_int_id(f"drift:{key}")
        is_ack = decisions.get(drift_id, {}).get("status") == "acknowledged"
        if acknowledged is not None and acknowledged != is_ack:
            continue
        rule = cluster["rule"]
        rows.append(
            {
                "id": drift_id,
                "protocol_id": rule["protocol_id"],
                "detected_at": _iso(cluster["last_seen"]),
                "summary": (
                    f"{rule['summary']} Observed {len(cluster['task_ids'])} "
                    f"related failure(s). Example: {cluster['examples'][0]}"
                ),
                "acknowledged": is_ack,
                "failure_count": len(cluster["task_ids"]),
                "examples": cluster["examples"],
            }
        )
    rows.sort(
        key=lambda row: (
            bool(row["acknowledged"]),
            -int(row["failure_count"]),
            str(row["protocol_id"]),
        )
    )
    return rows


def _protocol_repair_rows(
    journal: Any,
    *,
    status: str | None = None,
) -> list[dict[str, Any]]:
    drifts = _protocol_drift_rows(journal, acknowledged=None)
    rows: list[dict[str, Any]] = []
    by_protocol = {rule["protocol_id"]: rule for rule in _PROTOCOL_DRIFT_RULES}
    for drift in drifts:
        current_status = "acknowledged" if drift["acknowledged"] else "pending"
        if status and status != current_status:
            continue
        rule = by_protocol.get(str(drift["protocol_id"]), {})
        rows.append(
            {
                "id": _stable_int_id(f"repair:{drift['id']}"),
                "drift_event_id": drift["id"],
                "protocol_id": drift["protocol_id"],
                "created_at": drift["detected_at"],
                "suggested_diff": rule.get("suggested_diff", ""),
                "rationale": rule.get("rationale", drift["summary"]),
                "status": current_status,
            }
        )
    rows.sort(key=lambda row: (str(row["status"]) != "pending", str(row["protocol_id"])))
    return rows


def _protocol_drift_clusters(journal: Any) -> dict[str, dict[str, Any]]:
    clusters: dict[str, dict[str, Any]] = {}
    for event, traj in _trajectory_rows(journal):
        fallback_ts = (
            _as_dt(getattr(traj, "completed_at", None))
            or _as_dt(getattr(event, "ts", None))
            or _utcnow()
        )
        task_id = str(getattr(traj, "task_id", "") or "")
        for step in getattr(traj, "steps", []) or []:
            if bool(getattr(step, "success", False)):
                continue
            text = _protocol_failure_text(step)
            lowered = text.lower()
            if not lowered:
                continue
            for rule in _PROTOCOL_DRIFT_RULES:
                if not any(marker in lowered for marker in rule["markers"]):
                    continue
                key = f"{rule['protocol_id']}:{_protocol_subject(lowered)}"
                cluster = clusters.setdefault(
                    key,
                    {
                        "rule": rule,
                        "task_ids": set(),
                        "examples": [],
                        "last_seen": fallback_ts,
                    },
                )
                if task_id:
                    cluster["task_ids"].add(task_id)
                else:
                    cluster["task_ids"].add(f"event:{len(cluster['task_ids'])}")
                if len(cluster["examples"]) < 3:
                    cluster["examples"].append(_shorten_text(text, 180))
                if fallback_ts > cluster["last_seen"]:
                    cluster["last_seen"] = fallback_ts
    return clusters


def _protocol_failure_text(step: Any) -> str:
    action = getattr(step, "action", None)
    result = getattr(step, "result", None)
    parts: list[str] = []
    parts.append(str(getattr(action, "sucker_id", "") or ""))
    import contextlib

    with contextlib.suppress(Exception):
        parts.append(str(getattr(action, "args", {}) or {}))
    for attr in ("status", "error_type", "output", "exit_code"):
        parts.append(str(getattr(result, attr, "") or ""))
    with contextlib.suppress(Exception):
        parts.extend(str(tag) for tag in (getattr(result, "stderr_tags", []) or []))
    return " ".join(part for part in parts if part).strip()


def _protocol_subject(text: str) -> str:
    for marker in ("/api/", "api/", "sse", "eventsource", "schema", "tool"):
        if marker in text:
            start = max(0, text.find(marker))
            return _shorten_text(text[start : start + 80], 80)
    return _shorten_text(text, 80)


def _protocol_drift_decision_map(journal: Any) -> dict[int, dict[str, Any]]:
    decisions: dict[int, tuple[datetime, dict[str, Any]]] = {}
    if journal is None:
        return {}
    try:
        events = list(journal.read_by_type("protocol_drift_decision"))
    except (AttributeError, TypeError, OSError):
        events = [
            event
            for event in _journal_events(journal)
            if getattr(event, "event_type", "") == "protocol_drift_decision"
        ]
    for event in events:
        drift_id = int(getattr(event, "drift_id", 0) or 0)
        status = str(getattr(event, "status", "") or "").strip()
        if drift_id <= 0 or not status:
            continue
        ts = _as_dt(getattr(event, "ts", None)) or _utcnow()
        payload = {
            "status": status,
            "protocol_id": getattr(event, "protocol_id", ""),
            "reason": getattr(event, "reason", ""),
        }
        existing = decisions.get(drift_id)
        if existing is None or ts >= existing[0]:
            decisions[drift_id] = (ts, payload)
    return {key: payload for key, (_ts, payload) in decisions.items()}


def _write_protocol_drift_decision(
    journal: Any,
    *,
    drift_id: int,
    protocol_id: str,
    status: str,
    reason: str = "",
    details: dict[str, Any] | None = None,
) -> bool:
    if journal is None:
        return False
    try:
        if hasattr(journal, "write_protocol_drift_decision"):
            journal.write_protocol_drift_decision(
                drift_id=drift_id,
                protocol_id=protocol_id,
                status=status,
                reason=reason,
                details=details or {},
            )
        else:
            from runtime.memory.journal import ProtocolDriftDecisionEvent

            journal.write(
                ProtocolDriftDecisionEvent(
                    drift_id=drift_id,
                    protocol_id=protocol_id,
                    status=status,
                    reason=reason,
                    details=details or {},
                )
            )
        return True
    except (AttributeError, TypeError, OSError):
        return False
