from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from appliance import native_storage_pool


def _desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.zfs-mirror-desired.v1",
        "name": "family",
        "devices": ["/dev/sdb", "/dev/sdc"],
        "dataLossConfirmed": True,
        **overrides,
    }


def _identities(serial_suffix: str = "") -> list[dict[str, Any]]:
    return [
        {
            "devicefile": "/dev/sdb",
            "sizeBytes": 8 * 1024**3,
            "serial": f"disk-b{serial_suffix}",
            "wwn": None,
            "model": "QEMU HARDDISK",
        },
        {
            "devicefile": "/dev/sdc",
            "sizeBytes": 8 * 1024**3,
            "serial": f"disk-c{serial_suffix}",
            "wwn": None,
            "model": "QEMU HARDDISK",
        },
    ]


@pytest.fixture
def safe_pool_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    mount_root = tmp_path / "data"
    mount_root.mkdir()
    monkeypatch.setattr(native_storage_pool, "_ZFS_MOUNT_ROOT", mount_root)
    monkeypatch.setattr(native_storage_pool, "_ZFS_LOCK_PATH", tmp_path / "zfs.lock")
    monkeypatch.setattr(native_storage_pool, "_require_tools", lambda: None)
    monkeypatch.setattr(
        native_storage_pool,
        "_inspect_zfs_mirror_devices",
        lambda _devices: _identities(),
    )
    monkeypatch.setattr(native_storage_pool, "_existing_zfs_pool_names", lambda: [])
    return mount_root


def test_zfs_mirror_plan_binds_blank_disk_identities(safe_pool_host: Path) -> None:
    plan = native_storage_pool.plan_zfs_mirror(_desired(devices=["/dev/sdc", "/dev/sdb"]))

    assert plan["schema"] == "echo.omv.zfs-mirror-plan.v1"
    assert plan["operation"] == "create"
    assert plan["requiresApproval"] is True
    assert plan["desired"]["devices"] == ["/dev/sdb", "/dev/sdc"]
    assert plan["devices"] == _identities()
    assert plan["mountpoint"] == str(safe_pool_host / "family")
    assert plan["safety"]["destructive"] is True
    assert plan["safety"]["force"] is False


def test_zfs_mirror_apply_uses_exact_non_force_command_and_verifies(
    monkeypatch: pytest.MonkeyPatch, safe_pool_host: Path
) -> None:
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        native_storage_pool,
        "_run_mutating",
        lambda *args, **_kwargs: commands.append(args),
    )
    verified_pool = {
        "name": "family",
        "health": "ONLINE",
        "layout": "mirror",
        "mountpoint": str(safe_pool_host / "family"),
    }
    monkeypatch.setattr(native_storage_pool, "_verify_created_pool", lambda _plan: verified_pool)

    desired = _desired()
    plan = native_storage_pool.plan_zfs_mirror(desired)
    applied = native_storage_pool.apply_zfs_mirror(desired, plan["planId"])

    assert applied["verified"] is True
    assert applied["pool"] == verified_pool
    assert commands == [
        (
            "zpool",
            "create",
            "-o",
            "ashift=12",
            "-O",
            "compression=lz4",
            "-O",
            "atime=off",
            "-O",
            "xattr=sa",
            "-O",
            "acltype=posixacl",
            "-O",
            f"mountpoint={safe_pool_host / 'family'}",
            "family",
            "mirror",
            "/dev/sdb",
            "/dev/sdc",
        )
    ]
    assert "-f" not in commands[0]


def test_zfs_mirror_apply_rejects_rebound_disk_before_writing(
    monkeypatch: pytest.MonkeyPatch, safe_pool_host: Path
) -> None:
    identity_reads = iter((_identities(), _identities("-replacement")))
    monkeypatch.setattr(
        native_storage_pool,
        "_inspect_zfs_mirror_devices",
        lambda _devices: next(identity_reads),
    )
    writes: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        native_storage_pool,
        "_run_mutating",
        lambda *args, **_kwargs: writes.append(args),
    )

    desired = _desired()
    plan = native_storage_pool.plan_zfs_mirror(desired)
    with pytest.raises(ValueError, match="stale"):
        native_storage_pool.apply_zfs_mirror(desired, plan["planId"])

    assert writes == []


def test_zfs_mirror_verification_failure_destroys_new_pool(
    monkeypatch: pytest.MonkeyPatch, safe_pool_host: Path
) -> None:
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        native_storage_pool,
        "_run_mutating",
        lambda *args, **_kwargs: commands.append(args),
    )
    monkeypatch.setattr(
        native_storage_pool,
        "_verify_created_pool",
        lambda _plan: (_ for _ in ()).throw(OSError("read-back failed")),
    )
    monkeypatch.setattr(native_storage_pool, "_pool_exists", lambda _name: False)

    desired = _desired()
    plan = native_storage_pool.plan_zfs_mirror(desired)
    with pytest.raises(OSError, match="read-back failed"):
        native_storage_pool.apply_zfs_mirror(desired, plan["planId"])

    assert commands[-1] == ("zpool", "destroy", "family")


def test_disk_inspection_rejects_existing_partition_without_calling_wipefs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "blockdevices": [
            {
                "path": "/dev/sdb",
                "type": "disk",
                "size": 8 * 1024**3,
                "serial": "disk-b",
                "ro": False,
                "rm": False,
                "children": [{"path": "/dev/sdb1", "type": "part"}],
            },
            {
                "path": "/dev/sdc",
                "type": "disk",
                "size": 8 * 1024**3,
                "serial": "disk-c",
                "ro": False,
                "rm": False,
            },
        ]
    }
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: Any) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(native_storage_pool.subprocess, "run", run)

    with pytest.raises(ValueError, match="not blank"):
        native_storage_pool._inspect_zfs_mirror_devices(["/dev/sdb", "/dev/sdc"])

    assert [call[0] for call in calls] == ["lsblk"]


def test_zfs_mirror_requires_explicit_data_loss_confirmation() -> None:
    with pytest.raises(ValueError, match="dataLossConfirmed"):
        native_storage_pool.plan_zfs_mirror(_desired(dataLossConfirmed=False))


def test_zfs_reserved_pool_name_is_rejected_before_host_access() -> None:
    with pytest.raises(ValueError, match="reserved"):
        native_storage_pool.plan_zfs_mirror(_desired(name="mirrorhome"))


def test_candidate_inventory_returns_only_disks_that_pass_plan_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native_storage_pool, "_require_tools", lambda: None)
    monkeypatch.setattr(
        native_storage_pool,
        "_run_checked",
        lambda *_args, **_kwargs: json.dumps(
            {
                "blockdevices": [
                    {"path": "/dev/sda", "type": "disk"},
                    {"path": "/dev/sdb", "type": "disk"},
                    {"path": "/dev/sdc", "type": "disk"},
                    {"path": "/dev/sr0", "type": "rom"},
                ]
            }
        ),
    )

    def inspect(devices: list[str]) -> list[dict[str, Any]]:
        if devices == ["/dev/sda"]:
            raise ValueError("system disk has partitions")
        identity = next(item for item in _identities() if item["devicefile"] == devices[0])
        return [identity]

    monkeypatch.setattr(native_storage_pool, "_inspect_zfs_mirror_devices", inspect)

    assert native_storage_pool.zfs_mirror_candidates() == _identities()


def _export_desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.zfs-pool-export-desired.v1",
        "name": "family",
        "poolGuid": "15451357997522795478",
        "dataPreserved": True,
        **overrides,
    }


def _import_desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.zfs-pool-import-desired.v1",
        "name": "family",
        "poolGuid": "15451357997522795478",
        "mountPolicy": "echoDataRootOnly",
        **overrides,
    }


def _pool_snapshot() -> dict[str, Any]:
    return {
        "name": "family",
        "poolGuid": "15451357997522795478",
        "health": "ONLINE",
        "sizeBytes": 16 * 1024**3,
    }


def _import_candidate() -> dict[str, Any]:
    return {
        "name": "family",
        "poolGuid": "15451357997522795478",
        "state": "ONLINE",
        "layout": "mirror",
        "configHash": "c" * 64,
        "safeToImport": True,
    }


def test_importable_pool_parser_binds_documented_name_guid_state_and_layout() -> None:
    output = """
      pool: family
        id: 15451357997522795478
     state: ONLINE
    action: The pool can be imported using its name or numeric identifier.
    config:

            family      ONLINE
              mirror-0  ONLINE
                sdb     ONLINE
                sdc     ONLINE
    """

    candidates = native_storage_pool._parse_importable_pools(output)

    assert candidates == [
        {
            "name": "family",
            "poolGuid": "15451357997522795478",
            "state": "ONLINE",
            "layout": "mirror",
            "configHash": candidates[0]["configHash"],
            "safeToImport": True,
        }
    ]
    assert len(candidates[0]["configHash"]) == 64


@pytest.mark.parametrize(
    "state,action",
    [
        ("DEGRADED", "The pool can be imported using its name or numeric identifier."),
        ("ONLINE", "The pool was last accessed by another system and requires -f."),
    ],
)
def test_importable_pool_inventory_hides_degraded_or_force_required_candidates(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    action: str,
) -> None:
    monkeypatch.setattr(native_storage_pool, "_require_lifecycle_tools", lambda: None)
    monkeypatch.setattr(
        native_storage_pool,
        "_run_checked",
        lambda *_args, **_kwargs: (
            f"""
          pool: family
            id: 15451357997522795478
         state: {state}
        action: {action}
        config:
                family ONLINE
                  mirror-0 ONLINE
                    sdb ONLINE
                    sdc ONLINE
        """
        ),
    )

    assert native_storage_pool.importable_zfs_pools() == []


def test_exportable_pool_inventory_only_returns_idle_echo_layout_pools(
    monkeypatch: pytest.MonkeyPatch,
    zfs_lifecycle_host: Path,
) -> None:
    monkeypatch.setattr(native_storage_pool, "_imported_pool_snapshots", lambda: [_pool_snapshot()])
    monkeypatch.setattr(
        native_storage_pool,
        "_dataset_snapshots",
        lambda _name: [
            {
                "name": "family",
                "mountpoint": str(zfs_lifecycle_host / "family"),
                "canmount": "on",
                "mounted": "yes",
                "encryption": "off",
            }
        ],
    )
    monkeypatch.setattr(native_storage_pool, "_run_checked", lambda *_args, **_kwargs: "")

    assert native_storage_pool.exportable_zfs_pools() == [
        {
            **_pool_snapshot(),
            "rootMountpoint": str(zfs_lifecycle_host / "family"),
            "datasetCount": 1,
            "mountedCount": 1,
            "safeToExport": True,
        }
    ]


@pytest.mark.parametrize(
    "health,status,mountpoint",
    [
        ("DEGRADED", "", "echo"),
        ("ONLINE", "scan: scrub in progress", "echo"),
        ("ONLINE", "", "/srv/external"),
    ],
)
def test_exportable_pool_inventory_hides_unsafe_pools(
    monkeypatch: pytest.MonkeyPatch,
    zfs_lifecycle_host: Path,
    health: str,
    status: str,
    mountpoint: str,
) -> None:
    pool = {**_pool_snapshot(), "health": health}
    resolved_mountpoint = str(zfs_lifecycle_host / "family") if mountpoint == "echo" else mountpoint
    monkeypatch.setattr(native_storage_pool, "_imported_pool_snapshots", lambda: [pool])
    monkeypatch.setattr(native_storage_pool, "_run_checked", lambda *_args, **_kwargs: status)
    monkeypatch.setattr(
        native_storage_pool,
        "_dataset_snapshots",
        lambda _name: [
            {
                "name": "family",
                "mountpoint": resolved_mountpoint,
                "canmount": "on",
                "mounted": "yes",
                "encryption": "off",
            }
        ],
    )

    assert native_storage_pool.exportable_zfs_pools() == []


@pytest.fixture
def zfs_lifecycle_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    mount_root = tmp_path / "data"
    mount_root.mkdir()
    monkeypatch.setattr(native_storage_pool, "_ZFS_MOUNT_ROOT", mount_root)
    monkeypatch.setattr(native_storage_pool, "_ZFS_LOCK_PATH", tmp_path / "zfs.lock")
    monkeypatch.setattr(native_storage_pool, "_require_lifecycle_tools", lambda: None)
    return mount_root


def test_zfs_pool_export_is_guid_bound_non_force_and_rediscovers_candidate(
    monkeypatch: pytest.MonkeyPatch,
    zfs_lifecycle_host: Path,
) -> None:
    imported = {"value": True}
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        native_storage_pool,
        "_imported_pool_snapshots",
        lambda: [_pool_snapshot()] if imported["value"] else [],
    )
    monkeypatch.setattr(
        native_storage_pool,
        "_dataset_snapshots",
        lambda _name: [
            {
                "name": "family",
                "mountpoint": str(zfs_lifecycle_host / "family"),
                "canmount": "on",
                "mounted": "yes",
                "encryption": "off",
            }
        ],
    )
    monkeypatch.setattr(native_storage_pool, "_run_checked", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        native_storage_pool,
        "importable_zfs_pools",
        lambda: [_import_candidate()] if not imported["value"] else [],
    )

    def mutate(*args: str, **_kwargs: Any) -> None:
        commands.append(args)
        if args[:2] == ("zpool", "export"):
            imported["value"] = False

    monkeypatch.setattr(native_storage_pool, "_run_mutating", mutate)
    desired = _export_desired()
    plan = native_storage_pool.plan_zfs_pool_export(desired, [])
    applied = native_storage_pool.apply_zfs_pool_export(desired, plan["planId"], [])

    assert plan["pool"] == _pool_snapshot()
    assert plan["datasetCount"] == 1
    assert plan["mountedCount"] == 1
    assert plan["safety"]["force"] is False
    assert commands == [("zpool", "sync", "family"), ("zpool", "export", "family")]
    assert all("-f" not in command for command in commands)
    assert applied["verified"] is True
    assert applied["dataPreserved"] is True
    assert applied["pool"]["availability"] == "exported"


def test_zfs_pool_export_rejects_managed_share_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    zfs_lifecycle_host: Path,
) -> None:
    monkeypatch.setattr(native_storage_pool, "_imported_pool_snapshots", lambda: [_pool_snapshot()])
    writes: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        native_storage_pool,
        "_run_mutating",
        lambda *args, **_kwargs: writes.append(args),
    )

    with pytest.raises(ValueError, match="detach every Echo shared folder"):
        native_storage_pool.plan_zfs_pool_export(
            _export_desired(),
            [{"uuid": "11111111-2222-4333-8444-555555555555", "name": "Photos"}],
        )

    assert writes == []


def test_zfs_pool_import_uses_readonly_no_mount_inspection_then_mounts_exact_tree(
    monkeypatch: pytest.MonkeyPatch,
    zfs_lifecycle_host: Path,
) -> None:
    state = {"imported": False, "mounted": False}
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        native_storage_pool,
        "_imported_pool_snapshots",
        lambda: [_pool_snapshot()] if state["imported"] else [],
    )
    monkeypatch.setattr(
        native_storage_pool,
        "importable_zfs_pools",
        lambda: [] if state["imported"] else [_import_candidate()],
    )
    monkeypatch.setattr(
        native_storage_pool,
        "_dataset_snapshots",
        lambda _name: [
            {
                "name": "family",
                "mountpoint": str(zfs_lifecycle_host / "family"),
                "canmount": "on",
                "mounted": "yes" if state["mounted"] else "no",
                "encryption": "off",
            }
        ],
    )

    def mutate(*args: str, **_kwargs: Any) -> None:
        commands.append(args)
        if args[:2] == ("zpool", "import"):
            state["imported"] = True
        elif args[:2] == ("zpool", "export"):
            state["imported"] = False
            state["mounted"] = False
        elif args[:2] == ("zfs", "mount"):
            state["mounted"] = True

    monkeypatch.setattr(native_storage_pool, "_run_mutating", mutate)
    desired = _import_desired()
    plan = native_storage_pool.plan_zfs_pool_import(desired)
    applied = native_storage_pool.apply_zfs_pool_import(desired, plan["planId"])

    assert commands == [
        ("zpool", "import", "-N", "-o", "readonly=on", desired["poolGuid"]),
        ("zpool", "export", "family"),
        ("zpool", "import", "-N", desired["poolGuid"]),
        ("zfs", "mount", "family"),
    ]
    forbidden = {"-f", "-F", "-X", "-m", "-D", "-a"}
    assert all(forbidden.isdisjoint(command) for command in commands)
    assert applied["verified"] is True
    assert applied["pool"]["availability"] == "imported"
    assert applied["pool"]["mountedCount"] == 1


def test_zfs_pool_import_rejects_unsafe_mountpoint_and_restores_exported_state(
    monkeypatch: pytest.MonkeyPatch,
    zfs_lifecycle_host: Path,
) -> None:
    state = {"imported": False}
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        native_storage_pool,
        "_imported_pool_snapshots",
        lambda: [_pool_snapshot()] if state["imported"] else [],
    )
    monkeypatch.setattr(
        native_storage_pool,
        "importable_zfs_pools",
        lambda: [] if state["imported"] else [_import_candidate()],
    )
    monkeypatch.setattr(
        native_storage_pool,
        "_dataset_snapshots",
        lambda _name: [
            {
                "name": "family",
                "mountpoint": "/etc",
                "canmount": "on",
                "mounted": "no",
                "encryption": "off",
            }
        ],
    )

    def mutate(*args: str, **_kwargs: Any) -> None:
        commands.append(args)
        if args[:2] == ("zpool", "import"):
            state["imported"] = True
        elif args[:2] == ("zpool", "export"):
            state["imported"] = False

    monkeypatch.setattr(native_storage_pool, "_run_mutating", mutate)
    desired = _import_desired()
    plan = native_storage_pool.plan_zfs_pool_import(desired)

    with pytest.raises(ValueError, match="outside the Echo data root"):
        native_storage_pool.apply_zfs_pool_import(desired, plan["planId"])

    assert commands == [
        ("zpool", "import", "-N", "-o", "readonly=on", desired["poolGuid"]),
        ("zpool", "export", "family"),
    ]
    assert state["imported"] is False
