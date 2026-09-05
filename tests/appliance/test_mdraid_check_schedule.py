from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import mdraid_check_schedule_policy as policy
from appliance.native_storage_routes import create_omv_alias_router
from deploy.appliance import mdraid_check_schedule_runner as runner

REPOSITORY = Path(__file__).resolve().parents[2]
ARRAY_UUID = "11111111:22222222:33333333:44444444"


def _timer(tmp_path: Path) -> Path:
    timer = tmp_path / "echo-mdraid-check.timer"
    timer.write_text("[Timer]\n", encoding="utf-8")
    return timer


def test_absent_policy_is_disabled_and_enable_plan_requires_installed_timer(
    tmp_path: Path,
) -> None:
    path = tmp_path / "schedule.json"
    missing_timer = tmp_path / "missing.timer"
    status = policy.policy_status(
        path,
        timer_path=missing_timer,
        trusted_uid=tmp_path.stat().st_uid,
    )
    assert status["configured"] is False
    assert status["enabled"] is False
    assert status["schedulerInstalled"] is False

    desired = {"schema": policy.DESIRED_SCHEMA, "enabled": True}
    with pytest.raises(OSError, match="not installed"):
        policy.plan_policy(
            desired,
            path=path,
            timer_path=missing_timer,
            trusted_uid=tmp_path.stat().st_uid,
        )

    plan = policy.plan_policy(
        desired,
        path=path,
        timer_path=_timer(tmp_path),
        trusted_uid=tmp_path.stat().st_uid,
    )
    assert plan["operation"] == "enable"
    assert plan["requiresApproval"] is True
    assert plan["checkAction"] == "check"
    assert plan["safety"]["explicitRepair"] is False
    assert len(plan["planId"]) == 64


def test_apply_writes_only_bounded_policy_and_verifies(tmp_path: Path) -> None:
    path = tmp_path / "schedule.json"
    timer = _timer(tmp_path)
    desired = {"schema": policy.DESIRED_SCHEMA, "enabled": True}
    plan = policy.plan_policy(
        desired,
        path=path,
        timer_path=timer,
        trusted_uid=tmp_path.stat().st_uid,
    )

    result = policy.apply_policy(
        desired,
        plan["planId"],
        path=path,
        timer_path=timer,
        trusted_uid=tmp_path.stat().st_uid,
    )

    assert result["verified"] is True
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "enabled": True,
        "schemaVersion": 1,
    }
    assert path.stat().st_size < policy.MAX_FILE_BYTES


def test_runner_starts_only_eligible_echo_arrays_without_identity_output() -> None:
    calls: list[tuple[str, Any]] = []
    inventory = [
        {
            "array": {"name": "busy", "uuid": "aaaaaaaa:bbbbbbbb:cccccccc:dddddddd"},
            "canStartCheck": False,
        },
        {
            "array": {"name": "family", "uuid": ARRAY_UUID},
            "canStartCheck": True,
        },
    ]

    def plan(desired: dict[str, Any]) -> dict[str, Any]:
        calls.append(("plan", desired))
        return {"planId": "a" * 64}

    def apply(desired: dict[str, Any], plan_id: str) -> dict[str, Any]:
        calls.append(("apply", (desired, plan_id)))
        return {"verified": True}

    result = runner.run_schedule(
        policy_reader=lambda: (True, {"schemaVersion": 1, "enabled": True}),
        inventory_reader=lambda: inventory,
        planner=plan,
        applier=apply,
    )

    assert result == {"outcome": "completed", "started": 1, "skipped": 1, "errors": 0}
    assert [value for name, value in calls if name == "apply"] == [
        (
            {
                "schema": "echo.omv.mdraid-check-desired.v1",
                "name": "family",
                "arrayUuid": ARRAY_UUID,
                "operation": "start",
            },
            "a" * 64,
        )
    ]
    assert "family" not in json.dumps(result)
    assert ARRAY_UUID not in json.dumps(result)


def test_runner_is_fail_closed_when_disabled() -> None:
    result = runner.run_schedule(
        policy_reader=lambda: (False, {"schemaVersion": 1, "enabled": False}),
        inventory_reader=lambda: pytest.fail("disabled schedule must not enumerate arrays"),
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
        "appliance.native_storage_routes.mdraid_check_schedule_policy.plan_policy",
        lambda _desired: current_plan,
    )
    monkeypatch.setattr(
        "appliance.native_storage_routes.mdraid_check_schedule_policy.apply_policy",
        lambda _desired, _plan_id: {**current_plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))

    response = TestClient(app).post(
        "/api/appliance/omv/arrays/mdraid1/check/schedule/apply",
        json={
            "desired": {"schema": policy.DESIRED_SCHEMA, "enabled": True},
            "planId": plan_id,
        },
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approval_calls[0]["action"] == "storage.mdraid.check.schedule"
    assert {entry["action"] for entry in audit_calls} == {"storage.mdraid.check.schedule"}
    assert audit_calls[0]["metadata"] == {
        "enabled": True,
        "operation": "check",
        "explicitRepair": False,
        "scope": "echoManagedHealthyRaid1Only",
        "schedule": "monthlyFirstSundayLocal",
        "source": "native",
    }


def test_systemd_timer_matches_fnos_cadence_and_runner_is_constrained() -> None:
    service = (REPOSITORY / "deploy/appliance/systemd/echo-mdraid-check.service").read_text(
        encoding="utf-8"
    )
    timer = (REPOSITORY / "deploy/appliance/systemd/echo-mdraid-check.timer").read_text(
        encoding="utf-8"
    )
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")

    assert "deploy.appliance.mdraid_check_schedule_runner" in service
    assert "NoNewPrivileges=true" in service
    assert "PrivateNetwork=true" in service
    assert "ProtectSystem=strict" in service
    assert "ProtectKernelTunables=true" not in service
    assert "CapabilityBoundingSet=CAP_SYS_ADMIN CAP_SYS_RAWIO" in service
    assert "IOSchedulingClass=idle" in service
    assert "OnCalendar=Sun *-*-1..7 00:45:00" in timer
    assert "Persistent=true" in timer
    assert "RandomizedDelaySec=24h" in timer
    assert "echo-mdraid-check.timer" in provision
    assert "systemctl enable --now echo-mdraid-check.timer" in provision
