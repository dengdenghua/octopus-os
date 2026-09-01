"""Approval, trust-denial, token-usage, checkpoint, and resume endpoint handlers
for the agent trace router.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Query, Request

from ._agent_trace_router_stores import (
    RouterDeps,
    _get_review_queue,
    _get_store,
    _queue_repeated_trust_denials,
)


def register_approvals_endpoints(router, deps: RouterDeps) -> None:
    @router.get("/api/agent-trace/approvals")
    def api_agent_trace_approvals(
        thread_id: str | None = Query(default=None),
        tool_call_id: str | None = Query(default=None),
        decision: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        rows = _get_store(store=deps.store, db_path=deps.db_path).approvals(
            thread_id=thread_id,
            tool_call_id=tool_call_id,
            decision=decision,
            limit=limit,
            offset=offset,
        )
        return {"approvals": rows, "limit": limit, "offset": offset}

    @router.get("/api/agent-trace/trust-denials/summary")
    def api_agent_trace_trust_denials_summary(
        request: Request,
        thread_id: str | None = Query(default=None),
        turn_id: str | None = Query(default=None),
        task_id: str | None = Query(default=None),
        agent_id: str | None = Query(default=None),
        limit: int = Query(default=1000, ge=1, le=5000),
        queue_repeated: bool = Query(default=False),
        min_occurrences: int = Query(default=2, ge=1, le=100),
    ) -> dict[str, Any]:
        from runtime.safety.audit.trust_gateway import summarize_trust_denials

        rows = _get_store(store=deps.store, db_path=deps.db_path).approvals(
            thread_id=thread_id,
            turn_id=turn_id,
            task_id=task_id,
            agent_id=agent_id,
            limit=limit,
        )
        summary = summarize_trust_denials(rows)
        if queue_repeated:
            # Injecting review-queue rows is a state mutation; gate it behind
            # auth like the sibling write/install endpoints (the read-only
            # summary path stays open like other GETs).
            deps.auth(request, force=True)
            summary["queue"] = _queue_repeated_trust_denials(
                summary,
                review_queue=_get_review_queue(
                    review_queue=deps.review_queue,
                    review_queue_path=deps.review_queue_path,
                ),
                min_occurrences=min_occurrences,
            )
        return summary

    @router.get("/api/agent-trace/token-usage")
    def api_agent_trace_token_usage(
        task_id: str | None = Query(default=None),
        thread_id: str | None = Query(default=None),
        agent_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        rows = _get_store(store=deps.store, db_path=deps.db_path).token_usage(
            task_id=task_id,
            thread_id=thread_id,
            agent_id=agent_id,
            limit=limit,
            offset=offset,
        )
        return {"usage": rows, "limit": limit, "offset": offset}

    @router.get("/api/agent-trace/checkpoints")
    def api_agent_trace_checkpoints(
        task_id: str | None = Query(default=None),
        thread_id: str | None = Query(default=None),
        turn_id: str | None = Query(default=None),
        agent_id: str | None = Query(default=None),
        checkpoint_type: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        rows = _get_store(store=deps.store, db_path=deps.db_path).checkpoints(
            task_id=task_id,
            thread_id=thread_id,
            turn_id=turn_id,
            agent_id=agent_id,
            checkpoint_type=checkpoint_type,
            limit=limit,
            offset=offset,
        )
        return {"checkpoints": rows, "limit": limit, "offset": offset}

    @router.get("/api/agent-trace/checkpoints/latest")
    def api_agent_trace_latest_checkpoint(
        task_id: str,
        checkpoint_type: str | None = Query(default=None),
    ) -> dict[str, Any]:
        checkpoint = _get_store(store=deps.store, db_path=deps.db_path).latest_checkpoint(
            task_id=task_id,
            checkpoint_type=checkpoint_type,
        )
        if checkpoint is None:
            raise HTTPException(404, "checkpoint not found")
        return {"checkpoint": checkpoint}

    @router.get("/api/agent-trace/checkpoints/{checkpoint_id}/resume-proposal")
    def api_agent_trace_resume_proposal(checkpoint_id: int) -> dict[str, Any]:
        proposal = _get_store(store=deps.store, db_path=deps.db_path).resume_proposal(checkpoint_id)
        if proposal is None:
            raise HTTPException(404, "checkpoint not found")
        return {"proposal": proposal}

    @router.get("/api/agent-trace/resume-proposals")
    def api_agent_trace_resume_proposals(
        task_id: str | None = Query(default=None),
        thread_id: str | None = Query(default=None),
        turn_id: str | None = Query(default=None),
        agent_id: str | None = Query(default=None),
        checkpoint_type: str | None = Query(default=None),
        limit: int = Query(default=5, ge=1, le=50),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        proposals = _get_store(store=deps.store, db_path=deps.db_path).resume_proposals(
            task_id=task_id,
            thread_id=thread_id,
            turn_id=turn_id,
            agent_id=agent_id,
            checkpoint_type=checkpoint_type,
            limit=limit,
            offset=offset,
        )
        return {"proposals": proposals, "limit": limit, "offset": offset}

    @router.get("/api/agent-trace/resume-requests")
    def api_agent_trace_resume_requests(
        thread_id: str | None = Query(default=None),
        checkpoint_id: int | None = Query(default=None),
        status: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        rows = _get_store(store=deps.store, db_path=deps.db_path).resume_requests(
            thread_id=thread_id,
            checkpoint_id=checkpoint_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        return {"requests": rows, "limit": limit, "offset": offset}
