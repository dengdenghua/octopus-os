"""Durable Team Room message log + the /api/teams/{id}/messages read path."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.cowork.collaboration_store import CollaborationStore
from runtime.memory.cowork.room_messages import RoomMessageStore
from runtime.sensing.gateway.team_rooms_router import create_team_rooms_router


def test_append_assigns_monotonic_seq_per_room(tmp_path) -> None:
    s = RoomMessageStore(base_dir=tmp_path)
    assert s.append("r1", text="a") == 1
    assert s.append("r1", text="b") == 2
    assert s.append("r2", text="c") == 1  # seq restarts per room


def test_history_order_and_after_seq(tmp_path) -> None:
    s = RoomMessageStore(base_dir=tmp_path)
    for i in range(3):
        s.append("r1", text=f"m{i}", participant_id="p", display_name="P")
    assert [m["text"] for m in s.history("r1")] == ["m0", "m1", "m2"]
    assert [m["text"] for m in s.history("r1", after_seq=1)] == ["m1", "m2"]


def test_rooms_are_isolated(tmp_path) -> None:
    s = RoomMessageStore(base_dir=tmp_path)
    s.append("r1", text="x")
    s.append("r2", text="y")
    assert [m["text"] for m in s.history("r1")] == ["x"]
    assert [m["text"] for m in s.history("r2")] == ["y"]


def test_search_substring(tmp_path) -> None:
    s = RoomMessageStore(base_dir=tmp_path)
    s.append("r1", text="ship the nutrition report")
    s.append("r1", text="unrelated chatter")
    hits = s.search("r1", "nutrition")
    assert len(hits) == 1 and "nutrition" in hits[0]["text"]
    assert s.search("r1", "") == []


def test_survives_reopen(tmp_path) -> None:
    RoomMessageStore(base_dir=tmp_path).append("r1", text="durable")
    assert [m["text"] for m in RoomMessageStore(base_dir=tmp_path).history("r1")] == ["durable"]


def test_messages_endpoint_reads_store(tmp_path) -> None:
    store = RoomMessageStore(base_dir=tmp_path)
    store.append("team-x", text="hello", participant_id="p1", display_name="Alice")
    store.append("team-x", text="find me nutrition", participant_id="p2", display_name="Bob")

    app = FastAPI()
    app.include_router(
        create_team_rooms_router(
            state_path=tmp_path / "team_rooms.json",
            room_message_store=store,
        )
    )
    c = TestClient(app)

    body = c.get("/api/teams/team-x/messages").json()
    assert [m["text"] for m in body["messages"]] == ["hello", "find me nutrition"]

    # after_seq catch-up
    tail = c.get("/api/teams/team-x/messages", params={"after_seq": 1}).json()
    assert [m["text"] for m in tail["messages"]] == ["find me nutrition"]

    # search
    found = c.get("/api/teams/team-x/messages", params={"q": "nutrition"}).json()
    assert len(found["messages"]) == 1


def test_messages_endpoint_prefers_canonical_provider(tmp_path) -> None:
    store = RoomMessageStore(base_dir=tmp_path / "legacy")
    store.append("team-x", text="legacy line", participant_id="old", display_name="Old")

    app = FastAPI()
    app.include_router(
        create_team_rooms_router(
            state_path=tmp_path / "team_rooms.json",
            room_message_store=store,
            room_message_provider=lambda team_id, limit, after_seq, q: [
                {
                    "seq": 1,
                    "participant_id": "p1",
                    "display_name": "Alice",
                    "text": "canonical nutrition line",
                    "ts": "t0",
                }
            ],
        )
    )
    c = TestClient(app)

    body = c.get("/api/teams/team-x/messages").json()
    assert [m["text"] for m in body["messages"]] == ["canonical nutrition line"]


def test_room_message_projection_receives_ws_persist_lines(tmp_path) -> None:
    from runtime.sensing.gateway.team_rooms_ws import TeamRoomWsContext, _remember_line

    projected: list[tuple[str, dict]] = []
    ctx = TeamRoomWsContext(
        teams={},
        lock=__import__("threading").Lock(),
        live_sockets={},
        auth=lambda _request: None,
        save=lambda: None,
        broadcast=lambda *args, **kwargs: None,
        broadcast_presence=lambda _team_id: None,
        broadcast_floor=lambda _team_id, _team: None,
        active_participant=lambda _team_id, _participant_id: None,
        message_store=RoomMessageStore(base_dir=tmp_path / "legacy"),
        message_projection=lambda room_id, message: projected.append((room_id, message)),
    )

    _remember_line(ctx, "team-x", "p1", "Alice", "hello from ws")

    assert projected == [
        (
            "team-x",
            {
                "participant_id": "p1",
                "display_name": "Alice",
                "text": "hello from ws",
            },
        )
    ]


def test_persist_pool_append_persists(tmp_path) -> None:
    """The WS path offloads store.append to a background worker — verify that
    pooled append actually persists (no deadlock off the event loop)."""
    from runtime.sensing.gateway.team_rooms_ws import _persist_pool

    store = RoomMessageStore(base_dir=tmp_path)
    _persist_pool().submit(store.append, "r1", text="via pool").result(timeout=5)
    assert [m["text"] for m in store.history("r1")] == ["via pool"]


def test_plain_ws_message_persists_to_canonical_transcript_without_twin(tmp_path) -> None:
    canonical = CollaborationStore(base_dir=tmp_path / "canonical")
    legacy = RoomMessageStore(base_dir=tmp_path / "legacy")

    def project_message(room_id: str, message: dict) -> None:
        canonical.append_message_for_room(
            room_id,
            text=message["text"],
            participant_id=message["participant_id"],
            display_name=message["display_name"],
        )

    app = FastAPI()
    app.include_router(
        create_team_rooms_router(
            state_path=tmp_path / "team_rooms.json",
            room_message_store=legacy,
            room_message_projection=project_message,
            room_message_provider=lambda room_id, limit, after_seq, _q: canonical.messages_for_room(
                room_id,
                limit=limit,
                after_seq=after_seq,
            ),
            twin_responder=None,
        )
    )
    client = TestClient(app)
    room = client.post(
        "/api/teams",
        json={
            "name": "Canonical room",
            "thread_id": "thread-canonical",
            "members": [{"name": "general", "display_name": "General"}],
            "leaderId": "general",
        },
    ).json()
    room_id = room["id"]
    canonical.upsert_room("thread-canonical", room)

    with client.websocket_connect(
        f"/api/teams/{room_id}/ws?participant_id=owner-local&display_name=Owner"
    ) as ws:
        assert ws.receive_json()["type"] == "ready"
        assert ws.receive_json()["type"] == "presence"
        ws.send_json({"type": "message", "text": "ordinary durable line"})
        assert ws.receive_json()["text"] == "ordinary durable line"

    assert [message["text"] for message in canonical.messages_for_room(room_id)] == [
        "ordinary durable line"
    ]
    assert [
        message["text"]
        for message in client.get(f"/api/teams/{room_id}/messages").json()["messages"]
    ] == ["ordinary durable line"]

    # The legacy store remains a complete fallback/shadow for deployments
    # whose room has not yet been linked into CollaborationStore.
    from runtime.sensing.gateway.team_rooms_ws import _persist_pool

    _persist_pool().submit(lambda: None).result(timeout=5)
    assert [message["text"] for message in legacy.history(room_id)] == ["ordinary durable line"]

