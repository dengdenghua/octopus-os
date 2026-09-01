"""Persistence for user-defined cron jobs.

Read/validate and atomically write the cron-jobs JSON file. Both the
cron *skills* (execution) and the ``/api/cron`` compatibility *router*
(sensing/gateway) need this; it lived in the router, which made the
execution-layer cron skills depend upward on the web layer. It depends
only on stdlib + ``platform.io``, so it belongs with the cron domain
logic in execution. The router now imports it from here.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from runtime.platform.io import atomic_write_json
from runtime.safety.auth.scope import TenantScope

_T = TypeVar("_T")
_CRON_STORE_FALLBACK_LOCK = threading.RLock()

# These values were historically written by anonymous/local callers or by the
# model-callable schedule skill.  They describe provenance, not an authenticated
# owner, and therefore stay in the legacy-unowned bucket.
_LEGACY_UNOWNED_CREATORS = frozenset({"", "*", "agent_self"})


def _read_cron_jobs(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []

    jobs: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        command = str(item.get("command") or "").strip()
        if not name or not command:
            continue
        # Preserve forward-compatible execution fields.  The old fixed
        # projection silently deleted ``prompt``, delivery targets and scope
        # metadata whenever the settings API performed an unrelated mutation.
        # Public routers must still project their response explicitly; this is
        # the internal durable representation.
        job = dict(item)
        job.update(
            {
                "name": name,
                "command": command,
                "cron_expression": str(item.get("cron_expression") or "0 * * * *"),
                "last_run": item.get("last_run"),
                "last_status": item.get("last_status"),
                # Output excerpt from the last executor run (written by
                # ``runtime.execution.cron_executor``); surfaced by the
                # settings UI so a fired job is inspectable without logs.
                "last_output": item.get("last_output"),
                # ``creator_actor`` remains readable for pre-scope records.
                # New authenticated records additionally carry the exact
                # tenant/owner pair below.
                "creator_actor": item.get("creator_actor"),
            }
        )
        jobs.append(job)
    return jobs


def _write_cron_jobs(path: Path, jobs: list[dict[str, Any]]) -> None:
    atomic_write_json(path, jobs)


@contextmanager
def _cron_store_lock(path: Path) -> Iterator[None]:
    """Serialize read-modify-write operations across workers/processes.

    ``atomic_write_json`` protects an individual replacement, but without a
    surrounding transaction two tenants creating jobs concurrently can both
    read the same old list and one update is lost.  Keep this sidecar distinct
    from both the atomic writer lock and the executor's long-lived dispatch
    lock so nested persistence cannot deadlock.
    """

    lock_path = path.with_name(path.name + ".mutation.lock")
    handle = None
    fallback_acquired = False
    try:
        try:
            import fcntl

            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_path.open("a+", encoding="utf-8")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:
            _CRON_STORE_FALLBACK_LOCK.acquire()
            fallback_acquired = True
        yield
    finally:
        if fallback_acquired:
            _CRON_STORE_FALLBACK_LOCK.release()
        if handle is not None:
            with contextlib.suppress(Exception):
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            with contextlib.suppress(Exception):
                handle.close()


def _mutate_cron_jobs(
    path: Path,
    mutator: Callable[[list[dict[str, Any]]], _T],
    *,
    persist_if: Callable[[_T], bool] | None = None,
) -> _T:
    """Atomically mutate the shared job list while preserving all fields."""

    with _cron_store_lock(path):
        jobs = _read_cron_jobs(path)
        result = mutator(jobs)
        if persist_if is None or persist_if(result):
            _write_cron_jobs(path, jobs)
        return result


def _clean_scope_value(value: Any) -> str:
    return str(value or "").strip()


def cron_job_explicit_scope(job: dict[str, Any]) -> TenantScope | None:
    """Return a complete persisted scope, never an inferred partial one."""

    tenant_id = _clean_scope_value(job.get("tenant_id"))
    owner_actor_id = _clean_scope_value(job.get("owner_actor_id"))
    if tenant_id and owner_actor_id:
        return TenantScope(tenant_id=tenant_id, actor_id=owner_actor_id)
    return None


def cron_job_has_incomplete_scope(job: dict[str, Any]) -> bool:
    """True for corrupt half-scoped rows; these must never execute."""

    tenant_id = bool(_clean_scope_value(job.get("tenant_id")))
    owner_actor_id = bool(_clean_scope_value(job.get("owner_actor_id")))
    return tenant_id != owner_actor_id


def cron_job_legacy_actor(job: dict[str, Any]) -> str | None:
    """Recover the owner hint from a pre-tenant ``creator_actor`` row."""

    if cron_job_explicit_scope(job) is not None or cron_job_has_incomplete_scope(job):
        return None
    creator = _clean_scope_value(job.get("creator_actor"))
    if creator in _LEGACY_UNOWNED_CREATORS:
        return None
    return creator or None


def cron_job_is_legacy_unowned(job: dict[str, Any]) -> bool:
    """Return whether a row has no authenticated ownership at all."""

    return (
        cron_job_explicit_scope(job) is None
        and not cron_job_has_incomplete_scope(job)
        and cron_job_legacy_actor(job) is None
    )


def cron_job_effective_scope(job: dict[str, Any]) -> TenantScope | None:
    """Return exact scope or the isolated namespace for an old actor row.

    An old ``creator_actor=alice`` record cannot prove its original tenant.
    It is therefore quarantined in ``legacy:alice`` until a request resolved
    for Alice adopts it into Alice's current server-side tenant.  Background
    execution still retains an owner-bound Session instead of silently running
    it as an unscoped task.
    """

    explicit = cron_job_explicit_scope(job)
    if explicit is not None:
        return explicit
    legacy_actor = cron_job_legacy_actor(job)
    if legacy_actor:
        return TenantScope(tenant_id=f"legacy:{legacy_actor}", actor_id=legacy_actor)
    return None


def cron_job_visible_to_scope(
    job: dict[str, Any],
    scope: TenantScope | None,
) -> bool:
    """Exact request visibility with a narrow legacy migration bridge.

    No scope means local/anonymous compatibility and can only see genuinely
    unowned legacy rows.  A normal principal sees its exact tenant+owner rows,
    plus old rows whose sole ``creator_actor`` matches that same actor.  Only
    an explicitly cross-tenant scope can inspect every non-corrupt row.
    """

    if cron_job_has_incomplete_scope(job):
        return bool(scope is not None and scope.allow_cross_tenant)
    if scope is None:
        return cron_job_is_legacy_unowned(job)
    if scope.allow_cross_tenant:
        return True
    explicit = cron_job_explicit_scope(job)
    if explicit is not None:
        return explicit.tenant_id == scope.tenant_id and explicit.actor_id == scope.actor_id
    return cron_job_legacy_actor(job) == scope.actor_id


def bind_cron_job_scope(job: dict[str, Any], scope: TenantScope | None) -> None:
    """Stamp authoritative ownership or retain the local legacy shape."""

    if scope is None:
        job.pop("tenant_id", None)
        job.pop("owner_actor_id", None)
        return
    job["tenant_id"] = scope.tenant_id
    job["owner_actor_id"] = scope.actor_id
    job["creator_actor"] = scope.actor_id


def cron_job_identity(job: dict[str, Any]) -> tuple[str, str, str]:
    """Stable logical key used for scoped updates inside the flat store."""

    scope = cron_job_effective_scope(job)
    if scope is None:
        tenant_id = "__legacy_unowned__"
        actor_id = "__legacy_unowned__"
    else:
        tenant_id = scope.tenant_id
        actor_id = scope.actor_id
    return tenant_id, actor_id, _clean_scope_value(job.get("name"))


__all__ = [
    "_mutate_cron_jobs",
    "_read_cron_jobs",
    "_write_cron_jobs",
    "bind_cron_job_scope",
    "cron_job_effective_scope",
    "cron_job_explicit_scope",
    "cron_job_has_incomplete_scope",
    "cron_job_identity",
    "cron_job_is_legacy_unowned",
    "cron_job_legacy_actor",
    "cron_job_visible_to_scope",
]
