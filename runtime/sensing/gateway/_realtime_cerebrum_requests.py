"""JSON-RPC method dispatch for the realtime runtime.

Split out of ``realtime_cerebrum.py``: the ``handle_request`` body that
routes every ``item/*`` / ``thread/*`` / ``turn/*`` RPC method to its
handler. The owning class keeps a thin ``handle_request`` wrapper so the
``RealtimeRuntime`` interface (and subclass override points) stay stable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from runtime.memory.threads.event_log import EventLog
from runtime.protocol import (
    ItemStatus,
    JsonRpcErrorCode,
    ServerMethod,
    SteeringUserMessageItem,
    TurnStatus,
)
from runtime.sensing.gateway._realtime_thread_delete_probe import (
    claimed_runtime_thread_write,
)
from runtime.sensing.gateway.realtime_gateway import EventEmitter, _RpcError

if TYPE_CHECKING:
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime


def _subscribe_live_thread(emitter: EventEmitter, thread_id: str) -> None:
    """Establish the same-process live watch before cutting a replay snapshot.

    ``EventEmitter`` also has lightweight test/runtime implementations, so the
    transport-specific hook is capability-detected instead of being required
    by the general event-emitter protocol.
    """
    watch_thread = getattr(emitter, "watch_thread", None)
    if callable(watch_thread):
        watch_thread(thread_id)


async def _handle_request(
    runtime: CerebrumRuntime,
    method: str,
    params: dict[str, Any],
    emitter: EventEmitter,
) -> Any:
    if method == "turn/steer":
        thread_id = runtime._require_thread_id(params.get("threadId"))
        turn_id = params.get("turnId")
        text = params.get("text")
        item_id = params.get("itemId")
        if not isinstance(turn_id, str) or not turn_id:
            raise _RpcError(JsonRpcErrorCode.INVALID_PARAMS, "turn/steer requires turnId")
        if not isinstance(text, str) or not text.strip():
            raise _RpcError(JsonRpcErrorCode.INVALID_PARAMS, "turn/steer requires text")
        text = text.strip()
        if len(text) > 32_768:
            raise _RpcError(JsonRpcErrorCode.INVALID_PARAMS, "turn/steer text is too long")
        log = runtime._log_for(thread_id)
        turns = log.replay()
        runtime._require_thread_owner(
            log,
            getattr(emitter, "actor_id", None),
            turns=turns,
            access="write",
        )
        active = runtime._active_turns.get(turn_id)
        local_active = active is not None and turn_id in runtime._active_turn_ids
        if local_active:
            if not runtime._turn_steering_accepting.get(turn_id, False):
                raise _RpcError(JsonRpcErrorCode.INVALID_PARAMS, "target turn is finalizing")
            assert active is not None
            turn = active[0]
        else:
            if not runtime._has_fresh_active_turn_lease(
                thread_id,
                turn_id,
                require_accepting_steering=True,
            ):
                raise _RpcError(JsonRpcErrorCode.INVALID_PARAMS, "target turn is not active")
            found_turn = next((candidate for candidate in turns if candidate.id == turn_id), None)
            if found_turn is None or found_turn.status != TurnStatus.IN_PROGRESS:
                raise _RpcError(JsonRpcErrorCode.INVALID_PARAMS, "target turn is not active")
            turn = found_turn
        if turn.thread_id != thread_id:
            raise _RpcError(JsonRpcErrorCode.INVALID_PARAMS, "turn does not belong to thread")
        if isinstance(item_id, str) and item_id:
            existing = next((item for item in turn.items if item.id == item_id), None)
            if existing is not None:
                await emitter.notify(
                    ServerMethod.ITEM_COMPLETED,
                    {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "item": existing.model_dump(by_alias=True, mode="json"),
                    },
                )
                return {"turnId": turn_id, "itemId": item_id, "accepted": True}
        else:
            item_id = None
        item = SteeringUserMessageItem(
            **({"id": item_id} if item_id else {}),
            text=text,
            targetTurnId=turn_id,
        )
        if local_active:
            runtime._bind_turn_timeline(turn_id, item)
        else:
            previous = max(
                (candidate for candidate in turn.items if candidate.timeline_sequence is not None),
                key=lambda candidate: candidate.timeline_sequence or 0,
                default=None,
            )
            item.timeline_sequence = log.reserve_timeline_sequence(turn_id)
            item.parent_item_id = previous.id if previous is not None else None
        turn.items.append(item)
        if local_active:
            pending = runtime._turn_steering.get(turn_id)
            if pending is None:
                raise _RpcError(
                    JsonRpcErrorCode.INVALID_PARAMS,
                    "target turn is no longer active",
                )
            with runtime._turn_steering_lock:
                runtime._turn_steering_seen.setdefault(turn_id, set()).add(item.id)
                runtime._turn_steering_notified.setdefault(turn_id, set()).add(item.id)
            # Queue before the first socket await so the model cannot cross
            # a safe boundary while the UI notification is still in flight.
            pending.put((item.id, text))
        await runtime._emit_item_started(turn, log, emitter, item)
        item.status = ItemStatus.COMPLETED
        await runtime._emit_item_completed(turn, log, emitter, item)
        return {"turnId": turn_id, "itemId": item.id, "accepted": True}
    if method in ("thread/resume", "thread/read"):
        thread_id = runtime._require_thread_id(params.get("threadId"))
        log = runtime._log_for(thread_id)
        preflight_snapshot = log.snapshot()
        preflight_turns = log.replay(preflight_snapshot)
        runtime._require_thread_owner(
            log,
            getattr(emitter, "actor_id", None),
            turns=preflight_turns,
            access="read",
        )
        summary = log.summary(preflight_snapshot)
        if summary is not None and summary.archived:
            raise _RpcError(JsonRpcErrorCode.THREAD_NOT_FOUND, f"unknown thread {thread_id}")
        if method == "thread/resume" and preflight_snapshot.cursor > 0:
            # Authorization and archive checks happen first. Subscribe before
            # the immutable response prefix so a same-process append is either
            # in this snapshot or delivered live after it (duplicates are
            # reconciled by eventId on the client).
            _subscribe_live_thread(emitter, thread_id)
        # Close stale turns before capturing one immutable file prefix.
        # Cursor and replay then come from the exact same snapshot, so a
        # concurrent append is either wholly included or wholly deferred.
        runtime._resume_turns(log, turns=preflight_turns)
        snapshot = log.snapshot()
        turns = log.replay(snapshot)
        raw_after_sequence = params.get("afterSequence")
        requested_stream_id = params.get("eventStreamId")
        before_turn_id = (
            params.get("beforeTurnId") if isinstance(params.get("beforeTurnId"), str) else None
        )
        if (
            isinstance(raw_after_sequence, int)
            and not isinstance(raw_after_sequence, bool)
            and raw_after_sequence >= 0
            and before_turn_id is None
            and (
                not isinstance(requested_stream_id, str)
                or requested_stream_id == snapshot.stream_id
            )
        ):
            changed_ids, next_sequence, requires_reset = log.cursor_delta(
                raw_after_sequence,
                snapshot=snapshot,
            )
            if not requires_reset:
                changed = set(changed_ids)
                return {
                    "thread": {"id": thread_id, "path": str(log.path)},
                    "turns": [
                        turn.model_dump(by_alias=True, mode="json")
                        for turn in turns
                        if turn.id in changed
                    ],
                    "totalTurns": len(turns),
                    "hasMore": False,
                    "incremental": True,
                    "nextEventSequence": next_sequence,
                    "eventStreamId": snapshot.stream_id,
                }
        next_sequence = log.latest_sequence(snapshot=snapshot)
        raw_limit = params.get("limit")
        window, has_more = EventLog.paginate_turns(
            turns,
            limit=(
                raw_limit
                if isinstance(raw_limit, int) and not isinstance(raw_limit, bool)
                else None
            ),
            before_turn_id=before_turn_id,
        )
        last_turn = turns[-1] if turns else None
        return {
            "thread": {"id": thread_id, "path": str(log.path)},
            "turns": [t.model_dump(by_alias=True, mode="json") for t in window],
            "totalTurns": len(turns),
            # Keep the authoritative tail status beside the paginated
            # window.  A reconnect can otherwise mistake a locally cached
            # in-progress turn for live work after the server has already
            # reaped it as stale.
            "lastTurnId": last_turn.id if last_turn is not None else None,
            "lastTurnStatus": (last_turn.status.value if last_turn is not None else None),
            "hasMore": has_more,
            "incremental": False,
            "nextEventSequence": next_sequence,
            "eventStreamId": snapshot.stream_id,
        }
    if method == "thread/events":
        # Raw sequenced log slice for client-side replay (P2 of
        # docs/client-replay-design.md). Unlike thread/resume's
        # materialized turn snapshots, this ships the persisted events
        # themselves so a reconnecting client folds only what it missed.
        thread_id = runtime._require_thread_id(params.get("threadId"))
        log = runtime._log_for(thread_id)
        preflight_snapshot = log.snapshot()
        preflight_turns = log.replay(preflight_snapshot)
        runtime._require_thread_owner(
            log,
            getattr(emitter, "actor_id", None),
            turns=preflight_turns,
            access="read",
        )
        summary = log.summary(preflight_snapshot)
        if summary is not None and summary.archived:
            raise _RpcError(JsonRpcErrorCode.THREAD_NOT_FOUND, f"unknown thread {thread_id}")
        if preflight_snapshot.cursor > 0:
            _subscribe_live_thread(emitter, thread_id)
        # Close stale turns BEFORE capturing the immutable prefix, same
        # discipline as thread/resume: events and cursor below describe
        # the exact same file prefix, so a concurrent append is either
        # wholly included or wholly deferred to the next call.
        runtime._resume_turns(log, turns=preflight_turns)
        snapshot = log.snapshot()
        requested_stream_id = params.get("eventStreamId")
        raw_after = params.get("afterSequence")
        after = (
            raw_after
            if isinstance(raw_after, int) and not isinstance(raw_after, bool) and raw_after >= 0
            else 0
        )
        # Stream-id mismatch or an unsafe incremental window (compaction
        # inside it, or a cursor beyond the current file) means the
        # client must rebuild from scratch — serve the full log instead
        # of an interpretable slice.
        requires_reset = False
        if isinstance(requested_stream_id, str) and requested_stream_id != snapshot.stream_id:
            requires_reset = True
        elif after > 0:
            _, requires_reset = snapshot.cursor_delta(after)
        if requires_reset:
            after = 0
        raw_limit = params.get("limit")
        limit = (
            raw_limit
            if isinstance(raw_limit, int) and not isinstance(raw_limit, bool) and raw_limit > 0
            else None
        )
        coalesce = params.get("mode") == "coalesce"
        # Slice RAW events first; the limit counts raw entries so paging
        # covers the log at a steady rate regardless of coalescing.
        raw_slice = [(seq, event) for seq, event in snapshot.events if seq > after]
        has_more = False
        consumed_through = snapshot.cursor
        if limit is not None and len(raw_slice) > limit:
            raw_slice = raw_slice[:limit]
            has_more = True
            consumed_through = raw_slice[-1][0]
        # mode=coalesce shrinks full-log fetches (cold start / cache
        # backfill) without changing the state the slice rebuilds.
        # Replay-equivalence lives in coalesce_events' docstring — note
        # its eventId caveat: NEVER serve coalesced slices to a client
        # whose dedupe ledger may hold live-delivered ids.
        if coalesce:
            from runtime.memory.threads.event_log import coalesce_events

            raw_slice = coalesce_events(raw_slice)
        events = [
            {"sequence": sequence, **event.model_dump(by_alias=True, mode="json")}
            for sequence, event in raw_slice
        ]
        # Drift-check metadata: computed from the SAME snapshot, so the
        # client can verify its folded state against the authoritative
        # replay without a second round trip. Only meaningful on the
        # final page (has_more=False).
        turns = log.replay(snapshot)
        last_turn = turns[-1] if turns else None
        return {
            "thread": {"id": thread_id, "path": str(log.path)},
            "events": events,
            "cursor": consumed_through,
            "streamId": snapshot.stream_id,
            "requiresReset": requires_reset,
            "hasMore": has_more,
            "turnCount": len(turns),
            "lastTurnId": last_turn.id if last_turn is not None else None,
            "lastTurnStatus": last_turn.status.value if last_turn is not None else None,
        }
    if method == "thread/compact":
        thread_id = runtime._require_thread_id(params.get("threadId"))
        async with claimed_runtime_thread_write(runtime, thread_id):
            runtime._require_thread_owner(
                runtime._log_for(thread_id), getattr(emitter, "actor_id", None)
            )
            return await runtime.compact_thread(thread_id, emitter)
    if method == "thread/list":
        from runtime.memory.threads.event_log import list_threads

        include_archived = bool(params.get("includeArchived"))
        actor_id = getattr(emitter, "actor_id", None)
        summaries = list_threads(runtime._logs_root)
        items = []
        for summary in summaries:
            if not include_archived and summary.archived:
                continue
            log = runtime._log_for(summary.thread_id)
            try:
                runtime._require_thread_owner(log, actor_id, access="read")
            except _RpcError:
                continue
            items.append(summary.model_dump(by_alias=True, mode="json"))
        return {"threads": items}
    if method == "thread/archive":
        from runtime.memory.threads.event_log import archive_thread

        thread_id = runtime._require_thread_id(params.get("threadId"))
        async with claimed_runtime_thread_write(runtime, thread_id):
            runtime._require_thread_owner(
                runtime._log_for(thread_id), getattr(emitter, "actor_id", None)
            )
            if not archive_thread(runtime._logs_root, thread_id):
                raise _RpcError(JsonRpcErrorCode.THREAD_NOT_FOUND, f"unknown thread {thread_id}")
        return {"threadId": thread_id, "archived": True}
    if method == "item/fileChange/hunkDecide":
        return await runtime._handle_hunk_decide(params, emitter)
    raise _RpcError(JsonRpcErrorCode.METHOD_NOT_FOUND, method)
