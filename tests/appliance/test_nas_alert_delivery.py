from __future__ import annotations

import hashlib
import io
import json
import socket
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import nas_alert_delivery
from appliance.approval import APPROVAL_HEADER, HighRiskApprovalService, create_approval_router
from appliance.audit import ApplianceAudit
from appliance.nas_alert_delivery import (
    ALERT_DELIVERY_CONFIGURE_ACTION,
    ALERT_DELIVERY_TEST_ACTION,
    NasAlertDeliveryError,
    NasAlertDeliveryService,
    NasAlertSnapshotSource,
    WebhookDesiredState,
    create_nas_alert_delivery_router,
    validate_alert_delivery_state,
)
from runtime.safety.auth.identity import encode_jwt_hs256

JWT_SECRET = "Nas-Alert-Test-Secret_123456789012345678901234"
PASSWORD = "nas-alert-admin-password"
PASSWORD_HASH = hashlib.sha256(PASSWORD.encode()).hexdigest()


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 8, 8, 0, tzinfo=UTC)
        self.monotonic = 100.0

    def now(self) -> datetime:
        return self.value

    def tick(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)
        self.monotonic += seconds


def _health(alerts=None):
    return {"activeAlerts": list(alerts or [])}


def _ups():
    return {"configured": False, "devices": []}


def _services(alerts=None):
    return {"available": True, "activeAlerts": list(alerts or [])}


def _desired() -> WebhookDesiredState:
    return WebhookDesiredState.model_validate(
        {
            "enabled": True,
            "url": "https://hooks.example.com/echo?key=private-url-secret",
            "bearerToken": "private-bearer-token",
        }
    )


def test_configuration_is_encrypted_and_status_redacts_secrets(tmp_path) -> None:
    service = NasAlertDeliveryService(
        tmp_path,
        encryption_secret=JWT_SECRET,
        storage_reader=_health,
        ups_reader=_ups,
        sender=lambda *_args: None,
    )
    plan = service.plan(_desired())
    status = service.apply(plan["planId"])

    raw = service.path.read_text(encoding="utf-8")
    assert "private-url-secret" not in raw
    assert "private-bearer-token" not in raw
    assert status["destinationHost"] == "hooks.example.com"
    assert status["hasBearerToken"] is True
    assert "url" not in status

    restored = validate_alert_delivery_state(service.path, encryption_secret=JWT_SECRET)
    assert restored["enabled"] is True
    assert restored["url"].endswith("private-url-secret")
    assert restored["bearerToken"] == "private-bearer-token"


def test_corrupt_encrypted_configuration_stops_delivery_and_can_be_replaced(tmp_path) -> None:
    service = NasAlertDeliveryService(tmp_path, encryption_secret=JWT_SECRET)
    service.apply(service.plan(_desired())["planId"])
    envelope = json.loads(service.path.read_text(encoding="utf-8"))
    ciphertext = envelope["ciphertext"]
    envelope["ciphertext"] = ("A" if ciphertext[0] != "A" else "B") + ciphertext[1:]
    service.path.write_text(json.dumps(envelope), encoding="utf-8")

    reloaded = NasAlertDeliveryService(tmp_path, encryption_secret=JWT_SECRET)
    assert reloaded.status()["persistenceHealthy"] is False
    assert reloaded.status()["enabled"] is False
    disabled = WebhookDesiredState.model_validate({"enabled": False})
    repaired = reloaded.apply(reloaded.plan(disabled)["planId"])
    assert repaired["persistenceHealthy"] is True
    assert repaired["configured"] is False


def test_private_webhook_resolution_is_rejected_before_http(monkeypatch) -> None:
    monkeypatch.setattr(
        nas_alert_delivery.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("192.168.1.20", 443))],
    )
    with pytest.raises(NasAlertDeliveryError, match="public addresses"):
        nas_alert_delivery._assert_public_destination("hooks.example.com")


def test_default_sender_pins_the_verified_address_and_preserves_tls_hostname(
    monkeypatch,
) -> None:
    resolved_address = ("93.184.216.34", 443)
    resolutions = 0

    def resolve(*_args, **_kwargs):
        nonlocal resolutions
        resolutions += 1
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                resolved_address,
            )
        ]

    class FakeSocket:
        def __init__(self) -> None:
            self.connected = None
            self.closed = False

        def settimeout(self, _timeout: float) -> None:
            pass

        def connect(self, address) -> None:
            self.connected = address

        def close(self) -> None:
            self.closed = True

    class FakeTlsSocket:
        def __init__(self) -> None:
            self.request = b""

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def settimeout(self, _timeout: float) -> None:
            pass

        def sendall(self, request: bytes) -> None:
            self.request = request

        def makefile(self, _mode: str):
            return io.BytesIO(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")

    raw_socket = FakeSocket()
    tls_socket = FakeTlsSocket()
    tls_hostname = None

    class FakeTlsContext:
        def wrap_socket(self, wrapped, *, server_hostname: str):
            nonlocal tls_hostname
            assert wrapped is raw_socket
            tls_hostname = server_hostname
            return tls_socket

    monkeypatch.setattr(nas_alert_delivery.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(nas_alert_delivery.socket, "socket", lambda *_args: raw_socket)
    monkeypatch.setattr(
        nas_alert_delivery.ssl,
        "create_default_context",
        lambda: FakeTlsContext(),
    )

    nas_alert_delivery._default_sender(
        "https://hooks.example.com/echo?kind=health",
        "hooks.example.com",
        {"Content-Type": "application/json", "Authorization": "Bearer safe-token"},
        {"schema": "echo.test.v1"},
    )

    assert resolutions == 1
    assert raw_socket.connected == resolved_address
    assert tls_hostname == "hooks.example.com"
    assert tls_socket.request.startswith(b"POST /echo?kind=health HTTP/1.1\r\n")
    assert b"Host: hooks.example.com\r\n" in tls_socket.request
    assert b"Authorization: Bearer safe-token\r\n" in tls_socket.request


def test_bearer_token_rejects_header_injection_before_persistence(tmp_path) -> None:
    service = NasAlertDeliveryService(tmp_path, encryption_secret=JWT_SECRET)
    desired = WebhookDesiredState.model_validate(
        {
            "enabled": True,
            "url": "https://hooks.example.com/echo",
            "bearerToken": "safe-token\r\nX-Echo-Injected: true",
        }
    )

    with pytest.raises(NasAlertDeliveryError, match="invalid characters"):
        service.plan(desired)
    assert not service.path.exists()


def test_alert_snapshot_source_shares_one_probe_without_leaking_mutable_state() -> None:
    clock = _Clock()
    storage_reads = 0
    ups_reads = 0
    service_reads = 0

    def storage():
        nonlocal storage_reads
        storage_reads += 1
        return _health()

    def ups():
        nonlocal ups_reads
        ups_reads += 1
        return _ups()

    def services():
        nonlocal service_reads
        service_reads += 1
        return _services()

    source = NasAlertSnapshotSource(
        storage_reader=storage,
        ups_reader=ups,
        service_reader=services,
        cache_seconds=30,
        monotonic=lambda: clock.monotonic,
    )

    first = source.read()
    first.append({"id": "caller-mutation"})
    assert source.read() == []
    assert storage_reads == 1
    assert ups_reads == 1
    assert service_reads == 1

    clock.tick(31)
    assert source.read() == []
    assert storage_reads == 2
    assert ups_reads == 2
    assert service_reads == 2


def test_service_failure_is_normalized_for_headless_delivery() -> None:
    alerts = nas_alert_delivery.collect_nas_alerts(
        _health(),
        _ups(),
        _services(
            [
                {
                    "id": "echo-appliance.service:service.restart_storm",
                    "code": "service.restart_storm",
                    "severity": "critical",
                    "resource": "echo-appliance.service",
                    "message": "系统服务反复重启",
                }
            ]
        ),
    )

    assert len(alerts) == 1
    assert alerts[0]["source"] == "service"
    assert alerts[0]["code"] == "service.restart_storm"
    assert alerts[0]["severity"] == "critical"
    assert alerts[0]["id"] != "echo-appliance.service:service.restart_storm"


def test_delivery_deduplicates_active_alerts_and_rearms_after_resolution(tmp_path) -> None:
    active = [
        {
            "id": "disk-one",
            "code": "smart.failed",
            "severity": "critical",
            "resource": "/dev/sda",
            "message": "SMART failure",
        }
    ]
    sent: list[dict] = []
    service = NasAlertDeliveryService(
        tmp_path,
        encryption_secret=JWT_SECRET,
        storage_reader=lambda: _health(active),
        ups_reader=_ups,
        sender=lambda _url, _host, _headers, payload: sent.append(dict(payload)),
    )
    service.apply(service.plan(_desired())["planId"])

    assert service.poll()["consecutiveFailures"] == 0
    service.poll()
    assert len(sent) == 1
    assert sent[0]["severity"] == "critical"

    active.clear()
    service.poll()
    active.append(
        {
            "id": "disk-one",
            "code": "smart.failed",
            "severity": "critical",
            "resource": "/dev/sda",
            "message": "SMART failure",
        }
    )
    service.poll()
    assert len(sent) == 2


def test_delivery_retries_with_backoff_without_losing_alert(tmp_path) -> None:
    clock = _Clock()
    attempts = 0

    def sender(*_args) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise NasAlertDeliveryError(503, "simulated")

    service = NasAlertDeliveryService(
        tmp_path,
        encryption_secret=JWT_SECRET,
        storage_reader=lambda: _health(
            [
                {
                    "id": "volume-one",
                    "code": "filesystem.usage",
                    "severity": "warning",
                    "resource": "/srv/data",
                    "message": "volume is nearly full",
                }
            ]
        ),
        ups_reader=_ups,
        sender=sender,
        clock=clock.now,
        monotonic=lambda: clock.monotonic,
    )
    service.apply(service.plan(_desired())["planId"])

    failed = service.poll()
    assert attempts == 1
    assert failed["consecutiveFailures"] == 1
    assert failed["lastError"] == "delivery_failed"
    service.poll()
    assert attempts == 1

    clock.tick(30)
    recovered = service.poll()
    assert attempts == 2
    assert recovered["consecutiveFailures"] == 0
    assert recovered["lastSuccessAt"] is not None


def _client(tmp_path, sender, *, actor: str = "local:admin") -> TestClient:
    audit = ApplianceAudit.from_data_dir(tmp_path / "audit", jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=PASSWORD_HASH,
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"n" * 32,
    )
    service = NasAlertDeliveryService(
        tmp_path / "state",
        encryption_secret=JWT_SECRET,
        storage_reader=_health,
        ups_reader=_ups,
        sender=sender,
    )
    app = FastAPI()
    app.include_router(create_approval_router(approval, jwt_secret=JWT_SECRET))
    app.include_router(
        create_nas_alert_delivery_router(
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


def _approval(client: TestClient, action: str, target: str) -> str:
    response = client.post(
        "/api/appliance/approvals",
        json={"action": action, "target": target, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["approvalToken"])


def test_router_requires_operator_approval_and_audits_without_secrets(tmp_path) -> None:
    sent: list[dict] = []
    client = _client(
        tmp_path,
        lambda _url, _host, _headers, payload: sent.append(dict(payload)),
    )
    plan_response = client.post(
        "/api/appliance/notifications/webhook/plan",
        json={
            "enabled": True,
            "url": "https://hooks.example.com/echo?key=private-url-secret",
            "bearerToken": "private-bearer-token",
        },
    )
    assert plan_response.status_code == 200, plan_response.text
    plan_id = plan_response.json()["planId"]
    assert (
        client.post(
            "/api/appliance/notifications/webhook/apply", json={"planId": plan_id}
        ).status_code
        == 403
    )

    configured = client.post(
        "/api/appliance/notifications/webhook/apply",
        json={"planId": plan_id},
        headers={APPROVAL_HEADER: _approval(client, ALERT_DELIVERY_CONFIGURE_ACTION, plan_id)},
    )
    assert configured.status_code == 200, configured.text
    revision = configured.json()["revision"]
    tested = client.post(
        "/api/appliance/notifications/webhook/test",
        headers={APPROVAL_HEADER: _approval(client, ALERT_DELIVERY_TEST_ACTION, revision)},
    )
    assert tested.status_code == 200, tested.text
    assert sent[0]["test"] is True

    audit_text = (tmp_path / "audit" / "appliance-audit.jsonl").read_text(encoding="utf-8")
    assert ALERT_DELIVERY_CONFIGURE_ACTION in audit_text
    assert ALERT_DELIVERY_TEST_ACTION in audit_text
    assert "private-url-secret" not in audit_text
    assert "private-bearer-token" not in audit_text


def test_family_member_cannot_inspect_or_configure_alert_delivery(tmp_path) -> None:
    client = _client(tmp_path, lambda *_args: None, actor="local:alice")
    assert client.get("/api/appliance/notifications/webhook").status_code == 403
    assert (
        client.post(
            "/api/appliance/notifications/webhook/plan",
            json={"enabled": False},
        ).status_code
        == 403
    )
