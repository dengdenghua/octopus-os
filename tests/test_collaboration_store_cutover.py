from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.cowork.collaboration_store import CollaborationStore
from runtime.projectos.store import ProjectStore
from runtime.sensing.gateway.projects_router import create_projects_router
from runtime.sensing.gateway.team_tasks_router import create_team_tasks_router


def test_projectos_tasks_project_into_collaboration_store(tmp_path) -> None:
    project_store = ProjectStore(base_dir=tmp_path / "projectos")
    collaboration_store = CollaborationStore(base_dir=tmp_path / "cowork")
    app = FastAPI()
    app.include_router(
        create_projects_router(
            store=project_store,
            collaboration_store=collaboration_store,
        )
    )
    client = TestClient(app)

    planned = client.post(
        "/api/projects",
        json={"name": "Cutover", "goal": "Build a small audit report"},
    )
    assert planned.status_code == 200
    project_id = planned.json()["project"]["id"]

    run = client.post(f"/api/projects/{project_id}/run", json={"max_ticks": 5})
    assert run.status_code == 200

    tasks = collaboration_store.project_tasks_for_project(project_id)
    assert tasks
    assert {task["kind"] for task in tasks} == {"project"}
    assert {task["project_id"] for task in tasks} == {project_id}
    assert all(task["milestone_id"] for task in tasks)
    assert all(task["metadata"]["source"] == "projectos" for task in tasks)


def test_collaboration_store_preserves_team_task_kind_and_artifacts(tmp_path) -> None:
    store = CollaborationStore(base_dir=tmp_path)
    store.upsert_room("thread-1", {"id": "room-1", "name": "Room"})

    task = store.upsert_task(
        "thread-1",
        {
            "id": "task-1",
            "room_id": "room-1",
            "kind": "team",
            "title": "Do work",
            "status": "blocked",
            "produced_artifacts": [{"kind": "markdown", "path": "report.md"}],
            "lease": {"holder": "agent-a", "expires_at": 123},
        },
    )

    assert task["kind"] == "team"
    assert task["status"] == "blocked"
    assert task["artifacts"] == [{"kind": "markdown", "path": "report.md"}]
    assert task["lease"]["holder"] == "agent-a"


def test_team_tasks_project_into_collaboration_store(tmp_path) -> None:
    collaboration_store = CollaborationStore(base_dir=tmp_path / "cowork")
    collaboration_store.upsert_room("thread-team-alpha", {"id": "team-alpha", "name": "Team Alpha"})

    app = FastAPI()
    app.include_router(
        create_team_tasks_router(
            state_path=tmp_path / "team_tasks.json",
            task_projection=lambda room_id, task: collaboration_store.upsert_task_for_room(
                room_id,
                task,
            ),
            task_delete_projection=collaboration_store.delete_task,
        ),
    )
    client = TestClient(app)

    created = client.post(
        "/api/team-tasks",
        json={
            "room_id": "team-alpha",
            "title": "Write launch brief",
            "description": "Prepare a concise brief",
            "assignees": [{"kind": "agent", "ref": "writer"}],
        },
    )
    assert created.status_code == 200
    task_id = created.json()["id"]

    tasks = collaboration_store.tasks_for_room("team-alpha")
    assert len(tasks) == 1
    assert tasks[0]["id"] == task_id
    assert tasks[0]["kind"] == "team"
    assert tasks[0]["title"] == "Write launch brief"

    patched = client.patch(
        f"/api/team-tasks/{task_id}",
        json={"status": "done", "title": "Launch brief ready"},
    )
    assert patched.status_code == 200
    projected = collaboration_store.tasks_for_room("team-alpha")[0]
    assert projected["status"] == "done"
    assert projected["title"] == "Launch brief ready"

    deleted = client.delete(f"/api/team-tasks/{task_id}")
    assert deleted.status_code == 200
    assert collaboration_store.tasks_for_room("team-alpha") == []

