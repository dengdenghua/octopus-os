"""Privacy-preserving support bundle for the Echo OS appliance.

The bundle is intentionally an allow-list, not a redaction pass over arbitrary
logs or configuration.  It contains enough high-level evidence to triage a NAS
without exporting host names, addresses, mount paths, device identities,
accounts, credentials, environment variables, or raw command output.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import shutil
import sys
import zipfile
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.native_service_health import service_health
from appliance.security import ApplianceAuthenticator, resolve_authenticator

DIAGNOSTIC_BUNDLE_SCHEMA = "echo.appliance-diagnostics-bundle.v1"
DIAGNOSTIC_EXPORT_ACTION = "diagnostics.bundle.export"
DIAGNOSTIC_EXPORT_TARGET = "support-bundle"
MAX_BUNDLE_BYTES = 512 * 1024

_COMMANDS = (
    "btrfs",
    "docker",
    "exportfs",
    "findmnt",
    "lsblk",
    "mdadm",
    "smartctl",
    "smbstatus",
    "systemctl",
    "upsc",
    "zfs",
    "zpool",
)
_COUNT_FIELDS = ("pools", "arrays", "filesystems", "devices")
_HEALTH_STATES = {"healthy", "warning", "critical", "degraded", "unknown"}
_PROBE_STATES = {"ok", "empty", "partial", "error", "unavailable"}
_SEVERITIES = {"warning", "critical"}
_SAFE_CODE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class DiagnosticBundleError(RuntimeError):
    """Raised when a bounded diagnostic archive cannot be produced."""


@dataclass(frozen=True)
class DiagnosticBundle:
    archive: bytes
    filename: str
    bundle_id: str
    created_at: str


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _distribution_version() -> str:
    try:
        value = metadata.version("echo-os")
    except metadata.PackageNotFoundError:
        return "development"
    return value[:64] if value else "unknown"


def _bounded_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return min(max(value, 0), 1_000_000_000)


def _safe_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    return normalized if _SAFE_CODE.fullmatch(normalized) else None


def _safe_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _system_summary(command_finder: Callable[[str], str | None]) -> dict[str, Any]:
    machine = platform.machine().strip().casefold()[:32] or "unknown"
    kernel = platform.release().strip()[:128] or "unknown"
    return {
        "schema": "echo.appliance-diagnostics-system.v1",
        "echoOsVersion": _distribution_version(),
        "platform": {
            "family": platform.system().strip().casefold()[:32] or os.name,
            "architecture": machine,
            "kernelRelease": kernel,
        },
        "python": {
            "implementation": platform.python_implementation()[:32],
            "version": ".".join(str(part) for part in sys.version_info[:3]),
        },
        "commandsAvailable": {
            command: command_finder(command) is not None for command in _COMMANDS
        },
    }


def _unavailable_storage_summary() -> dict[str, Any]:
    return {
        "schema": "echo.appliance-diagnostics-storage.v1",
        "state": "unknown",
        "coverage": "none",
        "stale": True,
        "checkedAt": None,
        "counts": {field: 0 for field in _COUNT_FIELDS},
        "alerts": {"total": 0, "bySeverity": {}, "codes": []},
        "probes": {
            "total": 0,
            "required": 0,
            "byState": {},
            "failureCodes": ["collection_failed"],
        },
    }


def _storage_summary(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return _unavailable_storage_summary()

    raw_state = raw.get("state")
    state = raw_state if raw_state in _HEALTH_STATES else "unknown"
    raw_coverage = raw.get("coverage")
    coverage = raw_coverage if raw_coverage in {"complete", "partial", "none"} else "none"

    alerts = raw.get("activeAlerts")
    alert_items = alerts[:256] if isinstance(alerts, list) else []
    severities: Counter[str] = Counter()
    alert_codes: set[str] = set()
    for alert in alert_items:
        if not isinstance(alert, Mapping):
            continue
        severity = alert.get("severity")
        if severity in _SEVERITIES:
            severities[str(severity)] += 1
        code = _safe_code(alert.get("code"))
        if code is not None:
            alert_codes.add(code)

    probes = raw.get("probeEvidence")
    probe_items = probes[:256] if isinstance(probes, list) else []
    probe_states: Counter[str] = Counter()
    failure_codes: set[str] = set()
    required = 0
    for probe in probe_items:
        if not isinstance(probe, Mapping):
            continue
        if probe.get("required") is True:
            required += 1
        probe_state = probe.get("state")
        normalized_state = probe_state if probe_state in _PROBE_STATES else "error"
        probe_states[str(normalized_state)] += 1
        if normalized_state != "ok":
            code = _safe_code(probe.get("code"))
            if code is not None:
                failure_codes.add(code)

    return {
        "schema": "echo.appliance-diagnostics-storage.v1",
        "state": state,
        "coverage": coverage,
        "stale": raw.get("stale") is not False,
        "checkedAt": _safe_timestamp(raw.get("checkedAt")),
        "counts": {field: _bounded_count(raw.get(field)) for field in _COUNT_FIELDS},
        "alerts": {
            "total": sum(severities.values()),
            "bySeverity": dict(sorted(severities.items())),
            "codes": sorted(alert_codes)[:64],
        },
        "probes": {
            "total": len(probe_items),
            "required": required,
            "byState": dict(sorted(probe_states.items())),
            "failureCodes": sorted(failure_codes)[:64],
        },
    }


def _unavailable_service_summary() -> dict[str, Any]:
    return {
        "schema": "echo.appliance-diagnostics-services.v1",
        "state": "unknown",
        "available": False,
        "checkedAt": None,
        "counts": {
            "monitored": 0,
            "expected": 0,
            "active": 0,
            "failed": 0,
            "restarts": 0,
        },
        "alerts": {"total": 0, "bySeverity": {}, "codes": []},
    }


def _service_summary(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    if raw is None or raw.get("available") is not True:
        return _unavailable_service_summary()

    state_value = raw.get("state")
    state = state_value if state_value in _HEALTH_STATES else "unknown"
    units = raw.get("units")
    unit_items = units[:32] if isinstance(units, list) else []
    expected = 0
    active = 0
    failed = 0
    restarts = 0
    for unit in unit_items:
        if not isinstance(unit, Mapping):
            continue
        if unit.get("expected") is True:
            expected += 1
        active_state = unit.get("activeState")
        if active_state in {"active", "reloading", "activating"}:
            active += 1
        if active_state == "failed":
            failed += 1
        restarts += _bounded_count(unit.get("restarts"))

    alert_items = raw.get("activeAlerts")
    alerts = alert_items[:128] if isinstance(alert_items, list) else []
    severities: Counter[str] = Counter()
    codes: set[str] = set()
    for alert in alerts:
        if not isinstance(alert, Mapping):
            continue
        severity = alert.get("severity")
        if severity in _SEVERITIES:
            severities[str(severity)] += 1
        code = _safe_code(alert.get("code"))
        if code is not None:
            codes.add(code)

    return {
        "schema": "echo.appliance-diagnostics-services.v1",
        "state": state,
        "available": True,
        "checkedAt": _safe_timestamp(raw.get("checkedAt")),
        "counts": {
            "monitored": len(unit_items),
            "expected": expected,
            "active": active,
            "failed": failed,
            "restarts": min(restarts, 1_000_000_000),
        },
        "alerts": {
            "total": sum(severities.values()),
            "bySeverity": dict(sorted(severities.items())),
            "codes": sorted(codes)[:64],
        },
    }


def _audit_summary(audit: ApplianceAudit) -> dict[str, Any]:
    verification = audit.verify()
    key_status = audit.key_status()
    return {
        "schema": "echo.appliance-diagnostics-audit.v1",
        "integrity": {
            "ok": verification.ok,
            "entriesChecked": _bounded_count(verification.entries_checked),
            "brokenAt": verification.broken_at,
            "errorCode": None if verification.ok else "integrity_check_failed",
        },
        "keyring": {
            "keyCount": _bounded_count(key_status.get("keyCount")),
            "maximumKeys": _bounded_count(key_status.get("maximumKeys")),
            "secretsPersisted": False,
        },
    }


def _zip_entry(name: str, payload: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    entry.compress_type = zipfile.ZIP_DEFLATED
    entry.external_attr = 0o100644 << 16
    return entry, payload


class ApplianceDiagnosticService:
    def __init__(
        self,
        *,
        audit: ApplianceAudit,
        storage_reader: Callable[[], Mapping[str, Any]],
        service_reader: Callable[[], Mapping[str, Any]] = service_health,
        now: Callable[[], datetime] | None = None,
        command_finder: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._audit = audit
        self._storage_reader = storage_reader
        self._service_reader = service_reader
        self._now = now or (lambda: datetime.now(UTC))
        self._command_finder = command_finder

    def service_status(self) -> dict[str, Any]:
        try:
            return _service_summary(self._service_reader())
        except Exception:  # Keep the read-only status endpoint fail-closed and available.
            return _unavailable_service_summary()

    def build(self) -> DiagnosticBundle:
        created = self._now()
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        created = created.astimezone(UTC)
        created_at = created.isoformat().replace("+00:00", "Z")

        try:
            storage = _storage_summary(self._storage_reader())
        except Exception:  # A broken probe is itself useful diagnostic evidence.
            storage = _unavailable_storage_summary()
        try:
            services = _service_summary(self._service_reader())
        except Exception:  # Keep support export available when systemd is unavailable.
            services = _unavailable_service_summary()

        payloads = {
            "audit-summary.json": _json_bytes(_audit_summary(self._audit)),
            "privacy.json": _json_bytes(
                {
                    "schema": "echo.appliance-diagnostics-privacy.v1",
                    "allowListOnly": True,
                    "rawLogsIncluded": False,
                    "excluded": [
                        "accounts",
                        "configuration-files",
                        "credentials",
                        "device-identifiers",
                        "environment-variables",
                        "host-and-network-identifiers",
                        "mount-and-file-paths",
                        "raw-command-output",
                    ],
                }
            ),
            "service-summary.json": _json_bytes(services),
            "storage-summary.json": _json_bytes(storage),
            "system-summary.json": _json_bytes(_system_summary(self._command_finder)),
        }
        file_records = [
            {
                "name": name,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for name, payload in sorted(payloads.items())
        ]
        identity = "".join(
            f"{item['name']}\0{item['size']}\0{item['sha256']}\n" for item in file_records
        ).encode("utf-8")
        bundle_id = hashlib.sha256(identity).hexdigest()
        manifest = _json_bytes(
            {
                "schema": DIAGNOSTIC_BUNDLE_SCHEMA,
                "createdAt": created_at,
                "bundleId": bundle_id,
                "files": file_records,
            }
        )

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", allowZip64=False) as archive:
            for name, payload in sorted({**payloads, "manifest.json": manifest}.items()):
                entry, content = _zip_entry(name, payload)
                archive.writestr(entry, content)
        bundle = output.getvalue()
        if not bundle or len(bundle) > MAX_BUNDLE_BYTES:
            raise DiagnosticBundleError("diagnostic bundle exceeds the safe size limit")

        filename = f"echo-diagnostics-{created.strftime('%Y%m%dT%H%M%SZ')}.zip"
        return DiagnosticBundle(
            archive=bundle,
            filename=filename,
            bundle_id=bundle_id,
            created_at=created_at,
        )


def create_diagnostics_router(
    service: ApplianceDiagnosticService,
    *,
    audit: ApplianceAudit,
    jwt_secret: str | None = None,
    authenticator: ApplianceAuthenticator | None = None,
) -> APIRouter:
    require_operator = resolve_authenticator(
        jwt_secret=jwt_secret, authenticator=authenticator
    ).operator_dependency()
    router = APIRouter(prefix="/api/appliance/diagnostics", tags=["appliance", "diagnostics"])

    @router.get("/services")
    async def read_service_status(
        _actor: str = Depends(require_operator),
    ) -> dict[str, Any]:
        return await run_in_threadpool(service.service_status)

    @router.get("/bundle")
    async def download_bundle(actor: str = Depends(require_operator)) -> Response:
        try:
            bundle = await run_in_threadpool(service.build)
            audit.record(
                actor=actor,
                action=DIAGNOSTIC_EXPORT_ACTION,
                target=DIAGNOSTIC_EXPORT_TARGET,
                outcome="succeeded",
                metadata={"bundleId": bundle.bundle_id, "bytes": len(bundle.archive)},
            )
        except (OSError, AuditIntegrityError, DiagnosticBundleError) as exc:
            raise HTTPException(status_code=503, detail="diagnostic bundle is unavailable") from exc
        return Response(
            content=bundle.archive,
            media_type="application/zip",
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Cross-Origin-Resource-Policy": "same-origin",
                "Content-Disposition": f'attachment; filename="{bundle.filename}"',
            },
        )

    return router


__all__ = [
    "DIAGNOSTIC_BUNDLE_SCHEMA",
    "DIAGNOSTIC_EXPORT_ACTION",
    "DIAGNOSTIC_EXPORT_TARGET",
    "MAX_BUNDLE_BYTES",
    "ApplianceDiagnosticService",
    "DiagnosticBundle",
    "DiagnosticBundleError",
    "create_diagnostics_router",
]
