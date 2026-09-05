from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import btrfs_scrub_schedule_policy as policy
from appliance.native_storage_routes import create_omv_alias_router
from deploy.appliance import btrfs_scrub_schedule_runner as runner

REPOSITORY = Path(__file__).resolve().parents[2]
FILESYSTEM_UUID = "11111111-2222-3333-4444-555555555555"


def _timer(tmp_path: Path) -> Path:
    timer = tmp_path / "echo-btrfs-scrub.timer"
    timer.write_text("[Timer]\n", encoding="utf-8")
    return timer


def _filesystem(**overrides: Any) -> dict[str, Any]:
    return {
        "uuid": FILESYSTEM_UUID,
        "mountpoint": "/data/family",
        "status": "healthy",
        "readOnly": False,
        "totalDevices": 2,
        "activeDevices": 2,
        "missingDevices": 0,
        "dataProfile": "raid1",
        "metadataProfile": "raid1",
        "deviceErrorCount": 0,
        **overrides,
    }


def test_absent_policy_is_disabled_and_enable_requires_installed_timer(tmp_path: Path) -> None:
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
    assert status["scope"] == "echoManagedHealthyBtrfsRaid1Only"

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
    assert plan["scrubAction"] == "scrub"
    assert plan["safety"]["replicaRepair"] is True
    assert plan["safety"]["force"] is False
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


def test_runner_starts_only_healthy_error_free_idle_filesystems_without_identity_output() -> None:
    calls: list[tuple[str, Any]] = []
    inventory = [
        {"filesystem": _filesystem(uuid="bad", deviceErrorCount=1), "canStartScrub": True},
        {"filesystem": _filesystem(), "canStartScrub": True},
        {
            "filesystem": _filesystem(uuid="busy"),
            "canStartScrub": False,
        },
    ]

    def plan(desired: dict[str, Any]) -> dict[str, Any]:
        calls.append(("plan", desired))
        return {"planId": "a" * 64}

    def apply(desired: dict[str, Any], plan_id: str) -> dict[str, Any]:
        calls.append(("apply", (desired, plan_id)))
        return {"verified": True, "maintenanceState": "scrubbing"}

    result = runner.run_schedule(
        policy_reader=lambda: (True, {"schemaVersion": 1, "enabled": True}),
        inventory_reader=lambda: inventory,
        planner=plan,
        applier=apply,
    )

    assert result == {"outcome": "completed", "started": 1, "skipped": 2, "errors": 0}
    assert [value for name, value in calls if name == "apply"] == [
        (
            {
                "schema": "echo.omv.btrfs-scrub-desired.v1",
                "filesystemUuid": FILESYSTEM_UUID,
                "operation": "start",
            },
            "a" * 64,
        )
    ]
    assert FILESYSTEM_UUID not in json.dumps(result)


def test_runner_is_fail_closed_when_disabled() -> None:
    result = runner.run_schedule(
        policy_reader=lambda: (False, {"schemaVersion": 1, "enabled": False}),
        inventory_reader=lambda: pytest.fail("disabled schedule must not enumerate filesystems"),
    )
    assert result["outcome"] == "disabled"


def test_runner_is_fail_closed_when_enabled_value_has_no_policy_file() -> None:
    result = runner.run_schedule(
        policy_reader=lambda: (False, {"schemaVersion": 1, "enabled": True}),
        inventory_reader=lambda: pytest.fail("unconfigured schedule must not enumerate filesystems"),
    )
    assert result["outcome"] == "disabled"


def test_alias_apply_binds_exact_schedule_approval_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id = "c" * 64
    current_plan = {"planId": plan_id, "operation": "enable", "requiresApproval": True}
    approval_calls: list[dict[str, Any]] = []
    audit_calls: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approval_calls.append(kwargs)

    class Audit:
        def record(self, **kwargs: Any) -> None:
            audit_calls.append(kwargs)

    monkeypatch.setattr(
        "appliance.native_storage_routes.btrfs_scrub_schedule_policy.plan_policy",
        lambda _desired: current_plan,
    )
    monkeypatch.setattr(
        "appliance.native_storage_routes.btrfs_scrub_schedule_policy.apply_policy",
        lambda _desired, _plan_id: {**current_plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))

    response = TestClient(app).post(
        "/api/appliance/omv/volumes/btrfs-raid1/scrub/schedule/apply",
        json={
            "desired": {"schema": policy.DESIRED_SCHEMA, "enabled": True},
            "planId": plan_id,
        },
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approval_calls[0]["action"] == "storage.btrfs.scrub.schedule"
    assert {entry["action"] for entry in audit_calls} == {"storage.btrfs.scrub.schedule"}
    assert audit_calls[0]["metadata"] == {
        "enabled": True,
        "operation": "scrub",
        "replicaRepair": True,
        "scope": "echoManagedHealthyBtrfsRaid1Only",
        "schedule": "monthlyFirstSundayLocal",
        "source": "native",
    }


def test_schedule_route_rejects_coerced_boolean() -> None:
    app = FastAPI()
    app.include_router(create_omv_alias_router())

    response = TestClient(app).post(
        "/api/appliance/omv/volumes/btrfs-raid1/scrub/schedule/plan",
        json={"schema": policy.DESIRED_SCHEMA, "enabled": "true"},
    )

    assert response.status_code == 422


def test_systemd_timer_is_staggered_and_runner_is_constrained() -> None:
    service = (REPOSITORY / "deploy/appliance/systemd/echo-btrfs-scrub.service").read_text(
        encoding="utf-8"
    )
    timer = (REPOSITORY / "deploy/appliance/systemd/echo-btrfs-scrub.timer").read_text(
        encoding="utf-8"
    )
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(
        encoding="utf-8"
    )

    assert "deploy.appliance.btrfs_scrub_schedule_runner" in service
    assert "NoNewPrivileges=true" in service
    assert "PrivateNetwork=true" in service
    assert "ProtectSystem=strict" in service
    assert "ProtectKernelTunables=true" in service
    assert "CapabilityBoundingSet=CAP_SYS_ADMIN CAP_SYS_RAWIO" in service
    assert "ReadWritePaths=-/data -/mnt -/srv -/fs -/volume" in service
    assert "IOSchedulingClass=idle" in service
    assert "OnCalendar=Sun *-*-1..7 01:45:00" in timer
    assert "Persistent=true" in timer
    assert "RandomizedDelaySec=24h" in timer
    assert "echo-btrfs-scrub.timer" in provision
    assert "systemctl enable --now echo-btrfs-scrub.timer" in provision
