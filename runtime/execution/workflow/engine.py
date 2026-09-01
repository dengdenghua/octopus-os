"""Workflow engine host (dsh ``workflow-worker-thread/host``).

``start()`` validates enough synchronously to reject a malformed meta
block, an unparseable/contract-violating script, an invalid per-run cap
or an unavailable child route BEFORE a run exists. Once returned,
``WorkflowRun.result`` never rejects: execution failures resolve with
``stop_reason == "error"``, cancellation resolves with ``"cancelled"``
within a bounded grace, and the worker process is terminated when a run
crosses its bounds (sync-slice timeout, cancellation grace, disposal).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from .meta import validate_meta
from .protocol import (
    AgentStartRequest,
    AgentStartResult,
    WorkerInit,
    decode_worker_message,
    encode_host_message,
)
from .realm import check_meta_statement, validate_script
from .types import (
    WorkflowAgentEndInfo,
    WorkflowAgentInfo,
    WorkflowError,
    WorkflowMeta,
    WorkflowResult,
    WorkflowResultInfo,
    WorkflowRunId,
    WorkflowRunInfo,
)

_logger = logging.getLogger("runtime.execution.workflow")

_STDERR_CAP = 64 * 1024


class WorkflowObserver:
    """Lifecycle observer seam (dsh ``ctx.on('workflow/*')`` events).

    All callbacks are best-effort: the engine contains and logs any throw
    without starving the run. Payloads are borrowed immutable values.
    """

    def on_start(self, info: WorkflowRunInfo) -> None:  # pragma: no cover - seam
        pass

    def on_phase(self, info: WorkflowRunInfo, title: str) -> None:  # pragma: no cover
        pass

    def on_log(self, info: WorkflowRunInfo, message: str) -> None:  # pragma: no cover
        pass

    def on_agent_start(
        self, info: WorkflowRunInfo, agent: WorkflowAgentInfo
    ) -> None:  # pragma: no cover
        pass

    def on_agent_end(
        self, info: WorkflowRunInfo, agent: WorkflowAgentEndInfo
    ) -> None:  # pragma: no cover
        pass

    def on_end(self, info: WorkflowRunInfo, result: WorkflowResultInfo) -> None:  # pragma: no cover
        pass


ChildDispatcher = Callable[[AgentStartRequest], Awaitable[AgentStartResult]]


async def _default_child_dispatcher(
    request: AgentStartRequest,
    *,
    default_agent_id: str = "researcher",
) -> AgentStartResult:
    """Bridge one ``agent()`` call to the echo subagent seam.

    dsh routes children through a subagent provider; echo routes them
    through ``call_subagent`` with a role (``agent_id``). The workflow
    option ``provider`` maps to the child role; ``model`` pins the child's
    model via context. A child that fails for its own reasons is an
    ordinary failure (``ok=False``, not fatal); a broken dispatch
    machinery is an infrastructure fault (``fatal=True`` → AGENT_START).
    """
    from runtime.execution.subagents import call_subagent

    context: dict[str, Any] | None = None
    if request.get("model"):
        context = {"model_name": request["model"]}
    try:
        outcome = await asyncio.to_thread(
            call_subagent,
            agent_id=request.get("agent") or default_agent_id,
            prompt=request.get("prompt", ""),
            output_schema=request.get("schema"),
            context=context,
        )
    except Exception as exc:  # noqa: BLE001 — infrastructure fault
        _logger.warning("workflow agent dispatch failed: %s", exc)
        return {
            "ok": False,
            "fatal": True,
            "error": str(exc) or exc.__class__.__name__,
            "stop_reason": "error",
        }
    if not outcome.get("success"):
        return {
            "ok": False,
            "fatal": False,
            "error": str(outcome.get("error") or "child failed"),
            "stop_reason": "failed",
        }
    return {
        "ok": True,
        "output": str(outcome.get("output") or ""),
        "structured": outcome.get("parsed"),
        "stop_reason": "completed",
        "child_id": outcome.get("session_id"),
    }


class WorkflowEngine:
    """Execute model-authored orchestration scripts over subagents.

    Deployment knobs mirror dsh ``workflow-worker-thread`` defaults; a
    per-run ``maxTotalAgents`` may only lower the ceiling.
    """

    def __init__(
        self,
        *,
        subagent_agent_id: str = "researcher",
        max_concurrent_agents: int = 0,
        max_total_agents: int = 1000,
        max_items_per_call: int = 4096,
        sync_timeout_ms: int = 5000,
        dispose_grace_ms: int = 5000,
        run_timeout_ms: int = 1_800_000,
        observer: WorkflowObserver | None = None,
        child_dispatch: ChildDispatcher | None = None,
    ) -> None:
        if max_total_agents < 1:
            raise WorkflowError(
                "workflow maxTotalAgents must be a positive integer",
                "INVALID_ARGUMENT",
            )
        if max_items_per_call < 1:
            raise WorkflowError(
                "workflow maxItemsPerCall must be a positive integer",
                "INVALID_ARGUMENT",
            )
        self._subagent_agent_id = subagent_agent_id
        self._max_concurrent_agents = max_concurrent_agents
        self._max_total_agents = max_total_agents
        self._max_items_per_call = max_items_per_call
        self._sync_timeout_ms = sync_timeout_ms
        self._dispose_grace_ms = dispose_grace_ms
        # Audit T-10: a run-level total duration cap (0 disables). A runaway
        # script that keeps calling hooks can no longer pin the process
        # beyond this ceiling.
        self._run_timeout_ms = max(0, int(run_timeout_ms))
        self._observer = observer or WorkflowObserver()
        self._child_dispatch = child_dispatch or partial(
            _default_child_dispatcher,
            default_agent_id=self._subagent_agent_id,
        )

    def start(
        self,
        request: dict[str, Any],
        *,
        observer: WorkflowObserver | None = None,
    ) -> WorkflowRun:
        """Synchronously validate, then spawn the worker and return the run."""
        meta = validate_meta(request.get("meta"))
        script = request.get("script")
        if not isinstance(script, str) or not script.strip():
            raise WorkflowError(
                "workflow script must be a non-empty string",
                "SCRIPT_PARSE",
            )
        validate_script(script, name=meta.name)
        check_meta_statement(script, name=meta.name)

        ceiling = self._max_total_agents
        requested_cap = request.get("maxTotalAgents")
        if requested_cap is not None:
            if (
                not isinstance(requested_cap, int)
                or isinstance(requested_cap, bool)
                or requested_cap < 1
            ):
                raise WorkflowError(
                    "workflow maxTotalAgents must be a positive integer",
                    "INVALID_ARGUMENT",
                )
            if requested_cap > ceiling:
                raise WorkflowError(
                    f"workflow maxTotalAgents {requested_cap} exceeds the engine ceiling {ceiling}",
                    "INVALID_ARGUMENT",
                )
            ceiling = requested_cap

        concurrent = self._max_concurrent_agents
        if concurrent == 0:
            cores = os.cpu_count() or 2
            concurrent = min(16, max(1, cores - 2))
        if concurrent < 1:
            raise WorkflowError(
                "workflow maxConcurrentAgents must be a positive integer",
                "INVALID_ARGUMENT",
            )

        run_id = WorkflowRunId(uuid.uuid4().hex)
        info = WorkflowRunInfo(id=run_id, meta=meta)
        run = WorkflowRun(
            engine=self,
            info=info,
            worker_init={
                "runId": run_id,
                "name": meta.name,
                "body": script,
                "args": request.get("args"),
                "maxTotalAgents": ceiling,
                "maxConcurrentAgents": concurrent,
                "maxItemsPerCall": self._max_items_per_call,
            },
            dispatch=self._child_dispatch,
            sync_timeout_ms=self._sync_timeout_ms,
            dispose_grace_ms=self._dispose_grace_ms,
            run_timeout_ms=self._run_timeout_ms,
            observer=observer or self._observer,
        )
        run._spawn()  # noqa: SLF001 — same-package construction handshake
        self._emit(self._observer.on_start, info)
        return run

    def _emit(self, callback: Callable[..., Any], *args: Any) -> None:
        try:
            callback(*args)
        except Exception:  # noqa: BLE001 — listener containment
            _logger.warning("workflow observer callback failed", exc_info=True)


class WorkflowRun:
    """Holder-owned live run. ``result`` never rejects; ``dispose()`` is
    idempotent and awaits bounded settlement and cleanup."""

    def __init__(
        self,
        *,
        engine: WorkflowEngine,
        info: WorkflowRunInfo,
        worker_init: WorkerInit,
        dispatch: ChildDispatcher,
        sync_timeout_ms: int,
        dispose_grace_ms: int,
        run_timeout_ms: int,
        observer: WorkflowObserver,
    ) -> None:
        self._engine = engine
        self._info = info
        self._worker_init = worker_init
        self._dispatch = dispatch
        self._sync_timeout_ms = sync_timeout_ms
        self._dispose_grace_ms = dispose_grace_ms
        self._run_timeout_ms = max(0, int(run_timeout_ms))
        self._observer = observer
        self._loop: asyncio.AbstractEventLoop | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._result_future: asyncio.Future[WorkflowResult] | None = None
        self._settled = False
        self._cancelled = False
        self._cancel_reason: str | None = None
        self._host_started = 0
        self._in_flight: dict[int, asyncio.Task[Any]] = {}
        self._first_line_received = False
        self._sync_timer: asyncio.TimerHandle | None = None
        self._grace_timer: asyncio.TimerHandle | None = None
        self._run_timer: asyncio.TimerHandle | None = None
        self._reader_task: asyncio.Task[Any] | None = None
        self._exit_task: asyncio.Task[Any] | None = None
        self._stderr_task: asyncio.Task[Any] | None = None
        self._stderr_buf: list[bytes] = []
        self._disposed = False

    @property
    def id(self) -> WorkflowRunId:
        return self._info.id

    @property
    def meta(self) -> WorkflowMeta:
        return self._info.meta

    @property
    def result(self) -> asyncio.Future[WorkflowResult]:
        assert self._result_future is not None
        return self._result_future

    # ── lifecycle ─────────────────────────────────────────────

    def _spawn(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._result_future = self._loop.create_future()
        try:
            from runtime.platform.process.tree import process_group_kwargs

            self._proc = subprocess.Popen(
                [sys.executable, "-m", "runtime.execution.workflow.worker"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # Audit T-10: the worker runs in its own session (pid ==
                # pgid) so terminating it later kills the whole process
                # group — subagent children the worker spawned do not
                # outlive the run.
                **process_group_kwargs(),
            )
        except OSError as exc:
            self._settle(
                WorkflowResult(
                    None,
                    "error",
                    error=f"workflow worker could not start: {exc}",
                    agents_started=0,
                )
            )
            return
        # The worker's synchronous slice is bounded: no protocol line within
        # ``sync_timeout_ms`` means the script is stuck in pure CPU work (or
        # the worker never booted) — terminate it, mirroring dsh's vm
        # ``syncTimeoutMs``. The timer is cancelled by the first message.
        self._sync_timer = self._loop.call_later(
            self._sync_timeout_ms / 1000.0,
            self._on_sync_timeout,
        )
        # Audit T-10: run-level total duration cap. Fires once; if the run
        # is still alive it cancels with the cap reason.
        if self._run_timeout_ms > 0:
            self._run_timer = self._loop.call_later(
                self._run_timeout_ms / 1000.0,
                self._on_run_timeout,
            )
        self._reader_task = self._loop.create_task(self._read_loop())
        self._exit_task = self._loop.create_task(self._wait_exit())
        self._stderr_task = self._loop.create_task(self._drain_stderr())
        if self._proc.stdin is not None:
            self._write_init()

    def _write_init(self) -> None:
        assert self._proc is not None
        line = json.dumps(self._worker_init, ensure_ascii=False) + "\n"
        try:
            self._proc.stdin.write(line.encode("utf-8"))  # type: ignore[union-attr]
            self._proc.stdin.flush()  # type: ignore[union-attr]
        except (BrokenPipeError, OSError) as exc:
            _logger.debug("workflow worker stdin write failed: %s", exc)

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                raw = await self._read_line()
                if raw is None:
                    break
                if not self._first_line_received:
                    self._first_line_received = True
                    timer = getattr(self, "_sync_timer", None)
                    if timer is not None:
                        timer.cancel()
                try:
                    message = decode_worker_message(raw.decode("utf-8"))
                except (ValueError, KeyError, TypeError) as exc:
                    _logger.warning("workflow worker sent a malformed line: %s", exc)
                    continue
                self._handle_worker_message(message)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — reader must never die silently
            _logger.warning("workflow worker reader failed", exc_info=True)

    async def _read_line(self) -> bytes | None:
        assert self._proc is not None and self._proc.stdout is not None
        raw = await asyncio.to_thread(self._proc.stdout.readline)
        return raw if raw else None

    async def _drain_stderr(self) -> None:
        """Keep the stderr pipe drained so a chatty worker cannot deadlock."""
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while True:
                chunk = await asyncio.to_thread(self._proc.stderr.readline)
                if not chunk:
                    return
                if sum(len(b) for b in self._stderr_buf) < _STDERR_CAP:
                    self._stderr_buf.append(chunk)
        except (asyncio.CancelledError, OSError):  # noqa: BLE001 — shutdown is best-effort
            pass

    async def _wait_exit(self) -> None:
        assert self._proc is not None
        returncode = await asyncio.to_thread(self._proc.wait)
        stderr = b"".join(self._stderr_buf).decode("utf-8", "replace").strip()
        if not self._settled:
            detail = f" (worker exit {returncode})"
            if stderr:
                detail += f": {stderr[-512:]}"
            self._settle(
                WorkflowResult(
                    None,
                    "error",
                    error=f"workflow worker exited before settling{detail}",
                    agents_started=self._host_started,
                )
            )

    def _handle_worker_message(self, message: Any) -> None:
        kind, *rest = message
        if kind == "phase":
            self._emit(self._observer.on_phase, self._info, str(rest[0]))
        elif kind == "log":
            self._emit(self._observer.on_log, self._info, str(rest[0]))
        elif kind == "agent-start":
            seq, label, phase, child_id = rest  # type: ignore[misc]
            self._emit(
                self._observer.on_agent_start,
                self._info,
                WorkflowAgentInfo(
                    seq=int(seq),
                    label=str(label),
                    phase=phase,
                    child_id=child_id,
                ),
            )
        elif kind == "agent-end":
            seq, outcome, child_id = rest  # type: ignore[misc]
            self._emit(
                self._observer.on_agent_end,
                self._info,
                WorkflowAgentEndInfo(
                    seq=int(seq),
                    label="",
                    phase=None,
                    child_id=child_id,
                    outcome=str(outcome),
                ),
            )
        elif kind == "agent-request":
            seq, request = rest  # type: ignore[misc]
            self._host_started += 1
            self._in_flight[int(seq)] = self._loop.create_task(
                self._dispatch_agent(int(seq), request)
            )
        elif kind == "result":
            stop_reason, value, agents_started, error = rest  # type: ignore[misc]
            self._settle(
                WorkflowResult(
                    value,
                    stop_reason,
                    error=error,
                    agents_started=int(agents_started),
                )
            )

    async def _dispatch_agent(self, seq: int, request: AgentStartRequest) -> None:
        """Start one child, announce it, and reply with its outcome."""
        # ``agent-started`` fires as soon as dispatch begins so observers see
        # live child progress; the child id (session id) is attached to the
        # terminal ``agent-end`` via the response.
        self._send_host_message(("agent-started", seq, None))
        if self._cancelled:
            self._send_host_message(
                (
                    "agent-response",
                    seq,
                    {"ok": False, "error": "workflow run cancelled", "stop_reason": "cancelled"},
                )
            )
            return
        try:
            result = await self._dispatch(request)
        except Exception as exc:  # noqa: BLE001 — infrastructure fault
            result = {
                "ok": False,
                "fatal": True,
                "error": str(exc) or exc.__class__.__name__,
                "stop_reason": "error",
            }
        finally:
            self._in_flight.pop(seq, None)
        self._send_host_message(("agent-response", seq, result))

    def _send_host_message(self, message: Any) -> None:
        if self._proc is None or self._proc.stdin is None:
            return
        try:
            self._proc.stdin.write(  # type: ignore[union-attr]
                (encode_host_message(message) + "\n").encode("utf-8")
            )
            self._proc.stdin.flush()  # type: ignore[union-attr]
        except (BrokenPipeError, OSError):  # noqa: BLE001 — worker is gone; exit watcher settles the run
            pass

    # ── cancellation / disposal ───────────────────────────────

    def cancel(self, reason: str | None = None) -> None:
        """Cancel the run and its children (bounded grace, then force-settle)."""
        if self._settled or self._cancelled:
            return
        self._cancelled = True
        self._cancel_reason = reason or "workflow cancelled"
        self._send_host_message(("cancel", self._cancel_reason))
        # Reply to in-flight children immediately so the script can settle
        # fast. The worker now runs in its own session and force-settle
        # terminates the whole process group (audit T-10), so subagent
        # children do not outlive the run.
        for seq in list(self._in_flight):
            self._send_host_message(
                (
                    "agent-response",
                    seq,
                    {"ok": False, "error": self._cancel_reason, "stop_reason": "cancelled"},
                )
            )
        if not self._settled:
            self._grace_timer = self._loop.call_later(
                self._dispose_grace_ms / 1000.0,
                self._force_settle,
            )

    def _force_settle(self) -> None:
        if self._settled:
            return
        self._terminate_worker()
        self._settle(
            WorkflowResult(
                None,
                "cancelled",
                error=self._cancel_reason or "workflow cancelled",
                agents_started=self._host_started,
            )
        )

    def _on_run_timeout(self) -> None:
        """Total-duration cap reached (audit T-10): cancel the run."""
        if self._settled:
            return
        _logger.warning(
            "workflow run %s exceeded total duration cap (%d ms)",
            self.id,
            self._run_timeout_ms,
        )
        self.cancel(reason=f"workflow run exceeded total duration cap ({self._run_timeout_ms} ms)")

    def _on_sync_timeout(self) -> None:
        if self._settled or self._first_line_received:
            return
        self._terminate_worker()
        self._settle(
            WorkflowResult(
                None,
                "error",
                error=(
                    f"workflow script exceeded the synchronous-slice timeout "
                    f"({self._sync_timeout_ms} ms) — a runaway loop before the "
                    "first hook call"
                ),
                agents_started=self._host_started,
            )
        )

    def _terminate_worker(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        # Audit T-10: kill the whole process group (worker + any subagent
        # children it spawned) instead of only the direct child, so a
        # cancelled run does not leave children running in the background.
        try:
            from runtime.platform.process.tree import terminate_process_tree

            terminate_process_tree(proc)
        except Exception:  # noqa: BLE001 — best-effort termination
            _logger.exception("workflow worker tree termination failed")
            with contextlib.suppress(OSError):
                proc.kill()

    async def dispose(self) -> None:
        """Idempotent: cancel if needed, await bounded settlement, cleanup."""
        if self._disposed:
            return
        self._disposed = True
        if not self._settled:
            self.cancel()
        try:
            await asyncio.wait_for(
                asyncio.shield(self._result_future),
                timeout=self._dispose_grace_ms / 1000.0,
            )
        except TimeoutError:
            self._force_settle()
        # Give the reader/exit tasks a moment to finish their bookkeeping.
        for task in (getattr(self, "_reader_task", None), getattr(self, "_exit_task", None)):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(self._stderr_task), timeout=0.5)

    # ── settlement ────────────────────────────────────────────

    def _settle(self, result: WorkflowResult) -> None:
        if self._settled:
            return
        self._settled = True
        if self._grace_timer is not None:
            self._grace_timer.cancel()
        timer = getattr(self, "_sync_timer", None)
        if timer is not None:
            timer.cancel()
        run_timer = getattr(self, "_run_timer", None)
        if run_timer is not None:
            run_timer.cancel()
        if not self._result_future.done():
            self._result_future.set_result(result)
        self._emit(
            self._observer.on_end,
            self._info,
            WorkflowResultInfo(
                stop_reason=result.stop_reason,
                error=result.error,
                agents_started=result.agents_started,
            ),
        )

    def _emit(self, callback: Callable[..., Any], *args: Any) -> None:
        try:
            callback(*args)
        except Exception:  # noqa: BLE001 — listener containment
            _logger.warning("workflow observer callback failed", exc_info=True)


__all__ = [
    "ChildDispatcher",
    "WorkflowEngine",
    "WorkflowObserver",
    "WorkflowRun",
    "_default_child_dispatcher",
]
