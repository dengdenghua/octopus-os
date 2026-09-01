"""React-event → ``item/*`` bridge state for the realtime runtime.

Split out of ``realtime_cerebrum.py``: ``_ReactBridgeState`` tracks the
currently-open agentMessage / reasoning / tool items for a turn,
coalesces streaming deltas, watches background commands, and promotes
tool results to first-class ``FileChangeItem`` / ``VerificationItem``
records (the ``*_from_tool_evt`` builders live in
``_event_bridge_tool_items``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from runtime.memory.threads.event_log import EventLog
from runtime.protocol import (
    AgentMessageItem,
    AgentPhaseSnapshot,
    CommandExecutionItem,
    EvidenceReference,
    FileChangeItem,
    GroundingSource,
    ItemStatus,
    ReasoningItem,
    ServerMethod,
    ToolEffectSignal,
    Turn,
    TurnStatus,
    WorkspaceFocus,
)
from runtime.protocol.text_limits import (  # noqa: F401 — re-exported for tests/test_output_cap.py
    MAX_AGGREGATED_OUTPUT as _MAX_AGGREGATED_OUTPUT,
)
from runtime.protocol.text_limits import (  # noqa: F401
    MAX_STREAM_ITEM_CONTENT as _MAX_STREAM_ITEM_CONTENT,
)
from runtime.protocol.text_limits import (  # noqa: F401
    OUTPUT_TRUNCATION_MARK as _OUTPUT_TRUNCATION_MARK,
)
from runtime.protocol.text_limits import (  # noqa: F401
    STREAM_CONTENT_TRUNCATION_MARK as _STREAM_CONTENT_TRUNCATION_MARK,
)
from runtime.sensing.gateway._event_bridge_tool_items import (
    _append_capped_output,
    _append_capped_stream_content,
    _file_change_item_from_tool_evt,
    _tool_done_public_narrative,
    _tool_start_public_narrative,
    _verification_item_from_tool_evt,
)
from runtime.sensing.gateway.adaptive_delta_buffer import AdaptiveFlushPolicy
from runtime.sensing.gateway.realtime_gateway import EventEmitter
from runtime.sensing.gateway.realtime_workbench import (
    _grounding_evidence,
    _phases_from_plan_md,
    _phases_from_todo_preview,
    _phases_with_active_item,
    _terminal_workbench_phases,
    _tool_evidence,
    _workbench_snapshot,
    _workspace_focus_for_file_change,
    _workspace_focus_for_tool,
)
from runtime.sensing.gateway.tool_bridge import strip_leaked_protocol_tags

_logger = logging.getLogger(__name__)


def _safe_list_remove(bucket: list[Any], item: Any) -> None:
    """Remove ``item`` from ``bucket`` if present. Tolerant of races
    (the bucket may have been swept out from under us by a concurrent
    reap)."""
    with contextlib.suppress(ValueError):
        bucket.remove(item)


async def _guarded_background_write(
    emitter: Any,
    thread_id: str,
    operation: Callable[[], Awaitable[None]],
) -> None:
    """Run one late log projection under the emitter's canonical guard.

    Guard acquisition/probe failures intentionally escape so the owning
    watcher stops forever. Transport or append failures retain the legacy
    best-effort behavior and are suppressed only after authority is held.
    """

    async def _run() -> None:
        with contextlib.suppress(ConnectionError, OSError, RuntimeError, TypeError):
            await operation()

    guard = getattr(emitter, "background_write_guard", None)
    if callable(guard):
        async with guard(thread_id):
            await _run()
        return
    await _run()


# ── Bridge state — open agentMessage / reasoning / tool items ─


# Re-emit ITEM_STARTED for a live tool-call preview at most once per
# this many accumulated argument chars (dsh ``tool-call-delta`` lane).
# Fragments are token-sized, so emitting on every one would hammer the
# socket; the first fragment always goes out immediately (TTFT).
_TOOL_CALL_DELTA_EMIT_STRIDE = 64

# SUNSET (was "MIGRATION sunset target: v0.3"): ``turn/plan/updated`` used
# to embed the full ``workbenchSnapshot`` alongside the dedicated
# ``workbench/snapshot`` notification, doubling snapshot bandwidth on every
# plan update. The frontend reducer applies the embedded copy only when the
# field is present (presence-checked) and has natively consumed
# ``workbench/snapshot`` since the v2 protocol — so the legacy field now
# defaults OFF. Set ECHO_LEGACY_PLAN_SNAPSHOT=1 to re-enable it for
# out-of-tree clients that still read the embedded copy.
_LEGACY_PLAN_SNAPSHOT = os.environ.get("ECHO_LEGACY_PLAN_SNAPSHOT", "0").lower() in (
    "1",
    "true",
    "yes",
    "on",
)


class _ReactBridgeState:
    """Tracks which items are currently open per (turn, kind).

    The react loop streams ``text_delta`` / ``thinking_delta`` chunks
    that should land on a single ongoing item. ``tool_start``/``tool_end``
    bind by ``tool_call_id``. ``flush`` finalizes any open prose items
    so subsequent steps start fresh.
    """

    # Streaming text/reasoning chunks arrive per-token from the LLM.
    # One WS frame + one journal write per token is pure overhead —
    # the frontend coalesces per animation frame anyway. Buffer and
    # flush adaptively based on throughput: ~64 chars or 32ms for normal
    # speed, up to 256 chars or 100ms for high throughput bursts.
    # The FIRST chunk of each item is never buffered (time-to-first-token),
    # and any kind switch / item finalization drains the buffer, so
    # ordering and final content are byte-identical to unbuffered.

    # Fallback constants (used if adaptive batching is disabled)
    _DELTA_FLUSH_INTERVAL_S = 0.032
    _DELTA_FLUSH_MAX_CHARS = 64

    def __init__(
        self,
        on_background_task_start: Callable[[asyncio.Task[None]], None] | None = None,
        timeline_binder: Callable[[Any, str | None], None] | None = None,
        *,
        agent_display_name: str | None = None,
        agent_avatar_url: str | None = None,
        agent_icon: str | None = None,
        enable_adaptive_batching: bool = True,
    ) -> None:
        self._agent_display_name = agent_display_name
        self._agent_avatar_url = agent_avatar_url
        self._agent_icon = agent_icon
        # Pure flush-decision policy: this class owns NO content. The single
        # content buffer below is the only place deltas are stored, so there
        # is exactly one bookkeeping (no double append / dead get_content).
        self._flush_policy = AdaptiveFlushPolicy() if enable_adaptive_batching else None
        self.agent_message: AgentMessageItem | None = None
        self.commentary_message: AgentMessageItem | None = None
        self.last_public_commentary_key: str | None = None
        # A long tool-backed turn gets one explicit public handoff before its
        # final answer.  Provider reasoning remains a ReasoningItem; this flag
        # prevents token chunks from creating duplicate handoff messages.
        self.final_answer_handoff_emitted = False
        self.progress_sequence = 0
        self.timeline_sequence = 0
        self.last_timeline_item_id: str | None = None
        self.current_phase_id: str | None = None
        self.reasoning: ReasoningItem | None = None
        # monotonic start timestamp for the currently-open reasoning item;
        # used to compute duration_ms on _emit_completed. None when no
        # reasoning item is open or for legacy streams without this field.
        self.reasoning_started_monotonic: float | None = None
        self.tools: dict[str, CommandExecutionItem] = {}
        # Provider call ids are not guaranteed to be present or unique. Keep
        # an ordered alias queue so two calls with the same (or empty) id are
        # completed in start order instead of overwriting/orphaning one item.
        self.tool_call_queues: dict[str, list[str]] = {}
        self.tool_public_narrative_started: dict[str, bool] = {}
        # dsh ``tool-call-delta`` lane: raw argument fragments arrive
        # while the model is still assembling a call, BEFORE the bridge
        # emits tool_start (which only happens once the round's calls
        # complete). Buffer per provider call id, then merge into the
        # item's input_preview on start_tool for a live "arguments
        # filling in" preview. Preview only — never executed.
        self._tool_call_delta_buffers: dict[str, dict[str, str]] = {}
        # Last argument length already pushed into an open item's
        # input_preview, per call id — throttles ITEM_STARTED re-emits
        # so token-sized fragments don't hammer the socket.
        self._tool_call_delta_emitted: dict[str, int] = {}
        self.phases: list[AgentPhaseSnapshot] = []
        self.evidence: list[EvidenceReference] = []
        self.workbench_snapshot_version = 0
        self.background_tasks: list[asyncio.Task[None]] = []
        self._delta_buf: list[str] = []
        # Keep the threshold check O(1). Streaming providers commonly emit
        # one- or two-character chunks, so repeatedly summing the whole list
        # becomes quadratic between flushes.
        self._delta_buf_chars = 0
        self._delta_kind: str | None = None
        self._delta_ctx: tuple[Turn, EventLog, EventEmitter] | None = None
        self._delta_flush_task: asyncio.Task[None] | None = None
        # Serializes buffer drain between the consumer coroutine and
        # the delayed-flush task so emitted chunks never interleave
        # out of order on the socket.
        self._delta_lock = asyncio.Lock()
        # Optional sink for cross-turn ownership of background watchers.
        # When set, each task created by ``track_background_tool`` is
        # also pushed through this callback so the runtime can sweep
        # leftovers when the user starts a brand-new turn on the same
        # thread (the watcher lives on by design — see
        # ``test_background_tool_item_completes_after_turn_response`` —
        # but we don't want it bleeding into the NEXT conversation).
        self._on_background_task_start = on_background_task_start
        self._timeline_binder = timeline_binder

    def _new_agent_message(
        self,
        *,
        text: str,
        message_kind: str = "answer",
        **kwargs: Any,
    ) -> AgentMessageItem:
        """Create a transcript item carrying the owning agent's identity.

        ReAct text and commentary items are created before the frontend has a
        chance to infer identity from a final answer. Persisting it here keeps
        the avatar anchored even when a tool-call message is split into a
        hidden process group plus a visible final answer.
        """
        return AgentMessageItem(
            text=text,
            message_kind=message_kind,
            agent_display_name=self._agent_display_name,
            agent_avatar_url=self._agent_avatar_url,
            agent_icon=self._agent_icon,
            **kwargs,
        )

    @staticmethod
    def prose_status_for_turn(turn_status: TurnStatus) -> ItemStatus:
        """Map the authoritative turn outcome onto any still-open prose."""

        if turn_status in {
            TurnStatus.INTERRUPTED,
            TurnStatus.PAUSED,
            TurnStatus.CANCELLED,
        }:
            return ItemStatus.INTERRUPTED
        if turn_status == TurnStatus.FAILED:
            return ItemStatus.FAILED
        return ItemStatus.COMPLETED

    # ── Lifecycle helpers ──────────────────────────────────────────────
    # Every method in this class emits item lifecycle events as a pair:
    # journal write + WS notify. Inlining the 5-line ``notify`` payload
    # at every site obscured the actual logic and made the ServerMethod
    # name a search-and-replace hazard. These helpers centralize the
    # boilerplate; behaviour is byte-identical to the previous inline form.
    @staticmethod
    def _item_payload(turn: Turn, item: Any) -> dict[str, Any]:
        return {
            "threadId": turn.thread_id,
            "turnId": turn.id,
            "item": item.model_dump(by_alias=True, mode="json"),
        }

    def _bind_timeline(self, item: Any, *, phase_id: str | None = None) -> None:
        """Assign stable causal coordinates before an item is published.

        Transport arrival order is not durable: reconnect replay, browser
        batching and background completions can all deliver snapshots on a
        different schedule. A monotonic per-turn coordinate plus an explicit
        parent lets every client reconstruct the same conversational rhythm.
        """
        if self._timeline_binder is not None:
            self._timeline_binder(item, phase_id or self.current_phase_id)
            self.last_timeline_item_id = item.id
            return
        if getattr(item, "timeline_sequence", None) is None:
            self.timeline_sequence += 1
            item.timeline_sequence = self.timeline_sequence
        if getattr(item, "parent_item_id", None) is None:
            item.parent_item_id = self.last_timeline_item_id
        if getattr(item, "phase_id", None) is None:
            item.phase_id = phase_id or self.current_phase_id
        self.last_timeline_item_id = item.id

    async def _emit_started(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        item: Any,
        *,
        durable: bool = False,
    ) -> None:
        if durable:
            log.item_started(
                turn.thread_id,
                turn.id,
                item,
                durable=True,
            )
        else:
            # Preserve the long-standing three-argument protocol for
            # lightweight EventLog-compatible sinks used by other drivers.
            log.item_started(turn.thread_id, turn.id, item)
        await emitter.notify(
            ServerMethod.ITEM_STARTED,
            self._item_payload(turn, item),
        )

    async def _emit_completed(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        item: Any,
    ) -> None:
        log.item_completed(turn.thread_id, turn.id, item)
        await emitter.notify(
            ServerMethod.ITEM_COMPLETED,
            self._item_payload(turn, item),
        )

    async def append_agent_message(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        delta: str,
    ) -> None:
        if not delta:
            return
        # Strip structural protocol tags (``<ReasoningBlock>`` etc.) that
        # leaked into the literal text stream — they belong in structured
        # reasoning / tool_use fields, not chat prose. Mirrors the
        # checkpoint path's ``_PUBLIC_CHECKPOINT_PROTOCOL_RE`` detection
        # so the frontend ``INTERNAL_PROCESS_BLOCK_RE`` fallback stops
        # being the only line of defense. See ``strip_leaked_protocol_tags``.
        delta = strip_leaked_protocol_tags(delta)
        if not delta:
            return
        if self.commentary_message is not None:
            await self._flush_pending_delta()
            self.commentary_message.status = ItemStatus.COMPLETED
            await self._emit_completed(turn, log, emitter, self.commentary_message)
            self.commentary_message = None
        first = self.agent_message is None
        if first:
            self.agent_message = self._new_agent_message(text="")
            self._bind_timeline(self.agent_message)
            turn.items.append(self.agent_message)
            await self._emit_started(turn, log, emitter, self.agent_message)
        agent_message = self.agent_message
        assert agent_message is not None
        agent_message.text = _append_capped_stream_content(
            agent_message.text,
            delta,
        )
        await self._buffer_delta(
            turn,
            log,
            emitter,
            "agentMessage",
            delta,
            flush_now=first,
        )

    async def append_reasoning(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        delta: str,
    ) -> None:
        if not delta:
            return
        first = self.reasoning is None
        if first:
            self.reasoning = ReasoningItem(content="")
            self._bind_timeline(self.reasoning)
            turn.items.append(self.reasoning)
            self.reasoning_started_monotonic = time.monotonic()
            await self._emit_started(turn, log, emitter, self.reasoning)
        reasoning = self.reasoning
        assert reasoning is not None
        reasoning.content = _append_capped_stream_content(
            reasoning.content,
            delta,
        )
        await self._buffer_delta(
            turn,
            log,
            emitter,
            "reasoning",
            delta,
            flush_now=first,
        )

    async def append_commentary(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        delta: str,
        *,
        start_new_segment: bool = False,
    ) -> None:
        if not delta:
            return
        if start_new_segment:
            commentary_key = " ".join(delta.split()).casefold()
            if commentary_key == self.last_public_commentary_key:
                return
            self.last_public_commentary_key = commentary_key
        # Public-checkpoint boundaries are structural. Never inspect prose or
        # a hard-coded "investigate / implement / verify" label to decide
        # whether two messages belong together.
        if self.commentary_message is not None and start_new_segment:
            await self._flush_pending_delta()
            self.commentary_message.status = ItemStatus.COMPLETED
            await self._emit_completed(turn, log, emitter, self.commentary_message)
            self.commentary_message = None
        first = self.commentary_message is None
        if first:
            self.progress_sequence += 1
            phase_id = f"{turn.id}:progress:{self.progress_sequence}"
            self.current_phase_id = phase_id
            self.commentary_message = self._new_agent_message(
                text="",
                message_kind="commentary",
                phase_id=phase_id,
                progress_sequence=self.progress_sequence,
            )
            self._bind_timeline(self.commentary_message, phase_id=phase_id)
            turn.items.append(self.commentary_message)
            await self._emit_started(turn, log, emitter, self.commentary_message)
        commentary_message = self.commentary_message
        assert commentary_message is not None
        commentary_message.text = _append_capped_stream_content(
            commentary_message.text,
            delta,
        )
        await self._buffer_delta(
            turn,
            log,
            emitter,
            "commentary",
            delta,
            flush_now=first,
        )

    # ── Delta coalescing ────────────────────────────────────────────

    async def _buffer_delta(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        kind: str,
        delta: str,
        *,
        flush_now: bool,
    ) -> None:
        if self._delta_buf and self._delta_kind != kind:
            # Prose kind switched (reasoning ↔ message): drain the old
            # kind first so chunks never reorder across items.
            await self._flush_pending_delta()
        self._delta_kind = kind
        self._delta_ctx = (turn, log, emitter)
        self._delta_buf.append(delta)
        self._delta_buf_chars += len(delta)

        # 自适应批处理：策略只做"是否刷新"的决策（按吞吐量动态调整
        # 字符/时间阈值），内容仍只存于上面唯一的 _delta_buf。
        if self._flush_policy is not None:
            self._flush_policy.record(len(delta))
            should_flush = flush_now or self._flush_policy.should_flush()
        else:
            # 回退到固定批处理
            should_flush = flush_now or self._delta_buf_chars >= self._DELTA_FLUSH_MAX_CHARS

        if should_flush:
            await self._flush_pending_delta()
            return
        if self._delta_flush_task is None or self._delta_flush_task.done():
            # Deadline flush: without it, an LLM stall mid-stream
            # would leave the buffered tail invisible until the next
            # chunk arrives (which may be seconds away).
            self._delta_flush_task = asyncio.create_task(self._delayed_delta_flush())

    async def _delayed_delta_flush(self) -> None:
        # Deadline flush: without it, an LLM stall mid-stream would leave the
        # buffered tail invisible until the next chunk arrives. The interval
        # follows the policy's current throughput tier (16/32/64ms) instead
        # of a fixed 32ms.
        interval = (
            self._flush_policy.flush_interval_s()
            if self._flush_policy is not None
            else self._DELTA_FLUSH_INTERVAL_S
        )
        await asyncio.sleep(interval)
        await self._flush_pending_delta()

    async def _flush_pending_delta(self) -> None:
        async with self._delta_lock:
            task = self._delta_flush_task
            if task is not None and task is not asyncio.current_task():
                task.cancel()
            self._delta_flush_task = None
            if not self._delta_buf or self._delta_ctx is None:
                return
            combined = "".join(self._delta_buf)
            self._delta_buf.clear()
            self._delta_buf_chars = 0

            # 通知策略：本窗口已刷新（采样吞吐量并重置窗口）
            if self._flush_policy is not None:
                self._flush_policy.mark_flushed()

            kind = self._delta_kind
            turn, log, emitter = self._delta_ctx
            if kind == "agentMessage" and self.agent_message is not None:
                item_id = self.agent_message.id
                logged = log.item_delta(turn.thread_id, turn.id, item_id, "agentMessage", combined)
                event_id = getattr(logged, "event_id", None)
                await emitter.notify(
                    ServerMethod.ITEM_AGENT_MESSAGE_DELTA,
                    {
                        "threadId": turn.thread_id,
                        "turnId": turn.id,
                        "itemId": item_id,
                        "delta": combined,
                        "eventId": event_id,
                    },
                )
            elif kind == "commentary" and self.commentary_message is not None:
                item_id = self.commentary_message.id
                logged = log.item_delta(turn.thread_id, turn.id, item_id, "agentMessage", combined)
                event_id = getattr(logged, "event_id", None)
                await emitter.notify(
                    ServerMethod.ITEM_AGENT_MESSAGE_DELTA,
                    {
                        "threadId": turn.thread_id,
                        "turnId": turn.id,
                        "itemId": item_id,
                        "delta": combined,
                        "eventId": event_id,
                    },
                )
            elif kind == "reasoning" and self.reasoning is not None:
                item_id = self.reasoning.id
                logged = log.item_delta(turn.thread_id, turn.id, item_id, "reasoning", combined)
                event_id = getattr(logged, "event_id", None)
                await emitter.notify(
                    ServerMethod.ITEM_REASONING_TEXT_DELTA,
                    {
                        "threadId": turn.thread_id,
                        "turnId": turn.id,
                        "itemId": item_id,
                        "delta": combined,
                        "contentIndex": 0,
                        "eventId": event_id,
                    },
                )
            # else: the item was already finalized — drop the tail; the
            # item/completed snapshot carries the full text regardless.

    async def append_tool_call_delta(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        evt: dict[str, Any],
    ) -> None:
        """Merge one raw tool-call fragment into the live preview.

        dsh ``tool-call-delta`` lane: fragments arrive while the model
        is still assembling the call, BEFORE ``tool_start`` (the bridge
        emits start only once a round's calls complete). They buffer
        per provider call id; once the item exists, the accumulated
        arguments patch ``input_preview`` with a throttled
        ``ITEM_STARTED`` re-emit so the UI shows the call filling in.
        Nothing on this lane is ever executed — the completed call
        drives the real ``start_tool``.
        """
        call_id = str(evt.get("tool_call_id") or evt.get("id") or "")
        if not call_id:
            return
        buf = self._tool_call_delta_buffers.setdefault(
            call_id,
            {"name": "", "arguments": ""},
        )
        name = str(evt.get("tool_name") or evt.get("name") or "")
        if name:
            buf["name"] = name
        args_delta = str(evt.get("argumentsDelta") or "")
        if args_delta:
            buf["arguments"] += args_delta
        if not buf["name"] and not buf["arguments"]:
            return
        call_key = self._resolve_open_tool_key(call_id)
        item = self.tools.get(call_key) if call_key is not None else None
        if item is None:
            # Still assembling — ``start_tool`` merges the buffer later.
            return
        total = len(buf["arguments"])
        emitted = self._tool_call_delta_emitted.get(call_id, 0)
        if emitted > 0 and total - emitted < _TOOL_CALL_DELTA_EMIT_STRIDE:
            return
        preview = item.input_preview if isinstance(item.input_preview, dict) else {}
        item.input_preview = {
            **preview,
            "name": buf["name"],
            "arguments": buf["arguments"],
            "streaming": True,
        }
        self._tool_call_delta_emitted[call_id] = total
        await emitter.notify(
            ServerMethod.ITEM_STARTED,
            self._item_payload(turn, item),
        )

    async def start_tool(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        evt: dict[str, Any],
    ) -> None:
        has_open_public_prose = bool(
            self.commentary_message is not None and str(self.commentary_message.text or "").strip()
        )
        # Flush any open prose so the tool item appears after the
        # reasoning that produced it.
        provider_call_id = str(evt.get("tool_call_id") or "")
        call_id = provider_call_id
        # Disambiguate when the same tool_call_id appears twice (e.g.
        # swarm sub_tool ids built from ``agent-round-skill`` collide
        # if the same role calls the same skill twice in a round).
        # Without this the second start silently orphans the first
        # CommandExecutionItem — it never gets a tool_end since the
        # dict slot is overwritten.
        # A turn may invoke the ReAct driver more than once (for example the
        # bounded verification/repair follow-up). Each invocation owns a new
        # bridge state, so checking ``self.tools`` alone does not catch a
        # provider reusing the same call id across rounds. Public item ids are
        # turn-scoped; include already-persisted turn items when minting the
        # lifecycle key so later rounds cannot emit a second item with the
        # same id.
        existing_item_ids = {existing.id for existing in turn.items}
        if call_id and (call_id in self.tools or call_id in existing_item_ids):
            suffix = 2
            while (
                f"{provider_call_id}#{suffix}" in self.tools
                or f"{provider_call_id}#{suffix}" in existing_item_ids
            ):
                suffix += 1
            call_id = f"{provider_call_id}#{suffix}"
        # Let the model's default_factory mint the id when there's no
        # call_id — the old ``CommandExecutionItem().id`` built a throwaway
        # with no ``command`` and raised ValidationError (command is required).
        item = CommandExecutionItem(
            command=str(evt.get("tool_name", "tool")),
            input_preview=evt.get("input_preview"),
            **({"id": call_id} if call_id else {}),
        )
        # Keep the lifecycle lookup key aligned with incoming tool_end events.
        # If an adapter omits tool_call_id, both start/end use the empty key;
        # the item itself still gets a generated id for the public protocol.
        # Empty provider ids cannot be dictionary keys for more than one open
        # call. The public item id is already unique, so use it internally and
        # retain the empty id only as an alias queue key.
        call_key = call_id or item.id
        self.tool_call_queues.setdefault(provider_call_id, []).append(call_key)
        # Merge any buffered tool-call deltas (dsh ``tool-call-delta``
        # lane) so the item opens with the arguments the UI watched
        # assemble; the provider's parsed ``input`` (if any) still wins
        # when the model sent it on the start payload.
        pending_delta = self._tool_call_delta_buffers.get(provider_call_id)
        if pending_delta and (pending_delta["arguments"] or pending_delta["name"]):
            preview = item.input_preview if isinstance(item.input_preview, dict) else {}
            item.input_preview = {
                **preview,
                "name": pending_delta["name"],
                "arguments": pending_delta["arguments"],
            }
            self._tool_call_delta_emitted[provider_call_id] = len(pending_delta["arguments"])
        start_narrative = None if has_open_public_prose else _tool_start_public_narrative(evt)
        self.tool_public_narrative_started[call_key] = bool(start_narrative)
        if start_narrative:
            await self.append_commentary(
                turn,
                log,
                emitter,
                start_narrative,
                start_new_segment=True,
            )
        # A second tool_start may be a parallel sibling. Flush only prose;
        # closing foreground tools here would fabricate a completion before
        # their tool_end events arrive and would discard their receipts.
        await self.flush(turn, log, emitter, close_tools=False)
        self._bind_timeline(item)
        self.tools[call_key] = item
        turn.items.append(item)
        # ``tool_start`` is an execution intent, not decorative UI. The
        # realtime producer waits for this reducer to finish before resuming
        # the generator that performs the call, so make the journal record
        # durable before releasing that execution barrier.
        await self._emit_started(turn, log, emitter, item, durable=True)
        phases = _phases_from_todo_preview(item.input_preview, active_item_id=item.id)
        if phases is None:
            phases = _phases_from_plan_md(item.input_preview, active_item_id=item.id)
        if phases is not None:
            self.phases = phases
        await self._emit_turn_update(
            turn,
            log,
            emitter,
            workspace_focus=_workspace_focus_for_tool(item),
        )

    def _resolve_open_tool_key(self, provider_call_id: str) -> str | None:
        """Resolve a possibly duplicated/empty provider id to an open item."""

        queue = self.tool_call_queues.get(provider_call_id)
        if queue:
            while queue and queue[0] not in self.tools:
                queue.pop(0)
            if queue:
                return queue[0]
            self.tool_call_queues.pop(provider_call_id, None)
        # Compatibility with bridge state created before alias queues were
        # introduced (and with tests that inject tools directly).
        if provider_call_id in self.tools:
            return provider_call_id
        return None

    def _consume_tool_alias(self, provider_call_id: str, call_key: str) -> None:
        queue = self.tool_call_queues.get(provider_call_id)
        if not queue:
            return
        with contextlib.suppress(ValueError):
            queue.remove(call_key)
        if not queue:
            self.tool_call_queues.pop(provider_call_id, None)

    async def append_tool_output(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        evt: dict[str, Any],
    ) -> None:
        call_id = str(evt.get("tool_call_id") or "")
        call_key = self._resolve_open_tool_key(call_id)
        item = self.tools.get(call_key) if call_key is not None else None
        delta = evt.get("delta")
        if item is None or not isinstance(delta, str) or not delta:
            return
        item.aggregated_output = _append_capped_output(item.aggregated_output or "", delta)
        logged = log.item_delta(turn.thread_id, turn.id, item.id, "commandOutput", delta)
        await emitter.notify(
            ServerMethod.ITEM_COMMAND_OUTPUT_DELTA,
            {
                "threadId": turn.thread_id,
                "turnId": turn.id,
                "itemId": item.id,
                "delta": delta,
                "eventId": logged.event_id,
            },
        )

    async def track_background_tool(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        evt: dict[str, Any],
    ) -> None:
        call_id = str(evt.get("tool_call_id") or "")
        task_id = str(evt.get("task_id") or "")
        call_key = self._resolve_open_tool_key(call_id)
        item = self.tools.get(call_key) if call_key is not None else None
        if item is None or not task_id:
            return

        preview = item.input_preview if isinstance(item.input_preview, dict) else {}
        snapshot = evt.get("snapshot")
        if not isinstance(snapshot, dict):
            snapshot = {}
        item.input_preview = {
            **preview,
            "background": True,
            "task_id": task_id,
            "status": "running",
            "argv": snapshot.get("argv"),
            "cwd": snapshot.get("cwd") or item.cwd,
        }
        item.process_id = task_id
        # Re-emit ITEM_STARTED so the UI picks up the background metadata
        # we just added; the journal already has the original start record
        # from ``start_tool`` so we skip the journal write here.
        await emitter.notify(
            ServerMethod.ITEM_STARTED,
            self._item_payload(turn, item),
        )
        with contextlib.suppress(ConnectionError, OSError, RuntimeError, TypeError):
            await self.append_tool_output(
                turn,
                log,
                emitter,
                {
                    "tool_call_id": call_id,
                    "delta": f"background process started: {task_id}\n",
                },
            )

        async def _watch_background() -> None:
            last_stdout = ""
            last_stderr = ""
            try:
                from runtime.execution.suckers.write_skills import (
                    _kill_background_exec,
                    _read_background_output,
                )

                while True:
                    if emitter.is_turn_interrupted(turn.id):
                        snap = _kill_background_exec(task_id=task_id)
                    else:
                        snap = _read_background_output(task_id=task_id)
                    if not isinstance(snap, dict):
                        snap = {"status": "failed", "error": "invalid background snapshot"}

                    stdout = str(snap.get("stdout") or "")
                    stderr = str(snap.get("stderr") or "")
                    delta = ""
                    if len(stdout) > len(last_stdout):
                        delta += stdout[len(last_stdout) :]
                    if len(stderr) > len(last_stderr):
                        stderr_delta = stderr[len(last_stderr) :]
                        delta += stderr_delta if not delta else "\n[stderr]\n" + stderr_delta
                    last_stdout = stdout
                    last_stderr = stderr
                    if delta:

                        async def _append_delta(delta: str = delta) -> None:
                            await self.append_tool_output(
                                turn,
                                log,
                                emitter,
                                {"tool_call_id": call_id, "delta": delta},
                            )

                        await _guarded_background_write(
                            emitter,
                            turn.thread_id,
                            _append_delta,
                        )

                    status = str(snap.get("status") or "")
                    if status != "running":
                        if status == "cancelled":
                            end_status = "cancelled"
                        elif status == "completed":
                            end_status = "success"
                        else:
                            end_status = "error"

                        async def _complete(end_status: str = end_status) -> None:
                            await self.complete_tool(
                                turn,
                                log,
                                emitter,
                                {
                                    "tool_call_id": call_id,
                                    "tool_name": evt.get("tool_name") or item.command,
                                    "status": end_status,
                                    "output_preview": "",
                                    "duration_ms": evt.get("duration_ms"),
                                },
                            )

                        await _guarded_background_write(
                            emitter,
                            turn.thread_id,
                            _complete,
                        )
                        return
                    await asyncio.sleep(0.5)
            except Exception:  # noqa: BLE001
                # Losing canonical authority is terminal for this watcher. Kill
                # the underlying process as well so it cannot be rediscovered
                # and projected into a permanently deleted or newer turn.
                with contextlib.suppress(Exception):
                    from runtime.execution.suckers.write_skills import (
                        _kill_background_exec,
                    )

                    _kill_background_exec(task_id=task_id)
                _logger.debug("background command watcher failed", exc_info=True)

        _bg_task = asyncio.create_task(_watch_background())
        # Tag the watcher task with the background command so turn
        # finalization can tell "the model delegated verification" from an
        # unrelated watcher/server still running, when deciding whether to
        # close unverified code as completed-with-background.
        with contextlib.suppress(Exception):
            _bg_task.set_name(f"echo-background:{str(item.command or '')[:200]}")
        self.background_tasks.append(_bg_task)
        if self._on_background_task_start is not None:
            with contextlib.suppress(Exception):
                self._on_background_task_start(_bg_task)

    async def complete_tool(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        evt: dict[str, Any],
    ) -> None:
        call_id = str(evt.get("tool_call_id") or "")
        call_key = self._resolve_open_tool_key(call_id)
        item = self.tools.pop(call_key, None) if call_key is not None else None
        if item is None:
            # Unknown tool_call_id — skip rather than synthesize.
            return
        assert call_key is not None
        self._consume_tool_alias(call_id, call_key)
        self._tool_call_delta_buffers.pop(call_id, None)
        self._tool_call_delta_emitted.pop(call_id, None)
        emitted_start_narrative = self.tool_public_narrative_started.pop(call_key, False)
        status = evt.get("status", "success")
        if status == "rejected":
            item.status = ItemStatus.DECLINED
        elif status == "cancelled":
            item.status = ItemStatus.INTERRUPTED
        elif status == "error":
            item.status = ItemStatus.FAILED
        else:
            item.status = ItemStatus.COMPLETED
        if isinstance(evt.get("output_preview"), str) and not item.aggregated_output:
            # If the tool already streamed output incrementally, keep the
            # streamed text — ``output_preview`` is a *summary* that loses
            # detail, so overwriting would regress the live view.
            item.aggregated_output = _append_capped_output("", evt["output_preview"])
        effect_receipt = evt.get("effect_receipt")
        if isinstance(effect_receipt, dict):
            with contextlib.suppress(TypeError, ValueError):
                item.effect_receipt = ToolEffectSignal.model_validate(effect_receipt)
        await self._emit_completed(turn, log, emitter, item)
        # Apply-patch first-class item: when a file-editing tool ran
        # successfully and surfaced a unified diff, promote it to a
        # dedicated FileChangeItem so the UI can render hunks with
        # per-hunk accept/reject. We only do this on success — a
        # failed tool call has nothing to show.
        if item.status == ItemStatus.COMPLETED:
            structured_evidence: list[EvidenceReference] = []
            raw_evidence = evt.get("evidence")
            if isinstance(raw_evidence, list):
                for index, raw in enumerate(raw_evidence):
                    if not isinstance(raw, dict):
                        continue
                    payload = {
                        **raw,
                        "id": raw.get("id") or f"tool:{item.id}:{index}",
                        "sourceItemId": item.id,
                        "phaseId": item.phase_id,
                    }
                    with contextlib.suppress(TypeError, ValueError):
                        structured_evidence.append(EvidenceReference.model_validate(payload))
            self._record_evidence(
                structured_evidence or _tool_evidence(item, phase_id=item.phase_id)
            )
            related_change_item_ids: list[str] = []
            related_files: list[str] = []
            file_item = _file_change_item_from_tool_evt(evt)
            if file_item is not None:
                self._bind_timeline(file_item)
                related_change_item_ids.append(file_item.id)
                related_files = [change.path for change in file_item.changes]
                turn.items.append(file_item)
                started_file_item = FileChangeItem(
                    id=file_item.id,
                    changes=[],
                    grant_root=file_item.grant_root,
                    timeline_sequence=file_item.timeline_sequence,
                    parent_item_id=file_item.parent_item_id,
                    phase_id=file_item.phase_id,
                )
                await self._emit_started(turn, log, emitter, started_file_item)
                file_focus = _workspace_focus_for_file_change(file_item)
                await self._emit_turn_update(
                    turn,
                    log,
                    emitter,
                    workspace_focus=file_focus,
                )
                await self._emit_file_change_hunks(
                    turn,
                    log,
                    emitter,
                    file_item,
                    workspace_focus=file_focus,
                )
                # The promoted item is created with the default IN_PROGRESS
                # status. Flip it to COMPLETED before the completion event so
                # the item doesn't read as ``inProgress`` and get swept to
                # ``failed`` by _close_turn when the turn ends.
                file_item.status = ItemStatus.COMPLETED
                # ``_emit_item_completed`` lives on ``CerebrumRuntime`` and
                # isn't reachable from here — use the local ``_emit_completed``
                # so we don't reach across class boundaries.
                await self._emit_completed(turn, log, emitter, file_item)
                self._record_evidence(
                    [
                        EvidenceReference(
                            id=f"change:{file_item.id}:{path}",
                            kind="file",
                            title=path.replace("\\", "/").rsplit("/", 1)[-1],
                            uri=path,
                            status="passed",
                            origin="tool",
                            source_item_id=file_item.id,
                            phase_id=file_item.phase_id,
                        )
                        for path in related_files
                    ]
                )

            verification_item = _verification_item_from_tool_evt(
                item,
                evt,
                related_change_item_ids=related_change_item_ids,
                related_files=related_files,
            )
            if verification_item is not None:
                self._bind_timeline(verification_item)
                turn.items.append(verification_item)
                await self._emit_started(turn, log, emitter, verification_item)
                await self._emit_completed(turn, log, emitter, verification_item)
                self._record_verification_evidence(verification_item)
        else:
            verification_item = _verification_item_from_tool_evt(
                item,
                evt,
                related_change_item_ids=[],
                related_files=[],
            )
            if verification_item is not None:
                self._bind_timeline(verification_item)
                turn.items.append(verification_item)
                await self._emit_started(turn, log, emitter, verification_item)
                await self._emit_completed(turn, log, emitter, verification_item)
                self._record_verification_evidence(verification_item)
        if emitted_start_narrative:
            done_narrative = _tool_done_public_narrative(evt)
            if done_narrative:
                await self.append_commentary(
                    turn,
                    log,
                    emitter,
                    done_narrative,
                    start_new_segment=True,
                )
        await self._emit_turn_update(
            turn,
            log,
            emitter,
            workspace_focus=turn.workspace_focus,
        )

    async def update_grounding_evidence(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        sources: list[GroundingSource],
    ) -> None:
        self._record_evidence(_grounding_evidence(sources))
        await self._emit_turn_update(
            turn,
            log,
            emitter,
            workspace_focus=turn.workspace_focus,
        )

    def _record_verification_evidence(self, item: Any) -> None:
        status = "passed" if item.status == ItemStatus.COMPLETED else "failed"
        title = item.summary or item.command
        self._record_evidence(
            [
                EvidenceReference(
                    id=f"verification:{item.id}",
                    kind="verification",
                    title=title,
                    status=status,
                    origin="verification",
                    source_item_id=item.id,
                    phase_id=item.phase_id,
                    detail=item.command if title != item.command else None,
                )
            ]
        )

    def _record_evidence(self, evidence: list[EvidenceReference]) -> None:
        by_id = {item.id: item for item in self.evidence}
        for item in evidence:
            by_id[item.id] = item
        # A long research turn can touch thousands of search hits. The
        # snapshot is a useful recent-evidence index, not a second event log.
        self.evidence = list(by_id.values())[-200:]

    async def flush(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        *,
        status: ItemStatus = ItemStatus.COMPLETED,
        close_tools: bool = True,
    ) -> None:
        """Close the currently open prose lane with its true outcome.

        A transport item can be fully flushed without being a valid final
        answer.  Cancellation and failure used to call this same method and
        stamp partial prose as ``completed``, which made a source fragment or
        half sentence look authoritative after replay.  Callers that end the
        turn early now pass the corresponding terminal item status.
        """

        # Drain coalesced deltas BEFORE finalizing: completing an item
        # nulls the slot the pending tail would attach to.
        await self._flush_pending_delta()
        if self.agent_message is not None:
            self.agent_message.status = status
            await self._emit_completed(turn, log, emitter, self.agent_message)
            self.agent_message = None
        if self.commentary_message is not None:
            self.commentary_message.status = status
            await self._emit_completed(turn, log, emitter, self.commentary_message)
            self.commentary_message = None
        if self.reasoning is not None:
            self.reasoning.status = status
            if self.reasoning_started_monotonic is not None and self.reasoning.duration_ms is None:
                self.reasoning.duration_ms = max(
                    0,
                    int((time.monotonic() - self.reasoning_started_monotonic) * 1000),
                )
            await self._emit_completed(turn, log, emitter, self.reasoning)
            self.reasoning = None
            self.reasoning_started_monotonic = None
        # Close abandoned foreground tools so the terminal snapshot never
        # carries a spurious inProgress spinner. Background processes are
        # intentionally owned by their watcher after the turn ends; closing
        # them here would publish a false completion before the process exits.
        if close_tools:
            for item_id, tool in list(self.tools.items()):
                preview = tool.input_preview
                if isinstance(preview, dict) and preview.get("background") is True:
                    continue
                tool.status = status
                await self._emit_completed(turn, log, emitter, tool)
                del self.tools[item_id]

    async def finalize_workbench(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        *,
        terminal_status: TurnStatus,
    ) -> None:
        """Emit the terminal workbench snapshot for ``turn``.

        Called when the orchestrating turn reaches a terminal state
        (COMPLETED / FAILED / INTERRUPTED). ``_terminal_workbench_phases``
        rewrites pending/running phases into the appropriate terminal
        shape and clears every ``active_item_id`` so the UI stops
        highlighting items owned by long-lived background watchers
        (e.g. a long-running shell command whose output keeps
        streaming after the turn finishes).

        We do NOT bail when ``self.tools`` is non-empty — those are
        background watchers by design (see
        ``track_background_tool``); the user's *turn* is over even if
        the watcher process isn't. Bailing here used to leave the UI
        stuck at "running" forever.
        """
        if not self.phases:
            return
        terminal_phases = _terminal_workbench_phases(
            self.phases,
            terminal_status,
        )
        if terminal_phases == self.phases and turn.workbench_snapshot is not None:
            return
        self.phases = terminal_phases
        await self._emit_turn_update(
            turn,
            log,
            emitter,
            workspace_focus=turn.workspace_focus,
        )

    async def _emit_turn_update(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        *,
        workspace_focus: WorkspaceFocus | None = None,
    ) -> None:
        phases = _phases_with_active_item(self.phases, workspace_focus)
        turn.phases = phases
        if workspace_focus is not None:
            turn.workspace_focus = workspace_focus
        self.workbench_snapshot_version += 1
        snapshot = _workbench_snapshot(
            version=self.workbench_snapshot_version,
            phases=phases,
            workspace_focus=turn.workspace_focus,
            evidence=self.evidence,
        )
        turn.workbench_snapshot = snapshot
        phases_payload = [phase.model_dump(by_alias=True, mode="json") for phase in phases]
        focus_payload = (
            workspace_focus.model_dump(by_alias=True, mode="json")
            if workspace_focus is not None
            else None
        )
        snapshot_payload = snapshot.model_dump(by_alias=True, mode="json")
        logged_update = log.turn_updated(
            turn.thread_id,
            turn.id,
            phases=phases_payload,
            workspace_focus=focus_payload,
            workbench_snapshot=snapshot_payload,
        )
        # SUNSET: the embedded ``workbenchSnapshot`` copy ships only for
        # legacy clients (see _LEGACY_PLAN_SNAPSHOT above). New clients get
        # the identical frame from the dedicated ``workbench/snapshot``
        # notification below, halving the wire size for plan-only updates.
        legacy_snapshot_payload = (
            {"workbenchSnapshot": snapshot_payload} if _LEGACY_PLAN_SNAPSHOT else {}
        )
        await emitter.notify(
            ServerMethod.TURN_PLAN_UPDATED,
            {
                "threadId": turn.thread_id,
                "turnId": turn.id,
                "phases": phases_payload,
                **({"workspaceFocus": focus_payload} if focus_payload is not None else {}),
                **legacy_snapshot_payload,
                **({"eventId": logged_update.event_id} if logged_update is not None else {}),
            },
        )
        await emitter.notify(
            ServerMethod.WORKBENCH_SNAPSHOT,
            {
                "threadId": turn.thread_id,
                "turnId": turn.id,
                "snapshot": snapshot_payload,
            },
        )

    async def _emit_file_change_hunks(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        item: FileChangeItem,
        *,
        workspace_focus: WorkspaceFocus | None = None,
    ) -> None:
        focus_payload = (
            workspace_focus.model_dump(by_alias=True, mode="json")
            if workspace_focus is not None
            else None
        )
        for change in item.changes:
            for hunk in change.hunks:
                hunk_payload = hunk.model_dump(by_alias=True, mode="json")
                delta_payload = {
                    "path": change.path,
                    "op": change.op,
                    "hunk": hunk_payload,
                }
                logged = log.item_delta(
                    turn.thread_id,
                    turn.id,
                    item.id,
                    "fileChangeHunk",
                    delta_payload,
                )
                await emitter.notify(
                    ServerMethod.ITEM_FILE_CHANGE_HUNK_DELTA,
                    {
                        "threadId": turn.thread_id,
                        "turnId": turn.id,
                        "itemId": item.id,
                        "path": change.path,
                        "op": change.op,
                        "hunk": hunk_payload,
                        **({"workspaceFocus": focus_payload} if focus_payload is not None else {}),
                        "eventId": logged.event_id,
                    },
                )
