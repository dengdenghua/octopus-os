from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import disk_idle_policy as policy
from appliance.native_storage_routes import create_omv_alias_router


def _tools(tmp_path: Path) -> tuple[Path, Path, Path]:
    lsblk = tmp_path / "lsblk"
    hdparm = tmp_path / "hdparm"
    service = tmp_path / "echo-disk-idle.service"
    for path in (lsblk, hdparm, service):
        path.write_text("trusted", encoding="utf-8")
    return lsblk, hdparm, service


def _disk(
    path: str,
    *,
    transport: str = "sata",
    rotational: int = 1,
    removable: int = 0,
    serial: str = "SERIAL-1",
) -> dict[str, Any]:
    return {
        "path": path,
        "type": "disk",
        "size": 4_000_000_000_000,
        "rota": rotational,
        "rm": removable,
        "tran": transport,
        "model": "NAS HDD",
        "serial": serial,
        "wwn": "",
    }


def _runner(records: list[dict[str, Any]], calls: list[list[str]]):
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert kwargs["check"] is False
        assert kwargs.get("shell") is None
        if argv[0].endswith("lsblk"):
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps({"blockdevices": records}),
                "",
            )
        return subprocess.CompletedProcess(argv, 0, "setting standby timer", "")

    return run


def test_inventory_keeps_only_stable_internal_rotational_ata_disks(tmp_path: Path) -> None:
    lsblk, _hdparm, _service = _tools(tmp_path)
    records = [
        _disk("/dev/sda"),
        _disk("/dev/sdb", transport="usb", serial="USB"),
        _disk("/dev/sdc", removable=1, serial="REMOVABLE"),
        _disk("/dev/nvme0n1", transport="nvme", rotational=0, serial="NVME"),
        _disk("/dev/sdd", serial=""),
    ]

    devices = policy.eligible_devices(
        lsblk=lsblk,
        runner=_runner(records, []),
    )

    assert [item["devicefile"] for item in devices] == ["/dev/sda"]
    assert devices[0]["identityHash"]
    assert "serial" not in devices[0]


def test_status_defaults_disabled_and_reports_bounded_choices(tmp_path: Path) -> None:
    lsblk, _hdparm, service = _tools(tmp_path)
    result = policy.policy_status(
        tmp_path / "policy.json",
        service_path=service,
        lsblk=lsblk,
        trusted_uid=tmp_path.stat().st_uid,
        runner=_runner([_disk("/dev/sda")], []),
    )

    assert result["enabled"] is False
    assert result["idleMinutes"] == 0
    assert result["allowedIdleMinutes"] == [0, 30, 60, 120, 240]
    assert result["eligibleDeviceCount"] == 1
    assert result["hardwareVerification"] == "commandAcceptanceOnly"


def test_plan_rejects_enable_without_boot_service_or_eligible_disk(tmp_path: Path) -> None:
    lsblk, hdparm, service = _tools(tmp_path)
    service.unlink()
    desired = {"schema": policy.DESIRED_SCHEMA, "idleMinutes": 30}

    with pytest.raises(OSError, match="boot service"):
        policy.plan_policy(
            desired,
            path=tmp_path / "policy.json",
            service_path=service,
            lsblk=lsblk,
            hdparm=hdparm,
            trusted_uid=tmp_path.stat().st_uid,
            runner=_runner([_disk("/dev/sda")], []),
        )

    service.write_text("trusted", encoding="utf-8")
    with pytest.raises(policy.DiskIdlePolicyError, match="no eligible"):
        policy.plan_policy(
            desired,
            path=tmp_path / "policy.json",
            service_path=service,
            lsblk=lsblk,
            hdparm=hdparm,
            trusted_uid=tmp_path.stat().st_uid,
            runner=_runner([_disk("/dev/nvme0n1", transport="nvme", rotational=0)], []),
        )


def test_apply_binds_devices_sets_hardware_and_persists_policy(tmp_path: Path) -> None:
    lsblk, hdparm, service = _tools(tmp_path)
    path = tmp_path / "policy.json"
    calls: list[list[str]] = []
    runner = _runner([_disk("/dev/sda")], calls)
    desired = {"schema": policy.DESIRED_SCHEMA, "idleMinutes": 60}
    plan = policy.plan_policy(
        desired,
        path=path,
        service_path=service,
        lsblk=lsblk,
        hdparm=hdparm,
        trusted_uid=tmp_path.stat().st_uid,
        runner=runner,
    )

    result = policy.apply_policy(
        desired,
        plan["planId"],
        path=path,
        service_path=service,
        lock_path=tmp_path / "policy.lock",
        lsblk=lsblk,
        hdparm=hdparm,
        trusted_uid=tmp_path.stat().st_uid,
        runner=runner,
    )

    assert result["verified"] is True
    assert result["hardwareUpdated"] == 1
    assert json.loads(path.read_text(encoding="utf-8"))["idleMinutes"] == 60
    assert [str(hdparm), "-S", "242", "/dev/sda"] in calls
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_apply_rejects_a_device_swap_after_preview(tmp_path: Path) -> None:
    lsblk, hdparm, service = _tools(tmp_path)
    path = tmp_path / "policy.json"
    desired = {"schema": policy.DESIRED_SCHEMA, "idleMinutes": 30}
    first = _runner([_disk("/dev/sda", serial="FIRST")], [])
    plan = policy.plan_policy(
        desired,
        path=path,
        service_path=service,
        lsblk=lsblk,
        hdparm=hdparm,
        trusted_uid=tmp_path.stat().st_uid,
        runner=first,
    )

    with pytest.raises(policy.DiskIdlePolicyError, match="stale"):
        policy.apply_policy(
            desired,
            plan["planId"],
            path=path,
            service_path=service,
            lock_path=tmp_path / "policy.lock",
            lsblk=lsblk,
            hdparm=hdparm,
            trusted_uid=tmp_path.stat().st_uid,
            runner=_runner([_disk("/dev/sda", serial="SECOND")], []),
        )
    assert not path.exists()


def test_command_failure_restores_previous_hardware_timer_and_policy(tmp_path: Path) -> None:
    lsblk, hdparm, service = _tools(tmp_path)
    path = tmp_path / "policy.json"
    records = [_disk("/dev/sda", serial="A"), _disk("/dev/sdb", serial="B")]
    calls: list[list[str]] = []
    failures = 0

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal failures
        calls.append(argv)
        if argv[0] == str(lsblk):
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps({"blockdevices": records}),
                "",
            )
        if argv[-1] == "/dev/sdb" and argv[2] == "241":
            failures += 1
            return subprocess.CompletedProcess(argv, 1, "", "rejected")
        return subprocess.CompletedProcess(argv, 0, "accepted", "")

    desired = {"schema": policy.DESIRED_SCHEMA, "idleMinutes": 30}
    plan = policy.plan_policy(
        desired,
        path=path,
        service_path=service,
        lsblk=lsblk,
        hdparm=hdparm,
        trusted_uid=tmp_path.stat().st_uid,
        runner=run,
    )

    with pytest.raises(OSError, match="rolled back"):
        policy.apply_policy(
            desired,
            plan["planId"],
            path=path,
            service_path=service,
            lock_path=tmp_path / "policy.lock",
            lsblk=lsblk,
            hdparm=hdparm,
            trusted_uid=tmp_path.stat().st_uid,
            runner=run,
        )

    assert failures == 1
    assert [str(hdparm), "-S", "0", "/dev/sda"] in calls
    assert [str(hdparm), "-S", "0", "/dev/sdb"] in calls
    assert not path.exists()


def test_systemd_and_install_paths_keep_the_boot_apply_bounded() -> None:
    repository = Path(__file__).resolve().parents[2]
    service = (repository / "deploy/appliance/systemd/echo-disk-idle.service").read_text(
        encoding="utf-8"
    )
    provision = (repository / "deploy/provision/base/provision-lib.sh").read_text(
        encoding="utf-8"
    )

    assert "deploy.appliance.disk_idle_runner" in service
    assert "NoNewPrivileges=true" in service
    assert "PrivateNetwork=true" in service
    assert "ProtectSystem=strict" in service
    assert "CapabilityBoundingSet=CAP_SYS_RAWIO" in service
    assert "CAP_SYS_ADMIN" not in service
    assert "hdparm" in provision
    assert "systemctl enable --now echo-disk-idle.service" in provision


def test_boot_apply_fails_closed_without_config_and_emits_only_counts(tmp_path: Path) -> None:
    lsblk, hdparm, _service = _tools(tmp_path)
    assert policy.apply_configured_policy(
        path=tmp_path / "policy.json",
        lock_path=tmp_path / "policy.lock",
        lsblk=lsblk,
        hdparm=hdparm,
        trusted_uid=tmp_path.stat().st_uid,
        runner=_runner([_disk("/dev/sda")], []),
    ) == {"outcome": "disabled", "updated": 0, "errors": 0}


def test_route_uses_exact_plan_bound_approval_without_hardware_identity_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id = "d" * 64
    plan = {
        "planId": plan_id,
        "operation": "set",
        "requiresApproval": True,
        "devices": [{"devicefile": "/dev/sda", "identityHash": "e" * 64}],
    }
    approval_calls: list[dict[str, Any]] = []
    audit_calls: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approval_calls.append(kwargs)

    class Audit:
        def record(self, **kwargs: Any) -> None:
            audit_calls.append(kwargs)

    monkeypatch.setattr(policy, "plan_policy", lambda _desired: plan)
    monkeypatch.setattr(
        policy,
        "apply_policy",
        lambda _desired, _plan_id: {**plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))

    response = TestClient(app).post(
        "/api/appliance/omv/disks/idle/apply",
        json={
            "desired": {"schema": policy.DESIRED_SCHEMA, "idleMinutes": 60},
            "planId": plan_id,
        },
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approval_calls[0]["action"] == "storage.disk.idle.configure"
    assert {item["action"] for item in audit_calls} == {"storage.disk.idle.configure"}
    metadata = audit_calls[0]["metadata"]
    assert metadata["idleMinutes"] == 60
    assert metadata["scope"] == "stableInternalRotationalAtaSataWholeDisksOnly"
    assert metadata["hardwareVerification"] == "commandAcceptanceOnly"
    assert metadata["operation"] == "set"
    assert metadata["source"] == "native"
    assert "identityHash" not in json.dumps(metadata)


def test_route_rejects_boolean_idle_minutes() -> None:
    app = FastAPI()
    app.include_router(create_omv_alias_router())
    response = TestClient(app).post(
        "/api/appliance/omv/disks/idle/plan",
        json={"schema": policy.DESIRED_SCHEMA, "idleMinutes": False},
    )
    assert response.status_code == 422
