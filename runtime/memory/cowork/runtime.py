"""Runtime wiring for cowork background tasks.

This module turns the cowork data model into a live service: one shared group
store, one shared async-task store, and a runner that dispatches tasks through
the existing subagent bridge. The HTTP router can use the same stores, so
``POST /api/cowork/*/tasks`` feeds the background runner instead of a separate
queue instance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from runtime.memory.cowork.async_runner import AsyncWorkRunner
from runtime.memory.cowork.async_work import AsyncTask, AsyncWorkStore
from runtime.memory.cowork.collaboration_store import CollaborationStore
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.nominate import CompetenceStore

_LOG = logging.getLogger("echo.cowork.runtime")


@dataclass
class CoworkRuntime:
    group_store: GroupStore
    async_store: AsyncWorkStore
    collaboration_store: CollaborationStore
    runner: AsyncWorkRunner | None = None
    runner_enabled: bool = False
    runner_reason: str = "disabled"
    thread_store: Any | None = None

    def start(self, *, poll_seconds: float = 5.0) -> None:
        if self.runner is not None:
            self.runner.start(poll_seconds=poll_seconds)

    def stop(self, timeout: float = 5.0) -> None:
        if self.runner is not None:
            self.runner.stop(timeout=timeout)

    def status(self, thread_id: str | None = None) -> dict[str, Any]:
        runner_status = self.runner.status() if self.runner is not None else None
        return {
            "runner_enabled": self.runner_enabled,
            "runner_reason": self.runner_reason,
            "runner_status": runner_status,
            "task_counts": self.async_store.counts(thread_id),
        }


def create_cowork_runtime(
    *,
    base_dir: Any = None,
    thread_store: Any = None,
    enable_runner: bool = True,
) -> CoworkRuntime:
    """Build the shared cowork runtime used by app wiring and tests."""
    group_store = GroupStore(base_dir=base_dir)
    async_store = AsyncWorkStore(base_dir=group_store.base_dir, group_store=group_store)
    collaboration_store = CollaborationStore(base_dir=group_store.base_dir)
    runner: AsyncWorkRunner | None = None
    runner_enabled, runner_reason = _subagent_execution_available()
    if enable_runner and runner_enabled:
        runner = AsyncWorkRunner(
            async_store,
            group_store,
            _execute_subagent_task,
            competence=CompetenceStore(base_dir=group_store.base_dir),
            history_provider=_history_provider(thread_store),
        )
    elif not enable_runner:
        runner_reason = "runner disabled by app configuration"
    return CoworkRuntime(
        group_store=group_store,
        async_store=async_store,
        collaboration_store=collaboration_store,
        runner=runner,
        runner_enabled=runner is not None,
        runner_reason=runner_reason,
        thread_store=thread_store,
    )


def _subagent_execution_available() -> tuple[bool, str]:
    try:
        from runtime.execution.subagents import get_sub_agent_runner
        from runtime.execution.suckers.ephemeral_agents import (
            get_ephemeral_role_runner,
        )

        if get_sub_agent_runner() is not None:
            return True, "persistent subagent runner configured"
        runner = get_ephemeral_role_runner()
        if getattr(runner, "__name__", "") != "_null_ephemeral_runner":
            return True, "ephemeral subagent runner configured"
        return False, "subagent runner not configured"
    except Exception as exc:  # noqa: BLE001
        return False, f"subagent runner probe failed: {type(exc).__name__}: {exc}"


def _history_provider(thread_store: Any):
    def _history(thread_id: str) -> list[Any]:
        if thread_store is None:
            return []
        get_state = getattr(thread_store, "get_state", None)
        if not callable(get_state):
            return []
        try:
            state = get_state(thread_id)
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("cowork history lookup failed for %s: %s", thread_id, exc)
            return []
        values = state.get("values") if isinstance(state, dict) else None
        messages = values.get("messages") if isinstance(values, dict) else None
        return messages if isinstance(messages, list) else []

    return _history


def _execute_subagent_task(task: AsyncTask, context: dict[str, Any]) -> str:
    from runtime.execution.subagents import call_subagent
    from runtime.platform.process.session import Session, current_session

    # The async runner normally executes in its daemon thread, where the
    # request ContextVar is intentionally empty. Give each durable cowork task
    # its own host turn so recursive children cannot bypass the shared
    # governance ledger, and so journal/trace entries remain attributable to
    # the persisted task after a process restart. A caller that drains a task
    # synchronously still contributes its trusted metadata (for example a
    # tenant id), but never reuses the caller's execution lease for this
    # independent background job.
    parent = current_session()
    inherited_metadata: dict[str, Any] = {}
    # Only carry the stable tenant coordinate from an ambient foreground
    # session. Ephemeral fields such as ``_locked_write_root``, approval
    # handles, and parent tool ids belong to that foreground turn and must not
    # leak into a durable background job.
    parent_metadata = getattr(parent, "metadata", None) if parent else None
    if isinstance(parent_metadata, dict) and parent_metadata.get("tenant_id"):
        inherited_metadata["tenant_id"] = parent_metadata["tenant_id"]
    inherited_metadata.update(
        {
            "source": "cowork_async_task",
            "cowork_task_id": task.task_id,
        }
    )
    task_session = Session(
        actor=str(task.created_by or getattr(parent, "actor", None) or "").strip() or None,
        thread_id=task.thread_id,
        conversation_id=task.thread_id,
        turn_id=f"cowork:{task.task_id}",
        metadata=inherited_metadata,
    )

    result = call_subagent(
        task.assignee,
        task.prompt,
        context={
            "thread_id": task.thread_id,
            "parent_task_id": task.task_id,
            "source": "cowork_async_task",
            "cowork": context,
        },
        timeout_s=900,
        timeout_seconds=900.0,
        session=task_session,
    )
    if not result.get("success"):
        raise RuntimeError(str(result.get("error") or "subagent failed"))
    output = result.get("output")
    if output is None:
        parsed = result.get("parsed")
        if parsed is not None:
            output = parsed
    return str(output or "")


__all__ = ["CoworkRuntime", "create_cowork_runtime"]
