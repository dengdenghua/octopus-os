from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import native_ext4, native_storage
from appliance.native_storage_routes import create_omv_alias_router
from appliance.omv_protocol import validate_ext4_volume_desired

ARRAY_UUID = "11111111:22222222:33333333:44444444"
FS_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.ext4-volume-desired.v1",
        "arrayUuid": ARRAY_UUID,
        "name": "family",
        "dataLossConfirmed": True,
        **overrides,
    }


def _array(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "family",
        "devicefile": "/dev/md/echo-family",
        "uuid": ARRAY_UUID,
        "level": "raid1",
        "devices": ["/dev/sdb", "/dev/sdc"],
        "filesystem": None,
        **overrides,
    }


@pytest.fixture
def ext4_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    etc = tmp_path / "etc"
    data = tmp_path / "data"
    etc.mkdir()
    data.mkdir()
    monkeypatch.setattr(native_ext4, "_LOCK_PATH", tmp_path / "ext4.lock")
    monkeypatch.setattr(native_ext4, "_require_tools", lambda: None)
    monkeypatch.setattr(native_ext4, "managed_mdraid1_arrays", lambda **_kwargs: [_array()])
    monkeypatch.setattr(native_ext4, "_has_signatures", lambda _device: False)
    monkeypatch.setattr(native_ext4, "_is_mounted", lambda **_kwargs: False)
    return etc / "fstab", data


@pytest.mark.parametrize(
    "payload",
    [
        _desired(dataLossConfirmed=False),
        _desired(name="Family"),
        _desired(name="this-name-is-too-long"),
        _desired(arrayUuid="not-an-md-uuid"),
        {**_desired(), "filesystem": "xfs"},
    ],
)
def test_ext4_protocol_rejects_unsafe_or_expanded_state(payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        validate_ext4_volume_desired(payload)


def test_ext4_plan_binds_managed_array_fstab_and_absent_mountpoint(
    ext4_host: tuple[Path, Path],
) -> None:
    fstab, mount_root = ext4_host
    plan = native_ext4.plan_ext4_volume(_desired(), fstab_path=fstab, mount_root=mount_root)

    assert plan["schema"] == "echo.omv.ext4-volume-plan.v1"
    assert plan["operation"] == "createAndMount"
    assert plan["array"] == _array()
    assert plan["mountpoint"] == str(mount_root / "family")
    assert plan["requiresApproval"] is True
    assert plan["safety"]["force"] is False
    assert plan["safety"]["source"] == "healthyBlankEchoManagedMdRaid1Only"
    assert len(plan["planId"]) == 64


def test_ext4_apply_formats_persists_by_uuid_mounts_and_verifies(
    monkeypatch: pytest.MonkeyPatch,
    ext4_host: tuple[Path, Path],
) -> None:
    fstab, mount_root = ext4_host
    commands: list[tuple[str, ...]] = []

    def checked(*args: str, **_kwargs: Any) -> str:
        if args[:3] == ("blkid", "--probe", "--output"):
            return f"TYPE=ext4\nUUID={FS_UUID}\nLABEL=family\n"
        if args[:2] == ("findmnt", "--verify"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(native_ext4, "_run_checked", checked)
    monkeypatch.setattr(
        native_ext4,
        "_run_mutating",
        lambda *args, **_kwargs: commands.append(args),
    )
    verified: list[dict[str, str]] = []
    monkeypatch.setattr(
        native_ext4,
        "_verify_mount",
        lambda **kwargs: verified.append(kwargs),
    )
    plan = native_ext4.plan_ext4_volume(_desired(), fstab_path=fstab, mount_root=mount_root)
    result = native_ext4.apply_ext4_volume(
        _desired(), plan["planId"], fstab_path=fstab, mount_root=mount_root
    )

    assert commands == [
        ("mkfs.ext4", "-L", "family", "-m", "0", "/dev/md/echo-family"),
        ("systemctl", "daemon-reload"),
        ("mount", str(mount_root / "family")),
    ]
    assert "-F" not in commands[0]
    assert verified == [
        {
            "target": "/dev/md/echo-family",
            "mountpoint": str(mount_root / "family"),
            "filesystem_uuid": FS_UUID,
        }
    ]
    assert result["filesystem"]["uuid"] == FS_UUID
    assert result["filesystem"]["readOnly"] is False
    assert fstab.read_text(encoding="utf-8") == (
        "# BEGIN ECHO OS MANAGED EXT4\n"
        f"UUID={FS_UUID} {mount_root / 'family'} ext4 "
        "defaults,nofail,x-systemd.device-timeout=30s 0 2\n"
        "# END ECHO OS MANAGED EXT4\n"
    )


def test_ext4_stale_array_identity_is_rejected_before_format(
    monkeypatch: pytest.MonkeyPatch,
    ext4_host: tuple[Path, Path],
) -> None:
    fstab, mount_root = ext4_host
    plan = native_ext4.plan_ext4_volume(_desired(), fstab_path=fstab, mount_root=mount_root)
    monkeypatch.setattr(
        native_ext4,
        "managed_mdraid1_arrays",
        lambda **_kwargs: [_array(devices=["/dev/sdd", "/dev/sde"])],
    )
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        native_ext4,
        "_run_mutating",
        lambda *args, **_kwargs: commands.append(args),
    )

    with pytest.raises(ValueError, match="stale"):
        native_ext4.apply_ext4_volume(
            _desired(), plan["planId"], fstab_path=fstab, mount_root=mount_root
        )

    assert commands == []


def test_ext4_mount_failure_restores_fstab_clears_signature_and_directory(
    monkeypatch: pytest.MonkeyPatch,
    ext4_host: tuple[Path, Path],
) -> None:
    fstab, mount_root = ext4_host
    original = b"proc /proc proc defaults 0 0\n"
    fstab.write_bytes(original)
    commands: list[tuple[str, ...]] = []

    def checked(*args: str, **_kwargs: Any) -> str:
        if args[0] == "blkid":
            return f"TYPE=ext4\nUUID={FS_UUID}\nLABEL=family\n"
        if args[:2] == ("findmnt", "--verify"):
            return ""
        raise AssertionError(args)

    def mutate(*args: str, **_kwargs: Any) -> None:
        commands.append(args)
        if args[0] == "mount":
            raise OSError("mount failed")

    monkeypatch.setattr(native_ext4, "_run_checked", checked)
    monkeypatch.setattr(native_ext4, "_run_mutating", mutate)
    cleared: list[str] = []
    monkeypatch.setattr(
        native_ext4,
        "_clear_new_filesystem",
        lambda device: cleared.append(device) or True,
    )
    plan = native_ext4.plan_ext4_volume(_desired(), fstab_path=fstab, mount_root=mount_root)

    with pytest.raises(OSError, match="new filesystem state was removed"):
        native_ext4.apply_ext4_volume(
            _desired(), plan["planId"], fstab_path=fstab, mount_root=mount_root
        )

    assert fstab.read_bytes() == original
    assert cleared == ["/dev/md/echo-family"]
    assert not (mount_root / "family").exists()
    assert commands.count(("systemctl", "daemon-reload")) == 2


def test_ext4_mount_timeout_detects_and_unmounts_before_clearing(
    monkeypatch: pytest.MonkeyPatch,
    ext4_host: tuple[Path, Path],
) -> None:
    fstab, mount_root = ext4_host
    mounted = False
    events: list[str] = []

    def checked(*args: str, **_kwargs: Any) -> str:
        if args[0] == "blkid":
            return f"TYPE=ext4\nUUID={FS_UUID}\nLABEL=family\n"
        if args[:2] == ("findmnt", "--verify"):
            return ""
        raise AssertionError(args)

    def mutate(*args: str, **_kwargs: Any) -> None:
        nonlocal mounted
        if args[0] == "mount":
            events.append("mount-timeout")
            mounted = True
            raise OSError("timed out after kernel mount")
        if args[0] == "umount":
            events.append("umount")
            mounted = False

    def is_mounted(**kwargs: str) -> bool:
        if "mountpoint" in kwargs:
            return mounted
        return False

    monkeypatch.setattr(native_ext4, "_run_checked", checked)
    monkeypatch.setattr(native_ext4, "_run_mutating", mutate)
    monkeypatch.setattr(native_ext4, "_is_mounted", is_mounted)
    monkeypatch.setattr(
        native_ext4,
        "_clear_new_filesystem",
        lambda _device: events.append("clear") or True,
    )
    plan = native_ext4.plan_ext4_volume(_desired(), fstab_path=fstab, mount_root=mount_root)

    with pytest.raises(OSError, match="new filesystem state was removed"):
        native_ext4.apply_ext4_volume(
            _desired(), plan["planId"], fstab_path=fstab, mount_root=mount_root
        )

    assert events == ["mount-timeout", "umount", "clear"]


def test_ext4_candidates_hide_signed_or_mounted_arrays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native_ext4, "_require_tools", lambda: None)
    arrays = [
        _array(),
        _array(
            name="backup",
            devicefile="/dev/md/echo-backup",
            uuid="aaaaaaaa:bbbbbbbb:cccccccc:dddddddd",
        ),
    ]
    monkeypatch.setattr(native_ext4, "managed_mdraid1_arrays", lambda **_kwargs: arrays)
    monkeypatch.setattr(
        native_ext4,
        "_has_signatures",
        lambda device: device == "/dev/md/echo-family",
    )
    monkeypatch.setattr(native_ext4, "_is_mounted", lambda **_kwargs: False)

    assert native_ext4.ext4_volume_candidates() == [arrays[1]]


def test_ext4_blkid_parser_requires_exact_type_label_and_uuid() -> None:
    valid = f"TYPE=ext4\nUUID={FS_UUID}\nLABEL=family\n"
    assert native_ext4._parse_blkid_export(valid, expected_label="family")["uuid"] == FS_UUID
    with pytest.raises(OSError, match="identity"):
        native_ext4._parse_blkid_export(
            valid.replace("TYPE=ext4", "TYPE=xfs"), expected_label="family"
        )


def test_native_alias_binds_ext4_creation_to_destructive_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "e" * 64
    current_plan = {"planId": plan_id, "operation": "createAndMount", "requiresApproval": True}
    approval_calls: list[dict[str, Any]] = []
    audit_calls: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approval_calls.append(kwargs)

    class Audit:
        def record(self, **kwargs: Any) -> None:
            audit_calls.append(kwargs)

    monkeypatch.setattr(native_storage, "plan_ext4_volume", lambda _desired: current_plan)
    monkeypatch.setattr(
        native_storage,
        "apply_ext4_volume",
        lambda _desired, _plan_id: {**current_plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))

    response = TestClient(app).post(
        "/api/appliance/omv/volumes/ext4/apply",
        json={"desired": _desired(), "planId": plan_id},
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approval_calls[0]["action"] == "omv.ext4-volume.create"
    assert {entry["action"] for entry in audit_calls} == {"omv.ext4-volume.create"}


def test_native_alias_exposes_server_validated_ext4_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    expected = [_array()]
    monkeypatch.setattr(native_storage, "ext4_volume_candidates", lambda: expected)
    app = FastAPI()
    app.include_router(create_omv_alias_router())

    response = TestClient(app).get("/api/appliance/omv/volumes/ext4/candidates")

    assert response.status_code == 200
    assert response.json() == {"arrays": expected, "readOnly": True, "source": "native"}
