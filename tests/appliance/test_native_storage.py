from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import native_storage
from appliance.native_storage_routes import create_omv_alias_router

MOUNT_POINT_REF = "11111111-2222-4333-8444-555555555555"


def _desired(mount_point_ref: str, **overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.shared-folder-desired.v1",
        "mountPointRef": mount_point_ref,
        "name": "Photos",
        "comment": "Family photos",
        **overrides,
    }


@pytest.fixture
def native_volume(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path, str]:
    volume = tmp_path / "volume"
    volume.mkdir()
    registry = tmp_path / "state" / "native-shared-folders.json"
    mount_point_ref = native_storage.volume_uuid(str(volume))
    monkeypatch.setattr(native_storage, "_NATIVE_SHARE_REGISTRY", registry)
    monkeypatch.setattr(native_storage, "_NATIVE_DATA_MOUNT_ROOTS", (str(tmp_path),))
    monkeypatch.setattr(
        native_storage,
        "filesystems",
        lambda: [
            {
                "devicefile": "/dev/test0",
                "mountpoint": str(volume),
                "readOnly": False,
            }
        ],
    )
    monkeypatch.setattr(native_storage, "_users_group_gid", lambda: 100)
    monkeypatch.setattr(native_storage, "_configure_shared_folder", lambda _path, _gid: None)
    monkeypatch.setattr(
        native_storage,
        "_verify_shared_folder",
        lambda path, _gid: path.is_dir(),
    )
    return volume, registry, mount_point_ref


def test_native_status_advertises_only_the_available_write_slice() -> None:
    payload = native_storage.status()

    assert payload["readOnly"] is False
    assert payload["capabilities"] == ["shared-folder.create.simple.v1"]


def test_sharing_targets_match_the_frontend_contract_without_host_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_storage,
        "filesystems",
        lambda: [
            {
                "devicefile": "/dev/test0",
                "uuid": None,
                "label": "Family",
                "type": "ext4",
                "mountpoint": "/srv/family",
                "sizeBytes": 10_000,
                "availableBytes": 8_000,
                "readOnly": False,
                "supportsAcl": False,
            }
        ],
    )
    monkeypatch.setattr(native_storage, "_etc_entries", lambda _path: [])
    monkeypatch.setattr(native_storage, "_NATIVE_DATA_MOUNT_ROOTS", ("/srv",))
    monkeypatch.setattr(native_storage, "_samba_usershares", lambda: [])
    monkeypatch.setattr(native_storage, "_registry_load", lambda **_kwargs: [])

    target = native_storage.sharing_overview()["sharedFolderTargets"][0]

    assert target == {
        "mountPointRef": native_storage.volume_uuid("/srv/family"),
        "filesystemUuid": None,
        "label": "Family",
        "type": "ext4",
        "sizeBytes": 10_000,
        "availableBytes": 8_000,
        "readOnly": False,
    }
    assert "path" not in target


def test_system_mounts_are_never_offered_as_shared_folder_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = [
        {
            "devicefile": "/dev/system",
            "uuid": None,
            "label": "root",
            "type": "ext4",
            "mountpoint": "/",
            "sizeBytes": 10_000,
            "availableBytes": 8_000,
            "readOnly": False,
            "supportsAcl": True,
        },
        {
            "devicefile": "/dev/data",
            "uuid": None,
            "label": "family",
            "type": "zfs",
            "mountpoint": "/data/family",
            "sizeBytes": 20_000,
            "availableBytes": 18_000,
            "readOnly": False,
            "supportsAcl": True,
        },
    ]
    monkeypatch.setattr(native_storage, "filesystems", lambda: entries)
    monkeypatch.setattr(native_storage, "_etc_entries", lambda _path: [])
    monkeypatch.setattr(native_storage, "_samba_usershares", lambda: [])
    monkeypatch.setattr(native_storage, "_registry_load", lambda **_kwargs: [])

    overview = native_storage.sharing_overview()

    assert [target["label"] for target in overview["sharedFolderTargets"]] == ["family"]
    assert native_storage.volume_uuid("/") not in native_storage._writable_targets()


def test_shared_folder_create_is_atomic_and_idempotent(
    native_volume: tuple[Path, Path, str],
) -> None:
    volume, registry, mount_point_ref = native_volume
    desired = _desired(mount_point_ref)
    plan = native_storage.plan_shared_folder(desired)

    assert plan["operation"] == "create"
    assert plan["requiresApproval"] is True
    applied = native_storage.apply_shared_folder(desired, plan["planId"])

    assert applied["applied"] is True
    assert applied["verified"] is True
    assert (volume / "Photos").is_dir()
    persisted = json.loads(registry.read_text(encoding="utf-8"))
    assert persisted == [applied["sharedFolder"]]
    assert plan["shareUuid"] == applied["sharedFolder"]["uuid"]

    repeated_plan = native_storage.plan_shared_folder(desired)
    assert repeated_plan["operation"] == "none"
    assert repeated_plan["planId"] != plan["planId"]
    repeated = native_storage.apply_shared_folder(desired, repeated_plan["planId"])
    assert repeated["applied"] is False
    assert repeated["verified"] is True


def test_apply_rejects_a_plan_after_the_target_state_changes(
    native_volume: tuple[Path, Path, str],
) -> None:
    volume, _registry, mount_point_ref = native_volume
    desired = _desired(mount_point_ref)
    plan = native_storage.plan_shared_folder(desired)
    (volume / "Photos").mkdir()

    with pytest.raises(ValueError, match="outside the native registry"):
        native_storage.apply_shared_folder(desired, plan["planId"])


@pytest.mark.parametrize("failure_point", ["permissions", "registry"])
def test_failed_create_removes_its_empty_directory(
    native_volume: tuple[Path, Path, str],
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    volume, registry, mount_point_ref = native_volume
    desired = _desired(mount_point_ref)
    plan = native_storage.plan_shared_folder(desired)

    if failure_point == "permissions":
        monkeypatch.setattr(
            native_storage,
            "_configure_shared_folder",
            lambda _path, _gid: (_ for _ in ()).throw(OSError("permission failure")),
        )
    else:
        monkeypatch.setattr(
            native_storage,
            "_registry_save",
            lambda _entries: (_ for _ in ()).throw(OSError("registry failure")),
        )

    with pytest.raises(OSError):
        native_storage.apply_shared_folder(desired, plan["planId"])

    assert not (volume / "Photos").exists()
    assert not registry.exists()


def test_corrupt_registry_fails_closed(
    native_volume: tuple[Path, Path, str],
) -> None:
    _volume, registry, mount_point_ref = native_volume
    registry.parent.mkdir(parents=True)
    registry.write_text("not-json", encoding="utf-8")

    with pytest.raises(OSError, match="registry is unreadable"):
        native_storage.plan_shared_folder(_desired(mount_point_ref))


def test_concurrent_apply_allows_exactly_one_commit(
    native_volume: tuple[Path, Path, str],
) -> None:
    _volume, registry, mount_point_ref = native_volume
    desired = _desired(mount_point_ref)
    plan = native_storage.plan_shared_folder(desired)

    def apply() -> str:
        try:
            native_storage.apply_shared_folder(desired, plan["planId"])
        except ValueError:
            return "stale"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda _index: apply(), range(2)))

    assert outcomes == ["created", "stale"]
    assert len(json.loads(registry.read_text(encoding="utf-8"))) == 1


def test_native_alias_exposes_create_capability_and_stale_plan_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    current_plan = {
        "planId": "a" * 64,
        "operation": "create",
        "requiresApproval": True,
    }
    monkeypatch.setattr(native_storage, "plan_shared_folder", lambda _desired: current_plan)
    app = FastAPI()
    app.include_router(create_omv_alias_router())
    client = TestClient(app)

    status = client.get("/api/appliance/omv/status")
    response = client.post(
        "/api/appliance/omv/sharing/folders/apply",
        json={"desired": _desired(MOUNT_POINT_REF), "planId": "b" * 64},
    )

    assert status.status_code == 200
    assert status.json()["capabilities"] == ["shared-folder.create.simple.v1"]
    assert response.status_code == 409


def test_native_alias_maps_apply_io_failure_to_service_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "a" * 64
    monkeypatch.setattr(
        native_storage,
        "plan_shared_folder",
        lambda _desired: {
            "planId": plan_id,
            "operation": "create",
            "requiresApproval": True,
        },
    )
    monkeypatch.setattr(
        native_storage,
        "apply_shared_folder",
        lambda _desired, _plan_id: (_ for _ in ()).throw(OSError("disk failure")),
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router())
    client = TestClient(app)

    response = client.post(
        "/api/appliance/omv/sharing/folders/apply",
        json={"desired": _desired(MOUNT_POINT_REF), "planId": plan_id},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "原生存储面暂不可用"}
