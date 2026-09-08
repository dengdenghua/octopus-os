"""Echo desktop task projection over real Agent TaskSupervisor records."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.task_projection import create_task_projection_router
from runtime.platform.process.task_supervisor import TaskRunStatus, TaskSupervisor
from runtime.platform.runtime_policy.workspaces import WorkspaceManager
from runtime.safety.auth.identity import encode_jwt_hs256

JWT_SECRET = "task-projection-test-secret-which-is-not-production"


def _client(app: FastAPI, actor: str = "local:admin") -> TestClient:
    client = TestClient(app)
    token = encode_jwt_hs256(
        {"sub": actor, "iat": 0, "exp": 9_999_999_999},
        secret=JWT_SECRET,
    )
    client.cookies.set("echo_session", token)
    return client


@pytest.fixture
def private_task_app(tmp_path):
    supervisor = TaskSupervisor.from_path(tmp_path / "task-runs.json", holder_id="worker-a")
    for task_id, owner in (
        ("private-alice", "local:alice"),
        ("private-bob", "local:bob"),
        ("private-unowned", None),
    ):
        supervisor.start_task(
            task_id=task_id,
            kind="file_organization",
            owner_id=owner,
            title=f"synthetic-private-directory-{task_id}",
            workspace_path=str(tmp_path / task_id),
        )
        supervisor.store.mutate(
            task_id,
            lambda current: current.model_copy(
                update={
                    "lease": current.lease.model_copy(update={"expires_at": 1}),
                },
                deep=True,
            ),
        )
    supervisor.start_task(task_id="device-system", kind="system", owner_id="local:admin")
    app = FastAPI()
    app.include_router(create_task_projection_router(supervisor=supervisor, jwt_secret=JWT_SECRET))
    return app, supervisor


def test_private_file_tasks_filter_before_projection_and_counts(private_task_app):
    app, _ = private_task_app
    response = _client(app, "local:alice").get("/api/appliance/tasks")
    assert response.status_code == 200
    payload = response.json()
    assert {task["id"] for task in payload["tasks"]} == {"private-alice", "device-system"}
    assert payload["counts"]["total"] == 2
    assert "private-bob" not in response.text
    assert "private-unowned" not in response.text
    # This narrow privacy rule does not redefine existing system-task visibility.
    assert "device-system" in response.text
    admin = _client(app).get("/api/appliance/tasks").json()
    assert admin["counts"]["total"] == 4


@pytest.mark.parametrize("suffix", ["", "/takeover", "/resume-execution"])
def test_other_member_cannot_probe_or_control_private_task_by_spoofing_actor(
    private_task_app, suffix
):
    app, supervisor = private_task_app
    before = supervisor.store.get("private-bob")
    client = _client(app, "local:alice")
    if suffix:
        response = client.post(
            "/api/appliance/tasks/private-bob" + suffix,
            json={"actor": "local:admin", "owner_id": "local:bob"},
            headers={"X-Echo-Actor": "local:admin"},
        )
    else:
        response = client.get("/api/appliance/tasks/private-bob?actor=local:admin")
    assert response.status_code == 404
    assert response.json() == {"detail": "task not found"}
    assert supervisor.store.get("private-bob") == before


@pytest.mark.parametrize("actor", ["local:bob", "local:admin"])
def test_owner_and_explicit_admin_can_read_and_take_over_private_task(private_task_app, actor):
    app, supervisor = private_task_app
    client = _client(app, actor)
    assert client.get("/api/appliance/tasks/private-bob").status_code == 200
    response = client.post(
        "/api/appliance/tasks/private-bob/takeover",
        json={"actor": "local:forged"},
    )
    assert response.status_code == 200
    current = supervisor.store.get("private-bob")
    assert current.metadata["takeover_by"] == actor
    assert current.owner_id == "local:bob"


def test_unowned_private_task_is_admin_only_and_anonymous_still_requires_auth(private_task_app):
    app, _ = private_task_app
    assert (
        _client(app, "local:alice").get("/api/appliance/tasks/private-unowned").status_code == 404
    )
    assert _client(app).get("/api/appliance/tasks/private-unowned").status_code == 200
    assert TestClient(app).get("/api/appliance/tasks/private-alice").status_code == 401


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"completed_steps": 100, "total_steps": 500}, 20.0),
        ({"completed_steps": 250, "total_steps": 500}, 50.0),
        ({"completed_steps": 750, "total_steps": 500}, 100.0),
        ({"completed_steps": -1, "total_steps": 500}, None),
        ({"completed_steps": 1, "total_steps": 0}, None),
        ({"completed_steps": True, "total_steps": 500}, None),
        ({"completed_steps": "Infinity", "total_steps": 500}, None),
        ({"completed_steps": 100, "total_steps": "NaN"}, None),
        ({"progressPercent": "NaN"}, None),
        ({"progressPercent": "Infinity"}, None),
        ({"progressPercent": 125}, 100.0),
        ({"progressPercent": -5}, 0.0),
        ({"progressPercent": 42, "completed_steps": 250, "total_steps": 500}, 42.0),
        ({"progressPercent": "NaN", "completed_steps": 250, "total_steps": 500}, 50.0),
    ],
)
def test_projection_reports_full_task_counts_without_false_completion(tmp_path, metadata, expected):
    supervisor = TaskSupervisor.from_path(tmp_path / "task-runs.json", holder_id="worker-a")
    supervisor.start_task(
        task_id="task-documents",
        owner_id="local:admin",
        metadata=metadata,
    )
    app = FastAPI()
    app.include_router(create_task_projection_router(supervisor=supervisor, jwt_secret=JWT_SECRET))

    response = _client(app).get("/api/appliance/tasks")

    assert response.status_code == 200
    assert response.json()["tasks"][0]["progressPercent"] == expected


def test_projection_joins_agent_task_and_capability_audit(tmp_path) -> None:
    supervisor = TaskSupervisor.from_path(tmp_path / "task-runs.json", holder_id="worker-a")
    supervisor.start_task(
        task_id="task-photo-sort",
        kind="loop",
        owner_id="local:admin",
        thread_id="thread-photos",
        title="整理昨天的照片",
        goal="扫描重复照片，确认后移入回收站",
        mode="agent",
        metadata={
            "agent_id": "echo-eve",
            "completed_steps": 1,
            "total_steps": 4,
            "execution_engine": "echo",
            "model_name": "qwen3.5-plus",
        },
    )
    supervisor.start_task(
        task_id="task-app-start",
        kind="system",
        owner_id="local:admin",
        title="启动媒体服务",
        metadata={
            "approval_required": True,
            "approval_tool_name": "apps.start",
            "approval_action": "app.start",
        },
    )
    supervisor.transition(
        "task-app-start",
        TaskRunStatus.WAITING_APPROVAL,
        reason="需要管理员确认",
    )
    audit = ApplianceAudit.from_data_dir(tmp_path / "audit", jwt_secret=JWT_SECRET)
    audit.record(
        actor="local:admin",
        action="capability.decision",
        target="files.list",
        outcome="allow",
        metadata={
            "intentId": "task-photo-sort",
            "reasonCode": "POLICY_ALLOWED",
            "risk": "low",
            "requestedTarget": "photos/2026-08-25",
        },
    )
    audit.record(
        actor="local:admin",
        action="capability.decision",
        target="apps.start",
        outcome="ask",
        metadata={
            "intentId": "task-app-start",
            "reasonCode": "PASSWORD_STEP_UP_REQUIRED",
            "risk": "high",
            "requestedTarget": "a" * 12,
        },
    )
    app = FastAPI()
    app.include_router(
        create_task_projection_router(
            supervisor=supervisor,
            audit=audit,
            jwt_secret=JWT_SECRET,
        )
    )

    assert TestClient(app).get("/api/appliance/tasks").status_code == 401
    response = _client(app).get("/api/appliance/tasks")

    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "echo.task_projection.v1"
    assert body["available"] is True
    assert body["counts"] == {
        "total": 2,
        "active": 1,
        "waitingApproval": 1,
        "paused": 0,
        "recoveryNeeded": 0,
        "failed": 0,
        "completed": 0,
    }
    assert body["auditIntegrity"]["ok"] is True
    assert [task["id"] for task in body["tasks"]] == ["task-app-start", "task-photo-sort"]
    waiting = body["tasks"][0]
    assert waiting["status"] == "waiting_approval"
    assert waiting["approval"] == {
        "required": True,
        "tool": "apps.start",
        "action": "app.start",
        "reason": "需要管理员确认",
    }
    assert waiting["capabilityDecisions"][0]["capabilityId"] == "apps.start"
    photo = body["tasks"][1]
    assert photo["progressPercent"] == 25.0
    assert photo["agentId"] == "echo-eve"
    assert photo["executionEngine"] == "echo"
    assert photo["modelName"] == "qwen3.5-plus"
    assert photo["displayStatus"] == "running"
    assert photo["leaseHealth"]["recoveryNeeded"] is False
    assert photo["capabilityDecisions"][0]["target"] == "files.list"


def test_projection_exposes_bounded_completed_workspace_results(tmp_path) -> None:
    supervisor = TaskSupervisor.from_path(tmp_path / "task-runs.json", holder_id="worker-a")
    supervisor.start_task(
        task_id="task-result",
        owner_id="local:admin",
        thread_id="thread-result",
        title="生成报告",
    )
    supervisor.transition("task-result", TaskRunStatus.COMPLETED)
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    layout = workspaces.layout("thread-result")
    (layout.final / "report.md").write_text("# done", encoding="utf-8")
    app = FastAPI()
    app.include_router(
        create_task_projection_router(
            supervisor=supervisor,
            realtime_gateway=SimpleNamespace(_workspaces=workspaces),
            jwt_secret=JWT_SECRET,
        )
    )

    response = _client(app).get("/api/appliance/tasks")

    assert response.status_code == 200
    artifacts = response.json()["tasks"][0]["resultArtifacts"]
    assert artifacts[0]["name"] == "report.md"
    assert artifacts[0]["area"] == "final"
    assert artifacts[0]["resourceId"].startswith("workspace-file:v1:")
    assert str(tmp_path) not in response.text


def test_projection_skips_workspace_scan_for_live_tasks(tmp_path) -> None:
    supervisor = TaskSupervisor.from_path(tmp_path / "task-runs.json", holder_id="worker-a")
    supervisor.start_task(
        task_id="task-running-result",
        owner_id="local:admin",
        thread_id="thread-running-result",
        title="仍在执行",
    )

    class ExplodingWorkspaces:
        def layout(self, _thread_id):
            raise AssertionError("live task should not scan workspace outputs")

    app = FastAPI()
    app.include_router(
        create_task_projection_router(
            supervisor=supervisor,
            realtime_gateway=SimpleNamespace(_workspaces=ExplodingWorkspaces()),
            jwt_secret=JWT_SECRET,
        )
    )

    body = _client(app).get("/api/appliance/tasks").json()

    assert body["tasks"][0]["resultArtifacts"] == []


def test_projection_status_filter_and_unavailable_supervisor(tmp_path) -> None:
    audit = ApplianceAudit.from_data_dir(tmp_path / "audit", jwt_secret=JWT_SECRET)
    supervisor = TaskSupervisor.from_path(tmp_path / "task-runs.json", holder_id="worker-a")
    supervisor.start_task(task_id="task-running", title="进行中")
    supervisor.start_task(task_id="task-done", title="已完成")
    supervisor.transition("task-done", TaskRunStatus.COMPLETED)
    app = FastAPI()
    app.include_router(
        create_task_projection_router(
            supervisor=supervisor,
            audit=audit,
            jwt_secret=JWT_SECRET,
        )
    )
    missing_app = FastAPI()
    missing_app.include_router(
        create_task_projection_router(
            supervisor=None,
            audit=audit,
            jwt_secret=JWT_SECRET,
        )
    )

    completed = _client(app).get("/api/appliance/tasks", params={"status": "completed"})
    limited = _client(app).get("/api/appliance/tasks", params={"limit": 1})
    unavailable = _client(missing_app).get("/api/appliance/tasks")

    assert completed.json()["counts"]["total"] == 1
    assert completed.json()["tasks"][0]["id"] == "task-done"
    assert limited.json()["counts"]["total"] == 2
    assert len(limited.json()["tasks"]) == 1
    assert unavailable.json()["available"] is False
    assert unavailable.json()["tasks"] == []


def test_projection_failure_count_matches_desktop_terminal_filter(tmp_path) -> None:
    supervisor = TaskSupervisor.from_path(tmp_path / "task-runs.json", holder_id="worker-a")
    supervisor.start_task(task_id="task-failed", title="失败任务")
    supervisor.transition("task-failed", TaskRunStatus.FAILED, reason="执行失败")
    supervisor.start_task(task_id="task-cancelled", title="取消任务")
    supervisor.transition("task-cancelled", TaskRunStatus.CANCELLED, reason="用户取消")
    app = FastAPI()
    app.include_router(create_task_projection_router(supervisor=supervisor, jwt_secret=JWT_SECRET))

    body = _client(app).get("/api/appliance/tasks").json()

    assert body["counts"]["failed"] == 2
    assert {task["status"] for task in body["tasks"]} == {"failed", "cancelled"}


def test_projection_uses_agent_lease_health_for_interrupted_work(tmp_path) -> None:
    audit = ApplianceAudit.from_data_dir(tmp_path / "audit", jwt_secret=JWT_SECRET)
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task-runs.json",
        holder_id="old-worker",
    )
    supervisor.start_task(
        task_id="task-interrupted",
        thread_id="thread-interrupted",
        title="中断的任务",
    )
    supervisor.store.mutate(
        "task-interrupted",
        lambda current: current.model_copy(
            update={
                "lease": current.lease.model_copy(update={"expires_at": 1})
                if current.lease is not None
                else None
            },
            deep=True,
        ),
    )
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    (workspaces.layout("thread-interrupted").final / "checkpoint-result.txt").write_text(
        "partial result", encoding="utf-8"
    )
    app = FastAPI()
    app.include_router(
        create_task_projection_router(
            supervisor=supervisor,
            audit=audit,
            realtime_gateway=SimpleNamespace(_workspaces=workspaces),
            jwt_secret=JWT_SECRET,
        )
    )

    body = _client(app).get("/api/appliance/tasks").json()

    assert body["counts"]["active"] == 0
    assert body["counts"]["recoveryNeeded"] == 1
    assert body["counts"]["failed"] == 1
    task = body["tasks"][0]
    assert task["status"] == "running"
    assert task["displayStatus"] == "disconnected"
    assert task["leaseHealth"] == {
        "state": "expired",
        "recoveryNeeded": True,
        "canTakeover": True,
        "canResume": False,
        "recommendedAction": "takeover",
        "reason": "task has no live lease",
    }
    assert task["resultArtifacts"][0]["name"] == "checkpoint-result.txt"


def test_task_detail_and_takeover_reclaim_the_agent_lease_with_audit(tmp_path) -> None:
    audit = ApplianceAudit.from_data_dir(tmp_path / "audit", jwt_secret=JWT_SECRET)
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task-runs.json",
        holder_id="current-worker",
    )
    supervisor.start_task(
        task_id="task-recoverable",
        owner_id="local:admin",
        thread_id="thread-recoverable",
        title="恢复照片整理",
    )
    supervisor.store.mutate(
        "task-recoverable",
        lambda current: current.model_copy(
            update={
                "lease": current.lease.model_copy(
                    update={"holder_id": "dead-worker", "expires_at": 1}
                )
                if current.lease is not None
                else None
            },
            deep=True,
        ),
    )
    app = FastAPI()
    app.include_router(
        create_task_projection_router(
            supervisor=supervisor,
            audit=audit,
            jwt_secret=JWT_SECRET,
        )
    )
    client = _client(app)

    detail = client.get("/api/appliance/tasks/task-recoverable")
    takeover = client.post(
        "/api/appliance/tasks/task-recoverable/takeover",
        json={"reason": "设备管理员确认恢复"},
    )

    assert detail.status_code == 200
    assert detail.json()["schema"] == "echo.task_projection.detail.v1"
    assert detail.json()["task"]["leaseHealth"]["canTakeover"] is True
    assert takeover.status_code == 200
    body = takeover.json()
    assert body["schema"] == "echo.task_action.v1"
    assert body["action"] == "takeover"
    assert body["requiresWorkspaceResume"] is True
    assert body["task"]["status"] == "running"
    assert body["task"]["displayStatus"] == "running"
    assert body["task"]["leaseHealth"]["state"] == "ok"
    assert body["task"]["leaseHealth"]["canTakeover"] is False
    assert any(
        item["action"] == "task.takeover" and item["outcome"] == "succeeded"
        for item in body["task"]["activity"]
    )
    recovered = supervisor.store.get("task-recoverable")
    assert recovered is not None
    assert recovered.lease is not None
    assert recovered.lease.holder_id == "current-worker"
    assert recovered.metadata["takeover_by"] == "local:admin"

    task_events = [
        entry["payload"]
        for entry in audit.recent(20)
        if entry["payload"]["action"] == "task.takeover"
    ]
    assert [event["outcome"] for event in task_events] == [
        "attempted",
        "succeeded",
    ]
    assert task_events[-1]["metadata"]["intentId"] == "task-recoverable"
    assert task_events[-1]["metadata"]["reason"] == "设备管理员确认恢复"


def test_takeover_rejects_a_task_with_a_live_agent_lease(tmp_path) -> None:
    audit = ApplianceAudit.from_data_dir(tmp_path / "audit", jwt_secret=JWT_SECRET)
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task-runs.json",
        holder_id="current-worker",
    )
    supervisor.start_task(task_id="task-live", title="仍在执行")
    app = FastAPI()
    app.include_router(
        create_task_projection_router(
            supervisor=supervisor,
            audit=audit,
            jwt_secret=JWT_SECRET,
        )
    )

    unauthenticated = TestClient(app).post("/api/appliance/tasks/task-live/takeover")
    rejected = _client(app).post("/api/appliance/tasks/task-live/takeover")

    assert unauthenticated.status_code == 401
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "task lease is not available for takeover"
    assert supervisor.is_current_holder("task-live") is True
    assert audit.recent(10) == []


def test_takeover_fails_closed_before_mutation_when_audit_is_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    audit = ApplianceAudit.from_data_dir(tmp_path / "audit", jwt_secret=JWT_SECRET)
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task-runs.json",
        holder_id="current-worker",
    )
    supervisor.start_task(task_id="task-audit-blocked", title="等待安全接管")
    supervisor.store.mutate(
        "task-audit-blocked",
        lambda current: current.model_copy(
            update={
                "lease": current.lease.model_copy(
                    update={"holder_id": "dead-worker", "expires_at": 1}
                )
                if current.lease is not None
                else None
            },
            deep=True,
        ),
    )
    before = supervisor.store.get("task-audit-blocked")
    monkeypatch.setattr(
        audit,
        "record",
        lambda **_kwargs: (_ for _ in ()).throw(AuditIntegrityError("tampered")),
    )
    app = FastAPI()
    app.include_router(
        create_task_projection_router(
            supervisor=supervisor,
            audit=audit,
            jwt_secret=JWT_SECRET,
        )
    )

    response = _client(app).post(
        "/api/appliance/tasks/task-audit-blocked/takeover",
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "task action audit unavailable"
    after = supervisor.store.get("task-audit-blocked")
    assert before is not None and after is not None
    assert after.lease == before.lease
    assert "takeover_by" not in after.metadata


def test_resume_execution_uses_agent_checkpoint_route_and_audits_handoff(
    tmp_path,
    monkeypatch,
) -> None:
    audit = ApplianceAudit.from_data_dir(tmp_path / "audit", jwt_secret=JWT_SECRET)
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task-runs.json",
        holder_id="current-worker",
    )
    task_id = "4d1bb3de-65e4-4f42-b004-eb148eceae77"
    thread_id = "thread-checkpoint-recovery"
    supervisor.start_task(
        task_id=task_id,
        owner_id="local:admin",
        thread_id=thread_id,
        title="继续整理照片",
        metadata={"agent_id": "echo-eve"},
    )
    supervisor.store.mutate(
        task_id,
        lambda current: current.model_copy(
            update={
                "latest_checkpoint_id": 73,
                "lease": current.lease.model_copy(
                    update={"holder_id": "dead-worker", "expires_at": 1}
                )
                if current.lease is not None
                else None,
            },
            deep=True,
        ),
    )

    class _Gateway:
        _runtime = object()
        _active_turn_threads: set[str] = set()

    gateway = _Gateway()
    from runtime.sensing.gateway import _realtime_turn_lifecycle_resume as resume_module

    monkeypatch.setattr(
        resume_module,
        "_resume_checkpoint_metadata",
        lambda _runtime, candidate: (
            {"checkpoint_id": 73, "iteration": 8, "phase": "verify", "working_set": []}
            if candidate == task_id
            else None
        ),
    )
    observed: dict = {}
    app = FastAPI()

    @app.post("/api/task-runs/{candidate}/resume-execution", status_code=202)
    async def _agent_resume(candidate: str, request: Request) -> dict:
        observed["candidate"] = candidate
        observed["authorization"] = request.headers.get("authorization")
        observed["body"] = await request.json()
        supervisor.transition(
            candidate,
            TaskRunStatus.PAUSED,
            metadata_patch={"resume_execution_state": "turn_started"},
        )
        return {
            "schema": "echo.task_run_resume_execution.v1",
            "state": "turn_started",
            "turn_id": "trn-73",
            "request_id": observed["body"]["requestId"],
        }

    app.include_router(
        create_task_projection_router(
            supervisor=supervisor,
            realtime_gateway=gateway,
            audit=audit,
            jwt_secret=JWT_SECRET,
        )
    )
    client = _client(app)

    before = client.get("/api/appliance/tasks").json()["tasks"][0]
    takeover = client.post(
        f"/api/appliance/tasks/{task_id}/takeover",
        json={"reason": "接管离线 worker"},
    )
    after_takeover = takeover.json()["task"]
    resumed = client.post(
        f"/api/appliance/tasks/{task_id}/resume-execution",
        json={
            "requestId": "echo-recovery-73",
            "reason": "管理员确认从检查点继续",
        },
    )

    assert before["executionRecovery"] == {
        "checkpointAvailable": True,
        "canStart": False,
        "requiresTakeover": True,
        "checkpointId": 73,
        "iteration": 8,
        "phase": "verify",
        "reason": "检查点可用；请先接管失效的 Agent 租约",
    }
    assert after_takeover["executionRecovery"]["canStart"] is True
    assert resumed.status_code == 200
    body = resumed.json()
    assert body["schema"] == "echo.task_action.v1"
    assert body["action"] == "resume_execution"
    assert body["state"] == "turn_started"
    assert body["turnId"] == "trn-73"
    assert body["requestId"] == "echo-recovery-73"
    assert body["threadPath"] == f"/workspace/realtime/{thread_id}"
    assert body["task"]["status"] == "paused"
    assert observed["candidate"] == task_id
    assert observed["authorization"].startswith("Bearer ")
    assert observed["body"] == {
        "reason": "管理员确认从检查点继续",
        "requestId": "echo-recovery-73",
    }
    resume_events = [
        entry["payload"]
        for entry in audit.recent(30)
        if entry["payload"]["action"] == "task.resume_execution"
    ]
    assert [event["outcome"] for event in resume_events] == ["attempted", "accepted"]
    assert all(event["metadata"]["intentId"] == task_id for event in resume_events)
    assert resume_events[-1]["metadata"]["turnId"] == "trn-73"


def test_resume_execution_fails_closed_before_agent_handoff_when_audit_is_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    audit = ApplianceAudit.from_data_dir(tmp_path / "audit", jwt_secret=JWT_SECRET)
    supervisor = TaskSupervisor.from_path(tmp_path / "tasks.json", holder_id="worker")
    task_id = "2f0180fd-51ad-42dd-9382-87a2a12c7c31"
    supervisor.start_task(task_id=task_id, thread_id="thread-audit-resume")
    supervisor.transition(task_id, TaskRunStatus.PAUSED, checkpoint_id=5)

    class _Gateway:
        _runtime = object()
        _active_turn_threads: set[str] = set()

    from runtime.sensing.gateway import _realtime_turn_lifecycle_resume as resume_module

    monkeypatch.setattr(
        resume_module,
        "_resume_checkpoint_metadata",
        lambda _runtime, _candidate: {
            "checkpoint_id": 5,
            "iteration": 1,
            "phase": "execute",
            "working_set": [],
        },
    )
    monkeypatch.setattr(
        audit,
        "record",
        lambda **_kwargs: (_ for _ in ()).throw(AuditIntegrityError("tampered")),
    )
    calls: list[str] = []
    app = FastAPI()

    @app.post("/api/task-runs/{candidate}/resume-execution", status_code=202)
    async def _agent_resume(candidate: str) -> dict:
        calls.append(candidate)
        return {"schema": "echo.task_run_resume_execution.v1"}

    app.include_router(
        create_task_projection_router(
            supervisor=supervisor,
            realtime_gateway=_Gateway(),
            audit=audit,
            jwt_secret=JWT_SECRET,
        )
    )

    response = _client(app).post(
        f"/api/appliance/tasks/{task_id}/resume-execution",
        json={"requestId": "echo-audit-blocked"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "task action audit unavailable"
    assert calls == []
    task = supervisor.store.get(task_id)
    assert task is not None
    assert task.status == TaskRunStatus.PAUSED
