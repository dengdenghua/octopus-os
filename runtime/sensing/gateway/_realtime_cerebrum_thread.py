"""Thread/session + emit helpers for the realtime runtime.

Split out of ``realtime_cerebrum.py``: thread id/owner validation,
turn replay/resume, the legacy ``ThreadStateStore`` snapshot bridge,
agent resolution, policy wrapping, thread bootstrap and the small
``item/*`` emission helpers the drivers rely on.

Every function takes the owning ``CerebrumRuntime`` as its first
argument; cross-method calls go through the runtime so subclass
overrides keep working.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from runtime.memory.threads.event_log import EventLog
from runtime.platform.models import ParsedIntent
from runtime.platform.models.primitives import now_utc
from runtime.protocol import (
    AgentMessageItem,
    ItemStatus,
    JsonRpcErrorCode,
    ReasoningItem,
    ServerMethod,
    TodoListItem,
    TurnParams,
    TurnStatus,
)
from runtime.safety.approval.approval_gate import ApprovalProvider
from runtime.safety.approval.approval_policy_store import load_policy
from runtime.sensing.gateway._realtime_thread_delete_probe import (
    assert_thread_accepts_runtime_writes,
)
from runtime.sensing.gateway.realtime_event_bridge import _ReactBridgeState, _safe_list_remove
from runtime.sensing.gateway.realtime_gateway import EventEmitter, _RpcError
from runtime.sensing.gateway.realtime_thread_history import (
    _flatten_turns_to_messages,
    _title_from_messages,
)
from runtime.sensing.gateway.thread_workspace import (
    MANAGED_WORKSPACE_MARKER,
    MANAGED_WORKSPACE_METADATA_KEY,
    strip_client_workspace_metadata,
)

if TYPE_CHECKING:
    from runtime.protocol import Turn
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

_logger = logging.getLogger(__name__)


def _make_bridge_state(
    runtime: CerebrumRuntime,
    thread_id: str,
    turn_id: str | None = None,
    agent: Any | None = None,
) -> _ReactBridgeState:
    """Build a ``_ReactBridgeState`` wired to the per-thread
    background-task registry, so the next turn on this thread can
    sweep any watchers the previous turn left running."""

    def _register(task: asyncio.Task[None]) -> None:
        bucket = runtime._thread_background_tasks.setdefault(thread_id, [])
        bucket.append(task)
        # Auto-clean when the task finishes naturally — keeps the
        # bucket bounded for long-lived threads.
        task.add_done_callback(lambda t: _safe_list_remove(bucket, t))

    binder = None
    if turn_id is not None:

        def _bind(item: Any, phase_id: str | None) -> None:
            runtime._bind_turn_timeline(turn_id, item, phase_id=phase_id)

        binder = _bind
    agent_id = str(getattr(agent, "agent_id", "") or "").strip()
    display_name = (
        str(
            getattr(agent, "display_name", None) or getattr(agent, "name", None) or agent_id or ""
        ).strip()
        or None
    )
    avatar_url = f"/api/agents/{agent_id}/avatar" if agent_id else None
    return _ReactBridgeState(
        on_background_task_start=_register,
        timeline_binder=binder,
        agent_display_name=display_name,
        agent_avatar_url=avatar_url,
        agent_icon=getattr(agent, "icon", None),
    )


async def _reap_stale_background_tasks(runtime: CerebrumRuntime, thread_id: str) -> None:
    """Cancel and reap any background watchers from prior turns
    on this thread before a new turn begins.

    Called at the top of ``start_turn``. ``done`` tasks are
    already pruned by the registration done-callback, so this
    loop only fires for actually-still-running watchers — the
    common case (new turn after the prior one finished cleanly)
    is a no-op.
    """
    bucket = runtime._thread_background_tasks.get(thread_id)
    if not bucket:
        return
    stale = [t for t in bucket if not t.done()]
    if not stale:
        runtime._thread_background_tasks.pop(thread_id, None)
        return
    for task in stale:
        task.cancel()
    for task in stale:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    # Drop the whole bucket; done-callbacks may still fire and
    # try to remove from a stale list, but ``_safe_list_remove``
    # tolerates missing entries.
    runtime._thread_background_tasks.pop(thread_id, None)


def _resolve_agent(runtime: CerebrumRuntime, params: TurnParams) -> Any:
    """Pick the agent for this turn.

    Lookup order:
      1. The persisted owner of an existing thread.
      2. Realtime input metadata (``agent_id`` / ``agent`` /
         ``agent_name``), including the nested ``context`` bag the
         web UI sends on ``turn/start``.
      3. The registry's match for that id, if any.
      4. The default agent passed at construction time.
      5. ``None``.

    The owner-first rule is a server-side isolation boundary.  Role switches
    create a new thread; a stale or forged per-turn value cannot make an
    existing conversation execute under a different persona.
    """
    from runtime.sensing.gateway.turn_session import thread_owner_agent_id

    agent_id = thread_owner_agent_id(
        thread_id=params.thread_id,
        store=runtime._thread_store,
    )
    if not agent_id:
        for block in params.input:
            md = block.get("metadata") if isinstance(block, dict) else None
            if not isinstance(md, dict):
                continue
            candidates: list[Any] = [md.get("agent_id"), md.get("agent"), md.get("agent_name")]
            context = md.get("context")
            if isinstance(context, dict):
                candidates.extend(
                    [
                        context.get("agent_id"),
                        context.get("agent"),
                        context.get("agent_name"),
                    ]
                )
            agent_id = next(
                (value.strip() for value in candidates if isinstance(value, str) and value.strip()),
                "",
            )
            if agent_id:
                break
    if agent_id and runtime._agent_registry is not None:
        try:
            if runtime._agent_registry.has(agent_id):
                return runtime._agent_registry.get(agent_id)
        except (AttributeError, TypeError, OSError):  # noqa: BLE001 — agent lookup failed; fall back to default
            pass
    return runtime._default_agent


def _wrap_with_policy(runtime: CerebrumRuntime, fallback: ApprovalProvider) -> ApprovalProvider:
    """Two-layer permission: static rules first, fallback otherwise.

    Reads ``permissions.json`` on every turn so UI-initiated edits
    (the "always trust" button) take effect immediately without
    bouncing the runtime. The file is small (a handful of rules)
    so the IO cost is irrelevant compared to a turn's LLM calls.
    """
    from pathlib import Path

    if runtime._policy_path is None:
        return fallback
    path = Path(runtime._policy_path)
    policy = load_policy(path)
    if not policy.rules:
        return fallback
    from runtime.safety.audit.trust_gateway import TrustGatewayApprovalProvider

    return TrustGatewayApprovalProvider(
        static_policy=policy,
        fallback=fallback,
        trace_store=getattr(runtime, "_trace_store", None),
        turn_id=getattr(fallback, "_turn_id", None),
        agent_id=getattr(getattr(runtime, "_default_agent", None), "agent_id", None),
    )


def _log_for(runtime: CerebrumRuntime, thread_id: str) -> EventLog:
    from runtime.memory.threads.event_log import thread_log_path

    return EventLog(thread_log_path(runtime._logs_root, thread_id))


def _require_thread_id(runtime: CerebrumRuntime, value: Any) -> str:
    from runtime.memory.threads.event_log import validate_thread_id

    if not isinstance(value, str):
        raise _RpcError(JsonRpcErrorCode.INVALID_PARAMS, "threadId required")
    try:
        return validate_thread_id(value)
    except ValueError as exc:
        raise _RpcError(JsonRpcErrorCode.INVALID_PARAMS, str(exc)) from exc


def _require_thread_owner(
    runtime: CerebrumRuntime,
    log: EventLog,
    actor_id: str | None,
    *,
    turns: list[Turn] | None = None,
    access: str = "owner",
) -> None:
    from runtime.memory.threads.event_log import owner_actor_id_from_turns

    resolver = getattr(runtime, "_thread_access_resolver", None)
    if callable(resolver):
        try:
            decision = resolver(log.path.stem, actor_id)
        except Exception:  # noqa: BLE001 - authorization fails closed
            decision = None
        if decision is not None:
            allowed = {
                "read": bool(getattr(decision, "can_read", False)),
                "write": bool(getattr(decision, "can_write", False)),
                "owner": bool(getattr(decision, "can_manage", False)),
            }.get(access, False)
            if allowed:
                return
            # A canonical ThreadState row is authoritative. Do not let stale
            # event-log ownership override a current room removal or tenant
            # denial. Log-only legacy threads keep the compatibility fallback.
            if getattr(decision, "thread", None) is not None:
                raise _RpcError(
                    JsonRpcErrorCode.THREAD_NOT_FOUND,
                    f"unknown thread {log.path.stem}",
                )

    owner = owner_actor_id_from_turns(turns if turns is not None else log.replay())
    if owner is not None and actor_id != owner:
        raise _RpcError(JsonRpcErrorCode.THREAD_NOT_FOUND, f"unknown thread {log.path.stem}")


def _resume_turns(
    runtime: CerebrumRuntime,
    log: EventLog,
    *,
    turns: list[Turn] | None = None,
) -> list[Turn]:
    """Replay and close in-progress turns only under the OS claim.

    The active-turn lease is a routing/diagnostic hint, never liveness
    authority.  A long GC pause can miss its heartbeat while the original
    worker still owns the descriptor; taking terminal action from that TTL
    would split the thread timeline.  Recovery therefore proceeds only after
    acquiring the same no-wait claim used by ``turn/start`` and holds it
    through every terminal append.
    """
    turns = turns if turns is not None else log.replay()
    if not turns:
        return turns
    candidates = [
        turn
        for turn in turns
        if turn.status == TurnStatus.IN_PROGRESS and turn.id not in runtime._active_turn_ids
    ]
    if not candidates:
        return turns

    from runtime.platform.process.thread_turn_claim import (
        ThreadTurnClaimConflict,
        ThreadTurnClaimUnavailable,
        acquire_thread_turn_claim,
    )

    thread_id = candidates[0].thread_id
    try:
        recovery_claim = acquire_thread_turn_claim(runtime._logs_root, thread_id)
    except ThreadTurnClaimConflict:
        # A live descriptor is conclusive even when its advisory lease is old.
        return turns
    except ThreadTurnClaimUnavailable:
        # Fail closed: a read/reconnect may fail, but it must never manufacture
        # a terminal result while serialization authority is unavailable.
        raise

    try:
        # The owner may have completed between the caller's preflight snapshot
        # and our claim acquisition. Re-read while holding the descriptor so
        # only genuinely orphaned in-progress rows are recovered.
        turns = log.replay()
        stale = [
            turn
            for turn in turns
            if turn.status == TurnStatus.IN_PROGRESS and turn.id not in runtime._active_turn_ids
        ]
        for turn in stale:
            from runtime.core.cerebrum.pause_control import get_pause_controller

            pause_candidates = [
                request
                for request in get_pause_controller().list_paused()
                if request.thread_id == turn.thread_id
                and (not turn.task_id or request.task_id == turn.task_id)
            ]
            if pause_candidates:
                selected = max(pause_candidates, key=lambda request: request.requested_at)
                turn.status = TurnStatus.PAUSED
                turn.task_id = selected.task_id if len(pause_candidates) == 1 else turn.task_id
                turn.objective_id = turn.task_id or turn.objective_id
                turn.outcome_reason = (
                    selected.reason if len(pause_candidates) == 1 else "ambiguous_pause_recovery"
                )
                turn.interrupt_reason = (
                    selected.note
                    if len(pause_candidates) == 1 and selected.note
                    else "检测到已保存的暂停检查点，可选择对应任务继续"
                )
                turn.completed_at = now_utc()
                for item in turn.items:
                    if item.status == ItemStatus.IN_PROGRESS:
                        item.status = ItemStatus.INTERRUPTED
                log.turn_updated(
                    turn.thread_id,
                    turn.id,
                    objective_id=turn.objective_id,
                    task_id=turn.task_id,
                    outcome_reason=turn.outcome_reason,
                )
                log.turn_completed(turn.thread_id, turn.id, turn.status)
                runtime._record_task_run_finished(turn, recover_stale_lease=True)
                continue
            turn.status = TurnStatus.FAILED
            turn.error = {
                "message": "上次执行在后端重启或连接中断时未完成，已自动结束。请重新发送或点击重试。",
                "code": "stale_in_progress_turn",
            }
            turn.completed_at = now_utc()
            for item in turn.items:
                if item.status == ItemStatus.IN_PROGRESS:
                    item.status = ItemStatus.FAILED
            log.turn_completed(turn.thread_id, turn.id, turn.status, error=turn.error)
            runtime._record_task_run_finished(turn, recover_stale_lease=True)
        return turns
    finally:
        recovery_claim.release()


def _snapshot_to_thread_store(
    runtime: CerebrumRuntime,
    thread_id: str,
    log: EventLog,
    intent: ParsedIntent | None = None,
    *,
    session_titles: Any = None,
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

    ``session_titles`` (optional) is a ``SessionTitleService``; when
    present, the first-completed-turn auto-title regeneration runs
    after the snapshot, still inside the same swallowed try block so
    a provider failure can never break the turn lifecycle.
    """
    store = runtime._thread_store
    if store is None:
        return
    try:
        turns = log.replay()
        messages, artifacts, todos = _flatten_turns_to_messages(turns)
        latest_turn = turns[-1] if turns else None
        thread_status = (
            {
                TurnStatus.IN_PROGRESS: "running",
                TurnStatus.PAUSED: "paused",
                TurnStatus.CANCELLED: "cancelled",
                TurnStatus.INTERRUPTED: "disconnected",
                TurnStatus.FAILED: "failed",
                TurnStatus.COMPLETED: "idle",
            }.get(latest_turn.status, "idle")
            if latest_turn is not None
            else "idle"
        )
        title = _title_from_messages(messages) or ""
        values: dict[str, Any] = {
            "title": title,
            "messages": messages,
            "artifacts": artifacts,
            "lifecycle": {
                "status": thread_status,
                "turn_id": latest_turn.id if latest_turn is not None else None,
                "objective_id": latest_turn.objective_id if latest_turn is not None else None,
                "task_id": latest_turn.task_id if latest_turn is not None else None,
                "checkpoint_id": latest_turn.checkpoint_id if latest_turn is not None else None,
                "outcome_reason": latest_turn.outcome_reason if latest_turn is not None else None,
            },
        }
        if todos is not None:
            values["todos"] = todos
        uc = (intent.user_context or {}) if intent is not None else {}
        metadata: dict[str, Any] = {}
        for key in (
            "mode",
            "agent",
            "agent_name",
            "workspace_path",
            "workspace_scope",
            "personal_workspace_path",
            "personal_workspace_enabled",
            "owner_actor_id",
            "actor_id",
            "tenant_id",
        ):
            v = uc.get(key) if isinstance(uc, dict) else None
            if v is not None:
                # ThreadStateStore.search() filters by ``metadata.agent``
                # so we normalise the key name the sidebar expects.
                metadata[
                    "agent"
                    if key == "agent_name"
                    else "owner_actor_id"
                    if key == "actor_id"
                    else key
                ] = v
        # A realtime turn carries client context, so it must not be able to
        # replace the filesystem allocation or owner previously established
        # by the authenticated thread HTTP boundary. Local/user-selected
        # workspaces have no server marker and retain their legacy behavior.
        existing = store.get(thread_id) if hasattr(store, "get") else None
        existing_metadata: dict[str, Any] = {}
        if isinstance(existing, dict):
            raw_existing_metadata = existing.get("metadata")
            if isinstance(raw_existing_metadata, dict):
                existing_metadata = raw_existing_metadata
        if existing_metadata.get(MANAGED_WORKSPACE_METADATA_KEY) == MANAGED_WORKSPACE_MARKER:
            metadata = strip_client_workspace_metadata(metadata)
            for protected_key in ("owner_actor_id", "tenant_id"):
                protected_value = existing_metadata.get(protected_key)
                if protected_value is not None:
                    metadata[protected_key] = protected_value
        store.ensure_thread(
            thread_id,
            metadata=metadata,
            values=values,
            status=thread_status,
        )
        store.update_state(
            thread_id,
            values=values,
            metadata=metadata if metadata else None,
            status=thread_status,
        )
        # Auto-title is a first-*completed*-turn enhancement. Running it for a
        # prompt that the user just cancelled both wastes another model call
        # and holds the live ``turn/completed`` notification behind that call,
        # making a 50 ms cancellation look several seconds slow in the UI.
        # Failed/paused/interrupted turns also must not consume the one-shot
        # ``title_auto_attempted`` marker before a real answer exists.
        if (
            session_titles is not None
            and latest_turn is not None
            and latest_turn.status is TurnStatus.COMPLETED
        ):
            session_titles.maybe_auto_refresh(thread_id)
    except Exception as exc:  # noqa: BLE001
        # Not fatal (the realtime event log is the durable record), but a
        # swallowed snapshot write used to silently freeze thread status /
        # updated_at in the sidebar. Surface at warning level so a broken
        # serialisation or store failure is at least visible in logs.
        _logger.warning(
            "snapshot to thread_store failed for %s (%s: %s); sidebar thread status may be stale",
            thread_id,
            type(exc).__name__,
            exc,
            exc_info=True,
        )


async def _ensure_thread(
    runtime: CerebrumRuntime, thread_id: str, emitter: EventEmitter
) -> EventLog:
    async with runtime._lock:
        assert_thread_accepts_runtime_writes(runtime, thread_id)
        log = runtime._log_for(thread_id)
        if thread_id in runtime._known_threads:
            return log
        existed = log.path.exists() and log.path.stat().st_size > 0
        if not existed:
            evt = log.thread_started(thread_id)
            await emitter.notify(
                ServerMethod.THREAD_STARTED,
                {
                    "thread": {"id": thread_id},
                    "threadId": thread_id,
                    "eventId": evt.event_id,
                },
            )
        runtime._known_threads.add(thread_id)
    return log


async def _emit_item_started(
    runtime: CerebrumRuntime,
    turn: Turn,
    log: EventLog,
    emitter: EventEmitter,
    item: Any,
) -> None:
    evt = log.item_started(turn.thread_id, turn.id, item)
    await emitter.notify(
        ServerMethod.ITEM_STARTED,
        {
            "threadId": turn.thread_id,
            "turnId": turn.id,
            "item": item.model_dump(by_alias=True, mode="json"),
            "eventId": evt.event_id,
        },
    )


async def _emit_item_completed(
    runtime: CerebrumRuntime,
    turn: Turn,
    log: EventLog,
    emitter: EventEmitter,
    item: Any,
) -> None:
    evt = log.item_completed(turn.thread_id, turn.id, item)
    await emitter.notify(
        ServerMethod.ITEM_COMPLETED,
        {
            "threadId": turn.thread_id,
            "turnId": turn.id,
            "item": item.model_dump(by_alias=True, mode="json"),
            "eventId": evt.event_id,
        },
    )


async def _emit_agent_message(
    runtime: CerebrumRuntime,
    turn: Turn,
    log: EventLog,
    emitter: EventEmitter,
    text: str,
) -> None:
    item = AgentMessageItem(text=text)
    turn.items.append(item)
    await runtime._emit_item_started(turn, log, emitter, item)
    item.status = ItemStatus.COMPLETED
    await runtime._emit_item_completed(turn, log, emitter, item)


async def _emit_todo_list(
    runtime: CerebrumRuntime,
    turn: Turn,
    log: EventLog,
    emitter: EventEmitter,
    item: TodoListItem,
) -> None:
    item.objective_id = turn.objective_id
    item.task_id = turn.task_id
    turn.items.append(item)
    await runtime._emit_item_started(turn, log, emitter, item)
    item.status = ItemStatus.COMPLETED
    await runtime._emit_item_completed(turn, log, emitter, item)


async def _emit_reasoning(
    runtime: CerebrumRuntime,
    turn: Turn,
    log: EventLog,
    emitter: EventEmitter,
    item: ReasoningItem,
) -> None:
    turn.items.append(item)
    await runtime._emit_item_started(turn, log, emitter, item)
    item.status = ItemStatus.COMPLETED
    await runtime._emit_item_completed(turn, log, emitter, item)
