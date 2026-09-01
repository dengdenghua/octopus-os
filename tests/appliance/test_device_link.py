"""Echo device link: opt-in listener, bounded pairing and revocation."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.approval import APPROVAL_HEADER, HighRiskApprovalService, create_approval_router
from appliance.audit import ApplianceAudit
from appliance.device_link import DeviceLinkError, DeviceLinkService, create_device_link_router
from appliance.remote_access import RemoteAccessService
from runtime.safety.auth.identity import encode_jwt_hs256

JWT_SECRET = "Device-Link-Secret_123456789012345678901234"
PASSWORD = "device-link-admin-password"
PASSWORD_HASH = hashlib.sha256(PASSWORD.encode()).hexdigest()


class _Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _Pool:
    def __init__(self) -> None:
        self.devices: dict[str, object] = {}

    def all(self) -> list[object]:
        return list(self.devices.values())


class _Server:
    def __init__(self) -> None:
        self.port = 8765
        self.auth_token = ""
        self._server = None
        self._connections: dict[str, object] = {}

    def _check_auth(self, _message) -> bool:
        return False


class _Coordinator:
    def __init__(self) -> None:
        self.ws_server = _Server()
        self.pool = _Pool()
        self.starts = 0
        self.stops = 0

    async def start(self) -> None:
        self.starts += 1
        self.ws_server._server = object()

    async def stop(self) -> None:
        self.stops += 1
        self.ws_server._server = None


class _Connection:
    def __init__(self) -> None:
        self.closed: tuple[int, str] | None = None

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = code, reason


def _service(tmp_path, *, clock=None):
    coordinator = _Coordinator()
    service = DeviceLinkService(
        data_dir=tmp_path,
        jwt_secret=JWT_SECRET,
        coordinator_factory=lambda: coordinator,
        device_sync_port=8000,
        clock=clock or _Clock(),
        lan_ip_resolver=lambda: "192.168.50.10",
    )
    return service, coordinator


def _authorized_client(
    tmp_path,
    service: DeviceLinkService,
    *,
    actor: str = "local:admin",
) -> TestClient:
    audit = ApplianceAudit.from_data_dir(tmp_path / "audit", jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=PASSWORD_HASH,
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"d" * 32,
    )
    app = FastAPI()
    app.include_router(create_approval_router(approval, jwt_secret=JWT_SECRET))
    app.include_router(
        create_device_link_router(
            service,
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
    return client


def test_family_member_cannot_inspect_or_manage_device_links(tmp_path) -> None:
    service, _coordinator = _service(tmp_path)
    client = _authorized_client(tmp_path, service, actor="local:alice")

    assert client.get("/api/appliance/device-link").status_code == 403
    assert client.post("/api/appliance/device-link/enable").status_code == 403


def _approval(client: TestClient, action: str, target: str) -> str:
    response = client.post(
        "/api/appliance/approvals",
        json={"action": action, "target": target, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["approvalToken"])


def test_managed_link_is_off_by_default_and_pairing_is_device_bound(tmp_path) -> None:
    service, coordinator = _service(tmp_path)

    initial = service.status()
    assert initial["schema"] == "echo.device-link.v1"
    assert initial["enabled"] is False
    assert initial["listenerActive"] is False
    assert initial["remoteAccess"]["available"] is False

    import asyncio

    enabled = asyncio.run(service.enable())
    assert enabled["enabled"] is True
    assert enabled["listenerActive"] is True
    assert coordinator.starts == 1

    invitation = service.create_pairing_invitation()
    query = parse_qs(urlparse(invitation["connectString"]).query)
    token = query["token"][0]
    assert query["ws"] == ["ws://192.168.50.10:8765"]
    assert query["sync"] == ["http://192.168.50.10:8000"]
    assert invitation["deviceSync"] == {
        "baseUrl": "http://192.168.50.10:8000",
        "protocolVersion": 1,
        "transport": "lan-http",
    }
    assert invitation["credentialMode"] == "per-device"

    hello = {
        "params": {
            "tentacle_id": "phone-1",
            "auth_token": token,
            "platform": "android",
            "brand": "Echo",
            "model": "Pocket",
            "version": "1.2.3",
        }
    }
    assert coordinator.ws_server._check_auth(hello) is True
    assert (
        coordinator.ws_server._check_auth(
            {"params": {"tentacle_id": "phone-2", "auth_token": token}}
        )
        is False
    )
    assert "token" not in (tmp_path / "device-link.json").read_text(encoding="utf-8")

    status = service.status()
    assert status["pairedDeviceCount"] == 1
    assert status["devices"][0]["id"] == "phone-1"
    assert status["devices"][0]["platform"] == "android"
    assert status["devices"][0]["individuallyRevocable"] is True


def test_pairing_prefers_the_admins_reachable_lan_host(tmp_path) -> None:
    service, _coordinator = _service(tmp_path)
    import asyncio

    asyncio.run(service.enable())
    invitation = service.create_pairing_invitation(request_host="192.168.88.20")
    query = parse_qs(urlparse(invitation["connectString"]).query)

    assert query["ws"] == ["ws://192.168.88.20:8765"]
    assert query["sync"] == ["http://192.168.88.20:8000"]


@pytest.mark.parametrize(
    "host",
    ["203.0.113.20", "echo.example.com", "localhost", "host/path", "[::1]"],
)
def test_pairing_rejects_unsafe_request_hosts_and_uses_the_lan_resolver(
    tmp_path, host: str
) -> None:
    service, _coordinator = _service(tmp_path)
    import asyncio

    asyncio.run(service.enable())
    invitation = service.create_pairing_invitation(request_host=host)

    assert invitation["wsUrl"] == "ws://192.168.50.10:8765"


def test_configured_device_link_host_must_be_lan_scoped(tmp_path) -> None:
    with pytest.raises(ValueError, match="public host"):
        DeviceLinkService(
            data_dir=tmp_path,
            jwt_secret=JWT_SECRET,
            public_host="echo.example.com",
        )


def test_container_mode_never_advertises_its_private_bridge_address(tmp_path) -> None:
    coordinator = _Coordinator()
    service = DeviceLinkService(
        data_dir=tmp_path,
        jwt_secret=JWT_SECRET,
        coordinator_factory=lambda: coordinator,
        lan_ip_resolver=lambda: "172.20.0.7",
        allow_host_resolver_fallback=False,
    )
    import asyncio

    asyncio.run(service.enable())
    with pytest.raises(DeviceLinkError, match="reachable LAN device host"):
        service.create_pairing_invitation(request_host="echo.example.com")


def test_device_link_projects_remote_web_without_claiming_remote_tentacle(tmp_path) -> None:
    remote = RemoteAccessService(
        provider="tailscale-sidecar",
        endpoint="https://echo-os.example.ts.net",
        probe=lambda: True,
    )
    import asyncio

    asyncio.run(remote.refresh())
    remote.set_sync_available(True)
    coordinator = _Coordinator()
    service = DeviceLinkService(
        data_dir=tmp_path,
        jwt_secret=JWT_SECRET,
        coordinator_factory=lambda: coordinator,
        lan_ip_resolver=lambda: "192.168.50.10",
        remote_access=remote,
    )

    status = service.status()

    assert status["remoteAccess"]["available"] is True
    assert status["remoteAccess"]["features"]["desktopWeb"] is True
    assert status["remoteAccess"]["features"]["deviceLink"] is False
    assert status["transport"]["encrypted"] is False

    asyncio.run(service.enable())
    invitation = service.create_pairing_invitation()
    query = parse_qs(urlparse(invitation["connectString"]).query)
    assert query["sync"] == ["https://echo-os.example.ts.net"]
    assert invitation["deviceSync"]["transport"] == "tailnet-https"


def test_pairing_does_not_advertise_an_unconfigured_sync_origin(tmp_path) -> None:
    coordinator = _Coordinator()
    service = DeviceLinkService(
        data_dir=tmp_path,
        jwt_secret=JWT_SECRET,
        coordinator_factory=lambda: coordinator,
        lan_ip_resolver=lambda: "192.168.50.10",
    )
    import asyncio

    asyncio.run(service.enable())
    invitation = service.create_pairing_invitation()

    assert "deviceSync" not in invitation
    assert "sync" not in parse_qs(urlparse(invitation["connectString"]).query)


def test_invitation_expires_and_revoke_closes_live_connection(tmp_path) -> None:
    clock = _Clock()
    service, coordinator = _service(tmp_path, clock=clock)

    import asyncio

    asyncio.run(service.enable())
    invitation = service.create_pairing_invitation()
    token = parse_qs(urlparse(invitation["connectString"]).query)["token"][0]
    clock.now += 301
    assert (
        coordinator.ws_server._check_auth(
            {"params": {"tentacle_id": "late-phone", "auth_token": token}}
        )
        is False
    )

    invitation = service.create_pairing_invitation()
    token = parse_qs(urlparse(invitation["connectString"]).query)["token"][0]
    hello = {"params": {"tentacle_id": "phone-1", "auth_token": token}}
    assert coordinator.ws_server._check_auth(hello) is True
    connection = _Connection()
    coordinator.ws_server._connections["phone-1"] = connection

    result = asyncio.run(service.revoke_device("phone-1"))
    assert result["pairedDeviceCount"] == 0
    assert connection.closed == (1008, "device link revoked")
    assert coordinator.ws_server._check_auth(hello) is False


def test_device_link_router_requires_auth_and_step_up(tmp_path) -> None:
    service, coordinator = _service(tmp_path / "state")
    client = _authorized_client(tmp_path, service)
    anonymous = TestClient(client.app)

    assert anonymous.get("/api/appliance/device-link").status_code == 401
    assert client.post("/api/appliance/device-link/enable").status_code == 403

    approval = _approval(client, "device-link.enable", "lan")
    response = client.post(
        "/api/appliance/device-link/enable",
        headers={APPROVAL_HEADER: approval},
    )
    assert response.status_code == 200

    assert client.post("/api/appliance/device-link/pairing-invitations").status_code == 403
    approval = _approval(client, "device-link.pair", "lan")
    invitation = client.post(
        "/api/appliance/device-link/pairing-invitations",
        headers={APPROVAL_HEADER: approval},
    )
    assert invitation.status_code == 200
    assert "connectString" in invitation.json()
    assert "token" not in invitation.json()

    token = parse_qs(urlparse(invitation.json()["connectString"]).query)["token"][0]
    coordinator.ws_server._check_auth(
        {"params": {"tentacle_id": "router-phone", "auth_token": token}}
    )
    approval = _approval(client, "device-link.device.revoke", "router-phone")
    revoked = client.delete(
        "/api/appliance/device-link/devices/router-phone",
        headers={APPROVAL_HEADER: approval},
    )
    assert revoked.status_code == 200
    assert revoked.json()["pairedDeviceCount"] == 0


def test_external_agent_coordinator_is_reused_without_false_revocation_claims(tmp_path) -> None:
    coordinator = _Coordinator()
    coordinator.ws_server.auth_token = "existing-agent-token"
    coordinator.ws_server._server = object()
    device = SimpleNamespace(
        tentacle_id="legacy-phone",
        tentacle_type=SimpleNamespace(value="mobile"),
        platform="android",
        status=SimpleNamespace(value="online"),
        is_online=True,
        is_busy=False,
        capabilities=["android.tap"],
        meta={"brand": "Echo", "model": "Legacy"},
    )
    coordinator.pool.devices["legacy-phone"] = device
    service = DeviceLinkService(
        data_dir=tmp_path,
        jwt_secret=JWT_SECRET,
        coordinator=coordinator,
        lan_ip_resolver=lambda: "192.168.1.2",
    )

    status = service.status()
    assert status["mode"] == "agent-shared"
    assert status["canManageListener"] is False
    assert status["devices"][0]["individuallyRevocable"] is False
    invitation = service.create_pairing_invitation()
    assert invitation["credentialMode"] == "shared"
    assert "existing-agent-token" in invitation["connectString"]
    assert "deviceSync" not in invitation
