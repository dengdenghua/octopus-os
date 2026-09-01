"""Direct-LLM reflection fast-path stream driver.

Extracted from ``realtime_react_stream.py``: ``_drive_reflection_fast_path``
pumps the reflective direct path (no ReAct) on a worker thread and feeds the
resulting events onto an asyncio queue for the consumer to dispatch.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any

from runtime.memory.threads.event_log import EventLog
from runtime.platform.models import ParsedIntent
from runtime.protocol import TurnStatus
from runtime.sensing.gateway._realtime_react_stream_apply import _apply_react_event
from runtime.sensing.gateway._realtime_react_stream_helpers import (
    _SINGLE_AGENT_HEARTBEAT_INTERVAL_S,
    _emit_turn_heartbeat,
    _is_auth_context_error,
    _logger,
    _model_error_reply,
    _personalize_reflex_reply,
    _safe_stream_error_message,
    _try_reflex_reply,
)
from runtime.sensing.gateway.realtime_gateway import EventEmitter

if TYPE_CHECKING:
    from runtime.protocol import Turn
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime


async def _drive_reflection_fast_path(
    runtime: CerebrumRuntime,
    turn: Turn,
    log: EventLog,
    emitter: EventEmitter,
    intent: ParsedIntent,
    agent: Any,
    *,
    model: str | None = None,
) -> None:
    """Pump direct-LLM reflection output into realtime item events."""
    reflex_reply = _try_reflex_reply(runtime, intent)
    if reflex_reply:
        await runtime._emit_agent_message(
            turn,
            log,
            emitter,
            _personalize_reflex_reply(reflex_reply, agent),
        )
        return

    from runtime.safety.approval.cancellation import (
        CancellationSource,
        scoped_cancellation,
    )
    from runtime.sensing.gateway.openai_gateway.stream_handler import (
        _stream_direct_llm_fallback,
    )
    from runtime.sensing.gateway.realtime_turn_routing import local_non_tool_reply

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=64)
    loop = asyncio.get_running_loop()
    cancel_source = CancellationSource()

    def _safe_put(event: dict[str, Any] | None, *, timeout: float = 10.0) -> None:
        try:
            asyncio.run_coroutine_threadsafe(
                queue.put(event),
                loop,
            ).result(timeout=timeout)
        except (RuntimeError, TimeoutError):
            _logger.debug("reflection bridge enqueue dropped")

    def producer() -> None:
        # Chat fast-path (direct LLM, no ReAct). Feed journal_context
        # so token-usage events emitted here carry the thread_id
        # instead of None — this path has no Session/session_scope.
        from runtime.memory.journal.journal_context import journal_context

        _jagent = getattr(agent, "agent_id", None) if agent is not None else None
        with (
            journal_context(
                conversation_id=turn.thread_id,
                agent_id=_jagent,
            ),
            scoped_cancellation(cancel_source.token),
        ):
            try:
                for kind, payload, _final in (
                    _stream_direct_llm_fallback(
                        runtime._stack,
                        intent,
                        agent,
                        model=model,
                        reasoning_effort=(intent.user_context or {}).get(
                            "reasoning_effort",
                        ),
                    )
                    or ()
                ):
                    if cancel_source.is_cancelled:
                        _safe_put({"type": "react_cancelled"})
                        return
                    if kind == "text":
                        evt = {"type": "text_delta", "delta": payload or ""}
                    elif kind == "reasoning":
                        evt = {"type": "thinking_delta", "delta": payload or ""}
                    elif kind == "done":
                        evt = {"type": "throughput", "usage": payload}
                    else:
                        continue
                    _safe_put(evt)
            except Exception as exc:  # noqa: BLE001
                if cancel_source.is_cancelled:
                    _safe_put({"type": "react_cancelled"})
                    return
                fallback = _model_error_reply(exc) or (
                    local_non_tool_reply(intent.raw) if _is_auth_context_error(exc) else None
                )
                if fallback:
                    _safe_put({"type": "text_delta", "delta": fallback})
                    return
                _safe_put(
                    {
                        "type": "react_error",
                        "kind": exc.__class__.__name__,
                        "message": _safe_stream_error_message(exc),
                    }
                )
            finally:
                _safe_put(None, timeout=5.0)

    worker = asyncio.create_task(asyncio.to_thread(producer))
    state = runtime._make_bridge_state(turn.thread_id, agent=agent)

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
                    cancel_source.cancel(reason="user interrupted turn")
                    return
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            return

    watcher = asyncio.create_task(_interrupt_watcher())
    try:
        loop_started = time.monotonic()
        while True:
            try:
                evt = await asyncio.wait_for(
                    queue.get(), timeout=_SINGLE_AGENT_HEARTBEAT_INTERVAL_S
                )
            except TimeoutError:
                # No event for a while: the model is thinking or a tool is
                # running silently. Emit a keepalive (unless the turn is
                # already winding down) so the frontend reads "working",
                # not "stuck", then keep waiting.
                if not (cancel_source.is_cancelled or emitter.is_turn_interrupted(turn.id)):
                    await _emit_turn_heartbeat(emitter, turn, loop_started)
                continue
            if evt is None:
                break
            if emitter.is_turn_interrupted(turn.id):
                if not cancel_source.is_cancelled:
                    cancel_source.cancel(reason="user interrupted turn")
                turn.status = TurnStatus.CANCELLED
                turn.outcome_reason = "user_cancelled"
                if not turn.interrupt_reason:
                    with contextlib.suppress(Exception):
                        reason = emitter.get_interrupt_reason(turn.id)
                        if reason:
                            turn.interrupt_reason = reason
                # Drain rather than break — the producer must reach
                # its ``None`` sentinel for the worker thread to
                # finish cleanly.
                continue
            try:
                await _apply_react_event(runtime, turn, log, emitter, state, evt)
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "reflection event apply failed (kind=%s): %s",
                    evt.get("type") if isinstance(evt, dict) else "?",
                    exc,
                    exc_info=True,
                )
    finally:
        # Trip cancellation so the producer THREAD (asyncio.to_thread,
        # which task cancellation can't reach) observes it and bails
        # fast instead of looping to completion against a dead queue.
        # Without this, a consumer cancelled by ws disconnect leaves
        # the worker piling up pending Queue.put() tasks.
        cancel_source.cancel(reason="consumer teardown")
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        with contextlib.suppress(Exception):
            await worker
    with contextlib.suppress(Exception):
        await state.flush(
            turn,
            log,
            emitter,
            status=state.prose_status_for_turn(turn.status),
        )
