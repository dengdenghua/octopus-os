"""Recovery subscription contracts for replay-to-live handoff.

These tests cover the same-process guarantee only. A different Uvicorn
worker needs a shared event-log tailer or pub/sub transport because live
WebSocket registries are intentionally process-local.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from runtime.memory.threads.event_log import archive_thread
from runtime.protocol import ServerMethod, Turn, TurnParams, TurnStatus
from runtime.sensing.gateway._realtime_detached_turn import _DetachedTurnEmitter
from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
from runtime.sensing.gateway.realtime_gateway import RealtimeGateway, _RpcError


class _RecordingConnection:
    """Small RpcConnection-compatible probe with a bound watch callback."""

    def __init__(
        self,
        gateway: RealtimeGateway,
        *,
        on_watch: Callable[[str], None] | None = None,
    ) -> None:
        self.actor_id: str | None = None
        self.tenant_id: str | None = None
        self.last_resumed_thread_id: str | None = None
        self.watched_threads: set[str] = set()
        self._closed = False
        self._gateway = gateway
        self._on_watch = on_watch
        self.notifications: list[tuple[str, dict[str, Any]]] = []
        self.approval_requests: list[tuple[str, dict[str, Any]]] = []

    def watch_thread(self, thread_id: str) -> None:
        self._gateway._watch_thread(thread_id, self)  # type: ignore[arg-type]  # noqa: SLF001
        if self._on_watch is not None:
            self._on_watch(thread_id)

    async def notify(self, method: ServerMethod | str, params: dict[str, Any]) -> None:
        value = method.value if isinstance(method, ServerMethod) else method
        self.notifications.append((value, params))

    async def request_approval(
        self,
        method: ServerMethod | str,
        params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, str]:
        del timeout
        value = method.value if isinstance(method, ServerMethod) else method
        self.approval_requests.append((value, params))
        return {"action": "accept"}


@pytest.fixture()
def recovery_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[CerebrumRuntime, RealtimeGateway]:
    from runtime.execution.subagents import sessions

    # The wake handler is orthogonal here. Keeping it absent makes refcount
    # assertions deterministic and avoids touching the process-global store.
    monkeypatch.setattr(sessions, "get_subagent_session_store", lambda: None)
    runtime = CerebrumRuntime(
        stack=object(),
        agent=object(),
        logs_root=str(tmp_path / "threads"),
    )
    return runtime, RealtimeGateway(runtime=runtime)


@pytest.mark.parametrize(
    ("method", "thread_id"),
    [
        ("thread/events", "events-before-snapshot"),
        ("thread/resume", "resume-before-snapshot"),
    ],
)
def test_recovery_subscribes_before_authoritative_snapshot(
    recovery_gateway: tuple[CerebrumRuntime, RealtimeGateway],
    method: str,
    thread_id: str,
) -> None:
    runtime, gateway = recovery_gateway
    injected = False
    runtime._log_for(thread_id).thread_started(thread_id)  # noqa: SLF001

    def append_during_subscription(watched_thread_id: str) -> None:
        nonlocal injected
        if injected:
            return
        injected = True
        runtime._log_for(watched_thread_id).thread_started(watched_thread_id)  # noqa: SLF001

    conn = _RecordingConnection(gateway, on_watch=append_during_subscription)
    result = asyncio.run(gateway._invoke(method, {"threadId": thread_id}, conn))  # type: ignore[arg-type]  # noqa: SLF001

    assert injected is True
    assert conn.watched_threads == {thread_id}
    assert gateway._wake_watch_refs == {thread_id: 1}  # noqa: SLF001
    if method == "thread/events":
        assert result["cursor"] == 2
        assert [event["event"] for event in result["events"]] == [
            "thread_started",
            "thread_started",
        ]
    else:
        assert result["nextEventSequence"] == 2


@pytest.mark.parametrize("failure", ["archived", "foreign-owner", "unknown"])
def test_rejected_event_recovery_never_subscribes(
    recovery_gateway: tuple[CerebrumRuntime, RealtimeGateway],
    failure: str,
) -> None:
    runtime, gateway = recovery_gateway
    thread_id = f"rejected-{failure}"
    log = runtime._log_for(thread_id)  # noqa: SLF001
    if failure != "unknown":
        log.thread_started(thread_id)
    if failure == "archived":
        assert archive_thread(runtime._logs_root, thread_id) is True  # noqa: SLF001
    elif failure == "foreign-owner":
        turn = Turn(
            threadId=thread_id,
            params=TurnParams(
                threadId=thread_id,
                input=[{"type": "text", "metadata": {"actor_id": "alice"}}],
            ),
        )
        log.turn_started(thread_id, turn)

    watch_calls: list[str] = []
    conn = _RecordingConnection(gateway, on_watch=watch_calls.append)
    if failure == "foreign-owner":
        conn.actor_id = "bob"

    if failure == "unknown":
        result = asyncio.run(
            gateway._invoke(  # type: ignore[arg-type]  # noqa: SLF001
                "thread/events", {"threadId": thread_id}, conn
            )
        )
        assert result["events"] == []
    else:
        with pytest.raises(_RpcError):
            asyncio.run(
                gateway._invoke(  # type: ignore[arg-type]  # noqa: SLF001
                    "thread/events",
                    {"threadId": thread_id},
                    conn,
                )
            )

    assert watch_calls == []
    assert conn.watched_threads == set()
    assert gateway._wake_watch_refs == {}  # noqa: SLF001


def test_event_resume_watch_is_idempotent_and_receives_live_and_terminal_fanout(
    recovery_gateway: tuple[CerebrumRuntime, RealtimeGateway],
) -> None:
    _runtime, gateway = recovery_gateway
    watcher = _RecordingConnection(gateway)

    async def subscribe() -> None:
        _runtime._log_for("thread-a").thread_started("thread-a")  # noqa: SLF001
        _runtime._log_for("thread-b").thread_started("thread-b")  # noqa: SLF001
        await gateway._invoke(  # type: ignore[arg-type]  # noqa: SLF001
            "thread/events", {"threadId": "thread-a"}, watcher
        )
        await gateway._invoke(  # type: ignore[arg-type]  # noqa: SLF001
            "thread/events", {"threadId": "thread-a"}, watcher
        )
        await gateway._invoke(  # type: ignore[arg-type]  # noqa: SLF001
            "thread/resume", {"threadId": "thread-a"}, watcher
        )
        await gateway._invoke(  # type: ignore[arg-type]  # noqa: SLF001
            "thread/events", {"threadId": "thread-b"}, watcher
        )

    asyncio.run(subscribe())

    assert watcher.watched_threads == {"thread-a", "thread-b"}
    assert watcher.last_resumed_thread_id == "thread-b"
    assert gateway._wake_watch_refs == {"thread-a": 1, "thread-b": 1}  # noqa: SLF001

    # The stale legacy hint deliberately disagrees with membership: the new
    # connection most recently recovered thread-b but remains subscribed to
    # live events from both threads.
    stale_legacy_only = _RecordingConnection(gateway)
    stale_legacy_only.last_resumed_thread_id = "thread-a"
    closed_owner = _RecordingConnection(gateway)
    closed_owner._closed = True  # noqa: SLF001
    origin = _RecordingConnection(gateway)
    gateway._connections.update({watcher, stale_legacy_only, origin})  # noqa: SLF001

    emitter_a = _DetachedTurnEmitter(gateway, "thread-a", closed_owner)  # type: ignore[arg-type]
    emitter_b = _DetachedTurnEmitter(gateway, "thread-b", closed_owner)  # type: ignore[arg-type]
    assert emitter_a._live_targets() == [watcher]  # noqa: SLF001
    assert emitter_b._live_targets() == [watcher]  # noqa: SLF001

    asyncio.run(
        emitter_a.notify(
            ServerMethod.ITEM_AGENT_MESSAGE_DELTA,
            {"threadId": "thread-a", "turnId": "turn-a", "delta": "live"},
        )
    )
    approval = asyncio.run(
        emitter_a.request_approval(
            "item/commandExecution/requestApproval",
            {"threadId": "thread-a", "turnId": "turn-a"},
            timeout=0.1,
        )
    )
    asyncio.run(
        gateway._emit_turn_completed(  # noqa: SLF001
            origin,
            "thread-a",
            Turn(threadId="thread-a", status=TurnStatus.COMPLETED),
        )
    )

    watcher_methods = [method for method, _params in watcher.notifications]
    assert ServerMethod.ITEM_AGENT_MESSAGE_DELTA.value in watcher_methods
    assert ServerMethod.TURN_COMPLETED.value in watcher_methods
    assert approval == {"action": "accept"}
    assert watcher.approval_requests == [
        (
            "item/commandExecution/requestApproval",
            {"threadId": "thread-a", "turnId": "turn-a"},
        )
    ]
    assert stale_legacy_only.notifications == []

    # Disconnect cleanup decrements each unique thread exactly once after any
    # number of repeated resume/events calls.
    for thread_id in list(watcher.watched_threads):
        gateway._unwatch_thread(thread_id)  # noqa: SLF001
    assert gateway._wake_watch_refs == {}  # noqa: SLF001

