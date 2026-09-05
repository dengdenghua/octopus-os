from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import native_mdraid, native_storage
from appliance.native_storage_routes import create_omv_alias_router
from appliance.omv_protocol import validate_mdraid1_desired


def _desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": "echo.omv.mdraid1-desired.v1",
        "name": "family",
        "devices": ["/dev/sdb", "/dev/sdc"],
        "dataLossConfirmed": True,
        **overrides,
    }


def _identities() -> list[dict[str, Any]]:
    return [
        {
            "devicefile": "/dev/sdb",
            "sizeBytes": 8 * 1024**3,
            "serial": "disk-b",
            "wwn": None,
            "model": "QEMU HARDDISK",
        },
        {
            "devicefile": "/dev/sdc",
            "sizeBytes": 8 * 1024**3,
            "serial": "disk-c",
            "wwn": None,
            "model": "QEMU HARDDISK",
        },
    ]


@pytest.fixture
def mdraid_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    config_dir = tmp_path / "mdadm"
    config_dir.mkdir()
    monkeypatch.setattr(native_mdraid, "_LOCK_PATH", tmp_path / "mdraid.lock")
    monkeypatch.setattr(native_mdraid, "_require_tools", lambda: None)
    monkeypatch.setattr(native_mdraid, "inspect_blank_whole_disks", lambda _devices: _identities())
    monkeypatch.setattr(native_mdraid, "_scan_arrays", lambda: [])
    monkeypatch.setattr(native_mdraid.os.path, "lexists", lambda _path: False)
    return config_dir / "mdadm.conf"


@pytest.mark.parametrize(
    "payload",
    [
        _desired(dataLossConfirmed=False),
        _desired(name="Family"),
        _desired(devices=["/dev/sdb", "/dev/sdb"]),
        {**_desired(), "force": True},
    ],
)
def test_mdraid_protocol_rejects_unsafe_or_expanded_desired_state(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        validate_mdraid1_desired(payload)


def test_mdraid_plan_binds_blank_disk_identity_and_persistence_boundary(
    mdraid_host: Path,
) -> None:
    plan = native_mdraid.plan_mdraid1(_desired(), config_path=mdraid_host)

    assert plan["schema"] == "echo.omv.mdraid1-plan.v1"
    assert plan["operation"] == "create"
    assert plan["target"] == "/dev/md/echo-family"
    assert plan["devices"] == _identities()
    assert plan["usableBytes"] == 8 * 1024**3
    assert plan["requiresApproval"] is True
    assert plan["filesystemCreated"] is False
    assert plan["safety"]["degradedStart"] is False
    assert len(plan["planId"]) == 64


def test_mdraid_apply_uses_exact_non_force_command_persists_uuid_and_verifies(
    monkeypatch: pytest.MonkeyPatch,
    mdraid_host: Path,
) -> None:
    commands: list[tuple[str, ...]] = []

    def mutate(*args: str, **_kwargs: Any) -> None:
        commands.append(args)

    array = {
        "name": "family",
        "devicefile": "/dev/md/echo-family",
        "uuid": "11111111:22222222:33333333:44444444",
        "level": "raid1",
        "devices": ["/dev/sdb", "/dev/sdc"],
        "filesystem": None,
    }
    monkeypatch.setattr(native_mdraid, "_run_mutating", mutate)
    monkeypatch.setattr(native_mdraid, "_verify_created_array", lambda _plan: array)

    plan = native_mdraid.plan_mdraid1(_desired(), config_path=mdraid_host)
    result = native_mdraid.apply_mdraid1(_desired(), plan["planId"], config_path=mdraid_host)

    assert commands == [
        (
            "mdadm",
            "--create",
            "/dev/md/echo-family",
            "--metadata=1.2",
            "--level=1",
            "--raid-devices=2",
            "--name=echo-family",
            "--bitmap=internal",
            "/dev/sdb",
            "/dev/sdc",
        ),
        ("update-initramfs", "-u"),
    ]
    assert "--force" not in commands[0]
    assert result["verified"] is True
    assert result["array"] == array
    assert mdraid_host.read_text(encoding="utf-8") == (
        "# BEGIN ECHO OS MANAGED MDRAID\n"
        "ARRAY /dev/md/echo-family UUID=11111111:22222222:33333333:44444444 metadata=1.2\n"
        "# END ECHO OS MANAGED MDRAID\n"
    )


def test_mdraid_stale_plan_is_rejected_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    mdraid_host: Path,
) -> None:
    plan = native_mdraid.plan_mdraid1(_desired(), config_path=mdraid_host)
    monkeypatch.setattr(
        native_mdraid,
        "inspect_blank_whole_disks",
        lambda _devices: [{**item, "serial": f"new-{item['serial']}"} for item in _identities()],
    )
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        native_mdraid,
        "_run_mutating",
        lambda *args, **_kwargs: commands.append(args),
    )

    with pytest.raises(ValueError, match="stale"):
        native_mdraid.apply_mdraid1(_desired(), plan["planId"], config_path=mdraid_host)

    assert commands == []


def test_mdraid_plan_rejects_manually_registered_target_before_mutation(
    mdraid_host: Path,
) -> None:
    mdraid_host.write_text(
        "ARRAY /dev/md/echo-family UUID=aaaaaaaa:bbbbbbbb:cccccccc:dddddddd\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="already registered"):
        native_mdraid.plan_mdraid1(_desired(), config_path=mdraid_host)


def test_mdraid_initramfs_failure_restores_config_and_removes_array(
    monkeypatch: pytest.MonkeyPatch,
    mdraid_host: Path,
) -> None:
    original = b"HOMEHOST <system>\n"
    mdraid_host.write_bytes(original)
    refreshes = 0

    def mutate(*args: str, **_kwargs: Any) -> None:
        nonlocal refreshes
        if args == ("update-initramfs", "-u"):
            refreshes += 1
            if refreshes == 1:
                raise OSError("initramfs failed")

    monkeypatch.setattr(native_mdraid, "_run_mutating", mutate)
    monkeypatch.setattr(
        native_mdraid,
        "_verify_created_array",
        lambda _plan: {
            "uuid": "11111111:22222222:33333333:44444444",
        },
    )
    cleaned: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        native_mdraid,
        "_cleanup_partial_array",
        lambda target, devices: cleaned.append((target, devices)) or True,
    )
    plan = native_mdraid.plan_mdraid1(_desired(), config_path=mdraid_host)

    with pytest.raises(OSError, match="new array was removed"):
        native_mdraid.apply_mdraid1(_desired(), plan["planId"], config_path=mdraid_host)

    assert mdraid_host.read_bytes() == original
    assert refreshes == 2
    assert cleaned == [("/dev/md/echo-family", ["/dev/sdb", "/dev/sdc"])]


def test_mdraid_detail_parser_requires_exact_two_device_raid1() -> None:
    plan = {
        "desired": {"name": "family"},
        "target": "/dev/md/echo-family",
        "devices": _identities(),
    }
    valid = "\n".join(
        [
            "MD_LEVEL=raid1",
            "MD_DEVICES=2",
            "MD_METADATA=1.2",
            "MD_UUID=11111111:22222222:33333333:44444444",
            "MD_DEVNAME=echo-family",
            "MD_DEVICE_dev_sdb_DEV=/dev/sdb",
            "MD_DEVICE_dev_sdb_ROLE=0",
            "MD_DEVICE_dev_sdc_DEV=/dev/sdc",
            "MD_DEVICE_dev_sdc_ROLE=1",
        ]
    )

    assert native_mdraid._parse_detail(valid, plan)["uuid"].startswith("11111111:")
    with pytest.raises(OSError, match="planned topology"):
        native_mdraid._parse_detail(valid.replace("MD_LEVEL=raid1", "MD_LEVEL=raid0"), plan)


def test_managed_mdraid_inventory_requires_persisted_healthy_identity(
    monkeypatch: pytest.MonkeyPatch,
    mdraid_host: Path,
) -> None:
    monkeypatch.setattr(native_mdraid.shutil, "which", lambda _binary: "/usr/bin/mdadm")
    mdraid_host.write_text(
        "# BEGIN ECHO OS MANAGED MDRAID\n"
        "ARRAY /dev/md/echo-family UUID=11111111:22222222:33333333:44444444 metadata=1.2\n"
        "# END ECHO OS MANAGED MDRAID\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        native_mdraid,
        "_run",
        lambda *_args, **_kwargs: type("Result", (), {"returncode": 0})(),
    )
    monkeypatch.setattr(
        native_mdraid,
        "_run_checked",
        lambda *_args, **_kwargs: "\n".join(
            [
                "MD_LEVEL=raid1",
                "MD_DEVICES=2",
                "MD_METADATA=1.2",
                "MD_UUID=11111111:22222222:33333333:44444444",
                "MD_DEVNAME=echo-family",
                "MD_DEVICE_dev_sdb_DEV=/dev/sdb",
                "MD_DEVICE_dev_sdb_ROLE=0",
                "MD_DEVICE_dev_sdc_DEV=/dev/sdc",
                "MD_DEVICE_dev_sdc_ROLE=1",
            ]
        ),
    )

    assert native_mdraid.managed_mdraid1_arrays(config_path=mdraid_host) == [
        {
            "name": "family",
            "devicefile": "/dev/md/echo-family",
            "uuid": "11111111:22222222:33333333:44444444",
            "level": "raid1",
            "devices": ["/dev/sdb", "/dev/sdc"],
            "filesystem": None,
        }
    ]


def test_mdraid_candidates_hide_disks_that_fail_the_plan_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native_mdraid, "_require_tools", lambda: None)
    monkeypatch.setattr(
        native_mdraid,
        "_run_checked",
        lambda *_args, **_kwargs: json.dumps(
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
            raise ValueError("system disk")
        return [_identities()[0]]

    monkeypatch.setattr(native_mdraid, "inspect_blank_whole_disks", inspect)

    assert native_mdraid.mdraid1_candidates() == [_identities()[0]]


def test_native_alias_binds_mdraid_creation_to_destructive_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    plan_id = "d" * 64
    current_plan = {"planId": plan_id, "operation": "create", "requiresApproval": True}
    approval_calls: list[dict[str, Any]] = []
    audit_calls: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approval_calls.append(kwargs)

    class Audit:
        def record(self, **kwargs: Any) -> None:
            audit_calls.append(kwargs)

    monkeypatch.setattr(native_storage, "plan_mdraid1", lambda _desired: current_plan)
    monkeypatch.setattr(
        native_storage,
        "apply_mdraid1",
        lambda _desired, _plan_id: {**current_plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))

    response = TestClient(app).post(
        "/api/appliance/omv/arrays/mdraid1/apply",
        json={"desired": _desired(), "planId": plan_id},
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approval_calls[0]["action"] == "omv.mdraid1.create"
    assert {entry["action"] for entry in audit_calls} == {"omv.mdraid1.create"}


def test_native_alias_exposes_server_validated_mdraid_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    expected = [_identities()[0]]
    monkeypatch.setattr(native_storage, "mdraid1_candidates", lambda: expected)
    app = FastAPI()
    app.include_router(create_omv_alias_router())

    response = TestClient(app).get("/api/appliance/omv/arrays/mdraid1/candidates")

    assert response.status_code == 200
    assert response.json() == {"devices": expected, "readOnly": True, "source": "native"}
