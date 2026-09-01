"""Workflow worker subprocess (dsh ``workflow-worker-thread/runtime``).

One process hosts one script execution in an asyncio loop with a small
hook vocabulary; ``agent()`` calls round-trip to the host over JSONL.
Concurrency slots, per-run caps, cancellation and fatal-error propagation
mirror the dsh runtime; the host force-terminates the process when a run
crosses its bounds (sync-slice timeout, cancellation grace, disposal).

Not a security boundary by itself — the host subprocess boundary is. The
AST contract (``realm.validate_script``) keeps scripts inside the
vocabulary; the process boundary contains everything else.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from .protocol import (
    AgentStartRequest,
    encode_worker_message,
)
from .realm import (
    build_globals,
    check_meta_statement,
    materialize_json,
    validate_script,
    wrap_body,
)
from .types import WorkflowError, is_fatal_workflow_error

_SUPPORTED_AGENT_OPTIONS = frozenset({"label", "phase", "schema", "provider", "model"})
_DEFERRED_AGENT_OPTIONS = frozenset({"effort", "isolation", "agentType"})


def _default_label(prompt: str) -> str:
    """Short display label derived from the prompt when the script passes none."""
    line = prompt.split("\n", 1)[0]
    return line if len(line) <= 48 else f"{line[:47]}…"


class WorkflowExecution:
    """One live script execution inside the worker.

    ``run()`` is called exactly once and NEVER raises — every failure
    becomes a ``result`` message with a non-``completed`` stop reason.
    """

    def __init__(
        self,
        *,
        run_id: str,
        name: str,
        body: str,
        args: Any,
        max_total_agents: int,
        max_concurrent_agents: int,
        max_items_per_call: int,
    ) -> None:
        self._run_id = run_id
        self._name = name
        self._body = body
        self._args = args
        self._max_total_agents = max_total_agents
        self._max_concurrent_agents = max_concurrent_agents
        self._max_items_per_call = max_items_per_call
        self._started = 0
        self._active_slots = 0
        self._slot_waiters: list[tuple[asyncio.Future[None], Callable[[], None]]] = []
        self._cancelled = False
        self._cancel_reason = "workflow cancelled"
        self._current_phase: str | None = None
        self._responses: dict[int, asyncio.Future[Any]] = {}
        self._pending_starts: dict[int, tuple[str, str | None]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._host_dead = False

    # ── protocol helpers ──────────────────────────────────────

    def _send(self, message: Any) -> None:
        sys.stdout.write(encode_worker_message(message) + "\n")
        sys.stdout.flush()

    def _send_result(self, stop_reason: str, value: Any, error: str | None = None) -> None:
        self._send(("result", stop_reason, value, self._started, error))

    def _throw_if_cancelled(self) -> None:
        if self._cancelled:
            raise WorkflowError(
                f"workflow run cancelled: {self._cancel_reason}",
                "CANCELLED",
            )

    # ── host-side stdin reader (thread) ───────────────────────

    def _stdin_loop(self) -> None:
        """Daemon thread: relay host messages onto the asyncio loop."""
        assert self._loop is not None
        while True:
            line = sys.stdin.readline()
            if not line:
                self._loop.call_soon_threadsafe(self._handle_host_eof)
                return
            try:
                from .protocol import decode_host_message

                message = decode_host_message(line)
            except (ValueError, KeyError, TypeError) as exc:
                self._loop.call_soon_threadsafe(
                    self._handle_host_error,
                    f"malformed host message: {exc}",
                )
                continue
            self._loop.call_soon_threadsafe(self._handle_host_message, message)

    def _handle_host_eof(self) -> None:
        self._host_dead = True
        for future in list(self._responses.values()):
            if not future.done():
                future.set_exception(
                    WorkflowError(
                        "workflow host disconnected while an agent() call was in flight",
                        "AGENT_RESULT",
                    )
                )
        self._responses.clear()

    def _handle_host_error(self, message: str) -> None:
        self._host_dead = True
        self._cancelled = True
        self._cancel_reason = message

    def _handle_host_message(self, message: Any) -> None:
        kind, *rest = message
        if kind == "cancel":
            self._cancelled = True
            self._cancel_reason = str(rest[0])
            return
        if kind == "agent-started":
            seq, child_id = rest  # type: ignore[misc]
            pending = self._pending_starts.get(int(seq))
            if pending is not None:
                label, phase = pending
                self._send(("agent-start", int(seq), label, phase, child_id))
            return
        seq, result = rest  # type: ignore[misc]
        future = self._responses.pop(int(seq), None)
        if future is not None and not future.done():
            future.set_result(result)

    # ── run ───────────────────────────────────────────────────

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        threading.Thread(target=self._stdin_loop, daemon=True).start()
        try:
            # Let the stdin reader process any buffered host messages (an
            # early ``cancel()`` may already be in the pipe) before the body
            # runs — a cancelled run must not execute its script at all.
            await asyncio.sleep(0.01)
            self._throw_if_cancelled()
            await self._drive()
        except WorkflowError as exc:
            if self._cancelled:
                self._send_result("cancelled", None, error=exc.message)
            else:
                self._send_result("error", None, error=exc.message)
        except Exception as exc:  # noqa: BLE001 — run must never raise
            if self._cancelled:
                self._send_result(
                    "cancelled",
                    None,
                    error=f"workflow run cancelled: {self._cancel_reason}",
                )
            else:
                self._send_result("error", None, error=str(exc) or exc.__class__.__name__)

    async def _drive(self) -> None:
        self._throw_if_cancelled()
        validate_script(self._body, name=self._name)
        check_meta_statement(self._body, name=self._name)
        compiled = compile(wrap_body(self._body), filename=f"workflow:{self._name}", mode="exec")
        hooks: dict[str, Any] = {
            "agent": self._hook_agent,
            "parallel": self._hook_parallel,
            "pipeline": self._hook_pipeline,
            "phase": self._hook_phase,
            "log": self._hook_log,
        }
        globals_dict = build_globals(hooks, self._args)
        try:
            # The workflow DSL is parsed by validate_script/check_meta_statement,
            # wrapped into one async function, and receives only build_globals'
            # restricted hook vocabulary.  This is the intentional interpreter
            # boundary, not execution of an unchecked Python string.
            exec(compiled, globals_dict)  # nosec B102  # noqa: S102
            main = globals_dict["__workflow_main"]
            raw = await main()
            self._throw_if_cancelled()
            value = None if raw is None else materialize_json(raw)
            self._send_result("completed", value)
        except WorkflowError as exc:
            if self._cancelled:
                self._send_result("cancelled", None, error=exc.message)
            else:
                self._send_result("error", None, error=exc.message)
        except BaseException as exc:  # noqa: BLE001 — total render, never raise
            if self._cancelled:
                self._send_result(
                    "cancelled",
                    None,
                    error=f"workflow run cancelled: {self._cancel_reason}",
                )
            else:
                self._send_result("error", None, error=str(exc) or exc.__class__.__name__)

    # ── hook vocabulary ───────────────────────────────────────

    def _hook_phase(self, title: Any) -> None:
        self._throw_if_cancelled()
        if not isinstance(title, str) or not title:
            raise WorkflowError(
                "phase() requires a non-empty title string",
                "INVALID_ARGUMENT",
            )
        self._current_phase = title
        self._send(("phase", title))

    def _hook_log(self, message: Any) -> None:
        self._throw_if_cancelled()
        if not isinstance(message, str):
            raise WorkflowError("log() requires a message string", "INVALID_ARGUMENT")
        self._send(("log", message))

    async def _hook_agent(self, prompt: Any, opts: Any = None) -> Any:
        self._throw_if_cancelled()
        if not isinstance(prompt, str) or not prompt.strip():
            raise WorkflowError(
                "agent() requires a non-empty prompt string",
                "INVALID_ARGUMENT",
            )
        options = self._read_agent_options(opts)
        if self._started >= self._max_total_agents:
            raise WorkflowError(
                f"this run reached its total agent cap ({self._max_total_agents}) "
                "— a runaway-loop backstop; raise maxTotalAgents if the scale is intentional",
                "AGENT_CAP",
            )
        self._started += 1
        seq = self._started
        label = options.get("label") or _default_label(prompt)
        phase = options.get("phase") or self._current_phase

        await self._acquire_slot()
        try:
            self._throw_if_cancelled()
            request: AgentStartRequest = {"prompt": prompt.strip()}
            if options.get("schema") is not None:
                request["schema"] = options["schema"]
            if options.get("provider") is not None:
                request["agent"] = options["provider"]
            if options.get("model") is not None:
                request["model"] = options["model"]
            # The host answers with ``agent-started`` (child published) and
            # later ``agent-response``; the start event carries the child id.
            self._pending_starts[seq] = (label, phase)
            future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            self._responses[seq] = future
            self._send(("agent-request", seq, request))
            result: Any = await future
            self._responses.pop(seq, None)
            self._pending_starts.pop(seq, None)
            if self._cancelled:
                self._send_agent_end(seq, label, phase, None, "cancelled")
                raise WorkflowError(
                    f"workflow run cancelled: {self._cancel_reason}",
                    "CANCELLED",
                )
            child_id = result.get("child_id")
            if not result.get("ok"):
                if result.get("fatal"):
                    # Infrastructure fault (dsh AGENT_START): the host's
                    # dispatch machinery broke, not the child.
                    self._send_agent_end(seq, label, phase, child_id, "failed")
                    raise WorkflowError(
                        f"agent() could not start a child: {result.get('error') or 'unknown host fault'}",
                        "AGENT_START",
                    )
                # An ordinary child failure resolves to None — scripts
                # ``filter(Boolean)`` per the dsh contract.
                self._send_agent_end(seq, label, phase, child_id, "failed")
                return None
            self._send_agent_end(seq, label, phase, child_id, "completed")
            if options.get("schema") is not None:
                # A schema call without a structured value is a child failure.
                if result.get("structured") is None:
                    return None
                return result["structured"]
            return result.get("output") or ""
        finally:
            self._release_slot()

    def _send_agent_end(
        self,
        seq: int,
        label: str,
        phase: str | None,
        child_id: str | None,
        outcome: str,
    ) -> None:
        self._send(("agent-end", seq, outcome, child_id))

    async def _acquire_slot(self) -> None:
        if self._active_slots < self._max_concurrent_agents:
            self._active_slots += 1
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        def _acquire() -> None:
            self._active_slots += 1
            if not future.done():
                future.set_result(None)

        self._slot_waiters.append((future, _acquire))
        try:
            await future
        finally:
            if self._slot_waiters and self._slot_waiters[0][0] is future:
                self._slot_waiters.pop(0)

    def _release_slot(self) -> None:
        self._active_slots -= 1
        while self._slot_waiters:
            waiter, acquire = self._slot_waiters[0]
            if waiter.cancelled():
                self._slot_waiters.pop(0)
                continue
            acquire()
            return

    async def _hook_parallel(self, thunks: Any) -> list[Any]:
        self._throw_if_cancelled()
        if not isinstance(thunks, list):
            raise WorkflowError(
                "parallel() requires an array of zero-argument functions",
                "INVALID_ARGUMENT",
            )
        self._assert_item_cap(len(thunks), "parallel()")
        for index, thunk in enumerate(thunks):
            if not callable(thunk):
                raise WorkflowError(
                    f"parallel() item {index} is not a function",
                    "INVALID_ARGUMENT",
                )
        return await asyncio.gather(*[self._run_thunk(thunk) for thunk in thunks])

    async def _run_thunk(self, thunk: Callable[[], Any]) -> Any:
        """Run one parallel thunk; a synchronous or async throw becomes None
        unless the error is fatal (dsh parallel semantics)."""
        try:
            return await self._contain(thunk())
        except WorkflowError as exc:
            if is_fatal_workflow_error(exc):
                raise
            return None
        except Exception:
            return None

    async def _hook_pipeline(self, items: Any, *stages: Any) -> list[Any]:
        self._throw_if_cancelled()
        if not isinstance(items, list):
            raise WorkflowError("pipeline() requires an items array", "INVALID_ARGUMENT")
        self._assert_item_cap(len(items), "pipeline()")
        if not stages:
            raise WorkflowError(
                "pipeline() requires at least one stage function",
                "INVALID_ARGUMENT",
            )
        for index, stage in enumerate(stages):
            if not callable(stage):
                raise WorkflowError(
                    f"pipeline() stage {index} is not a function",
                    "INVALID_ARGUMENT",
                )
        return await asyncio.gather(
            *[self._run_pipeline_item(item, index, stages) for index, item in enumerate(items)]
        )

    async def _run_pipeline_item(
        self,
        item: Any,
        index: int,
        stages: tuple[Any, ...],
    ) -> Any:
        value = item
        try:
            for stage in stages:
                value = await self._contain(stage(value, item, index))
            return value
        except WorkflowError as exc:
            if is_fatal_workflow_error(exc):
                raise
            return None
        except Exception:
            return None

    async def _contain(self, awaitable: Awaitable[Any]) -> Any:
        """Resolve one hook result (awaitable or plain), mapping ordinary
        failures to ``None`` — like JS ``await`` on a promise or a value."""
        try:
            if inspect.isawaitable(awaitable):
                return await awaitable
            return awaitable
        except WorkflowError as exc:
            if is_fatal_workflow_error(exc):
                raise
            return None
        except Exception:
            return None

    def _assert_item_cap(self, length: int, hook: str) -> None:
        if length > self._max_items_per_call:
            raise WorkflowError(
                f"{hook} received {length} items — over the per-call cap "
                f"({self._max_items_per_call}); split the work or raise the limit",
                "ITEM_CAP",
            )

    # ── agent() options validation (dsh readAgentOptions) ─────

    def _read_agent_options(self, raw_opts: Any) -> dict[str, Any]:
        if raw_opts is None:
            return {}
        if not isinstance(raw_opts, dict):
            raise WorkflowError("agent() options must be an object", "INVALID_ARGUMENT")
        for key in raw_opts:
            if key in _SUPPORTED_AGENT_OPTIONS:
                continue
            if key in _DEFERRED_AGENT_OPTIONS:
                raise WorkflowError(
                    f'agent() option "{key}" is deferred and not supported by this '
                    "engine (supported: label, phase, schema, provider, model)",
                    "UNSUPPORTED_OPTION",
                )
            raise WorkflowError(
                f'agent() option "{key}" is not recognized '
                "(supported: label, phase, schema, provider, model)",
                "UNSUPPORTED_OPTION",
            )
        for key in ("label", "phase", "provider", "model"):
            raw = raw_opts.get(key)
            if raw is not None and not isinstance(raw, str):
                raise WorkflowError(
                    f'agent() option "{key}" must be a string',
                    "INVALID_ARGUMENT",
                )
        schema = raw_opts.get("schema")
        if schema is not None:
            if not isinstance(schema, dict):
                raise WorkflowError(
                    "agent() schema must be an object",
                    "UNSUPPORTED_SCHEMA",
                )
            schema_type = schema.get("type")
            if schema_type is not None and schema_type != "object":
                raise WorkflowError(
                    "agent() schema is outside the supported subset — only "
                    '"object" schemas are supported',
                    "UNSUPPORTED_SCHEMA",
                )
        return raw_opts


def main() -> int:
    """Subprocess entry: ``python -m runtime.execution.workflow.worker``."""
    line = sys.stdin.readline()
    if not line:
        return 2
    try:
        init = json.loads(line)
        execution = WorkflowExecution(
            run_id=str(init.get("runId", "workflow")),
            name=str(init.get("name", "workflow")),
            body=str(init.get("body", "")),
            args=init.get("args"),
            max_total_agents=int(init.get("maxTotalAgents", 1000)),
            max_concurrent_agents=int(init.get("maxConcurrentAgents", 4)),
            max_items_per_call=int(init.get("maxItemsPerCall", 4096)),
        )
    except (ValueError, KeyError, TypeError) as exc:
        sys.stderr.write(f"workflow worker init failed: {exc}\n")
        return 2
    asyncio.run(execution.run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
