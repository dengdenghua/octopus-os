"""ECHO Capability Contract discovery, policy and intent-bound execution tests."""

from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.app_registry.router import create_appliance_router
from appliance.approval import (
    APPROVAL_HEADER,
    INTENT_HEADER,
    HighRiskApprovalService,
    create_approval_router,
)
from appliance.audit import ApplianceAudit
from appliance.capabilities import (
    CapabilityRegistry,
    build_builtin_registry,
    create_capabilities_router,
)
from runtime.safety.auth.identity import encode_jwt_hs256

JWT_SECRET = "capability-contract-test-secret-which-is-not-production"
PASSWORD = "device-capability-password"
PASSWORD_HASH = hashlib.sha256(PASSWORD.encode()).hexdigest()


def _authenticated_client(app: FastAPI) -> TestClient:
    client = TestClient(app)
    token = encode_jwt_hs256(
        {"sub": "local:admin", "iat": 0, "exp": 9_999_999_999},
        secret=JWT_SECRET,
    )
    client.cookies.set("echo_session", token)
    return client


def _capability_client(tmp_path) -> tuple[TestClient, ApplianceAudit]:
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    app = FastAPI()
    app.include_router(
        create_capabilities_router(
            build_builtin_registry(),
            jwt_secret=JWT_SECRET,
            audit=audit,
        )
    )
    return _authenticated_client(app), audit


def test_builtin_registry_is_stable_unique_and_can_hide_unmounted_files() -> None:
    complete = build_builtin_registry()
    without_files = build_builtin_registry(include_files=False)

    assert [item.id for item in complete.list()] == [
        "apps.list",
        "apps.start",
        "apps.stop",
        "files.list",
        "files.trash.empty",
        "files.trash.move",
        "files.trash.restore",
        "files.upload",
        "hub.catalog.list",
        "hub.install.apply",
        "hub.install.plan",
        "hub.restart.plan",
        "hub.restart.queue",
        "hub.start.plan",
        "hub.start.queue",
        "hub.stop.plan",
        "hub.stop.queue",
        "hub.uninstall.apply",
        "hub.uninstall.plan",
        "hub.update.apply",
        "hub.update.plan",
        "photos.index.apply",
        "photos.index.plan",
        "photos.library.list",
        "photos.search",
        "storage.health.read",
    ]
    assert len(without_files) == 17
    assert all(not item.id.startswith("files.") for item in without_files.list())

    with pytest.raises(ValueError, match="duplicate capability id"):
        CapabilityRegistry([complete.get("apps.list"), complete.get("apps.list")])  # type: ignore[list-item]


def test_discovery_requires_login_and_exposes_provider_operations(tmp_path) -> None:
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    app = FastAPI()
    app.include_router(
        create_capabilities_router(
            build_builtin_registry(),
            jwt_secret=JWT_SECRET,
            audit=audit,
        )
    )
    anonymous = TestClient(app)

    assert anonymous.get("/api/appliance/capabilities").status_code == 401
    client = _authenticated_client(app)
    response = client.get("/api/appliance/capabilities")
    filtered = client.get("/api/appliance/capabilities", params={"provider": "echo-os.storage.omv"})
    detail = client.get("/api/appliance/capabilities/files.trash.empty")

    assert response.status_code == 200
    assert response.json()["count"] == 26
    assert filtered.json()["count"] == 1
    assert filtered.json()["capabilities"][0]["metadata"]["id"] == "storage.health.read"
    assert detail.json()["provider"]["operation"] == {
        "method": "POST",
        "path": "/api/appliance/files/trash/empty",
    }
    assert detail.json()["effect"] == {
        "type": "destructive",
        "risk": "high",
        "reversible": False,
    }
    assert detail.json()["authorization"]["approval"] == "password-step-up"


def test_policy_returns_allow_ask_and_default_closed_deny_with_audit(tmp_path) -> None:
    client, audit = _capability_client(tmp_path)

    allowed = client.post(
        "/api/appliance/capabilities/decisions",
        json={
            "capabilityId": "files.list",
            "intentId": "task.photos.scan",
            "target": "photos/2026",
        },
    )
    asked = client.post(
        "/api/appliance/capabilities/decisions",
        json={
            "capabilityId": "apps.start",
            "intentId": "task.media.start",
            "target": "a" * 12,
        },
    )
    invalid_scope = client.post(
        "/api/appliance/capabilities/decisions",
        json={
            "capabilityId": "files.list",
            "intentId": "task.escape.block",
            "target": "../etc",
        },
    )
    unknown = client.post(
        "/api/appliance/capabilities/decisions",
        json={
            "capabilityId": "system.root.shell",
            "intentId": "task.default.closed",
        },
    )

    assert allowed.json()["decision"] == "allow"
    assert allowed.json()["reasonCode"] == "POLICY_ALLOWED"
    assert allowed.json()["actor"] == "local:admin"
    assert allowed.json()["auditEventId"].startswith("appliance-audit:")

    asked_body = asked.json()
    assert asked_body["decision"] == "ask"
    assert asked_body["reasonCode"] == "PASSWORD_STEP_UP_REQUIRED"
    assert asked_body["approval"]["requestBody"] == {
        "action": "app.start",
        "target": "a" * 12,
        "intentId": "task.media.start",
    }
    assert asked_body["approval"]["executionHeaders"] == {
        APPROVAL_HEADER: "<approvalToken>",
        INTENT_HEADER: "task.media.start",
    }
    assert invalid_scope.json()["decision"] == "deny"
    assert invalid_scope.json()["reasonCode"] == "INVALID_SCOPE"
    assert "execute" not in invalid_scope.json()
    assert unknown.json()["decision"] == "deny"
    assert unknown.json()["reasonCode"] == "UNKNOWN_CAPABILITY"

    decisions = [
        event["payload"]
        for event in audit.recent(20)
        if event["payload"]["action"] == "capability.decision"
    ]
    assert [event["outcome"] for event in decisions] == ["allow", "ask", "deny", "deny"]
    assert decisions[1]["metadata"]["intentId"] == "task.media.start"


def test_policy_decision_fails_closed_when_production_audit_is_missing() -> None:
    app = FastAPI()
    app.include_router(
        create_capabilities_router(
            build_builtin_registry(),
            jwt_secret=JWT_SECRET,
            audit=None,
        )
    )
    client = _authenticated_client(app)

    assert client.get("/api/appliance/capabilities").status_code == 200
    decision = client.post(
        "/api/appliance/capabilities/decisions",
        json={"capabilityId": "apps.list", "intentId": "task.audit.required"},
    )
    assert decision.status_code == 503
    assert decision.json() == {"detail": "capability audit unavailable"}


def test_agent_hub_restart_capability_requires_plan_bound_step_up(tmp_path) -> None:
    client, _audit = _capability_client(tmp_path)
    plan_id = "d" * 64

    decision = client.post(
        "/api/appliance/capabilities/decisions",
        json={
            "capabilityId": "hub.restart.queue",
            "intentId": "task.hub.recover.nextcloud",
            "target": plan_id,
        },
    )

    assert decision.status_code == 200
    assert decision.json()["decision"] == "ask"
    assert decision.json()["approval"]["requestBody"] == {
        "action": "hub.app.restart",
        "target": plan_id,
        "intentId": "task.hub.recover.nextcloud",
    }


class _Docker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def list_containers(self, include_stopped: bool = True) -> list[dict]:
        return []

    def start(self, container_id: str) -> None:
        self.calls.append(("start", container_id))

    def stop(self, container_id: str) -> None:
        self.calls.append(("stop", container_id))


def test_step_up_token_can_be_bound_to_the_capability_task_intent(tmp_path) -> None:
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=PASSWORD_HASH,
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"capability-contract-nonce" * 2,
    )
    docker = _Docker()
    app = FastAPI()
    app.include_router(create_approval_router(approval, jwt_secret=JWT_SECRET))
    app.include_router(
        create_appliance_router(
            docker=docker,
            jwt_secret=JWT_SECRET,
            approval=approval,
            audit=audit,
        )
    )
    client = _authenticated_client(app)
    target = "b" * 12
    intent_id = "task.media.start"

    issued = client.post(
        "/api/appliance/approvals",
        json={
            "action": "app.start",
            "target": target,
            "intentId": intent_id,
            "password": PASSWORD,
        },
    )
    assert issued.status_code == 200
    assert issued.json()["intentId"] == intent_id
    approval_token = issued.json()["approvalToken"]

    missing_intent = client.post(
        f"/api/appliance/apps/{target}/start",
        headers={APPROVAL_HEADER: approval_token},
    )
    wrong_intent = client.post(
        f"/api/appliance/apps/{target}/start",
        headers={APPROVAL_HEADER: approval_token, INTENT_HEADER: "task.other"},
    )
    executed = client.post(
        f"/api/appliance/apps/{target}/start",
        headers={APPROVAL_HEADER: approval_token, INTENT_HEADER: intent_id},
    )

    assert missing_intent.status_code == 403
    assert wrong_intent.status_code == 403
    assert executed.status_code == 200
    assert docker.calls == [("start", target)]


def test_legacy_step_up_without_intent_remains_compatible(tmp_path) -> None:
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=PASSWORD_HASH,
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"legacy-capability-contract" * 2,
    )
    token, _expires_in = approval.issue(
        actor="local:admin",
        action="app.stop",
        target="c" * 12,
        password=PASSWORD,
        client_ip="127.0.0.1",
    )

    approval.consume(
        token=token,
        actor="local:admin",
        action="app.stop",
        target="c" * 12,
    )
