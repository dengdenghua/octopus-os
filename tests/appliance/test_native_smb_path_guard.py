"""Guard the native SMB plane against same-name foreign usershares."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from appliance import native_storage


def test_smb_plan_rejects_a_usershare_bound_to_another_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    volume = tmp_path / "data"
    volume.mkdir()
    folder = volume / "Photos"
    folder.mkdir()
    registry = tmp_path / "state" / "native-shared-folders.json"
    registry.parent.mkdir()
    folder_uuid = "11111111-2222-4333-8444-555555555555"
    mount_ref = native_storage.volume_uuid(str(volume))
    registry.write_text(
        json.dumps(
            [
                {
                    "uuid": folder_uuid,
                    "name": "Photos",
                    "relativePath": "Photos",
                    "comment": "",
                    "device": "/dev/test0",
                    "volumePath": str(volume),
                    "mountPointRef": mount_ref,
                }
            ]
        ),
        encoding="utf-8",
    )
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
    monkeypatch.setattr(
        native_storage,
        "_smb_usershare_info",
        lambda _name: {
            "path": str(tmp_path / "other"),
            "comment": "foreign",
            "usershare_acl": "Everyone:F,",
        },
    )

    desired: dict[str, Any] = {
        "schema": "echo.omv.smb-share-desired.v1",
        "sharedFolderRef": folder_uuid,
        "enabled": False,
        "readOnly": False,
        "browseable": True,
        "recycleBin": False,
        "comment": "foreign",
    }

    with pytest.raises(ValueError, match="another path"):
        native_storage.plan_smb(desired)


def test_smb_path_guard_normalizes_only_the_expected_path(tmp_path: Path) -> None:
    expected = tmp_path / "data" / "Photos"
    assert native_storage._smb_info_targets_path({"path": str(expected)}, expected)
    assert not native_storage._smb_info_targets_path(
        {"path": str(expected.parent / "Other")}, expected
    )
    assert not native_storage._smb_info_targets_path({"path": "Photos"}, expected)
    assert not native_storage._smb_info_targets_path({"path": ""}, expected)
