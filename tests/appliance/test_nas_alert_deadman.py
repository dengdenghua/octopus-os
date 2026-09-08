from __future__ import annotations

import contextlib

from appliance.nas_alert_deadman import DEADMAN_SCHEMA, run_deadman
from appliance.nas_alert_delivery import (
    NasAlertDeliveryService,
    WebhookDesiredState,
)
from appliance.nas_email_alert_delivery import (
    EmailDesiredState,
    NasEmailAlertDeliveryService,
)
from appliance.state_lock import StateLockError

JWT_SECRET = "Nas-Deadman-Test-Secret_123456789012345678901"


def _failed_health() -> dict:
    return {
        "available": True,
        "activeAlerts": [
            {
                "id": "echo-appliance.service:service.restart_storm",
                "code": "service.restart_storm",
                "severity": "critical",
                "resource": "echo-appliance.service",
                "message": "系统服务 echo-appliance.service 因短时间反复重启已触发保护",
            }
        ],
    }


def _lock(_root):
    return contextlib.nullcontext()


def test_live_runtime_or_maintenance_lock_suppresses_deadman_delivery(tmp_path) -> None:
    health_reads = 0

    def busy(_root):
        raise StateLockError("state is in use")

    def health():
        nonlocal health_reads
        health_reads += 1
        return _failed_health()

    result = run_deadman(tmp_path, lock_factory=busy, health_reader=health)

    assert result == {
        "schema": DEADMAN_SCHEMA,
        "state": "stateInUse",
        "channels": {},
        "retryRequired": False,
    }
    assert health_reads == 0


def test_healthy_or_disabled_appliance_does_not_open_auth_state(tmp_path) -> None:
    def secret(_root):
        raise AssertionError("healthy probe must not read the auth store")

    result = run_deadman(
        tmp_path,
        lock_factory=_lock,
        health_reader=lambda: {"available": True, "activeAlerts": []},
        secret_reader=secret,
    )

    assert result["state"] == "healthy"
    assert result["retryRequired"] is False


def test_deadman_reuses_channel_cursors_without_duplicate_recovery_alerts(tmp_path) -> None:
    webhook_sent: list[dict] = []
    email_sent: list[dict] = []
    webhook = NasAlertDeliveryService(
        tmp_path,
        encryption_secret=JWT_SECRET,
        alert_reader=lambda: [],
        sender=lambda *_args: None,
    )
    webhook.apply(
        webhook.plan(
            WebhookDesiredState.model_validate(
                {"enabled": True, "url": "https://hooks.example.com/echo"}
            )
        )["planId"]
    )
    email = NasEmailAlertDeliveryService(
        tmp_path,
        encryption_secret=JWT_SECRET,
        alert_reader=lambda: [],
        sender=lambda *_args: None,
    )
    email.apply(
        email.plan(
            EmailDesiredState.model_validate(
                {
                    "enabled": True,
                    "smtpHost": "smtp.example.com",
                    "smtpPort": 465,
                    "username": "echo-alerts@example.com",
                    "password": "private-app-password",
                    "fromAddress": "echo-alerts@example.com",
                    "recipient": "owner@example.net",
                }
            )
        )["planId"]
    )

    def webhook_factory(root, *, encryption_secret, alert_reader):
        return NasAlertDeliveryService(
            root,
            encryption_secret=encryption_secret,
            alert_reader=alert_reader,
            sender=lambda _url, _host, _headers, payload: webhook_sent.append(dict(payload)),
        )

    def email_factory(root, *, encryption_secret, alert_reader):
        return NasEmailAlertDeliveryService(
            root,
            encryption_secret=encryption_secret,
            alert_reader=alert_reader,
            sender=lambda _config, payload: email_sent.append(dict(payload)),
        )

    arguments = {
        "lock_factory": _lock,
        "health_reader": _failed_health,
        "secret_reader": lambda _root: JWT_SECRET,
        "webhook_factory": webhook_factory,
        "email_factory": email_factory,
    }
    first = run_deadman(tmp_path, **arguments)
    second = run_deadman(tmp_path, **arguments)

    assert first["state"] == "delivered"
    assert first["channels"] == {"webhook": "delivered", "email": "delivered"}
    assert second["state"] == "delivered"
    assert len(webhook_sent) == 1
    assert len(email_sent) == 1
    assert webhook_sent[0]["alerts"] == email_sent[0]["alerts"]
    assert webhook_sent[0]["alerts"][0]["source"] == "service"


def test_malformed_or_spoofed_health_data_fails_closed_without_secrets(tmp_path) -> None:
    result = run_deadman(
        tmp_path,
        lock_factory=_lock,
        health_reader=lambda: {
            "activeAlerts": [
                {
                    "id": "attacker.service:service.failed",
                    "code": "service.failed",
                    "severity": "critical",
                    "resource": "attacker.service",
                    "message": "private attacker detail",
                }
            ]
        },
        secret_reader=lambda _root: JWT_SECRET,
    )

    assert result["state"] == "unavailable"
    assert result["retryRequired"] is True
    assert "attacker" not in str(result)


class _RetryService:
    def status(self):
        return {"persistenceHealthy": True, "enabled": True}

    def poll(self):
        return {"lastError": "delivery_failed", "deliveredActiveAlerts": 0}


class _DisabledService:
    def status(self):
        return {"persistenceHealthy": True, "enabled": False}

    def poll(self):
        raise AssertionError("disabled channel must not be polled")


def test_failed_channel_requests_a_later_timer_retry(tmp_path) -> None:
    result = run_deadman(
        tmp_path,
        lock_factory=_lock,
        health_reader=_failed_health,
        secret_reader=lambda _root: JWT_SECRET,
        webhook_factory=lambda *_args, **_kwargs: _RetryService(),
        email_factory=lambda *_args, **_kwargs: _DisabledService(),
    )

    assert result["state"] == "retry"
    assert result["retryRequired"] is True
    assert result["channels"] == {"webhook": "retry", "email": "disabled"}
