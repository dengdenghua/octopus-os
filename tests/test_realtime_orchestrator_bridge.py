"""Tests for the orchestrator batch -> realtime SubagentItem bridge.

``bridge_orchestrator_batch`` renders parallel sub-agent tasks as live
``SubagentItem`` tiles on the realtime turn. This pins the live-progress
behaviour: while a task is in flight, each ``task_update`` that grows the
summary re-broadcasts ``item/started`` (so the workbench streams the running
text), and the terminal update emits ``item/completed``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from runtime.sensing.gateway._realtime_orchestrator_bridge import (
    bridge_orchestrator_batch,
)


class _FakeEmitter:
    def __init__(self) -> None:
        self.notified: list[tuple[str, dict[str, Any]]] = []

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        self.notified.append((method, params))


class _FakeLog:
    def __init__(self) -> None:
        self.started: int = 0
        self.completed: int = 0

    def item_started(self, *_args: Any, **_kwargs: Any) -> Any:
        self.started += 1
        return SimpleNamespace(event_id="started-1")

    def item_completed(self, *_args: Any, **_kwargs: Any) -> Any:
        self.completed += 1
        return SimpleNamespace(event_id="completed-1")


class _FakeOrchestrator:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def subscribe(self, batch_id: str, after_sequence: int = 0) -> Any:
        for evt in self._events:
            yield evt


def _task_update(
    task_id: str,
    *,
    status: str,
    message: str | None = None,
) -> Any:
    return SimpleNamespace(
        type="task_update",
        task_id=task_id,
        status=status,
        error=None,
        result_preview=message,
        message=message,
        subagent_name="researcher",
    )


def _batch_complete() -> Any:
    return SimpleNamespace(type="batch_complete")


def _turn() -> Any:
    return SimpleNamespace(id="trn-1", thread_id="th-1", items=[])


def _started_items(emitter: _FakeEmitter) -> list[dict[str, Any]]:
    return [params["item"] for method, params in emitter.notified if method == "item/started"]


def test_streams_growing_summary_live_and_completes() -> None:
    emitter = _FakeEmitter()
    log = _FakeLog()
    orchestrator = _FakeOrchestrator(
        [
            _task_update("task-1", status="running", message="已找到 1 篇"),
            _task_update("task-1", status="running", message="已找到 1 篇，正在阅读"),
            _task_update("task-1", status="completed", message="完成：三份报告"),
            _batch_complete(),
        ]
    )
    turn = _turn()

    asyncio.run(bridge_orchestrator_batch(orchestrator, "batch-1", turn, log, emitter))

    # item/started fires on spawn + each live summary growth; the terminal
    # update emits item/completed instead.
    started = _started_items(emitter)
    assert len(started) == 2
    summaries = [item["summary"] for item in started]
    assert summaries == ["已找到 1 篇", "已找到 1 篇，正在阅读"]
    # Only the terminal update emits item/completed.
    completed = [p for m, p in emitter.notified if m == "item/completed"]
    assert len(completed) == 1
    assert completed[0]["item"]["status"] == "completed"
    assert completed[0]["item"]["summary"] == "完成：三份报告"
    # The turn holds exactly one item (replaced in place, never duplicated).
    assert len(turn.items) == 1
    assert turn.items[0].summary == "完成：三份报告"
    assert log.started == 2
    assert log.completed == 1


def test_does_not_rebroadcast_unchanged_summary() -> None:
    emitter = _FakeEmitter()
    log = _FakeLog()
    orchestrator = _FakeOrchestrator(
        [
            _task_update("task-1", status="running", message="相同"),
            _task_update("task-1", status="running", message="相同"),
            _task_update("task-1", status="completed", message="相同"),
            _batch_complete(),
        ]
    )
    turn = _turn()

    asyncio.run(bridge_orchestrator_batch(orchestrator, "batch-1", turn, log, emitter))

    started = _started_items(emitter)
    # Spawn (1) + terminal completed, no redundant rebroadcast for the
    # unchanged middle update.
    assert len(started) == 1
    assert log.started == 1

