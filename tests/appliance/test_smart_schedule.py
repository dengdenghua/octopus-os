from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import smart_schedule_policy as policy
from appliance.native_storage_routes import create_omv_alias_router
from deploy.appliance import smart_schedule_runner as runner

REPOSITORY = Path(__file__).resolve().parents[2]


def test_absent_policy_is_disabled_and_plan_is_approval_bound(tmp_path: Path) -> None:
    path = tmp_path / "schedule.json"
    status = policy.policy_status(path, trusted_uid=tmp_path.stat().st_uid)
    assert status["configured"] is False
    assert status["enabled"] is False
    assert status["test"] == "short"

    plan = policy.plan_policy(
        {"schema": policy.DESIRED_SCHEMA, "enabled": True},
        path=path,
        trusted_uid=tmp_path.stat().st_uid,
    )
    assert plan["operation"] == "enable"
    assert plan["requiresApproval"] is True
    assert len(plan["planId"]) == 64


def test_apply_writes_only_bounded_root_policy_and_verifies(tmp_path: Path) -> None:
    path = tmp_path / "schedule.json"
    desired = {"schema": policy.DESIRED_SCHEMA, "enabled": True}
    plan = policy.plan_policy(desired, path=path, trusted_uid=tmp_path.stat().st_uid)
    result = policy.apply_policy(
        desired,
        plan["planId"],
        path=path,
        trusted_uid=tmp_path.stat().st_uid,
    )

    assert result["verified"] is True
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "enabled": True,
        "schemaVersion": 1,
    }
    assert path.stat().st_size < policy.MAX_FILE_BYTES


def test_runner_starts_only_idle_supported_whole_disks() -> None:
    calls: list[tuple[str, Any]] = []
    inventory = [
        {"devicefile": "/dev/sdb", "type": "disk", "serial": "PRIVATE-B"},
        {"devicefile": "/dev/sda", "type": "disk", "serial": "PRIVATE-A"},
        {"devicefile": "/dev/sda1", "type": "part"},
    ]

    def status(devicefile: str) -> dict[str, Any]:
        calls.append(("status", devicefile))
        return {"supported": True, "state": "inProgress" if devicefile == "/dev/sdb" else "idle"}

    def plan(desired: dict[str, Any]) -> dict[str, Any]:
        calls.append(("plan", desired))
        return {"planId": "a" * 64}

    def apply(desired: dict[str, Any], plan_id: str) -> dict[str, Any]:
        calls.append(("apply", (desired, plan_id)))
        return {"verified": True}

    result = runner.run_schedule(
        policy_reader=lambda: (True, {"schemaVersion": 1, "enabled": True}),
        inventory_reader=lambda: inventory,
        status_reader=status,
        planner=plan,
        applier=apply,
    )

    assert result == {"outcome": "completed", "started": 1, "skipped": 1, "errors": 0}
    assert calls[0] == ("status", "/dev/sda")
    applied = [value for name, value in calls if name == "apply"]
    assert applied == [
        (
            {
                "schema": "echo.omv.smart-self-test-desired.v1",
                "devicefile": "/dev/sda",
                "test": "short",
            },
            "a" * 64,
        )
    ]
    assert "PRIVATE" not in json.dumps(result)


def test_runner_is_fail_closed_when_disabled() -> None:
    result = runner.run_schedule(
        policy_reader=lambda: (False, {"schemaVersion": 1, "enabled": False}),
        inventory_reader=lambda: pytest.fail("disabled schedule must not enumerate disks"),
    )
    assert result["outcome"] == "disabled"


def test_alias_apply_binds_exact_schedule_approval_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id = "c" * 64
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

    monkeypatch.setattr(
        "appliance.native_storage_routes.smart_schedule_policy.plan_policy",
        lambda _desired: current_plan,
    )
    monkeypatch.setattr(
        "appliance.native_storage_routes.smart_schedule_policy.apply_policy",
        lambda _desired, _plan_id: {**current_plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))

    response = TestClient(app).post(
        "/api/appliance/omv/smart/self-test/schedule/apply",
        json={
            "desired": {"schema": policy.DESIRED_SCHEMA, "enabled": True},
            "planId": plan_id,
        },
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approval_calls[0]["action"] == "storage.smart.self-test.schedule"
    assert {entry["action"] for entry in audit_calls} == {"storage.smart.self-test.schedule"}
    assert audit_calls[0]["metadata"]["enabled"] is True
    assert audit_calls[0]["metadata"]["schedule"] == "weeklySundayLocal"
    assert audit_calls[0]["metadata"]["test"] == "short"


def test_systemd_timer_is_weekly_persistent_and_low_io_priority() -> None:
    service = (REPOSITORY / "deploy/appliance/systemd/echo-smart-self-test.service").read_text(
        encoding="utf-8"
    )
    timer = (REPOSITORY / "deploy/appliance/systemd/echo-smart-self-test.timer").read_text(
        encoding="utf-8"
    )
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    assert "deploy.appliance.smart_schedule_runner" in service
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service
    assert "IOSchedulingClass=idle" in service
    assert "OnCalendar=Sun *-*-* 03:30:00" in timer
    assert "Persistent=true" in timer
    assert "RandomizedDelaySec=30min" in timer
    assert "echo-smart-self-test.timer" in provision
