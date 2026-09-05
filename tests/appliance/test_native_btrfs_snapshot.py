from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import native_btrfs_snapshot
from appliance.native_storage_routes import create_omv_alias_router
from appliance.omv_protocol import (
    validate_btrfs_snapshot_delete_desired,
    validate_btrfs_snapshot_desired,
    validate_btrfs_snapshot_restore_copy_desired,
)

SHARE_UUID = "11111111-2222-4333-8444-555555555555"
SNAPSHOT_UUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
SOURCE_UUID = "12345678-1234-4234-9234-123456789abc"


def _desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.btrfs-snapshot-desired.v1",
        "sharedFolderRef": SHARE_UUID,
        "name": "before_upgrade",
        **overrides,
    }


def _delete_desired(snapshot_id: str) -> dict[str, Any]:
    return {
        "schema": "echo.omv.btrfs-snapshot-delete-desired.v1",
        "sharedFolderRef": SHARE_UUID,
        "snapshotId": snapshot_id,
    }


def _restore_desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.btrfs-snapshot-restore-copy-desired.v1",
        "sharedFolderRef": SHARE_UUID,
        "snapshotId": SNAPSHOT_UUID,
        "name": "photos_recovered",
        **overrides,
    }


def _identity(*, parent: str | None = None, read_only: bool = False) -> dict[str, Any]:
    return {
        "subvolumeUuid": SNAPSHOT_UUID if parent else SOURCE_UUID,
        "subvolumeId": 257 if parent else 256,
        "parentUuid": parent,
        "readOnly": read_only,
    }


@pytest.fixture
def snapshot_share(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    volume = tmp_path / "volume"
    source = volume / "Photos"
    source.mkdir(parents=True)
    entry = {
        "uuid": SHARE_UUID,
        "name": "Photos",
        "relativePath": "Photos",
        "volumePath": str(volume),
        "mountPointRef": "22222222-3333-4444-8555-666666666666",
        "storageKind": "btrfsSubvolume",
    }
    source_identity = _identity()
    monkeypatch.setattr(native_btrfs_snapshot.storage, "_registry_transaction", nullcontext)
    monkeypatch.setattr(
        native_btrfs_snapshot,
        "_resolve_share",
        lambda _ref: (entry, source, source_identity),
    )
    return entry, source, source_identity


def test_snapshot_validators_reject_paths_and_cross_share_delete() -> None:
    assert validate_btrfs_snapshot_desired(_desired())["name"] == "before_upgrade"
    with pytest.raises(ValueError, match="snapshot name"):
        validate_btrfs_snapshot_desired(_desired(name="../escape"))
    with pytest.raises(ValueError, match="reserved"):
        validate_btrfs_snapshot_desired(_desired(name="auto-20260905t010203z"))
    with pytest.raises(ValueError, match="snapshotId"):
        validate_btrfs_snapshot_delete_desired(_delete_desired("not-a-uuid"))
    assert (
        validate_btrfs_snapshot_restore_copy_desired(_restore_desired())["name"]
        == "photos_recovered"
    )
    with pytest.raises(ValueError, match="portable"):
        validate_btrfs_snapshot_restore_copy_desired(_restore_desired(name="../escape"))


def test_subvolume_parser_requires_stable_uuid_and_id() -> None:
    parsed = native_btrfs_snapshot._parse_subvolume_show(
        f"Name: before_upgrade\nUUID: {SNAPSHOT_UUID}\nParent UUID: {SOURCE_UUID}\n"
        "Subvolume ID: 257\n"
    )
    assert parsed == {
        "subvolumeUuid": SNAPSHOT_UUID,
        "subvolumeId": 257,
        "parentUuid": SOURCE_UUID,
    }
    with pytest.raises(OSError, match="stable UUID"):
        native_btrfs_snapshot._parse_subvolume_show("UUID: -\nSubvolume ID: 257\n")


def test_inventory_projects_the_root_managed_lock_state(
    monkeypatch: pytest.MonkeyPatch, snapshot_share
) -> None:
    entry, source, source_identity = snapshot_share
    snapshot_path = source.parent / ".echo-snapshots" / SHARE_UUID / "before_upgrade"
    snapshot_path.mkdir(parents=True)
    snapshot_id = native_btrfs_snapshot._snapshot_id(SHARE_UUID, "before_upgrade")
    monkeypatch.setattr(native_btrfs_snapshot, "_private_directory", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        native_btrfs_snapshot, "locked_snapshot_ids", lambda _ref: frozenset({snapshot_id})
    )
    monkeypatch.setattr(
        native_btrfs_snapshot,
        "_subvolume_identity",
        lambda path: (
            _identity(parent=source_identity["subvolumeUuid"], read_only=True)
            if path == snapshot_path
            else source_identity
        ),
    )
    snapshots = native_btrfs_snapshot._inventory(entry, source, source_identity)
    assert snapshots[0]["snapshotId"] == snapshot_id
    assert snapshots[0]["locked"] is True


def test_create_plan_is_path_free_and_idempotent(
    monkeypatch: pytest.MonkeyPatch, snapshot_share
) -> None:
    _entry, source, _source_identity = snapshot_share
    monkeypatch.setattr(native_btrfs_snapshot, "_inventory", lambda *_args: [])

    plan = native_btrfs_snapshot.plan_snapshot(_desired())

    assert plan["operation"] == "create"
    assert plan["requiresApproval"] is True
    assert str(source) not in json.dumps(plan)

    existing = {
        "snapshotId": native_btrfs_snapshot._snapshot_id(SHARE_UUID, "before_upgrade"),
        "name": "before_upgrade",
        "subvolumeUuid": SNAPSHOT_UUID,
        "readOnly": True,
    }
    monkeypatch.setattr(native_btrfs_snapshot, "_inventory", lambda *_args: [existing])
    repeated = native_btrfs_snapshot.plan_snapshot(_desired())
    assert repeated["operation"] == "none"
    assert repeated["requiresApproval"] is False


def test_create_plan_enforces_fnos_compatible_256_limit(
    monkeypatch: pytest.MonkeyPatch, snapshot_share
) -> None:
    monkeypatch.setattr(
        native_btrfs_snapshot,
        "_inventory",
        lambda *_args: [
            {
                "snapshotId": str(index),
                "name": f"s{index}",
                "subvolumeUuid": SNAPSHOT_UUID,
                "readOnly": True,
            }
            for index in range(256)
        ],
    )
    with pytest.raises(ValueError, match="maximum 256"):
        native_btrfs_snapshot.plan_snapshot(_desired())


def test_apply_creates_read_only_snapshot_and_reconciles_late_error(
    monkeypatch: pytest.MonkeyPatch, snapshot_share
) -> None:
    entry, source, source_identity = snapshot_share
    monkeypatch.setattr(native_btrfs_snapshot, "_inventory", lambda *_args: [])
    directory = source.parent / ".echo-snapshots" / SHARE_UUID
    monkeypatch.setattr(
        native_btrfs_snapshot, "_ensure_snapshot_directory", lambda *_args: directory
    )
    directory.mkdir(parents=True)
    commands: list[tuple[str, ...]] = []

    def late_failure(*args: str, **_kwargs: Any) -> None:
        commands.append(args)
        Path(args[-1]).mkdir()
        raise OSError("client timed out after kernel accepted snapshot")

    monkeypatch.setattr(native_btrfs_snapshot, "_run_mutation", late_failure)
    monkeypatch.setattr(
        native_btrfs_snapshot,
        "_subvolume_identity",
        lambda path: (
            _identity(parent=source_identity["subvolumeUuid"], read_only=True)
            if path != source
            else source_identity
        ),
    )
    plan = native_btrfs_snapshot.plan_snapshot(_desired())
    result = native_btrfs_snapshot.apply_snapshot(_desired(), plan["planId"])

    assert commands == [
        (
            "btrfs",
            "subvolume",
            "snapshot",
            "-r",
            str(source),
            str(directory / "before_upgrade"),
        )
    ]
    assert result["verified"] is True
    assert result["snapshot"]["readOnly"] is True
    assert result["snapshot"]["kind"] == "manual"
    assert result["snapshot"]["locked"] is False
    assert str(directory) not in json.dumps(result)


def test_scheduler_can_use_the_reserved_automatic_namespace(
    monkeypatch: pytest.MonkeyPatch, snapshot_share
) -> None:
    _entry, source, source_identity = snapshot_share
    monkeypatch.setattr(native_btrfs_snapshot, "_inventory", lambda *_args: [])
    directory = source.parent / ".echo-snapshots" / SHARE_UUID
    directory.mkdir(parents=True)
    monkeypatch.setattr(
        native_btrfs_snapshot, "_ensure_snapshot_directory", lambda *_args: directory
    )

    def create(*args: str, **_kwargs: Any) -> None:
        Path(args[-1]).mkdir()

    monkeypatch.setattr(native_btrfs_snapshot, "_run_mutation", create)
    monkeypatch.setattr(
        native_btrfs_snapshot,
        "_subvolume_identity",
        lambda path: (
            _identity(parent=source_identity["subvolumeUuid"], read_only=True)
            if path != source
            else source_identity
        ),
    )
    name = "auto-20260905t010203z"
    plan = native_btrfs_snapshot.plan_automatic_snapshot(SHARE_UUID, name)
    result = native_btrfs_snapshot.apply_automatic_snapshot(SHARE_UUID, name, plan["planId"])
    assert result["snapshot"]["kind"] == "automatic"


def test_apply_rejects_stale_inventory(monkeypatch: pytest.MonkeyPatch, snapshot_share) -> None:
    snapshots: list[dict[str, Any]] = []
    monkeypatch.setattr(native_btrfs_snapshot, "_inventory", lambda *_args: snapshots)
    plan = native_btrfs_snapshot.plan_snapshot(_desired())
    snapshots.append(
        {
            "snapshotId": native_btrfs_snapshot._snapshot_id(SHARE_UUID, "other"),
            "name": "other",
            "subvolumeUuid": SNAPSHOT_UUID,
            "readOnly": True,
        }
    )

    with pytest.raises(ValueError, match="stale"):
        native_btrfs_snapshot.apply_snapshot(_desired(), plan["planId"])


def test_failed_command_does_not_delete_an_unverified_racing_target(
    monkeypatch: pytest.MonkeyPatch, snapshot_share
) -> None:
    _entry, source, _source_identity = snapshot_share
    monkeypatch.setattr(native_btrfs_snapshot, "_inventory", lambda *_args: [])
    directory = source.parent / ".echo-snapshots" / SHARE_UUID
    directory.mkdir(parents=True)
    destination = directory / "before_upgrade"
    monkeypatch.setattr(
        native_btrfs_snapshot, "_ensure_snapshot_directory", lambda *_args: directory
    )

    def failed_command(*_args: str, **_kwargs: Any) -> None:
        destination.mkdir()
        raise OSError("command failed")

    monkeypatch.setattr(native_btrfs_snapshot, "_run_mutation", failed_command)
    monkeypatch.setattr(
        native_btrfs_snapshot,
        "_subvolume_identity",
        lambda _path: _identity(parent=None, read_only=True),
    )
    plan = native_btrfs_snapshot.plan_snapshot(_desired())

    with pytest.raises(OSError, match="command failed"):
        native_btrfs_snapshot.apply_snapshot(_desired(), plan["planId"])
    assert destination.exists()


def test_restore_copy_creates_a_new_writable_registered_share_without_paths(
    monkeypatch: pytest.MonkeyPatch, snapshot_share
) -> None:
    entry, source, _source_identity = snapshot_share
    volume = source.parent
    snapshot_directory = volume / ".echo-snapshots" / SHARE_UUID
    snapshot_path = snapshot_directory / "before_upgrade"
    snapshot_path.mkdir(parents=True)
    snapshot = {
        "snapshotId": SNAPSHOT_UUID,
        "name": "before_upgrade",
        "subvolumeUuid": SNAPSHOT_UUID,
        "readOnly": True,
        "kind": "manual",
    }
    registry = [entry]
    monkeypatch.setattr(native_btrfs_snapshot, "_inventory", lambda *_args: [snapshot])
    monkeypatch.setattr(
        native_btrfs_snapshot.storage,
        "_writable_targets",
        lambda: {entry["mountPointRef"]: str(volume)},
    )
    monkeypatch.setattr(
        native_btrfs_snapshot.storage,
        "_shared_folder_target_payload",
        lambda ref, path: {"mountPointRef": ref, "type": "btrfs", "readOnly": False},
    )
    monkeypatch.setattr(
        native_btrfs_snapshot.storage, "_registry_load", lambda **_kwargs: list(registry)
    )

    def save(entries: list[dict[str, Any]]) -> None:
        registry[:] = entries

    monkeypatch.setattr(native_btrfs_snapshot.storage, "_registry_save", save)
    monkeypatch.setattr(native_btrfs_snapshot.storage, "_users_group_gid", lambda: 100)
    monkeypatch.setattr(
        native_btrfs_snapshot.storage, "_configure_shared_folder", lambda *_args: None
    )
    monkeypatch.setattr(native_btrfs_snapshot.storage, "_verify_shared_folder", lambda *_args: True)
    recovered_uuid = native_btrfs_snapshot.storage._share_uuid(
        entry["mountPointRef"], "photos_recovered"
    )

    def recovered_entry(**kwargs: Any) -> dict[str, Any]:
        return {
            "uuid": recovered_uuid,
            "name": kwargs["name"],
            "comment": kwargs["comment"],
            "relativePath": kwargs["name"],
            "volumePath": kwargs["volume_path"],
            "mountPointRef": kwargs["volume_ref"],
            "storageKind": kwargs["storage_kind"],
        }

    monkeypatch.setattr(native_btrfs_snapshot.storage, "_registry_folder_entry", recovered_entry)
    monkeypatch.setattr(
        native_btrfs_snapshot.storage,
        "_public_shared_folder_entry",
        lambda value: {"uuid": value["uuid"], "name": value["name"]},
    )
    commands: list[tuple[str, ...]] = []

    def mutate(*args: str, **_kwargs: Any) -> None:
        commands.append(args)
        if args[2] == "snapshot":
            Path(args[-1]).mkdir()
        else:
            Path(args[-1]).rmdir()

    monkeypatch.setattr(native_btrfs_snapshot, "_run_mutation", mutate)
    monkeypatch.setattr(
        native_btrfs_snapshot,
        "_subvolume_identity",
        lambda path: (
            {
                "subvolumeUuid": "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
                "subvolumeId": 258,
                "parentUuid": SNAPSHOT_UUID,
                "readOnly": False,
            }
            if path.name == "photos_recovered"
            else _identity()
        ),
    )

    plan = native_btrfs_snapshot.plan_snapshot_restore_copy(_restore_desired())
    assert plan["operation"] == "createRecoveredShare"
    assert plan["safety"]["sourceShareUntouched"] is True
    assert str(volume) not in json.dumps(plan)
    result = native_btrfs_snapshot.apply_snapshot_restore_copy(_restore_desired(), plan["planId"])

    assert commands == [
        (
            "btrfs",
            "subvolume",
            "snapshot",
            str(snapshot_path),
            str(volume / "photos_recovered"),
        )
    ]
    assert result["verified"] is True
    assert result["sharedFolder"] == {"uuid": recovered_uuid, "name": "photos_recovered"}
    assert source.exists()
    assert snapshot_path.exists()
    assert len(registry) == 2
    assert str(volume) not in json.dumps(result)


def test_delete_is_bound_to_inventory_and_preserves_source(
    monkeypatch: pytest.MonkeyPatch, snapshot_share
) -> None:
    entry, source, source_identity = snapshot_share
    directory = source.parent / ".echo-snapshots" / SHARE_UUID
    destination = directory / "before_upgrade"
    destination.mkdir(parents=True)
    snapshot = {
        "snapshotId": native_btrfs_snapshot._snapshot_id(SHARE_UUID, "before_upgrade"),
        "name": "before_upgrade",
        "subvolumeUuid": SNAPSHOT_UUID,
        "readOnly": True,
    }
    monkeypatch.setattr(native_btrfs_snapshot, "_inventory", lambda *_args: [snapshot])

    def delete(*args: str, **_kwargs: Any) -> None:
        assert args[:-1] == ("btrfs", "subvolume", "delete")
        Path(args[-1]).rmdir()

    monkeypatch.setattr(native_btrfs_snapshot, "_run_mutation", delete)
    monkeypatch.setattr(native_btrfs_snapshot, "_subvolume_identity", lambda _path: source_identity)
    desired = _delete_desired(snapshot["snapshotId"])
    plan = native_btrfs_snapshot.plan_snapshot_delete(desired)
    result = native_btrfs_snapshot.apply_snapshot_delete(desired, plan["planId"])

    assert result["snapshotDeleted"] is True
    assert source.exists()
    assert not destination.exists()
    assert str(directory) not in json.dumps(result)


def test_locked_snapshot_must_be_unlocked_before_delete(
    monkeypatch: pytest.MonkeyPatch, snapshot_share
) -> None:
    snapshot = {
        "snapshotId": SNAPSHOT_UUID,
        "name": "before_upgrade",
        "subvolumeUuid": SNAPSHOT_UUID,
        "readOnly": True,
        "kind": "manual",
        "locked": True,
    }
    monkeypatch.setattr(native_btrfs_snapshot, "_inventory", lambda *_args: [snapshot])
    with pytest.raises(ValueError, match="must be unlocked"):
        native_btrfs_snapshot.plan_snapshot_delete(_delete_desired(SNAPSHOT_UUID))


def test_snapshot_route_consumes_exact_plan_bound_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "d" * 64
    current_plan = {"planId": plan_id, "operation": "create", "requiresApproval": True}
    approvals: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approvals.append(kwargs)

    class Audit:
        def record(self, **_kwargs: Any) -> None:
            pass

    monkeypatch.setattr(native_btrfs_snapshot, "plan_snapshot", lambda _desired: current_plan)
    monkeypatch.setattr(
        native_btrfs_snapshot,
        "apply_snapshot",
        lambda _desired, _plan_id: {**current_plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))
    response = TestClient(app).post(
        "/api/appliance/omv/sharing/snapshots/apply",
        json={"desired": _desired(), "planId": plan_id},
        headers={"X-Echo-Approval": "approval-token"},
    )
    assert response.status_code == 200
    assert approvals[0]["action"] == "omv.btrfs-snapshot.create"
    assert approvals[0]["target"] == plan_id


def test_snapshot_delete_route_consumes_exact_plan_bound_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "e" * 64
    snapshot_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    current_plan = {"planId": plan_id, "operation": "delete", "requiresApproval": True}
    approvals: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approvals.append(kwargs)

    class Audit:
        def record(self, **_kwargs: Any) -> None:
            pass

    monkeypatch.setattr(
        native_btrfs_snapshot, "plan_snapshot_delete", lambda _desired: current_plan
    )
    monkeypatch.setattr(
        native_btrfs_snapshot,
        "apply_snapshot_delete",
        lambda _desired, _plan_id: {**current_plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))
    response = TestClient(app).post(
        "/api/appliance/omv/sharing/snapshots/delete/apply",
        json={"desired": _delete_desired(snapshot_id), "planId": plan_id},
        headers={"X-Echo-Approval": "approval-token"},
    )
    assert response.status_code == 200
    assert approvals[0]["action"] == "omv.btrfs-snapshot.delete"
    assert approvals[0]["target"] == plan_id


def test_snapshot_restore_copy_route_consumes_exact_plan_bound_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "f" * 64
    current_plan = {
        "planId": plan_id,
        "operation": "createRecoveredShare",
        "requiresApproval": True,
    }
    approvals: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approvals.append(kwargs)

    class Audit:
        def record(self, **_kwargs: Any) -> None:
            pass

    monkeypatch.setattr(
        native_btrfs_snapshot, "plan_snapshot_restore_copy", lambda _desired: current_plan
    )
    monkeypatch.setattr(
        native_btrfs_snapshot,
        "apply_snapshot_restore_copy",
        lambda _desired, _plan_id: {**current_plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))
    response = TestClient(app).post(
        "/api/appliance/omv/sharing/snapshots/restore-copy/apply",
        json={"desired": _restore_desired(), "planId": plan_id},
        headers={"X-Echo-Approval": "approval-token"},
    )
    assert response.status_code == 200
    assert approvals[0]["action"] == "omv.btrfs-snapshot.restore-copy"
    assert approvals[0]["target"] == plan_id


def test_snapshot_list_route_returns_only_public_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    expected = {
        "sharedFolderRef": SHARE_UUID,
        "snapshots": [
            {
                "snapshotId": SNAPSHOT_UUID,
                "name": "before_upgrade",
                "subvolumeUuid": SOURCE_UUID,
                "readOnly": True,
                "kind": "manual",
                "locked": False,
            }
        ],
        "limit": 256,
        "source": "native",
    }
    monkeypatch.setattr(native_btrfs_snapshot, "list_snapshots", lambda _ref: expected)
    app = FastAPI()
    app.include_router(create_omv_alias_router())

    response = TestClient(app).get(f"/api/appliance/omv/sharing/{SHARE_UUID}/snapshots")

    assert response.status_code == 200
    assert response.json() == expected
