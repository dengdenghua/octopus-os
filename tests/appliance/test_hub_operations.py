from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from appliance.app_registry.docker_client import DockerConflict
from appliance.hub.operations import (
    HUB_OPERATIONS_FILENAME,
    HubOperationConflict,
    HubOperationCredentialsUnavailable,
    HubOperationService,
    HubOperationStore,
)
from appliance.hub.progress import validate_hub_progress


class _Executor:
    def __init__(self, *, conflict: bool = False) -> None:
        self.conflict = conflict
        self.calls: list[tuple[str, str, str]] = []

    def install_hub_app(self, app_id: str, *, plan_id: str, catalog_digest: str) -> dict:
        self.calls.append((app_id, plan_id, catalog_digest))
        if self.conflict:
            raise DockerConflict("private runtime detail")
        return {
            "schema": "echo.hub.install-result.v1",
            "appId": app_id,
            "planId": plan_id,
            "catalogDigest": catalog_digest,
            "containerId": "f" * 12,
            "state": "running",
            "image": "fixture",
            "revealedSecrets": {"admin-password": "do-not-store-in-plaintext"},
        }

    update_hub_app = install_hub_app
    uninstall_hub_app = install_hub_app
    start_hub_app = install_hub_app
    stop_hub_app = install_hub_app
    restart_hub_app = install_hub_app


class _StreamingExecutor(_Executor):
    def __init__(self) -> None:
        super().__init__()
        self.progress_written = threading.Event()
        self.release = threading.Event()

    def install_hub_app_with_progress(
        self,
        app_id: str,
        *,
        plan_id: str,
        catalog_digest: str,
        progress,
    ) -> dict:
        progress(
            {
                "schema": "echo.hub.progress.v1",
                "stage": "pulling",
                "step": "pulling-image",
                "completed": 4,
                "total": 11,
                "unit": "layers",
                "item": 2,
                "items": 3,
            }
        )
        self.progress_written.set()
        assert self.release.wait(timeout=2)
        return super().install_hub_app(
            app_id,
            plan_id=plan_id,
            catalog_digest=catalog_digest,
        )


def _wait_for_final(service: HubOperationService, operation_id: str) -> dict:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        operation = service.get(operation_id)
        if operation["status"] not in {"queued", "running"}:
            return operation
        time.sleep(0.01)
    raise AssertionError("Hub operation did not finish")


def test_store_encrypts_results_and_survives_restart(tmp_path: Path) -> None:
    store = HubOperationStore(tmp_path, encryption_secret="test-secret")
    created = store.create(
        action="install",
        app_id="demo-app",
        plan_id="a" * 64,
        catalog_digest="b" * 64,
        actor="admin",
        intent_id="hub-install-demo",
    )
    claimed = store.claim(created["operationId"])
    assert claimed is not None
    store.succeed(
        created["operationId"],
        {"revealedSecrets": {"admin-password": "do-not-store-in-plaintext"}},
    )

    raw = (tmp_path / HUB_OPERATIONS_FILENAME).read_bytes()
    assert b"do-not-store-in-plaintext" not in raw
    operation = store.get(created["operationId"])
    assert operation["credentialsAvailable"] is True
    assert operation["result"] == {}
    claimed = store.claim_credentials(created["operationId"])
    assert claimed["credentials"] == {"admin-password": "do-not-store-in-plaintext"}
    with pytest.raises(HubOperationCredentialsUnavailable):
        store.claim_credentials(created["operationId"])

    reopened = HubOperationStore(tmp_path, encryption_secret="test-secret")
    assert reopened.get(created["operationId"])["status"] == "succeeded"
    assert reopened.get(created["operationId"])["credentialsAvailable"] is False


def test_store_rejects_parallel_app_work_and_interrupts_stale_rows(tmp_path: Path) -> None:
    store = HubOperationStore(tmp_path, encryption_secret="test-secret")
    created = store.create(
        action="install",
        app_id="demo-app",
        plan_id="a" * 64,
        catalog_digest="b" * 64,
        actor="admin",
        intent_id=None,
    )
    with pytest.raises(HubOperationConflict):
        store.create(
            action="update",
            app_id="demo-app",
            plan_id="c" * 64,
            catalog_digest="b" * 64,
            actor="admin",
            intent_id=None,
        )

    restarted = HubOperationStore(tmp_path, encryption_secret="test-secret")
    operation = restarted.get(created["operationId"])
    assert operation["status"] == "interrupted"
    assert operation["error"]["code"] == "RUNTIME_RESTARTED"


def test_worker_persists_success_and_sanitized_failure(tmp_path: Path) -> None:
    success_store = HubOperationStore(tmp_path / "success", encryption_secret="secret")
    executor = _Executor()
    success = HubOperationService(success_store, executor=executor, audit=None, workers=1)
    queued = success.submit(
        action="install",
        app_id="demo-app",
        plan_id="a" * 64,
        catalog_digest="b" * 64,
        actor="admin",
        intent_id=None,
    )
    completed = _wait_for_final(success, queued["operationId"])
    assert completed["status"] == "succeeded"
    assert completed["result"]["containerId"] == "f" * 12
    assert completed["credentialsAvailable"] is True
    success.shutdown()

    failure_store = HubOperationStore(tmp_path / "failure", encryption_secret="secret")
    failure = HubOperationService(
        failure_store, executor=_Executor(conflict=True), audit=None, workers=1
    )
    queued = failure.submit(
        action="update",
        app_id="demo-app",
        plan_id="c" * 64,
        catalog_digest="d" * 64,
        actor="admin",
        intent_id=None,
    )
    failed = _wait_for_final(failure, queued["operationId"])
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "STATE_CHANGED"
    assert "private runtime detail" not in str(failed)
    failure.shutdown()


def test_worker_persists_real_stream_progress_without_raw_docker_text(tmp_path: Path) -> None:
    store = HubOperationStore(tmp_path, encryption_secret="secret")
    executor = _StreamingExecutor()
    service = HubOperationService(store, executor=executor, audit=None, workers=1)
    queued = service.submit(
        action="install",
        app_id="demo-app",
        plan_id="a" * 64,
        catalog_digest="b" * 64,
        actor="admin",
        intent_id=None,
    )
    assert executor.progress_written.wait(timeout=2)

    running = service.get(queued["operationId"])
    assert running["status"] == "running"
    assert running["progress"] == {
        "schema": "echo.hub.progress.v1",
        "stage": "pulling",
        "step": "pulling-image",
        "completed": 4,
        "total": 11,
        "unit": "layers",
        "item": 2,
        "items": 3,
        "sequence": 2,
    }
    assert "image" not in running["progress"]

    executor.release.set()
    completed = _wait_for_final(service, queued["operationId"])
    assert completed["progress"]["stage"] == "completed"
    service.shutdown()


def test_store_file_is_private_and_schema_does_not_use_agent_tables(tmp_path: Path) -> None:
    store = HubOperationStore(tmp_path, encryption_secret="test-secret")
    assert store.path.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(store.path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert tables == {"hub_operations"}


def test_store_migrates_existing_lifecycle_rows_before_accepting_control_actions(
    tmp_path: Path,
) -> None:
    path = tmp_path / HUB_OPERATIONS_FILENAME
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE hub_operations (
                operation_id TEXT PRIMARY KEY,
                action TEXT NOT NULL CHECK (action IN ('install', 'update', 'uninstall')),
                app_id TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                catalog_digest TEXT NOT NULL,
                actor TEXT NOT NULL,
                intent_id TEXT,
                status TEXT NOT NULL CHECK (
                    status IN ('queued', 'running', 'succeeded', 'failed', 'interrupted')
                ),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                error_code TEXT,
                error_message TEXT,
                recovery_action TEXT,
                warning_code TEXT,
                warning_message TEXT,
                result_ciphertext BLOB,
                credentials_claimed_at TEXT,
                progress_stage TEXT NOT NULL DEFAULT 'queued',
                progress_step TEXT NOT NULL DEFAULT 'waiting',
                progress_completed INTEGER,
                progress_total INTEGER,
                progress_unit TEXT,
                progress_item INTEGER,
                progress_items INTEGER,
                progress_sequence INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO hub_operations (
                operation_id, action, app_id, plan_id, catalog_digest,
                actor, status, created_at, updated_at
            ) VALUES (
                '11111111111111111111111111111111', 'install', 'old-app',
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                'admin', 'succeeded', '2026-01-01T00:00:00.000Z',
                '2026-01-01T00:00:00.000Z'
            );
            """
        )

    store = HubOperationStore(tmp_path, encryption_secret="test-secret")
    assert store.get("1" * 32)["operation"] == "install"
    created = store.create(
        action="restart",
        app_id="demo-app",
        plan_id="c" * 64,
        catalog_digest="d" * 64,
        actor="admin",
        intent_id=None,
    )
    assert created["operation"] == "restart"


def test_progress_contract_rejects_raw_docker_identity_and_invalid_counts() -> None:
    safe = {
        "schema": "echo.hub.progress.v1",
        "stage": "pulling",
        "step": "pulling-image",
        "completed": 4,
        "total": 11,
        "unit": "layers",
        "item": 2,
        "items": 3,
    }
    assert validate_hub_progress(safe) == safe
    with pytest.raises(ValueError, match="fields"):
        validate_hub_progress({**safe, "image": "private/image@sha256:deadbeef"})
    with pytest.raises(ValueError, match="completion"):
        validate_hub_progress({**safe, "completed": 12})
