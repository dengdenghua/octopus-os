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

    # ResearchJob and its persistent job store are process-global and do not
    # yet carry actor/tenant ownership. Keep the entire surface operational
    # until that schema can be migrated safely.
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

    def _auth(request: Any) -> str | None:
        """Authenticate and return actor_id.

        WARNING: DeepResearchJob does NOT yet track per-job ownership.
        Until it does, any authenticated user can read/start on any
        other user's jobs. The actor returned here is currently unused
        by the endpoints — marked with ``AUTH-OK: actor-agnostic``
        because the resource model itself doesn't support scoping.
        Plug ownership into ``DeepResearchJob`` and update endpoints
        to enforce it as a follow-up (parallel to what we did for
        ParallelAgentOrchestrator).
        """
        from .openai_gateway_router import _resolve_actor

        return _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

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

    def _refresh_job_from_batch(job: ResearchJob) -> ResearchJob:
        if orchestrator is None or not job.dispatch_batch_id:
            return job
        batch = orchestrator.get_batch(job.dispatch_batch_id)
        if batch is None:
            return job

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
        _auth(
            request
        )  # AUTH-OK: actor-agnostic — DeepResearchJob has no owner field (see _auth docstring)
        job = _build_job(body)
        jobs[job.job_id] = job
        _save_job(job)
        return job.model_dump()

    @router.post("/api/research/deep/start")
    def start_deep_research(request: Request, body: DeepResearchRequest) -> dict[str, Any]:
        _auth(
            request
        )  # AUTH-OK: actor-agnostic — DeepResearchJob has no owner field (see _auth docstring)
        job = _build_job(body)
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
                        "research_prefetch_logs": [log.model_dump() for log in job.prefetch_logs],
                    },
                )
                job.dispatch_batch_id = batch.batch_id
                job.status = "running"
            except (ConnectionError, TimeoutError, OSError) as exc:
                raise HTTPException(500, f"failed to dispatch research tasks: {exc}") from exc
        jobs[job.job_id] = job
        _save_job(job)
        return job.model_dump()

    @router.get("/api/research/deep/jobs/{job_id}")
    def get_deep_research_job(request: Request, job_id: str) -> dict[str, Any]:
        _auth(
            request
        )  # AUTH-OK: actor-agnostic — DeepResearchJob has no owner field (see _auth docstring)
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"research job not found: {job_id}")
        job = _refresh_job_from_batch(job)
        jobs[job.job_id] = job
        return job.model_dump()

    @router.get("/api/research/deep/jobs")
    def list_deep_research_jobs(request: Request) -> dict[str, Any]:
        _auth(
            request
        )  # AUTH-OK: actor-agnostic — DeepResearchJob has no owner field (see _auth docstring)
        return {
            "jobs": [_refresh_job_from_batch(job).model_dump() for job in jobs.values()],
            "count": len(jobs),
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
