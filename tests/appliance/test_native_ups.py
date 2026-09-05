from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import native_ups
from appliance.native_storage_routes import create_omv_alias_router


def test_missing_upsc_is_explicitly_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native_ups.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        native_ups.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    result = native_ups.ups_status()

    assert result["configured"] is False
    assert result["available"] is False
    assert result["state"] == "unavailable"
    assert result["code"] == "toolMissing"


def test_ups_inventory_is_local_bounded_and_omits_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(native_ups.shutil, "which", lambda _name: "/usr/bin/upsc")

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(argv))
        assert "shell" not in kwargs
        output = (
            "family-ups\n"
            if argv == ["upsc", "-l"]
            else "\n".join(
                (
                    "ups.status: OB LB DISCHRG",
                    "battery.charge: 18.5",
                    "battery.runtime: 420",
                    "ups.load: 37",
                    "input.voltage: 0",
                    "output.voltage: 229.4",
                    "battery.voltage: 24.8",
                    "ups.temperature: 31",
                    "ups.mfr: APC",
                    "ups.model: Back-UPS",
                    "device.serial: PRIVATE-SERIAL",
                    "driver.parameter.password: PRIVATE-PASSWORD",
                )
            )
        )
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(native_ups.subprocess, "run", run)

    result = native_ups.ups_status()

    assert calls == [("upsc", "-l"), ("upsc", "family-ups")]
    assert result["state"] == "ready"
    device = result["devices"][0]
    assert device["state"] == "lowBattery"
    assert device["statusFlags"] == ["DISCHRG", "LB", "OB"]
    assert device["chargePercent"] == 18.5
    assert device["runtimeSeconds"] == 420
    assert device["model"] == "Back-UPS"
    serialized = json.dumps(result)
    assert "PRIVATE-SERIAL" not in serialized
    assert "PRIVATE-PASSWORD" not in serialized
    assert "device.serial" not in serialized


def test_unsafe_daemon_device_name_is_never_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(native_ups.shutil, "which", lambda _name: "/usr/bin/upsc")

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "ups@remote.example\n", "")

    monkeypatch.setattr(native_ups.subprocess, "run", run)

    result = native_ups.ups_status()

    assert calls == [("upsc", "-l")]
    assert result["available"] is False
    assert result["code"] == "serviceUnavailable"


def test_partial_device_read_is_degraded_without_raw_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native_ups.shutil, "which", lambda _name: "/usr/bin/upsc")

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv == ["upsc", "-l"]:
            return subprocess.CompletedProcess(argv, 0, "first\nsecond\n", "")
        if argv == ["upsc", "first"]:
            return subprocess.CompletedProcess(argv, 0, "ups.status: OL\nbattery.charge: 100\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "PRIVATE-DAEMON-ERROR")

    monkeypatch.setattr(native_ups.subprocess, "run", run)

    result = native_ups.ups_status()

    assert result["state"] == "degraded"
    assert result["code"] == "partialRead"
    assert [device["available"] for device in result["devices"]] == [True, False]
    assert "PRIVATE-DAEMON-ERROR" not in json.dumps(result)


def test_native_alias_exposes_read_only_ups_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {
        "schemaVersion": 1,
        "source": "nut",
        "readOnly": True,
        "configured": False,
        "available": True,
        "state": "notConfigured",
        "code": "emptyInventory",
        "devices": [],
    }
    monkeypatch.setattr("appliance.native_storage_routes.ups_status", lambda: expected)
    app = FastAPI()
    app.include_router(create_omv_alias_router())

    response = TestClient(app).get("/api/appliance/omv/power/ups")

    assert response.status_code == 200
    assert response.json() == expected
