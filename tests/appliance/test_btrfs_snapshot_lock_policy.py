from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import btrfs_snapshot_lock_policy as policy
from appliance.native_storage_routes import create_omv_alias_router

SHARE_UUID = "11111111-2222-4333-8444-555555555555"
SNAPSHOT_UUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
OTHER_SHARE_UUID = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
OTHER_SNAPSHOT_UUID = "cccccccc-dddd-4eee-8fff-000000000001"


def _desired(**overrides: Any) -> dict[str, Any]:
    return {
        "schema": policy.DESIRED_SCHEMA,
        "sharedFolderRef": SHARE_UUID,
        "snapshotId": SNAPSHOT_UUID,
        "locked": True,
        **overrides,
    }


def _inventory(reference: str) -> dict[str, Any]:
    return {
        "sharedFolderRef": reference,
        "snapshots": [
            {
                "snapshotId": SNAPSHOT_UUID,
                "name": "before_upgrade",
                "kind": "manual",
                "readOnly": True,
                "locked": False,
            }
        ],
    }


def test_missing_registry_has_no_locks(tmp_path: Path) -> None:
    path = tmp_path / "locks.json"
    assert (
        policy.locked_snapshot_ids(SHARE_UUID, path, trusted_uid=tmp_path.stat().st_uid)
        == frozenset()
    )


def test_lock_and_unlock_preserve_other_rules_without_disclosing_them(tmp_path: Path) -> None:
    path = tmp_path / "locks.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "locks": [
                    {
                        "sharedFolderRef": OTHER_SHARE_UUID,
                        "snapshotId": OTHER_SNAPSHOT_UUID,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    trusted_uid = tmp_path.stat().st_uid
    plan = policy.plan_lock(
        _desired(),
        path=path,
        trusted_uid=trusted_uid,
        inventory_reader=_inventory,
    )
    assert plan["operation"] == "lock"
    assert plan["requiresApproval"] is True
    assert OTHER_SHARE_UUID not in json.dumps(plan)
    result = policy.apply_lock(
        _desired(),
        plan["planId"],
        path=path,
        trusted_uid=trusted_uid,
        inventory_reader=_inventory,
    )
    assert result["verified"] is True
    assert policy.locked_snapshot_ids(SHARE_UUID, path, trusted_uid=trusted_uid) == frozenset(
        {SNAPSHOT_UUID}
    )
    assert policy.locked_snapshot_ids(OTHER_SHARE_UUID, path, trusted_uid=trusted_uid) == frozenset(
        {OTHER_SNAPSHOT_UUID}
    )

    unlock = _desired(locked=False)
    unlock_plan = policy.plan_lock(
        unlock,
        path=path,
        trusted_uid=trusted_uid,
        inventory_reader=_inventory,
    )
    assert unlock_plan["operation"] == "unlock"
    unlocked = policy.apply_lock(
        unlock,
        unlock_plan["planId"],
        path=path,
        trusted_uid=trusted_uid,
        inventory_reader=_inventory,
    )
    assert unlocked["verified"] is True
    assert not policy.locked_snapshot_ids(SHARE_UUID, path, trusted_uid=trusted_uid)


def test_production_apply_serializes_with_snapshot_deletion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from appliance import native_btrfs_snapshot, native_storage

    path = tmp_path / "locks.json"
    trusted_uid = tmp_path.stat().st_uid
    events: list[str] = []

    @contextmanager
    def transaction():
        events.append("enter")
        yield
        events.append("exit")

    monkeypatch.setattr(native_storage, "_registry_transaction", transaction)
    monkeypatch.setattr(native_btrfs_snapshot, "_lock_policy_inventory", _inventory)
    plan = policy.plan_lock(
        _desired(),
        path=path,
        trusted_uid=trusted_uid,
        inventory_reader=_inventory,
    )
    result = policy.apply_lock(
        _desired(),
        plan["planId"],
        path=path,
        trusted_uid=trusted_uid,
    )
    assert result["verified"] is True
    assert events == ["enter", "exit"]


def test_failed_lock_write_restores_the_previous_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "locks.json"
    original = {"schemaVersion": 1, "locks": []}
    path.write_text(json.dumps(original), encoding="utf-8")
    path.chmod(0o600)
    trusted_uid = tmp_path.stat().st_uid
    plan = policy.plan_lock(
        _desired(),
        path=path,
        trusted_uid=trusted_uid,
        inventory_reader=_inventory,
    )
    atomic_write = policy._atomic_write
    writes = 0

    def fail_once(target: Path, payload: bytes) -> None:
        nonlocal writes
        writes += 1
        if writes == 1:
            target.write_bytes(b"truncated")
            raise OSError("injected write failure")
        atomic_write(target, payload)

    monkeypatch.setattr(policy, "_atomic_write", fail_once)
    with pytest.raises(OSError, match="rolled back"):
        policy.apply_lock(
            _desired(),
            plan["planId"],
            path=path,
            trusted_uid=trusted_uid,
            inventory_reader=_inventory,
        )
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_registry_and_desired_state_are_strict() -> None:
    duplicate = {
        "schemaVersion": 1,
        "locks": [
            {"sharedFolderRef": SHARE_UUID, "snapshotId": SNAPSHOT_UUID},
            {"sharedFolderRef": SHARE_UUID, "snapshotId": SNAPSHOT_UUID},
        ],
    }
    with pytest.raises(policy.BtrfsSnapshotLockPolicyError, match="duplicate"):
        policy.validate_registry(duplicate)
    with pytest.raises(policy.BtrfsSnapshotLockPolicyError, match="boolean"):
        policy.plan_lock(_desired(locked=1), inventory_reader=_inventory)


def test_lock_route_consumes_exact_plan_bound_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id = "d" * 64
    current_plan = {"planId": plan_id, "operation": "lock", "requiresApproval": True}
    approval_calls: list[dict[str, Any]] = []

    class Approval:
        def consume(self, **kwargs: Any) -> None:
            approval_calls.append(kwargs)

    class Audit:
        def record(self, **_kwargs: Any) -> None:
            pass

    monkeypatch.setattr(policy, "plan_lock", lambda _desired: current_plan)
    monkeypatch.setattr(
        policy,
        "apply_lock",
        lambda _desired, _plan_id: {**current_plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))
    response = TestClient(app).post(
        "/api/appliance/omv/sharing/snapshots/lock/apply",
        json={"desired": _desired(), "planId": plan_id},
        headers={"X-Echo-Approval": "approval-token"},
    )
    assert response.status_code == 200
    assert approval_calls[0]["action"] == "omv.btrfs-snapshot.lock"
    assert approval_calls[0]["target"] == plan_id


def test_lock_route_rejects_coerced_boolean() -> None:
    app = FastAPI()
    app.include_router(create_omv_alias_router())
    response = TestClient(app).post(
        "/api/appliance/omv/sharing/snapshots/lock/plan",
        json={**_desired(), "locked": "true"},
    )
    assert response.status_code == 422
