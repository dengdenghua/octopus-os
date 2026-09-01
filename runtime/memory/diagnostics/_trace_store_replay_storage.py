"""Replay-review methods layered on top of the trace storage primitives."""

from __future__ import annotations

from typing import Any

from runtime.safety.auth.scope import TenantScope

from ._trace_store_models import (
    TaskRunStatus,
    _evaluate_task_run_replay_case,
    _replay_gate_from_evaluations,
    _task_run_replay_case_from_review,
    _task_run_review_from_loop_checkpoint,
    _task_run_review_from_run,
)


class _TraceStoreReplayMixin:
    def task_run_review(
        self: Any, task_id: str, *, scope: TenantScope | None = None
    ) -> dict[str, Any] | None:
        run = self.task_run(task_id, scope=scope)
        if run is None:
            return None
        loop_checkpoint = self.latest_checkpoint(
            task_id=str(run["task_id"]), checkpoint_type="loop_run", scope=scope
        )
        if isinstance(loop_checkpoint, dict):
            return _task_run_review_from_loop_checkpoint(run, loop_checkpoint)
        return _task_run_review_from_run(
            run, self._approvals_for_task(str(run["task_id"]), scope=scope)
        )

    def task_run_replay_case(
        self: Any, task_id: str, *, scope: TenantScope | None = None
    ) -> dict[str, Any] | None:
        review = self.task_run_review(task_id, scope=scope)
        return None if review is None else _task_run_replay_case_from_review(review)

    def evaluate_task_run_replay_case(
        self: Any, task_id: str, *, scope: TenantScope | None = None
    ) -> dict[str, Any] | None:
        replay_case = self.task_run_replay_case(task_id, scope=scope)
        return None if replay_case is None else _evaluate_task_run_replay_case(replay_case)

    def task_run_replay_cases(
        self: Any,
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        agent_id: str | None = None,
        status: TaskRunStatus | None = None,
        limit: int = 100,
        offset: int = 0,
        scope: TenantScope | None = None,
    ) -> dict[str, Any]:
        if status is None:
            rows = self._task_run_ids(
                thread_id=thread_id,
                turn_id=turn_id,
                agent_id=agent_id,
                limit=limit,
                offset=offset,
                scope=scope,
            )
            task_ids = [str(row["task_id"]) for row in rows]
        else:
            runs = self.task_runs(
                thread_id=thread_id,
                turn_id=turn_id,
                agent_id=agent_id,
                status=status,
                limit=limit,
                offset=offset,
                scope=scope,
            )
            task_ids = [str(run.get("task_id") or "") for run in runs]
        cases = [
            case
            for task_id in task_ids
            if (case := self.task_run_replay_case(task_id, scope=scope)) is not None
        ]
        return {
            "schema": "echo.task_run_replay_case_corpus.v1",
            "cases": cases,
            "total": len(cases),
            "limit": limit,
            "offset": offset,
        }

    def evaluate_task_run_replay_cases(
        self: Any,
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        agent_id: str | None = None,
        status: TaskRunStatus | None = None,
        limit: int = 100,
        offset: int = 0,
        scope: TenantScope | None = None,
    ) -> dict[str, Any]:
        corpus = self.task_run_replay_cases(
            thread_id=thread_id,
            turn_id=turn_id,
            agent_id=agent_id,
            status=status,
            limit=limit,
            offset=offset,
            scope=scope,
        )
        evaluations = [
            _evaluate_task_run_replay_case(case)
            for case in corpus.get("cases", [])
            if isinstance(case, dict)
        ]
        return {
            "schema": "echo.task_run_replay_evaluation_corpus.v1",
            "passed": sum(1 for item in evaluations if item.get("passed") is True),
            "failed": sum(1 for item in evaluations if item.get("passed") is False),
            "total": len(evaluations),
            "limit": limit,
            "offset": offset,
            "evaluations": evaluations,
        }

    def replay_gate(
        self: Any,
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        agent_id: str | None = None,
        status: TaskRunStatus | None = None,
        min_cases: int = 1,
        min_score: float = 1.0,
        limit: int = 100,
        offset: int = 0,
        scope: TenantScope | None = None,
    ) -> dict[str, Any]:
        corpus = self.evaluate_task_run_replay_cases(
            thread_id=thread_id,
            turn_id=turn_id,
            agent_id=agent_id,
            status=status,
            limit=limit,
            offset=offset,
            scope=scope,
        )
        evaluations = [item for item in corpus.get("evaluations", []) if isinstance(item, dict)]
        return _replay_gate_from_evaluations(
            evaluations,
            min_cases=min_cases,
            min_score=min_score,
            filters={
                "thread_id": thread_id,
                "turn_id": turn_id,
                "agent_id": agent_id,
                "status": status,
                "limit": limit,
                "offset": offset,
            },
        )

    def replay_gate_for_task_ids(
        self: Any,
        task_ids: list[str],
        *,
        min_cases: int = 1,
        min_score: float = 1.0,
        scope: TenantScope | None = None,
    ) -> dict[str, Any]:
        clean_task_ids = [task_id for task_id in dict.fromkeys(task_ids) if task_id]
        evaluations = [
            evaluation
            for task_id in clean_task_ids
            if (evaluation := self.evaluate_task_run_replay_case(task_id, scope=scope)) is not None
        ]
        return _replay_gate_from_evaluations(
            evaluations,
            min_cases=min_cases,
            min_score=min_score,
            filters={"task_ids": clean_task_ids},
        )


__all__ = ["_TraceStoreReplayMixin"]
