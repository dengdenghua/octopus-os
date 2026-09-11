from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import nas_backup_routes

PLAN_ID = "a" * 64
RESTORE_SET_ID = "11111111-2222-4333-8444-555555555555"
RESTORE_SNAPSHOT_ID = "b" * 64
RESTORE_SOURCE_REF = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
RESTORE_TARGET_REF = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
RESTORE_CONFIRMATION = f"RESTORE ECHO NAS SET {RESTORE_SET_ID} SNAPSHOT {RESTORE_SNAPSHOT_ID}"


def _desired(enabled: bool = True) -> dict[str, Any]:
    return {
        "schema": "echo.nas-data-backup-schedule.v1",
        "enabled": enabled,
        "repository": "/mnt/off-device/echo-nas-data" if enabled else None,
        "repositoryMount": "/mnt/off-device" if enabled else None,
    }


def _plan(operation: str = "enable") -> dict[str, Any]:
    return {
        "schema": "echo.nas-data-backup-schedule-plan.v1",
        "planId": PLAN_ID,
        "operation": operation,
        "requiresApproval": operation != "none",
        "current": {
            "enabled": False,
            "repositoryConfigured": False,
            "timerEnabled": False,
        },
        "desired": {"enabled": True, "repositoryConfigured": True},
        "pathsRedacted": True,
    }


def _credential_desired() -> dict[str, Any]:
    return {
        "schema": "echo.nas-data-backup-credential-desired.v1",
        "mode": "initialize",
        "repository": "/mnt/off-device/echo-nas-data",
        "repositoryMount": "/mnt/off-device",
        "password": "correct-horse-battery",
    }


def _credential_plan() -> dict[str, Any]:
    return {
        "schema": "echo.nas-data-backup-credential-plan.v1",
        "planId": PLAN_ID,
        "operation": "initializeCredential",
        "requiresApproval": True,
        "desired": {
            "mode": "initialize",
            "repositoryConfigured": True,
            "passwordBound": True,
        },
        "pathsRedacted": True,
    }


def _rotation_desired() -> dict[str, Any]:
    return {
        "schema": "echo.nas-data-backup-credential-rotation-desired.v1",
        "repository": "/mnt/off-device/echo-nas-data",
        "repositoryMount": "/mnt/off-device",
        "currentPassword": "correct-horse-battery",
        "newPassword": "new-correct-horse-battery",
    }


def _rotation_plan() -> dict[str, Any]:
    return {
        "schema": "echo.nas-data-backup-credential-rotation-plan.v1",
        "planId": PLAN_ID,
        "operation": "rotateCredential",
        "requiresApproval": True,
        "desired": {
            "repositoryConfigured": True,
            "currentPasswordBound": True,
            "newPasswordBound": True,
        },
        "pathsRedacted": True,
    }


def _remote_desired() -> dict[str, Any]:
    return {
        "schema": "echo.nas-backup-remote-desired.v1",
        "operation": "create",
        "remoteId": "offsite",
        "label": "异地对象存储",
        "endpoint": "https://s3.example.test",
        "region": "us-east-1",
        "bucket": "echo-backups",
        "prefix": "family/nas",
        "accessKeyId": "ACCESS-KEY-123",
        "secretAccessKey": "private-secret-value",
    }


def _remote_plan() -> dict[str, Any]:
    return {
        "schema": "echo.nas-backup-remote-plan.v1",
        "planId": PLAN_ID,
        "operation": "create",
        "requiresApproval": True,
        "desired": {
            "operation": "create",
            "remoteId": "offsite",
            "kind": "s3",
            "label": "异地对象存储",
        },
        "mountpoint": "/mnt/echo-backup-remotes/offsite",
        "pathsRedacted": True,
        "secretsRedacted": True,
    }


def _restore_desired() -> dict[str, Any]:
    return {
        "schema": "echo.nas-data-backup-restore-desired.v2",
        "selector": RESTORE_SET_ID,
        "repository": "/mnt/off-device/repository",
        "repositoryMount": "/mnt/off-device",
        "targets": [
            {
                "sourceSharedFolderRef": RESTORE_SOURCE_REF,
                "targetSharedFolderRef": RESTORE_TARGET_REF,
            }
        ],
    }


def _restore_plan() -> dict[str, Any]:
    return {
        "schema": "echo.nas-data-backup-restore-plan.v1",
        "planId": PLAN_ID,
        "operation": "restoreBackupSet",
        "requiresApproval": True,
        "repositoryId": "c" * 64,
        "snapshotId": RESTORE_SNAPSHOT_ID,
        "setId": RESTORE_SET_ID,
        "manifestSha256": "d" * 64,
        "memberCount": 1,
        "members": [
            {
                "sharedFolderRef": RESTORE_SOURCE_REF,
                "sourceSharedFolderRef": RESTORE_SOURCE_REF,
                "targetSharedFolderRef": RESTORE_TARGET_REF,
                "remapped": True,
            }
        ],
        "confirmation": RESTORE_CONFIRMATION,
        "recoveryPending": False,
        "pathsRedacted": True,
        "safety": {},
    }


class Approval:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def consume(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class Audit:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def record(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _client(*, approval: Any | None = None, audit: Any | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(nas_backup_routes.create_nas_backup_router(approval=approval, audit=audit))
    return TestClient(app)


def test_status_returns_only_redacted_management_state(monkeypatch) -> None:
    monkeypatch.setattr(
        nas_backup_routes.credential_policy,
        "rotation_recovery_pending",
        lambda: False,
    )
    monkeypatch.setattr(
        nas_backup_routes.policy,
        "policy_status",
        lambda: {
            "schemaVersion": 1,
            "configured": True,
            "enabled": True,
            "repositoryConfigured": True,
            "credentialConfigured": True,
            "schedulerInstalled": True,
            "timerEnabled": True,
            "history": [],
            "pathsRedacted": True,
            "source": "native",
        },
    )
    response = _client().get("/api/appliance/storage/backups/schedule")
    assert response.status_code == 200
    assert response.json()["pathsRedacted"] is True
    assert response.json()["credentialRotationRecoveryPending"] is False
    assert "/mnt/" not in response.text


def test_repository_candidates_are_operator_only_and_source_redacted(monkeypatch) -> None:
    monkeypatch.setattr(
        nas_backup_routes.external_storage,
        "list_external_storage_mounts",
        lambda **_kwargs: {
            "schema": "echo.external-storage-candidates.v1",
            "candidates": [
                {
                    "mountpoint": "/mnt/off-device",
                    "filesystem": "fuse.rclone",
                    "kind": "remote",
                    "totalBytes": 1_000,
                    "freeBytes": 750,
                    "writable": True,
                }
            ],
            "truncated": False,
            "sourcesRedacted": True,
        },
    )

    response = _client().get("/api/appliance/storage/backups/repository-candidates")

    assert response.status_code == 200
    assert response.json()["candidates"][0]["kind"] == "remote"
    assert response.json()["sourcesRedacted"] is True
    assert "secret-remote" not in response.text


def test_repository_candidate_discovery_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        nas_backup_routes.external_storage,
        "list_external_storage_mounts",
        lambda **_kwargs: (_ for _ in ()).throw(
            nas_backup_routes.external_storage.ExternalStorageError("/mnt/secret-remote")
        ),
    )

    response = _client().get("/api/appliance/storage/backups/repository-candidates")

    assert response.status_code == 503
    assert "/mnt/secret-remote" not in response.text


def test_remote_list_returns_only_redacted_status(monkeypatch) -> None:
    monkeypatch.setattr(
        nas_backup_routes.remote_policy,
        "list_remotes",
        lambda: {
            "schema": "echo.nas-backup-remote-status.v1",
            "remotes": [
                {"id": "offsite", "label": "异地对象存储", "kind": "s3", "mounted": True}
            ],
            "count": 1,
            "pathsRedacted": True,
            "secretsRedacted": True,
        },
    )

    response = _client().get("/api/appliance/storage/backups/remotes")

    assert response.status_code == 200
    assert response.json()["remotes"][0]["mounted"] is True
    assert "s3.example.test" not in response.text
    assert "ACCESS-KEY" not in response.text


def test_remote_plan_passes_secrets_to_policy_but_never_returns_them(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def plan(desired: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        captured.update(desired)
        return _remote_plan()

    monkeypatch.setattr(nas_backup_routes.remote_policy, "plan_remote", plan)

    response = _client().post(
        "/api/appliance/storage/backups/remotes/plan",
        json=_remote_desired(),
    )

    assert response.status_code == 200
    assert captured["secretAccessKey"] == "private-secret-value"
    assert "private-secret-value" not in response.text
    assert "s3.example.test" not in response.text


def test_remote_apply_consumes_approval_and_audits_only_redacted_metadata(
    monkeypatch,
) -> None:
    approval = Approval()
    audit = Audit()
    applied: dict[str, Any] = {}
    monkeypatch.setattr(
        nas_backup_routes.remote_policy,
        "plan_remote",
        lambda *_args, **_kwargs: _remote_plan(),
    )

    def apply(desired: dict[str, Any], plan_id: str, **_kwargs: Any) -> dict[str, Any]:
        applied.update(desired)
        assert plan_id == PLAN_ID
        return {**_remote_plan(), "applied": True, "verified": True, "mounted": True}

    monkeypatch.setattr(nas_backup_routes.remote_policy, "apply_remote", apply)

    response = _client(approval=approval, audit=audit).post(
        "/api/appliance/storage/backups/remotes/apply",
        json={"desired": _remote_desired(), "planId": PLAN_ID},
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert applied["secretAccessKey"] == "private-secret-value"
    assert approval.calls[0]["action"] == nas_backup_routes.REMOTE_ACTION
    assert [call["outcome"] for call in audit.calls] == ["attempted", "succeeded"]
    assert all(call["metadata"]["secretRedacted"] is True for call in audit.calls)
    audit_text = json.dumps(audit.calls, ensure_ascii=False)
    assert "private-secret-value" not in audit_text
    assert "s3.example.test" not in audit_text


def test_remote_apply_rejects_stale_plan_before_approval(monkeypatch) -> None:
    approval = Approval()
    monkeypatch.setattr(
        nas_backup_routes.remote_policy,
        "plan_remote",
        lambda *_args, **_kwargs: _remote_plan(),
    )

    response = _client(approval=approval).post(
        "/api/appliance/storage/backups/remotes/apply",
        json={"desired": _remote_desired(), "planId": "b" * 64},
    )

    assert response.status_code == 409
    assert approval.calls == []


def test_status_reports_pending_rotation_recovery_without_exposing_paths(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        nas_backup_routes.credential_policy,
        "rotation_recovery_pending",
        lambda: True,
    )
    monkeypatch.setattr(
        nas_backup_routes.credential_policy,
        "recover_rotation",
        lambda: (_ for _ in ()).throw(OSError("/mnt/private unavailable")),
    )
    monkeypatch.setattr(
        nas_backup_routes.policy,
        "policy_status",
        lambda: {
            "schemaVersion": 1,
            "configured": True,
            "enabled": False,
            "repositoryConfigured": True,
            "credentialConfigured": True,
            "schedulerInstalled": True,
            "timerEnabled": False,
            "history": [],
            "pathsRedacted": True,
            "source": "native",
        },
    )

    response = _client().get("/api/appliance/storage/backups/schedule")

    assert response.status_code == 200
    assert response.json()["credentialRotationRecoveryPending"] is True
    assert "/mnt/private" not in response.text


def test_status_audits_successful_automatic_rotation_recovery(monkeypatch) -> None:
    audit = Audit()
    monkeypatch.setattr(
        nas_backup_routes.credential_policy,
        "rotation_recovery_pending",
        lambda: True,
    )
    monkeypatch.setattr(
        nas_backup_routes.credential_policy,
        "recover_rotation",
        lambda: {
            "schema": "echo.nas-data-backup-credential-rotation-receipt.v1",
            "planId": PLAN_ID,
            "recovered": True,
            "direction": "forward",
            "removedKeyCount": 1,
            "pathsRedacted": True,
        },
    )
    monkeypatch.setattr(
        nas_backup_routes.policy,
        "policy_status",
        lambda: {
            "schemaVersion": 1,
            "configured": True,
            "enabled": False,
            "repositoryConfigured": True,
            "credentialConfigured": True,
            "schedulerInstalled": True,
            "timerEnabled": False,
            "history": [],
            "pathsRedacted": True,
            "source": "native",
        },
    )

    response = _client(audit=audit).get("/api/appliance/storage/backups/schedule")

    assert response.status_code == 200
    assert response.json()["credentialRotationRecoveryPending"] is False
    assert audit.calls == [
        {
            "actor": "local:development",
            "action": nas_backup_routes.CREDENTIAL_ROTATION_ACTION,
            "target": PLAN_ID,
            "outcome": "recovered",
            "metadata": {
                "direction": "forward",
                "removedKeyCount": 1,
                "pathsRedacted": True,
                "secretRedacted": True,
                "source": "native",
            },
        }
    ]


def test_plan_rejects_coerced_boolean() -> None:
    response = _client().post(
        "/api/appliance/storage/backups/schedule/plan",
        json={**_desired(), "enabled": "true"},
    )
    assert response.status_code == 422


def test_restore_list_and_plan_never_expose_repository_paths(monkeypatch) -> None:
    monkeypatch.setattr(
        nas_backup_routes.restore_policy,
        "list_restore_sets",
        lambda _repository, **_kwargs: {
            "schema": "echo.nas-data-backup-restore-set-list.v1",
            "repositoryId": "c" * 64,
            "setCount": 1,
            "sets": [
                {
                    "setId": RESTORE_SET_ID,
                    "snapshotId": RESTORE_SNAPSHOT_ID,
                    "createdAt": "2026-09-08T00:00:00Z",
                    "manifestSha256": "d" * 64,
                    "memberCount": 1,
                    "members": [],
                }
            ],
            "truncated": False,
            "encrypted": True,
            "pathsRedacted": True,
            "verification": "authenticated_index_only",
            "restoreMode": "explicit-empty-managed-btrfs-target-mapping",
        },
    )
    monkeypatch.setattr(
        nas_backup_routes.restore_policy, "plan_restore", lambda _desired: _restore_plan()
    )

    listing = _client().post(
        "/api/appliance/storage/backups/restore/sets",
        json={
            "schema": "echo.nas-data-backup-restore-repository.v1",
            "repository": "/mnt/off-device/repository",
            "repositoryMount": "/mnt/off-device",
        },
    )
    plan = _client().post("/api/appliance/storage/backups/restore/plan", json=_restore_desired())

    assert listing.status_code == 200 and listing.json()["pathsRedacted"] is True
    assert plan.status_code == 200 and plan.json()["pathsRedacted"] is True
    assert "/mnt/" not in listing.text + plan.text


def test_restore_target_inventory_is_path_redacted(monkeypatch) -> None:
    monkeypatch.setattr(
        nas_backup_routes.restore_policy,
        "list_restore_targets",
        lambda: {
            "schema": "echo.nas-data-backup-restore-target-list.v1",
            "targetCount": 1,
            "targets": [
                {
                    "sharedFolderRef": RESTORE_TARGET_REF,
                    "name": "restored-photos",
                    "filesystemUuid": "99999999-9999-4999-8999-999999999999",
                    "empty": True,
                }
            ],
            "pathsRedacted": True,
        },
    )

    response = _client().get("/api/appliance/storage/backups/restore/targets")

    assert response.status_code == 200
    assert response.json()["targets"][0]["sharedFolderRef"] == RESTORE_TARGET_REF
    assert "/data/" not in response.text


def test_restore_apply_is_exactly_approved_and_audited(monkeypatch) -> None:
    approval = Approval()
    audit = Audit()
    monkeypatch.setattr(
        nas_backup_routes.restore_policy, "plan_restore", lambda _desired: _restore_plan()
    )
    monkeypatch.setattr(
        nas_backup_routes.restore_policy,
        "apply_restore",
        lambda _desired, plan_id, _confirmation: {
            "schema": "echo.nas-data-backup-restore-result.v1",
            "planId": plan_id,
            "setId": RESTORE_SET_ID,
            "snapshotId": RESTORE_SNAPSHOT_ID,
            "memberCount": 1,
            "members": [],
            "fullReadVerified": True,
            "contentVerified": True,
            "verified": True,
            "pathsRedacted": True,
        },
    )

    response = _client(approval=approval, audit=audit).post(
        "/api/appliance/storage/backups/restore/apply",
        json={
            "desired": _restore_desired(),
            "planId": PLAN_ID,
            "confirmation": RESTORE_CONFIRMATION,
        },
        headers={
            "X-Echo-Approval": "approval-token",
            "X-Echo-Intent": "task.backup.restore",
        },
    )

    assert response.status_code == 200 and response.json()["verified"] is True
    assert approval.calls[0]["action"] == nas_backup_routes.RESTORE_ACTION
    assert approval.calls[0]["target"] == PLAN_ID
    assert [item["outcome"] for item in audit.calls] == ["attempted", "succeeded"]
    assert all(item["action"] == nas_backup_routes.RESTORE_ACTION for item in audit.calls)
    assert all(item["metadata"]["intentId"] == "task.backup.restore" for item in audit.calls)
    assert "/mnt/" not in str(audit.calls)


def test_restore_stale_plan_and_wrong_confirmation_do_not_consume_approval(
    monkeypatch,
) -> None:
    approval = Approval()
    monkeypatch.setattr(
        nas_backup_routes.restore_policy, "plan_restore", lambda _desired: _restore_plan()
    )
    client = _client(approval=approval)

    stale = client.post(
        "/api/appliance/storage/backups/restore/apply",
        json={
            "desired": _restore_desired(),
            "planId": "f" * 64,
            "confirmation": RESTORE_CONFIRMATION,
        },
    )
    wrong_confirmation = client.post(
        "/api/appliance/storage/backups/restore/apply",
        json={
            "desired": _restore_desired(),
            "planId": PLAN_ID,
            "confirmation": "RESTORE SOMETHING ELSE",
        },
    )

    assert stale.status_code == 409 and wrong_confirmation.status_code == 409
    assert approval.calls == []


def test_plan_rejects_repository_paths_when_disabling() -> None:
    response = _client().post(
        "/api/appliance/storage/backups/schedule/plan",
        json={**_desired(), "enabled": False},
    )
    assert response.status_code == 422


def test_apply_binds_approval_and_audit_to_exact_plan(monkeypatch) -> None:
    approval = Approval()
    audit = Audit()
    monkeypatch.setattr(nas_backup_routes.policy, "plan_policy", lambda _desired: _plan())
    monkeypatch.setattr(
        nas_backup_routes.policy,
        "apply_policy",
        lambda _desired, plan_id: {
            **_plan(),
            "planId": plan_id,
            "applied": True,
            "verified": True,
        },
    )
    response = _client(approval=approval, audit=audit).post(
        "/api/appliance/storage/backups/schedule/apply",
        json={"desired": _desired(), "planId": PLAN_ID},
        headers={"X-Echo-Approval": "approval-token"},
    )
    assert response.status_code == 200
    assert response.json()["verified"] is True
    assert approval.calls[0]["action"] == nas_backup_routes.ACTION
    assert approval.calls[0]["target"] == PLAN_ID
    assert approval.calls[0]["token"] == "approval-token"
    assert [item["outcome"] for item in audit.calls] == ["attempted", "succeeded"]
    assert all(item["action"] == nas_backup_routes.ACTION for item in audit.calls)
    assert all("/mnt/" not in str(item["metadata"]) for item in audit.calls)


def test_apply_rejects_stale_plan_before_approval(monkeypatch) -> None:
    approval = Approval()
    monkeypatch.setattr(
        nas_backup_routes.policy,
        "plan_policy",
        lambda _desired: {**_plan(), "planId": "b" * 64},
    )
    response = _client(approval=approval).post(
        "/api/appliance/storage/backups/schedule/apply",
        json={"desired": _desired(), "planId": PLAN_ID},
    )
    assert response.status_code == 409
    assert approval.calls == []


def test_noop_apply_does_not_consume_approval(monkeypatch) -> None:
    approval = Approval()
    monkeypatch.setattr(nas_backup_routes.policy, "plan_policy", lambda _desired: _plan("none"))
    monkeypatch.setattr(
        nas_backup_routes.policy,
        "apply_policy",
        lambda _desired, _plan_id: {
            **_plan("none"),
            "applied": False,
            "verified": True,
        },
    )
    response = _client(approval=approval).post(
        "/api/appliance/storage/backups/schedule/apply",
        json={"desired": _desired(), "planId": PLAN_ID},
    )
    assert response.status_code == 200
    assert response.json()["applied"] is False
    assert approval.calls == []


def test_credential_plan_never_echoes_secret_or_paths(monkeypatch) -> None:
    monkeypatch.setattr(
        nas_backup_routes.credential_policy,
        "plan_credential",
        lambda _desired_state, **_kwargs: _credential_plan(),
    )
    response = _client().post(
        "/api/appliance/storage/backups/credential/plan",
        json=_credential_desired(),
    )

    assert response.status_code == 200
    assert response.json()["pathsRedacted"] is True
    assert "correct-horse-battery" not in response.text
    assert "/mnt/" not in response.text


def test_credential_apply_is_approval_bound_and_secret_redacted_from_audit(
    monkeypatch,
) -> None:
    approval = Approval()
    audit = Audit()
    monkeypatch.setattr(
        nas_backup_routes.credential_policy,
        "plan_credential",
        lambda _desired_state, **_kwargs: _credential_plan(),
    )
    monkeypatch.setattr(
        nas_backup_routes.credential_policy,
        "apply_credential",
        lambda _desired_state, plan_id, **_kwargs: {
            **_credential_plan(),
            "planId": plan_id,
            "applied": True,
            "verified": True,
            "repositoryId": "b" * 64,
        },
    )

    response = _client(approval=approval, audit=audit).post(
        "/api/appliance/storage/backups/credential/apply",
        json={"desired": _credential_desired(), "planId": PLAN_ID},
        headers={
            "X-Echo-Approval": "approval-token",
            "X-Echo-Intent": "task.backup.credential",
        },
    )

    assert response.status_code == 200
    assert approval.calls[0]["action"] == nas_backup_routes.CREDENTIAL_ACTION
    assert approval.calls[0]["target"] == PLAN_ID
    assert [item["outcome"] for item in audit.calls] == ["attempted", "succeeded"]
    assert all(item["metadata"]["intentId"] == "task.backup.credential" for item in audit.calls)
    serialized = str(audit.calls)
    assert "correct-horse-battery" not in serialized
    assert "/mnt/" not in serialized


def test_credential_rotation_is_exactly_approved_and_fully_redacted(
    monkeypatch,
) -> None:
    approval = Approval()
    audit = Audit()
    monkeypatch.setattr(
        nas_backup_routes.credential_policy,
        "plan_rotation",
        lambda _desired_state, **_kwargs: _rotation_plan(),
    )
    monkeypatch.setattr(
        nas_backup_routes.credential_policy,
        "apply_rotation",
        lambda _desired_state, plan_id, **_kwargs: {
            **_rotation_plan(),
            "planId": plan_id,
            "applied": True,
            "verified": True,
            "oldPasswordRevoked": True,
        },
    )

    response = _client(approval=approval, audit=audit).post(
        "/api/appliance/storage/backups/credential/rotation/apply",
        json={"desired": _rotation_desired(), "planId": PLAN_ID},
        headers={
            "X-Echo-Approval": "approval-token",
            "X-Echo-Intent": "task.backup.rotate",
        },
    )

    assert response.status_code == 200
    assert response.json()["oldPasswordRevoked"] is True
    assert approval.calls[0]["action"] == (nas_backup_routes.CREDENTIAL_ROTATION_ACTION)
    assert approval.calls[0]["target"] == PLAN_ID
    assert [item["outcome"] for item in audit.calls] == ["attempted", "succeeded"]
    assert all(item["metadata"]["secretRedacted"] is True for item in audit.calls)
    assert all(item["metadata"]["intentId"] == "task.backup.rotate" for item in audit.calls)
    serialized = str(audit.calls)
    assert "correct-horse-battery" not in serialized
    assert "new-correct-horse-battery" not in serialized
    assert "/mnt/" not in serialized
