"""Bridge a ``ParallelAgentOrchestrator`` batch stream onto a realtime turn.

Extracted as the orchestrator counterpart of ``_team_stream_topology``'s
sub-agent lifecycle bridge. When the ReAct loop's auto-parallel short-circuit
(``agent_auto_parallel.run_auto_parallel``) dispatches a batch, the driver
learns the ``batch_id`` via ``on_batch_started`` and starts this coroutine.
It subscribes to ``orchestrator.subscribe(batch_id)`` and translates each
``task_update`` ``BatchStreamEvent`` into a ``SubagentItem`` on the turn —
appending + emitting ``item/started`` when the task spawns and ``item/completed``
when it reaches a terminal state — so the frontend workbench renders the
parallel sub-tasks as live agent tiles instead of a blank gap until the batch
finishes.

Runs on the same asyncio loop as the react driver's consumer, so it mutates
``turn.items`` without racing the driver.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from runtime.execution.parallel_agents.orchestrator import ParallelAgentOrchestrator
from runtime.memory.threads.event_log import EventLog
from runtime.protocol import (
    ItemStatus,
    ServerMethod,
    SubagentItem,
)
from runtime.sensing.gateway.realtime_gateway import EventEmitter

if TYPE_CHECKING:
    from runtime.protocol import Turn

_TERMINAL_FAILED = {"failed", "timed_out", "cancelled"}


async def bridge_orchestrator_batch(
    orchestrator: ParallelAgentOrchestrator,
    batch_id: str,
    turn: Turn,
    log: EventLog,
    emitter: EventEmitter,
) -> None:
    """Stream ``batch_id``'s tasks onto ``turn`` as live ``SubagentItem`` tiles.

    Self-terminating: returns once the orchestrator emits ``batch_complete``
    (or the batch disappears). Subscribers are keyed by ``task_id``; each task
    is rendered from its first ``task_update`` (spawn) through its terminal
    state (finish).
    """
    subagent_items: dict[str, SubagentItem] = {}
    seq = 0

    async def _emit_started(item: SubagentItem) -> None:
        log.item_started(turn.thread_id, turn.id, item)
        with contextlib.suppress(Exception):
            await emitter.notify(
                ServerMethod.ITEM_STARTED,
                {
                    "threadId": turn.thread_id,
                    "turnId": turn.id,
                    "item": item.model_dump(by_alias=True, mode="json"),
                },
            )

    async def _emit_completed(item: SubagentItem) -> None:
        log.item_completed(turn.thread_id, turn.id, item)
        with contextlib.suppress(Exception):
            await emitter.notify(
                ServerMethod.ITEM_COMPLETED,
                {
                    "threadId": turn.thread_id,
                    "turnId": turn.id,
                    "item": item.model_dump(by_alias=True, mode="json"),
                },
            )

    try:
        async for ev in orchestrator.subscribe(batch_id, after_sequence=0):
            try:
                etype = str(ev.type or "")
            except AttributeError:  # pragma: no cover - defensive
                continue
            if etype == "task_update":
                task_id = str(ev.task_id or "") or f"task_{seq}"
                existing = subagent_items.get(task_id)
                status = str(ev.status or "") or "pending"
                terminal = status in _TERMINAL_FAILED or status == "completed"
                error = str(ev.error) if ev.error else None
                summary_preview = str(ev.result_preview or ev.message or "") or None
                subagent_name = str(ev.subagent_name or "") or task_id

                if existing is None:
                    seq += 1
                    safe_id = task_id if task_id else f"task_{seq}"
                    item = SubagentItem(
                        id=f"sub_{safe_id[:80]}",
                        subagent_id=task_id,
                        role=subagent_name,
                        name=subagent_name,
                        codename=subagent_name,
                        status=ItemStatus.COMPLETED if terminal else ItemStatus.IN_PROGRESS,
                        summary=summary_preview,
                        error=error if terminal else None,
                    )
                    subagent_items[task_id] = item
                    turn.items.append(item)
                    if terminal:
                        await _emit_completed(item)
                    else:
                        await _emit_started(item)
                else:
                    updated = existing.model_copy(
                        update={
                            "status": (
                                ItemStatus.COMPLETED
                                if status == "completed"
                                else ItemStatus.FAILED
                                if terminal
                                else ItemStatus.IN_PROGRESS
                            ),
                            "summary": summary_preview or existing.summary,
                            "error": error if terminal else existing.error,
                        }
                    )
                    subagent_items[task_id] = updated
                    turn.items = [updated if item.id == updated.id else item for item in turn.items]
                    if terminal:
                        await _emit_completed(updated)
                    elif updated.summary and updated.summary != existing.summary:
                        # Live progress: re-broadcast item/started so the
                        # frontend workbench streams the running summary on the
                        # sub-agent tile instead of waiting for the terminal
                        # item/completed snapshot. The reducer treats a second
                        # item/started for an in-progress item as a replace.
                        await _emit_started(updated)
            elif etype == "batch_complete":
                break
    except Exception:  # noqa: BLE001 - bridge is best-effort, never breaks the turn
        return
