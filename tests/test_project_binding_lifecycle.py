"""A group/thread gains and loses Project OS as one optional capability."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.cowork import service
from runtime.memory.cowork.collaboration_store import CollaborationStore
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.threads import ThreadStateStore
from runtime.projectos.engine import ProjectEngine
from runtime.projectos.model import Project, Task
from runtime.projectos.store import ProjectDeleteInProgressError, ProjectStore
from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway.cowork_group_router import create_cowork_group_router
from runtime.sensing.gateway.projects_router import create_projects_router
from runtime.sensing.gateway.team_rooms_router import create_team_rooms_router


def _local_stack(tmp_path: Path):
    projects = ProjectStore(base_dir=tmp_path / "projects")
    groups = GroupStore(base_dir=tmp_path / "groups")
    collaboration = CollaborationStore(base_dir=tmp_path / "collaboration")
    threads = ThreadStateStore(
        path=tmp_path / "threads.jsonl",
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )

    def project_room(room: dict[str, Any]) -> None:
        collaboration.upsert_room_by_id(room)

    rooms = create_team_rooms_router(
        state_path=tmp_path / "rooms.json",
        room_projection=project_room,
        project_store=projects,
    )
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
    client = TestClient(app)
    thread_id = "thread-lifecycle"
    threads.ensure_thread(thread_id, values={"title": "Ordinary group"})
    for agent_id in ("general", "coder"):
        service.invite_member(
            groups,
            thread_id,
            actor="local",
            target_id=agent_id,
            kind="agent",
        )
    service.set_mode(groups, thread_id, actor="local", mode="swarm")
    room = client.post(
        "/api/teams",
        json={
            "name": "Ordinary room",
            "members": [{"name": "general"}, {"name": "coder"}],
        },
    ).json()
    linked = client.post(
        f"/api/collab/{thread_id}/link-room",
        json={"room_id": room["id"]},
    )
    assert linked.status_code == 200, linked.json()
    return client, projects, groups, collaboration, threads, room["id"], app


def _reopen_stack(tmp_path: Path):
    """Rebuild every store/router over the same durable files."""

    projects = ProjectStore(base_dir=tmp_path / "projects")
    groups = GroupStore(base_dir=tmp_path / "groups")
    collaboration = CollaborationStore(base_dir=tmp_path / "collaboration")
    threads = ThreadStateStore(
        path=tmp_path / "threads.jsonl",
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )

    def project_room(room: dict[str, Any]) -> None:
        collaboration.upsert_room_by_id(room)

    rooms = create_team_rooms_router(
        state_path=tmp_path / "rooms.json",
        room_projection=project_room,
        project_store=projects,
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
        )
    )
    return TestClient(app), projects, collaboration, threads


def test_from_group_is_idempotent_and_concurrent_safe(tmp_path: Path) -> None:
    _client, projects, _groups, _collaboration, _threads, _room_id, app = _local_stack(tmp_path)
    barrier = Barrier(4)

    def promote(index: int) -> tuple[int, dict]:
        barrier.wait()
        response = TestClient(app).post(
            "/api/projects/from-group/thread-lifecycle",
            json={"name": f"Attempt {index}", "goal": f"Goal {index}"},
        )
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(promote, range(4)))

    assert {status for status, _body in responses} == {200}
    project_ids = {body["project"]["id"] for _status, body in responses}
    assert len(project_ids) == 1
    project_id = project_ids.pop()
    assert projects.project_for_thread("thread-lifecycle").id == project_id
    all_projects = projects.list_projects()
    assert project_id in {project.id for project in all_projects}
    for orphan in (project for project in all_projects if project.id != project_id):
        assert any(
            event["kind"] == "project.group_attach_orphaned"
            and event["payload"]["winner_project_id"] == project_id
            for event in projects.events_for_project(orphan.id)
        )
    event_kinds = [event["kind"] for event in projects.events_for_project(project_id)]
    assert event_kinds.count("project.planned") == 1
    assert event_kinds.count("project.attached_from_group") == 1
    assert sum(bool(body["reused"]) for _status, body in responses) == 3


@pytest.mark.parametrize("active", [False, True])
def test_move_requires_explicit_detach_without_changing_existing_read_models(
    tmp_path: Path,
    active: bool,
) -> None:
    client, projects, _groups, collaboration, threads, room_id, _app = _local_stack(tmp_path)
    attached = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={"name": "Existing project", "goal": "Keep the current capability"},
    )
    assert attached.status_code == 200, attached.json()
    current_project_id = attached.json()["project"]["id"]
    if active:
        current = projects.get_project(current_project_id)
        assert current is not None
        current.status = "running"
        current.started_at = "2026-08-22T00:00:00+00:00"
        projects.save_project(current)
    requested = client.post(
        "/api/projects",
        json={"name": "Requested project", "goal": "Require an explicit detach"},
    )
    assert requested.status_code == 200, requested.json()
    requested_project_id = requested.json()["project"]["id"]
    snapshots = {
        "thread": json.loads(json.dumps(threads.get("thread-lifecycle"))),
        "room": json.loads(json.dumps(collaboration.room_for_session("thread-lifecycle"))),
        "team": client.get(f"/api/teams/{room_id}").json(),
        "tasks": collaboration.tasks_for_session("thread-lifecycle"),
    }

    response = client.post(
        "/api/projects/move",
        json={
            "thread_id": "thread-lifecycle",
            "project_id": requested_project_id,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "PROJECT_MOVE_REQUIRES_DETACH",
        "message": "thread is already bound to another project; detach it before moving",
        "thread_id": "thread-lifecycle",
        "current_project_id": current_project_id,
        "requested_project_id": requested_project_id,
        "detach_required": True,
    }
    assert projects.project_for_thread("thread-lifecycle").id == current_project_id
    assert threads.get("thread-lifecycle") == snapshots["thread"]
    assert collaboration.room_for_session("thread-lifecycle") == snapshots["room"]
    assert client.get(f"/api/teams/{room_id}").json() == snapshots["team"]
    assert collaboration.tasks_for_session("thread-lifecycle") == snapshots["tasks"]
    assert projects.get_project(requested_project_id).execution_thread_id == ""


def test_move_refuses_a_target_already_bound_to_an_execution_thread(tmp_path: Path) -> None:
    client, projects, _groups, collaboration, threads, room_id, _app = _local_stack(tmp_path)
    planned = client.post(
        "/api/projects",
        json={"name": "Already executing", "goal": "Keep its execution boundary"},
    )
    assert planned.status_code == 200, planned.json()
    project_id = planned.json()["project"]["id"]
    projects.bind_thread("thread-execution", project_id)
    started = projects.start_project_if_bound(project_id, "thread-execution")
    assert started is not None and started.started_at
    snapshots = {
        "thread": json.loads(json.dumps(threads.get("thread-lifecycle"))),
        "room": json.loads(json.dumps(collaboration.room_for_session("thread-lifecycle"))),
        "team": client.get(f"/api/teams/{room_id}").json(),
    }

    response = client.post(
        "/api/projects/move",
        json={"thread_id": "thread-lifecycle", "project_id": project_id},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "PROJECT_ALREADY_BOUND",
        "message": "project is already bound to another thread; detach it first",
        "project_id": project_id,
        "canonical_thread_id": "thread-execution",
        "requested_thread_id": "thread-lifecycle",
    }
    assert projects.project_for_thread("thread-execution").id == project_id
    assert projects.project_for_thread("thread-lifecycle") is None
    assert projects.get_project(project_id).execution_thread_id == "thread-execution"
    assert threads.get("thread-lifecycle") == snapshots["thread"]
    assert collaboration.room_for_session("thread-lifecycle") == snapshots["room"]
    assert client.get(f"/api/teams/{room_id}").json() == snapshots["team"]


def test_move_refuses_blocked_target_even_without_started_at(tmp_path: Path) -> None:
    client, projects, _groups, collaboration, threads, room_id, _app = _local_stack(tmp_path)
    planned = client.post(
        "/api/projects",
        json={"name": "Blocked target", "goal": "Require explicit operator recovery"},
    )
    assert planned.status_code == 200, planned.json()
    project_id = planned.json()["project"]["id"]
    blocked = projects.get_project(project_id)
    assert blocked is not None
    blocked.status = "blocked"
    blocked.started_at = ""
    blocked.execution_thread_id = ""
    projects.save_project(blocked)
    snapshots = {
        "thread": json.loads(json.dumps(threads.get("thread-lifecycle"))),
        "room": json.loads(json.dumps(collaboration.room_for_session("thread-lifecycle"))),
        "team": client.get(f"/api/teams/{room_id}").json(),
    }

    response = client.post(
        "/api/projects/move",
        json={"thread_id": "thread-lifecycle", "project_id": project_id},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "TARGET_PROJECT_ACTIVE",
        "message": "target project is already executing on another thread",
        "thread_id": "thread-lifecycle",
        "project_id": project_id,
        "execution_thread_id": "",
        "status": "blocked",
        "started_at": "",
    }
    assert projects.project_for_thread("thread-lifecycle") is None
    assert projects.get_project(project_id).execution_thread_id == ""
    assert threads.get("thread-lifecycle") == snapshots["thread"]
    assert collaboration.room_for_session("thread-lifecycle") == snapshots["room"]
    assert client.get(f"/api/teams/{room_id}").json() == snapshots["team"]


def test_move_cas_loser_returns_the_canonical_binding_without_stale_projections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, collaboration, threads, _room_id, _app = _local_stack(tmp_path)
    requested = client.post(
        "/api/projects",
        json={"name": "CAS loser", "goal": "Never overwrite the winner"},
    ).json()["project"]["id"]
    winner = client.post(
        "/api/projects",
        json={"name": "CAS winner", "goal": "Remain authoritative"},
    ).json()["project"]["id"]
    thread_before = json.loads(json.dumps(threads.get("thread-lifecycle")))
    room_before = json.loads(json.dumps(collaboration.room_for_session("thread-lifecycle")))
    original_bind = projects.bind_thread_if_absent_versioned
    raced = False

    def install_winner_before_cas(thread_id, project_id, **kwargs):
        nonlocal raced
        if not raced:
            raced = True
            projects.bind_thread(thread_id, winner)
        return original_bind(thread_id, project_id, **kwargs)

    monkeypatch.setattr(
        projects,
        "bind_thread_if_absent_versioned",
        install_winner_before_cas,
    )

    response = client.post(
        "/api/projects/move",
        json={"thread_id": "thread-lifecycle", "project_id": requested},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "PROJECT_MOVE_REQUIRES_DETACH",
        "message": "thread is already bound to another project; detach it before moving",
        "thread_id": "thread-lifecycle",
        "current_project_id": winner,
        "requested_project_id": requested,
        "detach_required": True,
    }
    assert projects.project_for_thread("thread-lifecycle").id == winner
    assert threads.get("thread-lifecycle") == thread_before
    assert collaboration.room_for_session("thread-lifecycle") == room_before


def test_move_projects_every_group_read_model_and_is_idempotent(tmp_path: Path) -> None:
    client, projects, _groups, collaboration, threads, room_id, _app = _local_stack(tmp_path)
    planned = client.post(
        "/api/projects",
        json={"name": "Move safely", "goal": "Project every read model"},
    )
    assert planned.status_code == 200, planned.json()
    project = planned.json()["project"]
    project_id = project["id"]
    milestone_id = project["milestone_ids"][0]
    projects.save_task(
        Task(
            id="T-move-projection",
            milestone_id=milestone_id,
            type="code",
            goal="Appear in the group task board",
        )
    )

    first = client.post(
        "/api/projects/move",
        json={"thread_id": "thread-lifecycle", "project_id": project_id},
    )
    retry = client.post(
        "/api/projects/move",
        json={"thread_id": "thread-lifecycle", "project_id": project_id},
    )

    assert first.status_code == retry.status_code == 200
    assert projects.project_for_thread("thread-lifecycle").id == project_id
    thread = threads.get("thread-lifecycle")
    assert thread["metadata"]["project_id"] == project_id
    assert thread["values"]["project_id"] == project_id
    room = collaboration.room_for_session("thread-lifecycle")
    assert room["metadata"]["project_id"] == project_id
    assert room["is_project_group"] is True
    assert client.get(f"/api/teams/{room_id}").json()["join_policy"] == "apply_then_join"
    assert [task["id"] for task in collaboration.project_tasks_for_project(project_id)] == [
        "T-move-projection"
    ]


def test_move_projection_failure_preserves_binding_and_retry_converges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, collaboration, threads, room_id, _app = _local_stack(tmp_path)
    planned = client.post(
        "/api/projects",
        json={"name": "Recover move", "goal": "Retry incomplete projections"},
    )
    assert planned.status_code == 200, planned.json()
    project = planned.json()["project"]
    project_id = project["id"]
    projects.save_task(
        Task(
            id="T-move-recovery",
            milestone_id=project["milestone_ids"][0],
            type="code",
            goal="Remain idempotent across projection retry",
        )
    )
    real_setter = collaboration.set_room_project_metadata
    failed = False

    def fail_first_room_projection(session_id, projected_project_id, **kwargs):
        nonlocal failed
        if projected_project_id and not failed:
            failed = True
            raise RuntimeError("injected move projection failure")
        return real_setter(session_id, projected_project_id, **kwargs)

    monkeypatch.setattr(
        collaboration,
        "set_room_project_metadata",
        fail_first_room_projection,
    )

    first = client.post(
        "/api/projects/move",
        json={"thread_id": "thread-lifecycle", "project_id": project_id},
    )

    assert first.status_code == 500
    assert first.json()["detail"] == {
        "code": "PROJECT_PROJECTION_RECOVERY_REQUIRED",
        "message": "project binding was preserved but group projections need recovery",
        "project_id": project_id,
        "thread_id": "thread-lifecycle",
        "recovery_recorded": True,
        "recovery": {
            "method": "POST",
            "path": "/api/projects/move",
            "body": {
                "thread_id": "thread-lifecycle",
                "project_id": project_id,
            },
        },
    }
    assert projects.project_for_thread("thread-lifecycle").id == project_id
    assert threads.get("thread-lifecycle")["metadata"]["project_id"] == project_id
    assert [event["kind"] for event in projects.events_for_project(project_id)].count(
        "project.group_projection_recovery_pending"
    ) == 1

    retry = client.post(
        "/api/projects/move",
        json={"thread_id": "thread-lifecycle", "project_id": project_id},
    )

    assert retry.status_code == 200, retry.json()
    assert (
        collaboration.room_for_session("thread-lifecycle")["metadata"]["project_id"] == project_id
    )
    assert client.get(f"/api/teams/{room_id}").json()["join_policy"] == "apply_then_join"
    assert [task["id"] for task in collaboration.project_tasks_for_project(project_id)] == [
        "T-move-recovery"
    ]


def test_late_move_projection_converges_to_the_new_authoritative_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, collaboration, threads, room_id, app = _local_stack(tmp_path)
    first = client.post(
        "/api/projects",
        json={"name": "First mover", "goal": "Lose a delayed projection"},
    ).json()["project"]["id"]
    winner = client.post(
        "/api/projects",
        json={"name": "Canonical winner", "goal": "Own every final read model"},
    ).json()["project"]["id"]
    projection_paused = Barrier(2)
    release_projection = Event()
    real_upsert_room = collaboration.upsert_project_room

    def pause_first_room_projection(*, session_id, room, **kwargs):
        if isinstance(room, dict) and room.get("name") == "First mover":
            projection_paused.wait(timeout=5)
            assert release_projection.wait(timeout=5)
        return real_upsert_room(session_id=session_id, room=room, **kwargs)

    monkeypatch.setattr(
        collaboration,
        "upsert_project_room",
        pause_first_room_projection,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        delayed = pool.submit(
            TestClient(app).post,
            "/api/projects/move",
            json={"thread_id": "thread-lifecycle", "project_id": first},
        )
        projection_paused.wait(timeout=5)
        try:
            detached = client.request(
                "DELETE",
                "/api/projects/from-group/thread-lifecycle",
                json={"expected_project_id": first},
            )
            assert detached.status_code == 200, detached.json()
            moved = client.post(
                "/api/projects/move",
                json={"thread_id": "thread-lifecycle", "project_id": winner},
            )
            assert moved.status_code == 200, moved.json()
        finally:
            release_projection.set()
        stale_response = delayed.result(timeout=5)

    assert stale_response.status_code == 409
    assert stale_response.json()["detail"] == {
        "code": "PROJECT_BINDING_CHANGED",
        "message": "thread project binding changed while projections were updating",
        "thread_id": "thread-lifecycle",
        "requested_project_id": first,
        "winner_project_id": winner,
    }
    assert projects.thread_project_map()["thread-lifecycle"] == winner
    thread = threads.get("thread-lifecycle")
    assert thread["metadata"]["project_id"] == winner
    assert thread["values"]["project_id"] == winner
    room = collaboration.room_for_session("thread-lifecycle")
    assert room["metadata"]["project_id"] == winner
    team = client.get(f"/api/teams/{room_id}").json()
    assert team["project_id"] == winner
    assert team["join_policy"] == "apply_then_join"


def test_late_move_projection_clears_with_authoritative_tombstone_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, collaboration, threads, room_id, app = _local_stack(tmp_path)
    project_id = client.post(
        "/api/projects",
        json={"name": "Late clear", "goal": "Converge to the unbound tombstone"},
    ).json()["project"]["id"]
    projection_ready = Event()
    release_projection = Event()
    real_upsert_room = collaboration.upsert_project_room

    def pause_project_room(
        *, session_id: str, room: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        if isinstance(room, dict) and room.get("name") == "Late clear":
            projection_ready.set()
            assert release_projection.wait(timeout=5)
        return real_upsert_room(session_id=session_id, room=room, **kwargs)

    monkeypatch.setattr(collaboration, "upsert_project_room", pause_project_room)
    with ThreadPoolExecutor(max_workers=1) as pool:
        delayed = pool.submit(
            TestClient(app).post,
            "/api/projects/move",
            json={"thread_id": "thread-lifecycle", "project_id": project_id},
        )
        assert projection_ready.wait(timeout=5)
        _detached, generation = projects.unbind_thread_versioned(
            "thread-lifecycle",
            expected_project_id=project_id,
        )
        assert generation == 2
        release_projection.set()
        response = delayed.result(timeout=5)

    assert response.status_code == 409
    assert projects.binding_snapshot("thread-lifecycle") == (None, 2)
    thread = threads.get("thread-lifecycle")
    assert thread["metadata"].get("project_id") is None
    assert thread["metadata"]["project_binding_generation"] == 2
    room = collaboration.room_for_session("thread-lifecycle")
    assert room is not None
    assert room["metadata"].get("project_id") is None
    assert room["metadata"]["project_binding_generation"] == 2
    assert client.get(f"/api/teams/{room_id}").json().get("project_id") is None


def test_late_standalone_projection_cannot_recreate_a_room_after_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, collaboration, _threads, room_id, app = _local_stack(tmp_path)
    standalone_ready = Event()
    release_standalone = Event()
    real_upsert_project_room = collaboration.upsert_project_room

    def pause_standalone_projection(*, generation: int, **kwargs: Any) -> dict[str, Any]:
        if generation == 0:
            standalone_ready.set()
            assert release_standalone.wait(timeout=5)
        return real_upsert_project_room(generation=generation, **kwargs)

    monkeypatch.setattr(
        collaboration,
        "upsert_project_room",
        pause_standalone_projection,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        planned_future = pool.submit(
            TestClient(app).post,
            "/api/projects",
            json={"name": "Late standalone", "goal": "Keep the bound room canonical"},
        )
        assert standalone_ready.wait(timeout=5)
        project_id = projects.list_projects()[0].id
        try:
            moved = client.post(
                "/api/projects/move",
                json={"thread_id": "thread-lifecycle", "project_id": project_id},
            )
            assert moved.status_code == 200, moved.json()
        finally:
            release_standalone.set()
        planned = planned_future.result(timeout=5)

    assert planned.status_code == 200, planned.json()
    assert projects.binding_snapshot("thread-lifecycle")[0].id == project_id
    assert collaboration.room_for_session(f"project:{project_id}") is None
    room = collaboration.room_for_session("thread-lifecycle")
    assert room is not None
    assert room["id"] == room_id
    assert room["metadata"]["project_id"] == project_id
    assert room["metadata"]["project_binding_generation"] == 1


def test_three_generation_inverse_projections_cannot_overwrite_latest_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, collaboration, threads, room_id, app = _local_stack(tmp_path)
    first, second, third = [
        client.post(
            "/api/projects",
            json={"name": name, "goal": "Win only at the authoritative generation"},
        ).json()["project"]["id"]
        for name in ("First", "Second", "Third")
    ]
    first_projection_ready = Event()
    release_first_projection = Event()
    repair_projection_ready = Event()
    release_repair_projection = Event()
    real_thread_projection = threads.set_project_binding_metadata
    second_projection_calls = 0

    def pause_out_of_order_projection(
        thread_id: str,
        project_id: str | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal second_projection_calls
        generation = kwargs.get("generation")
        if project_id == first and generation == 1:
            first_projection_ready.set()
            assert release_first_projection.wait(timeout=5)
        elif project_id == second and generation == 3:
            second_projection_calls += 1
            if second_projection_calls == 2:
                repair_projection_ready.set()
                assert release_repair_projection.wait(timeout=5)
        return real_thread_projection(thread_id, project_id, **kwargs)

    monkeypatch.setattr(threads, "set_project_binding_metadata", pause_out_of_order_projection)

    with ThreadPoolExecutor(max_workers=1) as pool:
        delayed = pool.submit(
            TestClient(app).post,
            "/api/projects/move",
            json={"thread_id": "thread-lifecycle", "project_id": first},
        )
        assert first_projection_ready.wait(timeout=5)
        _old, clear_first = projects.unbind_thread_versioned(
            "thread-lifecycle",
            expected_project_id=first,
        )
        assert clear_first == 2
        _second, second_generation = projects.bind_thread_versioned(
            "thread-lifecycle",
            second,
        )
        assert second_generation == 3
        projected_second = client.post(
            "/api/projects/move",
            json={"thread_id": "thread-lifecycle", "project_id": second},
        )
        assert projected_second.status_code == 200, projected_second.json()

        release_first_projection.set()
        assert repair_projection_ready.wait(timeout=5)
        _old, clear_second = projects.unbind_thread_versioned(
            "thread-lifecycle",
            expected_project_id=second,
        )
        assert clear_second == 4
        _third, third_generation = projects.bind_thread_versioned(
            "thread-lifecycle",
            third,
        )
        assert third_generation == 5
        projected_third = client.post(
            "/api/projects/move",
            json={"thread_id": "thread-lifecycle", "project_id": third},
        )
        assert projected_third.status_code == 200, projected_third.json()
        release_repair_projection.set()
        stale_response = delayed.result(timeout=5)

    assert stale_response.status_code == 500
    canonical, generation = projects.binding_snapshot("thread-lifecycle")
    assert canonical is not None and canonical.id == third
    assert generation == 5
    thread = threads.get("thread-lifecycle")
    assert thread["metadata"]["project_id"] == third
    assert thread["metadata"]["project_binding_generation"] == 5
    room = collaboration.room_for_session("thread-lifecycle")
    assert room["metadata"]["project_id"] == third
    assert room["metadata"]["project_binding_generation"] == 5
    team = client.get(f"/api/teams/{room_id}").json()
    assert team["project_id"] == third


def test_thread_and_room_projection_generations_reject_late_writes(tmp_path: Path) -> None:
    threads = ThreadStateStore(
        path=tmp_path / "threads.jsonl",
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )
    threads.ensure_thread("thread-fence", values={"title": "Generation fence"})
    collaboration = CollaborationStore(base_dir=tmp_path / "collaboration")
    collaboration.upsert_room("thread-fence", {"id": "room-fence", "name": "Fence"})

    first_thread = threads.set_project_binding_metadata(
        "thread-fence",
        "P1",
        generation=1,
    )
    first_room = collaboration.set_room_project_metadata(
        "thread-fence",
        "P1",
        generation=1,
    )
    assert first_thread["metadata"]["project_binding_generation"] == 1
    assert first_room is not None
    assert first_room["metadata"]["project_binding_generation"] == 1

    # Same-generation retries are idempotent only for the same project.
    assert (
        threads.set_project_binding_metadata("thread-fence", "P1", generation=1)["metadata"]
        == first_thread["metadata"]
    )
    assert collaboration.set_room_project_metadata(
        "thread-fence",
        "P1",
        generation=1,
    ) == collaboration.room_for_session("thread-fence")
    with pytest.raises(RuntimeError):
        threads.set_project_binding_metadata("thread-fence", None, generation=1)
    with pytest.raises(RuntimeError):
        collaboration.set_room_project_metadata("thread-fence", None, generation=1)

    threads.set_project_binding_metadata("thread-fence", None, generation=2)
    collaboration.set_room_project_metadata("thread-fence", None, generation=2)
    with pytest.raises(RuntimeError):
        threads.set_project_binding_metadata("thread-fence", "P1", generation=1)
    with pytest.raises(RuntimeError):
        collaboration.set_room_project_metadata("thread-fence", "P1", generation=1)

    # Generic linked-room refreshes may change ordinary fields, but cannot
    # smuggle an unversioned project replacement through the merge path.
    collaboration.upsert_room(
        "thread-fence",
        {"id": "room-fence", "name": "Updated without binding fields"},
    )
    collaboration.upsert_room(
        "thread-fence",
        {"id": "room-fence", "project_id": "P2", "is_project_group": True},
    )
    room = collaboration.room_for_session("thread-fence")
    assert room is not None
    assert room["metadata"]["project_binding_generation"] == 2
    assert room["metadata"].get("project_id") is None

    # The tombstone survives deletion of the room JSON. Generic recreation
    # cannot smuggle a project binding back, and the late old generation is
    # still rejected by the independent durable fence.
    assert collaboration.delete_room_by_id("room-fence") is True
    recreated = collaboration.upsert_room(
        "thread-fence",
        {
            "id": "room-fence",
            "project_id": "P1",
            "is_project_group": True,
            "metadata": {"project_id": "P1", "project_binding_generation": 1},
        },
    )
    assert recreated.get("project_id") is None
    assert recreated.get("metadata", {}).get("project_id") is None
    with pytest.raises(RuntimeError, match="stale room project binding generation"):
        collaboration.set_room_project_metadata("thread-fence", "P1", generation=1)
    healed = collaboration.set_room_project_metadata("thread-fence", None, generation=2)
    assert healed is not None
    assert healed["metadata"]["project_binding_generation"] == 2

    threads.update_state(
        "thread-fence",
        metadata={
            "project_id": "P1",
            "project_home": True,
            "project_binding_generation": 1,
            "ordinary": "kept",
        },
        values={"project_id": "P1", "project_home": True, "draft": "kept"},
    )
    generic_thread = threads.get("thread-fence")
    assert generic_thread["metadata"].get("project_id") is None
    assert generic_thread["metadata"]["project_binding_generation"] == 2
    assert generic_thread["metadata"]["ordinary"] == "kept"
    assert generic_thread["values"].get("project_id") is None
    assert generic_thread["values"]["draft"] == "kept"


def test_project_task_generation_is_atomic_and_generic_writers_cannot_overwrite_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collaboration = CollaborationStore(base_dir=tmp_path)
    collaboration.upsert_room("thread-task-fence", {"id": "room-task-fence"})
    collaboration.set_room_project_metadata(
        "thread-task-fence",
        "P1",
        generation=1,
    )
    projected = collaboration.upsert_project_task(
        session_id="thread-task-fence",
        room_id="room-task-fence",
        project_id="P1",
        milestone_id="M1",
        task={"id": "T-project", "title": "Canonical project task"},
        binding_generation=1,
    )
    assert projected["metadata"]["project_binding_generation"] == 1

    with pytest.raises(RuntimeError, match="versioned project API"):
        collaboration.upsert_task(
            "thread-task-fence",
            {"id": "T-project", "room_id": "room-task-fence", "title": "Team overwrite"},
        )
    with pytest.raises(RuntimeError, match="versioned project API"):
        collaboration.upsert_task(
            "thread-task-fence",
            {
                "id": "T-smuggled",
                "room_id": "room-task-fence",
                "metadata": {"project_binding_generation": 1},
            },
        )

    winner = CollaborationStore(base_dir=tmp_path)
    begin_ready = Event()
    release_begin = Event()
    real_connect = collaboration._connect  # noqa: SLF001
    paused = False

    class PausedConnection:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def __enter__(self) -> PausedConnection:
            self._conn.__enter__()
            return self

        def __exit__(self, *args: Any) -> Any:
            return self._conn.__exit__(*args)

        def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> Any:
            nonlocal paused
            if sql == "BEGIN IMMEDIATE" and not paused:
                paused = True
                begin_ready.set()
                assert release_begin.wait(timeout=5)
            return self._conn.execute(sql, parameters)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._conn, name)

    monkeypatch.setattr(collaboration, "_connect", lambda: PausedConnection(real_connect()))
    with ThreadPoolExecutor(max_workers=1) as pool:
        late = pool.submit(
            collaboration.upsert_project_task,
            session_id="thread-task-fence",
            room_id="room-task-fence",
            project_id="P1",
            milestone_id="M1",
            task={"id": "T-late", "title": "Late old-generation task"},
            binding_generation=1,
        )
        assert begin_ready.wait(timeout=5)
        winner.set_room_project_metadata(
            "thread-task-fence",
            "P2",
            generation=2,
        )
        release_begin.set()
        with pytest.raises(RuntimeError, match="stale project task binding generation"):
            late.result(timeout=5)

    winner.set_room_project_metadata(
        "thread-task-fence",
        "P2",
        generation=2,
    )
    assert {task["id"] for task in collaboration.tasks_for_session("thread-task-fence")} == {
        "T-project"
    }


def test_active_worker_claim_blocks_intervene_recover_and_delete_until_unique_finalize(
    tmp_path: Path,
) -> None:
    client, projects, _groups, _collaboration, _threads, _room_id, _app = _local_stack(tmp_path)
    planned = client.post(
        "/api/projects",
        json={"name": "Claim fence", "goal": "Preserve the only external execution"},
    )
    assert planned.status_code == 200, planned.json()
    project_id = planned.json()["project"]["id"]
    project = projects.get_project(project_id)
    assert project is not None
    milestone = projects.milestones_for(project_id)[0]
    milestone.status = "in_progress"
    milestone.task_ids = ["T-claim-fence"]
    projects.save_milestone(project_id, milestone)
    projects.save_task(
        Task(
            id="T-claim-fence",
            milestone_id=milestone.id,
            type="code",
            goal="Execute once",
        )
    )
    project.current_ms = milestone.id
    projects.save_project(project)
    execute_entered = Event()
    release_execute = Event()
    execute_calls = 0

    def execute(_task: Task, _context: dict[str, Any]) -> str:
        nonlocal execute_calls
        execute_calls += 1
        execute_entered.set()
        assert release_execute.wait(timeout=5)
        return "one external result"

    engine = ProjectEngine(
        projects,
        generate_milestones=lambda _goal: [],
        decompose_tasks=lambda _milestone: [],
        execute_task=execute,
    )
    events_before = list(projects.events_for_project(project_id))
    second_worker = ProjectStore(base_dir=tmp_path / "projects")
    with ThreadPoolExecutor(max_workers=1) as pool:
        tick_future = pool.submit(engine.tick, project_id)
        assert execute_entered.wait(timeout=5)
        try:
            state = client.get(f"/api/projects/{project_id}").json()
            running_task = state["tasks"][milestone.id][0]
            assert running_task["status"] == "running"
            assert running_task["available_actions"] == ["inspect"]

            intervene = client.post(
                f"/api/projects/{project_id}/tasks/T-claim-fence/intervene",
                json={"action": "reset"},
            )
            recover = client.post(f"/api/projects/{project_id}/recover", json={})
            delete = client.delete(f"/api/projects/{project_id}")
            for response in (intervene, recover, delete):
                assert response.status_code == 409, response.json()
                assert response.json()["detail"]["code"] == "CLAIM_ACTIVE"
                assert response.json()["detail"]["task_ids"] == ["T-claim-fence"]
            assert second_worker.claim_task("T-claim-fence") is None
            assert projects.get_project(project_id) is not None
            assert projects.get_task("T-claim-fence").status == "running"
            assert projects.events_for_project(project_id) == events_before
        finally:
            release_execute.set()
        tick = tick_future.result(timeout=5)

    assert execute_calls == 1
    assert "task_done:T-claim-fence" in tick["events"]
    finalized = projects.get_task("T-claim-fence")
    assert finalized is not None
    assert finalized.status == "done"
    assert finalized.output == "one external result"
    with sqlite3.connect(str(tmp_path / "projects" / "projectos.db")) as conn:
        assert (
            conn.execute("SELECT 1 FROM task_claims WHERE task_id='T-claim-fence'").fetchone()
            is None
        )


def test_detach_preserves_group_room_history_and_project_but_clears_projection(
    tmp_path: Path,
) -> None:
    client, projects, groups, collaboration, threads, room_id, _app = _local_stack(tmp_path)
    collaboration.append_message(
        "thread-lifecycle",
        room_id=room_id,
        text="keep this history",
        participant_id="owner-local",
        display_name="local",
    )

    first = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={"name": "Unified project", "goal": "Ship it"},
    )
    retry = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={"name": "Ignored retry", "goal": "Must reuse"},
    )

    assert first.status_code == retry.status_code == 200
    project_id = first.json()["project"]["id"]
    assert retry.json()["project"]["id"] == project_id
    assert retry.json()["reused"] is True
    assert client.get(f"/api/teams/{room_id}").json()["join_policy"] == "apply_then_join"
    thread = threads.get("thread-lifecycle")
    assert thread["metadata"]["project_id"] == project_id
    assert thread["values"]["project_id"] == project_id
    assert (
        collaboration.room_for_session("thread-lifecycle")["metadata"]["project_id"] == project_id
    )
    reloaded_projects = ProjectStore(base_dir=tmp_path / "projects")
    reloaded_project = reloaded_projects.project_for_thread("thread-lifecycle")
    assert reloaded_project is not None
    assert reloaded_project.id == project_id

    detached = client.request(
        "DELETE",
        "/api/projects/from-group/thread-lifecycle",
        json={"expected_project_id": project_id},
    )

    assert detached.status_code == 200, detached.json()
    assert detached.json()["detached"] is True
    assert detached.json()["project_id"] == project_id
    assert detached.json()["project"]["execution_thread_id"] == ""
    assert projects.project_for_thread("thread-lifecycle") is None
    assert projects.get_project(project_id) is not None
    assert groups.state("thread-lifecycle").mode == "swarm"
    assert groups.state("thread-lifecycle").room_id == room_id
    thread = threads.get("thread-lifecycle")
    assert "project_id" not in thread["metadata"]
    assert "project_home" not in thread["metadata"]
    assert "project_id" not in thread["values"]
    room = collaboration.room_for_session("thread-lifecycle")
    assert room["metadata"].get("project_id") is None
    assert room["is_project_group"] is False
    assert collaboration.messages_for_session("thread-lifecycle")[0]["text"] == (
        "keep this history"
    )
    assert client.get(f"/api/teams/{room_id}").json()["join_policy"] == "direct_join"

    repeated = client.request(
        "DELETE",
        "/api/projects/from-group/thread-lifecycle",
        json={"expectedProjectId": project_id},
    )
    assert repeated.status_code == 200
    assert repeated.json()["detached"] is False
    assert repeated.json()["project"]["id"] == project_id
    assert projects.get_project(project_id) is not None
    reloaded_after_detach = ProjectStore(base_dir=tmp_path / "projects")
    assert reloaded_after_detach.project_for_thread("thread-lifecycle") is None
    assert reloaded_after_detach.get_project(project_id) is not None
    assert [event["kind"] for event in projects.events_for_project(project_id)].count(
        "project.detached_from_group"
    ) == 1


def test_detach_retry_after_process_crash_finishes_stale_projections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, collaboration, threads, _room_id, _app = _local_stack(tmp_path)
    attached = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={"name": "Crash-safe detach", "goal": "Retry external projections"},
    )
    assert attached.status_code == 200, attached.json()
    project_id = attached.json()["project"]["id"]

    class SimulatedProcessCrash(BaseException):
        pass

    real_thread_projection = threads.set_project_binding_metadata

    def crash_before_projection_clear(
        thread_id: str,
        projected_project_id: str | None,
        **kwargs,
    ):
        if projected_project_id is None:
            raise SimulatedProcessCrash("process died after durable unbind")
        return real_thread_projection(thread_id, projected_project_id, **kwargs)

    monkeypatch.setattr(
        threads,
        "set_project_binding_metadata",
        crash_before_projection_clear,
    )

    with pytest.raises(SimulatedProcessCrash, match="durable unbind"):
        client.request(
            "DELETE",
            "/api/projects/from-group/thread-lifecycle",
            json={"expected_project_id": project_id},
        )

    assert projects.project_for_thread("thread-lifecycle") is None
    assert projects.get_project(project_id) is not None
    assert threads.get("thread-lifecycle")["metadata"]["project_id"] == project_id
    assert (
        collaboration.room_for_session("thread-lifecycle")["metadata"]["project_id"] == project_id
    )

    retry_client, reloaded_projects, reloaded_collaboration, reloaded_threads = _reopen_stack(
        tmp_path
    )
    retried = retry_client.request(
        "DELETE",
        "/api/projects/from-group/thread-lifecycle",
        json={"expectedProjectId": project_id},
    )

    assert retried.status_code == 200, retried.json()
    assert retried.json()["detached"] is False
    assert reloaded_projects.get_project(project_id) is not None
    assert reloaded_projects.project_for_thread("thread-lifecycle") is None
    thread = reloaded_threads.get("thread-lifecycle")
    assert "project_id" not in thread["metadata"]
    assert "project_home" not in thread["metadata"]
    assert "project_id" not in thread["values"]
    assert (
        reloaded_collaboration.room_for_session("thread-lifecycle")["metadata"].get("project_id")
        is None
    )


def test_detach_compensation_projects_concurrent_authoritative_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, collaboration, threads, room_id, _app = _local_stack(tmp_path)
    attached = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={"name": "Old project", "goal": "Lose the detach compensation CAS"},
    )
    assert attached.status_code == 200, attached.json()
    old_project_id = attached.json()["project"]["id"]
    winner = Project(id="P-detach-winner", name="Detach winner", goal="Own every projection")
    real_room_projection = collaboration.set_room_project_metadata
    clear_ready = Barrier(2)
    release_clear = Event()
    clear_failed = False

    def fail_first_room_clear(
        session_id: str,
        project_id: str | None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        nonlocal clear_failed
        if project_id is None and not clear_failed:
            clear_failed = True
            clear_ready.wait(timeout=5)
            assert release_clear.wait(timeout=5)
            raise RuntimeError("injected detach projection failure")
        return real_room_projection(session_id, project_id, **kwargs)

    monkeypatch.setattr(collaboration, "set_room_project_metadata", fail_first_room_clear)

    with ThreadPoolExecutor(max_workers=1) as pool:
        response_future = pool.submit(
            client.request,
            "DELETE",
            "/api/projects/from-group/thread-lifecycle",
            json={"expected_project_id": old_project_id},
        )
        clear_ready.wait(timeout=5)
        try:
            projects.save_project(winner)
            projects.bind_thread("thread-lifecycle", winner.id)
            assert projects.project_for_thread("thread-lifecycle").id == winner.id
            assert "project_id" not in threads.get("thread-lifecycle")["metadata"]
            assert (
                collaboration.room_for_session("thread-lifecycle")["metadata"]["project_id"]
                == old_project_id
            )
        finally:
            release_clear.set()
        response = response_future.result(timeout=5)

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "PROJECT_DETACH_COMPENSATION_CONFLICT",
        "message": "a newer project binding won during detach compensation",
        "thread_id": "thread-lifecycle",
        "project_id": old_project_id,
        "winner_project_id": winner.id,
    }
    assert projects.get_project(old_project_id) is not None
    assert projects.project_for_thread("thread-lifecycle").id == winner.id
    thread = threads.get("thread-lifecycle")
    assert thread["metadata"]["project_id"] == winner.id
    assert thread["values"]["project_id"] == winner.id
    assert collaboration.room_for_session("thread-lifecycle")["metadata"]["project_id"] == (
        winner.id
    )
    assert client.get(f"/api/teams/{room_id}").json()["join_policy"] == "apply_then_join"


def test_detach_protects_active_project_until_force_is_explicit(tmp_path: Path) -> None:
    client, projects, _groups, _collaboration, _threads, _room_id, _app = _local_stack(tmp_path)
    attached = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={"name": "Active", "goal": "Still running"},
    ).json()
    project_id = attached["project"]["id"]
    project = projects.get_project(project_id)
    project.status = "running"
    project.started_at = "2026-08-22T00:00:00+00:00"
    projects.save_project(project)

    protected = client.request(
        "DELETE",
        "/api/projects/from-group/thread-lifecycle",
        json={"expected_project_id": project_id},
    )

    assert protected.status_code == 409
    assert protected.json()["detail"] == {
        "code": "PROJECT_ACTIVE",
        "message": "project is still active; complete it or explicitly detach with force=true",
        "project_id": project_id,
        "status": "running",
        "force_required": True,
    }
    assert projects.project_for_thread("thread-lifecycle").id == project_id

    forced = client.request(
        "DELETE",
        "/api/projects/from-group/thread-lifecycle",
        json={"expected_project_id": project_id, "force": True},
    )
    assert forced.status_code == 200
    assert forced.json()["detached"] is True
    assert projects.get_project(project_id).status == "running"


def test_promotion_projection_failure_preserves_plan_for_attach_only_recovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, projects, groups, collaboration, threads, room_id, _app = _local_stack(tmp_path)
    real_setter = collaboration.set_room_project_metadata
    failed = False

    def fail_first_attach(session_id, project_id, **kwargs):
        nonlocal failed
        if project_id and not failed:
            failed = True
            raise RuntimeError("injected project projection failure")
        return real_setter(session_id, project_id, **kwargs)

    monkeypatch.setattr(collaboration, "set_room_project_metadata", fail_first_attach)

    response = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={"name": "Must compensate", "goal": "No shell"},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "PROJECT_PROJECTION_RECOVERY_REQUIRED"
    project_id = detail["project_id"]
    assert projects.get_project(project_id) is not None
    assert projects.project_for_thread("thread-lifecycle").id == project_id
    assert any(
        event["kind"] == "project.group_projection_recovery_pending"
        for event in projects.events_for_project(project_id)
    )
    thread = threads.get("thread-lifecycle")
    assert thread["metadata"]["project_id"] == project_id
    assert thread["values"]["project_id"] == project_id
    room = collaboration.room_for_session("thread-lifecycle")
    assert room["metadata"].get("project_id") == project_id
    assert room["id"] == room_id
    assert groups.state("thread-lifecycle").room_id == room_id
    assert client.get(f"/api/teams/{room_id}").json()["join_policy"] == "apply_then_join"

    monkeypatch.setattr(collaboration, "set_room_project_metadata", real_setter)
    recovered = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={"name": "ignored", "goal": "repair", "run": False},
    )
    assert recovered.status_code == 200
    assert recovered.json()["project"]["id"] == project_id


def test_run_projection_failure_preserves_execution_and_attach_only_retry_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, collaboration, threads, room_id, _app = _local_stack(tmp_path)
    collaboration.upsert_project_task(
        session_id="thread-lifecycle",
        room_id=room_id,
        project_id="P-history",
        milestone_id="MS-history",
        task={
            "id": "history-task",
            "title": "Keep unrelated history",
            "metadata": {"source": "projectos"},
        },
    )
    collaboration.upsert_project_task(
        session_id="another-session",
        room_id="another-room",
        project_id="P-same-id",
        milestone_id="MS-other-session",
        task={
            "id": "other-session-task",
            "title": "Keep another session",
            "metadata": {"source": "projectos"},
        },
    )
    captured_project_ids: list[str] = []
    real_setter = collaboration.set_room_project_metadata
    project_projection_calls = 0

    def fail_attach_after_task_projection(session_id, project_id, **kwargs):
        nonlocal project_projection_calls
        if project_id:
            project_projection_calls += 1
        if project_id and project_projection_calls == 2:
            captured_project_ids.append(str(project_id))
            raise RuntimeError("injected late projection failure")
        return real_setter(session_id, project_id, **kwargs)

    monkeypatch.setattr(
        collaboration,
        "set_room_project_metadata",
        fail_attach_after_task_projection,
    )

    response = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={
            "name": "Must purge projection",
            "goal": "No dangling task references",
            "run": True,
            "max_ticks": 12,
        },
    )

    assert response.status_code == 409
    project_id = captured_project_ids[0]
    assert response.json()["detail"] == {
        "code": "PROJECT_PROJECTION_RECOVERY_REQUIRED",
        "message": "project execution was preserved but group projections need recovery",
        "project_id": project_id,
        "thread_id": "thread-lifecycle",
        "run_requested": True,
        "execution_started": True,
        "recovery_recorded": True,
        "recovery": {
            "method": "POST",
            "path": "/api/projects/from-group/thread-lifecycle",
            "run": False,
        },
    }
    assert projects.get_project(project_id) is not None
    assert projects.project_for_thread("thread-lifecycle").id == project_id
    projected_task_ids = {
        task["id"] for task in collaboration.project_tasks_for_project(project_id)
    }
    assert projected_task_ids
    assert [task["id"] for task in collaboration.project_tasks_for_project("P-history")] == [
        "history-task"
    ]
    assert [task["id"] for task in collaboration.project_tasks_for_project("P-same-id")] == [
        "other-session-task"
    ]
    event_kinds = [event["kind"] for event in projects.events_for_project(project_id)]
    assert event_kinds.count("project.run_from_group") == 1
    assert event_kinds.count("project.group_projection_recovery_pending") == 1

    monkeypatch.setattr(collaboration, "set_room_project_metadata", real_setter)
    recovered = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={
            "name": "Ignored recovery name",
            "goal": "Recover projections without running again",
            "run": False,
        },
    )

    assert recovered.status_code == 200, recovered.json()
    assert recovered.json()["project"]["id"] == project_id
    assert recovered.json()["reused"] is True
    assert recovered.json()["run_requested"] is False
    assert recovered.json()["execution_started"] is False
    assert {
        task["id"] for task in collaboration.project_tasks_for_project(project_id)
    } == projected_task_ids
    assert [event["kind"] for event in projects.events_for_project(project_id)].count(
        "project.run_from_group"
    ) == 1
    thread = threads.get("thread-lifecycle")
    assert thread["metadata"]["project_id"] == project_id
    assert collaboration.room_for_session("thread-lifecycle")["metadata"]["project_id"] == (
        project_id
    )


def test_promotion_keeps_project_when_external_task_cleanup_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, projects, _groups, collaboration, _threads, room_id, _app = _local_stack(tmp_path)
    captured_project_ids: list[str] = []
    real_setter = collaboration.set_room_project_metadata
    attach_failed = False

    def fail_attach_after_task_projection(session_id, project_id, **kwargs):
        nonlocal attach_failed
        if project_id and not attach_failed:
            attach_failed = True
            captured_project_ids.append(str(project_id))
            collaboration.upsert_project_task(
                session_id="thread-lifecycle",
                room_id=room_id,
                project_id=str(project_id),
                milestone_id="MS-cleanup-failure",
                task={"id": "keep-on-cleanup-failure", "title": "Keep source anchor"},
            )
            raise RuntimeError("injected late projection failure")
        return real_setter(session_id, project_id, **kwargs)

    def fail_project_task_cleanup(*args, **kwargs):
        raise RuntimeError("injected collaboration cleanup failure")

    monkeypatch.setattr(
        collaboration,
        "set_room_project_metadata",
        fail_attach_after_task_projection,
    )
    monkeypatch.setattr(
        collaboration,
        "delete_project_tasks",
        fail_project_task_cleanup,
        raising=False,
    )

    response = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={
            "name": "Retain source of truth",
            "goal": "Never leave dangling task references",
            "run": False,
        },
    )

    assert response.status_code == 409
    project_id = captured_project_ids[0]
    assert projects.get_project(project_id) is not None
    assert projects.project_for_thread("thread-lifecycle").id == project_id
    assert collaboration.project_tasks_for_project(project_id)
    assert (
        collaboration.room_for_session("thread-lifecycle")["metadata"]["project_id"] == project_id
    )


def test_promotion_failure_never_runs_destructive_compensation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, projects, _groups, collaboration, _threads, _room_id, _app = _local_stack(tmp_path)
    captured_project_ids: list[str] = []
    real_setter = collaboration.set_room_project_metadata
    attach_failed = False

    def fail_first_attach(session_id, project_id, **kwargs):
        nonlocal attach_failed
        if project_id and not attach_failed:
            attach_failed = True
            captured_project_ids.append(str(project_id))
            raise RuntimeError("injected late projection failure")
        return real_setter(session_id, project_id, **kwargs)

    def forbidden_cleanup(*_args, **_kwargs):
        raise AssertionError("committed promotion plans must not be auto-deleted")

    monkeypatch.setattr(collaboration, "set_room_project_metadata", fail_first_attach)
    monkeypatch.setattr(
        collaboration,
        "delete_project_tasks",
        forbidden_cleanup,
    )
    monkeypatch.setattr(projects, "delete_project", forbidden_cleanup)
    monkeypatch.setattr(projects, "delete_project_if_unbound", forbidden_cleanup)

    response = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={
            "name": "Preserved promotion",
            "goal": "Never run destructive compensation",
            "run": False,
        },
    )

    assert response.status_code == 409
    project_id = captured_project_ids[0]
    assert projects.project_for_thread("thread-lifecycle").id == project_id
    assert projects.get_project(project_id) is not None
    assert response.json()["detail"]["recovery_recorded"] is True


def test_promotion_failure_preserves_concurrent_external_source_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, collaboration, _threads, _room_id, _app = _local_stack(tmp_path)
    captured_project_ids: list[str] = []
    real_setter = collaboration.set_room_project_metadata
    external = ProjectStore(base_dir=tmp_path / "projects")
    attach_failed = False

    def fail_first_attach(session_id: str, project_id: str | None, **kwargs: Any):
        nonlocal attach_failed
        if project_id and not attach_failed:
            attach_failed = True
            captured_project_ids.append(project_id)
            project = external.get_project(project_id)
            assert project is not None
            project.name = "External update survives"
            external.save_project(project)
            milestone = external.milestones_for(project_id)[0]
            external.add_task_to_milestone(
                project_id,
                Task(
                    id="T-external-promotion",
                    milestone_id=milestone.id,
                    type="code",
                    goal="Preserve concurrent task",
                ),
            )
            external.append_event(
                project_id,
                kind="external.promotion_update",
                payload={"preserve": True},
            )
            raise RuntimeError("injected project projection failure")
        return real_setter(session_id, project_id, **kwargs)

    def forbidden_cleanup(*_args, **_kwargs):
        raise AssertionError("committed promotion plans must not be auto-deleted")

    monkeypatch.setattr(collaboration, "set_room_project_metadata", fail_first_attach)
    monkeypatch.setattr(projects, "delete_project", forbidden_cleanup)
    monkeypatch.setattr(projects, "delete_project_if_unbound", forbidden_cleanup)

    response = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={"name": "Preserve source", "goal": "Keep concurrent writes", "run": False},
    )

    assert response.status_code == 409
    project_id = captured_project_ids[0]
    assert projects.get_project(project_id).name == "External update survives"
    milestone = projects.milestones_for(project_id)[0]
    assert [task.id for task in projects.tasks_for_milestone(milestone.id)] == [
        "T-external-promotion"
    ]
    assert {event["kind"] for event in projects.events_for_project(project_id)} >= {
        "external.promotion_update",
        "project.group_projection_recovery_pending",
    }


def test_project_task_cleanup_is_exact_to_session_project_and_source(tmp_path: Path) -> None:
    collaboration = CollaborationStore(base_dir=tmp_path)
    collaboration.upsert_project_task(
        session_id="target-session",
        room_id="target-room",
        project_id="P-target",
        milestone_id="MS-target",
        task={"id": "delete-me", "title": "Projected task"},
    )
    collaboration.upsert_project_task(
        session_id="target-session",
        room_id="target-room",
        project_id="P-other",
        milestone_id="MS-other",
        task={"id": "keep-other-project", "title": "Other project"},
    )
    collaboration.upsert_project_task(
        session_id="other-session",
        room_id="other-room",
        project_id="P-target",
        milestone_id="MS-other-session",
        task={"id": "keep-other-session", "title": "Other session"},
    )
    collaboration.upsert_task(
        "target-session",
        {
            "id": "keep-other-source",
            "room_id": "target-room",
            "kind": "project",
            "project_id": "P-target",
            "title": "External projection",
            "metadata": {"source": "external"},
        },
    )

    deleted = collaboration.delete_project_tasks(
        session_id="target-session",
        project_id="P-target",
        source="projectos",
    )

    assert deleted == 1
    assert {task["id"] for task in collaboration.tasks_for_session("target-session")} == {
        "keep-other-project",
        "keep-other-source",
    }
    assert {task["id"] for task in collaboration.tasks_for_session("other-session")} == {
        "keep-other-session"
    }


def test_room_project_metadata_stale_clear_cannot_erase_cross_instance_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = CollaborationStore(base_dir=tmp_path)
    stale.upsert_room("thread-room-cas", {"id": "room-cas", "name": "CAS room"})
    stale.set_room_project_metadata("thread-room-cas", "P-old", generation=1)
    winner = CollaborationStore(base_dir=tmp_path)
    stale_begin = Event()
    release_stale = Event()
    real_connect = stale._connect
    paused = False

    class PausedConnection:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def __enter__(self) -> PausedConnection:
            self._conn.__enter__()
            return self

        def __exit__(self, *args: Any) -> Any:
            return self._conn.__exit__(*args)

        def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> Any:
            nonlocal paused
            if sql == "BEGIN IMMEDIATE" and not paused:
                paused = True
                stale_begin.set()
                assert release_stale.wait(timeout=5)
            return self._conn.execute(sql, parameters)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._conn, name)

    monkeypatch.setattr(stale, "_connect", lambda: PausedConnection(real_connect()))

    with ThreadPoolExecutor(max_workers=1) as pool:
        stale_clear = pool.submit(
            stale.set_room_project_metadata,
            "thread-room-cas",
            None,
            expected_project_id="P-old",
            generation=2,
        )
        assert stale_begin.wait(timeout=5)
        try:
            winner.set_room_project_metadata(
                "thread-room-cas",
                None,
                expected_project_id="P-old",
                generation=2,
            )
            winner.set_room_project_metadata("thread-room-cas", "P-new", generation=3)
        finally:
            release_stale.set()

        with pytest.raises(RuntimeError, match="stale room project binding generation"):
            stale_clear.result(timeout=5)

    room = winner.room_for_session("thread-room-cas")
    assert room is not None
    assert room["metadata"]["project_id"] == "P-new"
    assert room["project_id"] == "P-new"


def test_explicit_project_delete_clears_bound_projections_and_all_task_sessions(
    tmp_path: Path,
) -> None:
    client, projects, _groups, collaboration, threads, room_id, _app = _local_stack(tmp_path)
    attached = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={
            "name": "Delete cleanly",
            "goal": "No dangling read models",
            "run": True,
            "max_ticks": 12,
        },
    )
    assert attached.status_code == 200, attached.json()
    project_id = attached.json()["project"]["id"]
    collaboration.upsert_project_task(
        session_id="historical-session",
        room_id="historical-room",
        project_id=project_id,
        milestone_id="MS-history",
        task={"id": "historical-projection", "title": "Historical projection"},
    )
    assert collaboration.project_tasks_for_project(project_id)

    deleted = client.delete(f"/api/projects/{project_id}")

    assert deleted.status_code == 200, deleted.json()
    assert projects.get_project(project_id) is None
    assert collaboration.project_tasks_for_project(project_id) == []
    thread = threads.get("thread-lifecycle")
    assert "project_id" not in thread["metadata"]
    assert "project_id" not in thread["values"]
    assert collaboration.room_for_session("thread-lifecycle")["metadata"].get("project_id") is None
    assert client.get(f"/api/teams/{room_id}").json()["join_policy"] == "direct_join"


def test_explicit_project_delete_cleans_tasks_without_a_thread_binding(tmp_path: Path) -> None:
    client, projects, _groups, collaboration, _threads, _room_id, _app = _local_stack(tmp_path)
    project = Project(id="P-unbound-delete", name="Unbound", goal="Clean history")
    projects.save_project(project)
    for session_id in ("history-one", "history-two"):
        collaboration.upsert_project_task(
            session_id=session_id,
            room_id=f"room-{session_id}",
            project_id=project.id,
            milestone_id="MS-history",
            task={
                "id": f"task-{session_id}",
                "title": f"Projection in {session_id}",
            },
        )

    deleted = client.delete(f"/api/projects/{project.id}")

    assert deleted.status_code == 200, deleted.json()
    assert projects.get_project(project.id) is None
    assert collaboration.project_tasks_for_project(project.id) == []


def test_delete_lease_blocks_concurrent_source_writes_until_final_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, collaboration, _threads, _room_id, _app = _local_stack(tmp_path)
    attached = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={"name": "Deletion fence", "goal": "Reject late source writes"},
    )
    assert attached.status_code == 200, attached.json()
    project_id = attached.json()["project"]["id"]
    milestone = projects.milestones_for(project_id)[0]
    cleanup_entered = Event()
    release_cleanup = Event()
    real_cleanup = collaboration.tombstone_project_projection

    def paused_cleanup(project_id: str, token: str) -> None:
        cleanup_entered.set()
        assert release_cleanup.wait(timeout=5)
        real_cleanup(project_id, token)

    monkeypatch.setattr(collaboration, "tombstone_project_projection", paused_cleanup)
    concurrent = ProjectStore(base_dir=tmp_path / "projects")
    with ThreadPoolExecutor(max_workers=1) as pool:
        delete_future = pool.submit(client.delete, f"/api/projects/{project_id}")
        assert cleanup_entered.wait(timeout=5)
        try:
            with pytest.raises(ProjectDeleteInProgressError):
                concurrent.append_event(project_id, kind="late.write", payload={})
            stale = concurrent.get_project(project_id)
            assert stale is not None
            stale.name = "late overwrite"
            with pytest.raises(ProjectDeleteInProgressError):
                concurrent.save_project(stale)
            with pytest.raises(ProjectDeleteInProgressError):
                concurrent.add_task_to_milestone(
                    project_id,
                    Task(
                        id="T-late-delete",
                        milestone_id=milestone.id,
                        type="code",
                        goal="must not survive",
                    ),
                )
            with pytest.raises(ProjectDeleteInProgressError):
                concurrent.bind_thread("thread-late-delete", project_id)
            with (
                sqlite3.connect(str(tmp_path / "projects" / "projectos.db")) as conn,
                pytest.raises(sqlite3.IntegrityError, match="delete in progress"),
            ):
                conn.execute(
                    "INSERT INTO project_events(id, project_id, kind, payload, created_at) "
                    "VALUES ('EV-raw-late', ?, 'late.raw', '{}', 0)",
                    (project_id,),
                )
        finally:
            release_cleanup.set()
        deleted = delete_future.result(timeout=5)

    assert deleted.status_code == 200, deleted.json()
    assert projects.get_project(project_id) is None
    assert projects.get_task("T-late-delete") is None


def test_project_delete_retry_after_process_crash_recovers_thread_and_preserves_new_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, collaboration, threads, room_id, _app = _local_stack(tmp_path)
    attached = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={
            "name": "Crash-safe delete",
            "goal": "Recover the projection outbox",
            "run": True,
            "max_ticks": 12,
        },
    )
    assert attached.status_code == 200, attached.json()
    deleting_project_id = attached.json()["project"]["id"]
    assert collaboration.project_tasks_for_project(deleting_project_id)

    class SimulatedProcessCrash(BaseException):
        pass

    real_thread_projection = threads.set_project_binding_metadata

    def crash_before_projection_clear(
        thread_id: str,
        projected_project_id: str | None,
        **kwargs,
    ):
        if projected_project_id is None:
            raise SimulatedProcessCrash("process died after delete unbind")
        return real_thread_projection(thread_id, projected_project_id, **kwargs)

    monkeypatch.setattr(
        threads,
        "set_project_binding_metadata",
        crash_before_projection_clear,
    )

    with pytest.raises(SimulatedProcessCrash, match="delete unbind"):
        client.delete(f"/api/projects/{deleting_project_id}")

    assert projects.project_for_thread("thread-lifecycle") is None
    assert projects.get_project(deleting_project_id) is not None
    assert threads.get("thread-lifecycle")["metadata"]["project_id"] == deleting_project_id

    newer = Project(id="P-crash-retry-winner", name="New winner", goal="wins durable CAS")
    projects.save_project(newer)
    projects.bind_thread("thread-lifecycle", newer.id)

    retry_client, reloaded_projects, reloaded_collaboration, reloaded_threads = _reopen_stack(
        tmp_path
    )
    retried = retry_client.delete(f"/api/projects/{deleting_project_id}")

    assert retried.status_code == 200, retried.json()
    assert reloaded_projects.get_project(deleting_project_id) is None
    assert reloaded_projects.project_for_thread("thread-lifecycle").id == newer.id
    assert reloaded_projects.get_project(newer.id) is not None
    assert reloaded_collaboration.project_tasks_for_project(deleting_project_id) == []
    thread = reloaded_threads.get("thread-lifecycle")
    assert thread["metadata"]["project_id"] == newer.id
    assert thread["values"]["project_id"] == newer.id
    assert (
        reloaded_collaboration.room_for_session("thread-lifecycle")["metadata"]["project_id"]
        == newer.id
    )
    assert retry_client.get(f"/api/teams/{room_id}").json()["join_policy"] == "apply_then_join"


def test_explicit_project_delete_failure_keeps_roll_forward_claim(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, projects, _groups, collaboration, threads, _room_id, _app = _local_stack(tmp_path)
    attached = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={
            "name": "Recover deletion",
            "goal": "Keep canonical state",
            "run": True,
            "max_ticks": 12,
        },
    )
    assert attached.status_code == 200, attached.json()
    project_id = attached.json()["project"]["id"]

    real_tombstone = collaboration.tombstone_project_projection

    def fail_all_session_cleanup(*args, **kwargs):
        raise RuntimeError("injected all-session cleanup failure")

    monkeypatch.setattr(
        collaboration,
        "tombstone_project_projection",
        fail_all_session_cleanup,
        raising=False,
    )

    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PROJECT_DELETE_RECOVERY_PENDING"
    assert projects.get_project(project_id) is not None
    assert projects.project_for_thread("thread-lifecycle") is None
    thread = threads.get("thread-lifecycle")
    assert thread["metadata"].get("project_id") is None
    assert collaboration.project_tasks_for_project(project_id)
    survivor = projects.get_project(project_id)
    assert survivor is not None
    with pytest.raises(ProjectDeleteInProgressError):
        projects.save_project(survivor)

    monkeypatch.setattr(collaboration, "tombstone_project_projection", real_tombstone)
    retried = client.delete(f"/api/projects/{project_id}")

    assert retried.status_code == 200, retried.json()
    assert projects.get_project(project_id) is None
    assert collaboration.project_tasks_for_project(project_id) == []


def test_source_finalize_failure_rolls_forward_with_one_durable_delete_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, collaboration, _threads, _room_id, _app = _local_stack(tmp_path)
    planned = client.post(
        "/api/projects",
        json={"name": "Rollback standalone", "goal": "Keep the projected source usable"},
    )
    assert planned.status_code == 200, planned.json()
    project_id = planned.json()["project"]["id"]
    milestone_id = planned.json()["milestones"][0]["id"]
    session_id = f"project:{project_id}"
    room_id = session_id
    if collaboration.room_for_session(session_id) is None:
        collaboration.upsert_project_room(
            session_id=session_id,
            room={"id": room_id, "name": "Standalone"},
            project_id=project_id,
            generation=0,
        )
    collaboration.upsert_project_task(
        session_id=session_id,
        room_id=room_id,
        project_id=project_id,
        milestone_id=milestone_id,
        task={"id": "T-standalone-rollback", "title": "Must survive compensation"},
        binding_generation=0,
    )

    real_finalize = projects.finalize_project_delete
    monkeypatch.setattr(
        projects,
        "finalize_project_delete",
        lambda _project_id, _token: False,
    )

    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 409, response.json()
    assert response.json()["detail"]["code"] == "PROJECT_DELETE_RECOVERY_PENDING"
    assert projects.get_project(project_id) is not None
    fenced_room = collaboration.room_for_session(session_id)
    assert fenced_room is not None
    assert fenced_room["project_id"] is None
    assert fenced_room["metadata"]["project_binding_generation"] == 1
    assert collaboration.tasks_for_session(session_id) == []
    survivor = projects.get_project(project_id)
    assert survivor is not None
    with pytest.raises(ProjectDeleteInProgressError):
        projects.save_project(survivor)

    monkeypatch.setattr(projects, "finalize_project_delete", real_finalize)
    retried = client.delete(f"/api/projects/{project_id}")

    assert retried.status_code == 200, retried.json()
    assert projects.get_project(project_id) is None


def test_delete_tombstone_consumes_cleanup_window_writes_and_fences_late_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, collaboration, _threads, _room_id, _app = _local_stack(tmp_path)
    planned = client.post(
        "/api/projects",
        json={"name": "Cleanup window", "goal": "Fence every collaboration writer"},
    )
    assert planned.status_code == 200, planned.json()
    project_id = planned.json()["project"]["id"]
    milestone_id = planned.json()["milestones"][0]["id"]
    session_id = f"project:{project_id}"
    if collaboration.room_for_session(session_id) is None:
        collaboration.upsert_project_room(
            session_id=session_id,
            room={"id": session_id},
            project_id=project_id,
            generation=0,
        )
    entered_cleanup = Event()
    release_cleanup = Event()
    real_tombstone = collaboration.tombstone_project_projection

    def paused_tombstone(target_project_id: str, token: str) -> None:
        entered_cleanup.set()
        assert release_cleanup.wait(timeout=5)
        real_tombstone(target_project_id, token)

    monkeypatch.setattr(collaboration, "tombstone_project_projection", paused_tombstone)
    late_writer = CollaborationStore(base_dir=tmp_path / "collaboration")
    with ThreadPoolExecutor(max_workers=1) as pool:
        deleting = pool.submit(client.delete, f"/api/projects/{project_id}")
        assert entered_cleanup.wait(timeout=5)
        late_writer.upsert_project_task(
            session_id=session_id,
            room_id=session_id,
            project_id=project_id,
            milestone_id=milestone_id,
            task={"id": "T-cleanup-window", "title": "Visible before the tombstone"},
            binding_generation=0,
        )
        release_cleanup.set()
        response = deleting.result(timeout=5)

    assert response.status_code == 200, response.json()
    assert projects.get_project(project_id) is None
    room = collaboration.room_for_session(session_id)
    assert room is not None
    assert room["project_id"] is None
    assert room["metadata"]["project_binding_generation"] == 1
    assert collaboration.tasks_for_session(session_id) == []
    with pytest.raises(RuntimeError, match="projection was deleted"):
        late_writer.upsert_project_task(
            session_id=session_id,
            room_id=session_id,
            project_id=project_id,
            milestone_id=milestone_id,
            task={"id": "T-after-delete", "title": "Must be rejected"},
            binding_generation=0,
        )


def test_delete_retry_converges_after_source_commit_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, collaboration, _threads, _room_id, _app = _local_stack(tmp_path)
    planned = client.post(
        "/api/projects",
        json={"name": "Commit crash", "goal": "Converge permanent tombstones on retry"},
    )
    project_id = planned.json()["project"]["id"]
    milestone_id = planned.json()["milestones"][0]["id"]
    session_id = f"project:{project_id}"
    if collaboration.room_for_session(session_id) is None:
        collaboration.upsert_project_room(
            session_id=session_id,
            room={"id": session_id},
            project_id=project_id,
            generation=0,
        )
    collaboration.upsert_project_task(
        session_id=session_id,
        room_id=session_id,
        project_id=project_id,
        milestone_id=milestone_id,
        task={"id": "T-commit-crash", "title": "Deleted before the process crash"},
        binding_generation=0,
    )

    class SimulatedProcessCrash(BaseException):
        pass

    def crash_after_source_commit(*_args: Any) -> None:
        raise SimulatedProcessCrash

    monkeypatch.setattr(
        collaboration,
        "finalize_project_projection_tombstone",
        crash_after_source_commit,
    )
    with pytest.raises(SimulatedProcessCrash):
        client.delete(f"/api/projects/{project_id}")

    assert projects.get_project(project_id) is None
    retry_client, _reloaded_projects, reloaded_collaboration, _threads = _reopen_stack(tmp_path)
    retried = retry_client.delete(f"/api/projects/{project_id}")

    assert retried.status_code == 200, retried.json()
    assert retried.json()["recovered"] is True
    assert reloaded_collaboration.tasks_for_session(session_id) == []


def test_collaboration_tombstone_commit_then_raise_rolls_forward_on_same_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, collaboration, _threads, _room_id, _app = _local_stack(tmp_path)
    planned = client.post(
        "/api/projects",
        json={"name": "Tombstone commit", "goal": "Probe durable state after an exception"},
    )
    project_id = planned.json()["project"]["id"]
    real_tombstone = collaboration.tombstone_project_projection

    def commit_then_raise(target_project_id: str, token: str) -> None:
        real_tombstone(target_project_id, token)
        raise RuntimeError("response was lost after collaboration commit")

    monkeypatch.setattr(collaboration, "tombstone_project_projection", commit_then_raise)
    failed = client.delete(f"/api/projects/{project_id}")

    assert failed.status_code == 409, failed.json()
    detail = failed.json()["detail"]
    assert detail["code"] == "PROJECT_DELETE_RECOVERY_PENDING"
    assert detail["projection_phase"] == "tombstoned"
    assert collaboration.project_projection_tombstone_token(project_id) == detail["delete_token"]
    with sqlite3.connect(str(tmp_path / "projects" / "projectos.db")) as conn:
        assert conn.execute(
            "SELECT token FROM project_delete_claims WHERE project_id=?",
            (project_id,),
        ).fetchone() == (detail["delete_token"],)

    monkeypatch.setattr(collaboration, "tombstone_project_projection", real_tombstone)
    retried = client.delete(f"/api/projects/{project_id}")
    assert retried.status_code == 200, retried.json()


def test_concurrent_same_token_deletes_both_converge_after_one_source_finalize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, _collaboration, _threads, _room_id, _app = _local_stack(tmp_path)
    planned = client.post(
        "/api/projects",
        json={"name": "Concurrent delete", "goal": "One token and one source finalize"},
    )
    project_id = planned.json()["project"]["id"]
    finalize_barrier = Barrier(2)
    real_finalize = projects.finalize_project_delete

    def synchronized_finalize(target_project_id: str, token: str) -> bool:
        finalize_barrier.wait(timeout=5)
        return real_finalize(target_project_id, token)

    monkeypatch.setattr(projects, "finalize_project_delete", synchronized_finalize)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _index: client.delete(f"/api/projects/{project_id}"),
                range(2),
            )
        )

    assert [response.status_code for response in responses] == [200, 200]
    assert projects.get_project(project_id) is None


def test_delete_failure_preserves_claim_instead_of_reopening_execution_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, projects, _groups, collaboration, _threads, _room_id, _app = _local_stack(tmp_path)
    attached = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={"name": "Bound rollback", "goal": "Restore the exact execution pointer"},
    )
    assert attached.status_code == 200, attached.json()
    project_id = attached.json()["project"]["id"]
    prepared = projects.restore_thread_bindings(
        project_id,
        ["thread-lifecycle"],
        original_execution_thread_id="thread-lifecycle",
    )
    assert prepared.execution_restored is True
    assert prepared.project.execution_thread_id == "thread-lifecycle"

    real_tombstone = collaboration.tombstone_project_projection

    def fail_final_projection_cleanup(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("injected final projection cleanup failure")

    monkeypatch.setattr(
        collaboration,
        "tombstone_project_projection",
        fail_final_projection_cleanup,
    )

    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PROJECT_DELETE_RECOVERY_PENDING"
    assert projects.thread_project_map() == {}
    fenced = projects.get_project(project_id)
    assert fenced is not None
    assert fenced.execution_thread_id == "thread-lifecycle"
    with pytest.raises(ProjectDeleteInProgressError):
        projects.save_project(fenced)

    monkeypatch.setattr(collaboration, "tombstone_project_projection", real_tombstone)
    retried = client.delete(f"/api/projects/{project_id}")
    assert retried.status_code == 200, retried.json()


def test_explicit_delete_retry_converges_onto_a_new_thread_winner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, projects, _groups, collaboration, _threads, _room_id, _app = _local_stack(tmp_path)
    attached = client.post(
        "/api/projects/from-group/thread-lifecycle",
        json={
            "name": "Delete loser",
            "goal": "Respect concurrent winner",
            "run": True,
            "max_ticks": 12,
        },
    )
    assert attached.status_code == 200, attached.json()
    deleting_project_id = attached.json()["project"]["id"]
    newer = Project(id="P-delete-newer", name="New winner", goal="wins CAS")

    real_tombstone = collaboration.tombstone_project_projection

    def install_new_winner_then_fail(*args, **kwargs):
        projects.save_project(newer)
        projects.bind_thread("thread-lifecycle", newer.id)
        raise RuntimeError("injected delete cleanup race")

    monkeypatch.setattr(
        collaboration,
        "tombstone_project_projection",
        install_new_winner_then_fail,
        raising=False,
    )

    response = client.delete(f"/api/projects/{deleting_project_id}")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PROJECT_DELETE_RECOVERY_PENDING"
    assert projects.project_for_thread("thread-lifecycle").id == newer.id
    assert projects.get_project(deleting_project_id) is not None

    monkeypatch.setattr(collaboration, "tombstone_project_projection", real_tombstone)
    retried = client.delete(f"/api/projects/{deleting_project_id}")

    assert retried.status_code == 200, retried.json()
    assert projects.get_project(deleting_project_id) is None
    assert projects.project_for_thread("thread-lifecycle").id == newer.id
    assert collaboration.room_for_session("thread-lifecycle")["metadata"]["project_id"] == newer.id


def test_detach_is_owner_only_in_authenticated_groups(tmp_path: Path) -> None:
    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-alice",
    )
    identities.add(
        Identity(actor_id="bob", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-bob",
    )
    projects = ProjectStore(base_dir=tmp_path / "projects")
    groups = GroupStore(base_dir=tmp_path / "groups")
    threads = ThreadStateStore(index_enabled=False, search_enabled=False, feedback_enabled=False)
    threads.ensure_thread(
        "thread-owned",
        metadata={"owner_actor_id": "alice", "tenant_id": "tenant-a"},
    )
    service.invite_member(
        groups,
        "thread-owned",
        actor="alice",
        target_id="general",
        kind="agent",
    )
    app = FastAPI()
    app.include_router(
        create_projects_router(
            store=projects,
            group_store=groups,
            thread_store=threads,
            identity_store=identities,
            require_auth=True,
        )
    )
    client = TestClient(app)
    alice = {"Authorization": "Bearer sk-alice"}
    bob = {"Authorization": "Bearer sk-bob"}
    attached = client.post(
        "/api/projects/from-group/thread-owned",
        headers=alice,
        json={"name": "Owned", "goal": "Owner controls lifecycle"},
    )
    assert attached.status_code == 200, attached.json()
    project_id = attached.json()["project"]["id"]

    denied = client.request(
        "DELETE",
        "/api/projects/from-group/thread-owned",
        headers=bob,
        json={"expected_project_id": project_id},
    )
    assert denied.status_code == 404
    bound_project = projects.project_for_thread("thread-owned")
    assert bound_project is not None
    assert bound_project.id == project_id

    allowed = client.request(
        "DELETE",
        "/api/projects/from-group/thread-owned",
        headers=alice,
        json={"expected_project_id": project_id},
    )
    assert allowed.status_code == 200
    assert allowed.json()["detached"] is True


def test_store_unbind_keeps_project_and_is_compare_guarded(tmp_path: Path) -> None:
    store = ProjectStore(base_dir=tmp_path)
    store.save_project(Project(id="P-one", name="One", goal="g"))
    store.bind_thread("thread-one", "P-one")

    detached = store.unbind_thread(
        "thread-one",
        expected_project_id="P-one",
        event_kind="project.detached_from_group",
        event_payload={"thread_id": "thread-one"},
    )

    assert detached is not None
    assert detached.id == "P-one"
    assert detached.execution_thread_id == ""
    assert store.get_project("P-one") is not None
    assert store.project_for_thread("thread-one") is None
    assert [event["kind"] for event in store.events_for_project("P-one")] == [
        "project.detached_from_group"
    ]


def test_store_unbind_and_audit_event_roll_back_together(tmp_path: Path) -> None:
    store = ProjectStore(base_dir=tmp_path)
    store.save_project(Project(id="P-atomic", name="Atomic", goal="g"))
    store.bind_thread("thread-atomic", "P-atomic")
    with sqlite3.connect(str(store._db)) as conn:
        conn.execute(
            "CREATE TRIGGER reject_detach_event BEFORE INSERT ON project_events "
            "WHEN NEW.kind = 'project.detached_from_group' "
            "BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END"
        )

    try:
        store.unbind_thread(
            "thread-atomic",
            expected_project_id="P-atomic",
            event_kind="project.detached_from_group",
            event_payload={"thread_id": "thread-atomic"},
        )
    except sqlite3.IntegrityError as exc:
        assert "injected audit failure" in str(exc)
    else:  # pragma: no cover - trigger is the assertion mechanism
        raise AssertionError("audit failure should abort detach")

    bound_project = store.project_for_thread("thread-atomic")
    persisted_project = store.get_project("P-atomic")
    assert bound_project is not None
    assert persisted_project is not None
    assert bound_project.id == "P-atomic"
    assert persisted_project.execution_thread_id == "thread-atomic"
    assert store.events_for_project("P-atomic") == []


def test_thread_project_projection_merges_latest_cross_instance_state(tmp_path: Path) -> None:
    path = tmp_path / "threads.jsonl"
    first = ThreadStateStore(
        path=path,
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )
    first.ensure_thread("thread-shared", values={"title": "Old", "messages": []})
    stale = ThreadStateStore(
        path=path,
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )

    first.update_state(
        "thread-shared",
        values={"title": "Keep", "messages": [{"type": "human", "content": "keep me"}]},
    )
    stale.set_project_binding_metadata("thread-shared", "P-shared")

    reloaded = ThreadStateStore(
        path=path,
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    ).get("thread-shared")
    assert reloaded is not None
    assert reloaded["values"]["title"] == "Keep"
    assert reloaded["values"]["messages"] == [{"type": "human", "content": "keep me"}]
    assert reloaded["metadata"]["project_id"] == "P-shared"
    assert reloaded["values"]["project_id"] == "P-shared"


def test_thread_update_and_project_projection_serialize_cross_instance(tmp_path: Path) -> None:
    path = tmp_path / "threads.jsonl"
    first = ThreadStateStore(
        path=path,
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )
    first.ensure_thread("thread-race", values={"title": "Old", "messages": []})
    second = ThreadStateStore(
        path=path,
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )
    barrier = Barrier(2)

    def update_conversation() -> None:
        barrier.wait()
        first.update_state(
            "thread-race",
            values={"title": "Keep", "messages": [{"type": "human", "content": "kept"}]},
        )

    def attach_project() -> None:
        barrier.wait()
        second.set_project_binding_metadata("thread-race", "P-race")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(update_conversation), pool.submit(attach_project)]
        for future in futures:
            future.result(timeout=5)

    reloaded = ThreadStateStore(
        path=path,
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    ).get("thread-race")
    assert reloaded is not None
    assert reloaded["values"]["title"] == "Keep"
    assert reloaded["values"]["messages"] == [{"type": "human", "content": "kept"}]
    assert reloaded["metadata"]["project_id"] == "P-race"
    assert reloaded["values"]["project_id"] == "P-race"


def test_stale_cross_instance_delete_and_ensure_fold_latest_state(tmp_path: Path) -> None:
    path = tmp_path / "threads.jsonl"
    first = ThreadStateStore(
        path=path,
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )
    old = first.ensure_thread("thread-cas", values={"title": "Old", "messages": []})
    stale = ThreadStateStore(
        path=path,
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )
    first.update_state(
        "thread-cas",
        values={"title": "Keep", "messages": [{"type": "human", "content": "keep"}]},
    )

    assert stale.delete_if_unchanged("thread-cas", old) is False
    canonical = stale.ensure_thread("thread-cas", values={"title": "Wrong default"})
    assert canonical["values"]["title"] == "Keep"
    assert canonical["values"]["messages"] == [{"type": "human", "content": "keep"}]


def test_delete_waits_for_inflight_cross_instance_update(tmp_path: Path) -> None:
    path = tmp_path / "threads.jsonl"
    first = ThreadStateStore(
        path=path,
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )
    first.ensure_thread("thread-delete-race")
    second = ThreadStateStore(
        path=path,
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )
    update_at_append = Event()
    release_update = Event()
    delete_at_append = Event()
    original_upsert = first._append_upsert
    original_delete = second._append_delete

    def blocked_upsert(*args, **kwargs):
        update_at_append.set()
        assert release_update.wait(timeout=5)
        return original_upsert(*args, **kwargs)

    def observed_delete(*args, **kwargs):
        delete_at_append.set()
        return original_delete(*args, **kwargs)

    first._append_upsert = blocked_upsert  # type: ignore[method-assign]
    second._append_delete = observed_delete  # type: ignore[method-assign]
    with ThreadPoolExecutor(max_workers=2) as pool:
        update = pool.submit(
            first.update_state,
            "thread-delete-race",
            values={"messages": [{"type": "human", "content": "committed first"}]},
        )
        assert update_at_append.wait(timeout=5)
        delete = pool.submit(second.delete, "thread-delete-race")
        assert not delete_at_append.wait(timeout=0.2)
        release_update.set()
        update.result(timeout=5)
        assert delete.result(timeout=5) is True

    assert (
        ThreadStateStore(
            path=path,
            index_enabled=False,
            search_enabled=False,
            feedback_enabled=False,
        ).get("thread-delete-race")
        is None
    )


def test_per_agent_projection_ignores_touched_stale_copy(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    store = ThreadStateStore(
        per_agent_base=root,
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )
    store.ensure_thread(
        "thread-sharded",
        metadata={"agent": "alpha"},
        values={"title": "New", "messages": [{"type": "human", "content": "new"}]},
    )
    alpha = root / "agents" / "alpha" / "sessions" / "thread-sharded.jsonl"
    beta = root / "agents" / "beta" / "sessions" / "thread-sharded.jsonl"
    beta.parent.mkdir(parents=True, exist_ok=True)
    lines = alpha.read_text(encoding="utf-8").splitlines()
    records = []
    for line in lines:
        record = json.loads(line)
        thread = record.get("thread") if isinstance(record, dict) else None
        if isinstance(thread, dict):
            thread["updated_at"] = "2020-01-01T00:00:00Z"
            thread["metadata"]["agent"] = "beta"
            thread["values"]["title"] = "Old"
            thread["values"]["messages"] = []
            # Legacy snapshots duplicated these fields in ``state``. Compact
            # snapshots inherit them from ``thread`` and need no second edit.
            if record.get("state_from_thread") is not True:
                record["state"]["metadata"]["agent"] = "beta"
                record["state"]["values"] = thread["values"]
        records.append(json.dumps(record, ensure_ascii=False))
    beta.write_text("\n".join(records) + "\n", encoding="utf-8")
    future = time.time() + 60
    os.utime(beta, (future, future))

    store.set_project_binding_metadata("thread-sharded", "P-sharded")

    reloaded = ThreadStateStore(
        per_agent_base=root,
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    ).get("thread-sharded")
    assert reloaded is not None
    assert reloaded["values"]["title"] == "New"
    assert reloaded["values"]["messages"] == [{"type": "human", "content": "new"}]
    assert reloaded["metadata"]["project_id"] == "P-sharded"
    assert not beta.exists()

