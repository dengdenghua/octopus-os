"""/api/cowork/* thread-group HTTP layer: reads public, mutations attributed."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.cowork.async_work import AsyncWorkStore
from runtime.memory.cowork.collaboration_store import CollaborationStore
from runtime.memory.cowork.group import MemberEvent
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.threads import ThreadStateStore
from runtime.platform.ui.app import create_app
from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway.cowork_group_router import create_cowork_group_router
from runtime.sensing.gateway.team_rooms_router import create_team_rooms_router


def _client(tmp_path) -> TestClient:
    app = FastAPI()
    app.include_router(create_cowork_group_router(store=GroupStore(base_dir=tmp_path)))
    return TestClient(app)


def test_full_wechat_like_flow(tmp_path) -> None:
    c = _client(tmp_path)
    t = "thread-xyz"

    # Start a 1:1: pull in the human + one agent.
    c.post(f"/api/cowork/{t}/members", json={"target_id": "user", "kind": "human"})
    c.post(f"/api/cowork/{t}/members", json={"target_id": "alice", "kind": "agent"})
    state = c.get(f"/api/cowork/{t}").json()["state"]
    assert state["is_one_to_one"] is True
    assert {m["id"] for m in state["roster"]} == {"user", "alice"}

    # Mid-conversation, pull in a specialist with a from-join grant.
    r = c.post(
        f"/api/cowork/{t}/members",
        json={
            "target_id": "bob",
            "kind": "agent",
            "grant": {"scope": "from_join"},
            "at_message": 12,
        },
    )
    assert r.status_code == 200
    assert {m["id"] for m in r.json()["state"]["roster"]} == {"user", "alice", "bob"}

    # Switch to swarm and check responders follow the mode.
    c.post(f"/api/cowork/{t}/mode", json={"mode": "swarm"})
    body = c.get(f"/api/cowork/{t}").json()
    assert body["state"]["mode"] == "swarm"
    assert set(body["responders"]) == {"alice", "bob"}

    # Shared blackboard write is attributed and visible to the group.
    c.post(f"/api/cowork/{t}/blackboard", json={"key": "plan", "value": ["a", "b"]})
    assert c.get(f"/api/cowork/{t}").json()["blackboard"]["plan"] == ["a", "b"]

    # Remove alice — roster folds, blackboard survives.
    c.request("DELETE", f"/api/cowork/{t}/members/alice")
    after = c.get(f"/api/cowork/{t}").json()
    assert {m["id"] for m in after["state"]["roster"]} == {"user", "bob"}
    assert after["blackboard"]["plan"] == ["a", "b"]


def test_invalid_mode_rejected(tmp_path) -> None:
    c = _client(tmp_path)
    assert c.post("/api/cowork/t/mode", json={"mode": "bogus"}).status_code == 400


def test_atomic_roster_replace_preserves_humans_and_is_idempotent(tmp_path) -> None:
    c = _client(tmp_path)
    thread_id = "thread-roster"
    c.post(
        f"/api/cowork/{thread_id}/members",
        json={"target_id": "human-owner", "kind": "human"},
    )
    c.post(
        f"/api/cowork/{thread_id}/members",
        json={"target_id": "old-agent", "kind": "agent"},
    )

    replaced = c.put(
        f"/api/cowork/{thread_id}/roster",
        json={"agent_ids": ["new-agent", "critic", "new-agent"], "mode": "swarm"},
    )
    assert replaced.status_code == 200, replaced.json()
    body = replaced.json()
    assert [(item["action"], item["target_id"]) for item in body["events"]] == [
        ("leave", "old-agent"),
        ("invite", "new-agent"),
        ("invite", "critic"),
        ("mode", ""),
    ]
    assert body["state"]["mode"] == "swarm"
    assert {member["id"] for member in body["state"]["roster"]} == {
        "human-owner",
        "new-agent",
        "critic",
    }
    version = body["state"]["event_count"]

    unchanged = c.put(
        f"/api/cowork/{thread_id}/roster",
        json={"agent_ids": ["new-agent", "critic"], "mode": "swarm"},
    )
    assert unchanged.status_code == 200, unchanged.json()
    assert unchanged.json()["events"] == []
    assert unchanged.json()["state"]["event_count"] == version


def test_on_demand_agent_reference_add_remove_is_idempotent(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    app = FastAPI()
    app.include_router(create_cowork_group_router(store=store))
    client = TestClient(app)

    added = client.post(
        "/api/cowork/thread-members/members",
        json={"target_id": "installed_code_reviewer", "kind": "agent"},
    )
    assert added.status_code == 200, added.json()
    assert added.json()["added"] is True
    assert added.json()["state"]["event_count"] == 1
    # Membership is only a dispatchable id reference; no clone or per-thread
    # owner/home record is introduced into the group schema. Synthetic local
    # CLI and mobile ids intentionally live outside the built-in AgentRegistry.
    assert store.events("thread-members")[0].target_id == "installed_code_reviewer"

    retried = client.post(
        "/api/cowork/thread-members/members",
        json={"target_id": "installed_code_reviewer", "kind": "agent"},
    )
    assert retried.status_code == 200, retried.json()
    assert retried.json()["added"] is False
    assert retried.json()["state"]["event_count"] == 1

    removed = client.delete("/api/cowork/thread-members/members/installed_code_reviewer")
    assert removed.status_code == 200, removed.json()
    assert removed.json()["removed"] is True
    assert removed.json()["state"]["event_count"] == 2

    remove_retry = client.delete("/api/cowork/thread-members/members/installed_code_reviewer")
    assert remove_retry.status_code == 200, remove_retry.json()
    assert remove_retry.json()["removed"] is False
    assert remove_retry.json()["state"]["event_count"] == 2


def test_on_demand_roster_accepts_registry_cli_and_mobile_ids_atomically(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    app = FastAPI()
    app.include_router(create_cowork_group_router(store=store))
    client = TestClient(app)

    replaced = client.put(
        "/api/cowork/thread-roster-registry/roster",
        json={
            "agent_ids": ["advisor", "installed_code_reviewer", "mobile_phone1"],
            "mode": "swarm",
        },
    )

    assert replaced.status_code == 200, replaced.json()
    assert replaced.json()["state"]["mode"] == "swarm"
    assert {member["id"] for member in replaced.json()["state"]["roster"]} == {
        "advisor",
        "installed_code_reviewer",
        "mobile_phone1",
    }


def test_atomic_roster_replace_rolls_back_the_whole_diff_on_validation_error(tmp_path) -> None:
    c = _client(tmp_path)
    thread_id = "thread-roster-rollback"
    for member_id, kind in (("human-owner", "human"), ("old-agent", "agent")):
        c.post(
            f"/api/cowork/{thread_id}/members",
            json={"target_id": member_id, "kind": kind},
        )
    before = c.get(f"/api/cowork/{thread_id}").json()["state"]

    rejected = c.put(
        f"/api/cowork/{thread_id}/roster",
        json={"agent_ids": ["human-owner"], "mode": "swarm"},
    )
    assert rejected.status_code == 400
    after = c.get(f"/api/cowork/{thread_id}").json()["state"]
    assert after == before


def test_search_endpoint_spans_surfaces_and_filters(tmp_path) -> None:
    c = _client(tmp_path)
    t = "thread-search"
    c.post(f"/api/cowork/{t}/members", json={"target_id": "nutrition-expert", "kind": "agent"})
    c.post(f"/api/cowork/{t}/blackboard", json={"key": "decision", "value": "enter nutrition"})

    body = c.get(f"/api/cowork/{t}/search", params={"q": "nutrition"}).json()
    assert body["query"] == "nutrition"
    kinds = {h["kind"] for h in body["hits"]}
    assert "blackboard" in kinds and "event" in kinds

    # kinds filter narrows the surfaces searched.
    only_board = c.get(
        f"/api/cowork/{t}/search", params={"q": "nutrition", "kinds": "blackboard"}
    ).json()["hits"]
    assert {h["kind"] for h in only_board} == {"blackboard"}

    # Empty query is a clean empty result, not an error.
    empty = c.get(f"/api/cowork/{t}/search", params={"q": ""})
    assert empty.status_code == 200
    assert empty.json()["hits"] == []


def test_presence_unread_and_read_receipts(tmp_path) -> None:
    c = _client(tmp_path)
    t = "thread-presence"
    c.post(f"/api/cowork/{t}/members", json={"target_id": "user", "kind": "human"})
    c.post(f"/api/cowork/{t}/members", json={"target_id": "alice", "kind": "agent"})
    c.post(f"/api/cowork/{t}/mode", json={"mode": "swarm"})  # more activity

    pres = c.get(f"/api/cowork/{t}/presence").json()["members"]
    user = next(m for m in pres if m["member_id"] == "user")
    assert user["unread"] > 0
    assert user["online"] is False

    # Heartbeat → online; mark-read → unread clears.
    assert c.post(f"/api/cowork/{t}/heartbeat", json={"member_id": "user"}).status_code == 200
    assert c.post(f"/api/cowork/{t}/read", json={"member_id": "user"}).status_code == 200

    after = c.get(f"/api/cowork/{t}/presence").json()["members"]
    user2 = next(m for m in after if m["member_id"] == "user")
    assert user2["unread"] == 0
    assert user2["online"] is True


def test_invite_requires_target(tmp_path) -> None:
    c = _client(tmp_path)
    assert c.post("/api/cowork/t/members", json={"kind": "agent"}).status_code == 422


def test_advanced_cowork_endpoints(tmp_path) -> None:
    c = _client(tmp_path)
    t = "thread-advanced"

    c.post(f"/api/cowork/{t}/members", json={"target_id": "db-agent"})
    c.post(f"/api/cowork/{t}/members", json={"target_id": "ui-agent"})

    nominated = c.get(
        f"/api/cowork/{t}/nominate",
        params={"text": "database indexing latency"},
    ).json()
    assert nominated["nominated"][0] == "db-agent"

    task = c.post(
        f"/api/cowork/{t}/tasks",
        json={"assignee": "db-agent", "prompt": "check indexes"},
    ).json()["task"]
    done = c.post(
        f"/api/cowork/{t}/tasks/{task['task_id']}/complete",
        json={"result": "indexes checked", "blackboard_key": "indexes"},
    ).json()
    assert done["blackboard"]["indexes"] == "indexes checked"
    assert c.get(f"/api/cowork/{t}/tasks").json()["tasks"][0]["status"] == "done"

    forked = c.post(
        f"/api/cowork/{t}/breakout",
        json={
            "child_thread": "child-advanced",
            "members": [{"id": "db-agent"}],
            "grant": {"scope": "summary"},
        },
    ).json()
    assert forked["members"] == ["db-agent"]

    merged = c.post(
        f"/api/cowork/{t}/breakout/child-advanced/merge",
        json={"summary": "side thread result"},
    ).json()
    assert merged["blackboard"]["breakout:child-advanced"]["status"] == "merged"

    catchup = c.get("/api/cowork/child-advanced/catchup/db-agent").json()
    assert catchup["member_id"] == "db-agent"
    assert catchup["summary_only"] is True


def test_mutations_require_auth_when_enabled(tmp_path) -> None:
    store = IdentityStore()
    store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    thread_store = ThreadStateStore()
    thread_store.ensure_thread(
        "thread-auth",
        metadata={"owner_actor_id": "alice", "tenant_id": "legacy:alice"},
    )
    app = FastAPI()
    app.include_router(
        create_cowork_group_router(
            store=GroupStore(base_dir=tmp_path),
            runtime=SimpleNamespace(thread_store=thread_store),
            identity_store=store,
            require_auth=True,
        )
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer sk-alice"}

    assert client.get("/api/cowork/thread-auth").status_code == 401
    assert (
        client.post(
            "/api/cowork/thread-auth/members",
            json={"target_id": "alice", "kind": "agent"},
        ).status_code
        == 401
    )

    ok = client.post(
        "/api/cowork/thread-auth/members",
        json={"target_id": "alice", "kind": "agent"},
        headers=headers,
    )
    assert ok.status_code == 200
    body = client.get("/api/cowork/thread-auth", headers=headers).json()
    assert body["events"][0]["actor"] == "alice"


def test_cowork_and_collab_are_bound_to_thread_owner_and_tenant(tmp_path) -> None:
    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-alice",
    )
    identities.add(
        Identity(actor_id="bob", metadata={"tenant_id": "tenant-b"}),
        api_key_plaintext="sk-bob",
    )
    thread_store = ThreadStateStore()
    thread_store.ensure_thread(
        "alice-private",
        metadata={"owner_actor_id": "alice", "tenant_id": "tenant-a"},
    )
    group_store = GroupStore(base_dir=tmp_path)
    group_store.append(
        "alice-private",
        MemberEvent(action="invite", actor="alice", target_id="seed-agent"),
    )
    group_store.blackboard("alice-private").write("private", "alice-only", writer="alice")
    async_store = AsyncWorkStore(base_dir=tmp_path, group_store=group_store)
    async_store.assign("alice-private", "seed-agent", "private task", actor="alice")

    app = FastAPI()
    app.include_router(
        create_cowork_group_router(
            store=group_store,
            async_store=async_store,
            runtime=SimpleNamespace(thread_store=thread_store),
            identity_store=identities,
            require_auth=True,
        )
    )
    client = TestClient(app)
    alice = {"Authorization": "Bearer sk-alice"}
    bob = {"Authorization": "Bearer sk-bob"}

    for path in (
        "/api/cowork/alice-private",
        "/api/collab/alice-private",
        "/api/cowork/alice-private/tasks",
    ):
        assert client.get(path, headers=bob).status_code == 404
        assert client.get(path, headers=alice).status_code == 200

    attacks = (
        (
            "/api/cowork/alice-private/members",
            {"target_id": "bob-agent", "kind": "agent"},
        ),
        ("/api/cowork/alice-private/mode", {"mode": "swarm"}),
        ("/api/cowork/alice-private/blackboard", {"key": "hijack", "value": True}),
        (
            "/api/cowork/alice-private/tasks",
            {"assignee": "bob-agent", "prompt": "steal context"},
        ),
    )
    event_count = len(group_store.events("alice-private"))
    task_count = len(async_store.list("alice-private"))
    for path, body in attacks:
        assert client.post(path, json=body, headers=bob).status_code == 404
    assert len(group_store.events("alice-private")) == event_count
    assert len(async_store.list("alice-private")) == task_count
    assert "hijack" not in group_store.blackboard_snapshot("alice-private")

    assert client.post(attacks[0][0], json=attacks[0][1], headers=alice).status_code == 200
    assert client.post(attacks[1][0], json=attacks[1][1], headers=alice).status_code == 200
    assert client.post(attacks[2][0], json=attacks[2][1], headers=alice).status_code == 200
    assert client.post(attacks[3][0], json=attacks[3][1], headers=alice).status_code == 200

    # Cowork cannot claim an arbitrary id and bypass the managed-thread path.
    assert client.get("/api/cowork/not-managed", headers=alice).status_code == 404


def test_collab_room_projection_preserves_team_room_membership(tmp_path) -> None:
    from runtime.memory.cowork.session import link_room
    from runtime.sensing.gateway.team_rooms_router import create_team_rooms_router

    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-alice",
    )
    identities.add(
        Identity(actor_id="bob", metadata={"tenant_id": "tenant-b"}),
        api_key_plaintext="sk-bob",
    )
    thread_store = ThreadStateStore()
    thread_store.ensure_thread(
        "alice-room-thread",
        metadata={"owner_actor_id": "alice", "tenant_id": "tenant-a"},
    )
    group_store = GroupStore(base_dir=tmp_path / "cowork")
    rooms = create_team_rooms_router(
        state_path=tmp_path / "team_rooms.json",
        identity_store=identities,
        require_auth=True,
    )
    app = FastAPI()
    app.include_router(rooms)
    app.include_router(
        create_cowork_group_router(
            store=group_store,
            team_rooms_state_path=tmp_path / "team_rooms.json",
            team_rooms_router=rooms,
            runtime=SimpleNamespace(thread_store=thread_store),
            identity_store=identities,
            require_auth=True,
        )
    )
    client = TestClient(app)
    alice = {"Authorization": "Bearer sk-alice"}
    bob = {"Authorization": "Bearer sk-bob"}

    bob_room = client.post(
        "/api/teams",
        headers=bob,
        json={"name": "Bob private", "members": [{"name": "worker"}]},
    ).json()
    link_room(group_store, "alice-room-thread", bob_room["id"], actor="seed")

    # The thread owner may still use the room-independent group surface, but
    # cannot project or relink a Team Room they do not belong to.
    assert client.get("/api/cowork/alice-room-thread", headers=alice).status_code == 200
    assert client.get("/api/collab/alice-room-thread", headers=alice).status_code == 403
    assert (
        client.get("/api/cowork/alice-room-thread/search?q=private", headers=alice).status_code
        == 403
    )
    assert (
        client.post(
            "/api/collab/alice-room-thread/link-room",
            headers=alice,
            json={"room_id": bob_room["id"]},
        ).status_code
        == 403
    )

    alice_room = client.post(
        "/api/teams",
        headers=alice,
        json={"name": "Alice room", "members": [{"name": "worker"}]},
    ).json()
    thread_store.ensure_thread(
        "alice-own-room-thread",
        metadata={"owner_actor_id": "alice", "tenant_id": "tenant-a"},
    )
    assert (
        client.post(
            "/api/collab/alice-own-room-thread/link-room",
            headers=alice,
            json={"room_id": alice_room["id"]},
        ).status_code
        == 200
    )
    assert client.get("/api/collab/alice-own-room-thread", headers=alice).status_code == 200


def test_delayed_linked_room_roster_projection_cannot_overwrite_latest_group_roster(
    tmp_path,
    monkeypatch,
) -> None:
    group_store = GroupStore(base_dir=tmp_path / "groups")
    collaboration = CollaborationStore(base_dir=tmp_path / "collaboration")
    rooms = create_team_rooms_router(state_path=tmp_path / "team_rooms.json")
    app = FastAPI()
    app.include_router(rooms)
    app.include_router(
        create_cowork_group_router(
            store=group_store,
            collaboration_store=collaboration,
            team_rooms_router=rooms,
        )
    )
    client = TestClient(app)
    thread_id = "thread-delayed-roster-projection"
    room_id = client.post(
        "/api/teams",
        json={"name": "Roster projection", "members": [{"name": "seed-agent"}]},
    ).json()["id"]
    linked = client.post(
        f"/api/collab/{thread_id}/link-room",
        json={"room_id": room_id},
    )
    assert linked.status_code == 200, linked.json()

    first_projection_entered = Event()
    release_first_projection = Event()
    real_projector = rooms.replace_team_agent_members
    projection_calls = 0

    def delay_first_projector(request, team_id, members, leader_id=None):
        nonlocal projection_calls
        projection_calls += 1
        if projection_calls == 1:
            first_projection_entered.set()
            assert release_first_projection.wait(timeout=5)
        return real_projector(request, team_id, members, leader_id)

    monkeypatch.setattr(rooms, "replace_team_agent_members", delay_first_projector)
    with ThreadPoolExecutor(max_workers=1) as pool:
        stale_future = pool.submit(
            client.post,
            f"/api/cowork/{thread_id}/members",
            json={"target_id": "stale-agent", "kind": "agent"},
        )
        assert first_projection_entered.wait(timeout=5)
        try:
            latest = client.put(
                f"/api/cowork/{thread_id}/roster",
                json={"agent_ids": ["latest-agent"], "mode": "cluster"},
            )
        finally:
            release_first_projection.set()
        stale = stale_future.result(timeout=5)

    assert latest.status_code == 200, latest.json()
    assert stale.status_code == 200, stale.json()
    canonical_roster = {
        member.id for member in group_store.state(thread_id).roster if member.kind == "agent"
    }
    projected_roster = {
        member["name"] for member in client.get(f"/api/teams/{room_id}").json()["members"]
    }
    assert canonical_roster == {"latest-agent"}
    assert projected_roster == canonical_roster


def test_link_room_failure_preserves_visible_transcript_and_retries_recovery(
    tmp_path,
    monkeypatch,
) -> None:
    group_store = GroupStore(base_dir=tmp_path / "groups")
    collaboration = CollaborationStore(base_dir=tmp_path / "collaboration")
    rooms = create_team_rooms_router(state_path=tmp_path / "team_rooms.json")
    app = FastAPI()
    app.include_router(rooms)
    app.include_router(
        create_cowork_group_router(
            store=group_store,
            collaboration_store=collaboration,
            team_rooms_router=rooms,
        )
    )
    client = TestClient(app)
    room = client.post(
        "/api/teams",
        json={"name": "Visible room", "members": [{"name": "worker"}]},
    ).json()
    room_id = room["id"]
    thread_id = "thread-visible-room-link"
    projection_entered = Event()
    release_projection = Event()
    real_upsert_room = collaboration.upsert_room

    def commit_room_then_fail(target_thread_id, payload):
        real_upsert_room(target_thread_id, payload)
        projection_entered.set()
        assert release_projection.wait(timeout=5)
        raise RuntimeError("injected collaboration projection failure")

    real_session_probe = collaboration.session_id_for_room
    stale_probe_calls = 0

    def stale_session_probe(target_room_id):
        nonlocal stale_probe_calls
        stale_probe_calls += 1
        if stale_probe_calls <= 2:
            return None
        return real_session_probe(target_room_id)

    monkeypatch.setattr(collaboration, "upsert_room", commit_room_then_fail)
    monkeypatch.setattr(collaboration, "session_id_for_room", stale_session_probe)
    with ThreadPoolExecutor(max_workers=1) as pool:
        linking = pool.submit(
            client.post,
            f"/api/collab/{thread_id}/link-room",
            json={"room_id": room_id},
        )
        assert projection_entered.wait(timeout=5)
        collaboration.append_message(
            thread_id,
            room_id=room_id,
            text="VISIBLE-BEFORE-FAIL",
            participant_id="owner",
            display_name="Owner",
        )
        release_projection.set()
        failed = linking.result(timeout=5)

    assert failed.status_code == 409, failed.json()
    assert failed.json()["detail"]["code"] == "ROOM_LINK_RECOVERY_PENDING"
    assert group_store.state(thread_id).room_id == room_id
    assert client.get(f"/api/teams/{room_id}").json()["thread_id"] == thread_id
    assert collaboration.session_id_for_room(room_id) == thread_id
    assert [message["text"] for message in collaboration.messages_for_session(thread_id)] == [
        "VISIBLE-BEFORE-FAIL"
    ]
    recovery_key = f"system:room_link_recovery:{room_id}"
    assert group_store.blackboard(thread_id).read(recovery_key)["status"] == "pending"

    monkeypatch.setattr(collaboration, "upsert_room", real_upsert_room)
    monkeypatch.setattr(collaboration, "session_id_for_room", real_session_probe)
    retried = client.post(
        f"/api/collab/{thread_id}/link-room",
        json={"room_id": room_id},
    )

    assert retried.status_code == 200, retried.json()
    assert retried.json()["state"]["room_id"] == room_id
    assert [message["text"] for message in collaboration.messages_for_session(thread_id)] == [
        "VISIBLE-BEFORE-FAIL"
    ]
    assert group_store.blackboard(thread_id).read(recovery_key)["status"] == "resolved"
    refused_delete = client.delete(f"/api/teams/{room_id}")
    assert refused_delete.status_code == 409
    assert refused_delete.json()["detail"]["code"] == "TEAM_ROOM_LINKED"
    assert [message["text"] for message in collaboration.messages_for_session(thread_id)] == [
        "VISIBLE-BEFORE-FAIL"
    ]


def test_failed_link_never_unbinds_a_concurrent_same_room_winner(
    tmp_path,
    monkeypatch,
) -> None:
    group_store = GroupStore(base_dir=tmp_path / "groups")
    collaboration = CollaborationStore(base_dir=tmp_path / "collaboration")
    rooms = create_team_rooms_router(state_path=tmp_path / "team_rooms.json")
    app = FastAPI()
    app.include_router(rooms)
    app.include_router(
        create_cowork_group_router(
            store=group_store,
            collaboration_store=collaboration,
            team_rooms_router=rooms,
        )
    )
    client = TestClient(app)
    room_id = client.post(
        "/api/teams",
        json={"name": "Concurrent winner", "members": [{"name": "worker"}]},
    ).json()["id"]
    thread_id = "thread-link-winner"
    real_binder = rooms.bind_team_thread
    binder_calls = 0

    def first_binder_fails(request, target_room_id, target_thread_id):
        nonlocal binder_calls
        binder_calls += 1
        if binder_calls == 1:
            raise RuntimeError("first binder failed before commit")
        return real_binder(request, target_room_id, target_thread_id)

    monkeypatch.setattr(rooms, "bind_team_thread", first_binder_fails)
    real_session_probe = collaboration.session_id_for_room
    stale_probe_read = Event()
    winner_committed = Event()
    first_probe = True

    def pause_after_stale_probe(target_room_id):
        nonlocal first_probe
        observed = real_session_probe(target_room_id)
        if first_probe:
            first_probe = False
            stale_probe_read.set()
            assert winner_committed.wait(timeout=5)
        return observed

    monkeypatch.setattr(collaboration, "session_id_for_room", pause_after_stale_probe)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(
            client.post,
            f"/api/collab/{thread_id}/link-room",
            json={"room_id": room_id},
        )
        assert stale_probe_read.wait(timeout=5)
        winner = client.post(
            f"/api/collab/{thread_id}/link-room",
            json={"room_id": room_id},
        )
        winner_committed.set()
        first_response = first.result(timeout=5)

    assert winner.status_code == 200, winner.json()
    assert first_response.status_code == 200, first_response.json()
    assert client.get(f"/api/teams/{room_id}").json()["thread_id"] == thread_id
    assert group_store.state(thread_id).room_id == room_id
    assert real_session_probe(room_id) == thread_id


def test_concurrent_different_room_links_choose_one_before_external_writes(
    tmp_path,
    monkeypatch,
) -> None:
    group_store = GroupStore(base_dir=tmp_path / "groups")
    collaboration = CollaborationStore(base_dir=tmp_path / "collaboration")
    rooms = create_team_rooms_router(state_path=tmp_path / "team_rooms.json")
    app = FastAPI()
    app.include_router(rooms)
    app.include_router(
        create_cowork_group_router(
            store=group_store,
            collaboration_store=collaboration,
            team_rooms_router=rooms,
        )
    )
    client = TestClient(app)
    room_ids = [
        client.post(
            "/api/teams",
            json={"name": name, "members": [{"name": "worker"}]},
        ).json()["id"]
        for name in ("Room A", "Room B")
    ]
    thread_id = "thread-different-room-race"
    contenders_ready = Barrier(2)
    real_reserve = group_store.link_room_if_absent

    def synchronized_reserve(target_thread_id, room_id, *, actor):
        contenders_ready.wait(timeout=5)
        return real_reserve(target_thread_id, room_id, actor=actor)

    monkeypatch.setattr(group_store, "link_room_if_absent", synchronized_reserve)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda target_room_id: client.post(
                    f"/api/collab/{thread_id}/link-room",
                    json={"room_id": target_room_id},
                ),
                room_ids,
            )
        )

    winner = next(response for response in responses if response.status_code == 200)
    loser = next(response for response in responses if response.status_code == 409)
    winner_room_id = winner.json()["state"]["room_id"]
    loser_room_id = next(room_id for room_id in room_ids if room_id != winner_room_id)
    assert loser.json()["detail"]["code"] == "ROOM_LINK_CONFLICT"
    assert group_store.state(thread_id).room_id == winner_room_id
    assert collaboration.session_id_for_room(winner_room_id) == thread_id
    assert collaboration.session_id_for_room(loser_room_id) is None
    assert client.get(f"/api/teams/{winner_room_id}").json()["thread_id"] == thread_id
    assert client.get(f"/api/teams/{loser_room_id}").json().get("thread_id") is None


def test_ensure_room_and_explicit_link_share_the_same_atomic_reservation(
    tmp_path,
    monkeypatch,
) -> None:
    group_store = GroupStore(base_dir=tmp_path / "groups")
    collaboration = CollaborationStore(base_dir=tmp_path / "collaboration")
    rooms = create_team_rooms_router(state_path=tmp_path / "team_rooms.json")
    app = FastAPI()
    app.include_router(rooms)
    app.include_router(
        create_cowork_group_router(
            store=group_store,
            collaboration_store=collaboration,
            team_rooms_router=rooms,
        )
    )
    client = TestClient(app)
    explicit_room_id = client.post(
        "/api/teams",
        json={"name": "Explicit", "members": [{"name": "worker"}]},
    ).json()["id"]
    thread_id = "thread-ensure-link-race"
    auto_room_id = f"collab-{thread_id}"
    contenders_ready = Barrier(2)
    real_reserve = group_store.link_room_if_absent

    def synchronized_reserve(target_thread_id, room_id, *, actor):
        contenders_ready.wait(timeout=5)
        return real_reserve(target_thread_id, room_id, actor=actor)

    monkeypatch.setattr(group_store, "link_room_if_absent", synchronized_reserve)
    with ThreadPoolExecutor(max_workers=2) as pool:
        ensured_future = pool.submit(
            client.post,
            f"/api/collab/{thread_id}/room",
            json={"name": "Automatic", "members": [{"name": "worker"}]},
        )
        explicit_future = pool.submit(
            client.post,
            f"/api/collab/{thread_id}/link-room",
            json={"room_id": explicit_room_id},
        )
        ensured = ensured_future.result(timeout=5)
        explicit = explicit_future.result(timeout=5)

    assert sorted((ensured.status_code, explicit.status_code)) == [200, 409]
    canonical_room_id = str(group_store.state(thread_id).room_id)
    assert canonical_room_id in {auto_room_id, explicit_room_id}
    assert collaboration.session_id_for_room(canonical_room_id) == thread_id
    assert client.get(f"/api/teams/{canonical_room_id}").json()["thread_id"] == thread_id

    loser_room_id = explicit_room_id if canonical_room_id == auto_room_id else auto_room_id
    assert collaboration.session_id_for_room(loser_room_id) is None
    loser_room = client.get(f"/api/teams/{loser_room_id}")
    if loser_room.status_code == 200:
        assert loser_room.json().get("thread_id") is None
    else:
        assert loser_room.status_code == 404


def test_concurrent_ensure_room_reuses_one_exact_team_surface(tmp_path, monkeypatch) -> None:
    group_store = GroupStore(base_dir=tmp_path / "groups")
    collaboration = CollaborationStore(base_dir=tmp_path / "collaboration")
    async_store = AsyncWorkStore(base_dir=tmp_path / "groups", group_store=group_store)
    rooms = create_team_rooms_router(state_path=tmp_path / "team_rooms.json")
    app = FastAPI()
    app.include_router(rooms)
    app.include_router(
        create_cowork_group_router(
            store=group_store,
            async_store=async_store,
            collaboration_store=collaboration,
            team_rooms_router=rooms,
        )
    )
    client = TestClient(app)
    thread_id = "thread-double-ensure"
    room_id = f"collab-{thread_id}"
    creators_ready = Barrier(2)
    real_create = rooms.create_team_from_payload_exact

    def synchronized_create(request, payload):
        creators_ready.wait(timeout=5)
        return real_create(request, payload)

    monkeypatch.setattr(rooms, "create_team_from_payload_exact", synchronized_create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _index: client.post(
                    f"/api/collab/{thread_id}/room",
                    json={"name": "One room", "members": [{"name": "worker"}]},
                ),
                range(2),
            )
        )

    assert [response.status_code for response in responses] == [200, 200]
    assert {response.json()["room"]["id"] for response in responses} == {room_id}
    teams = client.get("/api/teams").json()["teams"]
    assert [team["id"] for team in teams] == [room_id]
    assert teams[0]["thread_id"] == thread_id
    assert group_store.state(thread_id).room_id == room_id
    assert collaboration.session_id_for_room(room_id) == thread_id


def test_room_reservation_before_binder_makes_concurrent_delete_fail_closed(
    tmp_path,
    monkeypatch,
) -> None:
    group_store = GroupStore(base_dir=tmp_path / "groups")
    collaboration = CollaborationStore(base_dir=tmp_path / "collaboration")
    rooms = create_team_rooms_router(state_path=tmp_path / "team_rooms.json")
    app = FastAPI()
    app.include_router(rooms)
    app.include_router(
        create_cowork_group_router(
            store=group_store,
            collaboration_store=collaboration,
            team_rooms_router=rooms,
        )
    )
    client = TestClient(app)
    thread_id = "thread-delete-link-race"
    room_id = client.post(
        "/api/teams",
        json={"name": "Delete link race", "members": [{"name": "worker"}]},
    ).json()["id"]
    binder_entered = Event()
    release_binder = Event()
    real_binder = rooms.bind_team_thread

    def delayed_binder(request, target_room_id, target_thread_id):
        binder_entered.set()
        assert release_binder.wait(timeout=5)
        return real_binder(request, target_room_id, target_thread_id)

    monkeypatch.setattr(rooms, "bind_team_thread", delayed_binder)
    with ThreadPoolExecutor(max_workers=1) as pool:
        linking = pool.submit(
            client.post,
            f"/api/collab/{thread_id}/link-room",
            json={"room_id": room_id},
        )
        assert binder_entered.wait(timeout=5)
        refused = client.delete(f"/api/teams/{room_id}")
        release_binder.set()
        linked = linking.result(timeout=5)

    assert refused.status_code == 409, refused.json()
    assert refused.json()["detail"] == {
        "code": "TEAM_ROOM_LINKED",
        "message": "unlink the collaboration thread before deleting this room",
        "team_id": room_id,
        "thread_id": thread_id,
    }
    assert linked.status_code == 200, linked.json()
    assert group_store.state(thread_id).room_id == room_id
    assert client.get(f"/api/teams/{room_id}").json()["thread_id"] == thread_id
    assert collaboration.session_id_for_room(room_id) == thread_id


def test_stale_team_router_refresh_never_reverts_a_new_room_binding(tmp_path) -> None:
    state_path = tmp_path / "team_rooms.json"
    seed_app = FastAPI()
    seed_rooms = create_team_rooms_router(state_path=state_path)
    seed_app.include_router(seed_rooms)
    seed = TestClient(seed_app)
    room_a = seed.post(
        "/api/teams",
        json={"name": "Room A", "members": [{"name": "worker"}]},
    ).json()

    linking_rooms = create_team_rooms_router(state_path=state_path)
    stale_create_rooms = create_team_rooms_router(state_path=state_path)
    stale_update_rooms = create_team_rooms_router(state_path=state_path)
    group_store = GroupStore(base_dir=tmp_path / "groups")
    collaboration = CollaborationStore(base_dir=tmp_path / "collaboration")
    linking_app = FastAPI()
    linking_app.include_router(linking_rooms)
    linking_app.include_router(
        create_cowork_group_router(
            store=group_store,
            collaboration_store=collaboration,
            team_rooms_router=linking_rooms,
        )
    )
    stale_create_app = FastAPI()
    stale_create_app.include_router(stale_create_rooms)
    stale_update_app = FastAPI()
    stale_update_app.include_router(stale_update_rooms)
    thread_id = "thread-team-delta"

    linked = TestClient(linking_app).post(
        f"/api/collab/{thread_id}/link-room",
        json={"room_id": room_a["id"]},
    )
    unrelated = TestClient(stale_create_app).post(
        "/api/teams",
        json={"name": "Room B", "members": [{"name": "other"}]},
    )
    stale_overwrite = TestClient(stale_update_app).put(
        f"/api/teams/{room_a['id']}",
        json={"name": "Stale A", "members": [{"name": "worker"}]},
    )

    assert linked.status_code == 200, linked.json()
    assert unrelated.status_code == 200, unrelated.json()
    assert stale_overwrite.status_code == 200, stale_overwrite.json()
    assert stale_overwrite.json()["thread_id"] == thread_id
    fresh_rooms = create_team_rooms_router(state_path=state_path)
    fresh_app = FastAPI()
    fresh_app.include_router(fresh_rooms)
    fresh = TestClient(fresh_app)
    assert fresh.get(f"/api/teams/{room_a['id']}").json()["thread_id"] == thread_id
    assert fresh.get(f"/api/teams/{unrelated.json()['id']}").status_code == 200
    assert group_store.state(thread_id).room_id == room_a["id"]
    assert collaboration.session_id_for_room(room_a["id"]) == thread_id


def test_reserved_team_delete_uses_latest_durable_room_not_stale_router_baseline(
    tmp_path,
) -> None:
    state_path = tmp_path / "team_rooms.json"
    seed_rooms = create_team_rooms_router(state_path=state_path)
    seed_app = FastAPI()
    seed_app.include_router(seed_rooms)
    seeded = (
        TestClient(seed_app)
        .post(
            "/api/teams",
            json={"name": "Delete v1", "members": [{"name": "worker"}]},
        )
        .json()
    )

    deleting_rooms = create_team_rooms_router(state_path=state_path)
    updating_rooms = create_team_rooms_router(state_path=state_path)
    group_store = GroupStore(base_dir=tmp_path / "groups")
    deleting_app = FastAPI()
    deleting_app.include_router(deleting_rooms)
    deleting_app.include_router(
        create_cowork_group_router(store=group_store, team_rooms_router=deleting_rooms)
    )
    updating_app = FastAPI()
    updating_app.include_router(updating_rooms)

    updated = TestClient(updating_app).put(
        f"/api/teams/{seeded['id']}",
        json={"name": "Delete v2", "members": [{"name": "worker"}]},
    )
    deleted = TestClient(deleting_app).delete(f"/api/teams/{seeded['id']}")

    assert updated.status_code == 200, updated.json()
    assert deleted.status_code == 200, deleted.json()
    assert deleted.json()["deleted"] is True
    lease = group_store.room_delete_lease(seeded["id"])
    assert lease is not None and lease.finalized is True
    fresh_rooms = create_team_rooms_router(state_path=state_path)
    fresh_app = FastAPI()
    fresh_app.include_router(fresh_rooms)
    assert TestClient(fresh_app).get(f"/api/teams/{seeded['id']}").status_code == 404


def test_team_delete_retry_replays_invite_revocation_and_rejects_id_reuse(
    tmp_path,
    monkeypatch,
) -> None:
    from runtime.memory.cowork.team_invitation_store import TeamInvitationStore

    state_path = tmp_path / "team_rooms.json"
    invitation_store = TeamInvitationStore(tmp_path / "team_invitations.db")
    rooms = create_team_rooms_router(
        state_path=state_path,
        invitation_store=invitation_store,
    )
    group_store = GroupStore(base_dir=tmp_path / "groups")
    app = FastAPI()
    app.include_router(rooms)
    app.include_router(create_cowork_group_router(store=group_store, team_rooms_router=rooms))
    client = TestClient(app)
    created = client.post(
        "/api/teams",
        json={"id": "room-never-reuse", "name": "Delete", "members": [{"name": "worker"}]},
    ).json()
    invitation = client.post(f"/api/teams/{created['id']}/invite", json={}).json()
    real_revoke = invitation_store.revoke_room
    calls = 0

    def commit_then_raise(**kwargs):
        nonlocal calls
        calls += 1
        result = real_revoke(**kwargs)
        if calls == 1:
            raise RuntimeError("invite revoke committed before transport failure")
        return result

    monkeypatch.setattr(invitation_store, "revoke_room", commit_then_raise)
    first = client.delete(f"/api/teams/{created['id']}")
    retry = client.delete(f"/api/teams/{created['id']}")
    recreated = client.post(
        "/api/teams",
        json={"id": created["id"], "name": "Recreate", "members": [{"name": "worker"}]},
    )

    assert first.status_code == 409, first.json()
    assert first.json()["detail"]["code"] == "TEAM_ROOM_DELETE_RECOVERY_PENDING"
    assert retry.status_code == 200, retry.json()
    assert calls == 2
    assert client.get(f"/api/team-invites/{invitation['invite_token']}").status_code == 410
    assert recreated.status_code == 409, recreated.json()
    assert recreated.json()["detail"]["code"] == "TEAM_ROOM_DELETED"


def test_link_failure_with_uncertain_visibility_returns_recovery_pending(
    tmp_path,
    monkeypatch,
) -> None:
    group_store = GroupStore(base_dir=tmp_path / "groups")
    collaboration = CollaborationStore(base_dir=tmp_path / "collaboration")
    rooms = create_team_rooms_router(state_path=tmp_path / "team_rooms.json")
    app = FastAPI()
    app.include_router(rooms)
    app.include_router(
        create_cowork_group_router(
            store=group_store,
            collaboration_store=collaboration,
            team_rooms_router=rooms,
        )
    )
    client = TestClient(app)
    room_id = client.post(
        "/api/teams",
        json={"name": "Uncertain room", "members": [{"name": "worker"}]},
    ).json()["id"]
    real_room_by_id = collaboration.room_by_id
    probe_calls = 0

    def fail_second_room_probe(target_room_id):
        nonlocal probe_calls
        probe_calls += 1
        if probe_calls == 2:
            raise RuntimeError("room visibility probe unavailable")
        return real_room_by_id(target_room_id)

    monkeypatch.setattr(collaboration, "room_by_id", fail_second_room_probe)
    monkeypatch.setattr(
        rooms,
        "bind_team_thread",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("binder failed")),
    )

    response = client.post(
        "/api/collab/thread-uncertain-link/link-room",
        json={"room_id": room_id},
    )

    assert response.status_code == 409, response.json()
    assert response.json()["detail"]["code"] == "ROOM_LINK_RECOVERY_PENDING"
    assert response.json()["detail"]["recovery_recorded"] is True


def test_app_cowork_router_uses_shared_runtime_store(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    app = create_app(journal_path=tmp_path / "data" / "events.jsonl")
    client = TestClient(app)

    response = client.post(
        "/api/cowork/thread-shared/tasks",
        json={"assignee": "worker", "prompt": "background check"},
    )

    assert response.status_code == 200
    task = response.json()["task"]
    stored = app.state.cowork_async_store.get(task["task_id"])
    assert stored is not None
    assert stored.prompt == "background check"
    assert app.state.cowork_runtime.async_store is app.state.cowork_async_store

    summary = client.get("/api/cowork/thread-shared/tasks/summary").json()
    assert summary["task_counts"]["pending"] == 1
    assert summary["runner_enabled"] is False


def test_health_endpoint_aggregates_runner_tasks_presence(tmp_path) -> None:
    from runtime.memory.cowork.async_work import AsyncWorkStore

    store = GroupStore(base_dir=tmp_path)
    app = FastAPI()
    app.include_router(create_cowork_group_router(store=store))
    c = TestClient(app)
    t = "thread-health"
    c.post(f"/api/cowork/{t}/members", json={"target_id": "user", "kind": "human"})
    c.post(f"/api/cowork/{t}/members", json={"target_id": "alice", "kind": "agent"})

    # Seed a failed task via the same-dir store the router reads.
    aw = AsyncWorkStore(base_dir=store.base_dir, group_store=store)
    task = aw.assign(t, "alice", "do x", actor="user")
    assert aw.claim(task.task_id)
    aw.fail(task.task_id, "boom")

    h = c.get(f"/api/cowork/{t}/health").json()
    assert h["roster_size"] == 2
    assert h["mode"] in ("chat", "cluster", "swarm")
    assert h["runner"]["enabled"] is False  # no runtime attached in this client
    assert h["tasks"]["counts"]["failed"] == 1
    assert h["tasks"]["failures"][0]["error"] == "boom"
    assert h["presence"]["members"] == 2
    assert len(h["recent_events"]) >= 2


def test_health_endpoint_includes_runner_status_when_runtime_attached(tmp_path) -> None:
    from runtime.memory.cowork.async_runner import AsyncWorkRunner
    from runtime.memory.cowork.async_work import AsyncWorkStore
    from runtime.memory.cowork.collaboration_store import CollaborationStore
    from runtime.memory.cowork.runtime import CoworkRuntime

    store = GroupStore(base_dir=tmp_path)
    async_store = AsyncWorkStore(base_dir=store.base_dir, group_store=store)
    runner = AsyncWorkRunner(async_store, store, lambda _task, _context: "done")
    runtime = CoworkRuntime(
        group_store=store,
        async_store=async_store,
        collaboration_store=CollaborationStore(base_dir=store.base_dir),
        runner=runner,
        runner_enabled=True,
        runner_reason="test runner",
    )
    app = FastAPI()
    app.include_router(create_cowork_group_router(store=store, runtime=runtime))
    c = TestClient(app)
    t = "thread-runner-health"
    async_store.assign(t, "worker", "background work", actor="user")
    assert runner.tick_once() == 1

    h = c.get(f"/api/cowork/{t}/health").json()

    assert h["runner"]["enabled"] is True
    assert h["runner"]["reason"] == "test runner"
    assert h["runner"]["status"]["total_ticks"] == 1
    assert h["runner"]["status"]["last_ran_count"] == 1
    assert h["runner"]["status"]["last_error"] is None


def test_legacy_project_mode_attaches_idle_project_and_normalizes_to_chat(tmp_path) -> None:
    """Old clients may still submit ``project`` to the mode endpoint.

    Preserve their intent by attaching a Project for the workbench, but never
    persist a fourth response mode or start milestone execution.
    """
    from runtime.projectos.store import ProjectStore

    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)
    project_store = ProjectStore(base_dir=str(tmp_path / "projects"))
    app = FastAPI()
    app.include_router(
        create_cowork_group_router(
            store=GroupStore(base_dir=tmp_path),
            project_store=project_store,
        )
    )
    c = TestClient(app)
    t = "thread-proj-mode"
    c.post(f"/api/cowork/{t}/members", json={"target_id": "alice", "kind": "agent"})
    c.post(f"/api/cowork/{t}/members", json={"target_id": "bob", "kind": "agent"})

    r = c.post(f"/api/cowork/{t}/mode", json={"mode": "project"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"]["mode"] == "chat"
    assert "bound_project_id" in body["state"]

    # The project is now visible via the workbench tab's by-thread lookup.
    state = project_store.project_for_thread(t)
    assert state is not None
    assert state.id == body["state"]["bound_project_id"]
    assert all(
        task.status == "pending"
        for milestone in project_store.milestones_for(state.id)
        for task in project_store.tasks_for_milestone(milestone.id)
    )
    assert "project.run_from_group" not in {
        event["kind"] for event in project_store.events_for_project(state.id)
    }

    # Switching again reuses the same project (no duplicate creation).
    r2 = c.post(f"/api/cowork/{t}/mode", json={"mode": "project"})
    assert r2.json()["state"]["mode"] == "chat"
    assert r2.json()["state"]["bound_project_id"] == state.id


def test_authenticated_legacy_project_mode_uses_principal_scope(tmp_path) -> None:
    from runtime.projectos.store import ProjectStore

    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-alice",
    )
    thread_store = ThreadStateStore(
        path=tmp_path / "threads.jsonl",
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )
    thread_store.ensure_thread(
        "thread-auth-project",
        metadata={"owner_actor_id": "alice", "tenant_id": "tenant-a"},
        values={"title": "Owned project"},
    )
    group_store = GroupStore(base_dir=tmp_path / "groups")
    group_store.append(
        "thread-auth-project",
        MemberEvent(action="invite", actor="alice", target_id="general"),
    )
    project_store = ProjectStore(base_dir=tmp_path / "projects")
    app = FastAPI()
    app.include_router(
        create_cowork_group_router(
            store=group_store,
            runtime=SimpleNamespace(thread_store=thread_store),
            project_store=project_store,
            identity_store=identities,
            require_auth=True,
        )
    )

    response = TestClient(app).post(
        "/api/cowork/thread-auth-project/mode",
        headers={"Authorization": "Bearer sk-alice"},
        json={"mode": "project"},
    )

    assert response.status_code == 200
    project = project_store.project_for_thread("thread-auth-project")
    assert project is not None
    assert project.owner_id == "alice"
    assert project.tenant_id == "tenant-a"


def test_legacy_project_mode_concurrent_attach_uses_one_cas_winner(
    tmp_path,
    monkeypatch,
) -> None:
    from runtime.projectos.engine import ProjectEngine
    from runtime.projectos.store import ProjectStore

    group_store = GroupStore(base_dir=tmp_path / "groups")
    group_store.append(
        "thread-project-cas",
        MemberEvent(action="invite", actor="local", target_id="general"),
    )
    project_store = ProjectStore(base_dir=tmp_path / "projects")
    app = FastAPI()
    app.include_router(
        create_cowork_group_router(
            store=group_store,
            project_store=project_store,
        )
    )
    barrier = Barrier(4)
    real_plan = ProjectEngine.plan

    def synchronized_plan(self, name, goal, *, project_id=None):
        barrier.wait()
        return real_plan(self, name, goal, project_id=project_id)

    monkeypatch.setattr(ProjectEngine, "plan", synchronized_plan)

    def attach(_index: int) -> tuple[int, str]:
        response = TestClient(app).post(
            "/api/cowork/thread-project-cas/mode",
            json={"mode": "project"},
        )
        return response.status_code, response.json()["state"]["bound_project_id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(attach, range(4)))

    assert {status for status, _project_id in responses} == {200}
    winner_ids = {project_id for _status, project_id in responses}
    assert len(winner_ids) == 1
    winner_id = next(iter(winner_ids))
    projects = project_store.list_projects()
    assert len(projects) == 4
    orphan_ids = {project.id for project in projects} - {winner_id}
    assert len(orphan_ids) == 3
    for orphan_id in orphan_ids:
        orphan_events = project_store.events_for_project(orphan_id)
        assert any(
            event["kind"] == "project.group_attach_orphaned"
            and event["payload"]["winner_project_id"] == winner_id
            for event in orphan_events
        )

