from __future__ import annotations

import hashlib
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.agent_api.capabilities import AgentCapabilityApiError
from appliance.agent_capabilities import create_agent_capabilities_router
from appliance.approval import APPROVAL_HEADER, HighRiskApprovalService, create_approval_router
from appliance.audit import ApplianceAudit
from runtime.safety.auth.identity import encode_jwt_hs256

JWT_SECRET = "echo-agent-capabilities-test-secret-that-is-long-enough"
PASSWORD = "Agent-capability-test-password-42"
PASSWORD_HASH = hashlib.sha256(PASSWORD.encode()).hexdigest()
PLAN_ID = "a" * 64


class _Bridge:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(
        self,
        operation: str,
        capability_id: str,
        *,
        actor_id: str,
        roles: tuple[str, ...] = ("admin",),
        **kwargs: Any,
    ) -> dict[str, Any]:
        recorded_kwargs = dict(kwargs)
        if isinstance(recorded_kwargs.get("tokens"), dict):
            recorded_kwargs["tokens"] = dict(recorded_kwargs["tokens"])
        self.calls.append(
            {
                "operation": operation,
                "capability_id": capability_id,
                "actor_id": actor_id,
                "roles": roles,
                **recorded_kwargs,
            }
        )
        if capability_id == "missing":
            raise AgentCapabilityApiError(
                "CAPABILITY_NOT_FOUND",
                "private Agent state: /var/lib/echo-agent/capabilities.db",
            )
        if capability_id == "unknown-error":
            raise AgentCapabilityApiError(
                "PRIVATE_AGENT_FAILURE",
                "private Agent endpoint: http://agent.internal:8010",
            )
        if operation.endswith("_plan"):
            return {
                "schema": f"echo.capability_{operation}.v1",
                "service_schema": "echo.capability-service.v1",
                "capability_id": capability_id,
                "plan_id": PLAN_ID,
                "can_install": True,
                "can_uninstall": True,
                "can_rollback": True,
                "permissions": ["account.credentials"],
                "blockers": [],
                "changes": ["verify_publisher_signature"],
                "private_path": "/var/lib/echo-agent/packages",
            }
        if operation == "status":
            return {
                "schema": "echo.capability-service.v1",
                "capability_id": capability_id,
                "status": {"connected": False},
            }
        if operation == "connection_profile":
            return {
                "schema": "echo.capability-service.v1",
                "capability_id": capability_id,
                "auth_mode": "token",
                "mode": "principal_credentials",
                "can_connect": True,
                "connected": False,
                "minimum_credentials": 1,
                "fields": [
                    {
                        "key": "access_token",
                        "label": "Access Token",
                        "label_zh": "访问令牌",
                        "secret": True,
                        "required": False,
                    }
                ],
                "blockers": [],
                "access_token": "must-never-cross-the-boundary",
            }
        return {
            "schema": "echo.capability-service.v1",
            "operation": operation,
            "capability": {
                "id": capability_id,
                "installed": operation != "uninstall",
                "privateStatePath": "/var/lib/echo-agent/state.db",
            },
            "result": {
                "connected": operation == "connect",
                "credentials": {"API_TOKEN": "must-never-cross-the-boundary"},
            },
        }


def _client(
    tmp_path,
    *,
    actor: str = "local:admin",
) -> tuple[TestClient, _Bridge, ApplianceAudit]:
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=PASSWORD_HASH,
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"agent-capability-tests",
    )
    bridge = _Bridge()
    app = FastAPI()
    app.include_router(create_approval_router(approval, jwt_secret=JWT_SECRET))
    app.include_router(
        create_agent_capabilities_router(
            bridge,
            jwt_secret=JWT_SECRET,
            approval=approval,
            audit=audit,
        )
    )
    client = TestClient(app)
    token = encode_jwt_hs256(
        {"sub": actor, "iat": 0, "exp": 9_999_999_999},
        secret=JWT_SECRET,
    )
    client.cookies.set("echo_session", token)
    return client, bridge, audit


def _approve(client: TestClient, action: str) -> str:
    response = client.post(
        "/api/appliance/approvals",
        json={"action": action, "target": PLAN_ID, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()["approvalToken"]


def test_agent_capability_routes_require_echo_authentication(tmp_path) -> None:
    client, _bridge, _audit = _client(tmp_path)
    client.cookies.clear()

    assert (
        client.post(
            "/api/appliance/agent-capabilities/plans/install",
            json={"capabilityId": "demo-token"},
        ).status_code
        == 401
    )


def test_non_admin_session_cannot_be_elevated_to_agent_admin(tmp_path) -> None:
    client, bridge, _audit = _client(tmp_path, actor="local:member")

    response = client.post(
        "/api/appliance/agent-capabilities/plans/install",
        json={"capabilityId": "demo-token"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "device operator permission is required"}
    assert bridge.calls == []


def test_member_session_can_only_manage_its_own_connection_and_activation(tmp_path) -> None:
    client, bridge, _audit = _client(tmp_path, actor="local:member")

    connected = client.post(
        "/api/appliance/agent-capabilities/connect",
        json={"capabilityId": "demo-token", "tokens": {"access_token": "member-secret"}},
    )
    disconnected = client.post(
        "/api/appliance/agent-capabilities/demo-token/disconnect",
    )
    disabled = client.post(
        "/api/appliance/agent-capabilities/demo-token/disable",
    )

    assert connected.status_code == 200, connected.text
    assert disconnected.status_code == 200, disconnected.text
    assert disabled.status_code == 200, disabled.text
    scoped_calls = bridge.calls[-3:]
    assert [call["operation"] for call in scoped_calls] == [
        "connect",
        "disconnect",
        "disable",
    ]
    assert all(call["actor_id"] == "local:member" for call in scoped_calls)
    assert all(call["roles"] == () for call in scoped_calls)


def test_device_install_requires_bound_step_up_and_is_audited(tmp_path) -> None:
    client, bridge, audit = _client(tmp_path)
    plan = client.post(
        "/api/appliance/agent-capabilities/plans/install",
        json={"capabilityId": "demo-token"},
    )
    assert plan.status_code == 200
    assert "private_path" not in plan.json()

    denied = client.post(
        "/api/appliance/agent-capabilities/plans/install/apply",
        json={"capabilityId": "demo-token", "planId": PLAN_ID},
    )
    assert denied.status_code == 403

    approval_token = _approve(client, "agent.capability.install")
    applied = client.post(
        "/api/appliance/agent-capabilities/plans/install/apply",
        json={"capabilityId": "demo-token", "planId": PLAN_ID},
        headers={APPROVAL_HEADER: approval_token},
    )
    assert applied.status_code == 200, applied.text
    assert bridge.calls[-1] == {
        "operation": "install",
        "capability_id": "demo-token",
        "actor_id": "local:admin",
        "roles": ("admin",),
        "plan_id": PLAN_ID,
    }
    log = audit.path.read_text(encoding="utf-8")
    assert "agent.capability.install" in log
    assert "demo-token" in log


def test_authorize_and_token_connect_never_return_or_audit_secret(tmp_path) -> None:
    client, bridge, audit = _client(tmp_path)
    approval_token = _approve(client, "agent.capability.authorize")
    authorized = client.post(
        "/api/appliance/agent-capabilities/plans/authorize/apply",
        json={
            "capabilityId": "demo-token",
            "planId": PLAN_ID,
            "permissions": ["account.credentials"],
            "activate": True,
        },
        headers={APPROVAL_HEADER: approval_token},
    )
    assert authorized.status_code == 200, authorized.text

    secret = "never-cross-the-echo-boundary"
    connected = client.post(
        "/api/appliance/agent-capabilities/connect",
        json={"capabilityId": "demo-token", "tokens": {"API_TOKEN": secret}},
    )
    assert connected.status_code == 200, connected.text
    assert secret not in connected.text
    assert "must-never-cross-the-boundary" not in connected.text
    assert secret not in audit.path.read_text(encoding="utf-8")
    assert bridge.calls[-1]["tokens"] == {"API_TOKEN": secret}


def test_connection_profile_is_authenticated_and_principal_scoped(tmp_path) -> None:
    client, bridge, _audit = _client(tmp_path)

    profile = client.get("/api/appliance/agent-capabilities/demo-token/connection-profile")

    assert profile.status_code == 200, profile.text
    assert profile.json()["fields"][0]["key"] == "access_token"
    assert "must-never-cross-the-boundary" not in profile.text
    assert bridge.calls[-1] == {
        "operation": "connection_profile",
        "capability_id": "demo-token",
        "actor_id": "local:admin",
        "roles": ("admin",),
    }


def test_failed_connection_is_audited_without_secret(tmp_path) -> None:
    client, bridge, audit = _client(tmp_path)
    secret = "failed-secret-must-not-be-audited"

    original_invoke = bridge.invoke

    def fail_connect(*args, **kwargs):
        if args[0] == "connect":
            raise AgentCapabilityApiError("CONNECT_BLOCKED", "private connector failure")
        return original_invoke(*args, **kwargs)

    bridge.invoke = fail_connect  # type: ignore[method-assign]
    response = client.post(
        "/api/appliance/agent-capabilities/connect",
        json={"capabilityId": "demo-token", "tokens": {"access_token": secret}},
    )

    assert response.status_code == 409
    log = audit.path.read_text(encoding="utf-8")
    assert "agent.capability.connect" in log
    assert '"outcome": "failed"' in log
    assert secret not in log


def test_agent_domain_errors_are_bounded(tmp_path) -> None:
    client, _bridge, _audit = _client(tmp_path)

    response = client.get("/api/appliance/agent-capabilities/missing")

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "CAPABILITY_NOT_FOUND",
            "message": "capability was not found",
        }
    }
    assert "/var/lib" not in response.text

    unknown = client.get("/api/appliance/agent-capabilities/unknown-error")
    assert unknown.status_code == 503
    assert unknown.json() == {
        "detail": {
            "code": "AGENT_CAPABILITY_UNAVAILABLE",
            "message": "Agent capability service is unavailable",
        }
    }
    assert "agent.internal" not in unknown.text


def test_authorize_rejects_unknown_permissions_and_connect_bounds_secrets(tmp_path) -> None:
    client, bridge, _audit = _client(tmp_path)

    permission = client.post(
        "/api/appliance/agent-capabilities/plans/authorize/apply",
        json={
            "capabilityId": "demo-token",
            "planId": PLAN_ID,
            "permissions": ["private.agent.root"],
        },
    )
    oversized_secret = client.post(
        "/api/appliance/agent-capabilities/connect",
        json={"capabilityId": "demo-token", "tokens": {"API_TOKEN": "x" * 8193}},
    )

    assert permission.status_code == 422
    assert oversized_secret.status_code == 422
    assert bridge.calls == []


def test_uninstall_and_rollback_have_distinct_approval_actions(tmp_path) -> None:
    client, bridge, _audit = _client(tmp_path)

    for operation in ("uninstall", "rollback"):
        plan = client.post(
            f"/api/appliance/agent-capabilities/plans/{operation}",
            json={"capabilityId": "demo-token"},
        )
        assert plan.status_code == 200
        token = _approve(client, f"agent.capability.{operation}")
        applied = client.post(
            f"/api/appliance/agent-capabilities/plans/{operation}/apply",
            json={"capabilityId": "demo-token", "planId": PLAN_ID},
            headers={APPROVAL_HEADER: token},
        )
        assert applied.status_code == 200, applied.text

    assert [call["operation"] for call in bridge.calls if "plan_id" in call] == [
        "uninstall",
        "rollback",
    ]
