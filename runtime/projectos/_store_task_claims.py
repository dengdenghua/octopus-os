"""Transactional task execution claims for :mod:`runtime.projectos.store`."""

from __future__ import annotations

import json
import time
from typing import Any
from uuid import uuid4

from runtime.projectos._store_helpers import (
    _milestone_from_doc,
    _normalize_milestone,
    _normalize_task,
    _project_from_doc,
    _require_id,
    _task_from_doc,
)
from runtime.projectos._store_project_deletion import (
    assert_milestone_not_deleted,
    assert_project_not_deleting,
    assert_task_not_deleted,
)
from runtime.projectos.model import Milestone, Project, Task
from runtime.safety.auth.scope import TenantScope

_CLAIMABLE_TASK_STATUSES = frozenset({"pending", "ready"})
_NON_RUNNABLE_PROJECT_STATUSES = frozenset({"blocked", "done", "failed"})


def _milestone_row_for_scope(
    store: Any,
    conn: Any,
    milestone_id: str,
    scope: TenantScope | None,
) -> tuple[Milestone, str] | None:
    row = conn.execute(
        "SELECT doc, project_id FROM milestones WHERE id=?",
        (milestone_id,),
    ).fetchone()
    if row is None:
        return None
    milestone = _milestone_from_doc(str(row[0]))
    if milestone is None:
        raise ValueError(f"corrupt existing milestone row: {milestone_id}")
    project_id = _require_id(row[1], label="project_id")
    if store._effective_scope(scope) is not None:
        project_row = conn.execute(
            "SELECT doc FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        project = _project_from_doc(str(project_row[0])) if project_row else None
        if project is None or not store._scope_project_allowed(
            project,
            store._effective_scope(scope),
        ):
            raise PermissionError("milestone belongs to another tenant or does not exist")
    return milestone, project_id


def _task_row_for_scope(
    store: Any,
    conn: Any,
    task_id: str,
    scope: TenantScope | None,
) -> tuple[Task, str] | None:
    row = conn.execute(
        "SELECT doc, milestone_id FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    task = _task_from_doc(str(row[0]))
    if task is None:
        raise ValueError(f"corrupt existing task row: {task_id}")
    milestone_id = _require_id(row[1], label="milestone_id")
    if task.milestone_id != milestone_id:
        raise ValueError(f"corrupt task milestone binding: {task_id}")
    parent = conn.execute(
        "SELECT project_id FROM milestones WHERE id=?",
        (milestone_id,),
    ).fetchone()
    if parent is None:
        raise ValueError(f"task milestone does not exist: {milestone_id}")
    if store._effective_scope(scope) is not None:
        project_row = conn.execute(
            "SELECT doc FROM projects WHERE id=?",
            (str(parent[0]),),
        ).fetchone()
        project = _project_from_doc(str(project_row[0])) if project_row else None
        if project is None or not store._scope_project_allowed(
            project,
            store._effective_scope(scope),
        ):
            raise PermissionError("task belongs to another tenant or does not exist")
    return task, milestone_id


def claim_task(
    store: Any,
    task_id: str,
    *,
    assigned_role: str | None = None,
    scope: TenantScope | None = None,
) -> tuple[Task, str] | None:
    """Atomically move one runnable task to ``running`` and fence its worker."""

    safe_task_id = _require_id(task_id, label="task_id")
    claim_id = f"TC-{uuid4().hex}"
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        assert_task_not_deleted(conn, safe_task_id, "")
        loaded = _task_row_for_scope(store, conn, safe_task_id, scope)
        if loaded is None:
            return None
        task, milestone_id = loaded
        if task.status not in _CLAIMABLE_TASK_STATUSES:
            return None
        milestone_row = conn.execute(
            "SELECT doc, project_id FROM milestones WHERE id=?",
            (milestone_id,),
        ).fetchone()
        milestone = _milestone_from_doc(str(milestone_row[0])) if milestone_row else None
        project = (
            store._project_doc_for_scope(conn, str(milestone_row[1]), scope)
            if milestone_row
            else None
        )
        if (
            milestone is None
            or milestone.status not in {"active", "in_progress"}
            or project is None
            or project.status in _NON_RUNNABLE_PROJECT_STATUSES
        ):
            return None
        assert_project_not_deleting(conn, project.id)
        if assigned_role:
            task.assigned_role = assigned_role
        task.status = "running"
        task.attempts += 1
        task = _normalize_task(task)
        conn.execute(
            "UPDATE tasks SET doc=?, milestone_id=? WHERE id=?",
            (
                json.dumps(task.to_dict(), ensure_ascii=False),
                milestone_id,
                safe_task_id,
            ),
        )
        conn.execute(
            "INSERT INTO task_claims(task_id, claim_id, claimed_at) VALUES (?, ?, ?) "
            "ON CONFLICT(task_id) DO UPDATE SET "
            "claim_id=excluded.claim_id, claimed_at=excluded.claimed_at",
            (safe_task_id, claim_id, time.time()),
        )
    return task, claim_id


def finalize_task_claim(
    store: Any,
    task: Task,
    claim_id: str,
    *,
    scope: TenantScope | None = None,
) -> tuple[Task | None, bool]:
    """Persist a claimed result only while its opaque fencing token still wins."""

    candidate = _normalize_task(task)
    safe_claim_id = _require_id(claim_id, label="task_claim_id")
    if candidate.status == "running":
        raise ValueError("claimed task result must leave running status")
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        loaded = _task_row_for_scope(store, conn, candidate.id, scope)
        if loaded is None:
            return None, False
        current, milestone_id = loaded
        claim_row = conn.execute(
            "SELECT claim_id FROM task_claims WHERE task_id=?",
            (candidate.id,),
        ).fetchone()
        if claim_row is None or str(claim_row[0]) != safe_claim_id or current.status != "running":
            return current, False
        if candidate.milestone_id != milestone_id:
            raise ValueError("task is already attached to another milestone")
        candidate.attempts = current.attempts
        candidate = _normalize_task(candidate)
        deleted = conn.execute(
            "DELETE FROM task_claims WHERE task_id=? AND claim_id=?",
            (candidate.id, safe_claim_id),
        )
        if deleted.rowcount != 1:
            return current, False
        conn.execute(
            "UPDATE tasks SET doc=?, milestone_id=? WHERE id=?",
            (
                json.dumps(candidate.to_dict(), ensure_ascii=False),
                milestone_id,
                candidate.id,
            ),
        )
    return candidate, True


def orphan_stale_task_claims(
    store: Any,
    project_id: str,
    *,
    stale_before: float,
    scope: TenantScope | None = None,
) -> list[Task]:
    """Fence expired workers and atomically make their tasks operator-visible.

    Expiry never makes a task runnable: an external executor may have completed
    its side effect just before dying.  The task, milestone, and project are
    therefore blocked until an operator explicitly calls the recovery path.
    """

    safe_project_id = _require_id(project_id, label="project_id")
    cutoff = float(stale_before)
    detected_at = time.time()
    orphaned: list[Task] = []
    blocked_milestones: dict[str, Any] = {}
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        project = store._project_doc_for_scope(conn, safe_project_id, scope)
        if project is None:
            return []
        rows = conn.execute(
            "SELECT t.doc, t.milestone_id, c.claim_id, c.claimed_at, m.doc "
            "FROM task_claims c "
            "INNER JOIN tasks t ON t.id=c.task_id "
            "INNER JOIN milestones m ON m.id=t.milestone_id "
            "WHERE m.project_id=? AND c.claimed_at<=? "
            "ORDER BY c.claimed_at, c.task_id",
            (safe_project_id, cutoff),
        ).fetchall()
        for task_doc, milestone_id, claim_id, claimed_at, milestone_doc in rows:
            task = _task_from_doc(str(task_doc))
            milestone = _milestone_from_doc(str(milestone_doc))
            if task is None or milestone is None:
                continue
            if task.status != "running":
                conn.execute(
                    "DELETE FROM task_claims WHERE task_id=? AND claim_id=?",
                    (task.id, str(claim_id)),
                )
                continue
            task.status = "blocked"
            task.qa_verdict = {
                "approved": False,
                "reason": "execution claim expired; explicit recovery required",
            }
            task = _normalize_task(task)
            deleted = conn.execute(
                "DELETE FROM task_claims WHERE task_id=? AND claim_id=?",
                (task.id, str(claim_id)),
            )
            if deleted.rowcount != 1:
                continue
            conn.execute(
                "UPDATE tasks SET doc=? WHERE id=?",
                (json.dumps(task.to_dict(), ensure_ascii=False), task.id),
            )
            if milestone.status not in {"done", "failed"}:
                milestone.status = "blocked"
                blocked_milestones[str(milestone_id)] = milestone
            conn.execute(
                "INSERT INTO project_events(id, project_id, kind, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    f"EV-{uuid4().hex[:12]}",
                    safe_project_id,
                    "task.claim_orphaned",
                    json.dumps(
                        {
                            "task_id": task.id,
                            "milestone_id": task.milestone_id,
                            "claimed_at": float(claimed_at),
                            "detected_at": detected_at,
                            "recovery_required": True,
                        },
                        ensure_ascii=False,
                    ),
                    detected_at,
                ),
            )
            orphaned.append(task)
        for milestone_id, milestone in blocked_milestones.items():
            conn.execute(
                "UPDATE milestones SET doc=? WHERE id=?",
                (json.dumps(milestone.to_dict(), ensure_ascii=False), milestone_id),
            )
        if orphaned and project.status not in {"done", "failed"}:
            project.status = "blocked"
            project.current_ms = orphaned[0].milestone_id
            conn.execute(
                "UPDATE projects SET doc=? WHERE id=?",
                (json.dumps(project.to_dict(), ensure_ascii=False), safe_project_id),
            )
    return orphaned


def claim_milestone_decomposition(
    store: Any,
    milestone_id: str,
    *,
    scope: TenantScope | None = None,
) -> tuple[Milestone, str] | None:
    """Elect the only worker allowed to call the external decomposer."""

    safe_milestone_id = _require_id(milestone_id, label="milestone_id")
    claim_id = f"MC-{uuid4().hex}"
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        assert_milestone_not_deleted(conn, safe_milestone_id)
        loaded = _milestone_row_for_scope(store, conn, safe_milestone_id, scope)
        if loaded is None:
            return None
        milestone, _project_id = loaded
        if milestone.status not in {"active", "in_progress"}:
            return None
        project = store._project_doc_for_scope(conn, _project_id, scope)
        if project is None or project.status in _NON_RUNNABLE_PROJECT_STATUSES:
            return None
        assert_project_not_deleting(conn, _project_id)
        if conn.execute(
            "SELECT 1 FROM tasks WHERE milestone_id=? LIMIT 1",
            (safe_milestone_id,),
        ).fetchone():
            return None
        inserted = conn.execute(
            "INSERT OR IGNORE INTO milestone_claims(milestone_id, claim_id, claimed_at) "
            "VALUES (?, ?, ?)",
            (safe_milestone_id, claim_id, time.time()),
        )
        if inserted.rowcount != 1:
            return None
    return milestone, claim_id


def orphan_stale_milestone_claims(
    store: Any,
    project_id: str,
    *,
    stale_before: float,
    scope: TenantScope | None = None,
) -> list[Milestone]:
    """Block abandoned decompositions instead of silently retrying the hook."""

    safe_project_id = _require_id(project_id, label="project_id")
    detected_at = time.time()
    orphaned: list[Milestone] = []
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        project = store._project_doc_for_scope(conn, safe_project_id, scope)
        if project is None:
            return []
        rows = conn.execute(
            "SELECT m.doc, c.claim_id, c.claimed_at "
            "FROM milestone_claims c "
            "INNER JOIN milestones m ON m.id=c.milestone_id "
            "WHERE m.project_id=? AND c.claimed_at<=? "
            "ORDER BY c.claimed_at, c.milestone_id",
            (safe_project_id, float(stale_before)),
        ).fetchall()
        for milestone_doc, claim_id, claimed_at in rows:
            milestone = _milestone_from_doc(str(milestone_doc))
            if milestone is None:
                continue
            has_tasks = conn.execute(
                "SELECT 1 FROM tasks WHERE milestone_id=? LIMIT 1",
                (milestone.id,),
            ).fetchone()
            deleted = conn.execute(
                "DELETE FROM milestone_claims WHERE milestone_id=? AND claim_id=?",
                (milestone.id, str(claim_id)),
            )
            if (
                deleted.rowcount != 1
                or has_tasks
                or milestone.status not in {"active", "in_progress"}
            ):
                continue
            milestone.status = "blocked"
            milestone = _normalize_milestone(milestone)
            conn.execute(
                "UPDATE milestones SET doc=? WHERE id=?",
                (json.dumps(milestone.to_dict(), ensure_ascii=False), milestone.id),
            )
            conn.execute(
                "INSERT INTO project_events(id, project_id, kind, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    f"EV-{uuid4().hex[:12]}",
                    safe_project_id,
                    "milestone.decomposition_claim_orphaned",
                    json.dumps(
                        {
                            "milestone_id": milestone.id,
                            "claimed_at": float(claimed_at),
                            "detected_at": detected_at,
                            "recovery_required": True,
                        },
                        ensure_ascii=False,
                    ),
                    detected_at,
                ),
            )
            orphaned.append(milestone)
        if orphaned and project.status not in {"done", "failed"}:
            project.status = "blocked"
            project.current_ms = orphaned[0].id
            conn.execute(
                "UPDATE projects SET doc=? WHERE id=?",
                (json.dumps(project.to_dict(), ensure_ascii=False), safe_project_id),
            )
    return orphaned


def finalize_milestone_decomposition(
    store: Any,
    project_id: str,
    milestone_id: str,
    tasks: list[Task],
    claim_id: str,
    *,
    blocked: bool = False,
    scope: TenantScope | None = None,
) -> tuple[Milestone | None, bool]:
    """Atomically publish one canonical task set, fenced by the claim token."""

    safe_project_id = _require_id(project_id, label="project_id")
    safe_milestone_id = _require_id(milestone_id, label="milestone_id")
    safe_claim_id = _require_id(claim_id, label="milestone_claim_id")
    if blocked and tasks:
        raise ValueError("blocked decomposition cannot publish tasks")
    if not blocked and not tasks:
        raise ValueError("successful decomposition must publish tasks")
    normalized_tasks: list[Task] = []
    seen_ids: set[str] = set()
    for task in tasks:
        candidate = _normalize_task(task)
        if candidate.milestone_id != safe_milestone_id:
            raise ValueError("decomposed task belongs to another milestone")
        if candidate.id in seen_ids:
            raise ValueError(f"duplicate decomposed task id: {candidate.id}")
        seen_ids.add(candidate.id)
        normalized_tasks.append(candidate)

    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        loaded = _milestone_row_for_scope(store, conn, safe_milestone_id, scope)
        if loaded is None:
            return None, False
        current, bound_project_id = loaded
        if bound_project_id != safe_project_id:
            raise ValueError("milestone does not belong to project")
        claim_row = conn.execute(
            "SELECT claim_id FROM milestone_claims WHERE milestone_id=?",
            (safe_milestone_id,),
        ).fetchone()
        if claim_row is None or str(claim_row[0]) != safe_claim_id:
            return current, False
        if current.status in {"done", "failed", "blocked"}:
            return current, False
        if conn.execute(
            "SELECT 1 FROM tasks WHERE milestone_id=? LIMIT 1",
            (safe_milestone_id,),
        ).fetchone():
            conn.execute(
                "DELETE FROM milestone_claims WHERE milestone_id=? AND claim_id=?",
                (safe_milestone_id, safe_claim_id),
            )
            return current, False
        for task in normalized_tasks:
            if conn.execute("SELECT 1 FROM tasks WHERE id=?", (task.id,)).fetchone():
                raise ValueError(f"task id already exists: {task.id}")
        deleted = conn.execute(
            "DELETE FROM milestone_claims WHERE milestone_id=? AND claim_id=?",
            (safe_milestone_id, safe_claim_id),
        )
        if deleted.rowcount != 1:
            return current, False
        for task in normalized_tasks:
            conn.execute(
                "INSERT INTO tasks(id, milestone_id, doc) VALUES (?, ?, ?)",
                (
                    task.id,
                    safe_milestone_id,
                    json.dumps(task.to_dict(), ensure_ascii=False),
                ),
            )
        current.task_ids = [task.id for task in normalized_tasks]
        current.status = "blocked" if blocked else "in_progress"
        current = _normalize_milestone(current)
        conn.execute(
            "UPDATE milestones SET doc=? WHERE id=?",
            (
                json.dumps(current.to_dict(), ensure_ascii=False),
                safe_milestone_id,
            ),
        )
    return current, True


def assert_no_active_claims(
    store: Any,
    project_id: str,
    *,
    task_ids: list[str] | tuple[str, ...] | None = None,
    scope: TenantScope | None = None,
) -> Project:
    """Fail atomically when an operator mutation would cross a live claim."""

    safe_project_id = _require_id(project_id, label="project_id")
    selected = tuple(
        sorted({_require_id(task_id, label="task_id") for task_id in (task_ids or [])})
    )
    with store._lock, store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        project = store._project_doc_for_scope(conn, safe_project_id, scope)
        if project is None:
            raise PermissionError("project belongs to another tenant or does not exist")
        task_sql = (
            "SELECT tc.task_id FROM task_claims tc "
            "INNER JOIN tasks t ON t.id=tc.task_id "
            "INNER JOIN milestones m ON m.id=t.milestone_id "
            "WHERE m.project_id=?"
        )
        params: list[str] = [safe_project_id]
        if task_ids is not None:
            if not selected:
                task_rows: list[Any] = []
            else:
                placeholders = ",".join("?" for _ in selected)
                task_rows = conn.execute(
                    f"{task_sql} AND tc.task_id IN ({placeholders}) ORDER BY tc.task_id",
                    (*params, *selected),
                ).fetchall()
        else:
            task_rows = conn.execute(f"{task_sql} ORDER BY tc.task_id", params).fetchall()
        milestone_rows = conn.execute(
            "SELECT mc.milestone_id FROM milestone_claims mc "
            "INNER JOIN milestones m ON m.id=mc.milestone_id "
            "WHERE m.project_id=? ORDER BY mc.milestone_id",
            (safe_project_id,),
        ).fetchall()
        if task_rows or milestone_rows:
            from runtime.projectos._store_thread_bindings import ProjectClaimActiveError

            raise ProjectClaimActiveError(
                project,
                task_ids=tuple(str(row[0]) for row in task_rows),
                milestone_ids=tuple(str(row[0]) for row in milestone_rows),
            )
        return project


class ProjectClaimStoreMixin:
    """ProjectStore methods backed by the execution-claim transactions above."""

    def claim_task(
        self,
        task_id: str,
        *,
        assigned_role: str | None = None,
        scope: TenantScope | None = None,
    ) -> tuple[Task, str] | None:
        return claim_task(self, task_id, assigned_role=assigned_role, scope=scope)

    def assert_no_active_claims(
        self,
        project_id: str,
        *,
        task_ids: list[str] | tuple[str, ...] | None = None,
        scope: TenantScope | None = None,
    ) -> Project:
        return assert_no_active_claims(
            self,
            project_id,
            task_ids=task_ids,
            scope=scope,
        )

    def finalize_task_claim(
        self,
        task: Task,
        claim_id: str,
        *,
        scope: TenantScope | None = None,
    ) -> tuple[Task | None, bool]:
        return finalize_task_claim(self, task, claim_id, scope=scope)

    def orphan_stale_task_claims(
        self,
        project_id: str,
        *,
        stale_before: float,
        scope: TenantScope | None = None,
    ) -> list[Task]:
        return orphan_stale_task_claims(self, project_id, stale_before=stale_before, scope=scope)

    def claim_milestone_decomposition(
        self,
        milestone_id: str,
        *,
        scope: TenantScope | None = None,
    ) -> tuple[Milestone, str] | None:
        return claim_milestone_decomposition(self, milestone_id, scope=scope)

    def orphan_stale_milestone_claims(
        self,
        project_id: str,
        *,
        stale_before: float,
        scope: TenantScope | None = None,
    ) -> list[Milestone]:
        return orphan_stale_milestone_claims(
            self,
            project_id,
            stale_before=stale_before,
            scope=scope,
        )

    def finalize_milestone_decomposition(
        self,
        project_id: str,
        milestone_id: str,
        tasks: list[Task],
        claim_id: str,
        *,
        blocked: bool = False,
        scope: TenantScope | None = None,
    ) -> tuple[Milestone | None, bool]:
        return finalize_milestone_decomposition(
            self,
            project_id,
            milestone_id,
            tasks,
            claim_id,
            blocked=blocked,
            scope=scope,
        )


__all__ = ["ProjectClaimStoreMixin"]
