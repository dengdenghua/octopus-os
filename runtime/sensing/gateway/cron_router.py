"""Cron settings compatibility router.

The settings UI still talks to ``/api/cron`` for simple user-defined jobs.
This router keeps that compatibility layer out of ``app.py`` while sharing
the same cwd-relative data path as the rest of the platform runtime.

Cron jobs drive arbitrary ``command`` strings on a schedule — full RCE
once the job fires — so every mutation end-point requires an operator or
admin identity and binds the job to that actor. Read endpoints remain
actor-scoped. In single-user local mode with no identity store, existing
anonymous behavior is retained.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Request

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Request = object  # type: ignore[assignment, misc]

from runtime.execution.cron_store import (
    _mutate_cron_jobs,
    _read_cron_jobs,
    bind_cron_job_scope,
    cron_job_visible_to_scope,
)
from runtime.execution.cron_store import (
    _write_cron_jobs as _write_cron_jobs,  # compatibility re-export
)
from runtime.platform.process.paths import app_paths
from runtime.safety.auth.principal import CurrentPrincipal
from runtime.safety.auth.scope import TenantScope, scope_from_principal

_PUBLIC_JOB_FIELDS = (
    "name",
    "command",
    "cron_expression",
    "last_run",
    "last_status",
    "last_output",
    "creator_actor",
)


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    """Settings projection; never expose delivery or internal scope fields."""

    return {key: job.get(key) for key in _PUBLIC_JOB_FIELDS if key in job}


def _principal_is_admin(principal: CurrentPrincipal | None) -> bool:
    return bool(principal is not None and principal.roles.intersection({"admin", "root"}))


def _request_scope(principal: CurrentPrincipal | None) -> TenantScope | None:
    return scope_from_principal(
        principal,
        allow_cross_tenant=_principal_is_admin(principal),
    )


def _run_visible_to_scope(record: dict[str, Any], scope: TenantScope | None) -> bool:
    """Apply the same exact ownership rule to execution history."""

    if scope is None:
        tenant = str(record.get("tenant_id") or "").strip()
        owner = str(record.get("owner_actor_id") or "").strip()
        creator = str(record.get("creator_actor") or "").strip()
        return not tenant and not owner and creator in {"", "*", "agent_self"}
    if scope.allow_cross_tenant:
        return True
    tenant = str(record.get("tenant_id") or "").strip()
    owner = str(record.get("owner_actor_id") or "").strip()
    if tenant and owner:
        if tenant == scope.tenant_id and owner == scope.actor_id:
            return True
        # Old actor-only rows execute in a quarantined legacy namespace until
        # adopted. The same actor may still read that migration history.
        return tenant == f"legacy:{scope.actor_id}" and owner == scope.actor_id
    return str(record.get("creator_actor") or "").strip() == scope.actor_id


def _public_run(record: dict[str, Any]) -> dict[str, Any]:
    """Hide IM destinations and raw ownership metadata from settings."""

    return {
        key: value
        for key, value in record.items()
        if key not in {"tenant_id", "owner_actor_id", "channel_id", "thread_id"}
    }


def create_cron_router(
    jobs_path: Path | str | None = None,
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    """Create the ``/api/cron`` compatibility router."""
    if not FASTAPI_AVAILABLE:
        raise RuntimeError("fastapi not installed (pip install 'echo-os[serve]')")

    path = Path(jobs_path) if jobs_path is not None else app_paths().cron_jobs_path
    router = APIRouter()

    def _force_auth(request: Any) -> CurrentPrincipal | None:
        """Resolve actor and require a token regardless of global
        ``require_auth``.

        Cron jobs execute shell commands; anonymous create would be
        arbitrary RCE.  Always force Bearer auth when ``identity_store``
        is configured.  When no identity store is configured at all
        (old, single-user local dev), fall back to the global flag.
        """
        try:
            from runtime.safety.auth.principal import resolve_principal

            force = True if identity_store is not None else require_auth
            return resolve_principal(
                request,
                identity_store,
                force,
                jwt_secret=jwt_secret,
                jwt_issuer=jwt_issuer,
                jwt_audience=jwt_audience,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(401, "auth required") from exc

    def _operator_principal(request: Any) -> CurrentPrincipal | None:
        """Require control-plane authority for shell-job mutations."""
        from runtime.safety.auth.principal import require_operator

        force = True if identity_store is not None else require_auth
        return require_operator(
            request,
            identity_store,
            force,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    @router.get("/api/cron")
    @router.get("/api/cron/")
    def api_cron_list(request: Request) -> list[dict[str, Any]]:
        principal = _force_auth(request)
        scope = _request_scope(principal)
        jobs = _read_cron_jobs(path)
        return [_public_job(job) for job in jobs if cron_job_visible_to_scope(job, scope)]

    @router.post("/api/cron")
    @router.post("/api/cron/")
    async def api_cron_create(request: Request) -> dict[str, Any]:
        principal = _operator_principal(request)
        scope = scope_from_principal(principal)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "invalid cron job")

        name = str(body.get("name") or "").strip()
        command = str(body.get("command") or "").strip()
        cron_expression = str(body.get("cron_expression") or "0 * * * *").strip()
        if not name:
            raise HTTPException(400, "name is required")
        if not command:
            raise HTTPException(400, "command is required")
        if "/" in name or "\\" in name:
            raise HTTPException(400, "name cannot contain path separators")
        try:
            from runtime.adapters.scheduler.cron import CronExpression

            CronExpression.parse(cron_expression)
        except Exception as exc:
            raise HTTPException(400, f"invalid cron expression: {exc}") from exc

        job = {
            "name": name,
            "command": command,
            "cron_expression": cron_expression,
            "last_run": None,
            "last_status": "created",
            "last_output": None,
            "creator_actor": principal.actor_id if principal is not None else "*",
        }
        bind_cron_job_scope(job, scope)

        def _upsert(jobs: list[dict[str, Any]]) -> None:
            # Job names are tenant-local. A colliding name in another tenant
            # is preserved and its existence is not disclosed.
            jobs[:] = [
                existing
                for existing in jobs
                if not (existing.get("name") == name and cron_job_visible_to_scope(existing, scope))
            ]
            jobs.append(job)

        await asyncio.to_thread(_mutate_cron_jobs, path, _upsert)
        return _public_job(job)

    @router.delete("/api/cron/{name}")
    def api_cron_delete(name: str, request: Request) -> dict[str, Any]:
        principal = _operator_principal(request)
        scope = _request_scope(principal)

        def _remove(jobs: list[dict[str, Any]]) -> int:
            matching = [
                job
                for job in jobs
                if job.get("name") == name and cron_job_visible_to_scope(job, scope)
            ]
            # A global admin URL contains only the display name. If several
            # tenants use that same name, refusing is safer than deleting all.
            if scope is not None and scope.allow_cross_tenant and len(matching) > 1:
                return -1
            if not matching:
                return 0
            target_ids = {id(job) for job in matching}
            jobs[:] = [job for job in jobs if id(job) not in target_ids]
            return len(matching)

        removed = _mutate_cron_jobs(path, _remove)
        if removed < 0:
            raise HTTPException(409, "cron job name is ambiguous across tenants")
        if removed == 0:
            # Do not reveal whether the name exists in another tenant.
            raise HTTPException(404, "cron job not found")
        return {"ok": True, "deleted": name}

    @router.get("/api/cron/runs")
    @router.get("/api/cron/runs/")
    def api_cron_runs(request: Request, limit: int = 50) -> dict[str, Any]:
        """Run history from the executor ledger (newest first).

        Non-admin actors only see runs of jobs they created — the same
        scoping rule as ``GET /api/cron``.
        """
        principal = _force_auth(request)
        scope = _request_scope(principal)
        from runtime.execution.cron_executor import read_run_ledger

        limit = max(1, min(int(limit or 50), 200))
        ledger = path.parent / "cron_runs.jsonl"
        runs = read_run_ledger(ledger, limit=limit)
        runs = [record for record in runs if _run_visible_to_scope(record, scope)]
        public_runs = [_public_run(record) for record in runs]
        return {"ok": True, "runs": public_runs, "count": len(public_runs)}

    return router
