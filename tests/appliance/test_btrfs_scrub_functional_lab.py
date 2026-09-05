from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from deploy.appliance import btrfs_scrub_functional_lab as lab


def test_paths_are_unique_and_bounded_to_fixed_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_root = tmp_path / "images"
    mount_root = tmp_path / "mounts"
    image_root.mkdir()
    mount_root.mkdir()
    monkeypatch.setattr(lab, "IMAGE_ROOT", image_root)
    monkeypatch.setattr(lab, "MOUNT_ROOT", mount_root)

    first, second, mountpoint = lab._paths("0123456789")

    assert first.parent == second.parent == image_root
    assert mountpoint.parent == mount_root
    assert len(mountpoint.name) <= 16
    with pytest.raises(lab.BtrfsScrubFunctionalLabError, match="run id"):
        lab._paths("../../escape")
    first.touch()
    with pytest.raises(lab.BtrfsScrubFunctionalLabError, match="already exist"):
        lab._paths("0123456789")


def test_loop_device_parser_rejects_non_kernel_paths() -> None:
    assert lab._loop_device("/dev/loop12\n") == "/dev/loop12"
    for value in ("/dev/sda", "/dev/loop-control", "/dev/loop1 extra", "../../loop1"):
        with pytest.raises(lab.BtrfsScrubFunctionalLabError, match="unsafe loop"):
            lab._loop_device(value)


def _record(**filesystem_overrides: Any) -> dict[str, Any]:
    filesystem = {
        "uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "status": "healthy",
        "readOnly": False,
        "totalDevices": 2,
        "activeDevices": 2,
        "missingDevices": 0,
        "dataProfile": "raid1",
        "metadataProfile": "raid1",
        "deviceErrorCount": 0,
        **filesystem_overrides,
    }
    return {
        "filesystems": [
            {
                "filesystem": filesystem,
                "exclusiveOperation": "none",
                "canStartScrub": True,
                "scan": {"state": "idle"},
            }
        ]
    }


def test_maintenance_record_accepts_only_complete_healthy_raid1() -> None:
    filesystem_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert lab._maintenance_record(_record(), filesystem_uuid)["canStartScrub"] is True

    with pytest.raises(lab.BtrfsScrubFunctionalLabError, match="not safe"):
        lab._maintenance_record(_record(missingDevices=1, activeDevices=1), filesystem_uuid)


def test_scrub_finished_requires_matching_uuid_finished_and_zero_errors() -> None:
    filesystem_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    completed = (
        f"UUID:             {filesystem_uuid}\n"
        "Status:           finished\n"
        "Error summary:    no errors found\n"
    )
    assert lab._scrub_finished(completed, filesystem_uuid)
    assert not lab._scrub_finished(completed.replace("finished", "running"), filesystem_uuid)
    assert not lab._scrub_finished(completed.replace("no errors found", "csum=1"), filesystem_uuid)
    assert not lab._scrub_finished(completed, "11111111-2222-3333-4444-555555555555")


def test_backing_image_assertion_allows_public_mountpoint_but_rejects_image_path() -> None:
    images = (Path("/var/tmp/first.img"), Path("/var/tmp/second.img"))

    lab._assert_backing_images_hidden({"filesystem": {"mountpoint": "/data/family"}}, images)
    with pytest.raises(lab.BtrfsScrubFunctionalLabError, match="backing image"):
        lab._assert_backing_images_hidden({"debug": {"device": "/var/tmp/second.img"}}, images)


def test_restore_replays_saved_files_in_reverse_order(monkeypatch: pytest.MonkeyPatch) -> None:
    restored: list[Path] = []
    first = lab.base.SavedFile(Path("/one"), False, None, None)
    second = lab.base.SavedFile(Path("/two"), False, None, None)
    monkeypatch.setattr(lab.base, "_atomic_restore", lambda saved: restored.append(saved.path))

    lab._restore([first, second])

    assert restored == [Path("/two"), Path("/one")]
