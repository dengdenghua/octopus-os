from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import native_smart
from appliance.native_storage_routes import create_omv_alias_router


def _inventory() -> list[dict[str, Any]]:
    return [
        {
            "devicefile": "/dev/sda",
            "type": "disk",
            "sizeBytes": 1_000_000_000_000,
            "model": "Family Disk",
            "serial": "PRIVATE-SERIAL",
        }
    ]


def _completed(argv: list[str], payload: dict[str, Any], code: int = 0):
    return subprocess.CompletedProcess(argv, code, json.dumps(payload), "PRIVATE-ERROR")


def test_ata_self_test_status_is_bounded_and_hides_serial() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert "shell" not in kwargs
        return _completed(
            argv,
            {
                "ata_smart_data": {
                    "self_test": {
                        "status": {
                            "value": 249,
                            "string": "Self-test routine in progress",
                            "remaining_percent": 90,
                        }
                    }
                },
                "serial_number": "PRIVATE-SERIAL",
            },
        )

    result = native_smart.smart_self_test_status(
        "/dev/sda", inventory_reader=_inventory, runner=run
    )

    assert calls == [["smartctl", "-j", "-c", "-l", "selftest", "/dev/sda"]]
    assert result["state"] == "inProgress"
    assert result["progressPercent"] == 10
    assert "PRIVATE-SERIAL" not in json.dumps(result)


def test_nvme_status_reports_exact_running_test_kind() -> None:
    result = native_smart.smart_self_test_status(
        "/dev/sda",
        inventory_reader=_inventory,
        runner=lambda argv, **_kwargs: _completed(
            argv,
            {
                "nvme_self_test_log": {
                    "current_self_test_operation": {"value": 2},
                    "current_self_test_completion_percent": 37,
                }
            },
        ),
    )

    assert result["state"] == "inProgress"
    assert result["kind"] == "long"
    assert result["progressPercent"] == 37


def test_plan_rejects_non_inventory_device_without_running_smartctl() -> None:
    with pytest.raises(native_smart.SmartSelfTestError, match="enumerated whole disk"):
        native_smart.plan_smart_self_test(
            {
                "schema": "echo.omv.smart-self-test-desired.v1",
                "devicefile": "/dev/sdz",
                "test": "short",
            },
            inventory_reader=_inventory,
            runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("must not run smartctl")
            ),
        )


def test_plan_and_apply_use_only_fixed_short_test_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    statuses = iter([0, 0, 249])

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert "shell" not in kwargs
        if "-t" in argv:
            return _completed(argv, {})
        value = next(statuses)
        return _completed(
            argv,
            {
                "ata_smart_data": {
                    "self_test": {
                        "status": {
                            "value": value,
                            "remaining_percent": 90 if value else 0,
                        }
                    }
                }
            },
        )

    monkeypatch.setattr(native_smart.shutil, "which", lambda _name: "/usr/sbin/smartctl")
    desired = {
        "schema": "echo.omv.smart-self-test-desired.v1",
        "devicefile": "/dev/sda",
        "test": "short",
    }
    plan = native_smart.plan_smart_self_test(desired, inventory_reader=_inventory, runner=run)
    result = native_smart.apply_smart_self_test(
        desired,
        plan["planId"],
        inventory_reader=_inventory,
        runner=run,
    )

    assert result["verified"] is True
    assert result["selfTest"]["state"] == "inProgress"
    assert [call for call in calls if "-t" in call] == [
        ["smartctl", "-j", "-t", "short", "/dev/sda"]
    ]


def test_active_test_blocks_a_second_start() -> None:
    with pytest.raises(native_smart.SmartSelfTestError, match="already"):
        native_smart.plan_smart_self_test(
            {
                "schema": "echo.omv.smart-self-test-desired.v1",
                "devicefile": "/dev/sda",
                "test": "long",
            },
            inventory_reader=_inventory,
            runner=lambda argv, **_kwargs: _completed(
                argv,
                {
                    "nvme_self_test_log": {
                        "current_self_test_operation": {"value": 1},
                        "current_self_test_completion_percent": 20,
                    }
                },
            ),
        )


def test_alias_apply_binds_exact_smart_approval_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id = "b" * 64
    current_plan = {
        "planId": plan_id,
        "operation": "start",
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

    monkeypatch.setattr(
        "appliance.native_storage_routes.plan_smart_self_test",
        lambda _desired: current_plan,
    )
    monkeypatch.setattr(
        "appliance.native_storage_routes.apply_smart_self_test",
        lambda _desired, _plan_id: {**current_plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))

    response = TestClient(app).post(
        "/api/appliance/omv/smart/self-test/apply",
        json={
            "desired": {
                "schema": "echo.omv.smart-self-test-desired.v1",
                "devicefile": "/dev/sda",
                "test": "short",
            },
            "planId": plan_id,
        },
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approval_calls[0]["action"] == "storage.smart.self-test.start"
    assert {entry["action"] for entry in audit_calls} == {"storage.smart.self-test.start"}
    assert audit_calls[0]["metadata"]["devicefile"] == "/dev/sda"
