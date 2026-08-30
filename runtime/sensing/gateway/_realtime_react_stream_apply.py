"""Reducer that maps bridge events to ``item/*`` notifications.

Extracted from ``realtime_react_stream.py``: ``_apply_react_event`` turns a
single event dict (text/commentary/tool/lifecycle/grounding ...) into the
corresponding ``item/*`` / ``turn/*`` / ``thread/*`` notifications via the
bridge state. Kept independent of the other ``_realtime_react_stream_*``
submodules.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from runtime.memory.threads.event_log import EventLog
from runtime.protocol import (
    ErrorItem,
    GroundingSource,
    ItemStatus,
    ServerMethod,
    TurnStatus,
    VisibilityItem,
)
from runtime.sensing.gateway.realtime_event_bridge import _ReactBridgeState
from runtime.sensing.gateway.realtime_gateway import EventEmitter

if TYPE_CHECKING:
    from runtime.protocol import Turn
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime


def _start_orchestrator_bridge(
    runtime: CerebrumRuntime,
    turn: Turn,
    log: EventLog,
    emitter: EventEmitter,
    batch_id: str,
) -> None:
    """Subscribe to a parallel batch and render its tasks as subagent tiles.

    Spawns a self-terminating task that streams ``batch_id``'s
    ``task_update`` events onto ``turn`` as ``SubagentItem``s. The task is
    tracked on ``runtime`` so the turn driver can cancel it on teardown and
    it is not garbage-collected mid-subscription.
    """
    from runtime.core.cerebrum.agent_auto_parallel import (
        get_auto_parallel_orchestrator,
    )
    from runtime.sensing.gateway._realtime_orchestrator_bridge import (
        bridge_orchestrator_batch,
    )

    tasks: set[asyncio.Task[Any]] | None = getattr(runtime, "_orchestrator_bridge_tasks", None)
    if tasks is None:
        tasks = set()
        runtime._orchestrator_bridge_tasks = tasks  # type: ignore[attr-defined]

    async def _run() -> None:
        try:
            await bridge_orchestrator_batch(
                get_auto_parallel_orchestrator(),
                batch_id,
                turn,
                log,
                emitter,
            )
        finally:
            tasks.discard(asyncio.current_task())
            with contextlib.suppress(Exception):
                await emitter.notify(
                    ServerMethod.TURN_HEARTBEAT,
                    {
                        "threadId": turn.thread_id,
                        "turnId": turn.id,
                        "role": "parallel",
                    },
                )

    task = asyncio.create_task(_run())
    tasks.add(task)


async def _apply_react_event(
    runtime: CerebrumRuntime,
    turn: Turn,
    log: EventLog,
    emitter: EventEmitter,
    state: _ReactBridgeState,
    evt: dict[str, Any],
) -> None:
    runtime._record_react_trace_event(turn, evt)
    kind = evt.get("type")
    if kind == "react_started":
        task_id = str(evt.get("task_id") or "").strip() or None
        if task_id:
            turn.task_id = task_id
            # ReAct task identity is the durable objective coordinate.  A
            # fresh turn starts with its own objective id; a resume keeps the
            # original ReAct id across UI turns.
            turn.objective_id = task_id
            logged_update = log.turn_updated(
                turn.thread_id,
                turn.id,
                objective_id=turn.objective_id,
                task_id=task_id,
            )
            del logged_update
        return
    if kind == "text_delta":
        await state.append_agent_message(turn, log, emitter, evt.get("delta", ""))
        return
    if kind == "commentary_delta":
        # Generic runtime fallback prose remains private: it made every
        # provider sound identical and often duplicated the final synthesis.
        # A completed, explicitly marked evidence receipt is different: it is
        # grounded in a real tool result and must remain visible between
        # ordered batches so the timeline does not collapse into tool rows.
        if (
            evt.get("progress_source") == "runtime"
            and not evt.get("public_evidence")
            and not evt.get("public_status")
        ):
            return
        await state.append_commentary(
            turn,
            log,
            emitter,
            evt.get("delta", ""),
            # Complete checkpoints default to a new conversational beat. A
            # provider may explicitly continue the current beat so token
            # chunks grow one message/avatar instead of producing a log row.
            start_new_segment=bool(evt.get("start_new_segment", True)),
        )
        return
    if kind == "thinking_delta":
        # Provider thinking tokens were previously treated as private
        # chain-of-thought and dropped at this boundary. The streaming
        # UX (foldable reasoning rows + live thinking typewriter) needs
        # them surfaced as a ReasoningItem, and the frontend already
        # renders reasoning items folded-by-default with a live
        # typewriter while the turn streams. The bridge state fully
        # supports reasoning items (append_reasoning / item/reasoning
        # textDelta), so route the deltas through instead of dropping.
        delta = evt.get("delta")
        if delta:
            await state.append_reasoning(turn, log, emitter, str(delta))
        return
    if kind == "tool_call_delta":
        await state.append_tool_call_delta(turn, log, emitter, evt)
        return
    if kind == "tool_start":
        await state.start_tool(turn, log, emitter, evt)
        return
    if kind == "tool_output_delta":
        await state.append_tool_output(turn, log, emitter, evt)
        return
    if kind == "tool_background":
        await state.track_background_tool(turn, log, emitter, evt)
        return
    if kind == "tool_end":
        await state.complete_tool(turn, log, emitter, evt)
        return
    if kind == "react_cancelled":
        # Producer already decided the loop is done. Flush any open
        # prose and mark the turn as interrupted so the gateway's
        # turn/completed wrapper preserves that status.
        await state.flush(
            turn,
            log,
            emitter,
            status=ItemStatus.INTERRUPTED,
        )
        # The event adapter itself represents a clean turn boundary stop.
        # Drivers that had to kill in-flight work may already have promoted
        # the turn to CANCELLED; direct/boundary cancellation remains the
        # resumable INTERRUPTED protocol state.
        if turn.status != TurnStatus.CANCELLED:
            turn.status = TurnStatus.INTERRUPTED
        turn.outcome_reason = str(evt.get("reason") or "user_cancelled")
        if not turn.interrupt_reason:
            turn.interrupt_reason = "任务被取消"
        log.turn_updated(
            turn.thread_id,
            turn.id,
            objective_id=turn.objective_id,
            task_id=turn.task_id,
            outcome_reason=turn.outcome_reason,
        )
        return
    if kind == "throughput":
        # Piggyback on thread/tokenUsage/updated — the frontend
        # reducer already routes this to a free-form ``tokenUsage``
        # record, so we can ship any shape without a schema bump.
        usage = evt.get("usage")
        if isinstance(usage, str) and usage.strip():
            import json

            try:
                parsed = json.loads(usage)
                token_usage = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                token_usage = {}
        elif isinstance(usage, dict):
            token_usage = usage
        else:
            token_usage = {
                "chars": evt.get("chars", 0),
                "elapsedMs": evt.get("elapsed_ms", 0),
                "charsPerSec": evt.get("chars_per_sec", 0.0),
            }
        await emitter.notify(
            ServerMethod.THREAD_TOKEN_USAGE_UPDATED,
            {
                "threadId": turn.thread_id,
                "tokenUsage": token_usage,
            },
        )
        return
    if kind == "codebase_grounding":
        # The loop folded these project docs/chunks into the prompt this turn.
        # Forward them so the frontend can show a plain-language grounding chip
        # on the AI reply. Best-effort UX — never a turn-breaking contract.
        sources = evt.get("sources")
        if isinstance(sources, list) and sources:
            validated_sources: list[GroundingSource] = []
            for source in sources:
                if not isinstance(source, dict):
                    continue
                with contextlib.suppress(TypeError, ValueError):
                    validated_sources.append(GroundingSource.model_validate(source))
            if not validated_sources:
                return
            turn.grounding = validated_sources
            sources_payload = [source.model_dump(mode="json") for source in validated_sources]
            logged_update = log.turn_updated(
                turn.thread_id,
                turn.id,
                grounding=sources_payload,
            )
            await emitter.notify(
                ServerMethod.TURN_GROUNDING,
                {
                    "threadId": turn.thread_id,
                    "turnId": turn.id,
                    "sources": sources_payload,
                    **({"eventId": logged_update.event_id} if logged_update is not None else {}),
                },
            )
            await state.update_grounding_evidence(turn, log, emitter, validated_sources)
        return
    if kind == "visibility":
        # Turn-assembly decision trace (capability routing / delegation
        # visibility / skill catalog) exported by react_loop. Snapshot-only
        # item: started + completed back-to-back, no incremental deltas.
        steps = evt.get("steps")
        if not isinstance(steps, list):
            steps = []
        item = VisibilityItem(
            summary=str(evt.get("summary") or "本轮能力路由 / 委派 / 技能目录决策"),
            steps=steps,
            status=ItemStatus.COMPLETED,
        )
        state._bind_timeline(item)
        turn.items.append(item)
        started_event = log.item_started(turn.thread_id, turn.id, item)
        await emitter.notify(
            ServerMethod.ITEM_STARTED,
            {
                "threadId": turn.thread_id,
                "turnId": turn.id,
                "item": item.model_dump(by_alias=True, mode="json"),
                "eventId": started_event.event_id,
            },
        )
        completed_event = log.item_completed(turn.thread_id, turn.id, item)
        await emitter.notify(
            ServerMethod.ITEM_COMPLETED,
            {
                "threadId": turn.thread_id,
                "turnId": turn.id,
                "item": item.model_dump(by_alias=True, mode="json"),
                "eventId": completed_event.event_id,
            },
        )
        return
    if kind == "react_step_complete":
        await state.flush(turn, log, emitter)
        return
    if kind == "react_completed":
        decision = evt.get("completion_decision")
        decision = decision if isinstance(decision, dict) else None
        if decision is not None:
            turn.completion_decision = dict(decision)
        success = (
            bool(decision.get("success"))
            if decision is not None and "success" in decision
            else evt.get("success") is not False
        )
        # A paused/cancelled turn is already INTERRUPTED (resumable via
        # Continue). The loop's trailing react_completed carries
        # success=False for a pause, which must NOT downgrade the resumable
        # pause into a hard failure — otherwise the sidebar shows "failed"
        # next to a "当前进度已暂停并保存" message the user can resume.
        if not success and turn.status in {
            TurnStatus.INTERRUPTED,
            TurnStatus.PAUSED,
            TurnStatus.CANCELLED,
        }:
            await state.flush(
                turn,
                log,
                emitter,
                status=ItemStatus.INTERRUPTED,
            )
            return
        await state.flush(
            turn,
            log,
            emitter,
            status=ItemStatus.COMPLETED if success else ItemStatus.FAILED,
        )
        if not success:
            turn.status = TurnStatus.FAILED
            reason = str(evt.get("terminated_reason") or "react_failed")
            disposition = str((decision or {}).get("outcome") or evt.get("disposition") or "failed")
            failure = evt.get("failure")
            failure = failure if isinstance(failure, dict) else None
            receipt = evt.get("completion_receipt")
            # Structured environmental failure → readable, specific outcome
            # reason. Without this the UI can only echo the raw stderr (e.g. a
            # pnpm no-TTY abort inside a husky hook) and the user is left with
            # a red turn and no explanation.
            if failure and failure.get("readable"):
                reason = str(failure["readable"])
                message = str(failure["readable"])
                code = str(failure.get("code") or reason)
            else:
                message = (
                    str(receipt.get("message") or receipt.get("summary") or reason)
                    if isinstance(receipt, dict)
                    else reason
                )
                code = reason
            turn.outcome_reason = reason
            turn.error = {
                "message": message,
                "code": code,
                "disposition": disposition,
                "failure_kind": str(failure.get("kind") or "") if failure else "",
                "details": receipt if isinstance(receipt, dict) else None,
                "completion_decision": decision,
            }
            log.turn_updated(
                turn.thread_id,
                turn.id,
                objective_id=turn.objective_id,
                task_id=turn.task_id,
                outcome_reason=reason,
            )
        return
    if kind == "react_paused":
        await state.flush(
            turn,
            log,
            emitter,
            status=ItemStatus.INTERRUPTED,
        )
        turn.status = TurnStatus.PAUSED
        turn.task_id = str(evt.get("task_id") or turn.task_id or "").strip() or None
        turn.objective_id = turn.task_id or turn.objective_id
        try:
            checkpoint_id = int(evt.get("checkpoint_id") or 0)
        except (TypeError, ValueError):
            checkpoint_id = 0
        if checkpoint_id <= 0 and turn.task_id and runtime._trace_store is not None:
            with contextlib.suppress(Exception):
                checkpoint = runtime._trace_store.latest_checkpoint(
                    task_id=turn.task_id,
                    checkpoint_type="react",
                )
                if isinstance(checkpoint, dict):
                    checkpoint_id = int(checkpoint.get("id") or 0)
        turn.checkpoint_id = checkpoint_id if checkpoint_id > 0 else None
        turn.outcome_reason = str(evt.get("reason") or "system_paused")
        if not turn.interrupt_reason:
            turn.interrupt_reason = str(evt.get("note") or "任务已暂停，进度和检查点已保存")
        log.turn_updated(
            turn.thread_id,
            turn.id,
            objective_id=turn.objective_id,
            task_id=turn.task_id,
            checkpoint_id=turn.checkpoint_id,
            outcome_reason=turn.outcome_reason,
        )
        return
    if kind == "react_resumed":
        await emitter.notify(
            ServerMethod.THREAD_STATUS_CHANGED,
            {
                "threadId": turn.thread_id,
                "status": {
                    "type": "resumed",
                    "taskId": evt.get("task_id"),
                    "checkpointIteration": evt.get("checkpoint_iteration"),
                    "resumeFromIteration": evt.get("resume_from_iteration"),
                    "restoredStepCount": evt.get("restored_step_count"),
                    "hasFinalAnswer": evt.get("has_final_answer"),
                    "currentPhase": evt.get("current_phase"),
                },
            },
        )
        return
    if kind in ("react_error",):
        await state.flush(
            turn,
            log,
            emitter,
            status=ItemStatus.FAILED,
        )
        err = ErrorItem(
            message=str(evt.get("message") or evt.get("kind") or "react error"),
            will_retry=False,
            error_info={
                "code": str(evt.get("kind") or "react_error"),
                "terminal_stage": evt.get("terminal_stage"),
                "task_id": evt.get("task_id") or turn.task_id,
            },
        )
        turn.status = TurnStatus.FAILED
        turn.outcome_reason = str(evt.get("kind") or "react_error")
        turn.error = {
            "message": err.message,
            "code": turn.outcome_reason,
            "details": err.error_info,
        }
        turn.items.append(err)
        log.item_started(turn.thread_id, turn.id, err)
        await emitter.notify(
            ServerMethod.ITEM_STARTED,
            {
                "threadId": turn.thread_id,
                "turnId": turn.id,
                "item": err.model_dump(by_alias=True, mode="json"),
            },
        )
        log.item_completed(turn.thread_id, turn.id, err)
        await emitter.notify(
            ServerMethod.ITEM_COMPLETED,
            {
                "threadId": turn.thread_id,
                "turnId": turn.id,
                "item": err.model_dump(by_alias=True, mode="json"),
            },
        )
