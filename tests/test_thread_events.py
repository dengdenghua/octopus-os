"""thread/events — raw sequenced log slices for client-side replay.

Covers the contract the frontend replay (``replay.ts``) depends on:

- events arrive with 1-based contiguous physical sequences and eventIds;
- incremental fetches (``afterSequence``) return only what the client missed;
- the same ``eventId`` stamps the live notification and the persisted log
  line — the dedupe key that makes live + replay composition safe;
- a foreign ``eventStreamId`` forces ``requiresReset`` with a full log;
- ``limit`` pages by sequence with a resumable cursor;
- drift-check metadata (turnCount/lastTurn*) describes the same snapshot.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    FastAPI = None  # type: ignore[assignment]
    TestClient = None  # type: ignore[assignment]

from runtime.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    Notification,
    decode_message,
    encode_message,
)

pytestmark = pytest.mark.skipif(
    FastAPI is None, reason="fastapi required for realtime gateway tests"
)


_SCRIPT: list[dict[str, Any]] = []


@pytest.fixture(autouse=True)
def _patch_react_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic fake planner — same seam test_realtime_cerebrum uses."""
    import runtime.core.cerebrum.react_loop as rl

    def fake_stream(*args: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
        yield from _SCRIPT[:]

    monkeypatch.setattr(rl, "stream_react_loop", fake_stream)


@pytest.fixture()
def gateway(tmp_path: Path) -> Any:
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    runtime = CerebrumRuntime(
        stack=object(),
        agent=object(),
        logs_root=str(tmp_path / "threads"),
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)
    with TestClient(app) as client:
        yield client


@pytest.fixture()
def gateway_with_instance(tmp_path: Path) -> Any:
    """Expose the gateway for watcher membership/refcount assertions."""
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway

    runtime = CerebrumRuntime(
        stack=object(),
        agent=object(),
        logs_root=str(tmp_path / "threads"),
    )
    gateway = RealtimeGateway(runtime=runtime, approval_timeout=5.0)
    app = FastAPI()
    app.include_router(gateway.router)
    with TestClient(app) as client:
        yield client, gateway


def _set_script(events: list[dict[str, Any]]) -> None:
    _SCRIPT.clear()
    _SCRIPT.extend(events)


def _drive(ws: Any, thread_id: str, text: str = "hi") -> list[Notification]:
    ws.send_text(
        encode_message(
            JsonRpcRequest(
                id=1,
                method="turn/start",
                params={
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": text}],
                    "approvalPolicy": "never",
                },
            )
        )
    )
    notifications: list[Notification] = []
    while True:
        msg = decode_message(ws.receive_text())
        if isinstance(msg, JsonRpcRequest):
            ws.send_text(encode_message(JsonRpcResponse(id=msg.id, result={"action": "accept"})))
            continue
        if isinstance(msg, Notification):
            notifications.append(msg)
            continue
        if isinstance(msg, JsonRpcResponse) and msg.id == 1:
            return notifications


def _fetch_events(
    ws: Any,
    thread_id: str,
    *,
    after_sequence: int | None = None,
    event_stream_id: str | None = None,
    limit: int | None = None,
    request_id: int = 42,
) -> dict[str, Any]:
    params: dict[str, Any] = {"threadId": thread_id}
    if after_sequence is not None:
        params["afterSequence"] = after_sequence
    if event_stream_id is not None:
        params["eventStreamId"] = event_stream_id
    if limit is not None:
        params["limit"] = limit
    ws.send_text(
        encode_message(JsonRpcRequest(id=request_id, method="thread/events", params=params))
    )
    while True:
        msg = decode_message(ws.receive_text())
        if isinstance(msg, JsonRpcResponse) and msg.id == request_id:
            assert msg.error is None, f"thread/events failed: {msg.error}"
            assert msg.result is not None
            return msg.result


def _drive_scripted_turn(gateway_client: Any, thread_id: str) -> list[Notification]:
    _set_script(
        [
            {"type": "text_delta", "delta": "hello "},
            {"type": "text_delta", "delta": "world"},
            {"type": "react_completed"},
        ]
    )
    with gateway_client.websocket_connect("/api/realtime") as ws:
        return _drive(ws, thread_id)


def test_full_fetch_returns_sequenced_events_with_ids(gateway: Any) -> None:
    _drive_scripted_turn(gateway, "th-events")
    with gateway.websocket_connect("/api/realtime") as ws:
        result = _fetch_events(ws, "th-events")

    events = result["events"]
    kinds = [e["event"] for e in events]
    assert kinds[0] == "thread_started"
    assert "turn_started" in kinds
    assert "item_delta" in kinds
    assert kinds[-1] == "turn_completed"

    sequences = [e["sequence"] for e in events]
    assert sequences == list(range(1, len(events) + 1))
    assert result["cursor"] == len(events)
    assert all(isinstance(e["eventId"], str) and e["eventId"] for e in events)

    assert result["requiresReset"] is False
    assert result["hasMore"] is False
    assert result["turnCount"] == 1
    assert result["lastTurnStatus"] == "completed"
    assert isinstance(result["streamId"], str)


def test_live_notification_and_log_line_share_event_id(gateway: Any) -> None:
    notifications = _drive_scripted_turn(gateway, "th-dedupe")
    notified = [n.params for n in notifications if n.method == "item/agentMessage/delta"]
    assert notified, "expected live agentMessage deltas"
    assert all(isinstance(p.get("eventId"), str) for p in notified)

    with gateway.websocket_connect("/api/realtime") as ws:
        result = _fetch_events(ws, "th-dedupe")
    logged = [
        e
        for e in result["events"]
        if e["event"] == "item_delta" and e["payload"]["kind"] == "agentMessage"
    ]
    assert {p["eventId"] for p in notified} == {e["eventId"] for e in logged}
    assert "".join(p["delta"] for p in notified) == "hello world"
    assert "".join(e["payload"]["delta"] for e in logged) == "hello world"


def test_incremental_fetch_returns_only_missed_events(gateway: Any) -> None:
    _drive_scripted_turn(gateway, "th-incr")
    with gateway.websocket_connect("/api/realtime") as ws:
        first = _fetch_events(ws, "th-incr")

    # A second turn on the same thread appends more events.
    _drive_scripted_turn(gateway, "th-incr")

    with gateway.websocket_connect("/api/realtime") as ws:
        incremental = _fetch_events(
            ws,
            "th-incr",
            after_sequence=first["cursor"],
            event_stream_id=first["streamId"],
        )

    assert incremental["requiresReset"] is False
    assert incremental["events"], "second turn must add events"
    assert incremental["events"][0]["sequence"] == first["cursor"] + 1
    kinds = [e["event"] for e in incremental["events"]]
    assert "turn_started" in kinds and kinds[-1] == "turn_completed"
    assert incremental["turnCount"] == 2
    assert incremental["cursor"] > first["cursor"]


def test_event_recovery_keeps_connection_live_for_every_watched_thread(
    gateway_with_instance: Any,
) -> None:
    """A thread/events-only reconnect remains a live terminal subscriber.

    Recovering a second thread changes the legacy last-resumed hint, so this
    also proves fan-out is driven by watched-thread membership. Repeating the
    first recovery must not inflate its wake-handler refcount.
    """
    client, gateway = gateway_with_instance
    _drive_scripted_turn(client, "th-events-live")
    _drive_scripted_turn(client, "th-events-other")

    with client.websocket_connect("/api/realtime") as watcher:
        _fetch_events(watcher, "th-events-live", request_id=51)
        _fetch_events(watcher, "th-events-live", request_id=52)
        _fetch_events(watcher, "th-events-other", request_id=53)
        assert gateway._wake_watch_refs == {  # noqa: SLF001
            "th-events-live": 1,
            "th-events-other": 1,
        }

        _drive_scripted_turn(client, "th-events-live")
        while True:
            message = decode_message(watcher.receive_text())
            if isinstance(message, Notification) and message.method == "turn/completed":
                break

        assert message.params["threadId"] == "th-events-live"
        assert message.params["turn"]["status"] == "completed"

    assert gateway._wake_watch_refs == {}  # noqa: SLF001


def test_foreign_stream_id_forces_full_reset(gateway: Any) -> None:
    _drive_scripted_turn(gateway, "th-stream")
    with gateway.websocket_connect("/api/realtime") as ws:
        full = _fetch_events(ws, "th-stream")
        result = _fetch_events(
            ws,
            "th-stream",
            after_sequence=full["cursor"],
            event_stream_id="stream_bogus",
        )

    assert result["requiresReset"] is True
    assert result["events"], "reset must serve the whole log"
    assert result["events"][0]["sequence"] == 1


def test_limit_pages_with_resumable_cursor(gateway: Any) -> None:
    _drive_scripted_turn(gateway, "th-paged")
    with gateway.websocket_connect("/api/realtime") as ws:
        full = _fetch_events(ws, "th-paged")
        total = len(full["events"])
        assert total >= 4, "fixture log too short for a paging test"

        page1 = _fetch_events(ws, "th-paged", limit=2, request_id=43)
        assert page1["hasMore"] is True
        assert len(page1["events"]) == 2
        assert page1["cursor"] == 2

        page2 = _fetch_events(
            ws, "th-paged", after_sequence=page1["cursor"], limit=total, request_id=44
        )
        assert page2["hasMore"] is False
        assert page2["events"][0]["sequence"] == 3
        assert page1["events"] + page2["events"] == full["events"]


def test_unknown_thread_events_returns_empty(gateway: Any) -> None:
    _drive_scripted_turn(gateway, "th-other")
    with gateway.websocket_connect("/api/realtime") as ws:
        result = _fetch_events(ws, "th-missing")

    assert result["events"] == []
    assert result["cursor"] == 0
    assert result["turnCount"] == 0
    assert result["lastTurnId"] is None


def test_coalesce_mode_shrinks_completed_items(gateway: Any) -> None:
    _drive_scripted_turn(gateway, "th-coalesce")
    with gateway.websocket_connect("/api/realtime") as ws:
        raw = _fetch_events(ws, "th-coalesce")
        ws.send_text(
            encode_message(
                JsonRpcRequest(
                    id=46,
                    method="thread/events",
                    params={"threadId": "th-coalesce", "mode": "coalesce"},
                )
            )
        )
        while True:
            msg = decode_message(ws.receive_text())
            if isinstance(msg, JsonRpcResponse) and msg.id == 46:
                assert msg.result is not None
                coalesced = msg.result
                break

    assert len(coalesced["events"]) < len(raw["events"])

    # Completed message: deltas dropped, the completed snapshot carries
    # the full text — state reconstructs identically.
    deltas = [
        e
        for e in coalesced["events"]
        if e["event"] == "item_delta" and e["payload"].get("kind") == "agentMessage"
    ]
    assert deltas == []
    completed = [e for e in coalesced["events"] if e["event"] == "item_completed"]
    assert completed
    texts = [
        e["payload"]["item"]["text"]
        for e in completed
        if e["payload"]["item"]["type"] == "agentMessage"
    ]
    assert "hello world" in texts

    # Drift metadata is unaffected by the mode.
    assert coalesced["cursor"] == raw["cursor"]
    assert coalesced["turnCount"] == raw["turnCount"]
    assert coalesced["lastTurnStatus"] == raw["lastTurnStatus"]

