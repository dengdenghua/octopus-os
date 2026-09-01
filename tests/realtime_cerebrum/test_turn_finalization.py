"""Live-path turn finalization contract.

Three invariants the frontend relies on:

* every terminal ``turn/completed`` snapshot carries a non-null
  ``completedAt`` (duration math falls back to ``startedAt`` → 0ms
  otherwise, diverging from the journal-replay view after refresh);
* an exception between ``turn/started`` and the driver dispatch still
  produces a terminal ``turn/completed`` (with the turn id) before the
  gateway's error notification — otherwise the turn stays inProgress
  for the rest of the session;
* task cancellation (connection teardown) journals the turn as
  INTERRUPTED and re-raises ``CancelledError`` instead of leaving an
  IN_PROGRESS turn for the next resume to reap.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from tests.realtime_cerebrum._helpers import drive as _drive
from tests.realtime_cerebrum._helpers import set_script as _set_script


def test_live_turn_completed_snapshot_has_completed_at(gateway: Any) -> None:
    client, _ = gateway
    _set_script(
        [
            {"type": "text_delta", "delta": "hello"},
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        result = _drive(
            ws,
            params={
                "threadId": "th_fin_done",
                "input": [{"type": "text", "text": "hi"}],
                "approvalPolicy": "never",
            },
        )
    completed = [n for n in result["notifications"] if n.method == "turn/completed"]
    assert completed, "expected a terminal turn/completed notification"
    turn = completed[-1].params["turn"]
    assert turn["status"] == "completed"
    assert turn["completedAt"], "live snapshot must carry completedAt"
    # The RPC result mirrors the notification snapshot.
    assert result["response"].result["turn"]["completedAt"]


def test_pre_driver_failure_emits_terminal_turn_completed(
    gateway: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime.sensing.gateway.realtime_turn_lifecycle as lifecycle

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("intent build exploded")

    monkeypatch.setattr(lifecycle, "_build_intent", boom)
    client, logs_root = gateway
    with client.websocket_connect("/api/realtime") as ws:
        result = _drive(
            ws,
            params={
                "threadId": "th_fin_boom",
                "input": [{"type": "text", "text": "hi"}],
                "approvalPolicy": "never",
            },
        )

    started = [n for n in result["notifications"] if n.method == "turn/started"]
    completed = [n for n in result["notifications"] if n.method == "turn/completed"]
    assert started and completed
    turn = completed[-1].params["turn"]
    assert turn["status"] == "failed"
    assert turn["id"] == started[0].params["turn"]["id"]
    assert turn["completedAt"]
    # The original gateway error path still runs after the snapshot.
    assert any(n.method == "error" for n in result["notifications"])
    assert result["response"].error is not None
    # The journal got the terminal event too (no stale IN_PROGRESS turn).
    events = _journal_events(logs_root)
    terminal = [e for e in events if e.get("event") == "turn_completed"]
    assert terminal and terminal[-1]["payload"]["status"] == "failed"


def test_paused_turn_keeps_distinct_resumable_status(gateway: Any) -> None:
    """A paused turn (react_paused followed by the loop's trailing
    react_completed with success=False) must remain resumable as
    ``paused`` — never flattened to ``interrupted`` or downgraded to
    ``failed``. Regression for the
    live session tUTpWExF1Q0N1_5580atdQ where the sidebar showed "失败"
    next to a "当前进度已暂停并保存" pause message.
    """
    client, _ = gateway
    _set_script(
        [
            {"type": "text_delta", "delta": "当前进度已暂停并保存，等待继续。"},
            {"type": "react_paused", "iteration": 1},
            {"type": "react_completed", "success": False},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        result = _drive(
            ws,
            params={
                "threadId": "th_fin_paused",
                "input": [{"type": "text", "text": "hi"}],
                "approvalPolicy": "never",
            },
        )
    completed = [n for n in result["notifications"] if n.method == "turn/completed"]
    assert completed, "expected a terminal turn/completed notification"
    turn = completed[-1].params["turn"]
    assert turn["status"] == "paused", (
        "a paused turn must stay explicitly resumable, not be flattened "
        f"to failed; got {turn['status']!r}"
    )
    assert turn["completedAt"]


@pytest.mark.asyncio()
async def test_cancelled_turn_journals_interruption_and_reraises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

    logs_root = tmp_path / "threads"
    runtime = CerebrumRuntime(stack=object(), agent=object(), logs_root=str(logs_root))

    class _Emitter:
        def __init__(self) -> None:
            self.notifications: list[tuple[str, dict[str, Any]]] = []

        async def notify(self, method: Any, params: dict[str, Any]) -> None:
            self.notifications.append((str(getattr(method, "value", method)), params))

        async def request_approval(
            self,
            method: Any,
            params: dict[str, Any],
            *,
            timeout: float | None = None,
        ) -> Any:
            raise AssertionError("no approval expected")

        def is_turn_interrupted(self, turn_id: str) -> bool:
            return False

        def register_turn(self, turn_id: str) -> None:
            pass

        def unregister_turn(self, turn_id: str) -> None:
            pass

    async def hang_forever(*args: Any, **kwargs: Any) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "_drive_react", hang_forever)

    emitter = _Emitter()
    task = asyncio.create_task(
        runtime.start_turn(
            {"threadId": "th_fin_cancel", "input": [{"type": "text", "text": "hi"}]},
            emitter,
        )
    )
    while not any(m == "turn/started" for m, _ in emitter.notifications):
        await asyncio.sleep(0.005)
    # Let the turn reach the (hanging) driver await before cancelling.
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    completed = [p for m, p in emitter.notifications if m == "turn/completed"]
    assert completed, "cancellation must still emit a best-effort turn/completed"
    turn = completed[-1]["turn"]
    assert turn["status"] == "interrupted"
    assert turn["completedAt"]

    events = _journal_events(logs_root)
    terminal = [e for e in events if e.get("event") == "turn_completed"]
    assert terminal and terminal[-1]["payload"]["status"] == "interrupted"
    # The turn id was released — a later resume won't see it as active.
    assert turn["id"] not in runtime._active_turn_ids


def _journal_events(logs_root: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for f in sorted(logs_root.glob("*.jsonl"))
        for line in f.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

