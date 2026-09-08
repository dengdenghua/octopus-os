from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import native_btrfs, native_storage
from appliance.native_storage_routes import create_omv_alias_router
from appliance.omv_protocol import validate_btrfs_raid1_desired

FS_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.btrfs-raid1-desired.v1",
        "name": "family",
        "devices": ["/dev/sdb", "/dev/sdc"],
        "dataLossConfirmed": True,
        **overrides,
    }


def _identities() -> list[dict[str, Any]]:
    return [
        {
            "devicefile": "/dev/sdb",
            "sizeBytes": 2 * 1024**3,
            "serial": "disk-b",
            "wwn": None,
            "model": "test",
        },
        {
            "devicefile": "/dev/sdc",
            "sizeBytes": 2 * 1024**3,
            "serial": "disk-c",
            "wwn": None,
            "model": "test",
        },
    ]


@pytest.fixture
def btrfs_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    etc = tmp_path / "etc"
    data = tmp_path / "data"
    etc.mkdir()
    data.mkdir()
    monkeypatch.setattr(native_btrfs, "_LOCK_PATH", tmp_path / "btrfs.lock")
    monkeypatch.setattr(native_btrfs, "_require_tools", lambda: None)
    monkeypatch.setattr(
        native_btrfs,
        "inspect_blank_whole_disks",
        lambda _devices: _identities(),
    )
    return etc / "fstab", data


@pytest.mark.parametrize(
    "payload",
    [
        _desired(dataLossConfirmed=False),
        _desired(name="Family"),
        _desired(name="this-name-is-too-long"),
        _desired(devices=["/dev/sdb"]),
        _desired(devices=["/dev/sdb", "/dev/sdb"]),
        {**_desired(), "force": True},
    ],
)
def test_btrfs_protocol_rejects_unsafe_or_expanded_state(payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        validate_btrfs_raid1_desired(payload)


def test_btrfs_plan_binds_blank_disk_identities_fstab_and_mountpoint(
    btrfs_host: tuple[Path, Path],
) -> None:
    fstab, mount_root = btrfs_host
    plan = native_btrfs.plan_btrfs_raid1(_desired(), fstab_path=fstab, mount_root=mount_root)

    assert plan["schema"] == "echo.omv.btrfs-raid1-plan.v1"
    assert plan["devices"] == _identities()
    assert plan["mountpoint"] == str(mount_root / "family")
    assert plan["requiresApproval"] is True
    assert plan["safety"]["dataProfile"] == "raid1"
    assert plan["safety"]["metadataProfile"] == "raid1"
    assert plan["safety"]["force"] is False


def test_btrfs_apply_formats_persists_mounts_and_verifies_profiles(
    monkeypatch: pytest.MonkeyPatch,
    btrfs_host: tuple[Path, Path],
) -> None:
    fstab, mount_root = btrfs_host
    commands: list[tuple[str, ...]] = []

    def checked(*args: str, **_kwargs: Any) -> str:
        if args[0] == "blkid":
            return f"TYPE=btrfs\nUUID={FS_UUID}\nLABEL=family\n"
        if args[:2] == ("findmnt", "--verify"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(native_btrfs, "_run_checked", checked)
    monkeypatch.setattr(
        native_btrfs,
        "_run_mutating",
        lambda *args, **_kwargs: commands.append(args),
    )
    verified: list[dict[str, Any]] = []
    monkeypatch.setattr(native_btrfs, "_verify_mount", lambda **kwargs: verified.append(kwargs))
    plan = native_btrfs.plan_btrfs_raid1(_desired(), fstab_path=fstab, mount_root=mount_root)
    result = native_btrfs.apply_btrfs_raid1(
        _desired(), plan["planId"], fstab_path=fstab, mount_root=mount_root
    )

    assert commands == [
        (
            "mkfs.btrfs",
            "--data",
            "raid1",
            "--metadata",
            "raid1",
            "--label",
            "family",
            "/dev/sdb",
            "/dev/sdc",
        ),
        ("systemctl", "daemon-reload"),
        ("btrfs", "device", "scan", "/dev/sdb", "/dev/sdc"),
        ("mount", str(mount_root / "family")),
    ]
    assert "--force" not in commands[0] and "-f" not in commands[0]
    assert verified == [
        {
            "devices": ["/dev/sdb", "/dev/sdc"],
            "mountpoint": str(mount_root / "family"),
            "filesystem_uuid": FS_UUID,
        }
    ]
    assert result["filesystem"]["dataProfile"] == "raid1"
    assert fstab.read_text(encoding="utf-8") == (
        "# BEGIN ECHO OS MANAGED BTRFS\n"
        f"UUID={FS_UUID} {mount_root / 'family'} btrfs "
        "defaults,nofail,x-systemd.device-timeout=30s 0 0\n"
        "# END ECHO OS MANAGED BTRFS\n"
    )


def test_btrfs_stale_hardware_identity_is_rejected_before_format(
    monkeypatch: pytest.MonkeyPatch,
    btrfs_host: tuple[Path, Path],
) -> None:
    fstab, mount_root = btrfs_host
    plan = native_btrfs.plan_btrfs_raid1(_desired(), fstab_path=fstab, mount_root=mount_root)
    changed = _identities()
    changed[0] = {**changed[0], "serial": "replacement"}
    monkeypatch.setattr(native_btrfs, "inspect_blank_whole_disks", lambda _devices: changed)
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        native_btrfs, "_run_mutating", lambda *args, **_kwargs: commands.append(args)
    )

    with pytest.raises(ValueError, match="stale"):
        native_btrfs.apply_btrfs_raid1(
            _desired(), plan["planId"], fstab_path=fstab, mount_root=mount_root
        )

    assert commands == []


def test_btrfs_rejects_mismatched_member_uuid_and_rolls_back_both_disks(
    monkeypatch: pytest.MonkeyPatch,
    btrfs_host: tuple[Path, Path],
) -> None:
    fstab, mount_root = btrfs_host
    original = b"proc /proc proc defaults 0 0\n"
    fstab.write_bytes(original)
    uuids = iter([FS_UUID, "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"])

    def checked(*args: str, **_kwargs: Any) -> str:
        if args[0] == "blkid":
            return f"TYPE=btrfs\nUUID={next(uuids)}\nLABEL=family\n"
        raise AssertionError(args)

    monkeypatch.setattr(native_btrfs, "_run_checked", checked)
    monkeypatch.setattr(native_btrfs, "_run_mutating", lambda *_args, **_kwargs: None)
    cleared: list[list[str]] = []
    monkeypatch.setattr(
        native_btrfs,
        "_clear_new_filesystems",
        lambda devices: cleared.append(devices) or True,
    )
    plan = native_btrfs.plan_btrfs_raid1(_desired(), fstab_path=fstab, mount_root=mount_root)

    with pytest.raises(OSError, match="new filesystem state was removed"):
        native_btrfs.apply_btrfs_raid1(
            _desired(), plan["planId"], fstab_path=fstab, mount_root=mount_root
        )

    assert cleared == [["/dev/sdb", "/dev/sdc"]]
    assert fstab.read_bytes() == original
    assert not (mount_root / "family").exists()


def test_btrfs_mount_failure_restores_fstab_and_clears_both_disks(
    monkeypatch: pytest.MonkeyPatch,
    btrfs_host: tuple[Path, Path],
) -> None:
    fstab, mount_root = btrfs_host
    original = b"proc /proc proc defaults 0 0\n"
    fstab.write_bytes(original)
    commands: list[tuple[str, ...]] = []

    def checked(*args: str, **_kwargs: Any) -> str:
        if args[0] == "blkid":
            return f"TYPE=btrfs\nUUID={FS_UUID}\nLABEL=family\n"
        if args[:2] == ("findmnt", "--verify"):
            return ""
        raise AssertionError(args)

    def mutate(*args: str, **_kwargs: Any) -> None:
        commands.append(args)
        if args[0] == "mount":
            raise OSError("mount failed")

    monkeypatch.setattr(native_btrfs, "_run_checked", checked)
    monkeypatch.setattr(native_btrfs, "_run_mutating", mutate)
    monkeypatch.setattr(
        native_btrfs,
        "_run",
        lambda *_args, **_kwargs: type(
            "Completed", (), {"returncode": 1, "stdout": "", "stderr": ""}
        )(),
    )
    cleared: list[list[str]] = []
    monkeypatch.setattr(
        native_btrfs,
        "_clear_new_filesystems",
        lambda devices: cleared.append(devices) or True,
    )
    plan = native_btrfs.plan_btrfs_raid1(_desired(), fstab_path=fstab, mount_root=mount_root)

    with pytest.raises(OSError, match="new filesystem state was removed"):
        native_btrfs.apply_btrfs_raid1(
            _desired(), plan["planId"], fstab_path=fstab, mount_root=mount_root
        )

    assert fstab.read_bytes() == original
    assert cleared == [["/dev/sdb", "/dev/sdc"]]
    assert commands.count(("systemctl", "daemon-reload")) == 2
    assert not (mount_root / "family").exists()


def test_btrfs_profile_verifier_requires_raid1_for_data_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_btrfs,
        "_run_checked",
        lambda *_args: "Data, RAID1: total=1, used=1\nMetadata, RAID1: total=1, used=1\n",
    )
    native_btrfs._verify_profiles(mountpoint="/data/family")

    monkeypatch.setattr(
        native_btrfs,
        "_run_checked",
        lambda *_args: "Data,single: total=1, used=1\nMetadata,RAID1: total=1, used=1\n",
    )
    with pytest.raises(OSError, match="profiles"):
        native_btrfs._verify_profiles(mountpoint="/data/family")


def test_btrfs_candidates_do_not_require_zfs_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native_btrfs, "_require_tools", lambda: None)
    monkeypatch.setattr(
        native_btrfs,
        "_run_checked",
        lambda *_args: json.dumps(
            {
                "blockdevices": [
                    {"path": "/dev/sda", "type": "disk"},
                    {"path": "/dev/sdb", "type": "disk"},
                    {"path": "/dev/sdb1", "type": "part"},
                ]
            }
        ),
    )

    def inspect(devices: list[str]) -> list[dict[str, Any]]:
        if devices == ["/dev/sda"]:
            raise ValueError("system disk is not blank")
        return [_identities()[0]]

    monkeypatch.setattr(native_btrfs, "inspect_blank_whole_disks", inspect)

    assert native_btrfs.btrfs_raid1_candidates() == [_identities()[0]]


def test_native_alias_binds_btrfs_creation_to_destructive_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "b" * 64
    current_plan = {"planId": plan_id, "operation": "createAndMount", "requiresApproval": True}
    approvals: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approvals.append(kwargs)

    class Audit:
        def record(self, **_kwargs: Any) -> None:
            pass

    monkeypatch.setattr(native_storage, "plan_btrfs_raid1", lambda _desired: current_plan)
    monkeypatch.setattr(
        native_storage,
        "apply_btrfs_raid1",
        lambda _desired, _plan_id: {**current_plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))

    response = TestClient(app).post(
        "/api/appliance/omv/volumes/btrfs-raid1/apply",
        json={"desired": _desired(), "planId": plan_id},
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approvals[0]["action"] == "omv.btrfs-raid1.create"


def test_native_alias_exposes_btrfs_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    expected = _identities()
    monkeypatch.setattr(native_storage, "btrfs_raid1_candidates", lambda: expected)
    app = FastAPI()
    app.include_router(create_omv_alias_router())

    response = TestClient(app).get("/api/appliance/omv/volumes/btrfs-raid1/candidates")

    assert response.status_code == 200
    assert response.json() == {"devices": expected, "readOnly": True, "source": "native"}


def test_native_status_hides_btrfs_without_btrfs_progs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        native_storage.shutil,
        "which",
        lambda binary: None if binary in {"btrfs", "mkfs.btrfs"} else f"/usr/bin/{binary}",
    )
    monkeypatch.setattr(native_storage, "_native_quota_tools_available", lambda: True)
    monkeypatch.setattr(native_storage, "_native_nut_usb_driver_available", lambda: True)
    monkeypatch.setattr(native_storage, "_native_mdraid_check_scheduler_available", lambda: True)

    capabilities = native_storage.status()["capabilities"]

    assert "storage.volume.btrfs-raid1.create-mount.v1" not in capabilities
    assert "storage.volume.btrfs.scrub.start.v1" not in capabilities
    assert "storage.pool.zfs-mirror.create.v1" in capabilities
