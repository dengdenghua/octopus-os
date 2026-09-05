from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import nut_device_config as config
from appliance.native_storage_routes import create_omv_alias_router


def _files(tmp_path: Path) -> tuple[Path, Path]:
    ups = tmp_path / "ups.conf"
    upsd = tmp_path / "upsd.conf"
    ups.write_text("# distro template\nmaxretry = 3\n", encoding="utf-8")
    upsd.write_text("# no listeners configured\n", encoding="utf-8")
    return ups, upsd


def test_status_is_disabled_and_exposes_no_credentials(tmp_path: Path) -> None:
    ups, upsd = _files(tmp_path)
    result = config.config_status(ups_path=ups, upsd_path=upsd, trusted_uid=tmp_path.stat().st_uid)
    assert result["enabled"] is False
    assert result["localOnly"] is True
    assert result["shutdownOwner"] == "echo-ups-shutdown-guard"
    assert "password" not in result


def test_plan_rejects_external_devices_and_non_loopback_server(tmp_path: Path) -> None:
    ups, upsd = _files(tmp_path)
    desired = {"schema": config.DESIRED_SCHEMA, "enabled": True, "driver": "usbhid-ups"}
    ups.write_text("[existing]\ndriver = usbhid-ups\nport = auto\n", encoding="utf-8")
    with pytest.raises(config.NutDeviceConfigError, match="non-Echo"):
        config.plan_config(
            desired, ups_path=ups, upsd_path=upsd, trusted_uid=tmp_path.stat().st_uid
        )

    ups.write_text("# empty\n", encoding="utf-8")
    upsd.write_text("LISTEN 0.0.0.0 3493\n", encoding="utf-8")
    with pytest.raises(config.NutDeviceConfigError, match="non-loopback"):
        config.plan_config(
            desired, ups_path=ups, upsd_path=upsd, trusted_uid=tmp_path.stat().st_uid
        )


def test_plan_allows_only_bundled_usb_drivers(tmp_path: Path) -> None:
    ups, upsd = _files(tmp_path)
    with pytest.raises(config.NutDeviceConfigError, match="not allowed"):
        config.plan_config(
            {"schema": config.DESIRED_SCHEMA, "enabled": True, "driver": "snmp-ups"},
            ups_path=ups,
            upsd_path=upsd,
            trusted_uid=tmp_path.stat().st_uid,
        )


def test_apply_writes_owned_blocks_and_never_configures_upsmon(tmp_path: Path) -> None:
    ups, upsd = _files(tmp_path)
    driver_root = tmp_path / "drivers"
    driver_root.mkdir()
    (driver_root / "usbhid-ups").write_text("binary", encoding="utf-8")
    systemctl = tmp_path / "systemctl"
    systemctl.write_text("binary", encoding="utf-8")
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert "shell" not in kwargs
        return subprocess.CompletedProcess(argv, 0, "", "")

    desired = {"schema": config.DESIRED_SCHEMA, "enabled": True, "driver": "usbhid-ups"}
    plan = config.plan_config(
        desired, ups_path=ups, upsd_path=upsd, trusted_uid=tmp_path.stat().st_uid
    )
    result = config.apply_config(
        desired,
        plan["planId"],
        ups_path=ups,
        upsd_path=upsd,
        trusted_uid=tmp_path.stat().st_uid,
        trusted_gid=tmp_path.stat().st_gid,
        driver_roots=(driver_root,),
        systemctl=systemctl,
        command_runner=run,
        status_reader=lambda: {"devices": [{"name": "echo-ups", "available": True}]},
    )

    assert result["verified"] is True
    assert "[echo-ups]" in ups.read_text(encoding="utf-8")
    assert "driver = usbhid-ups" in ups.read_text(encoding="utf-8")
    assert "LISTEN 127.0.0.1 3493" in upsd.read_text(encoding="utf-8")
    combined = ups.read_text() + upsd.read_text()
    assert "MONITOR" not in combined
    assert "password" not in combined.casefold()
    assert calls == [
        [str(systemctl), "enable", "--now", "nut-driver-enumerator.path"],
        [str(systemctl), "restart", "nut-driver-enumerator.service"],
        [str(systemctl), "enable", "--now", "nut-server.service"],
    ]


def test_failed_service_restart_restores_both_files(tmp_path: Path) -> None:
    ups, upsd = _files(tmp_path)
    before = (ups.read_bytes(), upsd.read_bytes())
    driver_root = tmp_path / "drivers"
    driver_root.mkdir()
    (driver_root / "usbhid-ups").write_text("binary", encoding="utf-8")
    systemctl = tmp_path / "systemctl"
    systemctl.write_text("binary", encoding="utf-8")
    attempts = 0

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal attempts
        attempts += 1
        return subprocess.CompletedProcess(argv, 1 if attempts == 2 else 0, "", "")

    desired = {"schema": config.DESIRED_SCHEMA, "enabled": True, "driver": "usbhid-ups"}
    plan = config.plan_config(
        desired, ups_path=ups, upsd_path=upsd, trusted_uid=tmp_path.stat().st_uid
    )
    with pytest.raises(OSError, match="rolled back"):
        config.apply_config(
            desired,
            plan["planId"],
            ups_path=ups,
            upsd_path=upsd,
            trusted_uid=tmp_path.stat().st_uid,
            trusted_gid=tmp_path.stat().st_gid,
            driver_roots=(driver_root,),
            systemctl=systemctl,
            command_runner=run,
        )
    assert (ups.read_bytes(), upsd.read_bytes()) == before
    if os.name == "posix":
        assert stat.S_IMODE(ups.stat().st_mode) == 0o644


def test_alias_apply_binds_exact_local_ups_approval_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id = "f" * 64
    current_plan = {
        "planId": plan_id,
        "operation": "enable",
        "requiresApproval": True,
    }
    approval_calls: list[dict[str, Any]] = []
    audit_calls: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approval_calls.append(kwargs)

    class Audit:
        def record(self, **kwargs: Any) -> None:
            audit_calls.append(kwargs)

    monkeypatch.setattr(config, "plan_config", lambda _desired: current_plan)
    monkeypatch.setattr(
        config,
        "apply_config",
        lambda _desired, _plan_id: {**current_plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))

    response = TestClient(app).post(
        "/api/appliance/omv/power/ups/config/apply",
        json={
            "desired": {
                "schema": config.DESIRED_SCHEMA,
                "enabled": True,
                "driver": "usbhid-ups",
            },
            "planId": plan_id,
        },
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approval_calls[0]["action"] == "power.ups.local-usb.configure"
    assert {entry["action"] for entry in audit_calls} == {"power.ups.local-usb.configure"}
    assert audit_calls[0]["metadata"]["driver"] == "usbhid-ups"
    assert audit_calls[0]["metadata"]["server"] == "loopbackOnly"
