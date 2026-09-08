from __future__ import annotations

import hashlib
import socket
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import nas_email_alert_delivery
from appliance.approval import APPROVAL_HEADER, HighRiskApprovalService, create_approval_router
from appliance.audit import ApplianceAudit
from appliance.nas_alert_delivery import NasAlertDeliveryError
from appliance.nas_email_alert_delivery import (
    EMAIL_ALERT_CONFIGURE_ACTION,
    EMAIL_ALERT_TEST_ACTION,
    EmailDesiredState,
    NasEmailAlertDeliveryService,
    create_nas_email_alert_delivery_router,
    validate_email_alert_delivery_state,
)
from runtime.safety.auth.identity import encode_jwt_hs256

JWT_SECRET = "Nas-Email-Alert-Test-Secret_1234567890123456789"
PASSWORD = "nas-email-alert-admin-password"
PASSWORD_HASH = hashlib.sha256(PASSWORD.encode()).hexdigest()


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
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


def _desired() -> EmailDesiredState:
    return EmailDesiredState.model_validate(_request())


def _request() -> dict[str, object]:
    return {
        "enabled": True,
        "smtpHost": "smtp.example.com",
        "smtpPort": 465,
        "username": "echo-alerts@example.com",
        "password": "private-app-password",
        "fromAddress": "echo-alerts@example.com",
        "recipient": "owner@example.net",
    }


def test_email_configuration_is_encrypted_and_status_redacts_secrets(tmp_path) -> None:
    service = NasEmailAlertDeliveryService(
        tmp_path,
        encryption_secret=JWT_SECRET,
        storage_reader=_health,
        ups_reader=_ups,
        sender=lambda *_args: None,
    )
    status = service.apply(service.plan(_desired())["planId"])

    raw = service.path.read_text(encoding="utf-8")
    assert "private-app-password" not in raw
    assert "echo-alerts@example.com" not in raw
    assert "owner@example.net" not in raw
    assert status["destinationHost"] == "smtp.example.com"
    assert status["recipientHint"] == "o***@example.net"
    assert status["hasCredentials"] is True
    assert "username" not in status
    assert "password" not in status

    restored = validate_email_alert_delivery_state(
        service.path,
        encryption_secret=JWT_SECRET,
    )
    assert restored["enabled"] is True
    assert restored["password"] == "private-app-password"
    assert restored["recipient"] == "owner@example.net"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("smtpHost", "127.0.0.1", "DNS name"),
        ("smtpPort", 25, "465 or 587"),
        ("password", "safe\r\nAUTH PLAIN injected", "invalid characters"),
        ("recipient", "Owner <owner@example.net>", "address is invalid"),
    ],
)
def test_unsafe_email_configuration_is_rejected_before_persistence(
    tmp_path,
    field: str,
    value,
    message: str,
) -> None:
    service = NasEmailAlertDeliveryService(tmp_path, encryption_secret=JWT_SECRET)
    desired = _request()
    desired[field] = value

    with pytest.raises((ValueError, NasAlertDeliveryError), match=message):
        service.plan(EmailDesiredState.model_validate(desired))
    assert not service.path.exists()


def test_private_smtp_resolution_is_rejected_before_connection(monkeypatch) -> None:
    monkeypatch.setattr(
        nas_email_alert_delivery.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.8", 465))
        ],
    )
    with pytest.raises(NasAlertDeliveryError, match="public addresses"):
        nas_email_alert_delivery._public_smtp_addresses("smtp.example.com", 465)


def test_implicit_tls_socket_pins_verified_address_and_preserves_sni(monkeypatch) -> None:
    pinned = ("93.184.216.34", 465)

    class FakeSocket:
        def __init__(self) -> None:
            self.timeout = None
            self.connected = None
            self.closed = False

        def settimeout(self, timeout: float) -> None:
            self.timeout = timeout

        def connect(self, address) -> None:
            self.connected = address

        def close(self) -> None:
            self.closed = True

    raw_socket = FakeSocket()
    wrapped_socket = object()
    sni = None

    class FakeContext:
        def wrap_socket(self, wrapped, *, server_hostname: str):
            nonlocal sni
            assert wrapped is raw_socket
            sni = server_hostname
            return wrapped_socket

    monkeypatch.setattr(
        nas_email_alert_delivery.socket,
        "socket",
        lambda *_args: raw_socket,
    )
    client = object.__new__(nas_email_alert_delivery._PinnedSMTPSSL)
    client._pinned_family = socket.AF_INET
    client._pinned_address = pinned
    client.context = FakeContext()
    client._host = "smtp.example.com"

    result = client._get_socket("smtp.example.com", 465, 7.0)

    assert result is wrapped_socket
    assert raw_socket.timeout == 7.0
    assert raw_socket.connected == pinned
    assert sni == "smtp.example.com"


def test_starttls_sender_upgrades_before_authentication(monkeypatch) -> None:
    events: list[object] = []
    context = object()

    class FakeSMTP:
        def __init__(self, family, address) -> None:
            events.append(("init", family, address))

        def connect(self, host, port) -> None:
            events.append(("connect", host, port))

        def ehlo(self) -> None:
            events.append("ehlo")

        def has_extn(self, name) -> bool:
            events.append(("has_extn", name))
            return True

        def starttls(self, *, context) -> None:
            events.append(("starttls", context))

        def login(self, username, password) -> None:
            events.append(("login", username, password))

        def send_message(self, _message, *, from_addr, to_addrs):
            events.append(("send", from_addr, to_addrs))
            return {}

        def close(self) -> None:
            events.append("close")

    monkeypatch.setattr(
        nas_email_alert_delivery,
        "_public_smtp_addresses",
        lambda *_args: ((socket.AF_INET, ("93.184.216.34", 587)),),
    )
    monkeypatch.setattr(nas_email_alert_delivery, "_PinnedSMTP", FakeSMTP)
    monkeypatch.setattr(
        nas_email_alert_delivery.ssl,
        "create_default_context",
        lambda: context,
    )
    config = _request()
    config["smtpPort"] = 587

    nas_email_alert_delivery._default_smtp_sender(
        config,
        {
            "title": "Echo OS NAS 测试邮件",
            "severity": "warning",
            "sentAt": "2026-09-09T08:00:00Z",
            "alerts": [],
        },
    )

    assert events.index(("starttls", context)) < events.index(
        ("login", "echo-alerts@example.com", "private-app-password")
    )
    assert events[-2] == ("send", "echo-alerts@example.com", ["owner@example.net"])
    assert events[-1] == "close"


def test_starttls_absence_fails_before_credentials_are_sent(monkeypatch) -> None:
    login_called = False

    class FakeSMTP:
        def __init__(self, _family, _address) -> None:
            pass

        def connect(self, _host, _port) -> None:
            pass

        def ehlo(self) -> None:
            pass

        def has_extn(self, _name) -> bool:
            return False

        def login(self, _username, _password) -> None:
            nonlocal login_called
            login_called = True

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        nas_email_alert_delivery,
        "_public_smtp_addresses",
        lambda *_args: ((socket.AF_INET, ("93.184.216.34", 587)),),
    )
    monkeypatch.setattr(nas_email_alert_delivery, "_PinnedSMTP", FakeSMTP)
    config = _request()
    config["smtpPort"] = 587

    with pytest.raises(NasAlertDeliveryError, match="does not require STARTTLS"):
        nas_email_alert_delivery._default_smtp_sender(
            config,
            {
                "title": "Echo OS NAS 测试邮件",
                "severity": "warning",
                "sentAt": "2026-09-09T08:00:00Z",
                "alerts": [],
            },
        )
    assert login_called is False


def test_email_delivery_deduplicates_and_retries_without_losing_alert(tmp_path) -> None:
    clock = _Clock()
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
    attempts = 0

    def sender(_config, payload) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise NasAlertDeliveryError(503, "simulated")
        sent.append(dict(payload))

    service = NasEmailAlertDeliveryService(
        tmp_path,
        encryption_secret=JWT_SECRET,
        storage_reader=lambda: _health(active),
        ups_reader=_ups,
        sender=sender,
        clock=clock.now,
        monotonic=lambda: clock.monotonic,
    )
    service.apply(service.plan(_desired())["planId"])

    assert service.poll()["consecutiveFailures"] == 1
    service.poll()
    assert attempts == 1
    clock.tick(30)
    assert service.poll()["consecutiveFailures"] == 0
    service.poll()
    assert attempts == 2
    assert sent[0]["severity"] == "critical"

    restarted = NasEmailAlertDeliveryService(
        tmp_path,
        encryption_secret=JWT_SECRET,
        storage_reader=lambda: _health(active),
        ups_reader=_ups,
        sender=sender,
        clock=clock.now,
        monotonic=lambda: clock.monotonic,
    )
    restarted.poll()
    assert attempts == 2


def _client(tmp_path, sender, *, actor: str = "local:admin") -> TestClient:
    audit = ApplianceAudit.from_data_dir(tmp_path / "audit", jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=PASSWORD_HASH,
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"e" * 32,
    )
    service = NasEmailAlertDeliveryService(
        tmp_path / "state",
        encryption_secret=JWT_SECRET,
        storage_reader=_health,
        ups_reader=_ups,
        sender=sender,
    )
    app = FastAPI()
    app.include_router(create_approval_router(approval, jwt_secret=JWT_SECRET))
    app.include_router(
        create_nas_email_alert_delivery_router(
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


def test_email_router_requires_operator_approval_and_audits_without_secrets(tmp_path) -> None:
    sent: list[dict] = []
    client = _client(tmp_path, lambda _config, payload: sent.append(dict(payload)))
    request = _request()
    plan_response = client.post("/api/appliance/notifications/email/plan", json=request)
    assert plan_response.status_code == 200, plan_response.text
    plan_id = plan_response.json()["planId"]
    assert (
        client.post(
            "/api/appliance/notifications/email/apply",
            json={"planId": plan_id},
        ).status_code
        == 403
    )

    configured = client.post(
        "/api/appliance/notifications/email/apply",
        json={"planId": plan_id},
        headers={APPROVAL_HEADER: _approval(client, EMAIL_ALERT_CONFIGURE_ACTION, plan_id)},
    )
    assert configured.status_code == 200, configured.text
    revision = configured.json()["revision"]
    tested = client.post(
        "/api/appliance/notifications/email/test",
        headers={APPROVAL_HEADER: _approval(client, EMAIL_ALERT_TEST_ACTION, revision)},
    )
    assert tested.status_code == 200, tested.text
    assert sent[0]["test"] is True

    audit_text = (tmp_path / "audit" / "appliance-audit.jsonl").read_text(encoding="utf-8")
    assert EMAIL_ALERT_CONFIGURE_ACTION in audit_text
    assert EMAIL_ALERT_TEST_ACTION in audit_text
    assert "private-app-password" not in audit_text
    assert "owner@example.net" not in audit_text


def test_family_member_cannot_inspect_or_configure_email_alerts(tmp_path) -> None:
    client = _client(tmp_path, lambda *_args: None, actor="local:alice")
    assert client.get("/api/appliance/notifications/email").status_code == 403
    assert (
        client.post(
            "/api/appliance/notifications/email/plan",
            json={"enabled": False},
        ).status_code
        == 403
    )
