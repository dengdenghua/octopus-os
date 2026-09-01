"""ReAct loop stream driver.

Extracted from ``realtime_react_stream.py``: ``_drive_react`` pumps the
``react_loop`` iterator (or the protocol-native tool-loop fallback) on a
worker thread, marshals every yielded event onto an asyncio queue, and
dispatches them via ``_apply_react_event``.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import Future, InvalidStateError
from typing import TYPE_CHECKING, Any

from runtime.execution.subagents._ambient import react_stack_scope
from runtime.memory.threads.event_log import EventLog
from runtime.platform.models import ParsedIntent
from runtime.platform.models.llm import default_reasoning_effort
from runtime.protocol import (
    ItemMarker,
    ItemStatus,
    McpToolCallItem,
    TurnStatus,
)
from runtime.safety.approval.approval_gate import ApprovalProvider
from runtime.sensing.gateway._realtime_react_stream_apply import (
    _apply_react_event,
    _start_orchestrator_bridge,
)
from runtime.sensing.gateway._realtime_react_stream_helpers import (
    _CRITICAL_STRUCTURAL_EVENT_TYPES,
    _REACT_QUEUE_PUT_TIMEOUT_S,
    _SINGLE_AGENT_HEARTBEAT_INTERVAL_S,
    _TERMINAL_REACT_EVENT_TYPES,
    _agentic_stream_event_to_react_event,
    _apply_react_session_metadata,
    _emit_turn_heartbeat,
    _is_coalescable_delta,
    _lease_renewal_interval_s,
    _logger,
    _QueuedReactEvent,
    _ReactStructuralDeliveryError,
    _safe_stream_error_message,
    _should_use_native_tool_loop,
    _ToolStartAuditError,
)
from runtime.sensing.gateway._realtime_subagent_journal_items import (
    _emit_subagent_lifecycle_item,
    _emit_subagent_progress_item,
    _parse_lifecycle_preview,  # noqa: F401 - compatibility re-export
    _subagent_lifecycle_item_from_journal,
    _subagent_lifecycle_matches,
    _subagent_progress_item_from_journal,
    _subagent_tool_item_from_journal,
)
from runtime.sensing.gateway.realtime_gateway import EventEmitter
from runtime.sensing.gateway.realtime_turn_input import (
    _resume_task_id_from_intent,
)

if TYPE_CHECKING:
    from runtime.protocol import Turn
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime


# Give cooperative providers a brief chance to unwind their stream after an
# explicit Stop.  The user-visible turn must not remain hostage to an upstream
# iterator that ignores cancellation; after this grace period the asyncio
# wrapper is detached while the daemon/provider read finishes in the
# background.  ``consumer_closed`` below prevents that abandoned producer from
# applying events or crossing a tool side-effect boundary.
_INTERRUPT_PRODUCER_DRAIN_TIMEOUT_S = 0.75


# ── Sub-agent lifecycle journal → workbench bridge ────────────────────
# ``run_orchestration`` (the audit.ultracode fan-out) spawns its parallel
# sub-agents via ``_call_agent_parallel`` → ``call_subagent`` WITHOUT an
# in-memory ``event_emitter``, so their ``subagent_spawned`` /
# ``subagent_finished`` events never reach any live stream — only the
# journal mirror (``bridge._safe_journal_emit``) carries them, and only
# when the bound ``Session.metadata`` injects a journal. The realtime WS —
# the only stream the workbench reads — has no journal→WS consumer, so
# the audit's parallel sub-agents render as one opaque ``run_orchestration`` row.
#
# These helpers are that missing consumer: a per-turn journal subscription
# that lifts the marker events onto the turn as the same ``McpToolCallItem``
# the wired paths emit, which the frontend's ``mcpItemToLiveEvent`` already
# translates into lifecycle tiles (zero frontend changes).


def _start_subagent_lifecycle_bridge(
    runtime: Any,
    turn: Any,
    log: EventLog,
    emitter: EventEmitter,
    loop: asyncio.AbstractEventLoop,
    task_id: str,
) -> Callable[[], None] | None:
    """Subscribe the genome journal for this turn's sub-agent lifecycle.

    Returns the unsubscribe callable, or ``None`` when the stack's journal
    isn't a live ``StreamingJournal`` (the base ``Journal.subscribe`` is a
    documented no-op that still returns an unsubscribe — mirror
    ``stream_handler._has_live_subscribe``'s guard).
    """
    journal = getattr(getattr(runtime, "_stack", None), "journal", None)
    if journal is None:
        return None
    from runtime.memory.journal.journal import Journal as _JournalBase

    subscribe = getattr(type(journal), "subscribe", None)
    if subscribe is None or subscribe is _JournalBase.subscribe:
        return None
    task_id = str(task_id or "").strip()
    lane_identity: dict[str, dict[str, Any]] = {}
    started_tools: dict[str, McpToolCallItem] = {}
    progress_items: dict[str, McpToolCallItem] = {}
    progress_text: dict[str, str] = {}

    def _identity_for_event(event: Any) -> dict[str, Any]:
        """Resolve one child without using a shared role as its primary key."""
        role = str(getattr(event, "role_id", "") or "")
        event_agent_id = str(getattr(event, "agent_id", "") or "")
        event_codename = str(getattr(event, "codename", "") or "")
        base: dict[str, Any] = {}
        for alias in (event_codename, event_agent_id, role):
            if alias and alias in lane_identity:
                base = lane_identity[alias]
                break
        return {
            **base,
            "agent_id": event_agent_id or base.get("agent_id"),
            "codename": event_codename or base.get("codename"),
            "avatar": getattr(event, "avatar", "") or base.get("avatar"),
            "role": role or base.get("role"),
        }

    def _remember_identity(identity: dict[str, Any], role: str = "") -> None:
        # Codename and requested id are unique lane aliases. A resolved role
        # such as ``explorer`` is shared by siblings, so only use it as a
        # fallback for legacy events that carry no stronger identity.
        aliases = [
            str(identity.get("requested_agent_id") or ""),
            str(identity.get("agent_id") or ""),
            str(identity.get("codename") or ""),
        ]
        if not any(aliases) and role:
            aliases.append(role)
        for alias in aliases:
            if alias:
                lane_identity[alias] = identity

    def _on_journal_event(event: Any) -> None:
        if not _subagent_lifecycle_matches(event, task_id):
            return
        if getattr(event, "event_type", None) == "sub_text_delta":
            role = str(getattr(event, "role_id", "") or "")
            identity = _identity_for_event(event)
            delta = str(getattr(event, "delta", "") or "")
            if not delta:
                return
            lane_key = str(
                getattr(event, "session_id", "")
                or (identity or {}).get("codename")
                or (identity or {}).get("agent_id")
                or role
            )
            previous = progress_text.get(lane_key, "")
            combined = previous + delta
            # The finish marker carries the complete answer. Keep the live
            # progress item bounded so token streaming cannot grow one turn
            # without limit while preserving the newest visible context.
            if len(combined) > 12_000:
                combined = "…（较早输出已省略）\n" + combined[-11_500:]
            progress_text[lane_key] = combined
            progress_item = _subagent_progress_item_from_journal(
                event,
                identity=identity,
                accumulated=combined,
            )
            if progress_item is None:
                return
            existing = progress_items.get(progress_item.id)
            if existing is not None:
                progress_item = progress_item.model_copy(update={"created_at": existing.created_at})
            progress_items[progress_item.id] = progress_item
            try:
                asyncio.run_coroutine_threadsafe(
                    _emit_subagent_progress_item(
                        turn,
                        log,
                        emitter,
                        progress_item,
                        started=existing is None,
                    ),
                    loop,
                )
            except (RuntimeError, ValueError):
                return
            return
        item = _subagent_lifecycle_item_from_journal(event)
        if item is not None and item.tool == ItemMarker.SUBAGENT_SPAWNED.value:
            identity = dict(item.arguments)
            role = str(identity.get("role") or getattr(event, "role_id", "") or "")
            _remember_identity(identity, role)
        if item is None:
            role = str(getattr(event, "role_id", "") or "")
            identity = _identity_for_event(event)
            probe = _subagent_tool_item_from_journal(
                event,
                identity=identity,
            )
            started_item = started_tools.get(probe.id) if probe is not None else None
            item = _subagent_tool_item_from_journal(
                event,
                identity=identity,
                started_item=started_item,
            )
        if item is None:
            return
        terminal = item.status in {
            ItemStatus.COMPLETED,
            ItemStatus.FAILED,
        }
        if not terminal:
            started_tools[item.id] = item
        else:
            started_tools.pop(item.id, None)

        terminal_progress: list[McpToolCallItem] = []
        if item.tool == ItemMarker.SUBAGENT_FINISHED.value:
            result = item.result if isinstance(item.result, dict) else {}
            target_agent_id = str(result.get("agent_id") or "")
            target_codename = str(result.get("codename") or "")
            target_role = str(result.get("role") or "")
            # McpToolProgress has its own compact wire vocabulary.  Passing
            # ItemStatus values ("completed"/"failed") here serialized an
            # invalid progress object and left some clients showing a stale
            # spinner after the child had finished.
            progress_status = "done" if item.status == ItemStatus.COMPLETED else "error"
            for progress_id, progress_item in list(progress_items.items()):
                args = progress_item.arguments
                progress_agent_id = str(args.get("agent_id") or "")
                progress_codename = str(args.get("subagent_codename") or "")
                progress_role = str(args.get("sub_agent_role") or "")
                same_agent = bool(
                    (target_agent_id and progress_agent_id == target_agent_id)
                    or (target_codename and progress_codename == target_codename)
                    or (
                        not target_agent_id
                        and not target_codename
                        and target_role
                        and progress_role == target_role
                    )
                )
                if not same_agent:
                    continue
                progress = progress_item.progress
                completed_progress = progress_item.model_copy(
                    update={
                        "status": item.status,
                        "progress": (
                            progress.model_copy(update={"status": progress_status})
                            if progress is not None
                            else None
                        ),
                    }
                )
                progress_items[progress_id] = completed_progress
                terminal_progress.append(completed_progress)

        async def _emit_item_sequence() -> None:
            # Finish a child's public text lane before its lifecycle marker so
            # a completed card never keeps a stale spinner while sibling
            # agents are still running.
            for progress_item in terminal_progress:
                await _emit_subagent_lifecycle_item(
                    turn,
                    log,
                    emitter,
                    progress_item,
                    terminal=True,
                )
            await _emit_subagent_lifecycle_item(
                turn,
                log,
                emitter,
                item,
                terminal=terminal,
            )

        try:
            asyncio.run_coroutine_threadsafe(
                _emit_item_sequence(),
                loop,
            )
        except (RuntimeError, ValueError):
            # Loop already closed / task cancelled — drop the frame rather
            # than leak it into a torn-down turn.
            return

    try:
        return journal.subscribe(_on_journal_event)
    except Exception:  # noqa: BLE001 — telemetry bridge never breaks the turn
        return None


async def _drive_react(
    runtime: CerebrumRuntime,
    turn: Turn,
    log: EventLog,
    emitter: EventEmitter,
    intent: ParsedIntent,
    provider: ApprovalProvider,
    agent: Any,
    *,
    model: str | None = None,
) -> None:
    """Pump the react_loop iterator, mapping each event to ``item/*``.

    The loop runs on a worker thread (``asyncio.to_thread``) so
    synchronous LLM calls inside ``stream_react_loop`` don't block
    the event loop. Each yielded event is delivered back to the
    coroutine via a queue.
    """
    from runtime.core.cerebrum.react_loop import stream_react_loop
    from runtime.core.cerebrum.react_step_evaluator import build_runtime_step_evaluator
    from runtime.safety.approval.cancellation import (
        CancellationSource,
        scoped_cancellation,
    )

    queue: asyncio.Queue[_QueuedReactEvent | dict[str, Any]] = asyncio.Queue(maxsize=64)
    loop = asyncio.get_running_loop()
    producer_done = asyncio.Event()
    interrupt_requested = asyncio.Event()
    consumer_closed = threading.Event()

    # Per-turn cancellation source. Tripped when the gateway records a
    # ``turn/interrupt`` for this turn id; every tool call inside
    # ``stream_react_loop`` sees the same token via the
    # ``scoped_cancellation`` contextvar and bails out fast.
    cancel_source = CancellationSource()
    pending_apply_receipts: set[Future[None]] = set()
    pending_apply_receipts_lock = threading.Lock()
    producer_failure: BaseException | None = None

    def _settle_apply_receipt(
        receipt: Future[None] | None,
        *,
        error: BaseException | None = None,
    ) -> None:
        if receipt is None:
            return
        with contextlib.suppress(InvalidStateError):
            if error is None:
                receipt.set_result(None)
            else:
                receipt.set_exception(error)

    def _safe_put(event: dict[str, Any], *, timeout: float | None = None) -> None:
        """Bounded blocking ``queue.put`` from the worker thread.

        ``run_coroutine_threadsafe(...).result()`` without a timeout
        deadlocks the worker if the consumer exits early (exception
        in the dispatch loop, ws error, etc.). Bounded blocking
        preserves backpressure for the normal case while leaving a
        kill-switch when something downstream is wedged.

        Coalescable decorative deltas bypass the blocking put: they are
        high-frequency and individually disposable, so on a full queue we
        drop the newest delta instead of stalling the producer 10s (which
        used to cascade and lose *structural* events downstream).
        """
        event_kind = str(event.get("type") or "")
        if consumer_closed.is_set():
            # Never let a provider that wakes up after the turn was already
            # cancelled advance past ``yield tool_start`` into the real side
            # effect. Decorative/prose/terminal events are simply abandoned;
            # the turn already has an authoritative terminal snapshot.
            if event_kind == "tool_start":
                raise _ToolStartAuditError(
                    "tool execution blocked because the interrupted turn already closed"
                )
            return

        timeout_s = _REACT_QUEUE_PUT_TIMEOUT_S if timeout is None else max(0.01, timeout)
        if _is_coalescable_delta(event):
            coalesce_future: Future[None] | None = None
            try:
                # put_nowait is a plain method, so wrap it in a coroutine that
                # swallows QueueFull: on a full queue the decorative delta is
                # dropped rather than making the producer block 10s.
                async def _coalesce() -> None:
                    with contextlib.suppress(asyncio.QueueFull):
                        queue.put_nowait(event)

                coalesce_future = asyncio.run_coroutine_threadsafe(
                    _coalesce(),
                    loop,
                )
                coalesce_future.result(timeout=0.05)
            except (RuntimeError, TimeoutError):
                if coalesce_future is not None:
                    coalesce_future.cancel()
                # Loop closed or consumer wedged — drop the decorative delta.
                _logger.debug(
                    "react bridge coalesced-delta drop (consumer slow) event=%s",
                    event.get("type") if isinstance(event, dict) else None,
                )
            return

        apply_receipt: Future[None] | None = None
        queued_event: _QueuedReactEvent | dict[str, Any] = event
        if isinstance(event, dict) and event.get("type") == "tool_start":
            apply_receipt = Future()
            queued_event = _QueuedReactEvent(event=event, applied=apply_receipt)
            with pending_apply_receipts_lock:
                pending_apply_receipts.add(apply_receipt)

        put_future = asyncio.run_coroutine_threadsafe(
            queue.put(queued_event),
            loop,
        )
        try:
            put_future.result(timeout=timeout_s)
        except (FutureCancelledError, RuntimeError, TimeoutError) as exc:
            put_future.cancel()
            if event_kind in _CRITICAL_STRUCTURAL_EVENT_TYPES:
                if apply_receipt is not None:
                    apply_receipt.cancel()
                cancel_source.cancel(reason=f"critical {event_kind} enqueue failed")
                with pending_apply_receipts_lock:
                    if apply_receipt is not None:
                        pending_apply_receipts.discard(apply_receipt)
                error_type = (
                    _ToolStartAuditError
                    if event_kind == "tool_start"
                    else _ReactStructuralDeliveryError
                )
                raise error_type(
                    f"critical react event {event_kind!r} could not be enqueued"
                ) from exc
            # RuntimeError: loop closed.
            # TimeoutError: consumer stuck — drop this event rather
            # than block the worker indefinitely.
            _logger.warning(
                "react bridge enqueue failed/timed out (event=%s) — "
                "text/tool events may be lost, frontend may show incomplete output",
                event.get("type") if isinstance(event, dict) else event,
            )
            return

        if apply_receipt is None:
            return
        try:
            # Advancing the generator after ``yield tool_start`` executes the
            # real handler. Wait until the async reducer has durably journaled
            # the CommandExecution item (and completed its live notification)
            # before allowing that next() call.
            apply_receipt.result(timeout=timeout_s)
        except Exception as exc:
            apply_receipt.cancel()
            cancel_source.cancel(reason="tool_start durable audit failed")
            raise _ToolStartAuditError(
                "tool execution blocked because its start event was not durably applied"
            ) from exc
        finally:
            with pending_apply_receipts_lock:
                pending_apply_receipts.discard(apply_receipt)

    def _push_chunk(call_id: str, stream: str, chunk: str) -> None:
        # Called from a reader sub-thread inside the tool's subprocess
        # plumbing. Hop back to the asyncio loop so the queue stays
        # single-producer-from-the-event-loop's-perspective.
        #
        # We use a SHORT timeout here (vs the 10s in _safe_put):
        # tool stdout chunks are high-frequency and individually
        # disposable — better to drop a chunk than block the
        # subprocess reader thread for 10s if the consumer is slow.
        evt = {
            "type": "tool_output_delta",
            "tool_call_id": call_id,
            "stream": stream,
            "delta": chunk,
        }

        async def _enqueue_chunk() -> None:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(evt)

        put_future = asyncio.run_coroutine_threadsafe(_enqueue_chunk(), loop)
        try:
            put_future.result(timeout=0.05)
        except (FutureCancelledError, RuntimeError, TimeoutError):
            put_future.cancel()
            _logger.warning(
                "tool_output_delta drop (consumer slow) — command output may be truncated in the UI"
            )

    def producer() -> None:
        nonlocal producer_failure
        # ``asyncio.to_thread`` copies ContextVars from the calling
        # task, so installing the cancellation scope here makes the
        # token visible to every subprocess call downstream.
        from runtime.memory.journal.journal_context import journal_context
        from runtime.platform.process.session import Session, session_scope

        session_metadata = dict(intent.user_context or {})
        _apply_react_session_metadata(session_metadata, runtime._stack, provider)
        # Sub-agents spawned inside the turn (run_orchestration fan-out,
        # call_agent_parallel, ...) mirror their lifecycle/tool events onto
        # the journal ONLY when the bound Session carries one
        # (see _ephemeral_events._emit_subagent_lifecycle_event). The
        # realtime WS is not a journal subscriber, so this injection is what
        # lets the per-turn bridge below lift those events onto the workbench.
        _stack_journal = getattr(getattr(runtime, "_stack", None), "journal", None)
        if _stack_journal is not None:
            session_metadata.setdefault("journal", _stack_journal)
        if runtime._workspaces is not None:
            session_metadata["_artifact_output_root"] = str(
                runtime._workspaces.layout(turn.thread_id).final,
            )
        if runtime._trace_store is not None:
            session_metadata["_trace_store"] = runtime._trace_store
        session_agent = agent if hasattr(agent, "agent_id") else None
        _journal_tenant_id = str(session_metadata.get("tenant_id") or "").strip() or None
        _journal_owner_actor_id = str(session_metadata.get("owner_actor_id") or "").strip() or None
        turn_session = Session(
            actor=getattr(intent, "actor", None) or _journal_owner_actor_id,
            agent=session_agent,
            thread_id=turn.thread_id,
            conversation_id=turn.thread_id,
            turn_id=turn.id,
            metadata=session_metadata,
        )
        # journal_context drives a SEPARATE contextvar that journal
        # write_* methods read for conversation_id/agent_id; without
        # it every journal/trace row lands with thread_id=None.
        # session_scope alone does not feed it.
        _journal_agent_id = getattr(session_agent, "agent_id", None)
        from runtime.execution.suckers.delegation_skills import (
            orchestration_progress_scope,
            workflow_settlement_scope,
        )

        def _orchestration_progress(line: str) -> None:
            _safe_put({"type": "thinking_delta", "delta": line + "\n"})

        def _workflow_settlement(payload: dict) -> None:
            """Handle workflow completion and emit notification to client."""
            try:
                from runtime.protocol import ServerMethod

                # Build notification payload
                notification_payload = {
                    "threadId": turn.thread_id,
                    "workflowName": payload.get("workflowName", "workflow"),
                    "workflowDescription": payload.get("workflowDescription", ""),
                    "runId": payload.get("runId", ""),
                    "stopReason": payload.get("stopReason", "unknown"),
                    "success": payload.get("success", False),
                    "agentsStarted": payload.get("agentsStarted", 0),
                    "error": payload.get("error"),
                }

                # Schedule notification on the event loop (emitter.notify is async)
                import asyncio

                asyncio.create_task(
                    emitter.notify(
                        ServerMethod.WORKFLOW_COMPLETED,
                        notification_payload,
                    )
                )
            except Exception:  # noqa: BLE001 — notification is best-effort
                pass

        with (
            session_scope(turn_session),
            journal_context(
                conversation_id=turn.thread_id,
                agent_id=_journal_agent_id,
                tenant_id=_journal_tenant_id,
                owner_actor_id=_journal_owner_actor_id,
            ),
            scoped_cancellation(cancel_source.token),
            orchestration_progress_scope(_orchestration_progress),
            workflow_settlement_scope(_workflow_settlement),
            react_stack_scope(runtime._stack),
        ):
            # Per-turn sub-agent lifecycle bridge. Launched once the react
            # boot yields ``react_started`` — the task_id it carries is the
            # only reliable key for the journal events sub-agents mirror.
            unsubscribe_lifecycle: Callable[[], None] | None = None
            try:
                _planning_mode = bool(
                    (intent.user_context or {}).get("planning_mode", False),
                )
                if _should_use_native_tool_loop(
                    runtime._stack,
                    intent,
                    planning_mode=_planning_mode,
                    model=model,
                ):
                    from runtime.sensing.gateway.tool_bridge import (
                        stream_agentic_fallback,
                    )

                    for kind, delta, final in stream_agentic_fallback(
                        runtime._stack,
                        intent,
                        agent,
                        model=model,
                        steering_drain=lambda: runtime._drain_turn_steering(turn.id),
                    ):
                        evt = _agentic_stream_event_to_react_event(
                            kind,
                            delta,
                            final,
                        )
                        if evt is not None:
                            _safe_put(evt)
                else:
                    _resume_task_id = _resume_task_id_from_intent(intent)

                    def _on_auto_parallel_batch(batch_id: str) -> None:
                        # The auto-parallel short-circuit (running on the
                        # producer thread) dispatched a parallel batch. Hop
                        # back to the event loop and start the orchestrator
                        # bridge so the workbench renders each sub-task as a
                        # live tile immediately.
                        async def _spawn() -> None:
                            _start_orchestrator_bridge(runtime, turn, log, emitter, batch_id)

                        with contextlib.suppress(RuntimeError):
                            asyncio.run_coroutine_threadsafe(_spawn(), loop)

                    events: Iterator[dict[str, Any]] = stream_react_loop(
                        runtime._stack,
                        intent,
                        agent,
                        thread_id=turn.thread_id,
                        max_iterations=runtime._max_iterations,
                        resume_task_id=_resume_task_id,
                        approval_provider=provider,
                        output_chunk_sink=_push_chunk,
                        step_evaluator=build_runtime_step_evaluator(),
                        planning_mode=_planning_mode,
                        model=model,
                        reasoning_effort=(
                            (intent.user_context or {}).get("reasoning_effort")
                            or default_reasoning_effort(model)
                        ),
                        steering_drain=lambda: runtime._drain_turn_steering(turn.id),
                        on_auto_parallel_batch=_on_auto_parallel_batch,
                    )
                    for evt in events:
                        if (
                            isinstance(evt, dict)
                            and evt.get("type") == "react_started"
                            and unsubscribe_lifecycle is None
                        ):
                            _react_task_id = str(evt.get("task_id") or "").strip()
                            if _react_task_id:
                                # Stamp task_id onto the session so sub-agent
                                # journal events carry it (they read
                                # session.metadata, and the react boot
                                # generates the id after this session was
                                # created).
                                with contextlib.suppress(Exception):
                                    turn_session.metadata["task_id"] = _react_task_id
                                unsubscribe_lifecycle = _start_subagent_lifecycle_bridge(
                                    runtime,
                                    turn,
                                    log,
                                    emitter,
                                    loop,
                                    _react_task_id,
                                )
                        _safe_put(evt)
            except Exception as exc:
                producer_failure = exc
                # The queue may be the very resource that failed. Keep the
                # exception in shared control-plane state above, then make a
                # best effort to publish the ordinary react_error event. The
                # consumer converts the shared failure to an explicit failed
                # turn after it drains all events that did make it through.
                with contextlib.suppress(Exception):
                    _safe_put(
                        {
                            "type": "react_error",
                            "kind": exc.__class__.__name__,
                            "message": _safe_stream_error_message(exc),
                        }
                    )
            finally:
                if unsubscribe_lifecycle is not None:
                    with contextlib.suppress(Exception):
                        unsubscribe_lifecycle()
                # Completion cannot be a capacity-bound queue sentinel: signal
                # it independently, then let the consumer drain the queue.
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(producer_done.set)

    worker = asyncio.create_task(asyncio.to_thread(producer))
    state = runtime._make_bridge_state(turn.thread_id, turn.id, agent=agent)

    async def _interrupt_watcher() -> None:
        # Polls the gateway's interrupt registry. Consumer-side polling
        # alone isn't enough: if the producer is blocked inside a long
        # subprocess.wait, no events reach the queue and the consumer
        # never wakes to notice. This task trips cancellation the
        # instant the flag flips, unblocking the subprocess wait via
        # current_cancellation_token() inside stream_run.
        try:
            while not cancel_source.is_cancelled:
                if emitter.is_turn_interrupted(turn.id):
                    interrupt_requested.set()
                    cancel_source.cancel(reason="user interrupted turn")
                    return
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            return

    watcher = asyncio.create_task(_interrupt_watcher())

    # ── Supervisor lease renewal ─────────────────────────────────
    # The realtime react loop (unlike the execution/loops controller
    # path) never renews its TaskSupervisor lease. A turn that outlives
    # the default 300s TTL fails at finish with "lease is no longer
    # current" and stays a zombie "running" task. Heartbeat from the
    # consumer loop (throttled to lease_ttl/3, matching the loops path)
    # keeps the lease alive for long tasks and still lets them
    # terminate cleanly.
    supervisor = getattr(runtime, "_task_supervisor", None)
    _last_supervisor_heartbeat = time.monotonic()
    _supervisor_heartbeat_interval = 0.0
    if supervisor is not None:
        try:
            _supervisor_heartbeat_interval = _lease_renewal_interval_s(
                float(supervisor.lease_ttl_seconds)
            )
        except Exception:  # noqa: BLE001 — malformed supervisor; skip renewal
            _supervisor_heartbeat_interval = 0.0

    def _supervisor_heartbeat_if_due(now: float) -> None:
        nonlocal _last_supervisor_heartbeat
        if (
            supervisor is None
            or _supervisor_heartbeat_interval <= 0.0
            or now - _last_supervisor_heartbeat < _supervisor_heartbeat_interval
        ):
            return
        task_id = turn.task_id or ""
        if not task_id:
            # react_started not seen yet — nothing registered to renew.
            return
        _last_supervisor_heartbeat = now
        try:
            supervisor.heartbeat(task_id)
        except Exception as exc:  # noqa: BLE001 — lease lost/revoked; abort turn
            _logger.warning(
                "react supervisor heartbeat failed for %s: %s — cancelling turn",
                task_id,
                exc,
            )
            if not cancel_source.is_cancelled:
                cancel_source.cancel(reason="task supervisor lease lost")

    saw_terminal_event = False
    structural_apply_failure: dict[str, Any] | None = None
    user_interrupt_terminal = False

    def _mark_user_interrupted_turn() -> None:
        nonlocal user_interrupt_terminal
        user_interrupt_terminal = True
        if not cancel_source.is_cancelled:
            cancel_source.cancel(reason="user interrupted turn")
        turn.status = TurnStatus.CANCELLED
        turn.outcome_reason = "user_cancelled"
        if not turn.interrupt_reason:
            with contextlib.suppress(Exception):
                reason = emitter.get_interrupt_reason(turn.id)
                if reason:
                    turn.interrupt_reason = reason

    producer_done_waiter = asyncio.create_task(producer_done.wait())
    interrupt_waiter = asyncio.create_task(interrupt_requested.wait())
    queue_getter: asyncio.Task[_QueuedReactEvent | dict[str, Any]] | None = None
    try:
        loop_started = time.monotonic()
        while True:
            _supervisor_heartbeat_if_due(time.monotonic())
            if producer_done.is_set() and queue.empty():
                break
            queue_getter = asyncio.create_task(queue.get())
            ready, _pending = await asyncio.wait(
                {queue_getter, producer_done_waiter, interrupt_waiter},
                timeout=_SINGLE_AGENT_HEARTBEAT_INTERVAL_S,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if interrupt_waiter in ready:
                queue_getter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await queue_getter
                _mark_user_interrupted_turn()
                # Do not wait for a provider iterator that is stuck before its
                # first byte. The finally block gives cooperative producers a
                # short drain window, then detaches them safely.
                break
            if queue_getter in ready:
                queued = queue_getter.result()
            elif producer_done_waiter in ready:
                # Done is set after all producer puts settle. Drain anything
                # already queued; an empty queue is now terminal.
                if queue.empty():
                    queue_getter.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await queue_getter
                    break
                queued = await queue_getter
            else:
                queue_getter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await queue_getter
                # No event for a while: the model is thinking or a tool is
                # running silently. Emit a keepalive (unless the turn is
                # already winding down) so the frontend reads "working",
                # not "stuck", then keep waiting.
                if not (cancel_source.is_cancelled or emitter.is_turn_interrupted(turn.id)):
                    await runtime._publish_discovered_steering(turn, emitter)
                    await _emit_turn_heartbeat(emitter, turn, loop_started)
                continue
            if isinstance(queued, _QueuedReactEvent):
                evt = queued.event
                apply_receipt = queued.applied
            else:
                evt = queued
                apply_receipt = None
            await runtime._publish_discovered_steering(turn, emitter)
            event_kind = str(evt.get("type") or "")
            if emitter.is_turn_interrupted(turn.id):
                _settle_apply_receipt(
                    apply_receipt,
                    error=_ToolStartAuditError(
                        "tool_start was skipped because the turn was interrupted"
                    ),
                )
                _mark_user_interrupted_turn()
                # The turn's control-plane terminal state must not wait for a
                # silent provider. Teardown below drains queued receipts and
                # closes the consumer boundary before detaching if needed.
                break
            try:
                await _apply_react_event(runtime, turn, log, emitter, state, evt)
            except asyncio.CancelledError:
                _settle_apply_receipt(
                    apply_receipt,
                    error=_ToolStartAuditError(
                        "tool_start apply was cancelled before the audit boundary"
                    ),
                )
                raise
            except Exception as exc:  # noqa: BLE001
                _settle_apply_receipt(apply_receipt, error=exc)
                if event_kind in _CRITICAL_STRUCTURAL_EVENT_TYPES:
                    cancel_source.cancel(reason=f"critical {event_kind} reducer apply failed")
                    if structural_apply_failure is None:
                        structural_apply_failure = {
                            "code": "react_structural_event_apply_failed",
                            "message": _safe_stream_error_message(exc),
                            "event_type": event_kind,
                            "exception_type": exc.__class__.__name__,
                        }
                    turn.status = TurnStatus.FAILED
                    turn.outcome_reason = "react_structural_event_apply_failed"
                    turn.error = dict(structural_apply_failure)
                # A single bad event shouldn't kill the dispatch
                # loop. Critical lifecycle failures still mark the turn
                # failed and cancel production above; draining lets queued
                # receipts close without converting that failure to success.
                _logger.warning(
                    "react event apply failed (kind=%s): %s",
                    evt.get("type") if isinstance(evt, dict) else "?",
                    exc,
                    exc_info=True,
                )
            else:
                if event_kind in _TERMINAL_REACT_EVENT_TYPES:
                    saw_terminal_event = True
                if apply_receipt is not None and (
                    cancel_source.is_cancelled or emitter.is_turn_interrupted(turn.id)
                ):
                    _settle_apply_receipt(
                        apply_receipt,
                        error=_ToolStartAuditError(
                            "tool_start was durably recorded but execution was cancelled"
                        ),
                    )
                else:
                    _settle_apply_receipt(apply_receipt)
    finally:
        # Trip cancellation so the producer THREAD (asyncio.to_thread)
        # observes it and bails — task cancellation alone can't stop a
        # real OS thread. Since audit T-01 the consumer only tears down
        # when the turn genuinely ends (terminal event, explicit
        # interrupt, or supervisor lease loss) — a dropped WebSocket no
        # longer reaches this path; the turn runs on server-side.
        cancel_source.cancel(reason="consumer teardown")
        consumer_closed.set()
        if queue_getter is not None and not queue_getter.done():
            queue_getter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await queue_getter
        if not producer_done_waiter.done():
            producer_done_waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer_done_waiter
        if not interrupt_waiter.done():
            interrupt_waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await interrupt_waiter
        # Reject receipts before awaiting the worker so teardown cannot
        # advance a stranded tool_start into its side effect.
        with pending_apply_receipts_lock:
            teardown_receipts = tuple(pending_apply_receipts)
        for receipt in teardown_receipts:
            _settle_apply_receipt(
                receipt,
                error=_ToolStartAuditError("tool_start skipped during consumer teardown"),
            )
        while True:
            try:
                abandoned = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if isinstance(abandoned, _QueuedReactEvent):
                _settle_apply_receipt(
                    abandoned.applied,
                    error=_ToolStartAuditError("tool_start skipped during consumer teardown"),
                )
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        if user_interrupt_terminal and not worker.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(worker),
                    timeout=_INTERRUPT_PRODUCER_DRAIN_TIMEOUT_S,
                )
            except TimeoutError:
                # ``asyncio.to_thread`` cannot kill the underlying OS thread.
                # Leave its wrapper detached so the provider can still unwind
                # and release the executor thread. The cancellation token plus
                # ``consumer_closed`` keep the late producer from publishing
                # output or starting a tool. Retrieve the eventual result to
                # avoid a detached-task exception warning.
                _logger.warning(
                    "react producer did not drain within %.2fs after interrupt; detaching it",
                    _INTERRUPT_PRODUCER_DRAIN_TIMEOUT_S,
                )

                def _consume_detached_worker_result(task: asyncio.Task[Any]) -> None:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        task.result()

                worker.add_done_callback(_consume_detached_worker_result)
            except Exception:  # noqa: BLE001 - producer records its own failure
                pass
        else:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await worker
        # Cancel any live orchestrator bridges for this turn. Each bridge
        # subscribes to a parallel batch that already terminated (the loop
        # consumed its synthetic observation), so leaving them running would
        # only leak tasks idling until batch GC.
        bridge_tasks: set[asyncio.Task] = getattr(runtime, "_orchestrator_bridge_tasks", set())
        for task in list(bridge_tasks):
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*bridge_tasks, return_exceptions=True)

    # Finalize anything still open. Wrapped in suppress so a torn-
    # down ws doesn't take the whole turn-completion path with it.
    if structural_apply_failure is not None:
        # A later drained pause/cancel cannot mask the audit failure.
        turn.status = TurnStatus.FAILED
        turn.outcome_reason = "react_structural_event_apply_failed"
        turn.error = dict(structural_apply_failure)
    if producer_failure is not None and turn.status == TurnStatus.IN_PROGRESS:
        structural_delivery_failure = isinstance(
            producer_failure,
            _ReactStructuralDeliveryError,
        )
        failure_code = (
            "react_structural_event_delivery_failed"
            if structural_delivery_failure
            else "react_producer_failed"
        )
        turn.status = TurnStatus.FAILED
        turn.outcome_reason = failure_code
        turn.error = {
            "code": failure_code,
            "message": _safe_stream_error_message(producer_failure),
            "exception_type": producer_failure.__class__.__name__,
        }
    if not saw_terminal_event and turn.status == TurnStatus.IN_PROGRESS:
        answer_text = (
            str(state.agent_message.text or "").strip() if state.agent_message is not None else ""
        )
        if answer_text:
            # Some provider adapters finish after yielding the complete
            # text_delta stream but omit the trailing react_completed event.
            # The answer item is already user-visible and is stronger terminal
            # evidence than the generator's empty return value; finalize it as
            # success instead of appending a contradictory error card.
            await _apply_react_event(
                runtime,
                turn,
                log,
                emitter,
                state,
                {"type": "react_completed", "recovered_from_text": True},
            )
        else:
            # A generator that returns ``None`` without a terminal event used
            # to make any earlier tool/commentary item count as a successful
            # turn, leaving the user with progress fragments and no final
            # answer. Fail explicitly while preserving those fragments for a
            # later Continue.
            await _apply_react_event(
                runtime,
                turn,
                log,
                emitter,
                state,
                {
                    "type": "react_error",
                    "kind": "missing_terminal_answer",
                    "message": (
                        "模型执行已结束，但没有生成可确认的最终答案。阶段进度已保留；"
                        "请点击继续重新收敛，或切换模型后重试。"
                    ),
                },
            )
    with contextlib.suppress(Exception):
        await state.flush(
            turn,
            log,
            emitter,
            status=state.prose_status_for_turn(turn.status),
        )
    if turn.status == TurnStatus.IN_PROGRESS:
        with contextlib.suppress(Exception):
            await state.finalize_workbench(
                turn,
                log,
                emitter,
                terminal_status=TurnStatus.COMPLETED,
            )
    # Note: background tool watchers (started by ``track_background_tool``)
    # are intentionally NOT cancelled here. They're designed to outlive
    # the current turn — the user starts a long-running shell command,
    # the LLM finalises with ``react_completed``, and the watcher keeps
    # streaming output deltas onto the open ``commandExecution`` item
    # until the process exits. See ``test_background_tool_item_completes
    # _after_turn_response``.
