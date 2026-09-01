from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from runtime.safety.organization.team_runner import (  # noqa: E402
    RoleOutput,
    TeamRunResult,
)
from runtime.safety.organization.topology import Role  # noqa: E402
from runtime.sensing.gateway.team_rooms_router import (  # noqa: E402
    create_team_rooms_router,
)
from runtime.sensing.gateway.team_tasks_router import (  # noqa: E402
    TeamTaskWire,
    _fallback_topology,
    _task_input_text,
    create_team_tasks_router,
)


class _SuccessRunner:
    def __init__(
        self,
        *,
        timeout_seconds: int | None = None,
        event_emitter=None,
    ) -> None:
        self._event_emitter = event_emitter

    def run(self, topology, task: str, *, context: dict[str, Any] | None = None):
        role, spec = next(iter(topology.agents.items()))
        if self._event_emitter is not None:
            self._event_emitter(
                {
                    "type": "team_role_start",
                    "role": str(role),
                    "agent_id": spec.agent_id,
                }
            )
            self._event_emitter(
                {
                    "type": "team_role_end",
                    "role": str(role),
                    "agent_id": spec.agent_id,
                    "status": "success",
                    "output": "role output",
                }
            )
        return TeamRunResult(
            topology_name=topology.name,
            topology_fingerprint=topology.fingerprint,
            task_bucket=topology.task_bucket,
            success=True,
            final_output=f"final output for {task}",
            role_outputs=[
                RoleOutput(
                    role=role,
                    agent_id=spec.agent_id,
                    output="role output",
                    duration_ms=1.0,
                ),
            ],
            iterations=1,
            total_duration_ms=2.0,
        )


class _FailureRunner:
    def __init__(
        self,
        *,
        timeout_seconds: int | None = None,
        event_emitter=None,
    ) -> None:
        self._event_emitter = event_emitter

    def run(self, topology, task: str, *, context: dict[str, Any] | None = None):
        role, spec = next(iter(topology.agents.items()))
        if self._event_emitter is not None:
            self._event_emitter(
                {
                    "type": "team_role_end",
                    "role": str(role),
                    "agent_id": spec.agent_id,
                    "status": "error",
                    "error": "boom",
                }
            )
        return TeamRunResult(
            topology_name=topology.name,
            topology_fingerprint=topology.fingerprint,
            task_bucket=topology.task_bucket,
            success=False,
            final_output="",
            role_outputs=[
                RoleOutput(
                    role=role,
                    agent_id=spec.agent_id,
                    output="",
                    error="boom",
                    duration_ms=1.0,
                ),
            ],
            iterations=1,
            total_duration_ms=2.0,
            error="boom",
        )


def _client(
    tmp_path: Path,
    runner_factory,
    events: list[tuple[str, dict[str, Any]]],
) -> TestClient:
    app = FastAPI()

    async def _broadcast(room_id: str, payload: dict[str, Any]) -> None:
        events.append((room_id, payload))

    app.include_router(
        create_team_tasks_router(
            state_path=tmp_path / "team_tasks.json",
            team_event_broadcaster=_broadcast,
            runner_factory=runner_factory,
        ),
    )
    return TestClient(app)


def _client_with_team_room_ws(tmp_path: Path) -> TestClient:
    app = FastAPI()
    rooms_router = create_team_rooms_router(
        state_path=tmp_path / "team_rooms.json",
    )
    app.include_router(rooms_router)
    app.include_router(
        create_team_tasks_router(
            state_path=tmp_path / "team_tasks.json",
            team_event_broadcaster=rooms_router.broadcast,
            runner_factory=_SuccessRunner,
        ),
    )
    return TestClient(app)


def _team_body(name: str = "Lifecycle Room") -> dict[str, Any]:
    return {
        "name": name,
        "members": [
            {
                "name": "general",
                "display_name": "Echo",
                "description": "General assistant",
                "model": None,
                "tool_groups": None,
            },
        ],
        "leaderId": "general",
    }


def _create_task(
    client: TestClient,
    *,
    title: str = "zzzz private runner smoke",
    sop_template: str = "",
) -> dict[str, Any]:
    response = client.post(
        "/api/team-tasks",
        json={
            "room_id": "team-alpha",
            "title": title,
            "description": "execute this task through the team runner",
            "sop_template": sop_template,
            "assignees": [{"kind": "agent", "ref": "agent-a"}],
        },
    )
    assert response.status_code == 200
    return response.json()


def _wait_for_status(
    client: TestClient,
    task_id: str,
    expected: str,
    *,
    timeout_s: float = 3.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/team-tasks/{task_id}")
        assert response.status_code == 200
        last = response.json()
        if last["status"] == expected:
            return last
        time.sleep(0.02)
    raise AssertionError(f"task did not reach {expected}; last={last}")


def _wait_for_event(
    events: list[tuple[str, dict[str, Any]]],
    expected: str,
    *,
    timeout_s: float = 3.0,
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if expected in [payload["event"] for _, payload in events]:
            return
        time.sleep(0.02)
    raise AssertionError(
        f"event {expected!r} not observed; events={[payload['event'] for _, payload in events]}",
    )


def test_create_update_delete_broadcast_task_progress_events(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    client = _client(tmp_path, _SuccessRunner, events)

    task = _create_task(client, title="lifecycle broadcast smoke")

    assert events[-1][0] == "team-alpha"
    assert events[-1][1]["type"] == "task:progress"
    assert events[-1][1]["event"] == "task_created"
    assert events[-1][1]["task_id"] == task["id"]

    updated = client.patch(
        f"/api/team-tasks/{task['id']}",
        json={"title": "updated lifecycle broadcast smoke"},
    )
    assert updated.status_code == 200
    assert events[-1][1]["event"] == "task_updated"
    assert events[-1][1]["task"]["title"] == "updated lifecycle broadcast smoke"

    deleted = client.delete(f"/api/team-tasks/{task['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert events[-1][1]["event"] == "task_deleted"
    assert events[-1][1]["deleted"] is True
    assert events[-1][1]["task_id"] == task["id"]


def test_create_task_persists_source_metadata(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    client = _client(tmp_path, _SuccessRunner, events)

    response = client.post(
        "/api/team-tasks",
        json={
            "room_id": "team-alpha",
            "title": "linked company task",
            "metadata": {
                "source": "company_workbench",
                "company_task_id": "task-1",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"] == {
        "source": "company_workbench",
        "company_task_id": "task-1",
    }


def test_task_input_includes_output_contract() -> None:
    task = TeamTaskWire(
        id="task-contract",
        room_id="team-alpha",
        title="Prepare project update",
        description="Summarize execution result",
        created_at="2026-06-07T00:00:00+00:00",
        updated_at="2026-06-07T00:00:00+00:00",
        metadata={
            "output_contract": {
                "name": "project_update_v1",
                "instructions": ["Include one fenced json block."],
                "schema": {
                    "risks": [],
                    "next_actions": [],
                    "decisions": [],
                },
            },
        },
    )

    text = _task_input_text(task)

    assert "Prepare project update" in text
    assert "Output contract: project_update_v1" in text
    assert "Include one fenced json block." in text
    assert '"next_actions": []' in text


def test_role_named_assignee_does_not_override_every_role() -> None:
    task = TeamTaskWire(
        id="task-role-map",
        room_id="team-alpha",
        title="Prepare project update",
        created_at="2026-06-07T00:00:00+00:00",
        updated_at="2026-06-07T00:00:00+00:00",
        assignees=[{"kind": "agent", "ref": "planner"}],
        metadata={
            "output_contract": {
                "name": "project_update_v1",
                "schema": {
                    "risks": [],
                    "next_actions": [],
                    "decisions": [],
                },
            },
        },
    )

    topology = _fallback_topology(task)

    assert topology.agents[Role.PLANNER].agent_id == "planner"
    assert topology.agents[Role.SYNTHESIZER].agent_id == "synthesizer"
    addendum = topology.agents[Role.SYNTHESIZER].system_addendum or ""
    assert "exactly one fenced json block" in addendum
    assert "project_update_v1" in addendum
    assert '"next_actions": []' in addendum


def test_custom_single_agent_assignee_can_still_run_all_roles() -> None:
    task = TeamTaskWire(
        id="task-custom-agent",
        room_id="team-alpha",
        title="Custom agent workflow",
        created_at="2026-06-07T00:00:00+00:00",
        updated_at="2026-06-07T00:00:00+00:00",
        assignees=[{"kind": "agent", "ref": "agent-a"}],
    )

    topology = _fallback_topology(task)

    assert topology.agents[Role.PLANNER].agent_id == "agent-a"
    assert topology.agents[Role.SYNTHESIZER].agent_id == "agent-a"


def test_task_lifecycle_events_reach_team_room_websocket(tmp_path: Path) -> None:
    client = _client_with_team_room_ws(tmp_path)
    team = client.post("/api/teams", json=_team_body()).json()
    url = f"/api/teams/{team['id']}/ws"

    with client.websocket_connect(
        f"{url}?participant_id=alice&display_name=Alice&thread_id=thread-a",
    ) as ws:
        assert ws.receive_json()["type"] == "ready"
        assert ws.receive_json()["type"] == "presence"

        created = client.post(
            "/api/team-tasks",
            json={
                "room_id": team["id"],
                "title": "WS lifecycle smoke",
                "description": "ensure lifecycle events reach room websocket",
                "assignees": [{"kind": "agent", "ref": "general"}],
            },
        )
        assert created.status_code == 200
        task = created.json()
        created_event = ws.receive_json()
        assert created_event["type"] == "task:progress"
        assert created_event["event"] == "task_created"
        assert created_event["task_id"] == task["id"]
        assert created_event["task"]["title"] == "WS lifecycle smoke"

        updated = client.patch(
            f"/api/team-tasks/{task['id']}",
            json={"title": "WS lifecycle smoke updated"},
        )
        assert updated.status_code == 200
        updated_event = ws.receive_json()
        assert updated_event["type"] == "task:progress"
        assert updated_event["event"] == "task_updated"
        assert updated_event["task"]["title"] == "WS lifecycle smoke updated"

        deleted = client.delete(f"/api/team-tasks/{task['id']}")
        assert deleted.status_code == 200
        deleted_event = ws.receive_json()
        assert deleted_event["type"] == "task:progress"
        assert deleted_event["event"] == "task_deleted"
        assert deleted_event["deleted"] is True
        assert deleted_event["task_id"] == task["id"]


def test_run_task_executes_runner_and_writes_done_state(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    client = _client(tmp_path, _SuccessRunner, events)
    task = _create_task(client)

    started = client.post(f"/api/team-tasks/{task['id']}/run")

    assert started.status_code == 200
    assert started.json()["status"] == "running"

    done = _wait_for_status(client, task["id"], "done")
    assert done["completed_at"]
    assert done["produced_artifacts"][0]["type"] == "team_runner_output"
    assert (
        "final output for zzzz private runner smoke" in (done["produced_artifacts"][0]["content"])
    )
    assert done["metadata"]["runner"]["topology"]["agents"]["planner"]["agent_id"] == "agent-a"

    _wait_for_event(events, "run_done")
    event_names = [payload["event"] for _, payload in events]
    assert "run_started" in event_names
    assert "role_completed" in event_names
    assert "run_done" in event_names
    assert {room_id for room_id, _ in events} == {"team-alpha"}


def test_run_task_records_failed_state_on_runner_failure(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    client = _client(tmp_path, _FailureRunner, events)
    task = _create_task(client, title="zzzz private runner failure")

    started = client.post(f"/api/team-tasks/{task['id']}/run")

    assert started.status_code == 200
    failed = _wait_for_status(client, task["id"], "failed")
    assert failed["metadata"]["error"] == "boom"
    assert failed["metadata"]["runner"]["error"] == "boom"
    assert failed["produced_artifacts"] == []
    assert "run_failed" in [payload["event"] for _, payload in events]


def test_run_task_rejects_missing_explicit_sop_template(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    client = _client(tmp_path, _SuccessRunner, events)
    task = _create_task(
        client,
        sop_template="definitely-missing-test-sop-template-9d75d08d",
    )
    events.clear()

    response = client.post(f"/api/team-tasks/{task['id']}/run")

    assert response.status_code == 400
    assert "meta-skill not found" in response.json()["detail"]
    unchanged = client.get(f"/api/team-tasks/{task['id']}").json()
    assert unchanged["status"] == "pending"
    assert events == []
