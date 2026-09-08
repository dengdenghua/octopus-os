"""Privacy and authorization contracts for the appliance support bundle."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance.audit import ApplianceAudit
from appliance.diagnostics import (
    DIAGNOSTIC_BUNDLE_SCHEMA,
    DIAGNOSTIC_EXPORT_ACTION,
    MAX_BUNDLE_BYTES,
    ApplianceDiagnosticService,
    create_diagnostics_router,
)
from runtime.safety.auth.identity import encode_jwt_hs256

JWT_SECRET = "Diagnostic-Secret_1234567890123456789012345"
CREATED_AT = datetime(2026, 9, 8, 12, 34, 56, tzinfo=UTC)


def _headers(actor: str = "local:admin") -> dict[str, str]:
    token = encode_jwt_hs256({"sub": actor, "iat": 0, "exp": 9_999_999_999}, secret=JWT_SECRET)
    return {"Authorization": f"Bearer {token}"}


def _raw_storage() -> dict:
    return {
        "state": "warning",
        "coverage": "partial",
        "stale": True,
        "checkedAt": "2026-09-08T12:34:55+00:00",
        "pools": 2,
        "arrays": 3,
        "filesystems": 4,
        "devices": 5,
        "activeAlerts": [
            {
                "severity": "warning",
                "code": "filesystem.usage",
                "resource": "/srv/dev-disk-by-uuid/secret-volume",
                "message": "admin-token=do-not-export",
            },
            {
                "severity": "critical",
                "code": "smart.failed",
                "resource": "/dev/disk/by-id/wwn-private",
                "serial": "SERIAL-PRIVATE",
            },
        ],
        "probeEvidence": [
            {"source": "lsblk", "state": "ok", "required": True, "count": 5},
            {
                "source": "smartctl",
                "state": "error",
                "required": True,
                "code": "permission_denied",
                "target": "/dev/sda",
            },
        ],
    }


def _raw_services() -> dict:
    return {
        "available": True,
        "state": "critical",
        "checkedAt": "2026-09-08T12:34:54+00:00",
        "units": [
            {
                "unit": "echo-appliance.service",
                "expected": True,
                "activeState": "active",
                "result": "success",
                "restarts": 2,
            },
            {
                "unit": "nginx.service",
                "expected": True,
                "activeState": "failed",
                "result": "private-result-detail",
                "restarts": 5,
            },
        ],
        "activeAlerts": [
            {
                "severity": "critical",
                "code": "service.restart_storm",
                "resource": "nginx.service",
                "message": "private service failure detail",
            }
        ],
    }


def _service(tmp_path, storage_reader=_raw_storage, service_reader=_raw_services):
    audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=JWT_SECRET)
    service = ApplianceDiagnosticService(
        audit=audit,
        storage_reader=storage_reader,
        service_reader=service_reader,
        now=lambda: CREATED_AT,
        command_finder=lambda command: f"/usr/bin/{command}" if command == "lsblk" else None,
    )
    return service, audit


def _read_archive(payload: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.testzip() is None
        assert all(not info.is_dir() for info in archive.infolist())
        return {name: archive.read(name) for name in archive.namelist()}


def test_bundle_is_bounded_allow_list_with_verified_manifest(tmp_path):
    service, _audit = _service(tmp_path)

    bundle = service.build()
    files = _read_archive(bundle.archive)

    assert bundle.filename == "echo-diagnostics-20260908T123456Z.zip"
    assert 0 < len(bundle.archive) < MAX_BUNDLE_BYTES
    assert set(files) == {
        "audit-summary.json",
        "manifest.json",
        "privacy.json",
        "service-summary.json",
        "storage-summary.json",
        "system-summary.json",
    }
    manifest = json.loads(files["manifest.json"])
    assert manifest["schema"] == DIAGNOSTIC_BUNDLE_SCHEMA
    assert manifest["bundleId"] == bundle.bundle_id
    assert manifest["createdAt"] == "2026-09-08T12:34:56Z"
    assert {item["name"]: (item["size"], item["sha256"]) for item in manifest["files"]} == {
        name: (len(payload), hashlib.sha256(payload).hexdigest())
        for name, payload in files.items()
        if name != "manifest.json"
    }

    storage = json.loads(files["storage-summary.json"])
    assert storage["counts"] == {
        "arrays": 3,
        "devices": 5,
        "filesystems": 4,
        "pools": 2,
    }
    assert storage["alerts"] == {
        "bySeverity": {"critical": 1, "warning": 1},
        "codes": ["filesystem.usage", "smart.failed"],
        "total": 2,
    }
    assert storage["probes"] == {
        "byState": {"error": 1, "ok": 1},
        "failureCodes": ["permission_denied"],
        "required": 2,
        "total": 2,
    }
    system = json.loads(files["system-summary.json"])
    assert system["commandsAvailable"]["lsblk"] is True
    assert system["commandsAvailable"]["smartctl"] is False
    services = json.loads(files["service-summary.json"])
    assert services["state"] == "critical"
    assert services["counts"] == {
        "active": 1,
        "expected": 2,
        "failed": 1,
        "monitored": 2,
        "restarts": 7,
    }
    assert services["alerts"] == {
        "bySeverity": {"critical": 1},
        "codes": ["service.restart_storm"],
        "total": 1,
    }


def test_bundle_never_copies_raw_identifiers_paths_or_secrets(tmp_path):
    service, _audit = _service(tmp_path)

    bundle = service.build()
    unpacked = b"\n".join(_read_archive(bundle.archive).values()).decode("utf-8")

    for forbidden in (
        "secret-volume",
        "admin-token",
        "do-not-export",
        "wwn-private",
        "SERIAL-PRIVATE",
        "/dev/sda",
        "/usr/bin/lsblk",
        "private-result-detail",
        "private service failure detail",
    ):
        assert forbidden not in unpacked
    privacy = json.loads(_read_archive(bundle.archive)["privacy.json"])
    assert privacy["allowListOnly"] is True
    assert privacy["rawLogsIncluded"] is False
    assert "credentials" in privacy["excluded"]
    assert "mount-and-file-paths" in privacy["excluded"]


def test_failed_storage_probe_produces_safe_unknown_summary(tmp_path):
    def unavailable():
        raise RuntimeError("secret at /srv/private should not escape")

    service, _audit = _service(tmp_path, unavailable)

    storage = json.loads(_read_archive(service.build().archive)["storage-summary.json"])

    assert storage["state"] == "unknown"
    assert storage["coverage"] == "none"
    assert storage["probes"]["failureCodes"] == ["collection_failed"]
    assert "secret" not in json.dumps(storage)


def test_service_status_requires_operator_and_returns_only_the_safe_summary(tmp_path):
    service, audit = _service(tmp_path)
    app = FastAPI()
    app.include_router(create_diagnostics_router(service, audit=audit, jwt_secret=JWT_SECRET))
    client = TestClient(app)

    assert client.get("/api/appliance/diagnostics/services").status_code == 401
    assert (
        client.get(
            "/api/appliance/diagnostics/services", headers=_headers("local:member")
        ).status_code
        == 403
    )

    response = client.get("/api/appliance/diagnostics/services", headers=_headers())

    assert response.status_code == 200
    assert response.json()["counts"]["failed"] == 1
    assert response.json()["alerts"]["codes"] == ["service.restart_storm"]
    assert "units" not in response.json()
    assert "private" not in response.text


def test_download_requires_operator_and_records_export(tmp_path):
    service, audit = _service(tmp_path)
    app = FastAPI()
    app.include_router(create_diagnostics_router(service, audit=audit, jwt_secret=JWT_SECRET))
    client = TestClient(app)

    assert client.get("/api/appliance/diagnostics/bundle").status_code == 401
    assert (
        client.get(
            "/api/appliance/diagnostics/bundle", headers=_headers("local:member")
        ).status_code
        == 403
    )

    response = client.get("/api/appliance/diagnostics/bundle", headers=_headers())

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert response.headers["content-disposition"] == (
        'attachment; filename="echo-diagnostics-20260908T123456Z.zip"'
    )
    assert set(_read_archive(response.content)) == {
        "audit-summary.json",
        "manifest.json",
        "privacy.json",
        "service-summary.json",
        "storage-summary.json",
        "system-summary.json",
    }
    event = audit.recent(1)[0]["payload"]
    assert event["actor"] == "local:admin"
    assert event["action"] == DIAGNOSTIC_EXPORT_ACTION
    assert event["target"] == "support-bundle"
    assert event["outcome"] == "succeeded"
    assert event["metadata"]["bytes"] == len(response.content)
