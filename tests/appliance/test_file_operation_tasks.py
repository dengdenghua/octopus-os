from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest

from appliance.agent_api.file_tasks import FileOperationTask
from appliance.agent_api.tasks import TaskLeaseConflict
from runtime.platform.process.task_supervisor import LostTaskLease, TaskSupervisor


@pytest.fixture
def record(tmp_path):
    return {
        "taskId": "organization-fixture",
        "owner": "local:alice",
        "workspacePath": str(tmp_path / "share"),
        "plan": {"planId": "a" * 64, "direction": "apply", "summary": {"ready": 2}},
    }


@pytest.fixture
def supervisor(tmp_path):
    return TaskSupervisor.from_path(tmp_path / "tasks.json", holder_id="fixture-worker")


def result(state="completed", **overrides):
    return {
        "state": state,
        "executionComplete": True,
        "counts": {
            "moved": 2,
            "conflicts": 0,
            "failed": 0,
            "pending": 0,
            "uncertain": 0,
            "skipped": 0,
        },
        **overrides,
    }


def test_real_supervisor_start_progress_cancel_and_finish_contract(supervisor, record):
    task = FileOperationTask(supervisor, record)
    task.start()
    initial = supervisor.store.get(task.task_id)
    assert initial.owner_id == record["owner"]
    assert initial.status == "running"
    assert initial.lease is not None
    task.progress(1)
    # Another request uses a fresh adapter and only requests cooperative stop.
    FileOperationTask(supervisor, record).request_cancel("local:alice")
    current = supervisor.store.get(task.task_id)
    assert current.status == "running"
    assert current.lease.token == initial.lease.token
    assert current.metadata["cancel_requested"] is True
    assert task.cancelled() is True
    task.finish(result("cancelled", executionComplete=False, counts={"moved": 1, "pending": 1}))
    current = supervisor.store.get(task.task_id)
    assert current.status == "cancelled"
    assert current.lease is None
    assert current.completed_at is not None
    assert current.metadata["completed_steps"] == 1
    # Persisted metadata is a task projection, not a copy of provider receipts.
    assert "counts" not in current.metadata
    assert "result" not in current.metadata


def test_duplicate_start_is_idempotent_for_same_attempt_and_does_not_clear_cancel(
    supervisor, record
):
    task = FileOperationTask(supervisor, record)
    task.start()
    token = supervisor.store.get(task.task_id).lease.token
    task.progress(1)
    task.request_cancel(record["owner"])
    task.start()
    current = supervisor.store.get(task.task_id)
    assert current.lease.token == token
    assert current.metadata["completed_steps"] == 1
    assert current.metadata["cancel_requested"] is True
    with pytest.raises(TaskLeaseConflict):
        FileOperationTask(supervisor, record).start()


def test_cancelled_terminal_retry_has_fresh_lease_and_old_adapter_cannot_mutate(supervisor, record):
    old = FileOperationTask(supervisor, record)
    old.start()
    old_token = supervisor.store.get(old.task_id).lease.token
    old.finish(result("cancelled", executionComplete=False, counts={"moved": 0, "pending": 2}))
    restarted = TaskSupervisor.from_path(supervisor.store.path, holder_id="restarted-worker")
    retry = FileOperationTask(restarted, record)
    retry.start()
    current = supervisor.store.get(old.task_id)
    assert current.status == "running"
    assert current.lease.token > old_token
    assert current.completed_at is None
    assert current.terminal_reason == ""
    assert current.metadata["restart_from_status"] == "cancelled"
    assert current.metadata["cancel_requested"] is False
    assert current.metadata["file_operation_state"] == "running"
    with pytest.raises(LostTaskLease):
        old.progress(2)
    assert old.cancelled() is True
    assert supervisor.store.get(old.task_id).metadata["completed_steps"] == 0
    retry.progress(2)
    retry.finish(result())
    assert restarted.store.get(retry.task_id).status == "completed"


def test_expired_attempt_retry_in_same_app_rejects_old_token_atomically(supervisor, record):
    old = FileOperationTask(supervisor, record)
    old.start()
    token = supervisor.store.get(old.task_id).lease.token
    supervisor.store.mutate(
        old.task_id,
        lambda current: current.model_copy(
            update={
                "lease": current.lease.model_copy(update={"expires_at": 1.0}),
            },
            deep=True,
        ),
    )
    retry = FileOperationTask(supervisor, record)
    retry.start()
    assert supervisor.store.get(old.task_id).lease.token > token
    # Verify the actual Supervisor transition mutator fences the old token,
    # even if the adapter's preliminary read had already passed.
    with pytest.raises(LostTaskLease, match="token changed"):
        old._attempt.transition(old.task_id, "completed", metadata_patch={"completed_steps": 99})
    with pytest.raises(LostTaskLease):
        old.finish(result())
    assert supervisor.store.get(old.task_id).status == "running"
    assert supervisor.store.get(old.task_id).metadata["completed_steps"] == 0


@pytest.mark.parametrize(
    "state,complete,expected",
    [
        ("completed", True, "completed"),
        ("completed", False, "failed"),
        ("partial", True, "failed"),
        ("uncertain", False, "failed"),
        ("failed", True, "failed"),
        ("cancelled", False, "cancelled"),
    ],
)
def test_finish_maps_result_state_instead_of_execution_complete_alone(
    supervisor, record, state, complete, expected
):
    task = FileOperationTask(supervisor, record)
    task.start()
    output = result(state, executionComplete=complete)
    if state == "partial":
        output["counts"].update(moved=1, conflicts=1)
    task.finish(output)
    current = supervisor.store.get(task.task_id)
    assert current.status == expected
    assert current.terminal_reason == state
    assert current.lease is None
    assert current.metadata["file_operation_state"] == state
    assert current.metadata["completed_steps"] == 2
    task.finish(copy.deepcopy(output))
    assert supervisor.store.get(task.task_id) == current


def test_successful_commit_wins_racing_cooperative_cancel(supervisor, record):
    task = FileOperationTask(supervisor, record)
    task.start()
    task.request_cancel(record["owner"])
    task.finish(result())
    assert supervisor.store.get(task.task_id).status == "completed"
    FileOperationTask(supervisor, record).request_cancel(record["owner"])
    assert supervisor.store.get(task.task_id).status == "completed"


@pytest.mark.parametrize(
    "change",
    [
        {"owner": "local:bob"},
        {"workspacePath": "another-synthetic-workspace"},
        {"plan": {"planId": "b" * 64, "direction": "apply", "summary": {"ready": 2}}},
    ],
)
def test_existing_task_binding_cannot_be_reassigned(supervisor, record, change):
    task = FileOperationTask(supervisor, record)
    task.start()
    before = supervisor.store.get(task.task_id)
    changed = {**record, **change}
    with pytest.raises(PermissionError):
        FileOperationTask(supervisor, changed).start()
    with pytest.raises(PermissionError):
        FileOperationTask(supervisor, changed).request_cancel(changed["owner"])
    assert supervisor.store.get(task.task_id) == before


def test_other_actor_cannot_cancel_and_unavailable_authority_never_succeeds(supervisor, record):
    task = FileOperationTask(supervisor, record)
    task.start()
    with pytest.raises(PermissionError):
        task.request_cancel("local:bob")
    assert task.cancelled() is False
    missing = FileOperationTask(None, record)
    with pytest.raises(RuntimeError, match="authority is unavailable"):
        missing.start()
    with pytest.raises(RuntimeError, match="authority is unavailable"):
        missing.request_cancel(record["owner"])
    assert missing.cancelled() is True


def test_malformed_progress_and_nonterminal_finish_do_not_change_task(supervisor, record):
    task = FileOperationTask(supervisor, record)
    task.start()
    task.progress(1)
    for value in (0, -1, 3, True, "2"):
        with pytest.raises(ValueError):
            task.progress(value)
    with pytest.raises(ValueError, match="not terminal"):
        task.finish(result("running"))
    with pytest.raises(ValueError, match="unresolved"):
        task.finish(result(counts={"moved": 1, "conflicts": 1}))
    assert supervisor.store.get(task.task_id).status == "running"
    assert supervisor.store.get(task.task_id).metadata["completed_steps"] == 1


def test_real_child_restart_reads_terminal_and_retries_shared_authority(supervisor, record):
    task = FileOperationTask(supervisor, record)
    task.start()
    before = supervisor.store.get(task.task_id).lease.token
    task.finish(result("cancelled", executionComplete=False))
    code = """
import json,sys
from runtime.platform.process.task_supervisor import TaskSupervisor
from appliance.agent_api.file_tasks import FileOperationTask
supervisor=TaskSupervisor.from_path(sys.argv[1],holder_id='child-restart')
task=FileOperationTask(supervisor,json.loads(sys.argv[2]))
assert supervisor.store.get(task.task_id).status=='cancelled'
task.start(); token=supervisor.store.get(task.task_id).lease.token
task.progress(2)
task.finish({'state':'completed','executionComplete':True,'counts':{'moved':2,'pending':0}})
print(json.dumps({'token':token,'status':str(supervisor.store.get(task.task_id).status)}))
"""
    child = subprocess.run(
        [sys.executable, "-c", code, str(supervisor.store.path), json.dumps(record)],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    evidence = json.loads(child.stdout)
    assert evidence["token"] > before
    assert evidence["status"] == "completed"
    assert supervisor.store.get(task.task_id).status == "completed"


def test_restart_cannot_steal_live_foreign_lease(supervisor, record):
    task = FileOperationTask(supervisor, record)
    task.start()
    before = supervisor.store.get(task.task_id)
    restarted = TaskSupervisor.from_path(supervisor.store.path, holder_id="other-process")
    with pytest.raises(TaskLeaseConflict):
        FileOperationTask(restarted, record).start()
    assert supervisor.store.get(task.task_id) == before


def test_real_authenticated_takeover_is_consumed_once_before_provider_retry(supervisor, record):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from appliance.task_projection import create_task_projection_router
    from runtime.safety.auth.identity import encode_jwt_hs256

    old = FileOperationTask(supervisor, record)
    old.start()
    before = supervisor.store.get(old.task_id).lease.token
    supervisor.store.mutate(
        old.task_id,
        lambda current: current.model_copy(
            update={
                "lease": current.lease.model_copy(update={"expires_at": 1}),
            },
            deep=True,
        ),
    )
    app = FastAPI()
    secret = "synthetic-organization-takeover-key"
    app.include_router(create_task_projection_router(supervisor=supervisor, jwt_secret=secret))
    client = TestClient(app)
    client.cookies.set(
        "echo_session",
        encode_jwt_hs256(
            {"sub": record["owner"], "exp": 9_999_999_999},
            secret=secret,
        ),
    )
    response = client.post(f"/api/appliance/tasks/{old.task_id}/takeover", json={"actor": "forged"})
    assert response.status_code == 200
    claimed = supervisor.store.get(old.task_id)
    assert claimed.lease.token > before
    assert claimed.metadata["file_operation_takeover_token"] == claimed.lease.token
    retry = FileOperationTask(supervisor, record)
    retry.start()
    current = supervisor.store.get(old.task_id)
    assert current.lease.token == claimed.lease.token
    assert current.metadata["file_operation_takeover_token"] is None
    with pytest.raises(TaskLeaseConflict):
        FileOperationTask(supervisor, record).start()
    with pytest.raises(LostTaskLease):
        old.progress(1)
    retry.progress(2)
    retry.finish(result())
    assert supervisor.store.get(old.task_id).status == "completed"
