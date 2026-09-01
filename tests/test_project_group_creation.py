"""Single-boundary project-group creation and durable recovery."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.cowork.collaboration_store import CollaborationStore
from runtime.memory.cowork.group import MemberEvent
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.room_messages import RoomMessageStore
from runtime.memory.threads import ThreadStateStore
from runtime.projectos.store import ProjectStore
from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway.cowork_group_router import create_cowork_group_router
from runtime.sensing.gateway.projects_router import create_projects_router
from runtime.sensing.gateway.team_rooms_router import create_team_rooms_router
from runtime.sensing.gateway.thread_workspace import verified_managed_workspace


def _stack(tmp_path: Path):
    projects = ProjectStore(base_dir=tmp_path / "projects")
    groups = GroupStore(base_dir=tmp_path / "cowork")
    collaboration = CollaborationStore(base_dir=tmp_path / "cowork")
    threads = ThreadStateStore(
        path=tmp_path / "threads.jsonl",
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )
    rooms = create_team_rooms_router(state_path=tmp_path / "rooms.json")
    app = FastAPI()
    app.include_router(rooms)
    app.include_router(
        create_cowork_group_router(
            store=groups,
            collaboration_store=collaboration,
            team_rooms_router=rooms,
        )
    )
    app.include_router(
        create_projects_router(
            store=projects,
            group_store=groups,
            collaboration_store=collaboration,
            team_rooms_router=rooms,
            thread_store=threads,
        )
    )
    return TestClient(app), projects, groups, collaboration, threads, rooms


def _create_body() -> dict:
    return {
        "name": "Atomic launch",
        "goal": "Ship without ghost state",
        "initialAgents": [
            {"id": "general", "displayName": "General"},
            {"id": "coder", "displayName": "Coder", "description": "Builds it"},
        ],
    }


def test_create_project_group_commits_every_surface(tmp_path: Path) -> None:
    client, projects, groups, collaboration, threads, _rooms = _stack(tmp_path)

    response = client.post("/api/projects/group", json=_create_body())

    assert response.status_code == 200, response.json()
    payload = response.json()
    project_id = payload["project"]["id"]
    thread_id = payload["thread_id"]
    room_id = payload["room"]["id"]
    assert len(payload["milestones"]) == 3
    assert projects.project_for_thread(thread_id).id == project_id
    thread = threads.get(thread_id)
    assert thread is not None
    assert thread["metadata"]["project_home"] is True
    assert thread["metadata"]["project_id"] == project_id
    assert thread["values"]["project_id"] == project_id
    state = groups.state(thread_id)
    assert state.room_id == room_id
    assert state.mode == "cluster"
    assert [member.id for member in state.roster] == ["general", "coder"]
    assert payload["room"]["thread_id"] == thread_id
    assert collaboration.session_id_for_room(room_id) == thread_id
    assert collaboration.room_for_session(thread_id)["metadata"]["project_id"] == project_id


def test_create_project_group_keeps_noncore_roles_as_members(tmp_path: Path) -> None:
    client, _projects, groups, _collaboration, threads, _rooms = _stack(tmp_path)

    response = client.post(
        "/api/projects/group",
        json={
            "name": "Expert group",
            "goal": "Use specialists without cloning an identity",
            "initialAgents": [
                {"id": "planner", "displayName": "Planner"},
                {"id": "installed_code_reviewer", "displayName": "Code Reviewer"},
            ],
        },
    )

    assert response.status_code == 200, response.json()
    thread_id = response.json()["thread_id"]
    thread = threads.get(thread_id)
    assert thread is not None
    assert thread["metadata"]["agent_name"] == "general"
    assert [member.id for member in groups.state(thread_id).roster] == [
        "general",
        "planner",
        "installed_code_reviewer",
    ]
    assert response.json()["room"]["leaderId"] == "general"


@pytest.mark.parametrize("repair_fails", [False, True])
def test_create_project_group_repairs_a_newer_binding_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repair_fails: bool,
) -> None:
    client, projects, groups, collaboration, threads, rooms = _stack(tmp_path)
    winner_id = client.post(
        "/api/projects",
        json={"name": "Canonical winner", "goal": "Own the final group generation"},
    ).json()["project"]["id"]
    stale_projection_ready = Event()
    release_stale_projection = Event()
    captured: dict[str, str] = {}
    real_set_room_project = collaboration.set_room_project_metadata

    def pause_after_stale_room_projection(
        session_id: str,
        project_id: str | None,
        **kwargs,
    ):
        projected = real_set_room_project(session_id, project_id, **kwargs)
        if kwargs.get("generation") == 1 and project_id != winner_id:
            captured.update(thread_id=session_id, stale_project_id=str(project_id or ""))
            stale_projection_ready.set()
            assert release_stale_projection.wait(timeout=5)
        return projected

    monkeypatch.setattr(
        collaboration,
        "set_room_project_metadata",
        pause_after_stale_room_projection,
    )
    real_set_thread_project = threads.set_project_binding_metadata

    def maybe_fail_winner_thread_projection(thread_id, project_id, **kwargs):
        if repair_fails and kwargs.get("generation") == 3:
            raise RuntimeError("injected winner repair failure")
        return real_set_thread_project(thread_id, project_id, **kwargs)

    monkeypatch.setattr(
        threads,
        "set_project_binding_metadata",
        maybe_fail_winner_thread_projection,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        response_future = pool.submit(client.post, "/api/projects/group", json=_create_body())
        assert stale_projection_ready.wait(timeout=5)
        thread_id = captured["thread_id"]
        stale_project_id = captured["stale_project_id"]
        try:
            detached, clear_generation = projects.unbind_thread_versioned(
                thread_id,
                expected_project_id=stale_project_id,
            )
            assert detached is not None
            assert clear_generation == 2
            canonical, winner_generation = projects.bind_thread_versioned(thread_id, winner_id)
            assert canonical.id == winner_id
            assert winner_generation == 3
        finally:
            release_stale_projection.set()
        response = response_future.result(timeout=5)

    assert response.status_code == 409
    if repair_fails:
        detail = response.json()["detail"]
        assert detail["code"] == "PROJECT_GROUP_CREATION_RECOVERY_PENDING"
        recovery = next(
            event
            for event in projects.events_for_project(stale_project_id)
            if event["id"] == detail["recovery_event_id"]
        )
        assert recovery["payload"]["requested_project_id"] == stale_project_id
        assert recovery["payload"]["winner_project_id"] == winner_id
        assert recovery["payload"]["binding_generation"] == 3
        assert projects.binding_snapshot(thread_id)[0].id == winner_id
        return
    assert response.json()["detail"] == {
        "code": "PROJECT_BINDING_CHANGED",
        "message": "thread project binding changed while the group was being created",
        "thread_id": thread_id,
        "requested_project_id": stale_project_id,
        "winner_project_id": winner_id,
        "binding_generation": 3,
    }
    assert projects.get_project(stale_project_id) is not None
    assert projects.binding_snapshot(thread_id)[0].id == winner_id
    thread = threads.get(thread_id)
    assert thread["metadata"]["project_id"] == winner_id
    assert thread["metadata"]["project_binding_generation"] == 3
    room = collaboration.room_for_session(thread_id)
    assert room["metadata"]["project_id"] == winner_id
    assert room["metadata"]["project_binding_generation"] == 3
    room_id = groups.state(thread_id).room_id
    assert room_id is not None
    assert rooms.team_snapshot(room_id)["project_id"] == winner_id


@pytest.mark.parametrize(
    "failure_stage",
    [
        "thread_after_commit",
        "project_binding",
        "roster",
        "room_creation_after_commit",
        "room_binding_after_commit",
        "projection_after_commit",
    ],
)
def test_create_project_group_preserves_public_surfaces_for_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    client, projects, groups, collaboration, threads, rooms = _stack(tmp_path)
    created_thread_ids: list[str] = []
    real_ensure = threads.ensure_thread

    def capture_thread(thread_id, **kwargs):
        thread = real_ensure(thread_id, **kwargs)
        created_thread_ids.append(thread["thread_id"])
        return thread

    monkeypatch.setattr(threads, "ensure_thread", capture_thread)

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"injected {failure_stage} failure")

    if failure_stage == "thread_after_commit":

        def fail_after_thread_commit(thread_id, **kwargs):
            capture_thread(thread_id, **kwargs)
            fail()

        monkeypatch.setattr(threads, "ensure_thread", fail_after_thread_commit)
    elif failure_stage == "project_binding":
        monkeypatch.setattr(projects, "bind_thread_versioned", fail)
    elif failure_stage == "roster":
        monkeypatch.setattr(groups, "replace_agent_roster", fail)
    elif failure_stage == "room_creation_after_commit":
        real_room_create = rooms.create_team_from_payload

        def fail_after_room_creation(*args, **kwargs):
            real_room_create(*args, **kwargs)
            fail()

        rooms.create_team_from_payload = fail_after_room_creation
    elif failure_stage == "room_binding_after_commit":
        real_room_bind = rooms.bind_team_thread

        def fail_after_room_binding(*args, **kwargs):
            real_room_bind(*args, **kwargs)
            fail()

        rooms.bind_team_thread = fail_after_room_binding
    else:
        real_projection = collaboration.upsert_project_room

        def fail_after_projection(*args, **kwargs):
            real_projection(*args, **kwargs)
            fail()

        monkeypatch.setattr(collaboration, "upsert_project_room", fail_after_projection)

    response = client.post("/api/projects/group", json=_create_body())

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "PROJECT_GROUP_CREATION_RECOVERY_PENDING"
    assert len(created_thread_ids) == 1
    thread_id = created_thread_ids[0]
    project_id = detail["project_id"]
    assert detail["thread_id"] == thread_id
    assert "thread" in detail["surfaces"]
    assert projects.get_project(project_id) is not None
    assert threads.get(thread_id) is not None
    recovery = [
        event
        for event in projects.events_for_project(project_id)
        if event["kind"] == "project.group_creation_recovery_pending"
    ]
    assert len(recovery) == 1
    assert recovery[0]["id"] == detail["recovery_event_id"]
    assert recovery[0]["payload"]["creation_id"] == detail["creation_id"]


def test_pre_public_thread_failure_preserves_the_committed_project_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, groups, collaboration, threads, _rooms = _stack(tmp_path)
    attempted_threads: list[str] = []

    def fail_before_thread_commit(thread_id: str, **_kwargs):
        attempted_threads.append(thread_id)
        raise RuntimeError("injected pre-public thread failure")

    monkeypatch.setattr(threads, "ensure_thread", fail_before_thread_commit)
    response = client.post("/api/projects/group", json=_create_body())

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "PROJECT_GROUP_CREATION_RECOVERY_PENDING"
    assert detail["recovery_recorded"] is True
    assert [project.id for project in projects.list_projects()] == [detail["project_id"]]
    assert len(attempted_threads) == 1
    thread_id = attempted_threads[0]
    assert threads.get(thread_id) is None
    assert groups.events(thread_id) == []
    assert collaboration.room_for_session(thread_id) is None


def test_plan_failure_before_commit_leaves_no_project_or_recovery_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, _collaboration, _threads, _rooms = _stack(tmp_path)

    def fail_before_plan_commit(*_args, **_kwargs):
        raise RuntimeError("injected pre-commit plan failure")

    monkeypatch.setattr(projects, "create_project_plan", fail_before_plan_commit)

    response = client.post("/api/projects/group", json=_create_body())

    assert response.status_code == 500
    assert projects.list_projects() == []


def test_plan_commit_then_raise_is_recovered_by_the_preallocated_project_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, _collaboration, _threads, _rooms = _stack(tmp_path)
    real_create_plan = projects.create_project_plan

    def fail_after_plan_commit(*args, **kwargs):
        real_create_plan(*args, **kwargs)
        raise RuntimeError("injected post-commit plan failure")

    monkeypatch.setattr(projects, "create_project_plan", fail_after_plan_commit)

    response = client.post("/api/projects/group", json=_create_body())

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "PROJECT_GROUP_CREATION_RECOVERY_PENDING"
    assert detail["thread_id"] == ""
    assert detail["recovery_recorded"] is True
    assert [project.id for project in projects.list_projects()] == [detail["project_id"]]
    recovery = projects.events_for_project(detail["project_id"])
    recovery_event = next(event for event in recovery if event["id"] == detail["recovery_event_id"])
    assert recovery_event["kind"] == "project.group_creation_recovery_pending"


def test_thread_failure_preserves_a_project_changed_by_another_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, _collaboration, threads, _rooms = _stack(tmp_path)
    external_projects = ProjectStore(base_dir=tmp_path / "projects")
    ensure_ready = Event()
    release_ensure = Event()

    def fail_before_thread_commit(*_args, **_kwargs):
        ensure_ready.set()
        assert release_ensure.wait(timeout=5)
        raise RuntimeError("injected pre-public thread failure")

    monkeypatch.setattr(threads, "ensure_thread", fail_before_thread_commit)

    with ThreadPoolExecutor(max_workers=1) as pool:
        response_future = pool.submit(
            client.post,
            "/api/projects/group",
            json=_create_body(),
        )
        assert ensure_ready.wait(timeout=5)
        try:
            project_id = projects.list_projects()[0].id
            external_event = external_projects.append_event(
                project_id,
                kind="project.external_intervention",
                payload={"source": "second-store"},
            )
        finally:
            release_ensure.set()
        response = response_future.result(timeout=5)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "PROJECT_GROUP_CREATION_RECOVERY_PENDING"
    assert detail["recovery_recorded"] is True
    assert projects.get_project(project_id) is not None
    event_ids = {event["id"] for event in projects.events_for_project(project_id)}
    assert external_event["id"] in event_ids
    assert detail["recovery_event_id"] in event_ids


@pytest.mark.parametrize(
    "external_write",
    [
        "thread",
        "collaboration_message",
        "group_event",
        "group_blackboard",
        "team_update",
        "team_message",
    ],
)
def test_late_failure_preserves_external_writes_on_every_public_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    external_write: str,
) -> None:
    client, projects, groups, collaboration, threads, rooms = _stack(tmp_path)
    real_projection = collaboration.upsert_project_room
    projection_ready = Event()
    release_projection = Event()
    created: dict[str, str] = {}

    def fail_after_paused_projection(*, session_id: str, room: dict, **kwargs):
        projected = real_projection(session_id=session_id, room=room, **kwargs)
        created.update(thread_id=session_id, room_id=str(projected["id"]))
        projection_ready.set()
        assert release_projection.wait(timeout=5)
        raise RuntimeError("injected late projection failure")

    monkeypatch.setattr(
        collaboration,
        "upsert_project_room",
        fail_after_paused_projection,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        response_future = pool.submit(
            client.post,
            "/api/projects/group",
            json=_create_body(),
        )
        assert projection_ready.wait(timeout=5)
        thread_id = created["thread_id"]
        room_id = created["room_id"]
        try:
            if external_write == "thread":
                second_threads = ThreadStateStore(
                    path=tmp_path / "threads.jsonl",
                    index_enabled=False,
                    search_enabled=False,
                    feedback_enabled=False,
                )
                second_threads.update_state(thread_id, values={"title": "LIVE"})
            elif external_write == "collaboration_message":
                CollaborationStore(base_dir=tmp_path / "cowork").append_message(
                    thread_id,
                    room_id=room_id,
                    text="LIVE",
                    participant_id="human",
                )
            elif external_write == "group_event":
                GroupStore(base_dir=tmp_path / "cowork").append(
                    thread_id,
                    MemberEvent(action="mode", actor="external", mode="swarm"),
                )
            elif external_write == "group_blackboard":
                GroupStore(base_dir=tmp_path / "cowork").blackboard(thread_id).write(
                    "live",
                    {"preserve": True},
                    writer="external",
                )
            elif external_write == "team_update":
                updated = client.put(
                    f"/api/teams/{room_id}",
                    json={
                        "name": "LIVE",
                        "members": [{"name": "general"}, {"name": "coder"}],
                        "leaderId": "general",
                    },
                )
                assert updated.status_code == 200, updated.json()
            else:
                RoomMessageStore(base_dir=tmp_path / "teamroom").append(
                    room_id,
                    text="LIVE",
                    participant_id="human",
                )
        finally:
            release_projection.set()
        response = response_future.result(timeout=5)

    assert response.status_code == 409
    detail = response.json()["detail"]
    project_id = detail["project_id"]
    assert detail["recovery_recorded"] is True
    assert projects.get_project(project_id) is not None
    assert threads.get(thread_id) is not None
    assert groups.events(thread_id)
    assert rooms.team_snapshot(room_id) is not None
    assert collaboration.room_by_id(room_id) is not None
    if external_write == "thread":
        assert (
            ThreadStateStore(
                path=tmp_path / "threads.jsonl",
                index_enabled=False,
                search_enabled=False,
                feedback_enabled=False,
            ).get(thread_id)["values"]["title"]
            == "LIVE"
        )
    elif external_write == "collaboration_message":
        assert collaboration.messages_for_room(room_id)[-1]["text"] == "LIVE"
    elif external_write == "group_event":
        assert groups.events(thread_id)[-1].mode == "swarm"
    elif external_write == "group_blackboard":
        assert groups.blackboard_snapshot(thread_id)["live"] == {"preserve": True}
    elif external_write == "team_update":
        assert rooms.team_snapshot(room_id)["name"] == "LIVE"
    else:
        assert (
            RoomMessageStore(base_dir=tmp_path / "teamroom").history(room_id)[-1]["text"] == "LIVE"
        )


def test_link_room_rejects_both_room_and_thread_double_binding(tmp_path: Path) -> None:
    client, _projects, groups, collaboration, _threads, _rooms = _stack(tmp_path)
    room_a = client.post(
        "/api/teams",
        json={"name": "A", "members": [{"name": "general"}]},
    ).json()
    room_b = client.post(
        "/api/teams",
        json={"name": "B", "members": [{"name": "general"}]},
    ).json()

    linked = client.post("/api/collab/thread-a/link-room", json={"room_id": room_a["id"]})
    assert linked.status_code == 200, linked.json()

    room_conflict = client.post(
        "/api/collab/thread-b/link-room",
        json={"room_id": room_a["id"]},
    )
    assert room_conflict.status_code == 409
    assert groups.state("thread-b").room_id is None

    thread_conflict = client.post(
        "/api/collab/thread-a/link-room",
        json={"room_id": room_b["id"]},
    )
    assert thread_conflict.status_code == 409
    assert groups.state("thread-a").room_id == room_a["id"]
    assert collaboration.session_id_for_room(room_a["id"]) == "thread-a"

    listed = {room["id"]: room for room in client.get("/api/teams").json()["teams"]}
    assert listed[room_a["id"]]["thread_id"] == "thread-a"
    assert listed[room_b["id"]]["thread_id"] is None


def test_authenticated_project_group_owns_and_preserves_managed_workspace_for_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-alice",
    )
    projects = ProjectStore(base_dir=tmp_path / "projects")
    groups = GroupStore(base_dir=tmp_path / "cowork")
    collaboration = CollaborationStore(base_dir=tmp_path / "cowork")
    threads = ThreadStateStore(
        path=tmp_path / "threads.jsonl",
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )
    workspace_root = tmp_path / "workspaces"
    rooms = create_team_rooms_router(
        state_path=tmp_path / "rooms.json",
        identity_store=identities,
        require_auth=True,
    )
    app = FastAPI()
    app.include_router(rooms)
    app.include_router(
        create_projects_router(
            store=projects,
            group_store=groups,
            collaboration_store=collaboration,
            team_rooms_router=rooms,
            thread_store=threads,
            workspace_root=workspace_root,
            identity_store=identities,
            require_auth=True,
        )
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer sk-alice"}

    created = client.post("/api/projects/group", headers=headers, json=_create_body())
    assert created.status_code == 200, created.json()
    payload = created.json()
    thread_id = payload["thread_id"]
    thread = threads.get(thread_id)
    assert payload["project"]["owner_id"] == "alice"
    assert payload["project"]["tenant_id"] == "tenant-a"
    assert thread["metadata"]["owner_actor_id"] == "alice"
    assert thread["metadata"]["tenant_id"] == "tenant-a"
    assert (
        verified_managed_workspace(
            workspace_root,
            thread_id=thread_id,
            metadata=thread["metadata"],
        )
        is not None
    )

    failed_workspace: list[Path] = []

    def fail_projection(*_args, **_kwargs):
        latest = threads.search(limit=1)[0]
        failed_workspace.append(Path(latest["metadata"]["workspace_path"]))
        raise RuntimeError("injected authenticated projection failure")

    monkeypatch.setattr(collaboration, "upsert_project_room", fail_projection)
    failed = client.post("/api/projects/group", headers=headers, json=_create_body())
    assert failed.status_code == 409
    assert failed.json()["detail"]["recovery_recorded"] is True
    assert len(projects.list_projects()) == 2
    assert len(failed_workspace) == 1
    assert failed_workspace[0].exists()


def test_recovery_journal_failure_never_falls_back_to_destructive_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, _collaboration, threads, _rooms = _stack(tmp_path)
    real_bind = projects.bind_thread_versioned
    real_append = projects.append_event

    def fail_after_binding(*args, **kwargs):
        real_bind(*args, **kwargs)
        raise RuntimeError("injected public-boundary failure")

    def fail_recovery_event(project_id: str, *, kind: str, **kwargs):
        if kind == "project.group_creation_recovery_pending":
            raise RuntimeError("injected recovery journal failure")
        return real_append(project_id, kind=kind, **kwargs)

    monkeypatch.setattr(projects, "bind_thread_versioned", fail_after_binding)
    monkeypatch.setattr(projects, "append_event", fail_recovery_event)

    response = client.post("/api/projects/group", json=_create_body())

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "PROJECT_GROUP_CREATION_RECOVERY_PENDING"
    assert detail["recovery_recorded"] is False
    assert detail["recovery_event_id"] == ""
    assert projects.get_project(detail["project_id"]) is not None
    assert threads.get(detail["thread_id"]) is not None

