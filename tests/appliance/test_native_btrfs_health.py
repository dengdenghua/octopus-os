from __future__ import annotations

import json
from typing import Any

from appliance import native_btrfs_health, native_storage
from appliance.native_storage_probe import Probe, ReadOutput, evidence

FS_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _inventory(*, uuid: str | None = FS_UUID) -> str:
    return json.dumps(
        {
            "filesystems": [
                {
                    "uuid": uuid,
                    "source": "/dev/sdb",
                    "target": "/data/family",
                    "fstype": "btrfs",
                    "options": "rw,relatime",
                }
            ]
        }
    )


def _show(*, missing: bool = False) -> str:
    suffix = "\n*** Some devices missing" if missing else ""
    second = "" if missing else "\n\tdevid    2 size 8589934592 used 2 path /dev/sdc"
    return (
        f"Label: 'family'  uuid: {FS_UUID}\n"
        "\tTotal devices 2 FS bytes used 1048576\n"
        "\tdevid    1 size 8589934592 used 2 path /dev/sdb"
        f"{second}{suffix}\n"
    )


def _stats(*, corruption: int = 0) -> str:
    lines: list[str] = []
    for device in ("/dev/sdb", "/dev/sdc"):
        lines.extend(
            [
                f"[{device}].write_io_errs   0",
                f"[{device}].read_io_errs    0",
                f"[{device}].flush_io_errs   0",
                f"[{device}].corruption_errs {corruption if device.endswith('b') else 0}",
                f"[{device}].generation_errs 0",
            ]
        )
    return "\n".join(lines) + "\n"


def _runner(*, missing: bool = False, corruption: int = 0):
    def run(*args: str, **_kwargs: Any) -> str:
        if args[0] == "findmnt":
            return ReadOutput(_inventory(), exit_code=0)
        if args[:3] == ("btrfs", "filesystem", "show"):
            return ReadOutput(_show(missing=missing), exit_code=0)
        if args[:3] == ("btrfs", "filesystem", "df"):
            return ReadOutput(
                "Data, RAID1: total=1073741824, used=1048576\n"
                "Metadata, RAID1: total=268435456, used=1048576\n",
                exit_code=0,
            )
        if args[:3] == ("btrfs", "device", "stats"):
            output = _stats(corruption=corruption)
            if corruption:
                return ReadOutput(
                    "",
                    state="error",
                    code="command_failed",
                    exit_code=64,
                    partial_stdout=output,
                )
            return ReadOutput(output, exit_code=0)
        raise AssertionError(args)

    return run


def test_btrfs_probe_reports_healthy_raid1_topology() -> None:
    result = native_btrfs_health.probe_btrfs_filesystems(
        expected=True,
        runner=_runner(),
        checked_at="2026-09-05T00:00:00+00:00",
    )

    assert result.evidence["state"] == "ok"
    assert result.evidence["count"] == 1
    assert result.value == [
        {
            "devicefile": FS_UUID,
            "uuid": FS_UUID,
            "mountpoint": "/data/family",
            "level": "btrfs-raid1",
            "status": "healthy",
            "totalDevices": 2,
            "activeDevices": 2,
            "missingDevices": 0,
            "dataProfile": "raid1",
            "metadataProfile": "raid1",
            "operation": None,
            "operationPercent": None,
            "deviceErrors": {
                "corruption_errs": 0,
                "flush_io_errs": 0,
                "generation_errs": 0,
                "read_io_errs": 0,
                "write_io_errs": 0,
            },
            "deviceErrorCount": 0,
            "readOnly": False,
            "kind": "btrfs",
        }
    ]


def test_btrfs_probe_reports_missing_member_as_degraded() -> None:
    result = native_btrfs_health.probe_btrfs_filesystems(
        expected=True,
        runner=_runner(missing=True),
        checked_at="2026-09-05T00:00:00+00:00",
    )

    assert result.value[0]["status"] == "degraded"
    assert result.value[0]["activeDevices"] == 1
    assert result.value[0]["missingDevices"] == 1


def test_btrfs_probe_recovers_uuid_after_live_member_loss() -> None:
    def runner(*args: str, **_kwargs: Any) -> str:
        if args[0] == "findmnt":
            return ReadOutput(_inventory(uuid=None), exit_code=0)
        return _runner(missing=True)(*args)

    result = native_btrfs_health.probe_btrfs_filesystems(
        expected=True,
        runner=runner,
        checked_at="2026-09-05T00:00:00+00:00",
    )

    assert result.evidence["state"] == "ok"
    assert result.value[0]["uuid"] == FS_UUID
    assert result.value[0]["status"] == "degraded"


def test_btrfs_probe_retains_nonzero_device_counters_as_warning_evidence() -> None:
    result = native_btrfs_health.probe_btrfs_filesystems(
        expected=True,
        runner=_runner(corruption=2),
        checked_at="2026-09-05T00:00:00+00:00",
    )

    assert result.evidence["state"] == "partial"
    assert result.evidence["code"] == "device_errors_observed"
    assert result.value[0]["status"] == "warning"
    assert result.value[0]["deviceErrorCount"] == 2


def test_btrfs_probe_is_not_applicable_without_detected_filesystems() -> None:
    called = False

    def runner(*_args: str, **_kwargs: Any) -> str:
        nonlocal called
        called = True
        raise AssertionError("runner must not be called")

    result = native_btrfs_health.probe_btrfs_filesystems(
        expected=False,
        runner=runner,
        checked_at="2026-09-05T00:00:00+00:00",
    )

    assert called is False
    assert result.value == []
    assert result.evidence["state"] == "not-applicable"


def test_btrfs_probe_fails_closed_on_unsafe_mount_identity() -> None:
    def runner(*args: str, **_kwargs: Any) -> str:
        if args[0] == "findmnt":
            return ReadOutput(
                _inventory().replace("/data/family", "relative/path"),
                exit_code=0,
            )
        raise AssertionError(args)

    result = native_btrfs_health.probe_btrfs_filesystems(
        expected=True,
        runner=runner,
        checked_at="2026-09-05T00:00:00+00:00",
    )

    assert result.value == []
    assert result.evidence["state"] == "error"
    assert result.evidence["code"] == "parse_failed"


def test_storage_health_surfaces_btrfs_corruption_alert(
    monkeypatch,
) -> None:
    checked_at = "2026-09-05T00:00:00+00:00"
    filesystem = {
        "mountpoint": "/data/family",
        "status": "warning",
        "deviceErrorCount": 2,
        "readOnly": False,
    }
    monkeypatch.setattr(native_storage, "_now", lambda: checked_at)
    monkeypatch.setattr(
        native_storage,
        "_probe_filesystems",
        lambda: Probe(
            [{"type": "btrfs", "mountpoint": "/data/family", "usedPercent": 1}],
            {**evidence("filesystems", checked_at), "count": 1},
        ),
    )
    monkeypatch.setattr(
        native_storage,
        "_probe_block_devices",
        lambda: Probe(
            [],
            {
                **evidence("block-devices", checked_at),
                "count": 1,
                "btrfsPresent": True,
                "mdraidPresent": False,
                "zfsPresent": False,
            },
        ),
    )
    monkeypatch.setattr(
        native_storage,
        "_probe_md_arrays",
        lambda **_kwargs: Probe([], {**evidence("mdraid", checked_at, required=False)}),
    )
    monkeypatch.setattr(
        native_storage,
        "_probe_zfs_pools",
        lambda **_kwargs: Probe(([], []), {**evidence("zfs", checked_at, required=False)}),
    )
    monkeypatch.setattr(
        native_storage,
        "_probe_btrfs_filesystems",
        lambda **_kwargs: Probe(
            [filesystem],
            {**evidence("btrfs", checked_at), "count": 1},
        ),
    )
    monkeypatch.setattr(native_storage, "_probe_smart_devices", lambda _devices: [])

    result = native_storage.storage_health()

    assert result["state"] == "warning"
    assert result["pools"] == 1
    assert result["arrays"] == 1
    assert result["activeAlerts"][0]["code"] == "btrfs.device.errors"
    assert result["activeAlerts"][0]["resource"] == "/data/family"


def test_storage_topology_includes_btrfs_filesystem_as_an_array(
    monkeypatch,
) -> None:
    checked_at = "2026-09-05T00:00:00+00:00"
    filesystem = {
        "devicefile": FS_UUID,
        "uuid": FS_UUID,
        "mountpoint": "/data/family",
        "level": "btrfs-raid1",
        "status": "healthy",
        "kind": "btrfs",
    }
    monkeypatch.setattr(native_storage, "_now", lambda: checked_at)
    monkeypatch.setattr(
        native_storage,
        "_probe_block_devices",
        lambda: Probe(
            [{"devicefile": "/dev/sdb"}],
            {
                **evidence("block-devices", checked_at),
                "count": 1,
                "btrfsPresent": True,
                "mdraidPresent": False,
                "zfsPresent": False,
            },
        ),
    )
    monkeypatch.setattr(
        native_storage,
        "_probe_md_arrays",
        lambda **_kwargs: Probe([], evidence("mdraid", checked_at, required=False)),
    )
    monkeypatch.setattr(
        native_storage,
        "_probe_zfs_pools",
        lambda **_kwargs: Probe(([], []), evidence("zfs", checked_at, required=False)),
    )
    monkeypatch.setattr(
        native_storage,
        "_probe_btrfs_filesystems",
        lambda **_kwargs: Probe(
            [filesystem],
            {**evidence("btrfs", checked_at), "count": 1},
        ),
    )

    result = native_storage.storage_topology()

    assert result["arrays"] == [filesystem]
    assert result["coverage"] == "complete"
