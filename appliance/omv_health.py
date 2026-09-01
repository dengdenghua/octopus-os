"""Persistent, read-only health monitoring for the optional OMV bridge.

The host bridge remains the only component that talks to OpenMediaVault.  This
module periodically reads the already-sanitized Echo client views, derives a
small set of deterministic alerts, and stores only those alerts plus their
transition history in the appliance state directory.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import stat
import tempfile
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from appliance.omv_client import OmvClient, OmvUnavailable

HEALTH_STATE_FILENAME = "omv-health-state.json"
HEALTH_SCHEMA_VERSION = 1
DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_TEMPERATURE_WARNING_C = 50
DEFAULT_TEMPERATURE_CRITICAL_C = 60
DEFAULT_CAPACITY_WARNING_PERCENT = 90
DEFAULT_CAPACITY_CRITICAL_PERCENT = 95
MAX_ACTIVE_ALERTS = 128
MAX_EVENTS = 256
MAX_STATE_BYTES = 512 * 1024

_KNOWN_CODES = {
    "bridge.unavailable",
    "disk.smart",
    "disk.temperature",
    "volume.capacity",
    "raid.degraded",
    "raid.recovering",
    "raid.checking",
    "raid.inactive",
    "raid.unknown",
}
_KNOWN_SEVERITIES = {"warning", "critical"}
_KNOWN_EVENT_TYPES = {"opened", "changed", "resolved"}
_KNOWN_STATES = {"notConfigured", "pending", "healthy", "warning", "critical", "unavailable"}
_HEALTHY_SMART_VALUES = {"passed", "ok", "good", "healthy", "true"}

_log = logging.getLogger("echo.appliance.omv_health")


class OmvHealthStateError(RuntimeError):
    """Persistent health state could not be read or written safely."""


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _valid_timestamp(value: Any, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or len(value) > 32:
        raise OmvHealthStateError("OMV health state contains an invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OmvHealthStateError("OMV health state contains an invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise OmvHealthStateError("OMV health state timestamp lacks a timezone")
    return _timestamp(parsed)


def _safe_text(value: Any, *, maximum: int, label: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise OmvHealthStateError(f"OMV health state contains an invalid {label}")
    if (not value and not allow_empty) or any(character < " " for character in value):
        raise OmvHealthStateError(f"OMV health state contains an invalid {label}")
    if "/dev/disk/by-id/" in value:
        raise OmvHealthStateError("OMV health state contains a forbidden by-id path")
    return value


def _alert_id(code: str, resource: str) -> str:
    return hashlib.sha256(f"{code}\0{resource}".encode()).hexdigest()[:24]


def _event_id(alert_id: str, event: str, at: str, ordinal: int) -> str:
    return hashlib.sha256(f"{alert_id}\0{event}\0{at}\0{ordinal}".encode()).hexdigest()[:24]


def _configured_integer(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = os.environ.get(name)
    try:
        value = default if raw is None or not raw.strip() else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _empty_snapshot(*, configured: bool, interval_seconds: int) -> dict[str, Any]:
    return {
        "schemaVersion": HEALTH_SCHEMA_VERSION,
        "state": "pending" if configured else "notConfigured",
        "stale": False,
        "checkedAt": None,
        "lastSuccessfulAt": None,
        "intervalSeconds": interval_seconds,
        "persistenceHealthy": True,
        "activeAlerts": [],
        "events": [],
        "summary": {"critical": 0, "warning": 0, "total": 0},
        "readOnly": True,
    }


def _validate_alert(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OmvHealthStateError("OMV health state contains an invalid alert")
    alert_id = _safe_text(value.get("id"), maximum=24, label="alert id")
    if len(alert_id) != 24 or any(character not in "0123456789abcdef" for character in alert_id):
        raise OmvHealthStateError("OMV health state contains an invalid alert id")
    code = _safe_text(value.get("code"), maximum=64, label="alert code")
    severity = _safe_text(value.get("severity"), maximum=16, label="alert severity")
    resource = _safe_text(value.get("resource"), maximum=256, label="alert resource")
    message = _safe_text(value.get("message"), maximum=512, label="alert message")
    if code not in _KNOWN_CODES or severity not in _KNOWN_SEVERITIES:
        raise OmvHealthStateError("OMV health state contains an unsupported alert")
    if alert_id != _alert_id(code, resource):
        raise OmvHealthStateError("OMV health state alert identity does not match its resource")
    occurrences = value.get("occurrences")
    if (
        isinstance(occurrences, bool)
        or not isinstance(occurrences, int)
        or not 1 <= occurrences <= 2**63 - 1
    ):
        raise OmvHealthStateError("OMV health state contains an invalid occurrence count")
    return {
        "id": alert_id,
        "code": code,
        "severity": severity,
        "resource": resource,
        "message": message,
        "firstSeenAt": _valid_timestamp(value.get("firstSeenAt")),
        "lastSeenAt": _valid_timestamp(value.get("lastSeenAt")),
        "occurrences": occurrences,
    }


def _validate_event(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OmvHealthStateError("OMV health state contains an invalid event")
    event_id = _safe_text(value.get("id"), maximum=24, label="event id")
    alert_id = _safe_text(value.get("alertId"), maximum=24, label="event alert id")
    event = _safe_text(value.get("event"), maximum=16, label="event type")
    code = _safe_text(value.get("code"), maximum=64, label="event code")
    severity = _safe_text(value.get("severity"), maximum=16, label="event severity")
    resource = _safe_text(value.get("resource"), maximum=256, label="event resource")
    message = _safe_text(value.get("message"), maximum=512, label="event message")
    at = _valid_timestamp(value.get("at"))
    if (
        len(event_id) != 24
        or any(character not in "0123456789abcdef" for character in event_id)
        or len(alert_id) != 24
        or any(character not in "0123456789abcdef" for character in alert_id)
        or event not in _KNOWN_EVENT_TYPES
        or code not in _KNOWN_CODES
        or severity not in _KNOWN_SEVERITIES
        or alert_id != _alert_id(code, resource)
    ):
        raise OmvHealthStateError("OMV health state contains an unsupported event")
    return {
        "id": event_id,
        "alertId": alert_id,
        "event": event,
        "at": at,
        "code": code,
        "severity": severity,
        "resource": resource,
        "message": message,
    }


def _summary(alerts: list[dict[str, Any]]) -> dict[str, int]:
    critical = sum(alert["severity"] == "critical" for alert in alerts)
    warning = sum(alert["severity"] == "warning" for alert in alerts)
    return {"critical": critical, "warning": warning, "total": len(alerts)}


def _validate_snapshot(value: Any, *, interval_seconds: int) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schemaVersion") != HEALTH_SCHEMA_VERSION:
        raise OmvHealthStateError("OMV health state schema is unsupported")
    state = _safe_text(value.get("state"), maximum=32, label="monitor state")
    if state not in _KNOWN_STATES or not isinstance(value.get("stale"), bool):
        raise OmvHealthStateError("OMV health state contains an invalid monitor state")
    alerts_raw = value.get("activeAlerts")
    events_raw = value.get("events")
    if (
        not isinstance(alerts_raw, list)
        or len(alerts_raw) > MAX_ACTIVE_ALERTS
        or not isinstance(events_raw, list)
        or len(events_raw) > MAX_EVENTS
    ):
        raise OmvHealthStateError("OMV health state exceeds its collection limit")
    alerts = [_validate_alert(item) for item in alerts_raw]
    if len({item["id"] for item in alerts}) != len(alerts):
        raise OmvHealthStateError("OMV health state contains duplicate alerts")
    events = [_validate_event(item) for item in events_raw]
    return {
        "schemaVersion": HEALTH_SCHEMA_VERSION,
        "state": state,
        "stale": value["stale"],
        "checkedAt": _valid_timestamp(value.get("checkedAt"), optional=True),
        "lastSuccessfulAt": _valid_timestamp(value.get("lastSuccessfulAt"), optional=True),
        "intervalSeconds": interval_seconds,
        "persistenceHealthy": True,
        "activeAlerts": alerts,
        "events": events,
        "summary": _summary(alerts),
        "readOnly": True,
    }


class OmvHealthMonitor:
    """Poll sanitized OMV views and retain bounded alert transitions."""

    def __init__(
        self,
        client: OmvClient,
        state_path: Path | str | None,
        *,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        temperature_warning_c: int = DEFAULT_TEMPERATURE_WARNING_C,
        temperature_critical_c: int = DEFAULT_TEMPERATURE_CRITICAL_C,
        capacity_warning_percent: int = DEFAULT_CAPACITY_WARNING_PERCENT,
        capacity_critical_percent: int = DEFAULT_CAPACITY_CRITICAL_PERCENT,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("OMV health interval must be positive")
        if not 0 <= temperature_warning_c < temperature_critical_c <= 300:
            raise ValueError("OMV health temperature thresholds are invalid")
        if not 0 <= capacity_warning_percent < capacity_critical_percent <= 100:
            raise ValueError("OMV health capacity thresholds are invalid")
        self._client = client
        self._state_path = Path(state_path) if state_path is not None else None
        self.interval_seconds = interval_seconds
        self.temperature_warning_c = temperature_warning_c
        self.temperature_critical_c = temperature_critical_c
        self.capacity_warning_percent = capacity_warning_percent
        self.capacity_critical_percent = capacity_critical_percent
        self._clock = clock
        self._state_lock = threading.RLock()
        self._poll_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot = _empty_snapshot(
            configured=client.configured,
            interval_seconds=interval_seconds,
        )
        self._load()

    @classmethod
    def from_environment(
        cls,
        client: OmvClient,
        state_path: Path | str | None,
    ) -> OmvHealthMonitor:
        interval = _configured_integer(
            "ECHO_OMV_HEALTH_INTERVAL_SECONDS",
            DEFAULT_INTERVAL_SECONDS,
            minimum=60,
            maximum=86400,
        )
        temperature_warning = _configured_integer(
            "ECHO_OMV_TEMP_WARNING_C",
            DEFAULT_TEMPERATURE_WARNING_C,
            minimum=30,
            maximum=90,
        )
        temperature_critical = _configured_integer(
            "ECHO_OMV_TEMP_CRITICAL_C",
            DEFAULT_TEMPERATURE_CRITICAL_C,
            minimum=31,
            maximum=100,
        )
        capacity_warning = _configured_integer(
            "ECHO_OMV_CAPACITY_WARNING_PERCENT",
            DEFAULT_CAPACITY_WARNING_PERCENT,
            minimum=50,
            maximum=99,
        )
        capacity_critical = _configured_integer(
            "ECHO_OMV_CAPACITY_CRITICAL_PERCENT",
            DEFAULT_CAPACITY_CRITICAL_PERCENT,
            minimum=51,
            maximum=100,
        )
        return cls(
            client,
            state_path,
            interval_seconds=interval,
            temperature_warning_c=temperature_warning,
            temperature_critical_c=temperature_critical,
            capacity_warning_percent=capacity_warning,
            capacity_critical_percent=capacity_critical,
        )

    @property
    def configured(self) -> bool:
        return self._client.configured

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._thread is not None and self._thread.is_alive()

    def _load(self) -> None:
        if not self.configured or self._state_path is None or not self._state_path.exists():
            return
        try:
            info = self._state_path.lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise OmvHealthStateError("OMV health state is not a private regular file")
            if info.st_size > MAX_STATE_BYTES:
                raise OmvHealthStateError("OMV health state exceeds its size limit")
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            snapshot = _validate_snapshot(raw, interval_seconds=self.interval_seconds)
        except (OSError, ValueError, OmvHealthStateError) as exc:
            self._snapshot["persistenceHealthy"] = False
            _log.error("OMV health state was not loaded safely: %s", exc)
            return
        self._snapshot = snapshot

    def _persist(self, snapshot: dict[str, Any]) -> None:
        if self._state_path is None:
            return
        path = self._state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        parent_info = path.parent.stat()
        if not stat.S_ISDIR(parent_info.st_mode) or path.parent.is_symlink():
            raise OmvHealthStateError("OMV health state parent is unsafe")
        if path.is_symlink():
            raise OmvHealthStateError("OMV health state must not be a symlink")
        encoded = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_STATE_BYTES:
            raise OmvHealthStateError("OMV health state exceeds its size limit")
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(fd, 0o600)
            descriptor = fd
            fd = -1
            with os.fdopen(descriptor, "wb") as output:
                output.write(encoded)
                output.write(b"\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            path.chmod(0o600)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if fd >= 0:
                os.close(fd)
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _candidate(
        *,
        code: str,
        severity: str,
        resource: str,
        message: str,
    ) -> dict[str, Any]:
        if code not in _KNOWN_CODES or severity not in _KNOWN_SEVERITIES:
            raise ValueError("unsupported OMV health alert")
        return {
            "id": _alert_id(code, resource),
            "code": code,
            "severity": severity,
            "resource": resource,
            "message": message[:512],
        }

    def _derive_alerts(
        self,
        filesystems: list[dict[str, Any]],
        devices: list[dict[str, Any]],
        topology: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        for device in devices:
            resource = str(device["devicefile"])
            health = str(device["health"])
            if health.strip().casefold() not in _HEALTHY_SMART_VALUES:
                alerts.append(
                    self._candidate(
                        code="disk.smart",
                        severity="critical",
                        resource=resource,
                        message=f"磁盘 SMART 健康异常：{health}",
                    )
                )
            temperature = device.get("temperatureC")
            if isinstance(temperature, int) and temperature >= self.temperature_warning_c:
                alerts.append(
                    self._candidate(
                        code="disk.temperature",
                        severity=(
                            "critical" if temperature >= self.temperature_critical_c else "warning"
                        ),
                        resource=resource,
                        message=f"磁盘温度为 {temperature}°C",
                    )
                )

        for filesystem in filesystems:
            size = int(filesystem["sizeBytes"])
            available = int(filesystem["availableBytes"])
            used = filesystem.get("usedPercent")
            if not isinstance(used, int):
                used = round((size - available) / size * 100) if size > 0 else 0
            if used < self.capacity_warning_percent:
                continue
            label = str(filesystem.get("label") or filesystem["devicefile"])
            alerts.append(
                self._candidate(
                    code="volume.capacity",
                    severity=("critical" if used >= self.capacity_critical_percent else "warning"),
                    resource=str(filesystem["devicefile"]),
                    message=f"存储卷 {label} 已使用 {used}%",
                )
            )

        for array in topology.get("arrays", []):
            status_value = str(array["status"]).casefold()
            if status_value not in {"degraded", "recovering", "checking", "inactive", "unknown"}:
                continue
            messages = {
                "degraded": "软件阵列已降级",
                "recovering": "软件阵列正在重建",
                "checking": "软件阵列正在校验",
                "inactive": "软件阵列未激活",
                "unknown": "软件阵列状态未知",
            }
            progress = array.get("operationPercent")
            message = messages[status_value]
            if isinstance(progress, int):
                message = f"{message}（{progress}%）"
            alerts.append(
                self._candidate(
                    code=f"raid.{status_value}",
                    severity=(
                        "critical"
                        if status_value in {"degraded", "inactive", "unknown"}
                        else "warning"
                    ),
                    resource=str(array["devicefile"]),
                    message=message,
                )
            )
        if len(alerts) > MAX_ACTIVE_ALERTS:
            raise OmvUnavailable("OMV health check produced too many alerts")
        return sorted(alerts, key=lambda item: (item["severity"] != "critical", item["id"]))

    @staticmethod
    def _transition_event(
        alert: dict[str, Any],
        *,
        event: str,
        at: str,
        ordinal: int,
    ) -> dict[str, Any]:
        return {
            "id": _event_id(alert["id"], event, at, ordinal),
            "alertId": alert["id"],
            "event": event,
            "at": at,
            "code": alert["code"],
            "severity": alert["severity"],
            "resource": alert["resource"],
            "message": alert["message"],
        }

    def _merge(
        self,
        candidates: list[dict[str, Any]],
        *,
        at: str,
        success: bool,
    ) -> dict[str, Any]:
        previous = self._snapshot
        previous_by_id = {item["id"]: item for item in previous["activeAlerts"]}
        candidate_by_id = {item["id"]: item for item in candidates}
        active: list[dict[str, Any]] = []
        events = list(previous["events"])

        for candidate in candidates:
            prior = previous_by_id.get(candidate["id"])
            if prior is None:
                alert = {
                    **candidate,
                    "firstSeenAt": at,
                    "lastSeenAt": at,
                    "occurrences": 1,
                }
                events.append(
                    self._transition_event(alert, event="opened", at=at, ordinal=len(events))
                )
                _log.warning("OMV health alert opened: %s %s", alert["code"], alert["resource"])
            else:
                changed = (
                    prior["severity"] != candidate["severity"]
                    or prior["message"] != candidate["message"]
                )
                alert = {
                    **candidate,
                    "firstSeenAt": prior["firstSeenAt"],
                    "lastSeenAt": at,
                    "occurrences": prior["occurrences"] + 1,
                }
                if changed:
                    events.append(
                        self._transition_event(
                            alert,
                            event="changed",
                            at=at,
                            ordinal=len(events),
                        )
                    )
            active.append(alert)

        if success:
            for alert_id, prior in previous_by_id.items():
                if alert_id in candidate_by_id:
                    continue
                events.append(
                    self._transition_event(
                        prior,
                        event="resolved",
                        at=at,
                        ordinal=len(events),
                    )
                )
                _log.info("OMV health alert resolved: %s %s", prior["code"], prior["resource"])

        events = events[-MAX_EVENTS:]
        summary = _summary(active)
        if not success:
            state = "unavailable"
        elif summary["critical"]:
            state = "critical"
        elif summary["warning"]:
            state = "warning"
        else:
            state = "healthy"
        return {
            "schemaVersion": HEALTH_SCHEMA_VERSION,
            "state": state,
            "stale": not success,
            "checkedAt": at,
            "lastSuccessfulAt": at if success else previous.get("lastSuccessfulAt"),
            "intervalSeconds": self.interval_seconds,
            "persistenceHealthy": True,
            "activeAlerts": active,
            "events": events,
            "summary": summary,
            "readOnly": True,
        }

    def poll(self) -> dict[str, Any]:
        """Perform one synchronous health check and return a safe snapshot."""

        with self._poll_lock:
            if not self.configured:
                with self._state_lock:
                    self._snapshot = _empty_snapshot(
                        configured=False,
                        interval_seconds=self.interval_seconds,
                    )
                return self.snapshot()

            at = _timestamp(self._clock())
            try:
                filesystems = self._client.filesystems()
                devices = self._client.smart_devices()
                topology = self._client.storage_topology()
                candidates = self._derive_alerts(filesystems, devices, topology)
                success = True
            except (OmvUnavailable, OSError) as exc:
                with self._state_lock:
                    prior = [
                        copy.deepcopy(item)
                        for item in self._snapshot["activeAlerts"]
                        if item["code"] != "bridge.unavailable"
                    ]
                candidates = [
                    {
                        "id": item["id"],
                        "code": item["code"],
                        "severity": item["severity"],
                        "resource": item["resource"],
                        "message": item["message"],
                    }
                    for item in prior
                ]
                candidates.append(
                    self._candidate(
                        code="bridge.unavailable",
                        severity="critical",
                        resource="openmediavault",
                        message="OMV 只读桥暂时不可用，之前的存储状态已标记为过期",
                    )
                )
                success = False
                _log.warning("OMV health poll failed: %s", type(exc).__name__)

            with self._state_lock:
                next_snapshot = self._merge(candidates, at=at, success=success)
                if not success:
                    prior_by_id = {item["id"]: item for item in self._snapshot["activeAlerts"]}
                    for alert in next_snapshot["activeAlerts"]:
                        if alert["code"] == "bridge.unavailable":
                            continue
                        prior = prior_by_id.get(alert["id"])
                        if prior is not None:
                            alert["firstSeenAt"] = prior["firstSeenAt"]
                            alert["lastSeenAt"] = prior["lastSeenAt"]
                            alert["occurrences"] = prior["occurrences"]
                self._snapshot = next_snapshot
                try:
                    self._persist(next_snapshot)
                except (OSError, OmvHealthStateError) as exc:
                    self._snapshot["persistenceHealthy"] = False
                    _log.error("OMV health state was not persisted safely: %s", exc)
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            result = copy.deepcopy(self._snapshot)
            result["monitoring"] = self.running
            return result

    def start(self) -> None:
        if not self.configured:
            return
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="echo-omv-health",
                daemon=True,
            )
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception:  # pragma: no cover - last-resort thread survival
                _log.exception("unexpected OMV health monitor failure")
            self._stop.wait(self.interval_seconds)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._state_lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)


__all__ = [
    "HEALTH_STATE_FILENAME",
    "OmvHealthMonitor",
    "OmvHealthStateError",
]
