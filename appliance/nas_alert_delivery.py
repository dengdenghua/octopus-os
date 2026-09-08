"""Headless delivery for native NAS health alerts.

The browser notification surface is useful while an operator is signed in, but
it cannot protect an unattended NAS.  This module owns a small encrypted
webhook configuration and a bounded delivery cursor so storage and UPS alerts
continue to leave the appliance when no browser is open.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import ssl
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from appliance import native_storage
from appliance.approval import HighRiskApprovalService, consume_request_approval
from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.native_service_health import service_health
from appliance.native_ups import ups_status
from appliance.security import ApplianceAuthenticator, resolve_authenticator

ALERT_DELIVERY_FILENAME = "nas-alert-delivery.json"
ALERT_DELIVERY_SCHEMA = "echo.nas-alert-delivery.v1"
ALERT_DELIVERY_ENVELOPE_SCHEMA = "echo.nas-alert-delivery.encrypted.v1"
ALERT_DELIVERY_CONFIGURE_ACTION = "notifications.webhook.configure"
ALERT_DELIVERY_TEST_ACTION = "notifications.webhook.test"
MAX_ALERT_DELIVERY_BYTES = 256 * 1024
MAX_ALERTS = 128
MAX_TRACKED_ALERTS = 256
MAX_PENDING_PLANS = 32
PLAN_TTL_SECONDS = 5 * 60
DEFAULT_INTERVAL_SECONDS = 300
MAX_DESTINATION_ADDRESSES = 8
MAX_RESPONSE_HEADER_BYTES = 64 * 1024
MAX_WEBHOOK_PAYLOAD_BYTES = 128 * 1024
_AAD = b"echo-os/nas-alert-delivery/v1"
_log = logging.getLogger("echo.appliance.nas_alert_delivery")


class NasAlertDeliveryError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class WebhookDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    url: SecretStr | None = Field(default=None, max_length=2048)
    bearer_token: SecretStr | None = Field(
        default=None,
        alias="bearerToken",
        max_length=4096,
    )

    @model_validator(mode="after")
    def validate_enabled_destination(self) -> WebhookDesiredState:
        if self.enabled and (self.url is None or not self.url.get_secret_value().strip()):
            raise ValueError("enabled webhook delivery requires a URL")
        return self


class WebhookApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(alias="planId", pattern=r"^[0-9a-f]{64}$")


def _timestamp(value: datetime | None = None) -> str:
    return (
        (value or datetime.now(UTC))
        .astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _valid_optional_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 32:
        raise ValueError("invalid alert delivery timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("alert delivery timestamp lacks a timezone")
    return _timestamp(parsed)


def _safe_identifier(value: Any, *, maximum: int = 128) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(character < " " for character in value)
    ):
        raise ValueError("invalid alert identifier")
    return value


def _validate_url(value: str) -> tuple[str, str]:
    url = value.strip()
    if not url or len(url) > 2048:
        raise NasAlertDeliveryError(422, "webhook URL is invalid")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise NasAlertDeliveryError(422, "webhook URL is invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise NasAlertDeliveryError(
            422,
            "webhook URL must use public HTTPS on port 443 without credentials or a fragment",
        )
    try:
        host = parsed.hostname.encode("idna").decode("ascii").rstrip(".").lower()
    except UnicodeError as exc:
        raise NasAlertDeliveryError(422, "webhook hostname is invalid") from exc
    if not host or len(host) > 253 or host == "localhost" or host.endswith(".localhost"):
        raise NasAlertDeliveryError(422, "webhook hostname is invalid")
    return url, host


def _public_destination_addresses(host: str) -> tuple[tuple[int, tuple[Any, ...]], ...]:
    try:
        resolved = socket.getaddrinfo(
            host,
            443,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError as exc:
        raise NasAlertDeliveryError(503, "webhook destination could not be resolved") from exc
    addresses: list[tuple[int, tuple[Any, ...]]] = []
    seen: set[tuple[int, tuple[Any, ...]]] = set()
    for family, socktype, protocol, _canonical, sockaddr in resolved:
        if (
            family not in {socket.AF_INET, socket.AF_INET6}
            or socktype != socket.SOCK_STREAM
            or protocol not in {0, socket.IPPROTO_TCP}
            or not isinstance(sockaddr, tuple)
            or not sockaddr
            or not isinstance(sockaddr[0], str)
        ):
            continue
        try:
            safe = ipaddress.ip_address(sockaddr[0]).is_global
        except ValueError as exc:
            raise NasAlertDeliveryError(503, "webhook destination resolved unsafely") from exc
        if not safe:
            raise NasAlertDeliveryError(
                422, "webhook destination must resolve only to public addresses"
            )
        item = (family, sockaddr)
        if item not in seen:
            seen.add(item)
            addresses.append(item)
    if not addresses:
        raise NasAlertDeliveryError(503, "webhook destination could not be resolved")
    if len(addresses) > MAX_DESTINATION_ADDRESSES:
        raise NasAlertDeliveryError(503, "webhook destination resolved ambiguously")
    return tuple(addresses)


def _assert_public_destination(host: str) -> None:
    _public_destination_addresses(host)


def _valid_bearer_token(value: str) -> str:
    if (
        not 1 <= len(value) <= 4096
        or any(ord(character) <= 0x20 or ord(character) > 0x7E for character in value)
        or "\r" in value
        or "\n" in value
    ):
        raise NasAlertDeliveryError(422, "bearer token contains invalid characters")
    return value


def _request_header(name: str, value: str) -> bytes:
    if (
        not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
        or name.lower() in {"connection", "content-length", "host"}
        or "\r" in value
        or "\n" in value
    ):
        raise NasAlertDeliveryError(422, "webhook request headers are invalid")
    try:
        return name.encode("ascii") + b": " + value.encode("ascii") + b"\r\n"
    except UnicodeEncodeError as exc:
        raise NasAlertDeliveryError(422, "webhook request headers are invalid") from exc


def _response_status(stream: Any) -> int:
    total = 0
    status_line = stream.readline(4097)
    total += len(status_line)
    if len(status_line) > 4096 or not status_line.endswith(b"\r\n"):
        raise NasAlertDeliveryError(503, "webhook destination returned an invalid response")
    try:
        protocol, raw_status, _reason = status_line.decode("ascii").rstrip("\r\n").split(" ", 2)
        status = int(raw_status)
    except (UnicodeDecodeError, ValueError) as exc:
        raise NasAlertDeliveryError(
            503, "webhook destination returned an invalid response"
        ) from exc
    if protocol not in {"HTTP/1.0", "HTTP/1.1"} or not 100 <= status <= 599:
        raise NasAlertDeliveryError(503, "webhook destination returned an invalid response")
    while True:
        line = stream.readline(8193)
        total += len(line)
        if (
            not line
            or len(line) > 8192
            or total > MAX_RESPONSE_HEADER_BYTES
            or not line.endswith(b"\r\n")
        ):
            raise NasAlertDeliveryError(503, "webhook destination returned an invalid response")
        if line == b"\r\n":
            return status


def _default_sender(
    url: str,
    host: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
) -> None:
    addresses = _public_destination_addresses(host)
    body = json.dumps(
        dict(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    if not body or len(body) > MAX_WEBHOOK_PAYLOAD_BYTES:
        raise NasAlertDeliveryError(503, "webhook alert payload exceeds its safety limit")
    try:
        target = httpx.URL(url).raw_path or b"/"
    except (httpx.InvalidURL, UnicodeError, ValueError) as exc:
        raise NasAlertDeliveryError(422, "webhook URL is invalid") from exc
    try:
        host_ip = ipaddress.ip_address(host)
        host_header = f"[{host}]" if host_ip.version == 6 else host
    except ValueError:
        host_header = host
    request = b"POST " + target + b" HTTP/1.1\r\n"
    request += b"Host: " + host_header.encode("ascii") + b"\r\n"
    request += b"Connection: close\r\n"
    request += f"Content-Length: {len(body)}\r\n".encode("ascii")
    for name, value in headers.items():
        request += _request_header(str(name), str(value))
    request += b"\r\n" + body

    context = ssl.create_default_context()
    connection_failed = False
    for family, sockaddr in addresses:
        raw_socket: socket.socket | None = None
        try:
            raw_socket = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
            raw_socket.settimeout(5.0)
            raw_socket.connect(sockaddr)
            with context.wrap_socket(raw_socket, server_hostname=host) as tls_socket:
                raw_socket = None
                tls_socket.settimeout(8.0)
                tls_socket.sendall(request)
                with tls_socket.makefile("rb") as stream:
                    status_code = _response_status(stream)
            if not 200 <= status_code < 300:
                raise NasAlertDeliveryError(503, "webhook destination rejected the alert")
            return
        except NasAlertDeliveryError:
            raise
        except (OSError, ssl.SSLError):
            connection_failed = True
        finally:
            if raw_socket is not None:
                raw_socket.close()
    if connection_failed:
        raise NasAlertDeliveryError(503, "webhook delivery failed")
    raise NasAlertDeliveryError(503, "webhook destination could not be reached")


def _empty_state() -> dict[str, Any]:
    return {
        "schema": ALERT_DELIVERY_SCHEMA,
        "enabled": False,
        "url": None,
        "bearerToken": None,
        "deliveredIds": [],
        "consecutiveFailures": 0,
        "nextRetryAt": None,
        "lastAttemptAt": None,
        "lastSuccessAt": None,
        "lastError": None,
    }


def _validate_state(value: Any) -> dict[str, Any]:
    expected = {
        "schema",
        "enabled",
        "url",
        "bearerToken",
        "deliveredIds",
        "consecutiveFailures",
        "nextRetryAt",
        "lastAttemptAt",
        "lastSuccessAt",
        "lastError",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("invalid alert delivery state fields")
    if value.get("schema") != ALERT_DELIVERY_SCHEMA or not isinstance(value.get("enabled"), bool):
        raise ValueError("unsupported alert delivery state")
    url_value = value.get("url")
    host: str | None = None
    if url_value is not None:
        if not isinstance(url_value, str):
            raise ValueError("invalid alert delivery URL")
        url_value, host = _validate_url(url_value)
    if value["enabled"] and host is None:
        raise ValueError("enabled alert delivery lacks a URL")
    token = value.get("bearerToken")
    if token is not None:
        if not isinstance(token, str):
            raise ValueError("invalid alert delivery bearer token")
        try:
            token = _valid_bearer_token(token)
        except NasAlertDeliveryError as exc:
            raise ValueError("invalid alert delivery bearer token") from exc
    delivered = value.get("deliveredIds")
    if not isinstance(delivered, list) or len(delivered) > MAX_TRACKED_ALERTS:
        raise ValueError("invalid alert delivery cursor")
    identifiers = [_safe_identifier(item) for item in delivered]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("duplicate alert delivery cursor")
    failures = value.get("consecutiveFailures")
    if isinstance(failures, bool) or not isinstance(failures, int) or not 0 <= failures <= 31:
        raise ValueError("invalid alert delivery failure count")
    error = value.get("lastError")
    if error is not None and error not in {"delivery_failed", "health_probe_failed"}:
        raise ValueError("invalid alert delivery error")
    return {
        "schema": ALERT_DELIVERY_SCHEMA,
        "enabled": value["enabled"],
        "url": url_value,
        "bearerToken": token,
        "deliveredIds": identifiers,
        "consecutiveFailures": failures,
        "nextRetryAt": _valid_optional_timestamp(value.get("nextRetryAt")),
        "lastAttemptAt": _valid_optional_timestamp(value.get("lastAttemptAt")),
        "lastSuccessAt": _valid_optional_timestamp(value.get("lastSuccessAt")),
        "lastError": error,
    }


def _read_envelope(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_size < 1
        or info.st_size > MAX_ALERT_DELIVERY_BYTES
        or (os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077)
    ):
        raise ValueError("alert delivery state is unsafe")
    if os.name == "nt":
        from appliance.windows_state import open_private_file, private_state_directory

        with (
            private_state_directory(path.parent, protect=True),
            os.fdopen(open_private_file(path), "r", encoding="utf-8") as handle,
        ):
            raw = json.load(handle)
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"schema", "ciphertext"}:
        raise ValueError("invalid alert delivery envelope")
    if raw.get("schema") != ALERT_DELIVERY_ENVELOPE_SCHEMA:
        raise ValueError("unsupported alert delivery envelope")
    return raw


def validate_alert_delivery_state(path: Path | str, *, encryption_secret: str) -> dict[str, Any]:
    """Decrypt and strictly validate one restored alert delivery file."""

    raw = _read_envelope(Path(path))
    try:
        encoded = base64.b64decode(raw["ciphertext"], validate=True)
        if len(encoded) < 13 or len(encoded) > MAX_ALERT_DELIVERY_BYTES:
            raise ValueError("invalid alert delivery ciphertext")
        key = hashlib.sha256(
            b"echo-os/nas-alert-delivery/v1\0" + encryption_secret.encode("utf-8")
        ).digest()
        plaintext = AESGCM(key).decrypt(encoded[:12], encoded[12:], _AAD)
        state = json.loads(plaintext)
    except (InvalidTag, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("alert delivery state authentication failed") from exc
    return _validate_state(state)


def _alert(source: str, value: Mapping[str, Any]) -> dict[str, str] | None:
    severity = value.get("severity")
    if severity not in {"warning", "critical"}:
        return None
    raw_id = _safe_identifier(value.get("id"), maximum=256)
    message = value.get("message")
    resource = value.get("resource", "unknown")
    code = value.get("code", "unknown")
    if not isinstance(message, str) or not message or len(message) > 512:
        raise ValueError("invalid NAS alert message")
    if not isinstance(resource, str) or not resource or len(resource) > 256:
        raise ValueError("invalid NAS alert resource")
    if not isinstance(code, str) or not code or len(code) > 64:
        raise ValueError("invalid NAS alert code")
    return {
        "id": hashlib.sha256(f"{source}\0{raw_id}".encode()).hexdigest()[:32],
        "source": source,
        "code": code,
        "severity": severity,
        "resource": resource,
        "message": message,
    }


def _ups_alerts(snapshot: Mapping[str, Any]) -> list[dict[str, str]]:
    if snapshot.get("configured") is not True:
        return []
    results: list[dict[str, str]] = []
    labels = {
        "onBattery": ("warning", "UPS 已切换到电池供电"),
        "lowBattery": ("critical", "UPS 电量低"),
        "replaceBattery": ("critical", "UPS 报告需要更换电池"),
        "shutdownPending": ("critical", "UPS 已发出强制关机信号"),
        "offline": ("warning", "UPS 离线"),
        "unknown": ("warning", "UPS 状态未知"),
    }
    devices = snapshot.get("devices")
    if not isinstance(devices, list) or len(devices) > 16:
        raise ValueError("invalid UPS snapshot")
    for device in devices:
        if not isinstance(device, dict):
            raise ValueError("invalid UPS device")
        state = device.get("state")
        if state not in labels:
            continue
        name = _safe_identifier(device.get("name"), maximum=64)
        severity, message = labels[state]
        results.append(
            {
                "id": hashlib.sha256(f"ups\0{name}\0{state}".encode()).hexdigest()[:32],
                "source": "ups",
                "code": f"ups.{state}",
                "severity": severity,
                "resource": name,
                "message": f"UPS {name}：{message}",
            }
        )
    return results


def _service_alerts(snapshot: Mapping[str, Any]) -> list[dict[str, str]]:
    raw_alerts = snapshot.get("activeAlerts", [])
    if not isinstance(raw_alerts, list) or len(raw_alerts) > MAX_ALERTS:
        raise ValueError("invalid system service health snapshot")
    return [item for value in raw_alerts if (item := _alert("service", value))]


def collect_nas_alerts(
    storage_snapshot: Mapping[str, Any],
    ups_snapshot: Mapping[str, Any],
    service_snapshot: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Return one bounded, stable warning/critical alert snapshot."""

    raw_alerts = storage_snapshot.get("activeAlerts")
    if not isinstance(raw_alerts, list) or len(raw_alerts) > MAX_ALERTS:
        raise ValueError("invalid storage health snapshot")
    alerts = [item for value in raw_alerts if (item := _alert("storage", value))]
    alerts.extend(_ups_alerts(ups_snapshot))
    if service_snapshot is not None:
        alerts.extend(_service_alerts(service_snapshot))
    if len(alerts) > MAX_ALERTS or len({item["id"] for item in alerts}) != len(alerts):
        raise ValueError("invalid or duplicate NAS alerts")
    return sorted(alerts, key=lambda item: (item["severity"] != "critical", item["id"]))


class NasAlertSnapshotSource:
    """Share one short-lived hardware probe across independent delivery channels."""

    def __init__(
        self,
        *,
        storage_reader: Callable[[], Mapping[str, Any]] = native_storage.storage_health,
        ups_reader: Callable[[], Mapping[str, Any]] = ups_status,
        service_reader: Callable[[], Mapping[str, Any]] = service_health,
        cache_seconds: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if cache_seconds < 0:
            raise ValueError("NAS alert snapshot cache must not be negative")
        self._storage_reader = storage_reader
        self._ups_reader = ups_reader
        self._service_reader = service_reader
        self._cache_seconds = cache_seconds
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._cached_at: float | None = None
        self._cached: list[dict[str, str]] | None = None

    def read(self) -> list[dict[str, str]]:
        with self._lock:
            now = self._monotonic()
            if (
                self._cached is not None
                and self._cached_at is not None
                and self._cache_seconds > 0
                and now - self._cached_at <= self._cache_seconds
            ):
                return copy.deepcopy(self._cached)
            alerts = collect_nas_alerts(
                self._storage_reader(),
                self._ups_reader(),
                self._service_reader(),
            )
            self._cached = copy.deepcopy(alerts)
            self._cached_at = now
            return alerts


class NasAlertDeliveryService:
    def __init__(
        self,
        data_dir: Path | str,
        *,
        encryption_secret: str,
        storage_reader: Callable[[], Mapping[str, Any]] = native_storage.storage_health,
        ups_reader: Callable[[], Mapping[str, Any]] = ups_status,
        service_reader: Callable[[], Mapping[str, Any]] = service_health,
        alert_reader: Callable[[], list[dict[str, str]]] | None = None,
        sender: Callable[[str, str, Mapping[str, str], Mapping[str, Any]], None] = _default_sender,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not encryption_secret:
            raise ValueError("alert delivery encryption secret is required")
        if interval_seconds <= 0:
            raise ValueError("alert delivery interval must be positive")
        self.path = Path(data_dir) / ALERT_DELIVERY_FILENAME
        self._key = hashlib.sha256(
            b"echo-os/nas-alert-delivery/v1\0" + encryption_secret.encode("utf-8")
        ).digest()
        self._alert_reader = (
            alert_reader
            or NasAlertSnapshotSource(
                storage_reader=storage_reader,
                ups_reader=ups_reader,
                service_reader=service_reader,
                cache_seconds=0,
                monotonic=monotonic,
            ).read
        )
        self._sender = sender
        self.interval_seconds = interval_seconds
        self._clock = clock
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._poll_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._plans: dict[str, tuple[float, dict[str, Any]]] = {}
        self._state = _empty_state()
        self._persistence_healthy = True
        if self.path.exists() or self.path.is_symlink():
            try:
                self._state = validate_alert_delivery_state(
                    self.path, encryption_secret=encryption_secret
                )
            except (OSError, ValueError, NasAlertDeliveryError):
                self._persistence_healthy = False

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def _revision(self, state: Mapping[str, Any] | None = None) -> str:
        selected = state or self._state
        canonical = json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(self._key, b"revision\0" + canonical, hashlib.sha256).hexdigest()

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = copy.deepcopy(self._state)
            persistence_healthy = self._persistence_healthy
        host = None
        if state["url"]:
            _url, host = _validate_url(state["url"])
        return {
            "schema": ALERT_DELIVERY_SCHEMA,
            "configured": state["url"] is not None,
            "enabled": state["enabled"] and persistence_healthy,
            "destinationHost": host,
            "hasBearerToken": state["bearerToken"] is not None,
            "deliveredActiveAlerts": len(state["deliveredIds"]),
            "consecutiveFailures": state["consecutiveFailures"],
            "nextRetryAt": state["nextRetryAt"],
            "lastAttemptAt": state["lastAttemptAt"],
            "lastSuccessAt": state["lastSuccessAt"],
            "lastError": state["lastError"],
            "persistenceHealthy": persistence_healthy,
            "monitoring": self.running,
            "revision": self._revision(state),
            "secretsRedacted": True,
        }

    def plan(self, desired: WebhookDesiredState) -> dict[str, Any]:
        if desired.enabled:
            url, host = _validate_url(desired.url.get_secret_value())  # type: ignore[union-attr]
            token = (
                desired.bearer_token.get_secret_value()
                if desired.bearer_token is not None
                else None
            )
            if token is not None:
                token = _valid_bearer_token(token)
        else:
            url, host, token = None, None, None
        planned = _empty_state()
        planned.update({"enabled": desired.enabled, "url": url, "bearerToken": token})
        plan_id = secrets.token_hex(32)
        now = self._monotonic()
        with self._lock:
            self._plans = {
                key: item for key, item in self._plans.items() if now - item[0] <= PLAN_TTL_SECONDS
            }
            if len(self._plans) >= MAX_PENDING_PLANS:
                oldest = min(self._plans, key=lambda key: self._plans[key][0])
                self._plans.pop(oldest, None)
            self._plans[plan_id] = (now, planned)
            current_enabled = self._state["enabled"]
            current_host = urlsplit(self._state["url"]).hostname if self._state["url"] else None
        return {
            "schema": ALERT_DELIVERY_SCHEMA,
            "planId": plan_id,
            "changes": [
                {"field": "enabled", "before": current_enabled, "after": desired.enabled},
                {"field": "destinationHost", "before": current_host, "after": host},
                {"field": "bearerToken", "before": "redacted", "after": "redacted"},
            ],
            "requiresApproval": True,
            "secretsPersistedEncrypted": True,
            "redirectsAllowed": False,
            "publicHttpsOnly": True,
        }

    def apply(self, plan_id: str) -> dict[str, Any]:
        now = self._monotonic()
        with self._lock:
            planned = self._plans.pop(plan_id, None)
            if planned is None or now - planned[0] > PLAN_TTL_SECONDS:
                raise NasAlertDeliveryError(409, "webhook plan is missing or expired")
            self._commit_locked(copy.deepcopy(planned[1]))
        return self.status()

    def _commit_locked(self, state: dict[str, Any]) -> None:
        previous = self._state
        self._state = state
        try:
            self._persist_locked()
        except (OSError, NasAlertDeliveryError) as exc:
            self._state = previous
            self._persistence_healthy = False
            if isinstance(exc, NasAlertDeliveryError):
                raise
            raise NasAlertDeliveryError(503, "alert delivery state could not be saved") from exc
        self._persistence_healthy = True

    def _persist_locked(self) -> None:
        plaintext = json.dumps(
            self._state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        nonce = secrets.token_bytes(12)
        ciphertext = nonce + AESGCM(self._key).encrypt(nonce, plaintext, _AAD)
        envelope = json.dumps(
            {
                "schema": ALERT_DELIVERY_ENVELOPE_SCHEMA,
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if len(envelope) > MAX_ALERT_DELIVERY_BYTES:
            raise NasAlertDeliveryError(500, "alert delivery state exceeds its safety limit")
        if os.name == "nt":
            from appliance.windows_state import private_state_directory

            context = private_state_directory(self.path.parent, create=True, protect=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            context = contextlib.nullcontext(self.path.parent)
        with context as parent:
            path = Path(parent) / self.path.name
            if path.is_symlink():
                raise NasAlertDeliveryError(503, "alert delivery state path is unsafe")
            fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            temporary = Path(name)
            try:
                if hasattr(os, "fchmod"):
                    os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as output:
                    fd = -1
                    output.write(envelope + b"\n")
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, path)
                if os.name == "nt":
                    from appliance.windows_state import open_private_file

                    os.close(open_private_file(path))
                else:
                    path.chmod(0o600)
            finally:
                if fd >= 0:
                    os.close(fd)
                with contextlib.suppress(FileNotFoundError):
                    temporary.unlink()

    def _collect(self) -> list[dict[str, str]]:
        alerts = self._alert_reader()
        if (
            not isinstance(alerts, list)
            or len(alerts) > MAX_ALERTS
            or any(not isinstance(item, dict) for item in alerts)
            or len({item.get("id") for item in alerts}) != len(alerts)
        ):
            raise ValueError("invalid or duplicate NAS alerts")
        return copy.deepcopy(alerts)

    def _configuration_matches_locked(self, observed: Mapping[str, Any]) -> bool:
        return all(self._state[key] == observed[key] for key in ("enabled", "url", "bearerToken"))

    def _payload(self, alerts: list[dict[str, str]], *, test: bool = False) -> dict[str, Any]:
        critical = any(item["severity"] == "critical" for item in alerts)
        sent_at = _timestamp(self._clock())
        return {
            "schema": "echo.nas-alert.webhook.v1",
            "eventId": hashlib.sha256(
                (sent_at + "\0" + "\0".join(item["id"] for item in alerts)).encode()
            ).hexdigest()[:32],
            "sentAt": sent_at,
            "test": test,
            "severity": "critical" if critical else "warning",
            "title": "Echo OS NAS 测试通知" if test else "Echo OS NAS 健康告警",
            "alerts": alerts,
        }

    def _send(self, state: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
        url, host = _validate_url(str(state["url"]))
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Echo-OS-NAS-Alerts/1",
        }
        if state["bearerToken"]:
            headers["Authorization"] = f"Bearer {state['bearerToken']}"
        self._sender(url, host, headers, payload)

    def poll(self) -> dict[str, Any]:
        with self._poll_lock:
            with self._lock:
                state = copy.deepcopy(self._state)
                healthy = self._persistence_healthy
            if not healthy or not state["enabled"]:
                return self.status()
            try:
                alerts = self._collect()
            except (OSError, ValueError):
                with self._lock:
                    next_state = copy.deepcopy(self._state)
                    next_state["lastError"] = "health_probe_failed"
                    self._commit_locked(next_state)
                return self.status()
            active_ids = {item["id"] for item in alerts}
            delivered_ids = set(state["deliveredIds"]) & active_ids
            unseen = [item for item in alerts if item["id"] not in delivered_ids]
            now = self._clock()
            retry_at = state["nextRetryAt"]
            if retry_at and now < datetime.fromisoformat(retry_at.replace("Z", "+00:00")):
                return self.status()
            if not unseen:
                if sorted(delivered_ids) != state["deliveredIds"]:
                    with self._lock:
                        if not self._configuration_matches_locked(state):
                            return self.status()
                        next_state = copy.deepcopy(self._state)
                        next_state["deliveredIds"] = sorted(delivered_ids)
                        self._commit_locked(next_state)
                return self.status()
            attempted = _timestamp(now)
            try:
                self._send(state, self._payload(unseen))
            except (NasAlertDeliveryError, OSError, ValueError):
                failures = min(31, state["consecutiveFailures"] + 1)
                delay = min(3600, 30 * 2 ** min(failures - 1, 7))
                next_retry = _timestamp(datetime.fromtimestamp(now.timestamp() + delay, UTC))
                with self._lock:
                    if not self._configuration_matches_locked(state):
                        return self.status()
                    next_state = copy.deepcopy(self._state)
                    next_state.update(
                        {
                            "deliveredIds": sorted(delivered_ids),
                            "consecutiveFailures": failures,
                            "nextRetryAt": next_retry,
                            "lastAttemptAt": attempted,
                            "lastError": "delivery_failed",
                        }
                    )
                    self._commit_locked(next_state)
                return self.status()
            with self._lock:
                if not self._configuration_matches_locked(state):
                    return self.status()
                next_state = copy.deepcopy(self._state)
                next_state.update(
                    {
                        "deliveredIds": sorted(active_ids),
                        "consecutiveFailures": 0,
                        "nextRetryAt": None,
                        "lastAttemptAt": attempted,
                        "lastSuccessAt": attempted,
                        "lastError": None,
                    }
                )
                self._commit_locked(next_state)
            return self.status()

    def send_test(self) -> dict[str, Any]:
        with self._lock:
            state = copy.deepcopy(self._state)
        if not self._persistence_healthy or not state["enabled"] or not state["url"]:
            raise NasAlertDeliveryError(409, "webhook alert delivery is not enabled")
        self._send(
            state,
            self._payload(
                [
                    {
                        "id": "test",
                        "source": "system",
                        "code": "notification.test",
                        "severity": "warning",
                        "resource": "echo-os",
                        "message": "这是一条 Echo OS NAS 测试通知",
                    }
                ],
                test=True,
            ),
        )
        return {"sent": True, "sentAt": _timestamp(self._clock())}

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="echo-nas-alert-delivery", daemon=True
            )
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception as exc:  # pragma: no cover - last-resort thread survival
                # The daemon must survive a transient probe/persistence failure;
                # status remains available without logging secrets or payloads.
                _log.error("NAS alert delivery poll failed: %s", type(exc).__name__)
            self._stop.wait(self.interval_seconds)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)


def create_nas_alert_delivery_router(
    service: NasAlertDeliveryService,
    *,
    approval: HighRiskApprovalService,
    audit: ApplianceAudit,
    jwt_secret: str | None = None,
    authenticator: ApplianceAuthenticator | None = None,
) -> APIRouter:
    auth = resolve_authenticator(jwt_secret=jwt_secret, authenticator=authenticator)
    require_operator = auth.operator_dependency()
    router = APIRouter(prefix="/api/appliance/notifications", tags=["appliance", "notifications"])

    def record(*, actor: str, action: str, target: str, outcome: str) -> None:
        try:
            audit.record(
                actor=actor,
                action=action,
                target=target,
                outcome=outcome,
                metadata={"channel": "webhook", "secretsRedacted": True},
            )
        except (OSError, AuditIntegrityError) as exc:
            raise HTTPException(503, "appliance audit integrity check failed") from exc

    @router.get("/webhook")
    def status(_actor: str = Depends(require_operator)) -> dict[str, Any]:
        return service.status()

    @router.post("/webhook/plan")
    def plan(
        body: WebhookDesiredState,
        _actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        try:
            return service.plan(body)
        except NasAlertDeliveryError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc

    @router.post("/webhook/apply")
    def apply(
        body: WebhookApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        plan_id = body.plan_id
        consume_request_approval(
            request,
            approval,
            actor=actor,
            action=ALERT_DELIVERY_CONFIGURE_ACTION,
            target=plan_id,
        )
        record(
            actor=actor,
            action=ALERT_DELIVERY_CONFIGURE_ACTION,
            target=plan_id,
            outcome="attempted",
        )
        try:
            result = service.apply(plan_id)
        except NasAlertDeliveryError as exc:
            record(
                actor=actor,
                action=ALERT_DELIVERY_CONFIGURE_ACTION,
                target=plan_id,
                outcome="failed",
            )
            raise HTTPException(exc.status_code, exc.detail) from exc
        record(
            actor=actor,
            action=ALERT_DELIVERY_CONFIGURE_ACTION,
            target=plan_id,
            outcome="succeeded",
        )
        return result

    @router.post("/webhook/test")
    def send_test(
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        target = service.status()["revision"]
        consume_request_approval(
            request,
            approval,
            actor=actor,
            action=ALERT_DELIVERY_TEST_ACTION,
            target=target,
        )
        record(
            actor=actor,
            action=ALERT_DELIVERY_TEST_ACTION,
            target=target,
            outcome="attempted",
        )
        try:
            result = service.send_test()
        except NasAlertDeliveryError as exc:
            record(
                actor=actor,
                action=ALERT_DELIVERY_TEST_ACTION,
                target=target,
                outcome="failed",
            )
            raise HTTPException(exc.status_code, exc.detail) from exc
        record(
            actor=actor,
            action=ALERT_DELIVERY_TEST_ACTION,
            target=target,
            outcome="succeeded",
        )
        return result

    return router


__all__ = [
    "ALERT_DELIVERY_CONFIGURE_ACTION",
    "ALERT_DELIVERY_FILENAME",
    "ALERT_DELIVERY_TEST_ACTION",
    "MAX_ALERT_DELIVERY_BYTES",
    "NasAlertDeliveryError",
    "NasAlertDeliveryService",
    "NasAlertSnapshotSource",
    "WebhookApplyRequest",
    "WebhookDesiredState",
    "collect_nas_alerts",
    "create_nas_alert_delivery_router",
    "validate_alert_delivery_state",
]
