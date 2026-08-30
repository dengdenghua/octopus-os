"""Turn collaboration-room messages into authoritative Project OS objects.

The chat transcript is the coordination timeline, not a second project store.
Every action in this module writes Project OS first and only then enriches the
source message / collaboration read projection.  Deterministic action, task,
event, and system-card ids make retries safe for browser/network clients.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from runtime.memory.cowork._collaboration_project_actions import (
    ProjectMessageProjectionStale,
)
from runtime.memory.cowork.ids import optional_cowork_id, require_cowork_id
from runtime.projectos.model import ROLE_FOR_TASK, Task
from runtime.projectos.store import ProjectBindingChangedError

_ACTION_ALIASES = {
    "link_milestone": "link_milestone",
    "create_item": "create_item",
    "create_task": "create_item",
    "create_project_task": "create_item",
    "record_decision": "record_decision",
    "publish_artifact": "publish_artifact",
}
_TASK_TYPES = frozenset({"design", "code", "research", "analysis", "review"})
_PRIORITIES = frozenset({"P0", "P1", "P2", "P3"})


class MessageProjectActionError(ValueError):
    """Expected API error raised while applying a message action."""

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status_code = int(status_code)
        self.detail = detail


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _source(message: dict[str, Any], *, thread_id: str, room_id: str) -> dict[str, Any]:
    raw_metadata = message.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    return {
        "schema": "echo.projectos.message_source.v1",
        "thread_id": thread_id,
        "room_id": room_id,
        "message_seq": int(message.get("seq") or 0),
        "source_message_id": str(metadata.get("source_message_id") or ""),
        "participant_id": str(message.get("participant_id") or ""),
        "display_name": str(message.get("display_name") or ""),
        "text": str(message.get("text") or ""),
    }


def _binding_changed(thread_id: str, project_id: str, generation: int) -> MessageProjectActionError:
    return MessageProjectActionError(
        409,
        {
            "code": "PROJECT_BINDING_CHANGED",
            "message": "thread project binding changed while the message action was applied",
            "thread_id": thread_id,
            "project_id": project_id,
            "binding_generation": generation,
        },
    )


def _bound_project(project_store: Any, thread_id: str, requested_id: str) -> tuple[Any, int]:
    project, generation = project_store.binding_snapshot(thread_id)
    if project is None:
        raise MessageProjectActionError(
            409,
            "collaboration session is not bound to a Project OS project",
        )
    if requested_id and requested_id != project.id:
        raise MessageProjectActionError(409, "requested project is not bound to this session")
    return project, generation


def _project_milestone(project_store: Any, project: Any, milestone_id: str) -> Any:
    safe_id = optional_cowork_id(milestone_id, label="milestone_id")
    if not safe_id:
        raise MessageProjectActionError(400, "milestone_id is required for this action")
    milestone = project_store.get_milestone(safe_id)
    if milestone is None or safe_id not in set(project.milestone_ids):
        raise MessageProjectActionError(404, "milestone not found in the bound project")
    return milestone


def _commit_source_action(
    project_store: Any,
    project_id: str,
    *,
    event_id: str,
    kind: str,
    payload: dict[str, Any],
    expected_thread_id: str,
    expected_binding_generation: int,
    task: Task | None = None,
) -> tuple[dict[str, Any], Task | None, bool]:
    try:
        return project_store.commit_message_action(
            project_id,
            event_id=event_id,
            kind=kind,
            payload=payload,
            expected_thread_id=expected_thread_id,
            expected_binding_generation=expected_binding_generation,
            task=task,
        )
    except ProjectBindingChangedError as exc:
        raise _binding_changed(exc.thread_id, exc.project_id, exc.generation) from exc
    except PermissionError as exc:
        raise MessageProjectActionError(404, "project not found") from exc
    except ValueError as exc:
        raise MessageProjectActionError(409, str(exc)) from exc


def _action_receipt(
    *,
    action_id: str,
    action: str,
    project_id: str,
    target: dict[str, Any],
    event_id: str,
    applied_at: str,
) -> dict[str, Any]:
    return {
        "id": action_id,
        "action": action,
        "project_id": project_id,
        "target": target,
        "event_id": event_id,
        "applied_at": applied_at,
    }


def _entity_ref(kind: str, entity_id: str, project_id: str, **extra: Any) -> dict[str, Any]:
    normalized_extra = {
        key: (str(value)[:256] if key == "label" else value)
        for key, value in extra.items()
        if value not in (None, "")
    }
    return {
        "kind": kind,
        "id": require_cowork_id(entity_id, label=f"{kind} id"),
        "project_id": project_id,
        **normalized_extra,
    }


def _existing_receipt(message: dict[str, Any], action_id: str) -> dict[str, Any] | None:
    raw_metadata = message.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    actions = metadata.get("project_actions")
    if not isinstance(actions, list):
        return None
    return next(
        (
            dict(item)
            for item in actions
            if isinstance(item, dict) and str(item.get("id") or "") == action_id
        ),
        None,
    )


def _task_projection(task: Task, *, project_id: str, tenant_id: str) -> dict[str, Any]:
    raw = task.to_dict()
    assigned_agent = str(raw.get("assigned_agent") or "")
    assigned_role = str(raw.get("assigned_role") or "")
    raw_input = raw.get("input")
    source_message = raw_input.get("source_message") if isinstance(raw_input, dict) else None
    return {
        "id": task.id,
        "kind": "project",
        "title": task.goal or task.id,
        "description": task.goal or "",
        "status": task.status or "pending",
        "assignees": [
            item
            for item in (
                {"name": assigned_agent, "role": "agent"},
                {"name": assigned_role, "role": "role"},
            )
            if item["name"]
        ],
        "artifacts": (
            [{"kind": "project_task_output", "output": raw.get("output")}]
            if raw.get("output") not in (None, "", {}, [])
            else []
        ),
        "metadata": {
            "source": "projectos",
            "project_id": project_id,
            "tenant_id": tenant_id,
            "milestone_id": task.milestone_id,
            "task_type": raw.get("type"),
            "assigned_agent": assigned_agent,
            "assigned_role": assigned_role,
            "attempts": raw.get("attempts"),
            **({"source_message": source_message} if source_message else {}),
        },
    }


def apply_message_project_action(
    project_store: Any,
    collaboration_store: Any,
    *,
    thread_id: str,
    room_id: str,
    message: dict[str, Any],
    body: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Apply one idempotent message action and return its structured receipt."""

    raw_action = str(body.get("action") or "").strip().lower()
    action = _ACTION_ALIASES.get(raw_action)
    if action is None:
        raise MessageProjectActionError(
            400,
            "action must be link_milestone | create_item | record_decision | publish_artifact",
        )
    project, binding_generation = _bound_project(
        project_store,
        thread_id,
        str(body.get("project_id") or "").strip(),
    )
    source = _source(message, thread_id=thread_id, room_id=room_id)
    action_seed = str(body.get("action_id") or "").strip() or {
        key: value
        for key, value in body.items()
        if key not in {"action_id", "run"} and value not in (None, "", [], {})
    }
    action_id = _stable_id(
        "MA",
        thread_id,
        project.id,
        binding_generation,
        source["message_seq"],
        action,
        action_seed,
    )
    existing_receipt = _existing_receipt(message, action_id)
    if existing_receipt is not None:
        if str(existing_receipt.get("project_id") or "") != project.id:
            raise _binding_changed(thread_id, project.id, binding_generation)
        card = collaboration_store.message_by_source_id(
            thread_id,
            f"project-action:{action_id}",
        )
        return {
            "ok": True,
            "replayed": True,
            "created": False,
            "action_id": action_id,
            "action": action,
            "project_id": project.id,
            "target": existing_receipt.get("target") or {},
            "receipt": existing_receipt,
            "source_message": message,
            "system_card_message": card,
            "projection_pending": False,
            "recovery": None,
        }

    milestone = None
    if action in {"link_milestone", "create_item"}:
        milestone = _project_milestone(
            project_store,
            project,
            str(body.get("milestone_id") or ""),
        )

    task: Task | None = None
    candidate_task: Task | None = None
    event_kind: str
    event_payload: dict[str, Any]
    target: dict[str, Any] | None = None
    if action == "create_item":
        if milestone is None:
            raise RuntimeError("create_item milestone was not resolved")
        if project.status in {"blocked", "done", "failed"}:
            raise MessageProjectActionError(409, "cannot add an item to a terminal project")
        if milestone.status in {"blocked", "done", "failed"}:
            raise MessageProjectActionError(409, "cannot add an item to a terminal milestone")
        task_type = str(body.get("task_type") or "analysis").strip().lower()
        if task_type not in _TASK_TYPES:
            raise MessageProjectActionError(400, "invalid task_type")
        priority = str(body.get("priority") or "P2").strip().upper()
        if priority not in _PRIORITIES:
            raise MessageProjectActionError(400, "priority must be P0 | P1 | P2 | P3")
        title = str(body.get("title") or source["text"]).strip()
        if not title:
            raise MessageProjectActionError(400, "title is required for create_item")
        task_id = optional_cowork_id(body.get("item_id"), label="item_id") or _stable_id(
            "PT",
            action_id,
        )
        dependencies = [
            require_cowork_id(item, label="depends_on task id")
            for item in (body.get("depends_on") or [])
        ]
        known_task_ids = {item.id for item in project_store.tasks_for_milestone(milestone.id)}
        if any(item not in known_task_ids for item in dependencies):
            raise MessageProjectActionError(400, "depends_on contains a task outside the milestone")
        assigned_role = optional_cowork_id(
            body.get("assigned_role") or ROLE_FOR_TASK.get(task_type, "engineer"),
            label="assigned_role",
        )
        assigned_agent = optional_cowork_id(
            body.get("assigned_agent"),
            label="assigned_agent",
        )
        try:
            estimate = max(0.0, float(body.get("estimate") or 0))
        except (TypeError, ValueError) as exc:
            raise MessageProjectActionError(400, "estimate must be a non-negative number") from exc
        candidate_task = Task(
            id=task_id,
            milestone_id=milestone.id,
            type=task_type,  # type: ignore[arg-type]
            goal=title,
            assigned_role=assigned_role or ROLE_FOR_TASK.get(task_type, "engineer"),
            assigned_agent=assigned_agent,
            priority=priority,
            estimate=estimate,
            due_at=str(body.get("due_at") or "").strip(),
            acceptance_criteria=[
                str(item).strip()
                for item in (body.get("acceptance_criteria") or [])
                if str(item).strip()
            ],
            depends_on=dependencies,
            input={
                "description": str(body.get("description") or "").strip(),
                "source_message": source,
            },
        )
        event_kind = "project.task_created_from_message"
        event_payload = {
            "actor": actor,
            "milestone_id": milestone.id,
            "task": candidate_task.to_dict(),
            "source_message": source,
        }
    elif action == "link_milestone":
        if milestone is None:
            raise RuntimeError("link_milestone milestone was not resolved")
        target = _entity_ref(
            "milestone",
            milestone.id,
            project.id,
            milestone_id=milestone.id,
            label=milestone.name,
        )
        event_kind = "project.message_linked"
        event_payload = {
            "actor": actor,
            "milestone_id": milestone.id,
            "source_message": source,
        }
        card_title = f"已关联里程碑 · {milestone.name}"
        card_summary = source["text"]
        card_status = milestone.status
    elif action == "record_decision":
        decision = str(body.get("decision") or body.get("title") or "").strip()
        if not decision:
            raise MessageProjectActionError(400, "decision is required for record_decision")
        event_kind = "project.decision_recorded"
        event_payload = {
            "actor": actor,
            "decision": decision,
            "rationale": str(body.get("rationale") or "").strip(),
            "source_message": source,
        }
        card_title = "已记录项目决策"
        card_summary = decision
        card_status = "recorded"
    else:  # publish_artifact
        artifact = dict(body.get("artifact") or {})
        if not artifact or not any(
            str(artifact.get(key) or "").strip() for key in ("id", "title", "name", "path", "url")
        ):
            raise MessageProjectActionError(
                400,
                "artifact needs at least one of id, title, name, path, or url",
            )
        artifact_id = optional_cowork_id(artifact.get("id"), label="artifact id") or _stable_id(
            "ART",
            action_id,
        )
        artifact["id"] = artifact_id
        artifact_name = (
            artifact.get("name")
            or artifact.get("title")
            or artifact.get("path")
            or artifact.get("url")
            or artifact_id
        )
        artifact["name"] = artifact_name
        artifact["title"] = artifact.get("title") or artifact_name
        event_kind = "project.artifact_published"
        event_payload = {
            "actor": actor,
            "artifact": artifact,
            "source_message": source,
        }
        target = _entity_ref(
            "artifact",
            artifact_id,
            project.id,
            label=str(artifact.get("title") or artifact_id)[:256],
        )
        card_title = f"已发布资料 · {artifact.get('title') or artifact_id}"
        card_summary = str(artifact.get("summary") or artifact.get("path") or "")
        card_status = "published"

    event_id = _stable_id("EV-MA", action_id)
    event_payload["projection_intent"] = {
        "schema": "echo.projectos.message_action_projection.v1",
        "action_id": action_id,
        "action": action,
        "thread_id": thread_id,
        "room_id": room_id,
        "project_id": project.id,
        "binding_generation": binding_generation,
        "source_message_seq": source["message_seq"],
    }
    event, task, created = _commit_source_action(
        project_store,
        project.id,
        event_id=event_id,
        kind=event_kind,
        payload=event_payload,
        expected_thread_id=thread_id,
        expected_binding_generation=binding_generation,
        task=candidate_task,
    )
    if action == "create_item":
        if task is None:
            raise RuntimeError("project message action task was not persisted")
        existing_source = task.input.get("source_message") if isinstance(task.input, dict) else None
        if existing_source != source:
            raise MessageProjectActionError(409, "item_id already belongs to another source")
        target = _entity_ref(
            "task",
            task.id,
            project.id,
            milestone_id=task.milestone_id,
            task_id=task.id,
            label=task.goal,
        )
        card_title = f"已创建事项 · {task.goal}"
        card_summary = str(body.get("description") or source["text"]).strip()
        card_status = task.status
    elif action == "record_decision":
        decision = str(event_payload["decision"])
        target = _entity_ref("decision", event["id"], project.id, label=decision[:256])
    if target is None:
        raise RuntimeError("project message action target was not resolved")

    project_ref = _entity_ref("project", project.id, project.id, label=project.name)
    card_title = str(card_title).strip()[:512]
    card_summary = str(card_summary).strip()[:4096]
    receipt = _action_receipt(
        action_id=action_id,
        action=action,
        project_id=project.id,
        target=target,
        event_id=event["id"],
        applied_at=datetime.fromtimestamp(float(event["created_at"]), UTC).isoformat(),
    )
    source_metadata = {
        "entity_refs": [project_ref, target],
        "project_actions": [receipt],
    }
    card_metadata = {
        "source_message_id": f"project-action:{action_id}",
        "message_type": "system_card",
        "entity_refs": [project_ref, target],
        "system_card": {
            "schema": "echo.project.system_card.v1",
            "type": action,
            "title": card_title,
            "summary": card_summary,
            "status": card_status,
            "project_id": project.id,
            "target": target,
            "source_message_seq": source["message_seq"],
        },
    }
    projection_pending = False
    recovery = None
    card_created = False
    try:
        projection = collaboration_store.commit_project_message_action(
            session_id=thread_id,
            room_id=room_id,
            project_id=project.id,
            binding_generation=binding_generation,
            source_message_seq=int(message["seq"]),
            source_metadata=source_metadata,
            card_text=card_title,
            card_metadata=card_metadata,
            task=(
                _task_projection(
                    task, project_id=project.id, tenant_id=str(project.tenant_id or "")
                )
                if task is not None
                else None
            ),
            milestone_id=task.milestone_id if task is not None else None,
        )
        source_message = projection["source_message"]
        card_message = projection["system_card_message"]
        card_created = bool(projection.get("card_created"))
    except Exception as exc:  # Source action is durable; projection is recoverable.
        projection_pending = True
        source_message = message
        card_message = None
        recovery = {
            "code": (
                "PROJECT_BINDING_CHANGED"
                if isinstance(exc, ProjectMessageProjectionStale)
                else "PROJECT_ACTION_PROJECTION_PENDING"
            ),
            "event_id": event["id"],
            "thread_id": thread_id,
            "room_id": room_id,
            "project_id": project.id,
            "binding_generation": binding_generation,
        }
    return {
        "ok": True,
        "replayed": not bool(created) and not card_created,
        "created": bool(created),
        "action_id": action_id,
        "action": action,
        "project_id": project.id,
        "milestone_id": milestone.id if milestone is not None else None,
        "target": target,
        "receipt": receipt,
        "event": event,
        "task": task.to_dict() if task is not None else None,
        "source_message": source_message,
        "system_card_message": card_message,
        "projection_pending": projection_pending,
        "recovery": recovery,
    }


__all__ = [
    "MessageProjectActionError",
    "apply_message_project_action",
]
