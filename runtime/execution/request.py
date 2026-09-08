"""Immutable host context shared by intelligent execution modules.

Engine conversations, credentials and checkpoints stay inside their adapters.
The host owns the task identity, permission ceiling, resource deadline and
artifact contract.  This prevents a continuation or a child Agent from
silently changing the authority of the parent task.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal, TypeVar

from runtime.execution.artifact_contracts import ArtifactContract
from runtime.platform.process.scope import ExecutionScope, execution_scope_ceiling


class ExecutionDeadlineExceeded(TimeoutError):
    """The host's absolute deadline expired."""


@dataclass(frozen=True, slots=True)
class ExecutionResources:
    """Resource policy shared by all continuations of one task."""

    token_target: int
    usd_target: float
    deadline: float | None

    def __post_init__(self) -> None:
        if type(self.token_target) is not int or self.token_target <= 0:
            raise ValueError("token target must be a positive integer")
        if not math.isfinite(self.usd_target) or self.usd_target <= 0:
            raise ValueError("USD target must be positive and finite")
        if self.deadline is not None and not math.isfinite(self.deadline):
            raise ValueError("execution deadline must be finite")

    def remaining_seconds(self) -> float | None:
        if self.deadline is None:
            return None
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ExecutionDeadlineExceeded("task execution deadline exceeded")
        return remaining


@dataclass(frozen=True, slots=True)
class ExecutionTask:
    """Server-owned identity and policy for one logical task."""

    task_id: str
    thread_id: str
    actor_id: str | None
    tenant_id: str | None
    goal: str
    permissions: ExecutionScope
    resources: ExecutionResources
    parent_task_id: str | None = None
    artifacts: ArtifactContract | None = None
    # Set only by the host adapter, never by serializable tool arguments.
    execution_engine: Literal["native", "octopus", "codex", "opencode"] | None = None
    # Approval and authorization context are host-owned and travel with the
    # immutable request into callback workers. They are deliberately excluded
    # from all client-facing protocol models.
    approval_provider: object | None = None
    authorization_intent: str = ""
    server_auto_approve: bool = False

    def __post_init__(self) -> None:
        if not self.task_id or not self.thread_id:
            raise ValueError("execution requires host task and thread coordinates")
        if bool(self.actor_id) != bool(self.tenant_id):
            raise ValueError("execution principal is incomplete")
        if self.execution_engine not in {None, "native", "octopus", "codex", "opencode"}:
            raise ValueError("unknown host execution engine")


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    task: ExecutionTask
    instruction: str


_CURRENT_REQUEST: ContextVar[ExecutionRequest | None] = ContextVar(
    "host_execution_request",
    default=None,
)

_ItemT = TypeVar("_ItemT")


def current_execution_request() -> ExecutionRequest | None:
    """Return the trusted request bound to the current worker context."""

    return _CURRENT_REQUEST.get()


@contextmanager
def execution_request_scope(request: ExecutionRequest | None) -> Iterator[None]:
    """Bind a request for synchronous tools and nested worker threads.

    ``None`` is accepted for compatibility with legacy sessions and resets the
    context to the legacy empty state for the duration of the scope.
    """

    token = _CURRENT_REQUEST.set(request)
    try:
        if request is None:
            yield
        else:
            with execution_scope_ceiling(request.task.permissions):
                yield
    finally:
        # A lazy provider iterator can be finalized by a different executor
        # context after cancellation.  In that case ContextVar refuses token
        # reset; the worker context is already gone, so there is no value to
        # leak and cleanup remains safe.
        with suppress(ValueError):
            _CURRENT_REQUEST.reset(token)


def iter_with_execution_request(
    values: Iterable[_ItemT],
    request: ExecutionRequest | None,
) -> Iterator[_ItemT]:
    """Iterate provider output while carrying the host request.

    ReAct and native fallback providers are synchronous generators consumed on
    a worker thread.  Wrapping the iterator, rather than only its construction,
    keeps the request bound while lazy generator bodies execute and while they
    spawn nested scoped workers.
    """

    with execution_request_scope(request):
        yield from values


__all__ = [
    "ExecutionDeadlineExceeded",
    "ExecutionRequest",
    "ExecutionResources",
    "ExecutionTask",
    "current_execution_request",
    "execution_request_scope",
    "iter_with_execution_request",
]
