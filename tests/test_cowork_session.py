"""Unified CollaborationSession read-model + the event-sourced room link (#1)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.cowork.async_work import AsyncWorkStore
from runtime.memory.cowork.group import MemberEvent
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.presence import PresenceStore
from runtime.memory.cowork.room_messages import RoomMessageStore
from runtime.memory.cowork.session import link_room, resolve_session
from runtime.sensing.gateway.cowork_group_router import create_cowork_group_router


def test_room_link_event_sets_room_id(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    store.append(
        "t1", MemberEvent(action="invite", actor="u", target_id="alice", target_kind="agent")
    )
    assert store.state("t1").room_id is None
    link_room(store, "t1", "room-42")
    assert store.state("t1").room_id == "room-42"
    # event-sourced: replay before the link shows no room
    seq_before = store.events("t1")[0].seq
    assert store.state("t1", until_seq=seq_before).room_id is None


def test_resolve_session_composes_surfaces(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    aw = AsyncWorkStore(base_dir=store.base_dir, group_store=store)
    ps = PresenceStore(base_dir=tmp_path)
    store.append(
        "t1", MemberEvent(action="invite", actor="u", target_id="alice", target_kind="agent")
    )
    store.append("t1", MemberEvent(action="mode", actor="u", mode="swarm"))
    store.blackboard("t1").write("plan", "ship it", writer="alice")
    aw.assign("t1", "alice", "do x", actor="u")
    link_room(store, "t1", "room-9")
    rms = RoomMessageStore(base_dir=tmp_path)
    rms.append("room-9", text="hi from the room", participant_id="p1", display_name="Bob")

    s = resolve_session(store, "t1", async_store=aw, presence_store=ps, room_message_store=rms)
    assert s.session_id == "t1"
    assert s.room_id == "room-9"
    assert s.mode == "swarm"
    assert {m["id"] for m in s.roster} == {"alice"}
    assert s.blackboard["plan"] == "ship it"
    assert len(s.tasks) == 1
    assert any(m["member_id"] == "alice" for m in s.presence)
    # linked room's transcript is folded into the session view
    assert [m["text"] for m in s.room_messages] == ["hi from the room"]


def test_unlinked_session_has_no_room_messages(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    store.append(
        "t1", MemberEvent(action="invite", actor="u", target_id="alice", target_kind="agent")
    )
    rms = RoomMessageStore(base_dir=tmp_path)
    rms.append("room-9", text="orphan", participant_id="p", display_name="P")
    # no link → the room's messages are NOT pulled in
    s = resolve_session(store, "t1", room_message_store=rms)
    assert s.room_id is None and s.room_messages == [] and s.room_participants == []


def test_session_folds_room_participants_via_provider(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    store.append(
        "t1", MemberEvent(action="invite", actor="u", target_id="alice", target_kind="agent")
    )
    link_room(store, "t1", "room-9")
    s = resolve_session(
        store,
        "t1",
        room_participants_provider=lambda rid: (
            [{"id": "p1", "display_name": "Bob"}] if rid == "room-9" else []
        ),
    )
    assert [p["display_name"] for p in s.room_participants] == ["Bob"]


def test_session_folds_room_tasks_via_provider(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    store.append(
        "t1", MemberEvent(action="invite", actor="u", target_id="alice", target_kind="agent")
    )
    link_room(store, "t1", "room-9")
    s = resolve_session(
        store,
        "t1",
        room_tasks_provider=lambda rid: (
            [{"id": "task-1", "title": "ship report", "status": "running"}]
            if rid == "room-9"
            else []
        ),
    )
    assert [t["title"] for t in s.room_tasks] == ["ship report"]
    # unlinked → no room tasks even with a provider
    s2 = resolve_session(store, "t2", room_tasks_provider=lambda rid: [{"id": "x"}])
    assert s2.room_tasks == []


def test_collab_endpoint_includes_room_tasks(tmp_path) -> None:
    from runtime.sensing.gateway.team_tasks_router import (
        TeamTaskWire,
    )
    from runtime.sensing.gateway.team_tasks_router import (
        _save_state as _save_tasks,
    )

    # Seed the team_tasks store the cowork router will read.
    tt_path = tmp_path / "team_tasks.json"
    task = TeamTaskWire(
        id="task-1",
        room_id="room-9",
        title="evaluate the merger",
        status="running",
        created_at="t0",
        updated_at="t1",
    )
    other = TeamTaskWire(
        id="task-2",
        room_id="room-OTHER",
        title="unrelated",
        created_at="t0",
        updated_at="t0",
    )
    _save_tasks(tt_path, {"task-1": task, "task-2": other})

    app = FastAPI()
    app.include_router(
        create_cowork_group_router(
            store=GroupStore(base_dir=tmp_path),
            team_tasks_state_path=tt_path,
        )
    )
    c = TestClient(app)
    c.post("/api/cowork/t1/members", json={"target_id": "alice", "kind": "agent"})
    c.post("/api/collab/t1/link-room", json={"room_id": "room-9"})

    sess = c.get("/api/collab/t1").json()
    # only this room's tasks, not the other room's
    assert [t["title"] for t in sess["room_tasks"]] == ["evaluate the merger"]

    # …and they are findable via session-wide search (room_task kind)
    hits = c.get("/api/cowork/t1/search", params={"q": "merger"}).json()["hits"]
    rt = next(h for h in hits if h["kind"] == "room_task")
    assert rt["ref"]["task_id"] == "task-1" and rt["ref"]["status"] == "running"


def _client(tmp_path) -> TestClient:
    app = FastAPI()
    app.include_router(create_cowork_group_router(store=GroupStore(base_dir=tmp_path)))
    return TestClient(app)


def test_thread_id_validation_is_an_api_error_not_a_server_error(tmp_path) -> None:
    response = _client(tmp_path).get("/api/collab/-2494-9AMFYgjfm9Ym0uN")

    assert response.status_code == 400
    assert "threadId must be" in response.json()["detail"]


def test_collab_endpoints(tmp_path) -> None:
    rms = RoomMessageStore(base_dir=tmp_path / "rooms")
    app = FastAPI()
    app.include_router(
        create_cowork_group_router(
            store=GroupStore(base_dir=tmp_path),
            room_message_store=rms,
        )
    )
    c = TestClient(app)
    t = "thread-collab"
    c.post(f"/api/cowork/{t}/members", json={"target_id": "alice", "kind": "agent"})
    c.post(f"/api/cowork/{t}/blackboard", json={"key": "k", "value": "v"})
    rms.append("room-x", text="room line", participant_id="p", display_name="P")

    # link a room, then the unified session reflects it (incl. the transcript)
    assert c.post(f"/api/collab/{t}/link-room", json={"room_id": "room-x"}).status_code == 200
    sess = c.get(f"/api/collab/{t}").json()
    assert sess["session_id"] == t
    assert sess["room_id"] == "room-x"
    assert {m["id"] for m in sess["roster"]} == {"alice"}
    assert sess["blackboard"]["k"] == "v"
    assert [m["text"] for m in sess["room_messages"]] == ["room line"]
    assert "presence" in sess and "tasks" in sess


def test_post_room_message_requires_linked_room(tmp_path) -> None:
    app = FastAPI()
    app.include_router(
        create_cowork_group_router(
            store=GroupStore(base_dir=tmp_path),
            room_message_store=RoomMessageStore(base_dir=tmp_path / "rooms"),
        )
    )
    c = TestClient(app)
    # No room linked yet → the write side refuses (409), pointing at /link-room.
    r = c.post("/api/collab/t1/room-message", json={"text": "hello"})
    assert r.status_code == 409


def test_post_room_message_writes_into_session_transcript(tmp_path) -> None:
    from runtime.memory.cowork.collaboration_store import CollaborationStore

    rms = RoomMessageStore(base_dir=tmp_path / "rooms")
    collab_store = CollaborationStore(base_dir=tmp_path / "cowork")
    app = FastAPI()
    app.include_router(
        create_cowork_group_router(
            store=GroupStore(base_dir=tmp_path),
            collaboration_store=collab_store,
            room_message_store=rms,
        )
    )
    c = TestClient(app)
    t = "thread-write"
    assert c.post(f"/api/collab/{t}/link-room", json={"room_id": "room-w"}).status_code == 200

    r = c.post(
        f"/api/collab/{t}/room-message",
        json={"text": "summary from the group", "display_name": "Planner"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["room_id"] == "room-w" and body["seq"] == 1
    assert [m["text"] for m in collab_store.messages_for_session(t)] == ["summary from the group"]

    # The write lands in the SAME transcript the unified read surfaces.
    sess = c.get(f"/api/collab/{t}").json()
    assert [m["text"] for m in sess["room_messages"]] == ["summary from the group"]
    # …and is findable via session-wide search (room_message kind).
    hits = c.get(f"/api/cowork/{t}/search", params={"q": "summary"}).json()["hits"]
    assert any(h["kind"] == "room_message" for h in hits)


def test_collab_endpoint_includes_room_participants(tmp_path) -> None:
    from runtime.sensing.gateway.team_rooms_router import (
        TeamParticipantWire,
        TeamRoomWire,
        _save_state,
    )

    # Seed the team_rooms store the cowork router will read.
    tr_path = tmp_path / "team_rooms.json"
    room = TeamRoomWire(
        id="room-9",
        name="R",
        created_at="t0",
        updated_at="t0",
        participants=[TeamParticipantWire(id="p1", display_name="Bob", joined_at="t0")],
    )
    _save_state(tr_path, {"room-9": room})

    app = FastAPI()
    app.include_router(
        create_cowork_group_router(
            store=GroupStore(base_dir=tmp_path),
            team_rooms_state_path=tr_path,
        )
    )
    c = TestClient(app)
    c.post("/api/cowork/t1/members", json={"target_id": "alice", "kind": "agent"})
    c.post("/api/collab/t1/link-room", json={"room_id": "room-9"})

    sess = c.get("/api/collab/t1").json()
    assert sess["room_id"] == "room-9"
    assert [p["display_name"] for p in sess["room_participants"]] == ["Bob"]


def test_collab_room_endpoint_creates_and_links_persistent_room(tmp_path) -> None:
    from runtime.sensing.gateway.team_rooms_router import create_team_rooms_router

    store = GroupStore(base_dir=tmp_path / "cowork")
    rooms = create_team_rooms_router(state_path=tmp_path / "team_rooms.json")
    app = FastAPI()
    app.include_router(rooms)
    app.include_router(
        create_cowork_group_router(
            store=store,
            team_rooms_state_path=tmp_path / "team_rooms.json",
            team_rooms_router=rooms,
        )
    )
    c = TestClient(app)
    c.post("/api/cowork/t1/members", json={"target_id": "alice", "kind": "agent"})

    r = c.post(
        "/api/collab/t1/room",
        json={
            "name": "Unified launch",
            "mode": "swarm",
            "members": [{"name": "alice", "display_name": "Alice"}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is True
    assert body["room"]["name"] == "Unified launch"
    assert body["session"]["room_id"] == body["room"]["id"]
    assert body["session"]["mode"] == "swarm"

    # The server-owned room binding, not a mutable URL query, carries the
    # canonical thread through human invitation preview and acceptance.
    invite = c.post(
        f"/api/teams/{body['room']['id']}/invites",
        json={"role": "member", "expires_in_seconds": 3600},
    ).json()
    preview = c.get(f"/api/team-invites/{invite['invite_token']}").json()
    assert preview["thread_id"] == "t1"
    joined = c.post(
        f"/api/team-invites/{invite['invite_token']}/join",
        json={"participant_id": "human-bob", "display_name": "Bob"},
    ).json()
    assert joined["thread_id"] == "t1"

    # Idempotent: the same session keeps the same linked room instead of
    # creating another "team" path.
    again = c.post("/api/collab/t1/room", json={"name": "ignored"}).json()
    assert again["created"] is False
    assert again["room"]["id"] == body["room"]["id"]

    # GroupStore is the AI-membership authority. An atomic roster change is
    # immediately projected into the linked TeamRoom without dropping human
    # participants, its canonical thread, or retained display metadata.
    replaced = c.put(
        "/api/cowork/t1/roster",
        json={"agent_ids": ["alice", "critic"], "mode": "cluster"},
    )
    assert replaced.status_code == 200, replaced.json()
    assert replaced.json()["room_projection"] == {
        "ok": True,
        "room_id": body["room"]["id"],
    }
    projected = c.get(f"/api/teams/{body['room']['id']}").json()
    assert [member["name"] for member in projected["members"]] == ["alice", "critic"]
    assert projected["members"][0]["display_name"] == "Alice"
    assert projected["thread_id"] == "t1"
    assert any(item["display_name"] == "Bob" for item in projected["participants"])
    assert c.get(f"/api/team-invites/{invite['invite_token']}").json()["team"]["member_count"] == 2

    # Plugin-style single-member add/remove uses the same canonical projection
    # as bulk roster replacement and remains idempotent on retries.
    invited = c.post(
        "/api/cowork/t1/members",
        json={"target_id": "reviewer", "kind": "agent"},
    )
    assert invited.status_code == 200, invited.json()
    assert invited.json()["added"] is True
    assert invited.json()["room_projection"] == {
        "ok": True,
        "room_id": body["room"]["id"],
    }
    projected = c.get(f"/api/teams/{body['room']['id']}").json()
    assert [member["name"] for member in projected["members"]] == [
        "alice",
        "critic",
        "reviewer",
    ]

    removed = c.delete("/api/cowork/t1/members/critic")
    assert removed.status_code == 200, removed.json()
    assert removed.json()["removed"] is True
    assert [
        member["name"] for member in c.get(f"/api/teams/{body['room']['id']}").json()["members"]
    ] == ["alice", "reviewer"]


def test_collab_room_endpoint_backfills_legacy_thread_binding(tmp_path) -> None:
    from runtime.memory.cowork.collaboration_store import CollaborationStore
    from runtime.sensing.gateway.team_rooms_router import create_team_rooms_router

    store = GroupStore(base_dir=tmp_path / "cowork")
    collab_store = CollaborationStore(base_dir=tmp_path / "cowork")
    rooms = create_team_rooms_router(
        state_path=tmp_path / "team_rooms.json",
        room_projection=collab_store.upsert_room_by_id,
    )
    app = FastAPI()
    app.include_router(rooms)
    app.include_router(
        create_cowork_group_router(
            store=store,
            collaboration_store=collab_store,
            team_rooms_state_path=tmp_path / "team_rooms.json",
            team_rooms_router=rooms,
        )
    )
    client = TestClient(app)

    legacy_room = client.post(
        "/api/teams",
        json={
            "id": "legacy-room",
            "name": "Legacy room",
            "members": [{"name": "alice", "display_name": "Alice"}],
            # Public callers cannot choose this binding.
            "thread_id": "attacker-controlled",
        },
    ).json()
    assert legacy_room["thread_id"] is None
    link_room(store, "t1", legacy_room["id"])

    ensured = client.post("/api/collab/t1/room", json={"name": "ignored"})
    assert ensured.status_code == 200
    assert ensured.json()["created"] is False
    assert ensured.json()["room"]["thread_id"] == "t1"
    persisted = client.get(f"/api/teams/{legacy_room['id']}").json()
    assert persisted["thread_id"] == "t1"
    assert persisted["participants"][0]["role"] == "owner"
    assert collab_store.room_by_id(legacy_room["id"])["participants"][0]["role"] == "owner"

    invite = client.post(f"/api/teams/{legacy_room['id']}/invites").json()
    preview = client.get(f"/api/team-invites/{invite['invite_token']}").json()
    assert preview["thread_id"] == "t1"

    # The durable room reservation rejects a corrupt second group link before
    # any Team/Collaboration projection can move the established room.
    from runtime.memory.cowork.group_store import GroupRoomLinkConflict

    with pytest.raises(GroupRoomLinkConflict):
        link_room(store, "t2", legacy_room["id"])
    assert store.state("t2").room_id is None


def test_collab_task_endpoint_auto_creates_room_and_folds_task(tmp_path) -> None:
    from runtime.memory.cowork.collaboration_store import CollaborationStore
    from runtime.sensing.gateway.team_rooms_router import create_team_rooms_router
    from runtime.sensing.gateway.team_tasks_router import create_team_tasks_router

    store = GroupStore(base_dir=tmp_path / "cowork")
    collab_store = CollaborationStore(base_dir=tmp_path / "cowork")
    rooms = create_team_rooms_router(state_path=tmp_path / "team_rooms.json")
    tasks = create_team_tasks_router(state_path=tmp_path / "team_tasks.json")
    app = FastAPI()
    app.include_router(rooms)
    app.include_router(tasks)
    app.include_router(
        create_cowork_group_router(
            store=store,
            collaboration_store=collab_store,
            team_rooms_state_path=tmp_path / "team_rooms.json",
            team_tasks_state_path=tmp_path / "team_tasks.json",
            team_rooms_router=rooms,
            team_tasks_router=tasks,
        )
    )
    c = TestClient(app)
    c.post("/api/cowork/t1/members", json={"target_id": "alice", "kind": "agent"})

    r = c.post(
        "/api/collab/t1/tasks",
        json={
            "title": "Draft release plan",
            "description": "Use the unified collaboration path",
            "assignees": [{"kind": "agent", "ref": "alice"}],
            "room": {
                "name": "Release room",
                "members": [{"name": "alice", "display_name": "Alice"}],
            },
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["room_id"] == body["session"]["room_id"]
    assert body["task"]["metadata"]["collab_session_id"] == "t1"
    assert body["task"]["metadata"]["source"] == "collab_session"
    assert collab_store.room_for_session("t1")["name"] == "Release room"
    assert [task["title"] for task in collab_store.tasks_for_session("t1")] == [
        "Draft release plan"
    ]

    sess = c.get("/api/collab/t1").json()
    assert [task["title"] for task in sess["room_tasks"]] == ["Draft release plan"]
    listed = c.get("/api/collab/t1/tasks").json()
    assert listed["count"] == 1
    assert listed["tasks"][0]["room_id"] == body["room_id"]


def test_collab_session_prefers_unified_task_store(tmp_path) -> None:
    from runtime.memory.cowork.collaboration_store import CollaborationStore

    store = GroupStore(base_dir=tmp_path / "cowork")
    collab_store = CollaborationStore(base_dir=tmp_path / "cowork")
    collab_store.upsert_room("t1", {"id": "room-1", "name": "Canonical"})
    collab_store.upsert_task(
        "t1",
        {
            "id": "task-canonical",
            "room_id": "room-1",
            "title": "Canonical task",
            "status": "done",
            "created_at": "t0",
            "updated_at": "t1",
        },
    )
    app = FastAPI()
    app.include_router(
        create_cowork_group_router(
            store=store,
            collaboration_store=collab_store,
        )
    )
    c = TestClient(app)
    c.post("/api/collab/t1/link-room", json={"room_id": "room-1"})

    sess = c.get("/api/collab/t1").json()
    assert sess["room_id"] == "room-1"
    assert [task["title"] for task in sess["room_tasks"]] == ["Canonical task"]
    listed = c.get("/api/collab/t1/tasks").json()
    assert listed["tasks"][0]["id"] == "task-canonical"


def test_collab_session_prefers_unified_message_store(tmp_path) -> None:
    from runtime.memory.cowork.collaboration_store import CollaborationStore

    store = GroupStore(base_dir=tmp_path / "cowork")
    collab_store = CollaborationStore(base_dir=tmp_path / "cowork")
    collab_store.upsert_room("t1", {"id": "room-1", "name": "Canonical"})
    collab_store.append_message(
        "t1",
        room_id="room-1",
        text="canonical transcript line",
        participant_id="p1",
        display_name="Planner",
    )
    legacy = RoomMessageStore(base_dir=tmp_path / "rooms")
    legacy.append("room-1", text="legacy transcript line", participant_id="old", display_name="Old")

    app = FastAPI()
    app.include_router(
        create_cowork_group_router(
            store=store,
            collaboration_store=collab_store,
            room_message_store=legacy,
        )
    )
    c = TestClient(app)
    c.post("/api/collab/t1/link-room", json={"room_id": "room-1"})

    sess = c.get("/api/collab/t1").json()
    assert [m["text"] for m in sess["room_messages"]] == ["canonical transcript line"]
    hits = c.get("/api/cowork/t1/search", params={"q": "canonical"}).json()["hits"]
    assert any(h["kind"] == "room_message" for h in hits)


def test_team_room_projection_updates_unified_room_snapshot(tmp_path) -> None:
    from runtime.memory.cowork.collaboration_store import CollaborationStore
    from runtime.sensing.gateway.team_rooms_router import create_team_rooms_router

    collab_store = CollaborationStore(base_dir=tmp_path / "cowork")
    projected: list[str] = []

    def project(room: dict) -> None:
        updated = collab_store.upsert_room_by_id(room)
        if updated is not None:
            projected.append(updated["name"])

    rooms = create_team_rooms_router(
        state_path=tmp_path / "team_rooms.json",
        room_projection=project,
    )
    app = FastAPI()
    app.include_router(rooms)
    c = TestClient(app)
    room = c.post(
        "/api/teams",
        json={
            "id": "room-1",
            "name": "Before",
            "members": [{"name": "alice"}],
            "leaderId": "alice",
        },
    ).json()
    collab_store.upsert_room("t1", room)

    updated = c.put(
        "/api/teams/room-1",
        json={
            "name": "After",
            "members": [{"name": "alice"}, {"name": "bob"}],
            "leaderId": "alice",
        },
    )
    assert updated.status_code == 200
    assert collab_store.room_for_session("t1")["name"] == "After"
    assert projected[-1] == "After"


def test_team_room_projection_can_promote_to_thread_session(tmp_path) -> None:
    from runtime.memory.cowork.collaboration_store import CollaborationStore

    collab_store = CollaborationStore(base_dir=tmp_path / "cowork")
    collab_store.upsert_room_by_id(
        {
            "id": "room-1",
            "name": "Team first",
            "participants": [
                {
                    "id": "actor-bob",
                    "actor_id": "bob",
                    "display_name": "Bob",
                    "role": "member",
                }
            ],
        }
    )
    collab_store.upsert_task_for_room(
        "room-1",
        {
            "id": "task-1",
            "room_id": "room-1",
            "title": "created from team",
            "created_at": "t0",
            "updated_at": "t0",
        },
    )

    assert collab_store.session_id_for_room("room-1") == "team:room-1"
    assert [t["title"] for t in collab_store.tasks_for_session("team:room-1")] == [
        "created from team"
    ]

    collab_store.upsert_room(
        "thread-1",
        {
            "id": "room-1",
            "name": "Thread linked",
        },
    )
    collab_store.set_room_project_metadata("thread-1", "P-1", generation=1)

    assert collab_store.session_id_for_room("room-1") == "thread-1"
    assert collab_store.room_for_session("team:room-1") is None
    linked_room = collab_store.room_for_session("thread-1")
    assert linked_room["participants"][0]["actor_id"] == "bob"
    assert linked_room["metadata"]["project_id"] == "P-1"
    assert [t["title"] for t in collab_store.tasks_for_session("thread-1")] == ["created from team"]

    # A later authoritative Team Room update refreshes participants without
    # erasing the Project OS metadata contributed by the other projection.
    collab_store.upsert_room_by_id(
        {
            "id": "room-1",
            "name": "Thread linked",
            "participants": [],
        }
    )
    refreshed_room = collab_store.room_for_session("thread-1")
    assert refreshed_room["participants"] == []
    assert refreshed_room["metadata"]["project_id"] == "P-1"


def test_team_room_delete_projection_removes_unified_room_state(tmp_path) -> None:
    from runtime.memory.cowork.collaboration_store import CollaborationStore
    from runtime.sensing.gateway.team_rooms_router import create_team_rooms_router

    collab_store = CollaborationStore(base_dir=tmp_path / "cowork")
    rooms = create_team_rooms_router(
        state_path=tmp_path / "team_rooms.json",
        room_projection=lambda room: collab_store.upsert_room_by_id(room),
        room_delete_projection=lambda room_id: collab_store.delete_room_by_id(room_id),
    )
    app = FastAPI()
    app.include_router(rooms)
    c = TestClient(app)

    team = c.post(
        "/api/teams",
        json={
            "id": "room-1",
            "name": "Team first",
            "members": [{"name": "alice"}],
            "leaderId": "alice",
        },
    ).json()
    collab_store.upsert_task_for_room(
        team["id"],
        {
            "id": "task-1",
            "room_id": team["id"],
            "title": "created from team",
            "created_at": "t0",
            "updated_at": "t0",
        },
    )
    collab_store.append_message_for_room(team["id"], text="room transcript")

    assert collab_store.room_by_id(team["id"]) is not None
    assert collab_store.tasks_for_room(team["id"])
    assert collab_store.messages_for_room(team["id"])

    deleted = c.delete(f"/api/teams/{team['id']}")

    assert deleted.status_code == 200
    assert collab_store.session_id_for_room(team["id"]) is None
    assert collab_store.room_by_id(team["id"]) is None
    assert collab_store.tasks_for_room(team["id"]) == []
    assert collab_store.messages_for_room(team["id"]) == []

