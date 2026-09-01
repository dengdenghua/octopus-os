"""Device account controls must affect the live Agent and every old JWT."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from appliance.agent_api.auth import hash_password
from appliance.approval import APPROVAL_HEADER
from appliance.extension import register_app
from runtime.platform.extensions import AppExtensionContext


def _configured_app(tmp_path, monkeypatch) -> FastAPI:
    monkeypatch.setenv("ECHO_APPLIANCE", "1")
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ECHO_NAS_ROOT", str(tmp_path / "nas"))
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD", "original-device-pass")
    (tmp_path / "nas").mkdir()
    app = FastAPI()

    @app.get("/api/agent/header-probe")
    def header_probe(request: Request) -> dict[str, str | None]:
        return {
            "authorization": request.headers.get("authorization"),
            "session": request.cookies.get("echo_session"),
        }

    @app.websocket("/api/agent/socket-probe")
    async def socket_probe(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_json(
            {
                "authorization": websocket.headers.get("authorization"),
                "protocol": websocket.headers.get("sec-websocket-protocol"),
                "token": websocket.query_params.get("token"),
            }
        )
        await websocket.close()

    @app.websocket("/api/agent/live-socket")
    async def live_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        while True:
            message = await websocket.receive_text()
            await websocket.send_text(f"echo:{message}")

    register_app(app, AppExtensionContext(identity_store=None))
    return app


def _login(client: TestClient, password: str) -> tuple[int, str | None]:
    response = client.post(
        "/api/auth/local/login",
        json={"username": "admin", "password": password},
        headers={"Origin": "http://testserver"},
    )
    token = response.json().get("access_token") if response.status_code == 200 else None
    return response.status_code, token


def _approval(
    client: TestClient,
    *,
    action: str,
    target: str,
    password: str,
    token: str,
) -> str:
    response = client.post(
        "/api/appliance/approvals",
        headers={"Authorization": f"Bearer {token}"},
        json={"action": action, "target": target, "password": password},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["approvalToken"])


def test_revoke_and_password_rotation_change_live_auth(tmp_path, monkeypatch) -> None:
    app = _configured_app(tmp_path, monkeypatch)

    with TestClient(app) as client:
        status, first_token = _login(client, "original-device-pass")
        assert status == 200 and first_token
        stale_app_approval = _approval(
            client,
            action="app.start",
            target="a" * 12,
            password="original-device-pass",
            token=first_token,
        )
        revoke_approval = _approval(
            client,
            action="sessions.revoke",
            target="all",
            password="original-device-pass",
            token=first_token,
        )
        with client.websocket_connect(
            "/api/agent/live-socket",
            headers={"Authorization": f"Bearer {first_token}"},
        ) as live_socket:
            live_socket.send_text("before")
            assert live_socket.receive_text() == "echo:before"
            revoked = client.post(
                "/api/appliance/sessions/revoke",
                headers={
                    "Authorization": f"Bearer {first_token}",
                    APPROVAL_HEADER: revoke_approval,
                },
            )
            with pytest.raises(WebSocketDisconnect) as disconnected:
                live_socket.receive_text()
            assert disconnected.value.code == 4401
        assert revoked.status_code == 200, revoked.text
        assert "echo_session=" in revoked.headers["set-cookie"]
        assert "Max-Age=0" in revoked.headers["set-cookie"]

        # The old token is removed before both Echo and arbitrary Agent routes.
        assert (
            client.get(
                "/api/appliance/apps",
                headers={"Authorization": f"Bearer {first_token}"},
            ).status_code
            == 401
        )
        probe = client.get(
            "/api/agent/header-probe",
            headers={
                "Authorization": f"Bearer {first_token}",
                "Cookie": f"echo_session={first_token}",
            },
        )
        assert probe.json() == {"authorization": None, "session": None}
        with client.websocket_connect(
            f"/api/agent/socket-probe?token={first_token}",
            headers={
                "Authorization": f"Bearer {first_token}",
                "Sec-WebSocket-Protocol": f"bearer, {first_token}",
            },
        ) as socket:
            assert socket.receive_json() == {
                "authorization": None,
                "protocol": None,
                "token": None,
            }

        # Session revocation does not change the password, but every approval
        # issued before it becomes cryptographically invalid.
        status, second_token = _login(client, "original-device-pass")
        assert status == 200 and second_token
        replay = client.post(
            f"/api/appliance/apps/{'a' * 12}/start",
            headers={
                "Authorization": f"Bearer {second_token}",
                APPROVAL_HEADER: stale_app_approval,
            },
        )
        assert replay.status_code == 403

        rotate_approval = _approval(
            client,
            action="credentials.rotate",
            target="admin",
            password="original-device-pass",
            token=second_token,
        )
        rotated = client.post(
            "/api/appliance/credentials/rotate",
            headers={
                "Authorization": f"Bearer {second_token}",
                APPROVAL_HEADER: rotate_approval,
            },
            json={"newPassword": "replacement-device-pass"},
        )
        assert rotated.status_code == 200, rotated.text
        assert (
            client.get(
                "/api/appliance/apps",
                headers={"Authorization": f"Bearer {second_token}"},
            ).status_code
            == 401
        )
        assert _login(client, "original-device-pass")[0] == 401
        new_status, third_token = _login(client, "replacement-device-pass")
        assert new_status == 200 and third_token

        old_password_approval = client.post(
            "/api/appliance/approvals",
            headers={"Authorization": f"Bearer {third_token}"},
            json={
                "action": "sessions.revoke",
                "target": "all",
                "password": "original-device-pass",
            },
        )
        assert old_password_approval.status_code == 403
        assert _approval(
            client,
            action="sessions.revoke",
            target="all",
            password="replacement-device-pass",
            token=third_token,
        )

    store = json.loads((tmp_path / "data" / "appliance-auth.json").read_text())
    assert store["session_not_before"] > 0
    assert store["password_hash"] == app.state.echo_appliance_auth_config.users["admin"]
    assert "original-device-pass" not in json.dumps(store)
    assert "replacement-device-pass" not in json.dumps(store)

    audit_text = (tmp_path / "data" / "appliance-audit.jsonl").read_text()
    audit_actions = {
        json.loads(line)["payload"]["action"] for line in audit_text.splitlines() if line.strip()
    }
    assert "sessions.revoke" in audit_actions
    assert "credentials.rotate" in audit_actions
    assert "original-device-pass" not in audit_text
    assert "replacement-device-pass" not in audit_text


def test_agent_boot_auth_route_is_overridden_by_live_device_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ECHO_APPLIANCE", "1")
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ECHO_NAS_ROOT", str(tmp_path / "nas"))
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD", "authoritative-device-pass")
    (tmp_path / "nas").mkdir()
    app = FastAPI()

    @app.post("/api/auth/local/login")
    def stale_agent_login() -> dict[str, str]:
        return {"source": "stale-agent-route"}

    register_app(app, AppExtensionContext(identity_store=None))

    with TestClient(app) as client:
        response = client.post(
            "/api/auth/local/login",
            json={"username": "admin", "password": "authoritative-device-pass"},
            headers={"Origin": "http://testserver"},
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json().get("source") is None


def test_member_revocation_closes_only_the_members_live_websocket(tmp_path, monkeypatch) -> None:
    app = _configured_app(tmp_path, monkeypatch)
    security = app.state.echo_appliance_account_security
    password = "Alice-independent-Echo-42!"
    active = {
        "display_name": "Alice",
        "role": "member",
        "password_hash": hash_password(password),
        "omv_username": "alice",
        "active": True,
    }
    security.persist_member_account(
        username="alice",
        account=active,
        expect_exists=False,
    )
    security.wait_for_account_login_window("alice")

    with TestClient(app) as client:
        response = client.post(
            "/api/auth/local/login",
            headers={"Origin": "http://testserver"},
            json={"username": "alice", "password": password},
        )
        assert response.status_code == 200, response.text
        token = response.json()["access_token"]
        with client.websocket_connect(
            "/api/agent/live-socket",
            headers={"Authorization": f"Bearer {token}"},
        ) as socket:
            socket.send_text("before")
            assert socket.receive_text() == "echo:before"
            security.persist_member_account(
                username="alice",
                account={**active, "active": False},
                expect_exists=True,
            )
            with pytest.raises(WebSocketDisconnect) as disconnected:
                socket.receive_text()
            assert disconnected.value.code == 4401
