from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.cowork.collaboration_store import CollaborationStore
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.session import link_room
from runtime.projectos.cowork_bridge import full_project_state
from runtime.projectos.model import Milestone, Project
from runtime.projectos.store import ProjectStore
from runtime.sensing.gateway.cowork_group_router import create_cowork_group_router
from runtime.sensing.gateway.projects_router import create_projects_router


def _project_group_client(tmp_path):
    group_store = GroupStore(base_dir=tmp_path / "cowork")
    collaboration_store = CollaborationStore(base_dir=tmp_path / "cowork")
    project_store = ProjectStore(base_dir=tmp_path / "projectos")
    project = Project(
        id="PROJ-1",
        name="Launch project",
        goal="Ship the launch",
        milestone_ids=["MS-1"],
    )
    project_store.save_project(project)
    project_store.save_milestone(
        project.id,
        Milestone(
            id="MS-1",
            name="Release candidate",
            goal="Prepare the release candidate",
            success_criteria=["Release candidate is approved"],
        ),
    )
    project_store.bind_thread("thread-1", project.id)
    link_room(group_store, "thread-1", "room-1")
    collaboration_store.upsert_room(
        "thread-1",
        {
            "id": "room-1",
            "name": "Launch project",
            "metadata": {"source": "projectos", "project_id": project.id},
        },
    )
    _bound, binding_generation = project_store.binding_snapshot("thread-1")
    collaboration_store.set_room_project_metadata(
        "thread-1",
        project.id,
        generation=binding_generation,
    )
    app = FastAPI()
    app.include_router(
        create_cowork_group_router(
            store=group_store,
            collaboration_store=collaboration_store,
            project_store=project_store,
        )
    )
    app.include_router(
        create_projects_router(
            store=project_store,
            collaboration_store=collaboration_store,
            group_store=group_store,
        )
    )
    return TestClient(app), project_store, collaboration_store


def test_structured_room_message_round_trip_and_source_idempotency(tmp_path) -> None:
    client, _project_store, collaboration_store = _project_group_client(tmp_path)
    payload = {
        "text": "Milestone is at risk",
        "participant_id": "project-os",
        "display_name": "Project OS",
        "source_message_id": "chat-msg-42",
        "message_type": "system_card",
        "entity_refs": [
            {
                "kind": "milestone",
                "id": "MS-1",
                "project_id": "PROJ-1",
                "label": "Release candidate",
            }
        ],
        "system_card": {
            "type": "milestone_risk",
            "title": "Release candidate at risk",
            "status": "blocked",
        },
        "metadata": {"channel": "project-group"},
    }

    created = client.post("/api/collab/thread-1/room-message", json=payload)

    assert created.status_code == 200
    message = created.json()["message"]
    assert message["metadata"]["schema"] == "echo.room_message.metadata.v1"
    assert message["metadata"]["source_message_id"] == "chat-msg-42"
    assert message["metadata"]["message_type"] == "system_card"
    assert message["metadata"]["entity_refs"][0]["id"] == "MS-1"
    assert message["metadata"]["system_card"]["status"] == "blocked"
    assert message["metadata"]["channel"] == "project-group"

    # Network retries with the same external/source id do not fork the
    # canonical transcript.
    retried = client.post("/api/collab/thread-1/room-message", json=payload)
    assert retried.status_code == 200
    assert retried.json()["seq"] == created.json()["seq"] == 1
    assert len(collaboration_store.messages_for_session("thread-1")) == 1

    conflict = client.post(
        "/api/collab/thread-1/room-message",
        json={**payload, "text": "different content"},
    )
    assert conflict.status_code == 400
    assert "source_message_id" in conflict.json()["detail"]

    session_message = client.get("/api/collab/thread-1").json()["room_messages"][0]
    assert session_message["metadata"]["system_card"]["type"] == "milestone_risk"


def test_collaboration_store_migrates_legacy_messages_for_optional_metadata(tmp_path) -> None:
    db = tmp_path / "collaboration.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(
            """
            CREATE TABLE collaboration_messages (
                session_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                room_id TEXT NOT NULL,
                participant_id TEXT,
                display_name TEXT,
                text TEXT NOT NULL,
                ts TEXT NOT NULL,
                PRIMARY KEY (session_id, seq)
            );
            INSERT INTO collaboration_messages(
                session_id, seq, room_id, participant_id, display_name, text, ts
            ) VALUES ('thread-old', 1, 'room-old', '', '', 'legacy line', 't0');
            """
        )

    store = CollaborationStore(base_dir=tmp_path)

    assert store.messages_for_session("thread-old")[0]["metadata"] == {}
    seq = store.append_message(
        "thread-old",
        room_id="room-old",
        text="structured line",
        metadata={"source_message_id": "source-2", "entity_refs": []},
    )
    assert seq == 2
    assert store.message_by_source_id("thread-old", "source-2")["text"] == "structured line"

    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "UPDATE collaboration_messages SET metadata_json = '{broken-json' "
            "WHERE session_id = 'thread-old' AND seq = 1"
        )
    legacy = store.message_for_session("thread-old", 1)
    assert legacy["text"] == "legacy line"
    assert legacy["metadata"] == {}


def test_create_item_writes_project_os_then_projects_to_group_idempotently(tmp_path) -> None:
    client, project_store, collaboration_store = _project_group_client(tmp_path)
    source = client.post(
        "/api/collab/thread-1/room-message",
        json={
            "text": "Please prepare the launch checklist",
            "source_message_id": "chat-source-1",
            "display_name": "Alice",
        },
    ).json()["message"]
    action_payload = {
        "action": "create_item",
        "milestone_id": "MS-1",
        "title": "Prepare launch checklist",
        "description": "Cover owners, dates, and rollback",
        "task_type": "analysis",
        "priority": "P1",
        "estimate": 1.5,
        "acceptance_criteria": ["Every launch step has an owner"],
        "assigned_agent": "planner",
    }

    response = client.post(
        f"/api/collab/thread-1/room-messages/{source['seq']}/project-actions",
        json=action_payload,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "create_item"
    assert body["created"] is True and body["replayed"] is False
    task = project_store.get_task(body["target"]["id"])
    assert task is not None
    assert task.milestone_id == "MS-1"
    assert task.goal == "Prepare launch checklist"
    assert task.priority == "P1"
    assert task.input["source_message"]["message_seq"] == source["seq"]
    assert task.id in project_store.get_milestone("MS-1").task_ids

    projected = collaboration_store.project_tasks_for_project("PROJ-1")
    assert [item["id"] for item in projected] == [task.id]
    assert projected[0]["metadata"]["source"] == "projectos"
    assert projected[0]["metadata"]["source_message"]["message_seq"] == source["seq"]
    assert body["system_card_message"]["metadata"]["message_type"] == "system_card"
    assert body["system_card_message"]["metadata"]["system_card"]["type"] == "create_item"

    retried = client.post(
        f"/api/collab/thread-1/room-messages/{source['seq']}/project-actions",
        json=action_payload,
    )
    assert retried.status_code == 200
    assert retried.json()["replayed"] is True
    assert len(project_store.tasks_for_milestone("MS-1")) == 1
    assert len(collaboration_store.messages_for_session("thread-1")) == 2
    assert [event["kind"] for event in project_store.events_for_project("PROJ-1")] == [
        "project.task_created_from_message"
    ]


@pytest.mark.parametrize(
    "action_payload",
    [
        {
            "action": "create_item",
            "milestone_id": "MS-1",
            "title": "Must stay on the observed project",
        },
        {"action": "record_decision", "decision": "Must stay on the observed project"},
    ],
)
def test_message_action_source_write_rejects_a_stale_binding_generation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    action_payload: dict,
) -> None:
    client, project_store, collaboration_store = _project_group_client(tmp_path)
    source = client.post(
        "/api/collab/thread-1/room-message",
        json={"text": "Apply this only to the current project"},
    ).json()["message"]
    winner = Project(id="PROJ-2", name="New winner", goal="Own later actions")
    project_store.save_project(winner)
    snapshot_read = Event()
    release_snapshot = Event()
    real_snapshot = project_store.binding_snapshot
    first_snapshot = True

    def pause_after_binding_read(thread_id: str, **kwargs):
        nonlocal first_snapshot
        result = real_snapshot(thread_id, **kwargs)
        if first_snapshot and thread_id == "thread-1":
            first_snapshot = False
            snapshot_read.set()
            assert release_snapshot.wait(timeout=5)
        return result

    monkeypatch.setattr(project_store, "binding_snapshot", pause_after_binding_read)
    endpoint = f"/api/collab/thread-1/room-messages/{source['seq']}/project-actions"
    with ThreadPoolExecutor(max_workers=1) as pool:
        response_future = pool.submit(client.post, endpoint, json=action_payload)
        assert snapshot_read.wait(timeout=5)
        detached, clear_generation = project_store.unbind_thread_versioned(
            "thread-1",
            expected_project_id="PROJ-1",
        )
        assert detached is not None and clear_generation == 2
        canonical, winner_generation = project_store.bind_thread_versioned(
            "thread-1",
            winner.id,
        )
        assert canonical.id == winner.id and winner_generation == 3
        release_snapshot.set()
        response = response_future.result(timeout=5)

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "PROJECT_BINDING_CHANGED",
        "message": "thread project binding changed while the message action was applied",
        "thread_id": "thread-1",
        "project_id": "PROJ-1",
        "binding_generation": 1,
    }
    assert project_store.tasks_for_milestone("MS-1") == []
    assert project_store.events_for_project("PROJ-1") == []
    assert collaboration_store.project_tasks_for_project("PROJ-1") == []
    assert len(collaboration_store.messages_for_session("thread-1")) == 1


def test_message_action_late_projection_is_atomic_and_reports_recovery(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, project_store, collaboration_store = _project_group_client(tmp_path)
    source = client.post(
        "/api/collab/thread-1/room-message",
        json={"text": "Commit this task before the room binding changes"},
    ).json()["message"]
    projection_started = Event()
    release_projection = Event()
    real_commit = collaboration_store.commit_project_message_action

    def pause_projection(**kwargs):
        projection_started.set()
        assert release_projection.wait(timeout=5)
        return real_commit(**kwargs)

    monkeypatch.setattr(
        collaboration_store,
        "commit_project_message_action",
        pause_projection,
    )
    endpoint = f"/api/collab/thread-1/room-messages/{source['seq']}/project-actions"
    with ThreadPoolExecutor(max_workers=1) as pool:
        response_future = pool.submit(
            client.post,
            endpoint,
            json={
                "action": "create_item",
                "milestone_id": "MS-1",
                "title": "Atomically audited task",
            },
        )
        assert projection_started.wait(timeout=5)

        # The authoritative task and its audit/outbox event are one commit:
        # neither can be observed without the other.
        tasks = project_store.tasks_for_milestone("MS-1")
        events = project_store.events_for_project("PROJ-1")
        assert len(tasks) == len(events) == 1
        assert collaboration_store.project_tasks_for_project("PROJ-1") == []

        winner = Project(id="PROJ-2", name="New winner", goal="Own later actions")
        project_store.save_project(winner)
        detached, clear_generation = project_store.unbind_thread_versioned(
            "thread-1",
            expected_project_id="PROJ-1",
        )
        assert detached is not None and clear_generation == 2
        canonical, winner_generation = project_store.bind_thread_versioned(
            "thread-1",
            winner.id,
        )
        assert canonical.id == winner.id and winner_generation == 3
        collaboration_store.upsert_project_room(
            session_id="thread-1",
            room={"id": "room-1", "name": "New winner"},
            project_id=winner.id,
            generation=winner_generation,
        )
        release_projection.set()
        response = response_future.result(timeout=5)

    assert response.status_code == 200
    body = response.json()
    assert body["projection_pending"] is True
    assert body["recovery"]["code"] == "PROJECT_BINDING_CHANGED"
    assert body["event"]["id"] == body["recovery"]["event_id"]
    assert collaboration_store.project_tasks_for_project("PROJ-1") == []
    enriched = collaboration_store.message_for_session("thread-1", source["seq"])
    assert enriched["metadata"].get("project_actions") in (None, [])
    assert len(collaboration_store.messages_for_session("thread-1")) == 1


def test_link_room_cannot_replace_a_project_owned_session_room(tmp_path) -> None:
    client, _project_store, collaboration_store = _project_group_client(tmp_path)
    collaboration_store.append_message(
        "thread-1",
        room_id="room-1",
        text="Keep the project transcript anchored",
    )

    response = client.post(
        "/api/collab/thread-1/link-room",
        json={"room_id": "room-replacement"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ROOM_LINK_CONFLICT"
    room = collaboration_store.room_for_session("thread-1")
    assert room is not None
    assert room["id"] == "room-1"
    assert room["project_id"] == "PROJ-1"
    assert [item["text"] for item in collaboration_store.messages_for_session("thread-1")] == [
        "Keep the project transcript anchored"
    ]
    assert collaboration_store.room_by_id("room-replacement") is None


def test_link_decision_and_artifact_actions_enrich_one_source_message(tmp_path) -> None:
    client, project_store, collaboration_store = _project_group_client(tmp_path)
    source = client.post(
        "/api/collab/thread-1/room-message",
        json={"text": "Use the Friday release window", "source_message_id": "chat-source-2"},
    ).json()["message"]
    endpoint = f"/api/collab/thread-1/room-messages/{source['seq']}/project-actions"

    linked = client.post(
        endpoint,
        json={"action": "link_milestone", "milestone_id": "MS-1"},
    )
    decided = client.post(
        endpoint,
        json={
            "action": "record_decision",
            "decision": "Release on Friday",
            "rationale": "Support coverage is highest",
        },
    )
    published = client.post(
        endpoint,
        json={
            "action": "publish_artifact",
            "artifact": {
                "id": "ART-PLAN",
                "title": "Launch plan",
                "path": "deliverables/launch-plan.md",
            },
        },
    )

    assert linked.status_code == decided.status_code == published.status_code == 200
    assert {event["kind"] for event in project_store.events_for_project("PROJ-1")} == {
        "project.message_linked",
        "project.decision_recorded",
        "project.artifact_published",
    }
    enriched = collaboration_store.message_for_session("thread-1", source["seq"])
    refs = {(ref["kind"], ref["id"]) for ref in enriched["metadata"]["entity_refs"]}
    assert ("project", "PROJ-1") in refs
    assert ("milestone", "MS-1") in refs
    assert ("artifact", "ART-PLAN") in refs
    assert any(kind == "decision" for kind, _entity_id in refs)
    assert len(enriched["metadata"]["project_actions"]) == 3
    assert len(collaboration_store.messages_for_session("thread-1")) == 4

    state_response = client.get("/api/projects/by-thread/thread-1")
    assert state_response.status_code == 200
    assert state_response.json()["artifacts"] == [
        {
            "id": "ART-PLAN",
            "name": "Launch plan",
            "path": "deliverables/launch-plan.md",
        }
    ]
    decision = state_response.json()["decisions"][0]
    assert decision == {
        "id": decided.json()["target"]["id"],
        "title": "Release on Friday",
        "summary": "Support coverage is highest",
        "decision": "Release on Friday",
        "actor": "user",
        "created_at": decision["created_at"],
        "source_message_id": "chat-source-2",
    }
    assert datetime.fromisoformat(decision["created_at"]).tzinfo is not None

    replayed = client.post(
        endpoint,
        json={
            "action": "publish_artifact",
            "artifact": {
                "id": "ART-PLAN",
                "title": "Launch plan",
                "path": "deliverables/launch-plan.md",
            },
        },
    )
    assert replayed.status_code == 200
    assert replayed.json()["replayed"] is True
    assert len(project_store.artifacts_for_project("PROJ-1")) == 1
    assert len(collaboration_store.messages_for_session("thread-1")) == 4

    # The read model is reconstructed from SQLite events, not process memory.
    reopened = ProjectStore(base_dir=tmp_path / "projectos")
    reopened_state = full_project_state(reopened, "PROJ-1")
    assert reopened_state["artifacts"] == [
        {
            "id": "ART-PLAN",
            "name": "Launch plan",
            "path": "deliverables/launch-plan.md",
        }
    ]
    assert reopened_state["decisions"] == state_response.json()["decisions"]

    hits = client.get(
        "/api/cowork/thread-1/search",
        params={"q": "Launch plan", "kinds": "room_message"},
    ).json()["hits"]
    assert any(hit["ref"].get("entity_refs") for hit in hits)


def test_message_project_action_requires_bound_project_and_owned_milestone(tmp_path) -> None:
    client, _project_store, _collaboration_store = _project_group_client(tmp_path)
    source = client.post(
        "/api/collab/thread-1/room-message",
        json={"text": "Convert me"},
    ).json()["message"]

    missing = client.post(
        f"/api/collab/thread-1/room-messages/{source['seq']}/project-actions",
        json={"action": "create_item", "milestone_id": "MS-NOT-OURS"},
    )

    assert missing.status_code == 404
    assert "milestone" in missing.json()["detail"]


def test_moving_project_to_thread_promotes_project_room_to_group_session(tmp_path) -> None:
    project_store = ProjectStore(base_dir=tmp_path / "projectos")
    collaboration_store = CollaborationStore(base_dir=tmp_path / "cowork")
    group_store = GroupStore(base_dir=tmp_path / "cowork")
    app = FastAPI()
    app.include_router(
        create_projects_router(
            store=project_store,
            collaboration_store=collaboration_store,
            group_store=group_store,
        )
    )
    client = TestClient(app)
    planned = client.post(
        "/api/projects",
        json={"name": "Group launch", "goal": "Deliver through the project group"},
    ).json()
    project_id = planned["project"]["id"]
    room_id = f"project:{project_id}"
    collaboration_store.append_message_for_room(room_id, text="project kickoff")

    moved = client.post(
        "/api/projects/move",
        json={"thread_id": "thread-project-group", "project_id": project_id},
    )

    assert moved.status_code == 200
    assert collaboration_store.session_id_for_room(room_id) == "thread-project-group"
    assert collaboration_store.room_for_session(f"project:{project_id}") is None
    assert [
        message["text"]
        for message in collaboration_store.messages_for_session("thread-project-group")
    ] == ["project kickoff"]

    # A late generation-zero standalone writer cannot steal the promoted room
    # back from its bound session.
    stale = CollaborationStore(base_dir=tmp_path / "cowork")
    with pytest.raises(RuntimeError, match="versioned project API"):
        stale.upsert_room(
            f"project:{project_id}",
            {"id": room_id, "name": "Late generic standalone projection"},
        )
    with pytest.raises(RuntimeError, match="superseded|stale|conflict"):
        stale.upsert_project_room(
            session_id=f"project:{project_id}",
            room={"id": room_id, "name": "Late standalone projection"},
            project_id=project_id,
            generation=0,
        )
    assert collaboration_store.session_id_for_room(room_id) == "thread-project-group"

    _bound, generation = project_store.binding_snapshot("thread-project-group")
    collaboration_store.upsert_project_task(
        session_id="thread-project-group",
        room_id=room_id,
        project_id=project_id,
        milestone_id=planned["milestones"][0]["id"],
        task={"id": "TASK-anchored", "title": "Stay in the canonical session"},
        binding_generation=generation,
    )

    # A Project OS project has one canonical collaboration thread. A second
    # move must explicitly detach the first instead of stealing its read model.
    second = client.post(
        "/api/projects/move",
        json={"thread_id": "thread-project-group-2", "project_id": project_id},
    )
    assert second.status_code == 409
    assert second.json()["detail"] == {
        "code": "PROJECT_ALREADY_BOUND",
        "message": "project is already bound to another thread; detach it first",
        "project_id": project_id,
        "canonical_thread_id": "thread-project-group",
        "requested_thread_id": "thread-project-group-2",
    }
    assert collaboration_store.room_for_session("thread-project-group-2") is None
    assert collaboration_store.room_for_session("thread-project-group")["id"] == room_id
    assert [
        message["text"]
        for message in collaboration_store.messages_for_session("thread-project-group")
    ] == ["project kickoff"]
    assert [
        task["id"] for task in collaboration_store.tasks_for_session("thread-project-group")
    ] == ["TASK-anchored"]
    assert collaboration_store.tasks_for_session("thread-project-group-2") == []
    assert project_store.thread_project_map() == {"thread-project-group": project_id}

