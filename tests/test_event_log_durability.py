from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from runtime.memory.threads.event_log import EventLog, LoggedEvent
from runtime.protocol import CommandExecutionItem, Turn, TurnStatus
from runtime.sensing.gateway.realtime_event_bridge import _ReactBridgeState


def test_turn_terminal_event_is_fsynced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[int] = []
    monkeypatch.setattr("runtime.memory.threads.event_log.os.fsync", calls.append)
    log = EventLog(tmp_path / "terminal.jsonl")

    log.turn_completed("thread-1", "turn-1", TurnStatus.COMPLETED)

    assert len(calls) == 1


def test_cross_worker_interrupt_request_is_fsynced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[int] = []
    monkeypatch.setattr("runtime.memory.threads.event_log.os.fsync", calls.append)
    log = EventLog(tmp_path / "interrupt.jsonl")

    event = log.turn_interrupt_requested(
        "thread-1",
        "turn-1",
        claim_epoch="epoch-server-owned",
        requested_by_actor="alice",
        tenant_id="tenant-a",
    )

    assert event.event_id
    assert len(calls) == 1


def test_durable_tool_start_is_fsynced_but_regular_events_are_not(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[int] = []
    monkeypatch.setattr("runtime.memory.threads.event_log.os.fsync", calls.append)
    log = EventLog(tmp_path / "items.jsonl")
    item = CommandExecutionItem(command="write_file")

    log.append(LoggedEvent(event="thread_started", threadId="thread-1"))
    log.item_started("thread-1", "turn-1", item)
    log.item_delta("thread-1", "turn-1", item.id, "commandOutput", "chunk")
    log.item_completed("thread-1", "turn-1", item)
    assert calls == []

    log.item_started("thread-1", "turn-2", item, durable=True)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_tool_bridge_fsyncs_before_live_notification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    order: list[str] = []
    monkeypatch.setattr(
        "runtime.memory.threads.event_log.os.fsync",
        lambda _fd: order.append("fsync"),
    )

    class _Emitter:
        async def notify(self, *_args: Any, **_kwargs: Any) -> None:
            order.append("notify")

    turn = Turn(threadId="thread-order")
    state = _ReactBridgeState(enable_adaptive_batching=False)
    await state.start_tool(
        turn,
        EventLog(tmp_path / "order.jsonl"),
        _Emitter(),  # type: ignore[arg-type]
        {
            "type": "tool_start",
            "tool_call_id": "call-order",
            "tool_name": "write_file",
            "input_preview": {"path": "result.txt"},
        },
    )

    assert order[:2] == ["fsync", "notify"]

