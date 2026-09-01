"""Parallel task runner — run multiple agent tasks concurrently.

Each task gets its own thread and runs independently. The runner
manages lifecycle (start, cancel, status) and exposes results via
a REST API. Frontend polls or uses SSE to track progress.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Request

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi

_logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):  # noqa: UP042 — keep str-mixin for JSON-wire compat with frontend
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ParallelTask:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    thread_id: str = ""
    prompt: str = ""
    agent_id: str = "coder"
    status: TaskStatus = TaskStatus.QUEUED
    result: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    workspace_path: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    owner_actor_id: str = ""
    tenant_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "prompt": self.prompt[:200],
            "agent_id": self.agent_id,
            "status": self.status.value,
            "result": self.result[:500] if self.result else "",
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "workspace_path": self.workspace_path,
        }


class ParallelTaskRunner:
    def __init__(
        self,
        max_workers: int = 3,
        stack: Any = None,
        *,
        max_retained_terminal: int = 200,
    ):
        self._tasks: dict[str, ParallelTask] = {}
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="parallel-task")
        self._cancelled: set[str] = set()
        self._futures: dict[str, Future[None]] = {}
        self._terminal_order: deque[str] = deque()
        self._terminal_recorded: set[str] = set()
        self._max_retained_terminal = max(0, int(max_retained_terminal))
        self._lock = RLock()
        self._closed = False
        # Audit T-09: one CancellationSource per in-flight task, installed as
        # the ambient token around run_react_loop so cancel() actually stops
        # the running loop instead of just labelling it cancelled.
        self._sources: dict[str, Any] = {}
        # The wired execution stack (``StackProtocol``) this runner drives
        # ``run_react_loop`` against. Previously ``_run_task`` reached for a
        # ``get_app_state()`` helper that never existed, so every task failed
        # on ImportError. Hold the stack here instead.
        self._stack = stack

    def submit(self, task: ParallelTask) -> ParallelTask:
        # Carry the spawning parent's prompt-injection taint into the
        # subagent: the thread-pool worker starts with a fresh contextvar,
        # so without this an injection-tainted parent could launder a risky
        # action through a freshly-spawned subagent. Captured HERE, in the
        # parent's context, before crossing the pool boundary.
        try:
            from runtime.safety.validation.prompt_injection import (
                current_injection_taint,
            )

            _taint = current_injection_taint()
            if _taint and _taint != "none" and isinstance(task.context, dict):
                task.context.setdefault("_inherited_injection_taint", _taint)
        except Exception:  # noqa: BLE001 - taint propagation is best-effort
            pass
        from runtime.safety.approval.cancellation import CancellationSource

        source = CancellationSource()
        with self._lock:
            if self._closed:
                raise RuntimeError("parallel task runner is shut down")
            if task.id in self._tasks:
                raise ValueError(f"parallel task already exists: {task.id}")
            # Install task + source before making work eligible to run. The
            # worker takes the same lock at entry, so cancel-before-start can
            # always signal a real source rather than racing its creation.
            self._tasks[task.id] = task
            self._sources[task.id] = source
            try:
                future = self._pool.submit(self._run_task, task.id)
            except Exception:
                self._tasks.pop(task.id, None)
                self._sources.pop(task.id, None)
                raise
            self._futures[task.id] = future
        return task

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                return False
            self._cancelled.add(task_id)
            source = self._sources.get(task_id)
            future = self._futures.get(task_id)
            task.status = TaskStatus.CANCELLED
            task.finished_at = time.time()
            self._record_terminal_locked(task_id)
        # Callbacks can execute arbitrary code; never invoke them under the
        # runner lock. The source already existed before the worker was queued.
        if source is not None:
            source.cancel(reason="cancelled by operator")
        if future is not None:
            future.cancel()
        return True

    def get(self, task_id: str) -> ParallelTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(
        self,
        workspace_path: str | None = None,
        *,
        owner_actor_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[ParallelTask]:
        with self._lock:
            tasks = list(self._tasks.values())
        if owner_actor_id is not None:
            tasks = [task for task in tasks if task.owner_actor_id == owner_actor_id]
        if tenant_id is not None:
            tasks = [task for task in tasks if task.tenant_id == tenant_id]
        if workspace_path:
            tasks = [t for t in tasks if t.workspace_path == workspace_path]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def _record_terminal_locked(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None or task.status not in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ):
            return
        if task_id not in self._terminal_recorded:
            self._terminal_recorded.add(task_id)
            self._terminal_order.append(task_id)
        while len(self._terminal_order) > self._max_retained_terminal:
            expired = self._terminal_order.popleft()
            self._terminal_recorded.discard(expired)
            expired_task = self._tasks.get(expired)
            if expired_task is not None and expired_task.status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                self._tasks.pop(expired, None)
                self._cancelled.discard(expired)
                self._sources.pop(expired, None)
                self._futures.pop(expired, None)

    def shutdown(self, *, wait: bool = True) -> None:
        """Cancel active work and release this app's executor exactly once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sources = list(self._sources.values())
            futures = list(self._futures.items())
            now = time.time()
            for task_id, task in list(self._tasks.items()):
                if task.status not in (
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                ):
                    self._cancelled.add(task_id)
                    task.status = TaskStatus.CANCELLED
                    task.finished_at = now
                    self._record_terminal_locked(task_id)
        for source in sources:
            source.cancel(reason="parallel task runner shutdown")
        for _task_id, future in futures:
            future.cancel()
        self._pool.shutdown(wait=wait, cancel_futures=True)
        with self._lock:
            # Queued futures cancelled by shutdown never enter the worker's
            # finally block, so release their transient bookkeeping here.
            for task_id, future in futures:
                if future.cancelled():
                    self._sources.pop(task_id, None)
                    self._futures.pop(task_id, None)

    def _run_task(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            source = self._sources.get(task_id)
            if task is None or source is None:
                return
            if task_id in self._cancelled or task.status is TaskStatus.CANCELLED:
                self._sources.pop(task_id, None)
                self._futures.pop(task_id, None)
                self._record_terminal_locked(task_id)
                return
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()

        from runtime.safety.approval.cancellation import scoped_cancellation

        try:
            from runtime.core.cerebrum.react_loop import run_react_loop
            from runtime.core.cerebrum.react_step_evaluator import (
                build_runtime_step_evaluator,
            )
            from runtime.platform.models import ParsedIntent

            authenticated = bool(task.owner_actor_id and task.tenant_id)
            if authenticated:
                # HTTP-authenticated tasks carry an immutable server-derived
                # execution boundary.  Apply it last so a nested context field
                # cannot restore a client-selected cwd or approval bypass.
                authoritative_metadata = {
                    "mode": "code",
                    "workspace_scope": "project",
                    "workspace_path": task.workspace_path,
                    "extra_workspaces": [],
                    "personal_workspace_path": "",
                    "sandbox_mode": "full",
                    "permission_mode": "default",
                    "approval_policy": "on-request",
                    "execution_environment": "sandbox",
                    "_artifact_output_root": str(Path(task.workspace_path) / "output" / "final"),
                    "owner_actor_id": task.owner_actor_id,
                    "tenant_id": task.tenant_id,
                }
                raw_metadata = task.context.get("metadata")
                metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
                metadata.update(authoritative_metadata)
                user_context = {
                    **task.context,
                    **authoritative_metadata,
                    "thread_id": task.thread_id,
                    "auto_approve": False,
                    "metadata": metadata,
                }
            else:
                # Preserve the historical anonymous/local contract, including
                # caller-selected workspace and auto-approval semantics.
                user_context = {
                    "workspace_path": task.workspace_path,
                    "mode": "code",
                    "auto_approve": True,
                    **task.context,
                }

            intent = ParsedIntent(
                raw=task.prompt,
                intent_type="task",
                normalized_goal=task.prompt,
                user_context=user_context,
            )

            state = self._stack
            if not state:
                raise RuntimeError("parallel runner has no execution stack wired in")

            from contextlib import nullcontext

            session_context: Any = nullcontext()
            if authenticated:
                from runtime.platform.process.session import Session, session_scope

                session_context = session_scope(
                    Session(
                        actor=task.owner_actor_id,
                        thread_id=task.thread_id,
                        metadata=dict(intent.user_context["metadata"]),
                    )
                )
            with scoped_cancellation(source.token), session_context:
                result = run_react_loop(
                    stack=state,
                    intent=intent,
                    agent=None,
                    max_iterations=6,
                    thread_id=task.thread_id or task.id,
                    step_evaluator=build_runtime_step_evaluator(),
                )
            with self._lock:
                if task_id in self._cancelled:
                    # A cancelled task must keep its CANCELLED terminal state
                    # instead of being overwritten by normal completion.
                    task.result = str(result) if result else "cancelled"
                    task.status = TaskStatus.CANCELLED
                else:
                    task.result = str(result) if result else "completed"
                    task.status = TaskStatus.COMPLETED
        except Exception as exc:
            _logger.exception("parallel task %s failed", task_id)
            with self._lock:
                if task_id in self._cancelled:
                    task.status = TaskStatus.CANCELLED
                else:
                    task.error = str(exc)
                    task.status = TaskStatus.FAILED
        finally:
            with self._lock:
                self._sources.pop(task_id, None)
                self._futures.pop(task_id, None)
                task.finished_at = time.time()
                self._record_terminal_locked(task_id)


_runner: ParallelTaskRunner | None = None


def get_runner() -> ParallelTaskRunner:
    global _runner
    if _runner is None:
        _runner = ParallelTaskRunner(max_workers=3)
    return _runner


def create_parallel_task_router(
    stack: Any = None,
    *,
    thread_store: Any = None,
    workspace_root: Any = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi(__name__)

    # One runner per app prevents a later app factory from replacing the
    # execution stack or task namespace of an already-running app.
    runner = ParallelTaskRunner(max_workers=3, stack=stack)

    router = APIRouter(tags=["parallel-tasks"])

    def _shutdown_runner() -> None:
        runner.shutdown(wait=False)

    router.add_event_handler("shutdown", _shutdown_runner)

    def _principal(request: Any) -> Any:
        from runtime.safety.auth.principal import resolve_principal

        return resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    def _authenticated_task_scope(
        request: Any,
        body: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any], str, str]:
        raw_context = body.get("context")
        context = dict(raw_context) if isinstance(raw_context, dict) else {}
        raw_workspace = body.get("workspace_path")
        workspace = str(raw_workspace) if isinstance(raw_workspace, str) else ""
        raw_thread_id = body.get("thread_id")
        thread_id = str(raw_thread_id) if isinstance(raw_thread_id, str) else ""
        if not require_auth:
            return thread_id, workspace, context, "", ""

        principal = _principal(request)
        if principal is None:
            raise HTTPException(401, "authentication required")
        thread_id = thread_id.strip()
        if not thread_id:
            raise HTTPException(400, "thread_id is required")
        try:
            from runtime.memory.threads.event_log import validate_thread_id

            thread_id = validate_thread_id(thread_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if thread_store is None or not hasattr(thread_store, "get"):
            raise HTTPException(503, "thread ownership store unavailable")
        thread = thread_store.get(thread_id)
        raw_metadata = thread.get("metadata") if isinstance(thread, dict) else None
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        if metadata.get("owner_actor_id") != principal.actor_id:
            raise HTTPException(404, f"thread not found: {thread_id}")
        if metadata.get("tenant_id") != principal.tenant_id:
            raise HTTPException(404, f"thread not found: {thread_id}")

        from runtime.platform.runtime_policy.workspaces import (
            PROTECTED_WORKSPACE_METADATA_KEYS,
            verified_managed_workspace,
        )

        managed = verified_managed_workspace(
            workspace_root,
            thread_id=thread_id,
            metadata=metadata,
        )
        if managed is None:
            raise HTTPException(409, "verified managed thread workspace required")

        # Client context remains presentation data only.  Strip every known
        # identity, path, tool, sandbox and approval authority at both levels.
        authority_keys = {
            *PROTECTED_WORKSPACE_METADATA_KEYS,
            "workspace",
            "sandbox_dir",
            "locked_write_root",
            "_locked_write_root",
            "allowed_paths",
            "tool_allowlist",
            "tool_allowlist_mode",
            "allowed_tools",
            "tools",
            "extra_tool_allowlist",
            "extra_tools",
            "extra_skills",
            "actor",
            "actor_id",
            "owner_actor_id",
            "tenant_id",
            "auto_approve",
            "approvalPolicy",
            "approval_policy",
            "permission_mode",
            "sandbox_mode",
            "execution_environment",
        }
        for key in authority_keys:
            context.pop(key, None)
        nested = context.get("metadata")
        nested_metadata = dict(nested) if isinstance(nested, dict) else {}
        for key in authority_keys:
            nested_metadata.pop(key, None)
        context["metadata"] = nested_metadata
        return (
            thread_id,
            str(managed),
            context,
            principal.actor_id,
            principal.tenant_id,
        )

    def _owned_task(request: Any, task_id: str) -> ParallelTask:
        principal = _principal(request)
        task = runner.get(task_id)
        if task is None:
            raise HTTPException(404, "task not found")
        if require_auth and (
            principal is None
            or task.owner_actor_id != principal.actor_id
            or task.tenant_id != principal.tenant_id
        ):
            raise HTTPException(404, "task not found")
        return task

    @router.post(
        "/api/tasks/submit",
        operation_id="parallel_submit_task",
    )
    def submit_task(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        thread_id, workspace_path, context, owner_actor_id, tenant_id = _authenticated_task_scope(
            request, body
        )
        task = ParallelTask(
            prompt=str(body.get("prompt") or ""),
            agent_id=str(body.get("agent_id") or "coder"),
            workspace_path=workspace_path,
            thread_id=thread_id,
            context=context,
            owner_actor_id=owner_actor_id,
            tenant_id=tenant_id,
        )
        runner.submit(task)
        return task.to_dict()

    @router.get(
        "/api/tasks",
        operation_id="parallel_list_tasks",
    )
    def list_tasks(request: Request, workspace_path: str | None = None) -> dict[str, Any]:
        principal = _principal(request)
        tasks = runner.list_tasks(
            workspace_path,
            owner_actor_id=(principal.actor_id if require_auth and principal is not None else None),
            tenant_id=(principal.tenant_id if require_auth and principal is not None else None),
        )
        return {"tasks": [t.to_dict() for t in tasks[:50]]}

    @router.get(
        "/api/tasks/{task_id}",
        operation_id="parallel_get_task",
    )
    def get_task(request: Request, task_id: str) -> dict[str, Any]:
        task = _owned_task(request, task_id)
        return task.to_dict()

    @router.post(
        "/api/tasks/{task_id}/cancel",
        operation_id="parallel_cancel_task",
    )
    def cancel_task(request: Request, task_id: str) -> dict[str, Any]:
        task = _owned_task(request, task_id)
        ok = runner.cancel(task.id)
        return {"cancelled": ok}

    return router
