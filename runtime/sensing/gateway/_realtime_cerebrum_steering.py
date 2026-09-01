"""Active-turn lease + steering management for the realtime runtime.

Split out of ``realtime_cerebrum.py``: the per-turn registry, the
on-disk active-turn lease files (used to detect a stale process reaping
in-progress turns) and the live steering queue that is the only
synchronization boundary between the asyncio RPC thread and the
native model/tool loop.

Every function takes the owning ``CerebrumRuntime`` as its first
argument; cross-method calls go through the runtime so subclass
overrides keep working.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import TYPE_CHECKING, Any

from runtime.memory.threads.event_log import EventLog
from runtime.protocol import ServerMethod, SteeringUserMessageItem, Turn
from runtime.sensing.gateway.realtime_gateway import EventEmitter

if TYPE_CHECKING:
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

_logger = logging.getLogger(__name__)


# Per-turn cap on sub-agent report injections into a running parent turn
# (dsh busy-owner ``inject`` flood guard). Reports beyond the budget stay in
# the durable ``pending_reports`` store and are delivered on the next wake or
# continuation instead of flooding the current turn's context with unbounded
# injected messages. Overridable via ECHO_MAX_TURN_STEERING_INJECTIONS.
_DEFAULT_MAX_TURN_STEERING_INJECTIONS = 20


def _max_turn_steering_injections() -> int:
    raw = os.environ.get("ECHO_MAX_TURN_STEERING_INJECTIONS", "").strip()
    if raw:
        try:
            value = int(raw)
            if value >= 0:
                return value
        except ValueError:  # expected · malformed env leaves the default
            pass
    return _DEFAULT_MAX_TURN_STEERING_INJECTIONS


# thread_id → (runtime, turn_id) for the single active turn per thread.
# Written by the asyncio RPC thread on turn register/unregister; read by
# worker threads (subagent report lane) so a queued child report can be
# injected into the running turn's next step (dsh ``inject``).
_THREAD_TURN_REGISTRY: dict[str, tuple[Any, str]] = {}
_THREAD_TURN_REGISTRY_LOCK = threading.Lock()


def _restored_steering(runtime: CerebrumRuntime) -> dict[str, deque[str]]:
    """Lazily support embedders/test runtimes created before this buffer."""

    restored = getattr(runtime, "_turn_steering_restored", None)
    if not isinstance(restored, dict):
        restored = {}
        runtime._turn_steering_restored = restored
    return restored


def _register_thread_turn(thread_id: str, runtime: CerebrumRuntime, turn_id: str) -> None:
    if not thread_id:
        return
    with _THREAD_TURN_REGISTRY_LOCK:
        _THREAD_TURN_REGISTRY[thread_id] = (runtime, turn_id)


def _unregister_thread_turn(thread_id: str, runtime: CerebrumRuntime, turn_id: str) -> None:
    if not thread_id:
        return
    with _THREAD_TURN_REGISTRY_LOCK:
        entry = _THREAD_TURN_REGISTRY.get(thread_id)
        if entry is not None and entry == (runtime, turn_id):
            # This lock is the injection linearization barrier.  Close intake
            # before removing the route so an injector either finishes before
            # teardown or observes a closed/missing turn and returns False.
            runtime._turn_steering_accepting[turn_id] = False
            del _THREAD_TURN_REGISTRY[thread_id]


def _inject_thread_steering(
    thread_id: str,
    text: str,
    *,
    source: str = "user",
) -> bool:
    """Queue ``text`` into the thread's running turn's next step (dsh inject).

    The react loop drains the turn's steering queue at its nearest step
    boundary (``steering_drain``), so a queued subagent report lands
    in-session while the parent is mid-turn — exactly dsh's busy-owner
    ``inject``. The durable copy stays in the subagent store
    (``pending_reports``), so a turn that ends before draining loses
    nothing; this is a live-only best-effort fast path.

    Returns True when queued into an accepting active turn, False when the
    thread has no such turn (report stays queued in the store until the
    next wake or continuation, as before).
    """
    with _THREAD_TURN_REGISTRY_LOCK:
        entry = _THREAD_TURN_REGISTRY.get(thread_id)
        if entry is None:
            return False
        runtime, turn_id = entry
        if not runtime._turn_steering_accepting.get(turn_id):
            return False
        queue = runtime._turn_steering.get(turn_id)
        if queue is None:
            return False
        budget = getattr(runtime, "_turn_steering_budget", None)
        if budget is None:
            # Runtime without budgeting configured (legacy / hand-rolled test
            # harness) → no per-turn cap, preserving prior behavior.
            remaining = _max_turn_steering_injections()
        else:
            remaining = budget.get(turn_id, _max_turn_steering_injections())
        if remaining <= 0:
            return False
        active = runtime._active_turns.get(turn_id)
        if active is None:
            # Teardown order is the reverse of what this guard once claimed:
            # ``_unregister_thread_turn`` closes intake and drops the registry
            # entry *under this same lock*, and only then does
            # ``_unregister_active_turn`` pop the active snapshot. So a racing
            # worker is already turned away by the accepting/registry guards
            # above, and reaching here means a caller mutated ``_active_turns``
            # out of band. Keep the guard as a belt-and-braces refusal — queuing
            # into an orphaned buffer would ack a durable report that never
            # landed — but decrement the budget only once the injection is
            # actually going to happen, so a refused attempt cannot burn a slot.
            return False
        if budget is not None:
            budget[turn_id] = remaining - 1
        item = SteeringUserMessageItem(
            text=text,
            targetTurnId=turn_id,
            source="subagent_report" if source == "subagent_report" else "user",
        )
        if active is not None:
            # Make the injection visible in the final turn snapshot. Appending
            # to a list is atomic under the GIL; the loop only reads this list
            # for steering sync, so a concurrent append is safe.
            active[0].items.append(item)
        # Mark the id as already-seen so the steering sync cannot discover
        # the durable log row below and deliver the same text twice.
        seen = runtime._turn_steering_seen.get(turn_id)
        if seen is not None:
            seen.add(item.id)
        notified = runtime._turn_steering_notified.get(turn_id)
        if notified is not None:
            notified.add(item.id)
        queue.put((item.id, item.text))
    if active is not None and active[1] is not None:
        # Durable mirror: replay/reconnect clients see the injection in the
        # thread's event log (EventLog.append is lock-protected, so a worker
        # thread may write). Live delivery already happened via the queue;
        # the log write is best-effort and never blocks the report.
        try:
            active[1].item_completed(thread_id, turn_id, item)
        except Exception:  # noqa: BLE001 — durability is best-effort
            _logger.debug("steering injection log write failed", exc_info=True)
    return True


def _register_turn_injector(thread_id: str) -> None:
    """Register this thread's live-injection hook on the subagent session store.

    Inverts the ``inject`` dependency: the execution layer (sessions.py) never
    imports the gateway — instead the gateway registers a callback the store
    invokes when a ``queued`` report should reach the running turn. Best-effort;
    a missing store (feature disabled) is a no-op.
    """
    try:
        from runtime.execution.subagents.sessions import get_subagent_session_store

        store = get_subagent_session_store()
        if store is None:
            return
        store.register_thread_injector(
            thread_id,
            lambda text: bool(
                _inject_thread_steering(
                    thread_id,
                    text,
                    source="subagent_report",
                )
            ),
        )
    except Exception:  # noqa: BLE001 — registration is best-effort
        pass


def _unregister_turn_injector(thread_id: str) -> None:
    """Drop the thread's live-injection hook (no-op when not registered)."""
    try:
        from runtime.execution.subagents.sessions import get_subagent_session_store

        store = get_subagent_session_store()
        if store is None:
            return
        store.unregister_thread_injector(thread_id)
    except Exception:  # noqa: BLE001 — best-effort
        pass


def _register_active_turn(runtime: CerebrumRuntime, turn: Turn, log: EventLog) -> None:
    runtime._active_turns[turn.id] = (turn, log)
    runtime._turn_steering[turn.id] = SimpleQueue()
    _restored_steering(runtime)[turn.id] = deque()
    runtime._turn_steering_seen[turn.id] = {
        item.id for item in turn.items if isinstance(item, SteeringUserMessageItem)
    }
    runtime._turn_steering_notified[turn.id] = set(runtime._turn_steering_seen[turn.id])
    runtime._turn_steering_last_sync[turn.id] = 0.0
    try:
        runtime._turn_steering_log_offsets[turn.id] = log.path.stat().st_size
    except OSError:
        runtime._turn_steering_log_offsets[turn.id] = 0
    runtime._turn_steering_accepting[turn.id] = True
    budget = getattr(runtime, "_turn_steering_budget", None)
    if budget is None:
        budget = runtime._turn_steering_budget = {}  # type: ignore[attr-defined]
    budget[turn.id] = _max_turn_steering_injections()
    _register_thread_turn(turn.thread_id, runtime, turn.id)
    _register_turn_injector(turn.thread_id)
    previous = max(
        (item for item in turn.items if item.timeline_sequence is not None),
        key=lambda item: item.timeline_sequence or 0,
        default=None,
    )
    runtime._turn_timeline[turn.id] = (
        previous.timeline_sequence or 0 if previous is not None else 0,
        previous.id if previous is not None else None,
    )
    _write_active_turn_lease(runtime, turn)

    async def _refresh_lease() -> None:
        try:
            while turn.id in runtime._active_turns:
                await asyncio.sleep(2.0)
                _write_active_turn_lease(runtime, turn)
        except asyncio.CancelledError:
            return

    runtime._active_turn_lease_tasks[turn.id] = asyncio.create_task(_refresh_lease())


def _unregister_active_turn(runtime: CerebrumRuntime, turn_id: str) -> None:
    active = runtime._active_turns.get(turn_id)
    if active is not None:
        # Retire the public injection route under its registry barrier before
        # removing the active snapshot/queue.  Deferred report workers that
        # arrive after this point fail injection and therefore leave their
        # durable report unacknowledged.
        _unregister_thread_turn(active[0].thread_id, runtime, turn_id)
        _unregister_turn_injector(active[0].thread_id)
        runtime._active_turns.pop(turn_id, None)
    runtime._turn_steering.pop(turn_id, None)
    _restored_steering(runtime).pop(turn_id, None)
    runtime._turn_steering_seen.pop(turn_id, None)
    runtime._turn_steering_notified.pop(turn_id, None)
    runtime._turn_steering_last_sync.pop(turn_id, None)
    runtime._turn_steering_log_offsets.pop(turn_id, None)
    runtime._turn_steering_accepting.pop(turn_id, None)
    getattr(runtime, "_turn_steering_budget", {}).pop(turn_id, None)
    runtime._turn_timeline.pop(turn_id, None)
    task = runtime._active_turn_lease_tasks.pop(turn_id, None)
    if task is not None:
        task.cancel()
    _remove_active_turn_lease(runtime, turn_id)


async def _drain_active_turns_for_shutdown(
    runtime: CerebrumRuntime,
    *,
    timeout_seconds: float = 3.0,
) -> dict[str, Any]:
    """Request checkpointed pauses and briefly drain live turns on shutdown."""
    from runtime.core.cerebrum.pause_control import get_pause_controller

    active_turns = list(runtime._active_turns.values())
    if not active_turns:
        return {"requested": [], "drained": [], "remaining": []}

    controller = get_pause_controller()
    react_by_thread: dict[str, list[Any]] = {}
    for active in controller.list_active():
        react_by_thread.setdefault(active.thread_id, []).append(active)

    requested: list[str] = []
    target_turn_ids: set[str] = set()
    for turn, _log in active_turns:
        candidates = react_by_thread.get(turn.thread_id, [])
        task_id = str(turn.task_id or "").strip()
        if not task_id and len(candidates) == 1:
            task_id = candidates[0].task_id
        if not task_id:
            continue
        matching = next((item for item in candidates if item.task_id == task_id), None)
        controller.request_pause(
            task_id,
            reason="external",
            requested_by="server_shutdown",
            note="服务关闭前自动暂停；将在模型安全边界保存 checkpoint",
            thread_id=turn.thread_id,
            agent_id=matching.agent_id if matching is not None else "",
        )
        requested.append(task_id)
        target_turn_ids.add(turn.id)

    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while target_turn_ids.intersection(runtime._active_turns) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    remaining_turn_ids = sorted(target_turn_ids.intersection(runtime._active_turns))
    return {
        "requested": requested,
        "drained": sorted(target_turn_ids.difference(remaining_turn_ids)),
        "remaining": remaining_turn_ids,
    }


def _active_turn_lease_path(runtime: CerebrumRuntime, turn_id: str) -> Path:
    digest = hashlib.sha256(turn_id.encode("utf-8")).hexdigest()
    return runtime._active_turn_lease_root / f"{digest}.json"


def _write_active_turn_lease(runtime: CerebrumRuntime, turn: Turn) -> None:
    path = _active_turn_lease_path(runtime, turn.id)
    payload = {
        "turnId": turn.id,
        "threadId": turn.thread_id,
        "instanceId": runtime._instance_id,
        "updatedAt": time.time(),
        "acceptingSteering": runtime._turn_steering_accepting.get(turn.id, False),
    }
    temporary = path.with_suffix(f".{runtime._instance_id}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        _logger.warning("failed to refresh active-turn lease %s", turn.id, exc_info=True)
        with contextlib.suppress(OSError):
            temporary.unlink()


def _has_fresh_active_turn_lease(
    runtime: CerebrumRuntime,
    thread_id: str,
    turn_id: str,
    *,
    require_accepting_steering: bool = False,
) -> bool:
    try:
        payload = json.loads(_active_turn_lease_path(runtime, turn_id).read_text(encoding="utf-8"))
        fresh = (
            payload.get("turnId") == turn_id
            and payload.get("threadId") == thread_id
            and time.time() - float(payload.get("updatedAt") or 0) <= 8.0
        )
        return fresh and (
            not require_accepting_steering or payload.get("acceptingSteering") is True
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _remove_active_turn_lease(runtime: CerebrumRuntime, turn_id: str) -> None:
    path = _active_turn_lease_path(runtime, turn_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("instanceId") != runtime._instance_id:
            return
        path.unlink()
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return


def _set_turn_steering_accepting(runtime: CerebrumRuntime, turn: Turn, accepting: bool) -> None:
    # Share the registry barrier with ``_inject_thread_steering``.  Closing
    # intake and performing the lifecycle's final drain must be ordered with a
    # worker-thread subagent injection: an injector that wins this lock queues
    # before the drain, while one that loses observes ``False`` and leaves the
    # durable report unacked for the next turn.
    with _THREAD_TURN_REGISTRY_LOCK:
        if turn.id not in runtime._active_turns:
            return
        runtime._turn_steering_accepting[turn.id] = accepting
    _write_active_turn_lease(runtime, turn)


def _bind_turn_timeline(
    runtime: CerebrumRuntime,
    turn_id: str,
    item: Any,
    *,
    phase_id: str | None = None,
) -> None:
    sequence, previous_id = runtime._turn_timeline.get(turn_id, (0, None))
    if getattr(item, "timeline_sequence", None) is None:
        active = runtime._active_turns.get(turn_id)
        sequence = (
            active[1].reserve_timeline_sequence(turn_id) if active is not None else sequence + 1
        )
        item.timeline_sequence = sequence
    else:
        sequence = max(sequence, int(item.timeline_sequence))
    if getattr(item, "parent_item_id", None) is None:
        item.parent_item_id = previous_id
    if getattr(item, "phase_id", None) is None:
        item.phase_id = phase_id
    runtime._turn_timeline[turn_id] = (sequence, item.id)


def _sync_persisted_turn_steering(
    runtime: CerebrumRuntime,
    turn_id: str,
    *,
    force: bool = False,
) -> list[SteeringUserMessageItem]:
    active = runtime._active_turns.get(turn_id)
    if active is None:
        return []
    turn, log = active
    now = time.monotonic()
    with runtime._turn_steering_lock:
        last_sync = runtime._turn_steering_last_sync.get(turn_id, 0.0)
        if not force and now - last_sync < 0.1:
            return []
        runtime._turn_steering_last_sync[turn_id] = now
    with runtime._turn_steering_lock:
        offset = runtime._turn_steering_log_offsets.get(turn_id, 0)
        events, next_offset = log.tail_events(offset)
        runtime._turn_steering_log_offsets[turn_id] = next_offset
    discovered: list[SteeringUserMessageItem] = []
    pending = runtime._turn_steering.get(turn_id)
    if pending is None:
        return []
    incoming: list[SteeringUserMessageItem] = []
    for event in events:
        if event.event != "item_completed" or event.turn_id != turn_id:
            continue
        raw_item = event.payload.get("item")
        if not isinstance(raw_item, dict) or raw_item.get("type") != "steeringUserMessage":
            continue
        try:
            incoming.append(SteeringUserMessageItem.model_validate(raw_item))
        except (TypeError, ValueError):
            continue
    with runtime._turn_steering_lock:
        seen = runtime._turn_steering_seen.setdefault(turn_id, set())
        live_indexes = {item.id: index for index, item in enumerate(turn.items)}
        for item in incoming:
            existing_index = live_indexes.get(item.id)
            if existing_index is None:
                turn.items.append(item)
                live_indexes[item.id] = len(turn.items) - 1
            else:
                turn.items[existing_index] = item
            if item.id in seen:
                continue
            seen.add(item.id)
            sequence = item.timeline_sequence
            if sequence is None:
                sequence = log.reserve_timeline_sequence(turn_id)
                item.timeline_sequence = sequence
            current_sequence, _ = runtime._turn_timeline.get(turn_id, (0, None))
            runtime._turn_timeline[turn_id] = (max(current_sequence, sequence), item.id)
            pending.put((item.id, item.text))
            discovered.append(item)
    return discovered


async def _publish_discovered_steering(
    runtime: CerebrumRuntime,
    turn: Turn,
    emitter: EventEmitter,
) -> None:
    _sync_persisted_turn_steering(runtime, turn.id)
    with runtime._turn_steering_lock:
        notified = runtime._turn_steering_notified.setdefault(turn.id, set())
        pending = [
            item
            for item in turn.items
            if isinstance(item, SteeringUserMessageItem) and item.id not in notified
        ]
        notified.update(item.id for item in pending)
    for item in pending:
        await emitter.notify(
            ServerMethod.ITEM_COMPLETED,
            {
                "threadId": turn.thread_id,
                "turnId": turn.id,
                "item": item.model_dump(by_alias=True, mode="json"),
            },
        )


def _drain_turn_steering(runtime: CerebrumRuntime, turn_id: str) -> list[str]:
    _sync_persisted_turn_steering(runtime, turn_id, force=True)
    pending = runtime._turn_steering.get(turn_id)
    if pending is None:
        return []
    messages: list[str] = []
    with runtime._turn_steering_lock:
        restored = _restored_steering(runtime).setdefault(turn_id, deque())
        while restored:
            messages.append(restored.popleft())
        while True:
            try:
                _, text = pending.get_nowait()
            except Empty:
                break
            messages.append(text)
    return messages


def _restore_turn_steering(
    runtime: CerebrumRuntime,
    turn_id: str,
    messages: list[str],
) -> None:
    """Restore steering that an external backend proved it did not accept.

    The original ``SteeringUserMessageItem`` is already durable and visible;
    only the execution queue payload is restored, so the lifecycle can run it
    as a same-thread continuation without duplicating the UI item.
    """

    if turn_id not in runtime._turn_steering:
        return
    with runtime._turn_steering_lock:
        restored = _restored_steering(runtime).setdefault(turn_id, deque())
        restored.extend(
            message for message in messages if isinstance(message, str) and message.strip()
        )
