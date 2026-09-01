"""Project OS API: plan / tick / run / report over HTTP (stub hooks, no LLM)."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.projectos.model import Milestone, Project, Task
from runtime.projectos.store import ProjectStore
from runtime.sensing.gateway.projects_router import create_projects_router


def _client(tmp_path) -> TestClient:
    app = FastAPI()
    app.include_router(create_projects_router(store=ProjectStore(base_dir=tmp_path)))
    return TestClient(app)


def _client_with_store(tmp_path) -> tuple[TestClient, ProjectStore]:
    store = ProjectStore(base_dir=tmp_path)
    app = FastAPI()
    app.include_router(create_projects_router(store=store))
    return TestClient(app), store


class _UnavailableModelRouter:
    def call(self, _request):
        raise RuntimeError("no LLM model configured")


class _StaticMilestoneModelRouter:
    def call(self, _request):
        return SimpleNamespace(
            text=(
                '[{"name":"Scope","goal":"Define scope"},'
                '{"name":"Deliver","goal":"Ship it","dependencies":["Scope"]}]'
            )
        )


def test_plan_run_report_flow(tmp_path) -> None:
    c = _client(tmp_path)

    planned = c.post("/api/projects", json={"name": "sleep", "goal": "smart sleep system"})
    assert planned.status_code == 200
    pid = planned.json()["project"]["id"]
    assert planned.json()["milestones"]  # at least one milestone generated (stub: 1)
    assert planned.json()["available_actions"] == ["run", "tick"]

    run = c.post(f"/api/projects/{pid}/run", json={"max_ticks": 20})
    assert run.status_code == 200
    assert run.json()["final_status"] == "done"

    report = c.get(f"/api/projects/{pid}/report").json()
    assert report["status"] == "done"
    assert report["milestones"][0]["tasks"][0]["status"] == "done"

    # appears in the list
    assert pid in [p["id"] for p in c.get("/api/projects").json()["projects"]]


def test_plan_without_configured_model_can_create_consecutive_projects(tmp_path) -> None:
    store = ProjectStore(base_dir=tmp_path)
    app = FastAPI()
    app.include_router(create_projects_router(store=store, model_router=_UnavailableModelRouter()))
    client = TestClient(app)

    first = client.post(
        "/api/projects",
        json={"name": "First project group", "goal": "Ship first"},
    )
    second = client.post(
        "/api/projects",
        json={"name": "Second project group", "goal": "Ship second"},
    )

    assert first.status_code == second.status_code == 200
    first_state = first.json()
    second_state = second.json()
    assert first_state["milestones"][0]["id"] == "MS1"
    assert first_state["milestones"][0]["name"] == "deliver"
    assert second_state["milestones"][0]["id"] == (f"{second_state['project']['id']}:MS1")
    assert second_state["milestones"][0]["goal"] == "Ship second"
    assert len(store.list_projects()) == 2
    assert all(
        [event["kind"] for event in store.events_for_project(project_id)] == ["project.planned"]
        for project_id in (
            first_state["project"]["id"],
            second_state["project"]["id"],
        )
    )


def test_normal_llm_plan_rewrites_dependencies_across_consecutive_projects(tmp_path) -> None:
    store = ProjectStore(base_dir=tmp_path)
    app = FastAPI()
    app.include_router(
        create_projects_router(store=store, model_router=_StaticMilestoneModelRouter())
    )
    client = TestClient(app)

    first = client.post("/api/projects", json={"name": "First", "goal": "First goal"})
    second = client.post("/api/projects", json={"name": "Second", "goal": "Second goal"})

    assert first.status_code == second.status_code == 200
    first_milestones = first.json()["milestones"]
    second_milestones = second.json()["milestones"]
    assert [item["id"] for item in first_milestones] == ["MS1", "MS2"]
    assert second_milestones[1]["dependencies"] == [second_milestones[0]["id"]]
    assert {item["id"] for item in first_milestones}.isdisjoint(
        {item["id"] for item in second_milestones}
    )


def test_sidebar_project_endpoints_list_move_and_delete(tmp_path) -> None:
    c = _client(tmp_path)
    planned = c.post("/api/projects", json={"name": "workspace", "goal": "organize work"})
    assert planned.status_code == 200
    project_id = planned.json()["project"]["id"]

    moved = c.post(
        "/api/projects/move",
        json={"thread_id": "thread-1", "project_id": project_id},
    )
    assert moved.status_code == 200
    assert c.get("/api/projects/thread-map").json() == {"thread-1": project_id}

    deleted = c.delete(f"/api/projects/{project_id}")
    assert deleted.status_code == 200
    assert c.get("/api/projects/thread-map").json() == {}
    assert c.get(f"/api/projects/{project_id}").status_code == 404


def test_run_tick_budget_is_bounded_at_api_boundary(tmp_path) -> None:
    c = _client(tmp_path)
    pid = c.post("/api/projects", json={"name": "sleep", "goal": "smart sleep system"}).json()[
        "project"
    ]["id"]

    too_large = c.post(f"/api/projects/{pid}/run", json={"max_ticks": 100_000})
    too_small = c.post(f"/api/projects/{pid}/run", json={"max_ticks": 0})

    assert too_large.status_code == 422
    assert too_small.status_code == 422


def test_tick_advances_incrementally(tmp_path) -> None:
    c = _client(tmp_path)
    pid = c.post("/api/projects", json={"name": "x", "goal": "g"}).json()["project"]["id"]
    r = c.post(f"/api/projects/{pid}/tick").json()
    assert "events" in r and r["project_status"] in ("running", "done")


def test_404s(tmp_path) -> None:
    c = _client(tmp_path)
    assert c.get("/api/projects/nope").status_code == 404
    assert c.post("/api/projects/nope/tick").status_code == 404
    assert c.get("/api/projects/nope/report").status_code == 404


def test_invalid_ids_return_400_instead_of_server_error(tmp_path) -> None:
    c, store = _client_with_store(tmp_path)
    project = Project(id="P-valid", name="valid", goal="g", milestone_ids=["MS1"])
    store.save_project(project)
    store.save_milestone(project.id, Milestone(id="MS1", name="build", goal="build it"))

    assert c.get("/api/projects/bad!").status_code == 400
    assert c.post("/api/projects/bad!/tick").status_code == 400
    assert c.get("/api/projects/bad!/report").status_code == 400
    assert c.get("/api/projects/bad!/events").status_code == 400
    assert c.get("/api/projects/by-thread/bad!").status_code == 400

    invalid_task = c.post(
        "/api/projects/P-valid/tasks/bad!/intervene",
        json={"action": "reset"},
    )
    assert invalid_task.status_code == 400


def test_recover_reopens_blocked_project_and_can_run(tmp_path) -> None:
    c, store = _client_with_store(tmp_path)
    project = Project(
        id="P-blocked",
        name="blocked",
        goal="recover me",
        milestone_ids=["MS1"],
        current_ms="MS1",
        status="blocked",
    )
    store.save_project(project)
    store.save_milestone(
        project.id,
        Milestone(id="MS1", name="build", goal="build it", status="blocked"),
    )
    store.save_task(
        Task(
            id="MS1-T1",
            milestone_id="MS1",
            type="code",
            goal="retry this",
            status="failed",
            output="bad",
            attempts=2,
        )
    )

    recovered = c.post(
        "/api/projects/P-blocked/recover",
        json={"run": True, "max_ticks": 10},
    )

    assert recovered.status_code == 200
    body = recovered.json()
    assert body["ok"] is True
    assert body["recover"]["project_status"] == "running"
    assert "project_recovered" in body["recover"]["events"]
    assert body["run"]["final_status"] == "done"
    assert body["project"]["status"] == "done"
    assert body["tasks"]["MS1"][0]["status"] == "done"
    assert body["available_actions"] == ["inspect", "report"]
    assert body["tasks"]["MS1"][0]["available_actions"] == ["reset"]
    events = c.get("/api/projects/P-blocked/events").json()["events"]
    assert [event["kind"] for event in events] == ["project.recover", "project.run"]
    recover_event = next(event for event in events if event["kind"] == "project.recover")
    run_event = next(event for event in events if event["kind"] == "project.run")
    assert recover_event["payload"]["events"] == body["recover"]["events"]
    assert run_event["payload"]["final_status"] == "done"


def test_intervene_task_reassigns_and_runs(tmp_path) -> None:
    c, store = _client_with_store(tmp_path)
    project = Project(
        id="P-intervene",
        name="intervene",
        goal="repair task",
        milestone_ids=["MS1"],
        current_ms="MS1",
        status="blocked",
    )
    store.save_project(project)
    store.save_milestone(
        project.id,
        Milestone(id="MS1", name="build", goal="build it", status="blocked"),
    )
    store.save_task(
        Task(
            id="MS1-T1",
            milestone_id="MS1",
            type="code",
            goal="retry this",
            assigned_agent="old-agent",
            status="failed",
            output="bad",
            attempts=2,
        )
    )

    res = c.post(
        "/api/projects/P-intervene/tasks/MS1-T1/intervene",
        json={
            "action": "reassign",
            "assigned_agent": "new-agent",
            "run": True,
            "max_ticks": 10,
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert "task_reassigned:MS1-T1" in body["intervention"]["events"]
    assert body["run"]["final_status"] == "done"
    task = body["tasks"]["MS1"][0]
    assert task["assigned_agent"] == "new-agent"
    assert task["status"] == "done"
    assert task["available_actions"] == ["reset"]
    events = c.get("/api/projects/P-intervene/events").json()["events"]
    assert [event["kind"] for event in events] == ["task.intervention", "project.run"]
    intervention = next(event for event in events if event["kind"] == "task.intervention")
    assert intervention["payload"]["action"] == "reassign"
    assert intervention["payload"]["assigned_agent"] == "new-agent"


def test_intervene_task_validates_missing_and_unknown_action(tmp_path) -> None:
    c, store = _client_with_store(tmp_path)
    project = Project(id="P-errors", name="errors", goal="g", milestone_ids=["MS1"])
    store.save_project(project)
    store.save_milestone(project.id, Milestone(id="MS1", name="build", goal="build it"))

    missing = c.post(
        "/api/projects/P-errors/tasks/no-task/intervene",
        json={"action": "reset"},
    )
    assert missing.status_code == 404

    store.save_task(Task(id="MS1-T1", milestone_id="MS1", type="code", goal="g"))
    unknown = c.post(
        "/api/projects/P-errors/tasks/MS1-T1/intervene",
        json={"action": "teleport"},
    )
    assert unknown.status_code == 400
    events = c.get("/api/projects/P-errors/events").json()["events"]
    assert [event["kind"] for event in events] == [
        "task.intervention_rejected",
        "task.intervention_rejected",
    ]


def test_project_events_404(tmp_path) -> None:
    c = _client(tmp_path)
    assert c.get("/api/projects/nope/events").status_code == 404


def test_project_state_exposes_action_hints(tmp_path) -> None:
    c, store = _client_with_store(tmp_path)
    project = Project(
        id="P-actions",
        name="actions",
        goal="g",
        milestone_ids=["MS1"],
        current_ms="MS1",
        status="blocked",
    )
    store.save_project(project)
    store.save_milestone(
        project.id,
        Milestone(id="MS1", name="build", goal="build it", status="blocked"),
    )
    store.save_task(
        Task(
            id="MS1-T1",
            milestone_id="MS1",
            type="code",
            goal="fix it",
            status="failed",
        )
    )

    body = c.get("/api/projects/P-actions").json()

    assert body["available_actions"] == ["recover", "recover_and_run"]
    assert body["action_specs"][0]["api"]["path"] == "/api/projects/P-actions/recover"
    assert body["action_specs"][1]["realtime_command"] == "/project recover run"
    assert body["tasks"]["MS1"][0]["available_actions"] == [
        "reassign",
        "reset",
        "complete",
        "skip",
    ]
    reassign_spec = body["tasks"]["MS1"][0]["action_specs"][0]
    assert reassign_spec["action"] == "reassign"
    assert reassign_spec["requires"] == ["assigned_agent"]
    assert reassign_spec["api"]["path"] == ("/api/projects/P-actions/tasks/MS1-T1/intervene")
    assert reassign_spec["realtime_command"] == ("/project task MS1-T1 reassign agent=<agent-id>")


def test_get_project_by_thread(tmp_path) -> None:
    c, store = _client_with_store(tmp_path)
    project = Project(id="P-thread", name="thread project", goal="g")
    store.save_project(project)
    store.bind_thread("thread-1", project.id)

    body = c.get("/api/projects/by-thread/thread-1").json()

    assert body["project"]["id"] == "P-thread"
    assert body["action_specs"][0]["api"]["path"] == "/api/projects/P-thread/run"
    assert c.get("/api/projects/by-thread/missing").status_code == 404


def test_project_process_timeline_endpoint_persists_run_evidence(tmp_path) -> None:
    c = _client(tmp_path)
    planned = c.post("/api/projects", json={"name": "timeline", "goal": "ship it"})
    assert planned.status_code == 200
    pid = planned.json()["project"]["id"]
    run = c.post(f"/api/projects/{pid}/run", json={"max_ticks": 20})
    assert run.status_code == 200

    response = c.get(f"/api/projects/{pid}/process-timeline")

    assert response.status_code == 200
    timeline = response.json()["timeline"]
    assert timeline["schema"] == "echo.projectos.process_timeline.v1"
    assert timeline["project_id"] == pid
    assert timeline["overview"]["status"] == "done"
    assert timeline["overview"]["event_count"] >= 2
    assert timeline["safety"]["raw_task_outputs_included"] is False
    assert timeline["safety"]["process_events_persisted"] is True
    kinds = {node["kind"] for node in timeline["timeline"]}
    lanes = {node["lane"] for node in timeline["timeline"]}
    assert {"project.planned", "project.run", "milestone_state", "task_state"} <= kinds
    assert {"project", "milestone", "task"} <= lanes
    assert c.get("/api/projects/nope/process-timeline").status_code == 404

