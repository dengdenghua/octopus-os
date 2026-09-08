"""Deep research API router."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException, Request

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment,misc]
    Depends = None  # type: ignore[assignment,misc]
    HTTPException = None  # type: ignore[assignment,misc]
    Request = None  # type: ignore[assignment,misc]

from runtime.platform.process.paths import app_paths, project_root
from runtime.platform.runtime_policy.workspaces import WorkspaceManager
from runtime.research.deep_research import (
    DeepResearchPlanner,
    DeepResearchRequest,
    ResearchJob,
    ResearchMaterial,
    ResearchRouteDecision,
)
from runtime.research.prefetch import ResearchPrefetcher
from runtime.sensing._fastapi_guard import require_fastapi


def create_deep_research_router(
    *,
    orchestrator: Any = None,
    workspace_root: Path | None = None,
    upload_root: Path | None = None,
    agents_root: Path | None = None,
    job_store_path: Path | None = None,
    review_queue_path: Path | None = None,
    prefetcher: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    """Create `/api/research/deep/*` endpoints."""
    require_fastapi(__name__)

    def _operator_dep(request: Request) -> None:
        from runtime.safety.auth.principal import require_roles

        require_roles(
            request,
            identity_store,
            require_auth,
            ("admin", "operator"),
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    router = APIRouter(
        tags=["deep-research"],
        dependencies=[Depends(_operator_dep)],
    )
    planner = DeepResearchPlanner()
    source_prefetcher = prefetcher or ResearchPrefetcher()
    store_path = job_store_path or _default_job_store_path()
    route_review_queue_path = review_queue_path or app_paths().review_queue_path
    jobs: dict[str, ResearchJob] = _load_jobs(store_path)
    workspace_manager = WorkspaceManager(workspace_root) if workspace_root else None

    def _principal(request: Any) -> Any:
        from runtime.safety.auth.principal import resolve_principal

        cached = getattr(getattr(request, "state", None), "principal", None)
        if cached is not None:
            return cached
        return resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _bind_owner(job: ResearchJob, principal: Any) -> ResearchJob:
        if principal is not None:
            job.owner_id = str(getattr(principal, "actor_id", "") or "").strip() or None
            job.tenant_id = str(getattr(principal, "tenant_id", "") or "").strip() or None
        return job

    def _is_admin(principal: Any) -> bool:
        roles = getattr(principal, "roles", ()) or ()
        return "admin" in {str(role).strip().lower() for role in roles}

    def _job_visible(job: ResearchJob, principal: Any) -> bool:
        if not require_auth:
            return True
        if principal is None:
            return False
        # Admins can inspect legacy/unowned jobs during migration. Operators
        # must have both immutable coordinates; an owner-only match is not
        # sufficient when one actor belongs to more than one tenant.
        if _is_admin(principal):
            return True
        return bool(
            job.owner_id
            and job.tenant_id
            and job.owner_id == getattr(principal, "actor_id", None)
            and job.tenant_id == getattr(principal, "tenant_id", None)
        )

    def _require_job(request: Any, job_id: str) -> tuple[ResearchJob, Any]:
        principal = _principal(request)
        job = jobs.get(job_id)
        if job is None or not _job_visible(job, principal):
            raise HTTPException(404, f"research job not found: {job_id}")
        return job, principal

    def _thread_upload_materials(thread_id: str | None) -> list[ResearchMaterial]:
        if not thread_id:
            return []
        folders: list[Path] = []
        if workspace_manager is not None:
            folders.append(workspace_manager.layout(thread_id).upload)
        root = upload_root or (app_paths().data_dir / "thread_uploads")
        legacy = root / thread_id
        if legacy not in folders:
            folders.append(legacy)
        materials: list[ResearchMaterial] = []
        seen: set[str] = set()
        for folder in folders:
            if not folder.exists() or not folder.is_dir():
                continue
            for path in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
                if not path.is_file() or path.name in seen:
                    continue
                seen.add(path.name)
                materials.append(
                    ResearchMaterial(
                        kind="file",
                        title=path.name,
                        path=str(path),
                        notes="thread upload",
                    )
                )
        return materials

    def _build_job(body: DeepResearchRequest) -> ResearchJob:
        return planner.build_plan(
            body,
            thread_materials=_thread_upload_materials(body.thread_id),
        )

    def _prefetch_job_sources(job: ResearchJob) -> ResearchJob:
        try:
            result = source_prefetcher.prefetch(job)
        except (ConnectionError, TimeoutError, OSError):
            result = []
        if isinstance(result, list):
            evidence = result
            logs: list[Any] = []
        else:
            evidence = getattr(result, "evidence", [])
            logs = getattr(result, "logs", [])
        return planner.attach_evidence_pool(job, evidence, prefetch_logs=logs)

    def _save_job(job: ResearchJob) -> None:
        _append_job(store_path, job)

    def _recovery_snapshot_for_job(job: ResearchJob) -> Any | None:
        """Resolve a job's redacted recovery view without broadening scope."""

        if orchestrator is None:
            return None
        batch_id = str(job.dispatch_batch_id or "").strip()
        if not batch_id and str(job.host_task_id or "").startswith("parallel-batch:"):
            batch_id = str(job.host_task_id).removeprefix("parallel-batch:").strip()
        if not batch_id:
            return None
        snapshot_getter = getattr(orchestrator, "recovery_snapshot", None)
        if not callable(snapshot_getter):
            return None
        snapshot = snapshot_getter(batch_id)
        if snapshot is None:
            return None
        expected_host_task_id = str(job.host_task_id or "").strip()
        actual_host_task_id = str(getattr(snapshot, "host_task_id", None) or "").strip()
        if expected_host_task_id and actual_host_task_id != expected_host_task_id:
            return None
        return snapshot

    def _refresh_job_from_batch(job: ResearchJob) -> ResearchJob:
        batch_id = str(job.dispatch_batch_id or "").strip()
        if not batch_id and str(job.host_task_id or "").startswith("parallel-batch:"):
            batch_id = str(job.host_task_id).removeprefix("parallel-batch:").strip()
        if orchestrator is None or not batch_id:
            return job
        batch = orchestrator.get_batch(batch_id)
        if batch is None:
            snapshot = _recovery_snapshot_for_job(job)
            if snapshot is not None:
                durable_only = (
                    isinstance(getattr(snapshot, "safety", None), dict)
                    and snapshot.safety.get("durable_only") is True
                )
                job.recovery_required = bool(
                    durable_only and (job.status == "running" or not job.final_report)
                )
                job.recovery_reason = (
                    "durable_only_recovery_view" if job.recovery_required else None
                )
            return job

        # A live batch is authoritative again. Clear a stale restart marker
        # before applying the current batch lifecycle.
        job.recovery_required = False
        job.recovery_reason = None

        # Keep the durable host coordinate on the research job itself.  The
        # batch object is process-local and may disappear after a restart,
        # while TaskSupervisor retains the aggregate recovery record.
        if getattr(batch, "host_task_id", None) and job.host_task_id != batch.host_task_id:
            job.host_task_id = batch.host_task_id

        route_decisions_changed = _sync_route_decisions(job, batch)
        results_by_task = {result.task_id: result for result in batch.results}
        for step in job.steps:
            if step.role_id == "synthesis":
                if batch.status == "completed" and job.final_report:
                    step.status = "completed"
                continue
            result = results_by_task.get(step.id)
            if result is None:
                continue
            if result.status in ("pending", "running", "completed", "failed"):
                step.status = result.status
            elif result.status in ("cancelled", "timed_out"):
                step.status = "failed"

        if batch.status == "completed":
            job.status = "completed"
            if batch.aggregated_content and not job.final_report:
                job.evidence = planner.extract_evidence_from_outputs(
                    job,
                    aggregated_content=batch.aggregated_content,
                )
                job.final_report = planner.synthesize_report(
                    job,
                    aggregated_content=batch.aggregated_content,
                )
                job.completed_at = datetime.now(UTC).isoformat()
                job = planner.write_lead_memory(job, agents_root=agents_root)
                for step in job.steps:
                    if step.role_id == "synthesis":
                        step.status = "completed"
            _save_job(job)
        elif batch.status in ("failed", "timed_out", "partial"):
            job.status = "cancelled" if batch.cancelled_tasks else "failed"
            _save_job(job)
        elif batch.status == "cancelled":
            job.status = "cancelled"
            _save_job(job)
        elif batch.status == "running":
            job.status = "running"
            if route_decisions_changed:
                _save_job(job)
        return job

    @router.post("/api/research/deep/plan")
    def plan_deep_research(request: Request, body: DeepResearchRequest) -> dict[str, Any]:
        principal = _principal(request)
        job = _bind_owner(_build_job(body), principal)
        jobs[job.job_id] = job
        _save_job(job)
        return job.model_dump()

    @router.post("/api/research/deep/start")
    def start_deep_research(request: Request, body: DeepResearchRequest) -> dict[str, Any]:
        principal = _principal(request)
        job = _bind_owner(_build_job(body), principal)
        if body.prefetch_sources:
            job = _prefetch_job_sources(job)
        tasks = planner.dispatch_tasks(job)
        if orchestrator is not None and tasks:
            try:
                batch = orchestrator.dispatch(
                    tasks,
                    max_concurrency=body.max_subagents or len(tasks),
                    aggregation_strategy="research_synthesis",
                    execution_mode="parallel",
                    thread_id=body.thread_id,
                    context={
                        "lead_agent_name": body.lead_agent_name,
                        "task_risk_level": body.task_risk_level or "low",
                        "review_queue_path": str(route_review_queue_path),
                        "subagent_policy_path": str(app_paths().subagent_policy_path),
                        "research_job_id": job.job_id,
                        "research_ephemeral_workers": True,
                        "research_sources": [
                            source.model_dump() for source in job.sources if source.enabled
                        ],
                        "research_materials": [material.model_dump() for material in job.materials],
                        "research_roles": [role.model_dump() for role in job.roles],
                        "research_evidence": [evidence.model_dump() for evidence in job.evidence],
                        "research_prefetch_logs": [
                            log.model_dump() for log in job.prefetch_logs
                        ],
                    },
                    owner_id=getattr(principal, "actor_id", None) if principal else None,
                    tenant_id=getattr(principal, "tenant_id", None) if principal else None,
                )
                job.dispatch_batch_id = batch.batch_id
                job.host_task_id = getattr(batch, "host_task_id", None)
                job.status = "running"
            except (ConnectionError, TimeoutError, OSError) as exc:
                raise HTTPException(500, f"failed to dispatch research tasks: {exc}") from exc
        jobs[job.job_id] = job
        _save_job(job)
        return job.model_dump()

    @router.get("/api/research/deep/jobs/{job_id}")
    def get_deep_research_job(request: Request, job_id: str) -> dict[str, Any]:
        job, _principal_value = _require_job(request, job_id)
        job = _refresh_job_from_batch(job)
        jobs[job.job_id] = job
        return job.model_dump()

    @router.get("/api/research/deep/jobs/{job_id}/recovery-snapshot")
    def get_deep_research_recovery_snapshot(request: Request, job_id: str) -> dict[str, Any]:
        """Return the batch's redacted recovery view after a restart.

        A ResearchJob is the user-facing durable record, while the parallel
        orchestrator owns execution state.  Keep the lookup scoped by the
        job's immutable owner/tenant coordinates and require the returned
        snapshot to match the persisted host coordinate before exposing it.
        """

        job, _principal_value = _require_job(request, job_id)
        if orchestrator is None:
            raise HTTPException(404, f"research recovery not available: {job_id}")
        snapshot = _recovery_snapshot_for_job(job)
        if snapshot is None:
            raise HTTPException(404, f"research recovery not available: {job_id}")
        return snapshot.model_dump()

    @router.get("/api/research/deep/jobs")
    def list_deep_research_jobs(request: Request) -> dict[str, Any]:
        principal = _principal(request)
        visible_jobs = [job for job in jobs.values() if _job_visible(job, principal)]
        return {
            "jobs": [_refresh_job_from_batch(job).model_dump() for job in visible_jobs],
            "count": len(visible_jobs),
        }

    return router


def _default_job_store_path() -> Path:
    root = project_root()
    current = root / ".echo" / "research" / "deep-research-jobs.jsonl"
    legacy = root / ".echo-research" / "deep-research-jobs.jsonl"
    if not current.exists() and legacy.exists():
        return legacy
    return current


def _load_jobs(path: Path) -> dict[str, ResearchJob]:
    if not path.exists():
        return {}
    jobs: dict[str, ResearchJob] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            job = ResearchJob.model_validate_json(line)
        except (json.JSONDecodeError, ValueError):
            try:
                job = ResearchJob.model_validate(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
        jobs[job.job_id] = job
    return jobs


def _append_job(path: Path, job: ResearchJob) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(job.model_dump_json() + "\n")


def _sync_route_decisions(job: ResearchJob, batch: Any) -> bool:
    old = [decision.model_dump() for decision in job.route_decisions]
    decisions = _route_decisions_from_batch(batch)
    if not decisions:
        return False
    job.route_decisions = decisions
    by_step = {decision.step_id: decision for decision in decisions if decision.step_id}
    for step in job.steps:
        if step.id in by_step:
            step.route_decision = by_step[step.id]
    new = [decision.model_dump() for decision in job.route_decisions]
    return new != old


def _route_decisions_from_batch(batch: Any) -> list[ResearchRouteDecision]:
    latest: dict[str, ResearchRouteDecision] = {}
    for event in getattr(batch, "event_log", None) or []:
        payload = getattr(event, "payload", None)
        if not isinstance(payload, dict):
            continue
        raw = payload.get("subagent_route_decision")
        if not isinstance(raw, dict):
            continue
        if raw.get("schema") != "echo.subagent_route_decision.v1":
            continue
        step_id = _clean_route_text(getattr(event, "task_id", None))
        role = _clean_route_text(raw.get("role"))
        if not step_id and not role:
            continue
        decision = ResearchRouteDecision(
            step_id=step_id or None,
            task_id=step_id or None,
            role=role,
            action=_clean_route_text(raw.get("action")) or "allow",
            reason=_clean_route_text(raw.get("reason"), limit=600),
            risk_level=_clean_route_text(raw.get("risk_level")) or "low",
            verdict=_clean_route_text(raw.get("verdict")) or "unknown",
            score=_float_or_none(raw.get("score")),
            confidence=_float_or_default(raw.get("confidence")),
            evidence_item_ids=[
                item
                for item in (
                    _clean_route_text(value) for value in (raw.get("evidence_item_ids") or [])
                )
                if item
            ],
            phase=_clean_route_text(getattr(event, "phase", None)) or None,
            created_at=_clean_route_text(getattr(event, "created_at", None)) or None,
        )
        latest[step_id or role] = decision
    return list(latest.values())


def _clean_route_text(value: Any, *, limit: int = 120) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float_or_default(value: Any) -> float:
    parsed = _float_or_none(value)
    return parsed if parsed is not None else 0.0


__all__ = ["create_deep_research_router"]
