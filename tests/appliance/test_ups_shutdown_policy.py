from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import ups_shutdown_policy as policy
from appliance.native_storage_routes import create_omv_alias_router


def _desired(*, enabled: bool, samples: int = 3) -> dict[str, Any]:
    return {
        "schema": "echo.ups-shutdown-policy-desired.v1",
        "enabled": enabled,
        "requiredConsecutiveSamples": samples,
    }


def test_missing_policy_is_explicitly_disabled(tmp_path: Path) -> None:
    result = policy.policy_status(tmp_path / "missing.json", trusted_uid=tmp_path.stat().st_uid)

    assert result["configured"] is False
    assert result["enabled"] is False
    assert result["requiredConsecutiveSamples"] == 3


def test_plan_apply_and_noop_are_bound_to_current_file(tmp_path: Path) -> None:
    path = tmp_path / "ups-shutdown.json"
    trusted_uid = tmp_path.stat().st_uid
    desired = _desired(enabled=True, samples=4)

    first_plan = policy.plan_policy(desired, path=path, trusted_uid=trusted_uid)
    result = policy.apply_policy(
        desired,
        first_plan["planId"],
        path=path,
        trusted_uid=trusted_uid,
    )
    second_plan = policy.plan_policy(desired, path=path, trusted_uid=trusted_uid)
    noop = policy.apply_policy(
        desired,
        second_plan["planId"],
        path=path,
        trusted_uid=trusted_uid,
    )

    assert first_plan["operation"] == "enable"
    assert first_plan["requiresApproval"] is True
    assert result["applied"] is True
    assert result["verified"] is True
    assert json.loads(path.read_text(encoding="utf-8"))["enabled"] is True
    assert second_plan["operation"] == "none"
    assert noop["applied"] is False


def test_apply_rejects_stale_plan_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "ups-shutdown.json"

    with pytest.raises(policy.UpsShutdownPolicyError, match="stale"):
        policy.apply_policy(
            _desired(enabled=True),
            "0" * 64,
            path=path,
            trusted_uid=tmp_path.stat().st_uid,
        )

    assert not path.exists()


def test_policy_rejects_remote_or_unknown_configuration_fields(tmp_path: Path) -> None:
    desired = {**_desired(enabled=True), "host": "remote.example"}

    with pytest.raises(policy.UpsShutdownPolicyError, match="unexpected schema"):
        policy.plan_policy(
            desired,
            path=tmp_path / "policy.json",
            trusted_uid=tmp_path.stat().st_uid,
        )


def test_alias_apply_uses_exact_approval_and_audit_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id = "a" * 64
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
        "appliance.native_storage_routes.plan_policy", lambda _desired: current_plan
    )
    monkeypatch.setattr(
        "appliance.native_storage_routes.apply_policy",
        lambda _desired, _plan_id: {**current_plan, "applied": True, "verified": True},
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router(approval=Approval(), audit=Audit()))

    response = TestClient(app).post(
        "/api/appliance/omv/power/ups/shutdown-policy/apply",
        json={"desired": _desired(enabled=True), "planId": plan_id},
        headers={"X-Echo-Approval": "approval-token"},
    )

    assert response.status_code == 200
    assert approval_calls[0]["action"] == "power.ups-shutdown-policy.set"
    assert {entry["action"] for entry in audit_calls} == {"power.ups-shutdown-policy.set"}
    assert audit_calls[0]["metadata"]["trigger"] == "FSD or persistent OB+LB"
