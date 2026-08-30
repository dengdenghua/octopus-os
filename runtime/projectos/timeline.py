"""Project OS process timeline read-model.

Team tasks already expose a persisted process timeline. Project OS runs need
the same operator-facing shape so cowork/project/team execution can be audited
as one employee loop after the original run response is gone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from runtime.projectos.cowork_bridge import full_project_state
from runtime.projectos.store import ProjectStore

_MAX_EVENT_LIMIT = 500
_SUMMARY_LIMIT = 500
_DATA_STRING_LIMIT = 1000
_DATA_LIST_LIMIT = 50
_DATA_DEPTH_LIMIT = 4
_SENSITIVE_OR_RAW_KEYS = {
    "done_outputs",
    "history",
    "output",
    "previous_output",
    "raw_messages",
}


def project_process_timeline(
    project_store: ProjectStore,
    project_id: str,
    *,
    limit: int = 100,
) -> dict[str, Any] | None:
    """Return a compact, persisted Project OS process timeline.

    The timeline intentionally omits raw task outputs. It records status,
    assignments, run/tick summaries, and safe audit payload slices so operators
    can replay the process without leaking large artifacts or model messages.
    """
    state = full_project_state(project_store, project_id)
    if state is None:
        return None

    events = project_store.events_for_project(
        project_id,
        limit=max(1, min(int(limit or 100), _MAX_EVENT_LIMIT)),
    )
    project = state["project"]
    milestones = [
        milestone for milestone in state.get("milestones", []) if isinstance(milestone, dict)
    ]
    tasks_by_ms = state.get("tasks") if isinstance(state.get("tasks"), dict) else {}
    tasks = [
        task
        for milestone_tasks in tasks_by_ms.values()
        if isinstance(milestone_tasks, list)
        for task in milestone_tasks
        if isinstance(task, dict)
    ]
    nodes = [_project_event_node(event, index) for index, event in enumerate(events)]
    nodes.extend(
        _milestone_state_node(milestone, tasks_by_ms, index)
        for index, milestone in enumerate(milestones)
    )
    nodes.extend(_task_state_node(task, index) for index, task in enumerate(tasks))
    nodes = sorted(nodes, key=_timeline_sort_key)
    thread_id = _thread_id_from_events(events)

    return {
        "schema": "echo.projectos.process_timeline.v1",
        "project_id": project.get("id"),
        "thread_id": thread_id,
        "overview": {
            "name": project.get("name") or "",
            "goal": project.get("goal") or "",
            "status": project.get("status") or "",
            "current_ms": project.get("current_ms"),
            "milestone_count": len(milestones),
            "task_count": len(tasks),
            "done_task_count": sum(1 for task in tasks if task.get("status") == "done"),
            "failed_task_count": sum(1 for task in tasks if task.get("status") == "failed"),
            "blocked_task_count": sum(1 for task in tasks if task.get("status") == "blocked"),
            "assigned_agent_count": len(
                {
                    str(task.get("assigned_agent") or "")
                    for task in tasks
                    if str(task.get("assigned_agent") or "").strip()
                }
            ),
            "event_count": len(events),
        },
        "milestones": [_milestone_summary(milestone, tasks_by_ms) for milestone in milestones],
        "events": [_safe_event(event) for event in events],
        "timeline": nodes,
        "safety": {
            "raw_task_outputs_included": False,
            "event_payloads_truncated": True,
            "process_events_persisted": True,
            "process_event_limit": _MAX_EVENT_LIMIT,
        },
    }


def _milestone_summary(
    milestone: dict[str, Any],
    tasks_by_ms: dict[str, Any],
) -> dict[str, Any]:
    ms_id = str(milestone.get("id") or "")
    tasks = tasks_by_ms.get(ms_id) if isinstance(tasks_by_ms, dict) else []
    tasks = tasks if isinstance(tasks, list) else []
    return {
        "id": ms_id,
        "name": milestone.get("name"),
        "status": milestone.get("status"),
        "task_count": len(tasks),
        "done_task_count": sum(
            1 for task in tasks if isinstance(task, dict) and task.get("status") == "done"
        ),
        "assignments": [
            {
                "task_id": task.get("id"),
                "type": task.get("type"),
                "status": task.get("status"),
                "assigned_agent": task.get("assigned_agent"),
                "assigned_role": task.get("assigned_role"),
            }
            for task in tasks
            if isinstance(task, dict)
        ],
    }


def _project_event_node(event: dict[str, Any], index: int) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    kind = str(event.get("kind") or "project.event")
    status = str(
        payload.get("final_status")
        or payload.get("project_status")
        or payload.get("status")
        or "ok"
    )
    return _timeline_node(
        node_id=f"event-{event.get('id') or index}",
        lane=_event_lane(kind),
        kind=kind,
        ts=_event_ts(event),
        title=_event_title(kind),
        status=status,
        severity=_event_severity(kind, status, payload),
        summary=_event_summary(kind, payload),
        data=_safe_data(payload),
    )


def _milestone_state_node(
    milestone: dict[str, Any],
    tasks_by_ms: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    summary = _milestone_summary(milestone, tasks_by_ms)
    return _timeline_node(
        node_id=f"milestone-{summary['id'] or index}",
        lane="milestone",
        kind="milestone_state",
        ts="",
        title=f"Milestone: {summary['name'] or summary['id'] or index}",
        status=str(summary["status"] or ""),
        severity=_status_severity(str(summary["status"] or "")),
        summary=(f"{summary['done_task_count']}/{summary['task_count']} tasks done"),
        data=summary,
    )


def _task_state_node(task: dict[str, Any], index: int) -> dict[str, Any]:
    status = str(task.get("status") or "")
    assigned = str(task.get("assigned_agent") or task.get("assigned_role") or "").strip()
    return _timeline_node(
        node_id=f"task-{task.get('id') or index}",
        lane="task",
        kind="task_state",
        ts="",
        title=f"Task: {task.get('id') or index}",
        status=status,
        severity=_status_severity(status),
        summary=assigned,
        data={
            "task_id": task.get("id"),
            "milestone_id": task.get("milestone_id"),
            "type": task.get("type"),
            "goal": task.get("goal"),
            "assigned_role": task.get("assigned_role"),
            "assigned_agent": task.get("assigned_agent"),
            "attempts": task.get("attempts"),
            "available_actions": task.get("available_actions") or [],
        },
    )


def _timeline_node(
    *,
    node_id: str,
    lane: str,
    kind: str,
    ts: str,
    title: str,
    status: str,
    severity: str,
    summary: str = "",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "lane": lane,
        "kind": kind,
        "ts": ts,
        "title": title,
        "status": status,
        "severity": severity,
        "summary": summary[:_SUMMARY_LIMIT],
        "data": data or {},
    }


def _event_lane(kind: str) -> str:
    if kind.startswith("task."):
        return "task"
    if "milestone" in kind:
        return "milestone"
    if kind.startswith("project."):
        return "project"
    return "timeline"


def _event_title(kind: str) -> str:
    titles = {
        "project.planned": "Project planned",
        "project.run": "Project run",
        "project.run_from_group": "Cowork group run",
        "project.recover": "Project recovered",
        "project.decompose_failed": "Task decomposition failed",
        "project.decompose_empty": "Task decomposition empty",
        "project.gate_failed": "Milestone gate failed",
        "task.intervention": "Task intervention",
        "task.intervention_rejected": "Task intervention rejected",
    }
    return titles.get(kind, kind.replace(".", " ").replace("_", " "))


def _event_severity(kind: str, status: str, payload: dict[str, Any]) -> str:
    if "rejected" in kind or "failed" in kind:
        return "high"
    if str(payload.get("error") or "").strip():
        return "high"
    return _status_severity(status)


def _status_severity(status: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"failed", "failure", "error", "blocked", "rejected"}:
        return "high"
    if normalized in {"cancelled", "running", "planning"}:
        return "medium"
    return "info"


def _event_summary(kind: str, payload: dict[str, Any]) -> str:
    if kind == "project.planned":
        return (
            f"{payload.get('name') or ''} · {len(payload.get('milestone_ids') or [])} milestones"
        ).strip(" ·")
    if kind == "project.run":
        return (
            f"{payload.get('final_status') or payload.get('project_status') or ''} · "
            f"{payload.get('ticks', 0)} ticks"
        ).strip(" ·")
    if kind == "project.run_from_group":
        roster = payload.get("roster") if isinstance(payload.get("roster"), list) else []
        trace = payload.get("trace") if isinstance(payload.get("trace"), dict) else {}
        return (
            f"thread {payload.get('thread_id') or ''} · "
            f"{len(roster)} members · {trace.get('tick_count', 0)} ticks"
        ).strip(" ·")
    events = payload.get("events")
    if isinstance(events, list) and events:
        return ", ".join(str(item) for item in events[:8])
    return str(payload.get("reason") or payload.get("error") or "")[:_SUMMARY_LIMIT]


def _event_ts(event: dict[str, Any]) -> str:
    try:
        created_at = float(event.get("created_at") or 0)
    except (TypeError, ValueError):
        created_at = 0
    if created_at <= 0:
        return ""
    return datetime.fromtimestamp(created_at, UTC).isoformat()


def _safe_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event.get("id"),
        "kind": event.get("kind"),
        "created_at": event.get("created_at"),
        "payload": _safe_data(event.get("payload")),
    }


def _safe_data(value: Any, *, depth: int = 0) -> Any:
    if depth >= _DATA_DEPTH_LIMIT:
        return _scalar_summary(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, child in value.items():
            text_key = str(key)
            if text_key in _SENSITIVE_OR_RAW_KEYS:
                out[text_key] = _scalar_summary(child)
                continue
            out[text_key] = _safe_data(child, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [_safe_data(item, depth=depth + 1) for item in value[:_DATA_LIST_LIMIT]]
    if isinstance(value, str):
        return value[:_DATA_STRING_LIMIT]
    if isinstance(value, int | float | bool) or value is None:
        return value
    return str(value)[:_DATA_STRING_LIMIT]


def _scalar_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return {"omitted": True, "type": "list", "count": len(value)}
    if isinstance(value, dict):
        return {"omitted": True, "type": "dict", "keys": sorted(map(str, value))[:20]}
    text = str(value)
    return {"omitted": True, "type": type(value).__name__, "preview": text[:120]}


def _thread_id_from_events(events: list[dict[str, Any]]) -> str:
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        thread_id = str(payload.get("thread_id") or "").strip()
        if thread_id:
            return thread_id
        trace = payload.get("trace") if isinstance(payload.get("trace"), dict) else {}
        thread_id = str(trace.get("thread_id") or "").strip()
        if thread_id:
            return thread_id
    return ""


def _timeline_sort_key(node: dict[str, Any]) -> tuple[str, str]:
    return (str(node.get("ts") or "9999"), str(node.get("id") or ""))


__all__ = ["project_process_timeline"]
