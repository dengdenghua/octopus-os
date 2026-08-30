"""Review, experience-ledger, and review-queue endpoint handlers for the agent
trace router.

These endpoints cover committing/queueing task-run reviews, querying the
experience ledger (records, weekly/quality summaries, recall), and managing
the review queue (items, summary, decisions).
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Query, Request

from ._agent_trace_router_stores import (
    RouterDeps,
    _get_experience_ledger,
    _get_review_queue,
    _get_store,
    _scope_for_request,
)


def register_review_endpoints(router, deps: RouterDeps) -> None:
    @router.post("/api/agent-trace/task-runs/{task_id}/review/commit")
    def api_agent_trace_commit_task_run_review(request: Request, task_id: str) -> dict[str, Any]:
        scope = _scope_for_request(request)
        review = _get_store(store=deps.store, db_path=deps.db_path).task_run_review(
            task_id, scope=scope
        )
        if review is None:
            raise HTTPException(404, "task run not found")
        result = _get_experience_ledger(
            experience_ledger=deps.experience_ledger,
            experience_ledger_path=deps.experience_ledger_path,
            scope=scope,
        ).add_from_task_run_review(review, scope=scope)
        return {"commit": result}

    @router.post("/api/agent-trace/task-runs/{task_id}/review/queue")
    def api_agent_trace_queue_task_run_review(request: Request, task_id: str) -> dict[str, Any]:
        scope = _scope_for_request(request)
        review = _get_store(store=deps.store, db_path=deps.db_path).task_run_review(
            task_id, scope=scope
        )
        if review is None:
            raise HTTPException(404, "task run not found")
        result = _get_review_queue(
            review_queue=deps.review_queue,
            review_queue_path=deps.review_queue_path,
            scope=scope,
        ).add_from_task_run_review(review, scope=scope)
        return {"queue": result}

    @router.get("/api/agent-trace/experience-ledger")
    def api_agent_trace_experience_ledger(
        request: Request,
        status: str | None = Query(default=None),
        bucket: str | None = Query(default=None),
        kind: str | None = Query(default=None),
        priority: str | None = Query(default=None),
        include_contradicted: bool = Query(default=False),
        min_reliability: float = Query(default=0.0, ge=0.0, le=1.0),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        scope = _scope_for_request(request)
        return _get_experience_ledger(
            experience_ledger=deps.experience_ledger,
            experience_ledger_path=deps.experience_ledger_path,
            scope=scope,
        ).records(
            status=status,
            bucket=bucket,
            kind=kind,
            priority=priority,
            include_contradicted=include_contradicted,
            min_reliability=min_reliability,
            limit=limit,
            offset=offset,
            scope=scope,
        )

    @router.get("/api/agent-trace/experience-ledger/weekly-summary")
    def api_agent_trace_experience_weekly_summary(
        request: Request,
        week_start: str | None = Query(default=None),
    ) -> dict[str, Any]:
        scope = _scope_for_request(request)
        return _get_experience_ledger(
            experience_ledger=deps.experience_ledger,
            experience_ledger_path=deps.experience_ledger_path,
            scope=scope,
        ).weekly_summary(week_start=week_start, scope=scope)

    @router.get("/api/agent-trace/experience-ledger/quality-summary")
    def api_agent_trace_experience_quality_summary(
        request: Request,
        limit: int = Query(default=10000, ge=1, le=50000),
    ) -> dict[str, Any]:
        scope = _scope_for_request(request)
        return _get_experience_ledger(
            experience_ledger=deps.experience_ledger,
            experience_ledger_path=deps.experience_ledger_path,
            scope=scope,
        ).quality_summary(limit=limit, scope=scope)

    @router.get("/api/agent-trace/experience-ledger/recall")
    def api_agent_trace_experience_recall(
        request: Request,
        q: str = Query(default=""),
        bucket: str | None = Query(default=None),
        min_reliability: float = Query(default=0.0, ge=0.0, le=1.0),
        limit: int = Query(default=10, ge=1, le=50),
    ) -> dict[str, Any]:
        scope = _scope_for_request(request)
        return _get_experience_ledger(
            experience_ledger=deps.experience_ledger,
            experience_ledger_path=deps.experience_ledger_path,
            scope=scope,
        ).recall(
            q,
            bucket=bucket,
            min_reliability=min_reliability,
            limit=limit,
            scope=scope,
        )

    @router.get("/api/agent-trace/review-queue")
    def api_agent_trace_review_queue(
        request: Request,
        status: str | None = Query(default=None),
        target_bucket: str | None = Query(default=None),
        priority: str | None = Query(default=None),
        source_task_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        scope = _scope_for_request(request)
        return _get_review_queue(
            review_queue=deps.review_queue,
            review_queue_path=deps.review_queue_path,
            scope=scope,
        ).items(
            status=status,
            target_bucket=target_bucket,
            priority=priority,
            source_task_id=source_task_id,
            limit=limit,
            offset=offset,
            scope=scope,
        )

    @router.get("/api/agent-trace/review-queue/summary")
    def api_agent_trace_review_queue_summary(request: Request) -> dict[str, Any]:
        scope = _scope_for_request(request)
        return _get_review_queue(
            review_queue=deps.review_queue,
            review_queue_path=deps.review_queue_path,
            scope=scope,
        ).summary(scope=scope)

    @router.post("/api/agent-trace/review-queue/{item_id}/decision")
    def api_agent_trace_review_queue_decision(
        request: Request,
        item_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            scope = _scope_for_request(request)
            return _get_review_queue(
                review_queue=deps.review_queue,
                review_queue_path=deps.review_queue_path,
                scope=scope,
            ).decide(
                item_id,
                action=str(payload.get("action") or ""),
                reason=str(payload.get("reason") or ""),
                promoted_to=payload.get("promoted_to"),
                scope=scope,
            )
        except KeyError:
            raise HTTPException(404, "review queue item not found") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
