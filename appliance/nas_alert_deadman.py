"""Out-of-process alert fallback for a stopped NAS control plane.

The normal webhook and email workers live inside ``echo-appliance.service``.
This one-shot helper is invoked by systemd only after that service fails, and
periodically by a timer.  It acquires the appliance's exclusive state lock
before reading the existing encrypted channel configuration, so it cannot race
the live API process or offline backup/restore tooling.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from appliance.auth import read_auth_store
from appliance.nas_alert_delivery import NasAlertDeliveryService, collect_nas_alerts
from appliance.nas_email_alert_delivery import NasEmailAlertDeliveryService
from appliance.native_service_health import MONITORED_UNITS, service_health
from appliance.state_lock import StateDirectoryLock, StateLockError

DEADMAN_SCHEMA = "echo.nas-alert-deadman.v1"
APPLIANCE_UNIT = "echo-appliance.service"
_ALLOWED_CODES = frozenset({"service.failed", "service.inactive", "service.restart_storm"})


class _DeliveryService(Protocol):
    def status(self) -> dict[str, Any]: ...

    def poll(self) -> dict[str, Any]: ...


def _exclusive_state_lock(state_dir: Path) -> StateDirectoryLock:
    return StateDirectoryLock.acquire(
        state_dir,
        exclusive=True,
        create=False,
        purpose="appliance deadman notifier",
    )


def _jwt_secret(state_dir: Path) -> str:
    payload = read_auth_store(state_dir / "appliance-auth.json")
    secret = payload.get("jwt_secret")
    if (
        not isinstance(secret, str)
        or not 32 <= len(secret.encode("utf-8")) <= 4096
        or any(ord(character) < 0x20 for character in secret)
    ):
        raise ValueError("appliance auth store lacks a valid signing secret")
    return secret


def _deadman_alert(snapshot: Mapping[str, Any]) -> dict[str, str] | None:
    alerts = snapshot.get("activeAlerts")
    if not isinstance(alerts, list) or len(alerts) > 16:
        raise ValueError("system service health snapshot is invalid")
    selected: list[dict[str, str]] = []
    for value in alerts:
        if not isinstance(value, Mapping):
            raise ValueError("system service health alert is invalid")
        code = value.get("code")
        severity = value.get("severity")
        resource = value.get("resource")
        message = value.get("message")
        identifier = value.get("id")
        if (
            resource not in MONITORED_UNITS
            or code not in _ALLOWED_CODES
            or severity not in {"warning", "critical"}
            or not isinstance(message, str)
            or not 1 <= len(message) <= 512
            or any(ord(character) < 0x20 for character in message)
            or identifier != f"{resource}:{code}"
        ):
            raise ValueError("system service health alert is invalid")
        if resource != APPLIANCE_UNIT:
            continue
        selected.append(
            {
                "id": identifier,
                "source": "service",
                "code": code,
                "severity": severity,
                "resource": APPLIANCE_UNIT,
                "message": message,
            }
        )
    if len(selected) > 1:
        raise ValueError("appliance service health snapshot is ambiguous")
    if not selected:
        return None
    normalized = collect_nas_alerts(
        {"activeAlerts": []},
        {"configured": False, "devices": []},
        {"activeAlerts": selected},
    )
    if len(normalized) != 1:
        raise ValueError("appliance service health alert normalization failed")
    return normalized[0]


def _deliver(service: _DeliveryService) -> str:
    before = service.status()
    if before.get("persistenceHealthy") is not True:
        return "stateInvalid"
    if before.get("enabled") is not True:
        return "disabled"
    try:
        after = service.poll()
    except Exception:  # The systemd result is intentionally detail-free.
        return "retry"
    if after.get("lastError") is not None:
        return "retry"
    if after.get("deliveredActiveAlerts") != 1:
        return "retry"
    return "delivered"


def run_deadman(
    state_dir: Path | str,
    *,
    health_reader: Callable[[], Mapping[str, Any]] = service_health,
    secret_reader: Callable[[Path], str] = _jwt_secret,
    lock_factory: Callable[[Path], Any] = _exclusive_state_lock,
    webhook_factory: Callable[..., _DeliveryService] = NasAlertDeliveryService,
    email_factory: Callable[..., _DeliveryService] = NasEmailAlertDeliveryService,
) -> dict[str, Any]:
    root = Path(state_dir)
    try:
        lock = lock_factory(root)
    except (OSError, StateLockError):
        return {
            "schema": DEADMAN_SCHEMA,
            "state": "stateInUse",
            "channels": {},
            "retryRequired": False,
        }

    try:
        with lock:
            alert = _deadman_alert(health_reader())
            if alert is None:
                return {
                    "schema": DEADMAN_SCHEMA,
                    "state": "healthy",
                    "channels": {},
                    "retryRequired": False,
                }
            secret = secret_reader(root)

            def alert_reader() -> list[dict[str, str]]:
                return [dict(alert)]

            channels = {
                "webhook": _deliver(
                    webhook_factory(
                        root,
                        encryption_secret=secret,
                        alert_reader=alert_reader,
                    )
                ),
                "email": _deliver(
                    email_factory(
                        root,
                        encryption_secret=secret,
                        alert_reader=alert_reader,
                    )
                ),
            }
    except (OSError, StateLockError, ValueError):
        return {
            "schema": DEADMAN_SCHEMA,
            "state": "unavailable",
            "channels": {},
            "retryRequired": True,
        }

    retry_required = any(value in {"retry", "stateInvalid"} for value in channels.values())
    delivered = any(value == "delivered" for value in channels.values())
    return {
        "schema": DEADMAN_SCHEMA,
        "state": "retry" if retry_required else "delivered" if delivered else "unconfigured",
        "channels": channels,
        "retryRequired": retry_required,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deliver a bounded Echo NAS deadman alert")
    parser.add_argument(
        "--state-dir",
        default=os.environ.get("ECHO_DATA_DIR", "/data"),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    result = run_deadman(args.state_dir)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 1 if result["retryRequired"] else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["APPLIANCE_UNIT", "DEADMAN_SCHEMA", "main", "run_deadman"]
