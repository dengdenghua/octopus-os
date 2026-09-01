from __future__ import annotations

import time
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.core.cerebrum.pause_control import PauseController
from runtime.platform.process.task_supervisor import TaskRunStatus, TaskSupervisor
from runtime.protocol import JsonRpcErrorCode
from runtime.safety.auth.identity import Identity, IdentityStore
from runtime.sensing.gateway._realtime_gateway_types import _RpcError
from runtime.sensing.gateway.task_runs_router import create_task_runs_router


def test_task_runs_router_lists_and_reads_supervisor_records(tmp_path):
    supervisor = TaskSupervisor.from_path(tmp_path / "task_runs.json", holder_id="worker-a")
    supervisor.start_task(
        task_id="task-1",
        kind="loop",
        owner_id="alice",
        thread_id="thread-1",
        title="Fix tests",
        goal="Fix tests",
        mode="code",
    )
    supervisor.transition("task-1", TaskRunStatus.COMPLETED, checkpoint_id=7)
    supervisor.start_task(
        task_id="task-2",
        kind="background",
        owner_id="bob",
        thread_id="thread-2",
        title="Sync",
        goal="Sync",
    )

    app = FastAPI()
    app.include_router(create_task_runs_router(supervisor=supervisor))
    client = TestClient(app)

    listed = client.get("/api/task-runs", params={"kind": "loop"})
    detail = client.get("/api/task-runs/task-1")
    missing = client.get("/api/task-runs/missing")
    overview = client.get("/api/task-runs/overview")
    alice_overview = client.get("/api/task-runs/overview", params={"owner_id": "alice"})

    assert listed.status_code == 200
    body = listed.json()
    assert body["schema"] == "echo.task_runs.v1"
    assert body["total"] == 1
    assert body["tasks"][0]["task_id"] == "task-1"
    assert body["tasks"][0]["status"] == "completed"
    assert body["items"][0]["task_run"]["task_id"] == "task-1"
    assert body["items"][0]["lease_health"]["state"] == "terminal"
    assert body["items"][0]["lease_health"]["recommended_action"] == "none"

    assert detail.status_code == 200
    assert detail.json()["task_run"]["latest_checkpoint_id"] == 7
    assert detail.json()["lease_health"]["state"] == "terminal"

    assert missing.status_code == 404

    assert overview.status_code == 200
    overview_body = overview.json()
    assert overview_body["schema"] == "echo.task_runs_overview.v1"
    assert overview_body["total"] == 2
    assert overview_body["active_count"] == 1
    assert overview_body["terminal_count"] == 1
    assert overview_body["by_status"] == {"completed": 1, "running": 1}
    assert overview_body["active_task_ids"] == ["task-2"]
    assert overview_body["takeover_recommended_count"] == 0
    assert overview_body["resumable_count"] == 0
    assert overview_body["by_recommended_action"] == {"monitor": 1, "none": 1}
    assert overview_body["lease_health"][0]["task_id"] == "task-2"
    assert overview_body["lease_health"][0]["state"] == "ok"
    assert overview_body["lease_health"][0]["recommended_action"] == "monitor"

    assert alice_overview.status_code == 200
    alice_body = alice_overview.json()
    assert alice_body["total"] == 1
    assert alice_body["terminal_count"] == 1
    assert alice_body["active_count"] == 0
    assert alice_body["by_recommended_action"] == {"none": 1}
    assert alice_body["filters"]["owner_id"] == "alice"


def test_task_runs_router_recovery_queue_filters_and_isolates_owner(tmp_path):
    path = tmp_path / "task_runs.json"
    supervisor = TaskSupervisor.from_path(path, holder_id="worker-a", lease_ttl_seconds=30)
    supervisor.start_task(
        task_id="task-alice-expired",
        kind="loop",
        owner_id="alice",
        title="Alice expired",
    )
    supervisor.start_task(
        task_id="task-alice-running",
        kind="loop",
        owner_id="alice",
        title="Alice running",
    )
    supervisor.start_task(
        task_id="task-bob-failed",
        kind="loop",
        owner_id="bob",
        title="Bob failed",
    )
    supervisor.transition(
        "task-bob-failed",
        TaskRunStatus.FAILED,
        checkpoint_id="ckpt-bob",
    )

    def _expire(record):
        assert record.lease is not None
        return record.model_copy(
            update={
                "latest_checkpoint_id": "ckpt-alice",
                "lease": record.lease.model_copy(update={"expires_at": time.time() - 1}),
            },
            deep=True,
        )

    supervisor.store.mutate("task-alice-expired", _expire)
    identity_store = IdentityStore()
    identity_store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    identity_store.add(Identity(actor_id="bob"), api_key_plaintext="sk-bob")

    app = FastAPI()
    app.include_router(
        create_task_runs_router(
            supervisor=supervisor,
            identity_store=identity_store,
            require_auth=True,
        )
    )
    client = TestClient(app)

    alice_queue = client.get(
        "/api/task-runs/recovery-queue",
        headers={"Authorization": "Bearer sk-alice"},
    )
    alice_with_monitor = client.get(
        "/api/task-runs/recovery-queue",
        params={"include_monitor": True},
        headers={"Authorization": "Bearer sk-alice"},
    )
    bob_queue = client.get(
        "/api/task-runs/recovery-queue",
        headers={"Authorization": "Bearer sk-bob"},
    )

    assert alice_queue.status_code == 200
    assert alice_queue.json()["schema"] == "echo.task_recovery_queue.v1"
    assert alice_queue.json()["filters"]["owner_id"] == "alice"
    assert [item["task_id"] for item in alice_queue.json()["items"]] == ["task-alice-expired"]
    assert alice_queue.json()["items"][0]["recommended_action"] == "takeover_and_resume"
    assert alice_queue.json()["items"][0]["latest_checkpoint_id"] == "ckpt-alice"
    assert alice_queue.json()["items"][0]["operation"] == "takeover_then_resume"
    assert alice_queue.json()["items"][0]["steps"] == [
        "takeover_task",
        "resume_from_checkpoint",
    ]
    assert alice_queue.json()["items"][0]["recovery_plan"]["checkpoint_id"] == "ckpt-alice"

    assert alice_with_monitor.status_code == 200
    assert alice_with_monitor.json()["filters"]["include_monitor"] is True
    assert {item["task_id"] for item in alice_with_monitor.json()["items"]} == {
        "task-alice-expired",
        "task-alice-running",
    }

    assert bob_queue.status_code == 200
    assert [item["task_id"] for item in bob_queue.json()["items"]] == ["task-bob-failed"]
    assert bob_queue.json()["items"][0]["recommended_action"] == "resume_from_checkpoint"
    assert bob_queue.json()["items"][0]["operation"] == "resume_from_checkpoint"


def test_task_runs_router_recovers_persisted_queue_read_only_after_restart(tmp_path):
    path = tmp_path / "task_runs.json"
    before_power_loss = TaskSupervisor.from_path(
        path,
        holder_id="worker-before-power-loss",
        lease_ttl_seconds=30,
    )
    task_id = "task-persisted-across-cold-boot"
    before_power_loss.start_task(
        task_id=task_id,
        kind="realtime_objective",
        owner_id="local:admin",
        thread_id="thread-persisted-across-cold-boot",
        title="继续断电前的任务",
    )

    def _checkpoint_then_power_loss(record):
        assert record.lease is not None
        return record.model_copy(
            update={
                "latest_checkpoint_id": 88,
                "lease": record.lease.model_copy(update={"expires_at": time.time() - 1}),
            },
            deep=True,
        )

    before_power_loss.store.mutate(task_id, _checkpoint_then_power_loss)
    persisted_before_read = path.read_bytes()

    after_cold_boot = TaskSupervisor.from_path(
        path,
        holder_id="worker-after-cold-boot",
        lease_ttl_seconds=30,
    )
    app = FastAPI()
    app.include_router(create_task_runs_router(supervisor=after_cold_boot))

    response = TestClient(app).get(
        "/api/task-runs/recovery-queue",
        params={"limit": 200},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "echo.task_recovery_queue.v1"
    assert body["count"] == 1
    assert body["limit"] == 200
    assert body["items"][0]["task_id"] == task_id
    assert body["items"][0]["recommended_action"] == "takeover_and_resume"
    assert body["items"][0]["checkpoint_id"] == 88
    assert body["items"][0]["steps"] == [
        "takeover_task",
        "resume_from_checkpoint",
    ]
    assert path.read_bytes() == persisted_before_read


def test_task_runs_router_list_total_counts_filtered_rows_not_page_size(tmp_path):
    supervisor = TaskSupervisor.from_path(tmp_path / "task_runs.json", holder_id="worker-a")
    for index in range(3):
        supervisor.start_task(
            task_id=f"task-loop-{index}",
            kind="loop",
            owner_id="alice",
            thread_id=f"thread-{index}",
        )
    supervisor.start_task(task_id="task-background", kind="background", owner_id="alice")

    app = FastAPI()
    app.include_router(create_task_runs_router(supervisor=supervisor))
    client = TestClient(app)

    response = client.get("/api/task-runs", params={"kind": "loop", "limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["count"] == 2
    assert len(body["tasks"]) == 2
    assert len(body["items"]) == 2
    assert all(item["task_run"]["kind"] == "loop" for item in body["items"])


def test_task_runs_router_records_approval_decision_with_owner_isolation(tmp_path):
    supervisor = TaskSupervisor.from_path(tmp_path / "task_runs.json", holder_id="worker-a")
    supervisor.start_task(
        task_id="task-approval",
        kind="loop",
        owner_id="alice",
        metadata={
            "approval_required": True,
            "approval_tool_name": "exec_shell",
            "approval_action": "confirm",
        },
    )
    supervisor.transition(
        "task-approval",
        TaskRunStatus.WAITING_APPROVAL,
        reason="approval required",
    )
    supervisor.start_task(task_id="task-running", kind="loop", owner_id="alice")
    identity_store = IdentityStore()
    identity_store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    identity_store.add(Identity(actor_id="bob"), api_key_plaintext="sk-bob")

    app = FastAPI()
    app.include_router(
        create_task_runs_router(
            supervisor=supervisor,
            identity_store=identity_store,
            require_auth=True,
        )
    )
    client = TestClient(app)

    denied = client.post(
        "/api/task-runs/task-approval/approval-decision",
        json={"approved": True, "reason": "ship it"},
        headers={"Authorization": "Bearer sk-bob"},
    )
    conflict = client.post(
        "/api/task-runs/task-running/approval-decision",
        json={"approved": True},
        headers={"Authorization": "Bearer sk-alice"},
    )
    approved = client.post(
        "/api/task-runs/task-approval/approval-decision",
        json={"approved": True, "reason": "ship it"},
        headers={"Authorization": "Bearer sk-alice"},
    )

    assert denied.status_code == 404
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "task is not waiting for approval"

    assert approved.status_code == 200
    body = approved.json()
    assert body["schema"] == "echo.task_run_approval_decision.v1"
    assert body["task_run"]["status"] == "running"
    assert body["task_run"]["metadata"]["approval_decision"] == "approved"
    assert body["task_run"]["metadata"]["approval_decided_by"] == "alice"
    assert body["task_run"]["metadata"]["approval_decision_reason"] == "ship it"
    assert body["lease_health"]["recommended_action"] == "monitor"


def test_task_runs_router_auth_hides_unowned_and_other_owners(
    tmp_path,
):
    supervisor = TaskSupervisor.from_path(tmp_path / "task_runs.json", holder_id="worker-a")
    supervisor.start_task(task_id="task-public", kind="loop")
    supervisor.start_task(task_id="task-alice", kind="loop", owner_id="alice")
    supervisor.start_task(task_id="task-bob", kind="loop", owner_id="bob")
    identity_store = IdentityStore()
    identity_store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    identity_store.add(
        Identity(actor_id="admin", roles=("admin",)),
        api_key_plaintext="sk-admin",
    )

    app = FastAPI()
    app.include_router(
        create_task_runs_router(
            supervisor=supervisor,
            identity_store=identity_store,
            require_auth=True,
        )
    )
    client = TestClient(app)

    listed = client.get("/api/task-runs", headers={"Authorization": "Bearer sk-alice"})
    public_detail = client.get(
        "/api/task-runs/task-public",
        headers={"Authorization": "Bearer sk-alice"},
    )
    bob_detail = client.get(
        "/api/task-runs/task-bob",
        headers={"Authorization": "Bearer sk-alice"},
    )
    overview = client.get("/api/task-runs/overview", headers={"Authorization": "Bearer sk-alice"})
    admin_listed = client.get(
        "/api/task-runs",
        headers={"Authorization": "Bearer sk-admin"},
    )
    admin_public_detail = client.get(
        "/api/task-runs/task-public",
        headers={"Authorization": "Bearer sk-admin"},
    )

    assert listed.status_code == 200
    task_ids = {task["task_id"] for task in listed.json()["tasks"]}
    assert task_ids == {"task-alice"}
    assert listed.json()["total"] == 1
    assert public_detail.status_code == 404
    assert bob_detail.status_code == 404
    assert overview.status_code == 200
    assert overview.json()["total"] == 1
    assert overview.json()["filters"]["owner_id"] == "alice"
    assert {task["task_id"] for task in admin_listed.json()["tasks"]} == {"task-public"}
    assert admin_public_detail.status_code == 200


def test_task_runs_router_records_approval_rejection(tmp_path):
    supervisor = TaskSupervisor.from_path(tmp_path / "task_runs.json", holder_id="worker-a")
    supervisor.start_task(
        task_id="task-reject",
        kind="loop",
        metadata={"approval_required": True, "approval_tool_name": "exec_shell"},
    )
    supervisor.transition(
        "task-reject",
        TaskRunStatus.WAITING_APPROVAL,
        reason="approval required",
    )

    app = FastAPI()
    app.include_router(create_task_runs_router(supervisor=supervisor))
    client = TestClient(app)

    rejected = client.post(
        "/api/task-runs/task-reject/approval-decision",
        json={"approved": False, "reason": "too risky"},
    )

    assert rejected.status_code == 200
    body = rejected.json()
    assert body["task_run"]["status"] == "paused"
    assert body["task_run"]["metadata"]["approval_decision"] == "rejected"
    assert body["task_run"]["metadata"]["approval_denied"] is True
    assert body["lease_health"]["recommended_action"] == "resume_paused_task"


def test_task_runs_router_rejects_non_approvable_denials(tmp_path):
    supervisor = TaskSupervisor.from_path(tmp_path / "task_runs.json", holder_id="worker-a")
    supervisor.start_task(
        task_id="task-policy-denied",
        kind="loop",
        metadata={
            "approval_required": False,
            "approval_denied": True,
            "approval_action": "deny",
        },
    )
    supervisor.transition(
        "task-policy-denied",
        TaskRunStatus.WAITING_APPROVAL,
        reason="approval policy denied",
    )
    supervisor.start_task(
        task_id="task-capability-denied",
        kind="loop",
        metadata={
            "approval_required": False,
            "approval_denied": True,
            "approval_action": "capability_denied",
            "capability_denied": True,
        },
    )
    supervisor.transition(
        "task-capability-denied",
        TaskRunStatus.WAITING_APPROVAL,
        reason="task capability group disabled: shell",
    )

    app = FastAPI()
    app.include_router(create_task_runs_router(supervisor=supervisor))
    client = TestClient(app)

    policy_approved = client.post(
        "/api/task-runs/task-policy-denied/approval-decision",
        json={"approved": True, "reason": "override"},
    )
    policy_rejected = client.post(
        "/api/task-runs/task-policy-denied/approval-decision",
        json={"approved": False, "reason": "ack"},
    )
    capability_approved = client.post(
        "/api/task-runs/task-capability-denied/approval-decision",
        json={"approved": True, "reason": "override"},
    )
    capability_rejected = client.post(
        "/api/task-runs/task-capability-denied/approval-decision",
        json={"approved": False, "reason": "ack"},
    )
    detail = client.get("/api/task-runs/task-capability-denied")

    assert policy_approved.status_code == 409
    assert policy_approved.json()["detail"] == "task is blocked by approval policy"
    assert policy_rejected.status_code == 409
    assert policy_rejected.json()["detail"] == "task is blocked by approval policy"
    assert capability_approved.status_code == 409
    assert capability_approved.json()["detail"] == "task is blocked by disabled capability"
    assert capability_rejected.status_code == 409
    assert capability_rejected.json()["detail"] == "task is blocked by disabled capability"
    assert detail.status_code == 200
    assert detail.json()["lease_health"]["recommended_action"] == "capability_policy_denied"


def test_task_runs_router_maps_expired_approval_lease_to_conflict(tmp_path):
    path = tmp_path / "task_runs.json"
    worker = TaskSupervisor.from_path(path, holder_id="worker-a", lease_ttl_seconds=30)
    worker.start_task(
        task_id="task-approval-expired",
        kind="loop",
        metadata={
            "approval_required": True,
            "approval_tool_name": "exec_shell",
            "approval_action": "confirm",
        },
    )
    worker.transition(
        "task-approval-expired",
        TaskRunStatus.WAITING_APPROVAL,
        reason="approval required",
    )

    def _expire(record):
        assert record.lease is not None
        return record.model_copy(
            update={"lease": record.lease.model_copy(update={"expires_at": time.time() - 1})},
            deep=True,
        )

    worker.store.mutate("task-approval-expired", _expire)
    app = FastAPI()
    app.include_router(create_task_runs_router(supervisor=worker))
    client = TestClient(app)

    approved = client.post(
        "/api/task-runs/task-approval-expired/approval-decision",
        json={"approved": True, "reason": "ship"},
    )
    detail = client.get("/api/task-runs/task-approval-expired")

    assert approved.status_code == 409
    assert "lease is no longer current" in approved.json()["detail"]
    assert detail.status_code == 200
    assert detail.json()["lease_health"]["recommended_action"] == "takeover_for_approval"


def test_task_runs_router_takes_over_expired_task_with_owner_isolation(tmp_path):
    path = tmp_path / "task_runs.json"
    worker_a = TaskSupervisor.from_path(path, holder_id="worker-a", lease_ttl_seconds=30)
    operator = TaskSupervisor.from_path(path, holder_id="operator", lease_ttl_seconds=30)
    worker_a.start_task(
        task_id="task-expired",
        kind="loop",
        owner_id="alice",
        metadata={"attempt_count": 1},
    )

    def _expire(record):
        assert record.lease is not None
        return record.model_copy(
            update={
                "lease": record.lease.model_copy(update={"expires_at": time.time() - 1}),
            },
            deep=True,
        )

    worker_a.store.mutate("task-expired", _expire)
    identity_store = IdentityStore()
    identity_store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    identity_store.add(Identity(actor_id="bob"), api_key_plaintext="sk-bob")

    app = FastAPI()
    app.include_router(
        create_task_runs_router(
            supervisor=operator,
            identity_store=identity_store,
            require_auth=True,
        )
    )
    client = TestClient(app)

    denied = client.post(
        "/api/task-runs/task-expired/takeover",
        json={"reason": "lease expired"},
        headers={"Authorization": "Bearer sk-bob"},
    )
    taken = client.post(
        "/api/task-runs/task-expired/takeover",
        json={"reason": "lease expired"},
        headers={"Authorization": "Bearer sk-alice"},
    )

    assert denied.status_code == 404
    assert taken.status_code == 200
    body = taken.json()
    assert body["schema"] == "echo.task_run_takeover.v1"
    assert body["task_run"]["status"] == "running"
    assert body["task_run"]["lease"]["holder_id"] == "operator"
    assert body["task_run"]["metadata"]["takeover_by"] == "alice"
    assert body["task_run"]["metadata"]["takeover_reason"] == "lease expired"
    assert body["task_run"]["metadata"]["attempt_count"] == 1
    assert body["lease_health"]["state"] == "ok"
    assert body["lease_health"]["recommended_action"] == "monitor"


def test_task_runs_router_rejects_takeover_of_live_lease(tmp_path):
    path = tmp_path / "task_runs.json"
    worker_a = TaskSupervisor.from_path(path, holder_id="worker-a", lease_ttl_seconds=30)
    operator = TaskSupervisor.from_path(path, holder_id="operator", lease_ttl_seconds=30)
    worker_a.start_task(task_id="task-live", kind="loop")

    app = FastAPI()
    app.include_router(create_task_runs_router(supervisor=operator))
    client = TestClient(app)

    response = client.post(
        "/api/task-runs/task-live/takeover",
        json={"reason": "try takeover"},
    )

    assert response.status_code == 409
    assert "already leased by" in response.json()["detail"]


def test_task_runs_router_surfaces_restart_audit_and_recovery_health(tmp_path):
    supervisor = TaskSupervisor.from_path(tmp_path / "task_runs.json", holder_id="worker-a")
    supervisor.start_task(task_id="task-retry", kind="loop", owner_id="alice")
    supervisor.transition(
        "task-retry",
        TaskRunStatus.FAILED,
        reason="verifier failed",
        checkpoint_id="ckpt-failed",
    )
    supervisor.start_task(task_id="task-retry", kind="loop")

    app = FastAPI()
    app.include_router(create_task_runs_router(supervisor=supervisor))
    client = TestClient(app)

    listed = client.get("/api/task-runs", params={"owner_id": "alice"})
    detail = client.get("/api/task-runs/task-retry")

    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["task_run"]["metadata"]["restart"] is True
    assert item["task_run"]["metadata"]["restart_from_checkpoint_id"] == "ckpt-failed"
    assert item["lease_health"]["state"] == "ok"
    assert item["lease_health"]["recommended_action"] == "monitor"

    assert detail.status_code == 200
    body = detail.json()
    assert body["task_run"]["metadata"]["restart_events"][-1]["previous_status"] == "failed"
    assert body["lease_health"]["state"] == "ok"


class _FakeRecoveryGateway:
    def __init__(self, *, error: Exception | None = None) -> None:
        self._runtime = object()
        self._connections: set[object] = set()
        self.error = error
        self.calls: list[tuple[dict, object]] = []
        self.unwatched: list[str] = []

    async def _invoke_turn_start(self, params, connection):
        self.calls.append((params, connection))
        connection.watched_threads.add(params["threadId"])
        if self.error is not None:
            raise self.error
        turn = {"id": "trn_recovery_1", "status": "inProgress"}
        await connection.notify(
            "turn/started",
            {"threadId": params["threadId"], "turn": turn, "eventId": "evt-1"},
        )
        return {"turn": {**turn, "status": "completed"}}

    def _unwatch_thread(self, thread_id: str) -> None:
        self.unwatched.append(thread_id)


def _install_resume_checkpoint(monkeypatch, value):
    from runtime.sensing.gateway import _realtime_turn_lifecycle_resume as resume_module

    monkeypatch.setattr(
        resume_module,
        "_resume_checkpoint_metadata",
        lambda _runtime, _task_id: value,
    )


def test_task_runs_router_takeover_then_starts_real_checkpoint_resume(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "task_runs.json"
    old_worker = TaskSupervisor.from_path(path, holder_id="old-worker", lease_ttl_seconds=30)
    operator = TaskSupervisor.from_path(path, holder_id="operator", lease_ttl_seconds=30)
    task_id = str(uuid4())
    thread_id = "thread-recovery"
    old_worker.start_task(
        task_id=task_id,
        kind="realtime_objective",
        thread_id=thread_id,
        metadata={"agent_id": "default"},
    )

    def _expire(record):
        assert record.lease is not None
        return record.model_copy(
            update={
                "latest_checkpoint_id": 41,
                "lease": record.lease.model_copy(update={"expires_at": time.time() - 1}),
            },
            deep=True,
        )

    old_worker.store.mutate(task_id, _expire)
    _install_resume_checkpoint(
        monkeypatch,
        {"checkpoint_id": 41, "iteration": 7, "phase": "verify", "working_set": []},
    )
    pause_controller = PauseController(store_path=None, autoload=False)
    gateway = _FakeRecoveryGateway()
    app = FastAPI()
    app.state.realtime_gateway = gateway
    app.state.pause_controller = pause_controller
    app.include_router(create_task_runs_router(supervisor=operator))
    client = TestClient(app)

    takeover_required = client.post(
        f"/api/task-runs/{task_id}/resume-execution",
        json={"requestId": "recovery-request-1"},
    )
    takeover = client.post(
        f"/api/task-runs/{task_id}/takeover",
        json={"reason": "old worker disappeared"},
    )
    resumed = client.post(
        f"/api/task-runs/{task_id}/resume-execution",
        json={"requestId": "recovery-request-1", "reason": "continue from checkpoint"},
    )

    assert takeover_required.status_code == 409
    assert takeover_required.json()["detail"] == (
        "task lease must be taken over before execution can resume"
    )
    assert takeover.status_code == 200
    assert resumed.status_code == 202
    body = resumed.json()
    assert body["schema"] == "echo.task_run_resume_execution.v1"
    assert body["accepted"] is True
    assert body["state"] == "turn_started"
    assert body["replayed"] is False
    assert body["task_id"] == task_id
    assert body["thread_id"] == thread_id
    assert body["turn_id"] == "trn_recovery_1"
    assert body["checkpoint"]["checkpoint_id"] == 41
    assert body["request_id"] == "recovery-request-1"
    assert body["user_item_id"].startswith("itm_resume_")
    # The trigger remains paused until the real ReAct loop emits
    # react_started and reclaims this exact objective id.
    assert body["task_run"]["status"] == "paused"
    assert body["task_run"]["metadata"]["resume_execution_state"] == "turn_started"
    assert pause_controller.consume_pending_resume(thread_id) == task_id
    assert pause_controller.consume_grant(task_id)["extra_iterations"] == 15

    assert len(gateway.calls) == 1
    params, connection = gateway.calls[0]
    assert params["threadId"] == thread_id
    assert params["input"] == [{"type": "text", "text": "继续"}]
    assert params["approvalPolicy"] == "on-request"
    assert params["userItemId"] == body["user_item_id"]
    assert connection._closed is True
    assert gateway.unwatched == [thread_id]


def test_task_runs_router_resume_requires_actual_runtime_checkpoint(tmp_path, monkeypatch):
    supervisor = TaskSupervisor.from_path(tmp_path / "task_runs.json", holder_id="operator")
    task_id = str(uuid4())
    supervisor.start_task(task_id=task_id, thread_id="thread-no-checkpoint")
    supervisor.transition(task_id, TaskRunStatus.PAUSED, checkpoint_id="projection-only")
    _install_resume_checkpoint(monkeypatch, None)
    gateway = _FakeRecoveryGateway()
    app = FastAPI()
    app.state.realtime_gateway = gateway
    app.state.pause_controller = PauseController(store_path=None, autoload=False)
    app.include_router(create_task_runs_router(supervisor=supervisor))
    client = TestClient(app)

    response = client.post(f"/api/task-runs/{task_id}/resume-execution", json={})

    assert response.status_code == 409
    assert response.json()["detail"] == "no durable ReAct checkpoint is available for this task"
    assert gateway.calls == []


def test_task_runs_router_resume_never_bypasses_waiting_approval(tmp_path, monkeypatch):
    supervisor = TaskSupervisor.from_path(tmp_path / "task_runs.json", holder_id="operator")
    task_id = str(uuid4())
    supervisor.start_task(
        task_id=task_id,
        thread_id="thread-approval",
        metadata={"approval_required": True},
    )
    supervisor.transition(
        task_id,
        TaskRunStatus.WAITING_APPROVAL,
        checkpoint_id=9,
    )
    _install_resume_checkpoint(
        monkeypatch,
        {"checkpoint_id": 9, "iteration": 2, "phase": "execute", "working_set": []},
    )
    gateway = _FakeRecoveryGateway()
    app = FastAPI()
    app.state.realtime_gateway = gateway
    app.state.pause_controller = PauseController(store_path=None, autoload=False)
    app.include_router(create_task_runs_router(supervisor=supervisor))
    client = TestClient(app)

    response = client.post(f"/api/task-runs/{task_id}/resume-execution", json={})

    assert response.status_code == 409
    assert response.json()["detail"] == "task is waiting for its original approval decision"
    assert gateway.calls == []


def test_task_runs_router_resume_maps_realtime_claim_conflict_and_stays_paused(
    tmp_path,
    monkeypatch,
):
    supervisor = TaskSupervisor.from_path(tmp_path / "task_runs.json", holder_id="operator")
    task_id = str(uuid4())
    supervisor.start_task(task_id=task_id, thread_id="thread-busy")
    supervisor.transition(task_id, TaskRunStatus.PAUSED, checkpoint_id=12)
    _install_resume_checkpoint(
        monkeypatch,
        {"checkpoint_id": 12, "iteration": 3, "phase": "execute", "working_set": []},
    )
    gateway = _FakeRecoveryGateway(
        error=_RpcError(JsonRpcErrorCode.SERVER_BUSY, "thread already has an active turn")
    )
    app = FastAPI()
    app.state.realtime_gateway = gateway
    app.state.pause_controller = PauseController(store_path=None, autoload=False)
    app.include_router(create_task_runs_router(supervisor=supervisor))
    client = TestClient(app)

    response = client.post(f"/api/task-runs/{task_id}/resume-execution", json={})
    detail = client.get(f"/api/task-runs/{task_id}")

    assert response.status_code == 503
    assert response.json()["detail"] == "Agent could not schedule the recovery turn"
    assert detail.status_code == 200
    assert detail.json()["task_run"]["status"] == "paused"
    assert detail.json()["task_run"]["metadata"]["resume_execution_state"] == "schedule_failed"
    assert (
        "thread already has an active turn"
        in detail.json()["task_run"]["metadata"]["resume_execution_error"]
    )


def test_task_runs_router_terminal_recovery_is_marked_and_deduplicated(
    tmp_path,
    monkeypatch,
):
    supervisor = TaskSupervisor.from_path(tmp_path / "task_runs.json", holder_id="operator")
    task_id = str(uuid4())
    supervisor.start_task(task_id=task_id, thread_id="thread-terminal-recovery")
    supervisor.transition(
        task_id,
        TaskRunStatus.FAILED,
        reason="worker crashed",
        checkpoint_id=21,
    )
    _install_resume_checkpoint(
        monkeypatch,
        {"checkpoint_id": 21, "iteration": 5, "phase": "execute", "working_set": []},
    )
    gateway = _FakeRecoveryGateway()
    app = FastAPI()
    app.state.realtime_gateway = gateway
    app.state.pause_controller = PauseController(store_path=None, autoload=False)
    app.include_router(create_task_runs_router(supervisor=supervisor))
    client = TestClient(app)

    first = client.post(
        f"/api/task-runs/{task_id}/resume-execution",
        json={"requestId": "terminal-recovery-1"},
    )
    duplicate = client.post(
        f"/api/task-runs/{task_id}/resume-execution",
        json={"requestId": "terminal-recovery-1"},
    )

    assert first.status_code == 202
    assert first.json()["task_run"]["status"] == "failed"
    assert first.json()["task_run"]["metadata"]["resume_execution_state"] == "turn_started"
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "task recovery is already queued or running"
    assert len(gateway.calls) == 1

