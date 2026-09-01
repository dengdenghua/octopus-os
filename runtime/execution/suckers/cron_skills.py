"""cron_skills · let the agent self-schedule a future turn from inside a turn.

Today ``runtime/sensing/siphon/cron_router.py`` is a UI-driven CRUD for cron
jobs.  This module exposes the same persistent store as three model-callable
skills so the agent can say "remind me in 1 hour to check the deploy" mid-turn.

Persistence
-----------
We write to ``app_paths().cron_jobs_path`` using the *same* on-disk shape as
``cron_router._read_cron_jobs`` / ``_write_cron_jobs`` so the existing settings
UI sees model-scheduled tasks alongside user-created ones. Each agent-created
record carries ``created_by="agent_self"``. Authenticated turns bind
``tenant_id`` + ``owner_actor_id`` from the server-stamped Session; anonymous
local turns retain the historical ``creator_actor="agent_self"`` shape.

Scheduling shapes
-----------------
Exactly one of ``cron_expression`` (recurring) or ``fire_at`` (one-shot ISO
8601 timestamp with timezone offset) must be supplied. ``fire_at`` is encoded
into a one-shot cron expression matching the exact minute and we mark
``recurring=False`` on the record so a downstream runner can disable it after
firing. The UI's existing cron file format only carries
``cron_expression``, so we also add an optional ``fire_at`` field that the UI
ignores but ``list_scheduled_tasks`` uses for display.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.execution.cron_store import (
    _mutate_cron_jobs,
    _read_cron_jobs,
    bind_cron_job_scope,
    cron_job_visible_to_scope,
)
from runtime.platform.process.paths import app_paths
from runtime.safety.auth.scope import TenantScope
from runtime.safety.recovery.tenant_scope import trusted_scope_from_user_context

from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase

_log = logging.getLogger(__name__)

# Loose 5-field cron pattern — cheap pre-flight reject of obvious garbage.
# The authoritative parser is ``CronExpression.parse``; we lean on it for the
# real validation so cron syntax stays consistent between UI and skill.
_CRON_FIELD_RE = re.compile(r"^[\d\*\-\,\/]+$")
_AGENT_CREATOR = "agent_self"


def _cron_path() -> Path:
    return app_paths().cron_jobs_path


def _current_tenant_scope() -> TenantScope | None:
    """Read only the server-stamped scope carried by the active Session.

    Ordinary ``tenant_id``/``owner_actor_id`` tool arguments and presentation
    metadata are intentionally ignored.  HTTP/realtime boundaries overwrite
    the private marker after authenticating the principal; a local CLI turn
    has no marker and remains in the legacy-unowned compatibility bucket.
    """

    try:
        from runtime.platform.process.session import current_session

        session = current_session()
    except Exception:  # noqa: BLE001 — session support is optional in direct tests
        return None
    if session is None:
        return None
    return trusted_scope_from_user_context(getattr(session, "metadata", None))


def _validate_cron_expression(expr: str) -> str | None:
    """Return None if valid, else an error message."""
    parts = expr.strip().split()
    if len(parts) != 5:
        return f"cron expression must have 5 fields (got {len(parts)})"
    for token in parts:
        if not _CRON_FIELD_RE.match(token):
            return f"cron field {token!r} contains unsupported characters"
    try:
        from runtime.adapters.scheduler.cron import CronExpression

        CronExpression.parse(expr)
    except Exception as exc:  # noqa: BLE001
        return f"invalid cron expression: {exc}"
    return None


def _parse_fire_at(value: str) -> tuple[datetime | None, str | None]:
    """Return (dt, err). Requires ISO 8601 with explicit timezone offset."""
    raw = value.strip()
    if not raw:
        return None, "fire_at is empty"
    candidate = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError as exc:
        return None, f"fire_at is not a valid ISO 8601 timestamp: {exc}"
    if dt.tzinfo is None:
        return None, "fire_at must include a timezone offset (e.g. +00:00)"
    return dt, None


def _fire_at_to_cron(dt: datetime) -> str:
    """One-shot cron line that matches the exact minute of ``dt``.

    Local-time semantics — we convert to the host's local clock before
    serializing so the existing ``CronExpression.matches`` (which sees a
    ``datetime.now()``-style local datetime) fires on the right minute.
    """
    local = dt.astimezone()
    return f"{local.minute} {local.hour} {local.day} {local.month} *"


def _next_run_iso(cron_expression: str) -> str | None:
    try:
        from runtime.adapters.scheduler.cron import CronExpression

        ce = CronExpression.parse(cron_expression)
        return ce.next_after(datetime.now().astimezone()).isoformat()
    except Exception:  # noqa: BLE001
        return None


def _err(msg: str, error_type: str = "invalid_argument") -> dict[str, Any]:
    return {"ok": False, "error": msg, "error_type": error_type}


def _schedule_task(
    prompt: str = "",
    cron_expression: str = "",
    fire_at: str = "",
    recurring: bool = True,
    name: str = "",
    channel_id: str = "",
    thread_id: str = "",
    **_: Any,
) -> dict[str, Any]:
    """Persist a new scheduled task via the same store as the cron UI."""
    prompt = (prompt or "").strip()
    if not prompt:
        return _err("prompt is required")

    # Delivery target · default to the IM conversation this task was
    # scheduled from (章鱼助手订阅推送), if any; explicit args win.
    try:
        from runtime.adapters.channels.manager import current_channel_target

        _target = current_channel_target()
    except Exception:  # noqa: BLE001 — channels optional; fall back to no target
        _target = None
    if _target is not None:
        ch_ctx, th_ctx = _target
    else:
        ch_ctx, th_ctx = "", ""
    channel_id = (channel_id or "").strip() or ch_ctx
    thread_id = (thread_id or "").strip() or th_ctx

    have_cron = bool((cron_expression or "").strip())
    have_fire = bool((fire_at or "").strip())
    if have_cron and have_fire:
        return _err(
            "supply exactly one of cron_expression or fire_at, not both",
        )
    if not have_cron and not have_fire:
        return _err("supply exactly one of cron_expression or fire_at")

    final_cron: str
    is_recurring = bool(recurring)
    fire_at_iso: str | None = None

    if have_cron:
        err = _validate_cron_expression(cron_expression)
        if err:
            return _err(err)
        final_cron = cron_expression.strip()
    else:
        dt, err = _parse_fire_at(fire_at)
        if err or dt is None:
            return _err(err or "fire_at parse failed")
        now = datetime.now(tz=UTC)
        if dt <= now:
            return _err("fire_at must be in the future")
        final_cron = _fire_at_to_cron(dt)
        is_recurring = False
        fire_at_iso = dt.isoformat()

    task_id = (name or "").strip() or f"auto_{uuid.uuid4().hex[:8]}"
    if "/" in task_id or "\\" in task_id:
        return _err("name cannot contain path separators")

    path = _cron_path()
    scope = _current_tenant_scope()
    record: dict[str, Any] = {
        "name": task_id,
        # Reuse the UI's ``command`` slot to carry the agent prompt so the
        # downstream runner has a single field to dispatch on.
        "command": prompt,
        "cron_expression": final_cron,
        "last_run": None,
        "last_status": "created",
        # Pre-scope local tasks keep the old marker. Authenticated tasks retain
        # the actual owner and keep agent provenance in a separate field.
        "creator_actor": scope.actor_id if scope is not None else _AGENT_CREATOR,
        "created_by": _AGENT_CREATOR,
        # Optional fields the UI ignores but the skill round-trips.
        "fire_at": fire_at_iso,
        "recurring": is_recurring,
        "prompt": prompt,
        # 订阅推送 · where to push the cron result over IM (章鱼助手).
        "channel_id": channel_id,
        "thread_id": thread_id,
    }
    bind_cron_job_scope(record, scope)

    def _upsert(jobs: list[dict[str, Any]]) -> None:
        # Names are unique only inside one tenant+owner scope. Two tenants can
        # both schedule ``daily-report`` without overwriting each other.
        jobs[:] = [
            job
            for job in jobs
            if not (job.get("name") == task_id and cron_job_visible_to_scope(job, scope))
        ]
        jobs.append(record)

    _mutate_cron_jobs(path, _upsert)

    return {
        "ok": True,
        "task_id": task_id,
        "next_run_at": _next_run_iso(final_cron),
        "cron_expression": final_cron,
        "recurring": is_recurring,
    }


def _list_scheduled_tasks(**_: Any) -> dict[str, Any]:
    """List tasks visible to the active server-authenticated scope."""
    path = _cron_path()
    scope = _current_tenant_scope()
    jobs = [job for job in _read_cron_jobs(path) if cron_job_visible_to_scope(job, scope)]
    out: list[dict[str, Any]] = []
    for job in jobs:
        out.append(
            {
                "task_id": job.get("name"),
                "cron_expression": job.get("cron_expression"),
                "creator_actor": job.get("creator_actor"),
                "last_run": job.get("last_run"),
                "last_status": job.get("last_status"),
                "next_run_at": _next_run_iso(job.get("cron_expression") or ""),
                "fire_at": job.get("fire_at"),
                "recurring": job.get("recurring", True),
                "prompt": job.get("prompt") or job.get("command"),
            }
        )
    return {"ok": True, "tasks": out, "count": len(out)}


def _cancel_scheduled_task(task_id: str = "", **_: Any) -> dict[str, Any]:
    """Remove a task by id; idempotent (no-op + ok=False if missing)."""
    tid = (task_id or "").strip()
    if not tid:
        return _err("task_id is required")
    path = _cron_path()
    scope = _current_tenant_scope()

    def _remove(jobs: list[dict[str, Any]]) -> bool:
        before = len(jobs)
        jobs[:] = [
            job
            for job in jobs
            if not (job.get("name") == tid and cron_job_visible_to_scope(job, scope))
        ]
        return len(jobs) != before

    removed = _mutate_cron_jobs(path, _remove)
    if not removed:
        return {"ok": False, "error": f"task {tid!r} not found", "error_type": "not_found"}
    return {"ok": True, "task_id": tid, "deleted": True}


def register_cron_skills(registry: SkillRegistry) -> int:
    """Register schedule_task / list_scheduled_tasks / cancel_scheduled_task.

    Returns the count (3).
    """
    registry.register(
        Skill(
            name="schedule_task",
            description=(
                "Schedule a future agent turn. Persists into the same cron "
                "store the settings UI uses so model-scheduled tasks show up "
                "alongside user-created ones. Authenticated tasks are bound "
                "to the current server-resolved tenant and owner. "
                "Args: {prompt: str (required), cron_expression: 5-field "
                "cron string for recurring runs, fire_at: ISO 8601 timestamp "
                "with timezone offset for one-shot, recurring: bool (default "
                "true), name: optional task id (auto-generated if omitted), "
                "channel_id/thread_id: optional IM delivery target (defaults "
                "to the conversation this task is scheduled from, so a "
                "reminder/subscription auto-pushes its result back over IM). "
                "Supply exactly one of cron_expression / fire_at. Returns "
                "{ok, task_id, next_run_at}."
            ),
            affinity=["scheduling", "self_schedule", "cron"],
            cost_profile="low",
            trusted_source="builtin://schedule_task",
            handler=_schedule_task,
            tests=[
                SkillTestCase(
                    name="missing_prompt_returns_error",
                    tier="golden",
                    args={"prompt": "", "cron_expression": "0 * * * *"},
                    expect=SkillExpect(schema_keys=["ok", "error", "error_type"]),
                    custom_predicate=lambda r: (
                        isinstance(r, dict)
                        and r.get("ok") is False
                        and r.get("error_type") == "invalid_argument"
                    ),
                ),
            ],
        )
    )

    registry.register(
        Skill(
            name="list_scheduled_tasks",
            description=(
                "List scheduled tasks owned by the current authenticated "
                "tenant and actor (or legacy local tasks in local mode). Returns "
                "{ok, tasks: [{task_id, cron_expression, creator_actor, "
                "next_run_at, fire_at, recurring, prompt, last_run, "
                "last_status}], count}."
            ),
            affinity=["scheduling", "introspection", "cron"],
            cost_profile="low",
            trusted_source="builtin://list_scheduled_tasks",
            handler=_list_scheduled_tasks,
            tests=[
                SkillTestCase(
                    name="returns_tasks_list",
                    tier="golden",
                    args={},
                    expect=SkillExpect(schema_keys=["ok", "tasks", "count"]),
                ),
            ],
        )
    )

    registry.register(
        Skill(
            name="cancel_scheduled_task",
            description=(
                "Remove a scheduled task by id. Returns "
                "{ok, task_id, deleted} on success, or "
                "{ok: false, error, error_type: 'not_found'} if no task "
                "with that id exists."
            ),
            affinity=["scheduling", "cron"],
            cost_profile="low",
            trusted_source="builtin://cancel_scheduled_task",
            handler=_cancel_scheduled_task,
            tests=[
                SkillTestCase(
                    name="empty_task_id_returns_error",
                    tier="golden",
                    args={"task_id": ""},
                    expect=SkillExpect(schema_keys=["ok", "error", "error_type"]),
                    custom_predicate=lambda r: (
                        isinstance(r, dict)
                        and r.get("ok") is False
                        and r.get("error_type") == "invalid_argument"
                    ),
                ),
            ],
        )
    )
    return 3


__all__ = [
    "register_cron_skills",
    "_schedule_task",
    "_list_scheduled_tasks",
    "_cancel_scheduled_task",
]
