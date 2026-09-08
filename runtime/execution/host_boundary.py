"""Construct the trusted execution boundary for non-realtime callers."""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import Any, Literal

from runtime.execution.artifact_contracts import HandoffRecorder
from runtime.execution.request import (
    ExecutionRequest,
    ExecutionResources,
    ExecutionTask,
)
from runtime.platform.config.schema import BudgetConfig
from runtime.platform.process.scope import resolve_execution_scope
from runtime.platform.process.session import Session, current_session, session_scope

_PRIVATE_METADATA_KEYS = frozenset(
    {
        "_execution_request",
        "_execution_task",
        "_execution_handoff_recorder",
        "_file_write_leases",
        "_file_write_lease_handoffs",
        "_file_write_lease_history",
        "_file_read_snapshots",
    }
)


@dataclass(frozen=True, slots=True)
class HostExecutionBoundary:
    session: Session
    request: ExecutionRequest


@contextmanager
def host_execution_scope(
    *,
    supervisor: Any | None,
    task_id: str,
    thread_id: str,
    goal: str,
    timeout_s: float,
    actor_id: str | None = None,
    tenant_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    parent_task_id: str | None = None,
    kind: str = "task",
    mode: str = "",
    workspace_path: str | None = None,
    budget: BudgetConfig | None = None,
) -> Iterator[Session | None]:
    """Run one non-realtime entrypoint inside the host execution contract.

    A realtime/Codex caller that already owns a host request is inherited after
    its coordinates are checked.  A standalone entrypoint admits one fresh
    ``TaskExecutionGuard`` when a supervisor is supplied.  ``supervisor=None``
    keeps the historical local/test path unchanged while still allowing the
    same call site to opt into the boundary in production.
    """

    parent = current_session()
    parent_request = parent.execution_request if isinstance(parent, Session) else None
    normalized_thread = str(thread_id or "").strip()
    normalized_actor = str(actor_id or "").strip() or None
    normalized_tenant = str(tenant_id or "").strip() or None
    if not normalized_thread:
        raise ValueError("host execution requires a thread coordinate")

    if isinstance(parent_request, ExecutionRequest):
        parent_task = parent_request.task
        if parent_task.thread_id != normalized_thread:
            raise ValueError("child execution thread does not match host task")
        if parent_task.actor_id != normalized_actor:
            raise ValueError("child execution actor does not match host task")
        if parent_task.tenant_id != normalized_tenant:
            raise ValueError("child execution tenant does not match host task")
        # Re-enter the session so the provider-neutral ContextVar is present
        # even when a caller passed the session explicitly into a worker.
        with session_scope(parent):
            yield parent
        return

    if supervisor is None:
        yield None
        return

    from runtime.platform.process._task_supervisor_models import TaskRunStatus
    from runtime.platform.process.task_execution import TaskExecutionGuard

    guard = TaskExecutionGuard(supervisor)
    boundary = create_host_execution_boundary(
        task_id=task_id,
        thread_id=normalized_thread,
        actor_id=normalized_actor,
        tenant_id=normalized_tenant,
        goal=goal,
        timeout_s=timeout_s,
        metadata=metadata,
        parent_task_id=parent_task_id,
        budget=budget,
    )
    boundary.session.execution_lease = guard
    try:
        guard.admit(
            task_id=str(task_id or "").strip(),
            kind=kind,
            owner_id=normalized_actor,
            thread_id=normalized_thread,
            title=str(goal or "").strip()[:200],
            goal=str(goal or "").strip(),
            mode=mode,
            workspace_path=workspace_path,
            parent_task_id=parent_task_id,
            metadata={
                "host_execution": "octopus.execution.v1",
                # TaskRunRecord keeps the principal owner in ``owner_id``;
                # retain the tenant coordinate in metadata so observers can
                # apply the same tenant filter without reconstructing it from
                # a mutable Session.
                "tenant_id": normalized_tenant,
            },
        )
        with session_scope(boundary.session):
            try:
                yield boundary.session
            except BaseException as exc:
                # Preserve the original exception while making the durable
                # execution state recoverable and visible to the task UI.
                with suppress(Exception):
                    guard.transition(
                        TaskRunStatus.FAILED,
                        reason=f"{type(exc).__name__}: {exc}"[:500],
                    )
                raise
            else:
                guard.transition(TaskRunStatus.COMPLETED, reason="host scope completed")
    finally:
        guard.close()


def inherit_host_execution_session(
    parent: Session,
    *,
    thread_id: str,
    actor_id: str | None,
    tenant_id: str | None,
    metadata: Mapping[str, Any] | None = None,
) -> Session:
    """Create a child Session without allowing boundary replacement."""

    request = parent.execution_request
    if not isinstance(request, ExecutionRequest):
        raise ValueError("parent session has no host execution request")
    task = request.task
    if str(thread_id or "").strip() != task.thread_id:
        raise ValueError("child orchestration thread does not match host task")
    if (str(actor_id or "").strip() or None) != task.actor_id:
        raise ValueError("child orchestration actor does not match host task")
    if (str(tenant_id or "").strip() or None) != task.tenant_id:
        raise ValueError("child orchestration tenant does not match host task")

    merged = dict(parent.metadata)
    additions = dict(metadata or {})
    for key in _PRIVATE_METADATA_KEYS:
        additions.pop(key, None)
    merged.update(additions)
    return Session(
        actor=parent.actor,
        agent=parent.agent,
        thread_id=parent.thread_id,
        conversation_id=parent.conversation_id,
        turn_id=parent.turn_id,
        started_at=parent.started_at,
        metadata=merged,
        execution_lease=parent.execution_lease,
        execution_request=request,
    )


def create_host_execution_boundary(
    *,
    task_id: str,
    thread_id: str,
    goal: str,
    timeout_s: float,
    actor_id: str | None = None,
    tenant_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    parent_task_id: str | None = None,
    handoff_recorder: HandoffRecorder | None = None,
    budget: BudgetConfig | None = None,
) -> HostExecutionBoundary:
    """Build a fresh boundary from already authenticated host coordinates."""

    normalized_task_id = str(task_id or "").strip()
    normalized_thread_id = str(thread_id or "").strip()
    normalized_goal = str(goal or "").strip()
    normalized_actor = str(actor_id or "").strip() or None
    normalized_tenant = str(tenant_id or "").strip() or None
    if not normalized_task_id or not normalized_thread_id:
        raise ValueError("host execution requires task and thread coordinates")
    if bool(normalized_actor) != bool(normalized_tenant):
        raise ValueError("host execution principal is incomplete")
    duration = float(timeout_s)
    if duration <= 0:
        raise ValueError("host execution timeout must be positive")

    trusted_metadata = dict(metadata or {})
    for key in _PRIVATE_METADATA_KEYS:
        trusted_metadata.pop(key, None)
    trusted_metadata.update(
        {
            "task_id": normalized_task_id,
            "intent_id": normalized_task_id,
            "owner_actor_id": normalized_actor,
            "tenant_id": normalized_tenant,
            "_file_write_leases": {},
            "_file_write_lease_handoffs": {},
            "_file_write_lease_history": [],
            "_file_read_snapshots": {},
        }
    )
    if handoff_recorder is not None:
        trusted_metadata["_execution_handoff_recorder"] = handoff_recorder

    session = Session(
        actor=normalized_actor,
        thread_id=normalized_thread_id,
        conversation_id=normalized_thread_id,
        turn_id=normalized_task_id,
        metadata=trusted_metadata,
    )
    limits = budget or BudgetConfig()
    task = ExecutionTask(
        task_id=normalized_task_id,
        thread_id=normalized_thread_id,
        actor_id=normalized_actor,
        tenant_id=normalized_tenant,
        goal=normalized_goal,
        permissions=resolve_execution_scope(session),
        resources=ExecutionResources(
            token_target=limits.max_tokens,
            usd_target=limits.max_usd,
            deadline=time.monotonic() + duration,
        ),
        parent_task_id=str(parent_task_id or "").strip() or None,
    )
    request = ExecutionRequest(task=task, instruction=normalized_goal)
    session.execution_request = request
    return HostExecutionBoundary(session=session, request=request)


def bind_session_execution_request(
    session: Session,
    *,
    task_id: str,
    goal: str,
    timeout_s: float,
    parent_task_id: str | None = None,
    budget: BudgetConfig | None = None,
    execution_engine: Literal["native", "codex", "opencode"] | None = None,
) -> ExecutionRequest:
    """Attach a host request to an already-created realtime Session.

    Realtime admission creates its ``TaskExecutionGuard`` before the lazy
    provider generator starts.  This helper lets that path bind the same
    immutable identity after lease admission while retaining the existing
    agent, journal and cancellation fields on the Session.
    """

    actor_id = str(session.actor or "").strip() or None
    metadata = session.metadata or {}
    tenant_id = str(metadata.get("tenant_id") or "").strip() or None
    if bool(actor_id) != bool(tenant_id):
        raise ValueError("session execution principal is incomplete")
    normalized_task_id = str(task_id or "").strip()
    normalized_goal = str(goal or "").strip()
    if not normalized_task_id or not normalized_goal:
        raise ValueError("session execution requires task and goal")
    duration = float(timeout_s)
    if duration <= 0:
        raise ValueError("host execution timeout must be positive")
    limits = budget or BudgetConfig()
    request = ExecutionRequest(
        task=ExecutionTask(
            task_id=normalized_task_id,
            thread_id=str(session.thread_id or "").strip(),
            actor_id=actor_id,
            tenant_id=tenant_id,
            goal=normalized_goal,
            permissions=resolve_execution_scope(session),
            resources=ExecutionResources(
                token_target=limits.max_tokens,
                usd_target=limits.max_usd,
                deadline=time.monotonic() + duration,
            ),
            parent_task_id=str(parent_task_id or "").strip() or None,
            execution_engine=execution_engine,
        ),
        instruction=normalized_goal,
    )
    session.execution_request = request
    return request


__all__ = [
    "HostExecutionBoundary",
    "bind_session_execution_request",
    "create_host_execution_boundary",
    "host_execution_scope",
    "inherit_host_execution_session",
]
