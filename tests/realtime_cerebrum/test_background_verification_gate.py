"""Verification-gate narrowing for completed-with-background turns.

Regression coverage for the audit finding that ``realtime_turn_lifecycle``
closed unverified code as COMPLETED whenever *any* background task was in
flight — an unrelated watcher / dev-server / poller could silently green
code changes that never went through the verification gate.

The fix has two halves, both covered here:

1. ``_background_task_is_verification`` (helpers) decides whether a tagged
   background task plausibly runs verification; only those tasks may trigger
   the completed-with-background bypass.
2. ``RealtimeEventBridge.track_background_tool`` tags each watcher task with
   its background command (``echo-background:<command>``) so the decision
   in (1) has real command text to match against.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any
from unittest.mock import MagicMock

import pytest

from runtime.protocol.items import CommandExecutionItem
from runtime.sensing.gateway._realtime_turn_lifecycle_helpers import (
    _background_task_is_verification,
)
from runtime.sensing.gateway.realtime_cerebrum import _ReactBridgeState


@pytest.mark.parametrize(
    ("task_name", "expected"),
    [
        # ── verification-looking commands → bypass allowed ─────────
        ("echo-background:pytest tests/ -q", True),
        ("echo-background:npm run test", True),
        ("echo-background:pnpm test", True),
        ("echo-background:yarn lint", True),
        ("echo-background:.venv/bin/python -m pytest tests/x.py", True),
        ("echo-background:python3 -m unittest discover", True),
        ("echo-background:ruff check runtime/", True),
        ("echo-background:make lint", True),
        ("echo-background:ninja test", True),
        ("echo-background:go test ./...", True),
        ("echo-background:tsc --noEmit", True),
        ("echo-background:cmake --build .", True),
        ("echo-background:pytest", True),
        # ── unrelated watchers / servers → gate runs normally ─────
        ("echo-background:vite --host", False),
        ("echo-background:pnpm dev --watch", False),
        ("echo-background:tail -f logs/app.log", False),
        ("echo-background:docker compose up", False),
        ("echo-background:node server.js", False),
        ("echo-background:python -m http.server", False),
        # ── edge cases ─────────────────────────────────────────────
        ("echo-background:", False),  # tagged but empty command
        ("", False),  # empty task name
        ("Task-123", True),  # untagged → pre-tagging behavior (hot reload)
    ],
)
def test_background_task_is_verification(task_name: str, expected: bool) -> None:
    assert _background_task_is_verification(task_name) is expected


@pytest.mark.asyncio()
async def test_track_background_tool_tags_watcher_with_command() -> None:
    """The bridge must tag the background watcher with its command so turn
    finalization can distinguish delegated verification from unrelated tasks."""
    state = _ReactBridgeState()

    class _FakeEmitter:
        interrupted: bool = False

        async def notify(self, *args: Any, **kwargs: Any) -> None:
            return None

        def is_turn_interrupted(self, turn_id: str) -> bool:
            return self.interrupted

    class _FakeLog:
        def item_delta(self, *args: Any, **kwargs: Any) -> Any:
            return MagicMock(event_id="e-1")

    turn = MagicMock()
    turn.thread_id = "th-1"
    turn.id = "turn-1"

    item = CommandExecutionItem(command="pytest tests/ --check")
    state.tools["c1"] = item

    emitter = _FakeEmitter()
    log = _FakeLog()
    await state.track_background_tool(
        turn,
        log,
        emitter,
        {
            "tool_call_id": "c1",
            "task_id": "bg-1",
            "snapshot": {"status": "running"},
        },
    )

    assert state.background_tasks, "expected a watcher task to be registered"
    watcher = state.background_tasks[-1]
    try:
        assert watcher.get_name().startswith("echo-background:")
        assert "pytest tests/ --check" in watcher.get_name()
    finally:
        # The watcher polls a fake background task that will never resolve;
        # cancel it so the test doesn't leave a dangling task behind.
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher

