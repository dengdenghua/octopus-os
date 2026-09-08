"""Encrypted, headless SMTP delivery for native NAS health alerts.

Email is deliberately a separate delivery channel from the webhook service.
Each channel owns its own deduplication cursor and retry state, so a temporary
SMTP failure cannot cause a successful webhook to be sent twice.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import smtplib
import socket
import ssl
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from appliance import native_storage
from appliance.approval import HighRiskApprovalService, consume_request_approval
from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.nas_alert_delivery import (
    MAX_ALERTS,
    MAX_PENDING_PLANS,
    MAX_TRACKED_ALERTS,
    PLAN_TTL_SECONDS,
    NasAlertDeliveryError,
    NasAlertSnapshotSource,
)
from appliance.native_ups import ups_status
from appliance.security import ApplianceAuthenticator, resolve_authenticator

EMAIL_ALERT_DELIVERY_FILENAME = "nas-email-alert-delivery.json"
EMAIL_ALERT_DELIVERY_SCHEMA = "echo.nas-alert-email.v1"
EMAIL_ALERT_DELIVERY_ENVELOPE_SCHEMA = "echo.nas-alert-email.encrypted.v1"
EMAIL_ALERT_CONFIGURE_ACTION = "notifications.email.configure"
EMAIL_ALERT_TEST_ACTION = "notifications.email.test"
MAX_EMAIL_ALERT_DELIVERY_BYTES = 256 * 1024
MAX_SMTP_DESTINATION_ADDRESSES = 8
MAX_SMTP_MESSAGE_BYTES = 128 * 1024
DEFAULT_INTERVAL_SECONDS = 300
SMTP_TIMEOUT_SECONDS = 10.0
_AAD = b"echo-os/nas-email-alert-delivery/v1"
_EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?"
)
_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


class EmailDesiredState(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool
    smtp_host: SecretStr | None = Field(default=None, alias="smtpHost", max_length=253)
    smtp_port: int = Field(default=465, alias="smtpPort")
    username: SecretStr | None = Field(default=None, max_length=320)
    password: SecretStr | None = Field(default=None, max_length=4096)
    from_address: SecretStr | None = Field(default=None, alias="fromAddress", max_length=320)
    recipient: SecretStr | None = Field(default=None, max_length=320)

    @model_validator(mode="after")
    def validate_enabled_destination(self) -> EmailDesiredState:
        if self.smtp_port not in {465, 587}:
            raise ValueError("SMTP port must be 465 or 587")
        if self.enabled and any(
            value is None or not value.get_secret_value().strip()
            for value in (
                self.smtp_host,
                self.username,
                self.password,
                self.from_address,
                self.recipient,
            )
        ):
            raise ValueError("enabled email delivery requires complete SMTP settings")
        return self


class EmailApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(alias="planId", pattern=r"^[0-9a-f]{64}$")


def _timestamp(value: datetime | None = None) -> str:
    return (
        (value or datetime.now(UTC))
        .astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _optional_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 32:
        raise ValueError("invalid email alert timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("email alert timestamp lacks a timezone")
    return _timestamp(parsed)


def _identifier(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(character < " " for character in value)
    ):
        raise ValueError("invalid email alert identifier")
    return value


def _smtp_host(value: str) -> str:
    candidate = value.strip().rstrip(".")
    try:
        host = candidate.encode("idna").decode("ascii").lower()
        ipaddress.ip_address(host)
    except UnicodeError as exc:
        raise NasAlertDeliveryError(422, "SMTP hostname is invalid") from exc
    except ValueError:
        pass
    else:
        raise NasAlertDeliveryError(422, "SMTP hostname must be a DNS name")
    labels = host.split(".")
    if (
        not host
        or len(host) > 253
        or len(labels) < 2
        or host == "localhost"
        or host.endswith(".localhost")
        or any(_HOST_LABEL.fullmatch(label) is None for label in labels)
    ):
        raise NasAlertDeliveryError(422, "SMTP hostname is invalid")
    return host


def _smtp_credential(value: str, *, label: str, maximum: int) -> str:
    if (
        not 1 <= len(value) <= maximum
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
        or "\r" in value
        or "\n" in value
    ):
        raise NasAlertDeliveryError(422, f"SMTP {label} contains invalid characters")
    return value


def _email_address(value: str, *, label: str) -> str:
    candidate = value.strip()
    parsed_name, parsed_address = parseaddr(candidate)
    if (
        parsed_name
        or parsed_address != candidate
        or len(candidate) > 320
        or _EMAIL_PATTERN.fullmatch(candidate) is None
        or ".." in candidate
    ):
        raise NasAlertDeliveryError(422, f"SMTP {label} address is invalid")
    local, domain = candidate.rsplit("@", 1)
    try:
        normalized_domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise NasAlertDeliveryError(422, f"SMTP {label} address is invalid") from exc
    if len(normalized_domain) > 253 or any(
        _HOST_LABEL.fullmatch(part) is None for part in normalized_domain.split(".")
    ):
        raise NasAlertDeliveryError(422, f"SMTP {label} address is invalid")
    return f"{local}@{normalized_domain}"


def _recipient_hint(value: str | None) -> str | None:
    if value is None:
        return None
    local, domain = value.rsplit("@", 1)
    return f"{local[:1]}***@{domain}"


def _public_smtp_addresses(
    host: str,
    port: int,
) -> tuple[tuple[int, tuple[Any, ...]], ...]:
    try:
        resolved = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError as exc:
        raise NasAlertDeliveryError(503, "SMTP destination could not be resolved") from exc
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
            raise NasAlertDeliveryError(503, "SMTP destination resolved unsafely") from exc
        if not safe:
            raise NasAlertDeliveryError(
                422, "SMTP destination must resolve only to public addresses"
            )
        item = (family, sockaddr)
        if item not in seen:
            seen.add(item)
            addresses.append(item)
    if not addresses:
        raise NasAlertDeliveryError(503, "SMTP destination could not be resolved")
    if len(addresses) > MAX_SMTP_DESTINATION_ADDRESSES:
        raise NasAlertDeliveryError(503, "SMTP destination resolved ambiguously")
    return tuple(addresses)


class _PinnedSMTP(smtplib.SMTP):
    def __init__(self, family: int, address: tuple[Any, ...]) -> None:
        self._pinned_family = family
        self._pinned_address = address
        super().__init__(timeout=SMTP_TIMEOUT_SECONDS)

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        del host, port
        connection = socket.socket(self._pinned_family, socket.SOCK_STREAM)
        try:
            connection.settimeout(timeout)
            connection.connect(self._pinned_address)
            return connection
        except BaseException:
            connection.close()
            raise


class _PinnedSMTPSSL(smtplib.SMTP_SSL):
    def __init__(
        self,
        family: int,
        address: tuple[Any, ...],
        context: ssl.SSLContext,
    ) -> None:
        self._pinned_family = family
        self._pinned_address = address
        super().__init__(timeout=SMTP_TIMEOUT_SECONDS, context=context)

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        del host, port
        connection = socket.socket(self._pinned_family, socket.SOCK_STREAM)
        try:
            connection.settimeout(timeout)
            connection.connect(self._pinned_address)
            return self.context.wrap_socket(connection, server_hostname=self._host)
        except BaseException:
            connection.close()
            raise


def _message(config: Mapping[str, Any], payload: Mapping[str, Any]) -> EmailMessage:
    title = str(payload.get("title") or "Echo OS NAS 健康告警")
    severity = str(payload.get("severity") or "warning").upper()
    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or len(alerts) > MAX_ALERTS:
        raise NasAlertDeliveryError(503, "email alert payload is invalid")
    lines = [title, "", f"级别：{severity}", f"时间：{payload.get('sentAt', '')}", ""]
    for item in alerts:
        if not isinstance(item, Mapping):
            raise NasAlertDeliveryError(503, "email alert payload is invalid")
        lines.extend(
            [
                f"[{str(item.get('severity', '')).upper()}] {item.get('message', '')}",
                f"资源：{item.get('resource', '')}",
                f"代码：{item.get('code', '')}",
                "",
            ]
        )
    lines.append("此邮件由 Echo OS NAS 后台健康监控自动发送。")
    message = EmailMessage()
    message["From"] = str(config["fromAddress"])
    message["To"] = str(config["recipient"])
    message["Subject"] = f"[{severity}] {title}"
    message.set_content("\n".join(lines), charset="utf-8")
    if len(message.as_bytes()) > MAX_SMTP_MESSAGE_BYTES:
        raise NasAlertDeliveryError(503, "email alert payload exceeds its safety limit")
    return message


def _default_smtp_sender(config: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    host = _smtp_host(str(config["smtpHost"]))
    port = int(config["smtpPort"])
    addresses = _public_smtp_addresses(host, port)
    message = _message(config, payload)
    tls_context = ssl.create_default_context()
    for family, address in addresses:
        server: smtplib.SMTP | None = None
        try:
            if port == 465:
                server = _PinnedSMTPSSL(family, address, tls_context)
                server.connect(host, port)
            else:
                server = _PinnedSMTP(family, address)
                server.connect(host, port)
                server.ehlo()
                if not server.has_extn("starttls"):
                    raise NasAlertDeliveryError(503, "SMTP server does not require STARTTLS")
                server.starttls(context=tls_context)
                server.ehlo()
            server.login(str(config["username"]), str(config["password"]))
            refused = server.send_message(
                message,
                from_addr=str(config["fromAddress"]),
                to_addrs=[str(config["recipient"])],
            )
            if refused:
                raise NasAlertDeliveryError(503, "SMTP server rejected the recipient")
            return
        except NasAlertDeliveryError:
            raise
        except (OSError, smtplib.SMTPException, UnicodeError):
            continue
        finally:
            if server is not None:
                with contextlib.suppress(OSError, smtplib.SMTPException):
                    server.close()
    raise NasAlertDeliveryError(503, "SMTP email delivery failed")


def _empty_state() -> dict[str, Any]:
    return {
        "schema": EMAIL_ALERT_DELIVERY_SCHEMA,
        "enabled": False,
        "smtpHost": None,
        "smtpPort": 465,
        "username": None,
        "password": None,
        "fromAddress": None,
        "recipient": None,
        "deliveredIds": [],
        "consecutiveFailures": 0,
        "nextRetryAt": None,
        "lastAttemptAt": None,
        "lastSuccessAt": None,
        "lastError": None,
    }


def _validated_config(value: Mapping[str, Any]) -> dict[str, Any]:
    host = _smtp_host(str(value.get("smtpHost", "")))
    port = value.get("smtpPort")
    if isinstance(port, bool) or port not in {465, 587}:
        raise ValueError("invalid SMTP port")
    username = _smtp_credential(str(value.get("username", "")), label="username", maximum=320)
    password = _smtp_credential(str(value.get("password", "")), label="password", maximum=4096)
    from_address = _email_address(str(value.get("fromAddress", "")), label="sender")
    recipient = _email_address(str(value.get("recipient", "")), label="recipient")
    return {
        "smtpHost": host,
        "smtpPort": port,
        "username": username,
        "password": password,
        "fromAddress": from_address,
        "recipient": recipient,
    }


def _validate_state(value: Any) -> dict[str, Any]:
    expected = set(_empty_state())
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("invalid email alert delivery state fields")
    if value.get("schema") != EMAIL_ALERT_DELIVERY_SCHEMA or not isinstance(
        value.get("enabled"), bool
    ):
        raise ValueError("unsupported email alert delivery state")
    configured = value.get("smtpHost") is not None
    if value["enabled"] and not configured:
        raise ValueError("enabled email alert delivery lacks SMTP configuration")
    if configured:
        try:
            config = _validated_config(value)
        except NasAlertDeliveryError as exc:
            raise ValueError("invalid SMTP configuration") from exc
    else:
        if (
            any(
                value.get(key) is not None
                for key in ("username", "password", "fromAddress", "recipient")
            )
            or value.get("smtpPort") != 465
        ):
            raise ValueError("disabled email alert delivery contains partial SMTP configuration")
        config = {
            "smtpHost": None,
            "smtpPort": 465,
            "username": None,
            "password": None,
            "fromAddress": None,
            "recipient": None,
        }
    delivered = value.get("deliveredIds")
    if not isinstance(delivered, list) or len(delivered) > MAX_TRACKED_ALERTS:
        raise ValueError("invalid email alert delivery cursor")
    identifiers = [_identifier(item) for item in delivered]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("duplicate email alert delivery cursor")
    failures = value.get("consecutiveFailures")
    if isinstance(failures, bool) or not isinstance(failures, int) or not 0 <= failures <= 31:
        raise ValueError("invalid email alert delivery failure count")
    error = value.get("lastError")
    if error is not None and error not in {"delivery_failed", "health_probe_failed"}:
        raise ValueError("invalid email alert delivery error")
    return {
        "schema": EMAIL_ALERT_DELIVERY_SCHEMA,
        "enabled": value["enabled"],
        **config,
        "deliveredIds": identifiers,
        "consecutiveFailures": failures,
        "nextRetryAt": _optional_timestamp(value.get("nextRetryAt")),
        "lastAttemptAt": _optional_timestamp(value.get("lastAttemptAt")),
        "lastSuccessAt": _optional_timestamp(value.get("lastSuccessAt")),
        "lastError": error,
    }


def _read_envelope(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_size < 1
        or info.st_size > MAX_EMAIL_ALERT_DELIVERY_BYTES
        or (os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077)
    ):
        raise ValueError("email alert delivery state is unsafe")
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
        raise ValueError("invalid email alert delivery envelope")
    if raw.get("schema") != EMAIL_ALERT_DELIVERY_ENVELOPE_SCHEMA:
        raise ValueError("unsupported email alert delivery envelope")
    return raw


def validate_email_alert_delivery_state(
    path: Path | str,
    *,
    encryption_secret: str,
) -> dict[str, Any]:
    """Decrypt and strictly validate one restored SMTP alert state file."""

    raw = _read_envelope(Path(path))
    try:
        encoded = base64.b64decode(raw["ciphertext"], validate=True)
        if len(encoded) < 13 or len(encoded) > MAX_EMAIL_ALERT_DELIVERY_BYTES:
            raise ValueError("invalid email alert delivery ciphertext")
        key = hashlib.sha256(
            b"echo-os/nas-email-alert-delivery/v1\0" + encryption_secret.encode("utf-8")
        ).digest()
        plaintext = AESGCM(key).decrypt(encoded[:12], encoded[12:], _AAD)
        state = json.loads(plaintext)
    except (InvalidTag, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("email alert delivery state authentication failed") from exc
    return _validate_state(state)


class NasEmailAlertDeliveryService:
    def __init__(
        self,
        data_dir: Path | str,
        *,
        encryption_secret: str,
        storage_reader: Callable[[], Mapping[str, Any]] = native_storage.storage_health,
        ups_reader: Callable[[], Mapping[str, Any]] = ups_status,
        alert_reader: Callable[[], list[dict[str, str]]] | None = None,
        sender: Callable[[Mapping[str, Any], Mapping[str, Any]], None] = _default_smtp_sender,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not encryption_secret:
            raise ValueError("email alert delivery encryption secret is required")
        if interval_seconds <= 0:
            raise ValueError("email alert delivery interval must be positive")
        self.path = Path(data_dir) / EMAIL_ALERT_DELIVERY_FILENAME
        self._key = hashlib.sha256(
            b"echo-os/nas-email-alert-delivery/v1\0" + encryption_secret.encode("utf-8")
        ).digest()
        self._alert_reader = (
            alert_reader
            or NasAlertSnapshotSource(
                storage_reader=storage_reader,
                ups_reader=ups_reader,
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
                self._state = validate_email_alert_delivery_state(
                    self.path,
                    encryption_secret=encryption_secret,
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
        return {
            "schema": EMAIL_ALERT_DELIVERY_SCHEMA,
            "configured": state["smtpHost"] is not None,
            "enabled": state["enabled"] and persistence_healthy,
            "destinationHost": state["smtpHost"],
            "smtpPort": state["smtpPort"] if state["smtpHost"] else None,
            "security": ("implicit_tls" if state["smtpPort"] == 465 else "starttls")
            if state["smtpHost"]
            else None,
            "recipientHint": _recipient_hint(state["recipient"]),
            "hasCredentials": state["username"] is not None and state["password"] is not None,
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

    def plan(self, desired: EmailDesiredState) -> dict[str, Any]:
        if desired.enabled:
            config = _validated_config(
                {
                    "smtpHost": desired.smtp_host.get_secret_value(),  # type: ignore[union-attr]
                    "smtpPort": desired.smtp_port,
                    "username": desired.username.get_secret_value(),  # type: ignore[union-attr]
                    "password": desired.password.get_secret_value(),  # type: ignore[union-attr]
                    "fromAddress": desired.from_address.get_secret_value(),  # type: ignore[union-attr]
                    "recipient": desired.recipient.get_secret_value(),  # type: ignore[union-attr]
                }
            )
            planned = _empty_state()
            planned.update({"enabled": True, **config})
        else:
            planned = _empty_state()
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
            current = copy.deepcopy(self._state)
        return {
            "schema": EMAIL_ALERT_DELIVERY_SCHEMA,
            "planId": plan_id,
            "changes": [
                {"field": "enabled", "before": current["enabled"], "after": desired.enabled},
                {
                    "field": "destinationHost",
                    "before": current["smtpHost"],
                    "after": planned["smtpHost"],
                },
                {
                    "field": "smtpPort",
                    "before": current["smtpPort"] if current["smtpHost"] else None,
                    "after": planned["smtpPort"] if planned["smtpHost"] else None,
                },
                {
                    "field": "recipient",
                    "before": _recipient_hint(current["recipient"]),
                    "after": _recipient_hint(planned["recipient"]),
                },
                {"field": "credentials", "before": "redacted", "after": "redacted"},
            ],
            "requiresApproval": True,
            "secretsPersistedEncrypted": True,
            "publicSmtpOnly": True,
            "tlsRequired": True,
        }

    def apply(self, plan_id: str) -> dict[str, Any]:
        now = self._monotonic()
        with self._lock:
            planned = self._plans.pop(plan_id, None)
            if planned is None or now - planned[0] > PLAN_TTL_SECONDS:
                raise NasAlertDeliveryError(409, "email alert plan is missing or expired")
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
            raise NasAlertDeliveryError(503, "email alert state could not be saved") from exc
        self._persistence_healthy = True

    def _persist_locked(self) -> None:
        plaintext = json.dumps(
            self._state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        nonce = secrets.token_bytes(12)
        ciphertext = nonce + AESGCM(self._key).encrypt(nonce, plaintext, _AAD)
        envelope = json.dumps(
            {
                "schema": EMAIL_ALERT_DELIVERY_ENVELOPE_SCHEMA,
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if len(envelope) > MAX_EMAIL_ALERT_DELIVERY_BYTES:
            raise NasAlertDeliveryError(500, "email alert state exceeds its safety limit")
        if os.name == "nt":
            from appliance.windows_state import private_state_directory

            context = private_state_directory(self.path.parent, create=True, protect=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            context = contextlib.nullcontext(self.path.parent)
        with context as parent:
            path = Path(parent) / self.path.name
            if path.is_symlink():
                raise NasAlertDeliveryError(503, "email alert state path is unsafe")
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

    @staticmethod
    def _configuration_matches(current: Mapping[str, Any], observed: Mapping[str, Any]) -> bool:
        return all(
            current[key] == observed[key]
            for key in (
                "enabled",
                "smtpHost",
                "smtpPort",
                "username",
                "password",
                "fromAddress",
                "recipient",
            )
        )

    def _payload(self, alerts: list[dict[str, str]], *, test: bool = False) -> dict[str, Any]:
        critical = any(item["severity"] == "critical" for item in alerts)
        sent_at = _timestamp(self._clock())
        return {
            "schema": "echo.nas-alert.email.v1",
            "eventId": hashlib.sha256(
                (sent_at + "\0" + "\0".join(item["id"] for item in alerts)).encode()
            ).hexdigest()[:32],
            "sentAt": sent_at,
            "test": test,
            "severity": "critical" if critical else "warning",
            "title": "Echo OS NAS 测试邮件" if test else "Echo OS NAS 健康告警",
            "alerts": alerts,
        }

    def poll(self) -> dict[str, Any]:
        with self._poll_lock:
            with self._lock:
                state = copy.deepcopy(self._state)
                healthy = self._persistence_healthy
            if not healthy or not state["enabled"]:
                return self.status()
            try:
                alerts = self._alert_reader()
                if (
                    not isinstance(alerts, list)
                    or len(alerts) > MAX_ALERTS
                    or any(not isinstance(item, dict) for item in alerts)
                    or len({item.get("id") for item in alerts}) != len(alerts)
                ):
                    raise ValueError("invalid or duplicate NAS alerts")
                alerts = copy.deepcopy(alerts)
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
                        if not self._configuration_matches(self._state, state):
                            return self.status()
                        next_state = copy.deepcopy(self._state)
                        next_state["deliveredIds"] = sorted(delivered_ids)
                        self._commit_locked(next_state)
                return self.status()
            attempted = _timestamp(now)
            try:
                self._sender(state, self._payload(unseen))
            except (NasAlertDeliveryError, OSError, ValueError):
                failures = min(31, state["consecutiveFailures"] + 1)
                delay = min(3600, 30 * 2 ** min(failures - 1, 7))
                next_retry = _timestamp(datetime.fromtimestamp(now.timestamp() + delay, UTC))
                with self._lock:
                    if not self._configuration_matches(self._state, state):
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
                if not self._configuration_matches(self._state, state):
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
        if not self._persistence_healthy or not state["enabled"] or not state["smtpHost"]:
            raise NasAlertDeliveryError(409, "email alert delivery is not enabled")
        self._sender(
            state,
            self._payload(
                [
                    {
                        "id": "test",
                        "source": "system",
                        "code": "notification.test",
                        "severity": "warning",
                        "resource": "echo-os",
                        "message": "这是一封 Echo OS NAS 测试邮件",
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
                target=self._run,
                name="echo-nas-email-alert-delivery",
                daemon=True,
            )
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception as exc:  # pragma: no cover - last-resort thread survival
                import logging

                logging.getLogger("echo.appliance.nas_email_alert_delivery").error(
                    "NAS email alert delivery poll failed: %s",
                    type(exc).__name__,
                )
            self._stop.wait(self.interval_seconds)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)


def create_nas_email_alert_delivery_router(
    service: NasEmailAlertDeliveryService,
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
                metadata={"channel": "email", "secretsRedacted": True},
            )
        except (OSError, AuditIntegrityError) as exc:
            raise HTTPException(503, "appliance audit integrity check failed") from exc

    @router.get("/email")
    def status(_actor: str = Depends(require_operator)) -> dict[str, Any]:
        return service.status()

    @router.post("/email/plan")
    def plan(
        body: EmailDesiredState,
        _actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        try:
            return service.plan(body)
        except NasAlertDeliveryError as exc:
            raise HTTPException(exc.status_code, exc.detail) from exc

    @router.post("/email/apply")
    def apply(
        body: EmailApplyRequest,
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        plan_id = body.plan_id
        consume_request_approval(
            request,
            approval,
            actor=actor,
            action=EMAIL_ALERT_CONFIGURE_ACTION,
            target=plan_id,
        )
        record(
            actor=actor,
            action=EMAIL_ALERT_CONFIGURE_ACTION,
            target=plan_id,
            outcome="attempted",
        )
        try:
            result = service.apply(plan_id)
        except NasAlertDeliveryError as exc:
            record(
                actor=actor,
                action=EMAIL_ALERT_CONFIGURE_ACTION,
                target=plan_id,
                outcome="failed",
            )
            raise HTTPException(exc.status_code, exc.detail) from exc
        record(
            actor=actor,
            action=EMAIL_ALERT_CONFIGURE_ACTION,
            target=plan_id,
            outcome="succeeded",
        )
        return result

    @router.post("/email/test")
    def send_test(
        request: Request,
        actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        target = service.status()["revision"]
        consume_request_approval(
            request,
            approval,
            actor=actor,
            action=EMAIL_ALERT_TEST_ACTION,
            target=target,
        )
        record(
            actor=actor,
            action=EMAIL_ALERT_TEST_ACTION,
            target=target,
            outcome="attempted",
        )
        try:
            result = service.send_test()
        except NasAlertDeliveryError as exc:
            record(
                actor=actor,
                action=EMAIL_ALERT_TEST_ACTION,
                target=target,
                outcome="failed",
            )
            raise HTTPException(exc.status_code, exc.detail) from exc
        record(
            actor=actor,
            action=EMAIL_ALERT_TEST_ACTION,
            target=target,
            outcome="succeeded",
        )
        return result

    return router


__all__ = [
    "EMAIL_ALERT_CONFIGURE_ACTION",
    "EMAIL_ALERT_DELIVERY_FILENAME",
    "EMAIL_ALERT_TEST_ACTION",
    "EmailApplyRequest",
    "EmailDesiredState",
    "MAX_EMAIL_ALERT_DELIVERY_BYTES",
    "NasEmailAlertDeliveryService",
    "create_nas_email_alert_delivery_router",
    "validate_email_alert_delivery_state",
]
