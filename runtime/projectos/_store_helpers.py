"""Pure helpers used by the Project OS persistence layer."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from runtime.projectos.model import Milestone, Project, Task

_TASK_TYPES = frozenset({"design", "code", "research", "analysis", "review"})
_TASK_STATUSES = frozenset({"pending", "ready", "running", "blocked", "done", "failed", "rejected"})
_MILESTONE_STATUSES = frozenset({"pending", "active", "in_progress", "blocked", "done", "failed"})
_PROJECT_STATUSES = frozenset({"planning", "running", "blocked", "done", "failed"})
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,239}$")
_SAFE_KIND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_TEXT_LENGTH = 65_536
_MAX_NAME_LENGTH = 512
_MAX_LIST_ITEMS = 512
_MAX_JSON_BYTES = 1024 * 1024


def _require_id(value: object, *, label: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID_RE.fullmatch(text):
        raise ValueError(
            f"invalid {label}: use 1-240 letters, numbers, dot, underscore, colon, @, or hyphen"
        )
    return text


def _optional_id(value: object, *, label: str) -> str | None:
    text = str(value or "").strip()
    return _require_id(text, label=label) if text else None


def _require_kind(value: object) -> str:
    text = str(value or "").strip()
    if not _SAFE_KIND_RE.fullmatch(text):
        raise ValueError("invalid event kind")
    return text


def _text(
    value: object,
    *,
    label: str,
    max_length: int = _MAX_TEXT_LENGTH,
    default: str = "",
) -> str:
    text = str(value if value is not None else default).strip()
    if not text:
        text = default
    if len(text) > max_length or any(ord(ch) < 32 and ch not in "\n\r\t" for ch in text):
        raise ValueError(f"invalid {label}: too long or contains unsupported control characters")
    if any(ord(ch) == 127 for ch in text):
        raise ValueError(f"invalid {label}: contains unsupported control characters")
    return text


def _id_list(values: object, *, label: str) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        if len(out) >= _MAX_LIST_ITEMS:
            break
        safe = _optional_id(value, label=label)
        if safe:
            out.append(safe)
    return out


def _text_list(values: object, *, label: str) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        if len(out) >= _MAX_LIST_ITEMS:
            break
        text = _text(value, label=label, max_length=4096)
        if text:
            out.append(text)
    return out


def _json_value(value: Any, *, label: str) -> Any:
    try:
        blob = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {label}: not JSON serializable") from exc
    if len(blob.encode("utf-8")) > _MAX_JSON_BYTES:
        raise ValueError(f"invalid {label}: JSON payload exceeds {_MAX_JSON_BYTES} bytes")
    try:
        return json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {label}: JSON round-trip failed") from exc


def _json_dict(value: Any, *, label: str) -> dict[str, Any]:
    normalized = _json_value(value or {}, label=label)
    return normalized if isinstance(normalized, dict) else {}


def _normalize_project(project: Project) -> Project:
    project_id = _require_id(project.id, label="project_id")
    return Project(
        id=project_id,
        name=_text(
            project.name, label="project name", max_length=_MAX_NAME_LENGTH, default=project_id
        ),
        goal=_text(project.goal, label="project goal"),
        milestone_ids=_id_list(project.milestone_ids, label="milestone_id"),
        current_ms=_optional_id(project.current_ms, label="milestone_id"),
        status=project.status if project.status in _PROJECT_STATUSES else "planning",
        owner_id=_text(project.owner_id, label="owner_id", max_length=256),
        tenant_id=_text(project.tenant_id, label="tenant_id", max_length=256),
        execution_thread_id=(
            _optional_id(project.execution_thread_id, label="execution_thread_id") or ""
        ),
        owner=_text(project.owner, label="project owner", max_length=256),
        created_at=_text(project.created_at, label="created_at", max_length=64),
        started_at=_text(project.started_at, label="started_at", max_length=64),
        finished_at=_text(project.finished_at, label="finished_at", max_length=64),
    )


def _normalize_milestone(ms: Milestone) -> Milestone:
    ms_id = _require_id(ms.id, label="milestone_id")
    return Milestone(
        id=ms_id,
        name=_text(ms.name, label="milestone name", max_length=_MAX_NAME_LENGTH, default=ms_id),
        goal=_text(ms.goal, label="milestone goal"),
        spec=_json_dict(ms.spec, label="milestone spec"),
        success_criteria=_text_list(ms.success_criteria, label="success criterion"),
        priority=ms.priority if ms.priority in ("P0", "P1", "P2", "P3") else "P2",
        planned_start=_text(ms.planned_start, label="planned_start", max_length=64),
        due_at=_text(ms.due_at, label="due_at", max_length=64),
        status=ms.status if ms.status in _MILESTONE_STATUSES else "pending",
        dependencies=_id_list(ms.dependencies, label="milestone dependency"),
        task_ids=_id_list(ms.task_ids, label="task_id"),
    )


def _normalize_task(task: Task) -> Task:
    task_id = _require_id(task.id, label="task_id")
    milestone_id = _require_id(task.milestone_id, label="milestone_id")
    return Task(
        id=task_id,
        milestone_id=milestone_id,
        type=task.type if task.type in _TASK_TYPES else "code",
        goal=_text(task.goal, label="task goal"),
        assigned_role=_optional_id(task.assigned_role, label="assigned_role") or "engineer",
        assigned_agent=_optional_id(task.assigned_agent, label="assigned_agent") or "",
        team_mode=task.team_mode if task.team_mode in ("single", "swarm", "cluster") else "single",
        priority=task.priority if task.priority in ("P0", "P1", "P2", "P3") else "P2",
        estimate=max(0.0, float(task.estimate or 0)),
        due_at=_text(task.due_at, label="due_at", max_length=64),
        acceptance_criteria=_text_list(task.acceptance_criteria, label="acceptance criterion"),
        status=task.status if task.status in _TASK_STATUSES else "pending",
        depends_on=_id_list(task.depends_on, label="task dependency"),
        input=_json_dict(task.input, label="task input"),
        output=_json_value(task.output, label="task output"),
        qa_verdict=(
            _json_dict(task.qa_verdict, label="task qa_verdict")
            if task.qa_verdict is not None
            else None
        ),
        attempts=max(0, min(int(task.attempts or 0), 100)),
    )


def _project_from_doc(raw: str) -> Project | None:
    try:
        return _normalize_project(Project.from_dict(json.loads(raw)))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _milestone_from_doc(raw: str) -> Milestone | None:
    try:
        return _normalize_milestone(Milestone.from_dict(json.loads(raw)))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _task_from_doc(raw: str) -> Task | None:
    try:
        return _normalize_task(Task.from_dict(json.loads(raw)))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _milestone_has_unfinished_tasks(conn: Any, milestone_id: str) -> bool:
    rows = conn.execute(
        "SELECT doc FROM tasks WHERE milestone_id=?",
        (milestone_id,),
    ).fetchall()
    for (raw,) in rows:
        task = _task_from_doc(str(raw))
        if task is None or task.status != "done":
            return True
    return False


def _available_milestone_id(
    project_id: str,
    preferred_id: str,
    *,
    used_ids: set[str],
) -> str:
    """Keep a planner id when free, otherwise namespace it by project.

    Milestone ids predate multi-project persistence and are global primary
    keys. LLM and fallback planners commonly emit ``MS1`` for every project,
    so collisions need resolving before any part of a plan is committed.
    """

    if preferred_id not in used_ids:
        return preferred_id
    scoped = f"{project_id}:{preferred_id}"
    if len(scoped) <= 240 and scoped not in used_ids:
        return scoped
    attempt = 0
    while True:
        digest = hashlib.sha256(f"{project_id}:{preferred_id}:{attempt}".encode()).hexdigest()[:24]
        candidate = f"MS-{digest}"
        if candidate not in used_ids:
            return candidate
        attempt += 1
