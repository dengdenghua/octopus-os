"""Write-plane guards for mounts whose writability could not be verified."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from appliance import native_storage


def _filesystem(mountpoint: str, *, mount_options_known: bool) -> dict[str, Any]:
    return {
        "uuid": "11111111-2222-4333-8444-555555555555",
        "devicefile": "/dev/test0",
        "type": "ext4",
        "mountpoint": mountpoint,
        "label": "data",
        "sizeBytes": 10_000,
        "availableBytes": 9_000,
        "readOnly": False,
        "mountOptionsKnown": mount_options_known,
        "supportsQuota": True,
    }


def test_unknown_mount_options_are_not_writable_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_storage,
        "filesystems",
        lambda: [_filesystem("/srv/data", mount_options_known=False)],
    )
    monkeypatch.setattr(native_storage, "_NATIVE_DATA_MOUNT_ROOTS", ("/srv",))

    assert native_storage._writable_targets() == {}


def test_shared_folder_plan_rejects_unknown_mount_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    volume = tmp_path / "data"
    volume.mkdir()
    registry = tmp_path / "state" / "native-shared-folders.json"
    monkeypatch.setattr(native_storage, "_NATIVE_SHARE_REGISTRY", registry)
    monkeypatch.setattr(native_storage, "_NATIVE_DATA_MOUNT_ROOTS", (str(tmp_path),))
    monkeypatch.setattr(
        native_storage,
        "filesystems",
        lambda: [_filesystem(str(volume), mount_options_known=False)],
    )

    desired = {
        "schema": "echo.omv.shared-folder-desired.v1",
        "mountPointRef": native_storage.volume_uuid(str(volume)),
        "name": "Photos",
        "comment": "Family photos",
    }

    with pytest.raises(ValueError, match="mounted writable volume"):
        native_storage.plan_shared_folder(desired)


def test_quota_plan_rejects_unknown_mount_options_before_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filesystem = _filesystem("/srv/data", mount_options_known=False)
    monkeypatch.setattr(native_storage, "filesystems", lambda: [filesystem])
    monkeypatch.setattr(
        native_storage,
        "_native_quota_tools_available",
        lambda: (_ for _ in ()).throw(AssertionError("quota tools must not be probed")),
    )
    monkeypatch.setattr(
        native_storage,
        "_principal_id",
        lambda *_args: (_ for _ in ()).throw(AssertionError("principal must not be resolved")),
    )

    desired = {
        "schema": "echo.omv.filesystem-quota-desired.v1",
        "filesystemUuid": filesystem["uuid"],
        "subjectType": "user",
        "subjectName": "mother",
        "hardLimitBytes": 1024**2,
    }

    with pytest.raises(ValueError, match="mount options are unavailable"):
        native_storage.plan_quota(desired)
