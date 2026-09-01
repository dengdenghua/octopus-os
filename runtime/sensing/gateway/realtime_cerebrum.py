"""Cerebrum-backed realtime runtime.

Bridges the existing :func:`runtime.core.cerebrum.react_loop.stream_react_loop`
to the JSON-RPC ``item/*`` protocol. Translation rules:

  ``text_delta``           → ``item/agentMessage/delta`` on the active
                             agentMessage item (created lazily).
  ``thinking_delta``       → kept private; public progress uses the explicit
                             commentary/public-summary channel.
  ``tool_start``           → emits ``item/started`` for a new
                             commandExecution / mcpToolCall item; record
                             the tool call id so subsequent events bind.
  ``tool_approval_request``→ converted into a server-initiated
                             ``item/commandExecution/requestApproval``
                             via the gateway's approval channel; the
                             returned decision is forwarded to the
                             :class:`ApprovalProvider` blocking the
                             react loop's thread.
  ``tool_end``             → emits ``item/completed`` for the matching
                             tool item, propagating status/exit code.
  ``react_step_complete``  → flushes any open agentMessage/reasoning
                             items and finalizes them.
  ``react_completed``      → triggers turn close; ``turn/completed`` is
                             emitted by the gateway after start_turn
                             returns.
  ``react_error``          → final ``error`` item.

The ``GatewayApprovalProvider`` runs the react loop's blocking
``request`` call on the asyncio event loop's executor, then awaits the
gateway's :meth:`EventEmitter.request_approval` from the running loop.
This is the only place where async↔sync handoff happens; the rest of
the bridge stays in the runtime's coroutine.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections import deque
from pathlib import Path
from queue import SimpleQueue
from typing import Any

from runtime.memory.threads.event_log import EventLog
from runtime.platform.models import ParsedIntent
from runtime.platform.process.bounded_set import BoundedSet
from runtime.platform.process.keyed_lock import KeyedLock
from runtime.protocol import (
    ReasoningItem,
    SteeringUserMessageItem,
    TodoListItem,
    Turn,
    TurnParams,
)
from runtime.safety.approval.approval_gate import (
    ApprovalProvider,
)
from runtime.sensing.gateway._event_bridge_tool_items import (
    _file_change_item_from_tool_evt as _file_change_item_from_tool_evt,
)
from runtime.sensing.gateway._realtime_cerebrum_project_os import (
    _drive_project_os,
)
from runtime.sensing.gateway._realtime_cerebrum_project_os import (
    _format_project_os_result as _format_project_os_result,
)
from runtime.sensing.gateway._realtime_cerebrum_project_os import (
    _parse_project_os_control as _parse_project_os_control,
)
from runtime.sensing.gateway._realtime_cerebrum_requests import _handle_request
from runtime.sensing.gateway._realtime_cerebrum_steering import (
    _active_turn_lease_path,
    _bind_turn_timeline,
    _drain_active_turns_for_shutdown,
    _drain_turn_steering,
    _has_fresh_active_turn_lease,
    _publish_discovered_steering,
    _register_active_turn,
    _remove_active_turn_lease,
    _restore_turn_steering,
    _set_turn_steering_accepting,
    _sync_persisted_turn_steering,
    _unregister_active_turn,
    _write_active_turn_lease,
)
from runtime.sensing.gateway._realtime_cerebrum_thread import (
    _emit_agent_message,
    _emit_item_completed,
    _emit_item_started,
    _emit_reasoning,
    _emit_todo_list,
    _ensure_thread,
    _log_for,
    _make_bridge_state,
    _reap_stale_background_tasks,
    _require_thread_id,
    _require_thread_owner,
    _resolve_agent,
    _resume_turns,
    _snapshot_to_thread_store,
    _wrap_with_policy,
)
from runtime.sensing.gateway._realtime_react_stream_helpers import (
    _agentic_stream_event_to_react_event as _agentic_stream_event_to_react_event,
)
from runtime.sensing.gateway._realtime_react_stream_helpers import (
    _should_use_native_tool_loop as _should_use_native_tool_loop,
)

# ── Split-module compat re-exports ────────────────────────────
# The helpers below moved out of this file into focused sibling
# modules. Re-import them under their original names (redundant-alias
# form marks an intentional re-export) so existing imports and tests
# that reach into ``realtime_cerebrum`` keep working unchanged.
from runtime.sensing.gateway.realtime_approval import GatewayApprovalProvider
from runtime.sensing.gateway.realtime_event_bridge import (
    _ReactBridgeState,
)
from runtime.sensing.gateway.realtime_gateway import EventEmitter, RealtimeRuntime
from runtime.sensing.gateway.realtime_react_stream import (
    _apply_react_event,
    _drive_react,
    _drive_reflection_fast_path,
    _should_use_reflection_fast_path,
    _try_reflex_reply,
)
from runtime.sensing.gateway.realtime_team_stream import (
    _drive_group_fanout,
    _drive_swarm_mesh,
    _drive_team_topology,
)
from runtime.sensing.gateway.realtime_thread_history import (
    _conversation_messages_for_react as _conversation_messages_for_react,
)
from runtime.sensing.gateway.realtime_thread_history import (
    _flatten_turns_to_messages as _flatten_turns_to_messages,
)
from runtime.sensing.gateway.realtime_thread_ops import (
    _handle_hunk_decide,
    _maybe_compact,
    _maybe_compact_locked,
    _resolve_hunk_path,
    compact_thread,
)
from runtime.sensing.gateway.realtime_turn_input import (
    _build_intent as _build_intent,
)
from runtime.sensing.gateway.realtime_turn_input import (
    _should_default_planning_mode as _should_default_planning_mode,
)
from runtime.sensing.gateway.realtime_turn_input import (
    _should_default_topology as _should_default_topology,
)
from runtime.sensing.gateway.realtime_turn_lifecycle import (
    _consume_confirmed_resume_intent,
    _consume_paused_task_resume_intent,
    _record_pending_resume_intent,
    _start_turn,
)
from runtime.sensing.gateway.realtime_turn_outcome import (
    _record_failed_turn_proposal,
    _record_react_trace_event,
    _record_successful_turn_example,
    _record_task_run_finished,
    _record_task_run_started,
)
from runtime.sensing.gateway.realtime_workbench import (
    _current_workbench_phase as _current_workbench_phase,
)
from runtime.sensing.gateway.realtime_workbench import (
    _phase_title as _phase_title,
)
from runtime.sensing.gateway.realtime_workbench import (
    _terminal_workbench_phases as _terminal_workbench_phases,
)
from runtime.sensing.gateway.realtime_workbench import (
    _workbench_snapshot as _workbench_snapshot,
)
from runtime.sensing.gateway.realtime_workbench import (
    _workbench_status as _workbench_status,
)

_logger = logging.getLogger(__name__)


# ── Cerebrum runtime ──────────────────────────────────────────


class CerebrumRuntime:
    """Realtime runtime backed by the project's ReAct planner.

    Construct with the same execution stack the rest of the runtime
    uses. ``logs_root`` is the per-thread JSONL directory (same shape
    as :class:`EchoRuntime`).
    """

    def __init__(
        self,
        stack: Any,
        *,
        agent: Any = None,
        agent_registry: Any = None,
        logs_root: str = "data/threads",
        max_iterations: int = 30,
        policy_path: Any = None,
        workspace_root: Any = None,
        compaction_policy: Any = None,
        summary_router: Any = None,
        thread_store: Any = None,
        allow_client_auto_approve: bool = False,
        allow_local_workspace_access: bool = False,
        reflex_router: Any = None,
        trace_store: Any = None,
        cowork_group_store: Any = None,
        collaboration_store: Any = None,
        project_store: Any = None,
        project_os_hooks: dict[str, Any] | None = None,
        subagent_runner: Any = None,
        task_supervisor: Any = None,
        session_titles: Any = None,
    ) -> None:
        """Wire a CerebrumRuntime onto an existing echo stack.

        ``agent`` is the default agent — used when the turn params don't
        specify one. ``agent_registry`` lets the runtime resolve a
        per-turn agent from ``params['agent']`` (typically the assistant
        id picked by the UI). Either or both may be ``None``; the
        underlying react_loop also accepts a None agent and falls back
        to its catch-all skill catalogue.

        ``policy_path`` points at the ``permissions.json`` the UI writes
        to when the user clicks "always trust". None = no static layer,
        every approval round-trips through the gateway.

        ``workspace_root`` is the directory where per-thread isolated
        workspaces are allocated. When None, the runtime does not
        auto-allocate: turns use whatever ``cwd`` the client supplies
        (or None). When set, turns without an explicit cwd default to
        ``<workspace_root>/<thread_id>/``, giving parallel threads
        collision-free filesystem scope — each thread gets its own
        directory, so two concurrent threads can't write to the
        same file.

        ``compaction_policy`` enables automatic turn-compaction after
        each completed turn (bounded context window). None disables
        compaction entirely. When ``summary_router`` is also
        provided, an LLM-backed summariser is wired in — otherwise the
        mechanical default (deterministic prose) is used.

        ``session_titles`` (optional) is a ``SessionTitleService``; the
        first-completed-turn auto-title regeneration (dsh auto-title)
        runs against it after each thread-store snapshot.
        """
        self._stack = stack
        self._default_agent = agent
        self._agent_registry = agent_registry
        self._logs_root = logs_root
        self._max_iterations = max_iterations
        self._policy_path = policy_path
        self._compaction_policy = compaction_policy
        self._summary_router = summary_router
        # Optional handle to the legacy ``ThreadStateStore`` so each
        # completed realtime turn can write a flattened AgentThreadState
        # snapshot. The workspace sidebar's "recent chats" list reads
        # from that store; without this bridge, realtime conversations
        # would never appear in the history grouping (today / 7d / 30d).
        self._thread_store = thread_store
        # Optional SessionTitleService for first-turn auto-title
        # regeneration (dsh auto-title); None keeps the old behaviour.
        self._session_titles = session_titles
        self._reflex_router = reflex_router
        self._trace_store = trace_store
        self._task_supervisor = task_supervisor
        self._cowork_group_store = cowork_group_store
        self._collaboration_store = collaboration_store
        self._project_store = project_store
        self._project_os_hooks = dict(project_os_hooks or {})
        self._subagent_runner = subagent_runner
        # Server-side authority over auto-approval. When False (default),
        # a client setting ``approvalPolicy="never"`` is downgraded to
        # ``"on-request"`` server-side — the client never gets to silently
        # disable approval gates. Operators who genuinely want headless
        # batches must opt in at config time.
        self._allow_client_auto_approve = bool(allow_client_auto_approve)
        self._allow_local_workspace_access = bool(allow_local_workspace_access)
        from pathlib import Path

        Path(logs_root).mkdir(parents=True, exist_ok=True)
        self._proposal_ledger_path = Path(logs_root).parent / "proposal_ledger.jsonl"
        self._workspaces: Any = None
        if workspace_root is not None:
            from runtime.platform.runtime_policy.workspaces import WorkspaceManager

            self._workspaces = WorkspaceManager(Path(workspace_root))
        # Dedup ledger for thread_started emission. Bounded so a server
        # that handles a large number of distinct short-lived threads over
        # its lifetime doesn't accumulate one entry forever — re-seeing an
        # evicted thread at worst re-emits thread_started, which the
        # persisted-log check in _ensure_thread already guards against.
        self._known_threads = BoundedSet(maxsize=8192)
        self._lock = asyncio.Lock()
        self._active_turn_ids: set[str] = set()
        # Live steering state. The realtime RPC runs on the asyncio thread,
        # while the native model/tool loop drains messages from a worker
        # thread, so SimpleQueue is the deliberately small synchronization
        # boundary between them.
        self._active_turns: dict[str, tuple[Turn, EventLog]] = {}
        self._turn_steering: dict[str, SimpleQueue[tuple[str, str]]] = {}
        self._turn_steering_restored: dict[str, deque[str]] = {}
        self._turn_steering_seen: dict[str, set[str]] = {}
        self._turn_steering_notified: dict[str, set[str]] = {}
        self._turn_steering_last_sync: dict[str, float] = {}
        self._turn_steering_log_offsets: dict[str, int] = {}
        self._turn_steering_lock = threading.Lock()
        self._turn_steering_accepting: dict[str, bool] = {}
        self._turn_timeline: dict[str, tuple[int, str | None]] = {}
        self._instance_id = f"{os.getpid()}-{id(self):x}"
        self._active_turn_lease_root = Path(logs_root) / ".active-turns"
        self._active_turn_lease_root.mkdir(parents=True, exist_ok=True)
        self._active_turn_lease_tasks: dict[str, asyncio.Task[None]] = {}
        # Per-thread compaction serialization. Reference-counted so the
        # map is reclaimed when a thread goes idle rather than leaking one
        # lock per thread_id forever.
        self._compaction_locks = KeyedLock()
        self._pending_resume_intents: dict[str, dict[str, Any]] = {}
        self._resume_intents_lock = asyncio.Lock()
        # Per-thread registry of background command watchers. Each
        # ``track_background_tool`` call registers its asyncio task
        # here; the next turn on the same thread reaps any still-
        # running entries before it begins. Without this, watchers
        # outlive their turn (by design — long shells must keep
        # streaming after the LLM finalises) but they CAN bleed into
        # a brand-new conversation when the user reuses the thread.
        self._thread_background_tasks: dict[str, list[asyncio.Task[None]]] = {}
        # Cold durable subagent-session scans get only a bounded place in the
        # foreground startup path.  Keep deferred tasks strongly referenced
        # and one-per-thread so a later turn reuses the same claim attempt
        # instead of racing it and injecting one parked report twice.
        self._pending_subagent_report_tasks: dict[
            str,
            asyncio.Task[tuple[int, int]],
        ] = {}
        self._pending_subagent_refill_tasks: dict[str, asyncio.Task[None]] = {}

    def _make_bridge_state(
        self,
        thread_id: str,
        turn_id: str | None = None,
        agent: Any | None = None,
    ) -> _ReactBridgeState:
        """Build a ``_ReactBridgeState`` wired to the per-thread
        background-task registry, so the next turn on this thread can
        sweep any watchers the previous turn left running."""
        return _make_bridge_state(self, thread_id, turn_id=turn_id, agent=agent)

    def _register_active_turn(self, turn: Turn, log: EventLog) -> None:
        _register_active_turn(self, turn, log)

    def _unregister_active_turn(self, turn_id: str) -> None:
        _unregister_active_turn(self, turn_id)

    async def drain_active_turns_for_shutdown(
        self,
        *,
        timeout_seconds: float = 3.0,
    ) -> dict[str, Any]:
        return await _drain_active_turns_for_shutdown(
            self,
            timeout_seconds=timeout_seconds,
        )

    def _active_turn_lease_path(self, turn_id: str) -> Path:
        return _active_turn_lease_path(self, turn_id)

    def _write_active_turn_lease(self, turn: Turn) -> None:
        _write_active_turn_lease(self, turn)

    def _has_fresh_active_turn_lease(
        self,
        thread_id: str,
        turn_id: str,
        *,
        require_accepting_steering: bool = False,
    ) -> bool:
        return _has_fresh_active_turn_lease(
            self,
            thread_id,
            turn_id,
            require_accepting_steering=require_accepting_steering,
        )

    def _remove_active_turn_lease(self, turn_id: str) -> None:
        _remove_active_turn_lease(self, turn_id)

    def _set_turn_steering_accepting(self, turn: Turn, accepting: bool) -> None:
        _set_turn_steering_accepting(self, turn, accepting)

    def _bind_turn_timeline(
        self,
        turn_id: str,
        item: Any,
        *,
        phase_id: str | None = None,
    ) -> None:
        _bind_turn_timeline(self, turn_id, item, phase_id=phase_id)

    def _sync_persisted_turn_steering(
        self,
        turn_id: str,
        *,
        force: bool = False,
    ) -> list[SteeringUserMessageItem]:
        return _sync_persisted_turn_steering(self, turn_id, force=force)

    async def _publish_discovered_steering(
        self,
        turn: Turn,
        emitter: EventEmitter,
    ) -> None:
        await _publish_discovered_steering(self, turn, emitter)

    def _drain_turn_steering(self, turn_id: str) -> list[str]:
        return _drain_turn_steering(self, turn_id)

    def _restore_turn_steering(self, turn_id: str, messages: list[str]) -> None:
        _restore_turn_steering(self, turn_id, messages)

    # ── Turn telemetry records (bodies in realtime_turn_outcome) ──

    def _record_task_run_started(
        self,
        turn: Turn,
        *,
        text: str,
        params: TurnParams,
    ) -> None:
        _record_task_run_started(self, turn, text=text, params=params)

    def _record_task_run_finished(
        self,
        turn: Turn,
        *,
        recover_stale_lease: bool = False,
    ) -> None:
        _record_task_run_finished(
            self,
            turn,
            recover_stale_lease=recover_stale_lease,
        )

    def _record_react_trace_event(self, turn: Turn, evt: dict[str, Any]) -> None:
        _record_react_trace_event(self, turn, evt)

    def _record_failed_turn_proposal(
        self,
        turn: Turn,
        *,
        intent: ParsedIntent | None,
        failure_source: str,
    ) -> None:
        _record_failed_turn_proposal(self, turn, intent=intent, failure_source=failure_source)

    def _record_successful_turn_example(
        self,
        turn: Turn,
        *,
        intent: ParsedIntent | None,
    ) -> None:
        _record_successful_turn_example(self, turn, intent=intent)

    async def _reap_stale_background_tasks(self, thread_id: str) -> None:
        """Cancel and reap any background watchers from prior turns
        on this thread before a new turn begins.

        Called at the top of ``start_turn``. ``done`` tasks are
        already pruned by the registration done-callback, so this
        loop only fires for actually-still-running watchers — the
        common case (new turn after the prior one finished cleanly)
        is a no-op.
        """
        await _reap_stale_background_tasks(self, thread_id)

    def _resolve_agent(self, params: TurnParams) -> Any:
        """Pick the agent for this turn.

        Lookup order:
          1. The persisted owner of an existing thread.
          2. Realtime input metadata (``agent_id`` / ``agent`` /
             ``agent_name``), including the nested ``context`` bag the
             web UI sends on ``turn/start``.
          3. The registry's match for that id, if any.
          4. The default agent passed at construction time.
          5. ``None``.
        """
        return _resolve_agent(self, params)

    def _wrap_with_policy(self, fallback: ApprovalProvider) -> ApprovalProvider:
        """Two-layer permission: static rules first, fallback otherwise.

        Reads ``permissions.json`` on every turn so UI-initiated edits
        (the "always trust" button) take effect immediately without
        bouncing the runtime. The file is small (a handful of rules)
        so the IO cost is irrelevant compared to a turn's LLM calls.
        """
        return _wrap_with_policy(self, fallback)

    # ── Compaction (bodies in realtime_thread_ops) ────────────

    async def _maybe_compact(
        self,
        thread_id: str,
        log: EventLog,
        emitter: EventEmitter,
    ) -> None:
        await _maybe_compact(self, thread_id, log, emitter)

    async def _maybe_compact_locked(
        self,
        thread_id: str,
        log: EventLog,
        emitter: EventEmitter,
    ) -> None:
        await _maybe_compact_locked(self, thread_id, log, emitter)

    def _log_for(self, thread_id: str) -> EventLog:
        return _log_for(self, thread_id)

    def _require_thread_id(self, value: Any) -> str:
        return _require_thread_id(self, value)

    def _require_thread_owner(
        self,
        log: EventLog,
        actor_id: str | None,
        *,
        turns: list[Turn] | None = None,
        access: str = "owner",
    ) -> None:
        _require_thread_owner(self, log, actor_id, turns=turns, access=access)

    def _resume_turns(
        self,
        log: EventLog,
        *,
        turns: list[Turn] | None = None,
    ) -> list[Turn]:
        """Replay and close in-progress turns left by an older process."""
        return _resume_turns(self, log, turns=turns)

    def _snapshot_to_thread_store(
        self,
        thread_id: str,
        log: EventLog,
        intent: ParsedIntent | None = None,
    ) -> None:
        """Flatten the realtime conversation into the legacy
        ``AgentThreadState`` shape and upsert it into ``ThreadStateStore``.

        Without this bridge, realtime turns live only in the per-thread
        JSONL event log under ``data/threads/`` and never surface in the
        sidebar's "recent chats" list, which reads ``ThreadStateStore``
        (``agents/<agent>/sessions/<thread>.jsonl`` + the legacy
        ``data/threads.jsonl`` index).

        Called after every turn — completed, failed, or interrupted —
        so a half-completed conversation still shows up in history.
        Failures here are swallowed: the realtime event log is the
        durable record; the legacy store is a derived cache for the
        sidebar.
        """
        _snapshot_to_thread_store(
            self,
            thread_id,
            log,
            intent=intent,
            session_titles=self._session_titles,
        )

    async def _ensure_thread(self, thread_id: str, emitter: EventEmitter) -> EventLog:
        return await _ensure_thread(self, thread_id, emitter)

    # ── RealtimeRuntime ──────────────────────────────────────

    async def start_turn(
        self,
        params: dict[str, Any],
        emitter: EventEmitter,
    ) -> Turn:
        """Start a new turn in a realtime thread.

        The orchestration body lives in
        :func:`runtime.sensing.gateway.realtime_turn_lifecycle._start_turn`;
        this method keeps the public ``RealtimeRuntime`` surface (and
        subclass override point) stable.
        """
        return await _start_turn(self, params, emitter)

    async def _record_pending_resume_intent(
        self,
        thread_id: str,
        resume_intent: dict[str, Any],
    ) -> None:
        await _record_pending_resume_intent(self, thread_id, resume_intent)

    async def _consume_confirmed_resume_intent(
        self,
        thread_id: str,
        text: str,
    ) -> dict[str, Any] | None:
        return await _consume_confirmed_resume_intent(self, thread_id, text)

    async def _consume_paused_task_resume_intent(
        self,
        thread_id: str,
        text: str,
    ) -> dict[str, Any] | None:
        return await _consume_paused_task_resume_intent(self, thread_id, text)

    async def handle_request(
        self,
        method: str,
        params: dict[str, Any],
        emitter: EventEmitter,
    ) -> Any:
        return await _handle_request(self, method, params, emitter)

    async def compact_thread(
        self,
        thread_id: str,
        emitter: EventEmitter | None = None,
    ) -> dict[str, Any]:
        """Manually compact a thread (body in ``realtime_thread_ops``)."""
        return await compact_thread(self, thread_id, emitter)

    async def _handle_hunk_decide(
        self,
        params: dict[str, Any],
        emitter: EventEmitter,
    ) -> dict[str, Any]:
        return await _handle_hunk_decide(self, params, emitter)

    def _resolve_hunk_path(self, thread_id: str, path_value: str) -> Path:
        return _resolve_hunk_path(self, thread_id, path_value)

    # ── Drivers ───────────────────────────────────────────────
    # Bodies live in ``realtime_react_stream`` / ``realtime_team_stream``;
    # these thin methods keep the original call surface (and subclass
    # override points) stable.

    def _should_use_reflection_fast_path(
        self,
        text: str,
        params: TurnParams,
        *,
        conversation_messages: list[dict[str, object]] | None = None,
        has_resumable_task: bool = False,
        thread_id: str | None = None,
    ) -> bool:
        return _should_use_reflection_fast_path(
            self,
            text,
            params,
            conversation_messages=conversation_messages,
            has_resumable_task=has_resumable_task,
            thread_id=thread_id,
        )

    async def _drive_reflection_fast_path(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        intent: ParsedIntent,
        agent: Any,
        *,
        model: str | None = None,
    ) -> None:
        await _drive_reflection_fast_path(self, turn, log, emitter, intent, agent, model=model)

    def _try_reflex_reply(self, intent: ParsedIntent) -> str | None:
        return _try_reflex_reply(self, intent)

    async def _drive_team_topology(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        intent: ParsedIntent,
        *,
        text: str,
        topology_id: str,
    ) -> None:
        await _drive_team_topology(
            self, turn, log, emitter, intent, text=text, topology_id=topology_id
        )

    async def _drive_swarm_mesh(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        intent: ParsedIntent,
        *,
        text: str,
        topology_id: str = "",
    ) -> None:
        await _drive_swarm_mesh(
            self,
            turn,
            log,
            emitter,
            intent,
            text=text,
            topology_id=topology_id,
        )

    async def _drive_group_fanout(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        intent: ParsedIntent,
        *,
        text: str,
    ) -> None:
        await _drive_group_fanout(
            self,
            turn,
            log,
            emitter,
            intent,
            text=text,
        )

    async def _drive_react(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        intent: ParsedIntent,
        provider: ApprovalProvider,
        agent: Any,
        *,
        model: str | None = None,
    ) -> None:
        await _drive_react(
            self,
            turn,
            log,
            emitter,
            intent,
            provider,
            agent,
            model=model,
        )

    async def _apply_react_event(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        state: _ReactBridgeState,
        evt: dict[str, Any],
    ) -> None:
        await _apply_react_event(self, turn, log, emitter, state, evt)

    async def _emit_item_started(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        item: Any,
    ) -> None:
        await _emit_item_started(self, turn, log, emitter, item)

    async def _emit_item_completed(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        item: Any,
    ) -> None:
        await _emit_item_completed(self, turn, log, emitter, item)

    async def _emit_agent_message(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        text: str,
    ) -> None:
        await _emit_agent_message(self, turn, log, emitter, text)

    async def _emit_todo_list(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        item: TodoListItem,
    ) -> None:
        await _emit_todo_list(self, turn, log, emitter, item)

    async def _emit_reasoning(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        item: ReasoningItem,
    ) -> None:
        await _emit_reasoning(self, turn, log, emitter, item)

    async def _drive_project_os(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        intent: ParsedIntent,
        *,
        thread_id: str,
        text: str,
    ) -> None:
        """Handle an explicit ``/project`` command for a cowork thread."""
        await _drive_project_os(
            self,
            turn,
            log,
            emitter,
            intent,
            thread_id=thread_id,
            text=text,
        )

    def _is_codex_app_server_partner(self, agent: Any) -> bool:
        """Return whether the selected role uses embedded Codex App Server."""
        from runtime.sensing.gateway.realtime_codex_backend import (
            agent_is_codex_app_server_partner,
        )

        return agent_is_codex_app_server_partner(agent)

    async def _drive_codex_app_server(
        self,
        turn: Turn,
        log: EventLog,
        emitter: EventEmitter,
        intent: ParsedIntent,
        agent: Any,
        provider: ApprovalProvider,
        *,
        text: str,
    ) -> bool:
        """Drive one outer turn through an isolated Codex App Server.

        Returns ``True`` when App Server owned the inner turn. ``False`` is
        reserved for a pre-turn compatibility fallback to hardened
        ``codex exec``; the lifecycle uses it to keep later steering on the
        same execution engine.
        """
        from runtime.sensing.gateway.realtime_codex_backend import (
            drive_codex_app_server,
        )

        return await drive_codex_app_server(
            self,
            turn,
            log,
            emitter,
            intent,
            agent,
            provider,
            text=text,
        )


# Static check: this class fulfills the realtime contract.
_: RealtimeRuntime = CerebrumRuntime.__new__(CerebrumRuntime)  # type: ignore[arg-type]
del _


__all__ = ["CerebrumRuntime", "GatewayApprovalProvider"]
