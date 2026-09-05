from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import btrfs_snapshot_schedule_policy as policy
from appliance.native_storage_routes import create_omv_alias_router
from deploy.appliance import btrfs_snapshot_schedule_runner as runner

REPOSITORY = Path(__file__).resolve().parents[2]
SHARE_UUID = "11111111-2222-4333-8444-555555555555"
OTHER_SHARE_UUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _timer(tmp_path: Path) -> Path:
    timer = tmp_path / "echo-btrfs-snapshot.timer"
    timer.write_text("[Timer]\n", encoding="utf-8")
    return timer


def _desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": policy.DESIRED_SCHEMA,
        "sharedFolderRef": SHARE_UUID,
        "enabled": True,
        "keepLatest": 8,
        **overrides,
    }


def test_absent_policy_is_disabled_and_enable_requires_installed_timer(tmp_path: Path) -> None:
    path = tmp_path / "schedule.json"
    missing_timer = tmp_path / "missing.timer"
    status = policy.policy_status(
        SHARE_UUID,
        path,
        timer_path=missing_timer,
        trusted_uid=tmp_path.stat().st_uid,
    )
    assert status["enabled"] is False
    assert status["keepLatest"] == 8
    assert status["schedulerInstalled"] is False

    with pytest.raises(OSError, match="not installed"):
        policy.plan_policy(
            _desired(),
            path=path,
            timer_path=missing_timer,
            trusted_uid=tmp_path.stat().st_uid,
            eligibility_reader=lambda _ref: pytest.fail("timer gate must run first"),
        )


def test_policy_apply_updates_one_share_and_disable_works_offline(tmp_path: Path) -> None:
    path = tmp_path / "schedule.json"
    timer = _timer(tmp_path)

    def eligibility(reference: str) -> dict[str, Any]:
        return {"sharedFolderRef": reference, "snapshots": []}

    plan = policy.plan_policy(
        _desired(),
        path=path,
        timer_path=timer,
        trusted_uid=tmp_path.stat().st_uid,
        eligibility_reader=eligibility,
    )
    result = policy.apply_policy(
        _desired(),
        plan["planId"],
        path=path,
        timer_path=timer,
        trusted_uid=tmp_path.stat().st_uid,
        eligibility_reader=eligibility,
    )
    assert result["verified"] is True
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schemaVersion": 1,
        "shares": [{"keepLatest": 8, "sharedFolderRef": SHARE_UUID}],
    }

    disabled = _desired(enabled=False)
    disable_plan = policy.plan_policy(
        disabled,
        path=path,
        timer_path=timer,
        trusted_uid=tmp_path.stat().st_uid,
        eligibility_reader=lambda _ref: pytest.fail("disable must work while volume is offline"),
    )
    assert disable_plan["operation"] == "disable"


def test_policy_plan_binds_but_does_not_disclose_other_share_rules(tmp_path: Path) -> None:
    path = tmp_path / "schedule.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "shares": [
                    {"sharedFolderRef": OTHER_SHARE_UUID, "keepLatest": 4},
                ],
            }
        ),
        encoding="utf-8",
    )
    timer = _timer(tmp_path)
    plan = policy.plan_policy(
        _desired(),
        path=path,
        timer_path=timer,
        trusted_uid=tmp_path.stat().st_uid,
        eligibility_reader=lambda reference: {"sharedFolderRef": reference, "snapshots": []},
    )
    assert OTHER_SHARE_UUID not in json.dumps(plan)

    result = policy.apply_policy(
        _desired(),
        plan["planId"],
        path=path,
        timer_path=timer,
        trusted_uid=tmp_path.stat().st_uid,
        eligibility_reader=lambda reference: {"sharedFolderRef": reference, "snapshots": []},
    )
    assert result["verified"] is True
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert {item["sharedFolderRef"] for item in stored["shares"]} == {
        SHARE_UUID,
        OTHER_SHARE_UUID,
    }


def test_policy_rejects_duplicate_shares_and_coerced_retention() -> None:
    with pytest.raises(policy.BtrfsSnapshotSchedulePolicyError, match="duplicate"):
        policy.validate_policy(
            {
                "schemaVersion": 1,
                "shares": [
                    {"sharedFolderRef": SHARE_UUID, "keepLatest": 8},
                    {"sharedFolderRef": SHARE_UUID, "keepLatest": 4},
                ],
            }
        )
    with pytest.raises(policy.BtrfsSnapshotSchedulePolicyError, match="keepLatest"):
        policy.plan_policy(_desired(keepLatest=True))


def test_runner_creates_one_snapshot_and_prunes_only_old_automatic_entries() -> None:
    deleted: list[dict[str, Any]] = []
    automatic = [
        {
            "snapshotId": f"00000000-0000-4000-8000-{index:012d}",
            "name": name,
            "kind": "automatic",
            "readOnly": True,
        }
        for index, name in enumerate(
            [
                "auto-20260801t000000z",
                "auto-20260802t000000z",
                "auto-20260905t000000z",
            ],
            start=1,
        )
    ]
    manual = {
        "snapshotId": "ffffffff-ffff-4fff-8fff-ffffffffffff",
        "name": "important",
        "kind": "manual",
        "readOnly": True,
    }

    def delete_plan(desired: dict[str, Any]) -> dict[str, Any]:
        deleted.append(desired)
        return {"planId": "d" * 64}

    result = runner.run_schedule(
        now=datetime(2026, 9, 5, tzinfo=UTC),
        policy_reader=lambda: (
            True,
            {
                "schemaVersion": 1,
                "shares": [{"sharedFolderRef": SHARE_UUID, "keepLatest": 2}],
            },
        ),
        create_planner=lambda ref, name: {
            "planId": "c" * 64,
            "operation": "create",
        },
        create_applier=lambda ref, name, plan_id: {
            "verified": True,
            "snapshot": {"kind": "automatic"},
        },
        inventory_reader=lambda _ref: {"snapshots": [*automatic, manual]},
        delete_planner=delete_plan,
        delete_applier=lambda desired, plan_id: {
            "verified": True,
            "snapshotDeleted": True,
        },
    )

    assert result == {"outcome": "completed", "created": 1, "pruned": 1, "errors": 0}
    assert deleted == [
        {
            "schema": "echo.omv.btrfs-snapshot-delete-desired.v1",
            "sharedFolderRef": SHARE_UUID,
            "snapshotId": automatic[0]["snapshotId"],
        }
    ]
    assert manual["snapshotId"] not in json.dumps(deleted)
    assert SHARE_UUID not in json.dumps(result)


def test_runner_fails_closed_when_policy_is_absent() -> None:
    result = runner.run_schedule(
        policy_reader=lambda: (False, {"schemaVersion": 1, "shares": []}),
        inventory_reader=lambda _ref: pytest.fail("disabled runner must not inspect shares"),
    )
    assert result == {"outcome": "disabled", "created": 0, "pruned": 0, "errors": 0}


def test_runner_frees_one_automatic_slot_at_the_256_limit() -> None:
    oldest = {
        "snapshotId": "00000000-0000-4000-8000-000000000001",
        "name": "auto-20260801t000000z",
        "kind": "automatic",
    }
    snapshots = [
        oldest,
        *[
            {
                "snapshotId": f"10000000-0000-4000-8000-{index:012d}",
                "name": f"manual_{index}",
                "kind": "manual",
            }
            for index in range(255)
        ],
    ]

    def delete(desired: dict[str, Any], _plan_id: str) -> dict[str, Any]:
        snapshots[:] = [item for item in snapshots if item["snapshotId"] != desired["snapshotId"]]
        return {"verified": True, "snapshotDeleted": True}

    def create(_ref: str, name: str, _plan_id: str) -> dict[str, Any]:
        snapshots.append(
            {
                "snapshotId": "20000000-0000-4000-8000-000000000001",
                "name": name,
                "kind": "automatic",
            }
        )
        return {"verified": True, "snapshot": {"kind": "automatic"}}

    result = runner.run_schedule(
        now=datetime(2026, 9, 5, tzinfo=UTC),
        policy_reader=lambda: (
            True,
            {
                "schemaVersion": 1,
                "shares": [{"sharedFolderRef": SHARE_UUID, "keepLatest": 8}],
            },
        ),
        inventory_reader=lambda _ref: {"snapshots": list(snapshots)},
        create_planner=lambda _ref, _name: {
            "planId": "c" * 64,
            "operation": "create",
        },
        create_applier=create,
        delete_planner=lambda _desired: {"planId": "d" * 64},
        delete_applier=delete,
    )
    assert result == {"outcome": "completed", "created": 1, "pruned": 1, "errors": 0}
    assert oldest not in snapshots
    assert len(snapshots) == 256


def test_runner_never_prunes_locked_automatic_snapshots() -> None:
    locked = {
        "snapshotId": "00000000-0000-4000-8000-000000000001",
        "name": "auto-20260801t000000z",
        "kind": "automatic",
        "locked": True,
    }
    unlocked = {
        "snapshotId": "00000000-0000-4000-8000-000000000002",
        "name": "auto-20260802t000000z",
        "kind": "automatic",
        "locked": False,
    }
    newest = {
        "snapshotId": "00000000-0000-4000-8000-000000000003",
        "name": "auto-20260905t000000z",
        "kind": "automatic",
        "locked": False,
    }
    deleted: list[str] = []
    result = runner.run_schedule(
        now=datetime(2026, 9, 5, tzinfo=UTC),
        policy_reader=lambda: (
            True,
            {
                "schemaVersion": 1,
                "shares": [{"sharedFolderRef": SHARE_UUID, "keepLatest": 1}],
            },
        ),
        inventory_reader=lambda _ref: {"snapshots": [locked, unlocked, newest]},
        create_planner=lambda _ref, _name: {"planId": "c" * 64, "operation": "create"},
        create_applier=lambda _ref, _name, _plan_id: {
            "verified": True,
            "snapshot": {"kind": "automatic"},
        },
        delete_planner=lambda _desired: {"planId": "d" * 64},
        delete_applier=lambda desired, _plan_id: (
            deleted.append(desired["snapshotId"]) or {"verified": True, "snapshotDeleted": True}
        ),
    )
    assert result == {"outcome": "completed", "created": 1, "pruned": 1, "errors": 0}
    assert deleted == [unlocked["snapshotId"]]
    assert locked["snapshotId"] not in deleted


def test_schedule_route_binds_exact_approval_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id = "c" * 64
    current_plan = {"planId": plan_id, "operation": "enable", "requiresApproval": True}
    approval_calls: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approval_calls.append(kwargs)

    class Audit:
        def record(self, **_kwargs: Any) -> None:
            pass

    monkeypatch.setattr(policy, "plan_policy", lambda _desired: current_plan)
    monkeypatch.setattr(
        policy,
        "apply_policy",
        lambda _desired, _plan_id: {**current_plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))
    response = TestClient(app).post(
        "/api/appliance/omv/sharing/snapshots/schedule/apply",
        json={"desired": _desired(), "planId": plan_id},
        headers={"X-Echo-Approval": "approval-token"},
    )
    assert response.status_code == 200
    assert approval_calls[0]["action"] == "storage.btrfs.snapshot.schedule"
    assert approval_calls[0]["target"] == plan_id


def test_schedule_route_rejects_coerced_values() -> None:
    app = FastAPI()
    app.include_router(create_omv_alias_router())
    response = TestClient(app).post(
        "/api/appliance/omv/sharing/snapshots/schedule/plan",
        json={**_desired(), "enabled": "true", "keepLatest": "8"},
    )
    assert response.status_code == 422


def test_daily_timer_and_runner_are_constrained() -> None:
    service = (REPOSITORY / "deploy/appliance/systemd/echo-btrfs-snapshot.service").read_text(
        encoding="utf-8"
    )
    timer = (REPOSITORY / "deploy/appliance/systemd/echo-btrfs-snapshot.timer").read_text(
        encoding="utf-8"
    )
    provision = (REPOSITORY / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    assert "deploy.appliance.btrfs_snapshot_schedule_runner" in service
    assert "NoNewPrivileges=true" in service
    assert "PrivateNetwork=true" in service
    assert "ProtectSystem=strict" in service
    assert "CapabilityBoundingSet=CAP_SYS_ADMIN" in service
    assert "ReadWritePaths=-/data -/mnt -/srv -/fs -/volume" in service
    assert "OnCalendar=*-*-* 02:15:00" in timer
    assert "Persistent=true" in timer
    assert "RandomizedDelaySec=45min" in timer
    assert "echo-btrfs-snapshot.timer" in provision
    assert "systemctl enable --now echo-btrfs-snapshot.timer" in provision
