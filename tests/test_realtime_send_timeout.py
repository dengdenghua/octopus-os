"""Bounded realtime projection when a WebSocket stops accepting writes."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from runtime.memory.threads.event_log import EventLog
from runtime.platform.models import ParsedIntent
from runtime.platform.process.thread_turn_claim import acquire_thread_turn_claim
from runtime.protocol import Notification, ServerMethod, Turn, TurnStatus
from runtime.sensing.gateway._realtime_detached_turn import _DetachedTurnEmitter
from runtime.sensing.gateway._realtime_gateway_connection import RpcConnection
from runtime.sensing.gateway.realtime_event_bridge import _ReactBridgeState
from runtime.sensing.gateway.realtime_gateway import RealtimeGateway


class _BlackholeWebSocket:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def send_text(self, _text: str) -> None:
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class _RecordingWatcher:
    def __init__(self, *thread_ids: str) -> None:
        self.watched_threads = set(thread_ids)
        self.last_resumed_thread_id: str | None = None
        self._closed = False
        self.notifications: list[tuple[str, dict[str, Any]]] = []
        self.notification_times: list[float] = []

    async def notify(self, method: ServerMethod | str, params: dict[str, Any]) -> None:
        value = method.value if isinstance(method, ServerMethod) else method
        self.notifications.append((value, params))
        self.notification_times.append(time.monotonic())


@pytest.mark.asyncio
async def test_detached_projection_fans_same_batch_out_concurrently() -> None:
    """Two black holes cost one send deadline and do not starve a fast peer."""

    thread_id = "thread-watcher-fanout"
    slow_websockets = [_BlackholeWebSocket(), _BlackholeWebSocket()]
    slow_connections = [
        RpcConnection(
            websocket,  # type: ignore[arg-type]
            outbound_send_timeout_seconds=0.15,
        )
        for websocket in slow_websockets
    ]
    for connection in slow_connections:
        connection.watched_threads.add(thread_id)
    fast = _RecordingWatcher(thread_id)
    owner = _RecordingWatcher()
    owner._closed = True  # noqa: SLF001 - force the reconnected-watcher path
    gateway = SimpleNamespace(_connections={*slow_connections, fast})
    emitter = _DetachedTurnEmitter(gateway, thread_id, owner)  # type: ignore[arg-type]

    started_at = time.monotonic()
    await asyncio.wait_for(
        emitter.notify(ServerMethod.TURN_HEARTBEAT, {"threadId": thread_id}),
        timeout=0.35,
    )
    elapsed = time.monotonic() - started_at
    await asyncio.sleep(0)

    # Sequential delivery would consume ~0.30s.  Concurrent delivery consumes
    # one ~0.15s deadline, while the healthy watcher receives immediately.
    assert elapsed < 0.25
    assert fast.notifications == [(ServerMethod.TURN_HEARTBEAT.value, {"threadId": thread_id})]
    assert fast.notification_times[0] - started_at < 0.1
    assert all(websocket.started.is_set() for websocket in slow_websockets)
    assert all(websocket.cancelled.is_set() for websocket in slow_websockets)
    assert all(connection._closed for connection in slow_connections)  # noqa: SLF001


@pytest.mark.asyncio
async def test_blackhole_send_times_out_and_releases_queued_writer() -> None:
    websocket = _BlackholeWebSocket()
    connection = RpcConnection(
        websocket,  # type: ignore[arg-type]
        outbound_send_timeout_seconds=0.05,
    )
    message = Notification(method="turn/heartbeat", params={"turnId": "turn-slow"})

    first = asyncio.create_task(connection.send(message))
    await asyncio.wait_for(websocket.started.wait(), timeout=0.2)
    second = asyncio.create_task(connection.send(message))
    started_at = time.monotonic()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=0.3)
    elapsed = time.monotonic() - started_at
    await asyncio.sleep(0)

    assert elapsed < 0.2
    assert websocket.calls == 1
    assert websocket.cancelled.is_set()
    assert connection._closed is True  # noqa: SLF001
    assert connection.is_turn_interrupted("turn-slow") is True

    # Once closed, later projections do not wait for either the old write
    # lock or another timeout budget.
    later_started = time.monotonic()
    await connection.send(message)
    assert time.monotonic() - later_started < 0.02


class _ResidentToolRuntime:
    _max_iterations = 2
    _task_supervisor = None
    _workspaces = None
    _trace_store = None

    def __init__(
        self,
        logs_root: Path,
        *,
        durable_start: threading.Event,
    ) -> None:
        self._logs_root = logs_root
        self._stack = SimpleNamespace(journal=None)
        self._orchestrator_bridge_tasks: set[asyncio.Task[Any]] = set()
        self._durable_start = durable_start
        self._log = EventLog(logs_root / "thread-slow.jsonl")
        original_item_started = self._log.item_started

        def _record_item_started(*args: Any, **kwargs: Any) -> Any:
            event = original_item_started(*args, **kwargs)
            if kwargs.get("durable") is True:
                self._durable_start.set()
            return event

        self._log.item_started = _record_item_started  # type: ignore[method-assign]

    def _log_for(self, _thread_id: str) -> EventLog:
        return self._log

    def _make_bridge_state(self, *_args: Any, **_kwargs: Any) -> _ReactBridgeState:
        return _ReactBridgeState(enable_adaptive_batching=False)

    def _drain_turn_steering(self, _turn_id: str) -> list[str]:
        return []

    def _record_react_trace_event(self, _turn: Turn, _event: dict[str, Any]) -> None:
        return None

    async def _publish_discovered_steering(self, *_args: Any) -> None:
        return None

    async def start_turn(self, params: dict[str, Any], emitter: Any) -> Turn:
        from runtime.sensing.gateway._realtime_react_stream_drive import _drive_react

        thread_id = str(params["threadId"])
        turn = Turn(threadId=thread_id)
        emitter.register_turn(turn.id)
        try:
            self._log.thread_started(thread_id)
            self._log.turn_started(thread_id, turn)
            await _drive_react(
                self,
                turn,
                self._log,
                emitter,
                ParsedIntent(
                    raw="write the file",
                    intent_type="task",
                    normalized_goal="write the file",
                    user_context={},
                ),
                None,
                None,
            )
            turn.status = TurnStatus.COMPLETED
            self._log.turn_completed(thread_id, turn.id, turn.status)
            return turn
        finally:
            emitter.unregister_turn(turn.id)


@pytest.mark.asyncio
async def test_resident_tool_crosses_durable_boundary_after_owner_send_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime.core.cerebrum.react_loop as react_loop
    import runtime.sensing.gateway._realtime_react_stream_drive as drive

    durable_start = threading.Event()
    side_effect = threading.Event()

    def _tool_stream(*_args: Any, **_kwargs: Any):
        yield {
            "type": "tool_start",
            "tool_name": "write_file",
            "tool_call_id": "call-slow-owner",
            "input_preview": {"path": "result.txt"},
        }
        # Advancing beyond tool_start represents executing the real tool.
        assert durable_start.is_set()
        side_effect.set()
        yield {"type": "react_completed"}

    real_apply = drive._apply_react_event

    async def _apply_tool_start_and_terminal(
        runtime: Any,
        turn: Turn,
        log: EventLog,
        emitter: Any,
        state: Any,
        event: dict[str, Any],
    ) -> None:
        if event.get("type") == "tool_start":
            await real_apply(runtime, turn, log, emitter, state, event)
        elif event.get("type") == "react_completed":
            turn.status = TurnStatus.COMPLETED

    monkeypatch.setattr(react_loop, "stream_react_loop", _tool_stream)
    monkeypatch.setattr(drive, "_should_use_native_tool_loop", lambda *_a, **_k: False)
    monkeypatch.setattr(drive, "_apply_react_event", _apply_tool_start_and_terminal)

    runtime = _ResidentToolRuntime(tmp_path / "threads", durable_start=durable_start)
    gateway = RealtimeGateway(runtime=runtime, outbound_send_timeout_seconds=0.05)
    websocket = _BlackholeWebSocket()
    owner = RpcConnection(
        websocket,  # type: ignore[arg-type]
        shared_interrupts=gateway._shared_interrupts,  # noqa: SLF001
        outbound_send_timeout_seconds=0.05,
    )
    watcher = _RecordingWatcher("thread-slow")
    gateway._connections.add(watcher)  # type: ignore[arg-type]  # noqa: SLF001
    claim = gateway._acquire_thread_turn_claim("thread-slow")  # noqa: SLF001

    resident = asyncio.create_task(
        gateway._run_resident_turn(  # type: ignore[arg-type]  # noqa: SLF001
            "thread-slow",
            {"threadId": "thread-slow"},
            owner,
            claim,
        )
    )
    await asyncio.wait_for(websocket.started.wait(), timeout=0.3)
    assert durable_start.is_set()
    await asyncio.sleep(0.02)
    assert not side_effect.is_set()

    result = await asyncio.wait_for(resident, timeout=1.0)
    assert result.status is TurnStatus.COMPLETED
    assert side_effect.is_set()
    assert owner._closed is True  # noqa: SLF001

    watcher_methods = [method for method, _params in watcher.notifications]
    assert ServerMethod.ITEM_STARTED.value in watcher_methods
    assert ServerMethod.TURN_COMPLETED.value in watcher_methods

    # The resident finally released the authoritative descriptor; a new turn
    # can claim the same thread immediately instead of hanging behind I/O.
    replacement = acquire_thread_turn_claim(runtime._logs_root, "thread-slow")
    replacement.release()

