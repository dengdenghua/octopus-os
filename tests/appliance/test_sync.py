"""Managed device backup grants, resumable bytes and keep-both conflicts."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi import HTTPException as FastAPIHTTPException
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from appliance.approval import APPROVAL_HEADER, HighRiskApprovalService, create_approval_router
from appliance.audit import ApplianceAudit
from appliance.device_link import DeviceLinkService
from appliance.files import FileManager
from appliance.files.manager import DEFAULT_UPLOAD_CHUNK_BYTES
from appliance.sync import (
    SYNC_PROTOCOL_VERSION,
    SYNC_VERSION_HEADER,
    DeviceSyncService,
    SyncError,
    create_device_sync_router,
)
from runtime.safety.auth.identity import encode_jwt_hs256

JWT_SECRET = "Device-Sync-Secret_123456789012345678901234"
PASSWORD = "device-sync-admin-password"
PASSWORD_HASH = hashlib.sha256(PASSWORD.encode()).hexdigest()
CONTRACT_PATH = Path(__file__).resolve().parents[2] / "docs/mobile/device-sync-contract.json"


class _Pool:
    def all(self) -> list[object]:
        return []


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

    async def start(self) -> None:
        self.ws_server._server = object()

    async def stop(self) -> None:
        self.ws_server._server = None


class _Photos:
    def __init__(self) -> None:
        self.invalidations = 0

    def invalidate_scan_cache(self) -> None:
        self.invalidations += 1


def _linked(tmp_path, device_id: str = "phone-1"):
    coordinator = _Coordinator()
    link = DeviceLinkService(
        data_dir=tmp_path / "link",
        jwt_secret=JWT_SECRET,
        coordinator_factory=lambda: coordinator,
        lan_ip_resolver=lambda: "192.168.1.8",
    )
    asyncio.run(link.enable())
    invitation = link.create_pairing_invitation()
    token = parse_qs(urlparse(invitation["connectString"]).query)["token"][0]
    assert coordinator.ws_server._check_auth(
        {
            "params": {
                "tentacle_id": device_id,
                "auth_token": token,
                "platform": "android",
            }
        }
    )
    return link, token, coordinator


def _services(tmp_path):
    link, token, coordinator = _linked(tmp_path)
    files = FileManager(tmp_path / "nas", upload_reserve_bytes=0)
    photos = _Photos()
    sync = DeviceSyncService(
        data_dir=tmp_path / "state",
        files=files,
        device_link=link,
        photos=photos,
    )
    return link, token, files, photos, sync, coordinator


def test_machine_readable_contract_matches_server_constants() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert contract["schema"] == "echo.device-sync.v1"
    assert contract["protocolVersion"] == SYNC_PROTOCOL_VERSION
    assert contract["minimumClientProtocolVersion"] == SYNC_PROTOCOL_VERSION
    assert contract["authentication"]["headers"][SYNC_VERSION_HEADER] == str(SYNC_PROTOCOL_VERSION)
    assert contract["capabilities"]["maxChunkBytes"] == DEFAULT_UPLOAD_CHUNK_BYTES
    assert contract["compatibility"]["unsupportedVersionStatus"] == 426


def test_backup_requires_an_explicit_device_scope(tmp_path) -> None:
    _link, _token, _files, _photos, sync, _coordinator = _services(tmp_path)
    digest = hashlib.sha256(b"photo").hexdigest()

    with pytest.raises(SyncError) as denied:
        sync.preflight(
            "phone-1",
            asset_id="asset-1",
            scope="photos",
            path="Camera/photo.jpg",
            size=5,
            sha256=digest,
        )

    assert denied.value.status_code == 403
    status = sync.set_scope("phone-1", "photos", enabled=True)
    assert status["devices"][0]["grants"]["photos"] is True
    assert status["protocolVersion"] == SYNC_PROTOCOL_VERSION
    assert status["capabilities"]["conflictPolicy"] == "keep-both"


def test_resumable_photo_backup_is_idempotent_and_invalidates_library(tmp_path) -> None:
    _link, _token, files, photos, sync, _coordinator = _services(tmp_path)
    sync.set_scope("phone-1", "photos", enabled=True)
    payload = b"verified-photo-bytes"
    digest = hashlib.sha256(payload).hexdigest()

    created = sync.preflight(
        "phone-1",
        asset_id="asset-1",
        scope="photos",
        path="Camera/2026/photo.jpg",
        size=len(payload),
        sha256=digest,
        modified_at=123,
    )
    assert created["decision"] == "upload"
    session_id = created["session"]["sessionId"]
    sync.append_chunk("phone-1", session_id, 0, payload[:8])

    resumed = sync.preflight(
        "phone-1",
        asset_id="asset-1",
        scope="photos",
        path="Camera/2026/photo.jpg",
        size=len(payload),
        sha256=digest,
    )
    assert resumed["decision"] == "resume"
    assert resumed["session"]["uploadedBytes"] == 8

    sync.append_chunk("phone-1", session_id, 8, payload[8:])
    committed = sync.complete("phone-1", session_id)
    assert committed["state"] == "committed"
    assert committed["conflict"] is False
    assert files.file_for_download(committed["target"]).read_bytes() == payload
    assert photos.invalidations == 1

    skipped = sync.preflight(
        "phone-1",
        asset_id="asset-1",
        scope="photos",
        path="Camera/2026/photo.jpg",
        size=len(payload),
        sha256=digest,
    )
    assert skipped["decision"] == "skip"
    assert sync.changes("phone-1")["changes"][0]["sha256"] == digest


def test_changed_asset_keeps_both_versions_instead_of_overwriting(tmp_path) -> None:
    _link, _token, files, _photos, sync, _coordinator = _services(tmp_path)
    sync.set_scope("phone-1", "files", enabled=True)

    targets = []
    for payload in (b"first version", b"second version"):
        digest = hashlib.sha256(payload).hexdigest()
        prepared = sync.preflight(
            "phone-1",
            asset_id="document-1",
            scope="files",
            path="Documents/report.txt",
            size=len(payload),
            sha256=digest,
        )
        session_id = prepared["session"]["sessionId"]
        sync.append_chunk("phone-1", session_id, 0, payload)
        result = sync.complete("phone-1", session_id)
        targets.append(result["target"])

    assert targets[0] != targets[1]
    assert "(conflict " in targets[1]
    assert files.file_for_download(targets[0]).read_bytes() == b"first version"
    assert files.file_for_download(targets[1]).read_bytes() == b"second version"
    assert sync.device_status("phone-1")["summary"]["files"]["conflicts"] == 1


def test_one_paired_device_cannot_resume_another_devices_session(tmp_path) -> None:
    link, _token, _files, _photos, sync, coordinator = _services(tmp_path)
    invitation = link.create_pairing_invitation()
    second_token = parse_qs(urlparse(invitation["connectString"]).query)["token"][0]
    assert coordinator.ws_server._check_auth(
        {"params": {"tentacle_id": "phone-2", "auth_token": second_token}}
    )
    sync.set_scope("phone-1", "files", enabled=True)
    sync.set_scope("phone-2", "files", enabled=True)
    payload = b"private upload"
    prepared = sync.preflight(
        "phone-1",
        asset_id="private-asset",
        scope="files",
        path="private.txt",
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    with pytest.raises(SyncError) as denied:
        sync.upload_status("phone-2", prepared["session"]["sessionId"])

    assert denied.value.status_code == 404
    session_id = prepared["session"]["sessionId"]
    sync.append_chunk("phone-1", session_id, 0, payload)
    sync.complete("phone-1", session_id)
    assert sync.changes("phone-2")["changes"] == []


@pytest.mark.parametrize(
    ("scope", "path"),
    [("files", "../escape.txt"), ("files", "/absolute.txt"), ("photos", "Camera/a.exe")],
)
def test_unsafe_or_non_photo_paths_fail_closed(tmp_path, scope: str, path: str) -> None:
    _link, _token, _files, _photos, sync, _coordinator = _services(tmp_path)
    sync.set_scope("phone-1", scope, enabled=True)
    with pytest.raises(SyncError) as error:
        sync.preflight(
            "phone-1",
            asset_id="asset-1",
            scope=scope,
            path=path,
            size=1,
            sha256=hashlib.sha256(b"x").hexdigest(),
        )
    assert error.value.status_code == 422


def test_device_http_api_uses_pair_credential_and_admin_step_up(tmp_path) -> None:
    link, token, files, photos, sync, _coordinator = _services(tmp_path)
    audit = ApplianceAudit.from_data_dir(tmp_path / "audit", jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=PASSWORD_HASH,
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"s" * 32,
    )
    app = FastAPI()
    app.include_router(create_approval_router(approval, jwt_secret=JWT_SECRET))
    app.include_router(
        create_device_sync_router(
            sync,
            jwt_secret=JWT_SECRET,
            approval=approval,
            audit=audit,
        )
    )
    client = TestClient(app)
    browser_token = encode_jwt_hs256(
        {"sub": "local:admin", "iat": 0, "exp": 9_999_999_999}, secret=JWT_SECRET
    )
    client.cookies.set("echo_session", browser_token)

    denied = client.post("/api/appliance/sync/devices/phone-1/files/enable")
    assert denied.status_code == 403
    issued = client.post(
        "/api/appliance/approvals",
        json={
            "action": "device-sync.files.enable",
            "target": "phone-1",
            "password": PASSWORD,
        },
    )
    enabled = client.post(
        "/api/appliance/sync/devices/phone-1/files/enable",
        headers={APPROVAL_HEADER: issued.json()["approvalToken"]},
    )
    assert enabled.status_code == 200

    anonymous = TestClient(app)
    assert anonymous.get("/api/appliance/device-sync").status_code == 401
    authenticated = {
        "Authorization": f"EchoDevice {token}",
        "X-Echo-Device-ID": "phone-1",
    }
    missing_version = anonymous.get("/api/appliance/device-sync", headers=authenticated)
    assert missing_version.status_code == 426
    assert missing_version.headers[SYNC_VERSION_HEADER] == str(SYNC_PROTOCOL_VERSION)
    unsupported_version = anonymous.get(
        "/api/appliance/device-sync",
        headers={**authenticated, SYNC_VERSION_HEADER: "99"},
    )
    assert unsupported_version.status_code == 426
    headers = {
        **authenticated,
        SYNC_VERSION_HEADER: str(SYNC_PROTOCOL_VERSION),
    }
    device_status = anonymous.get("/api/appliance/device-sync", headers=headers)
    assert device_status.status_code == 200
    assert device_status.headers[SYNC_VERSION_HEADER] == str(SYNC_PROTOCOL_VERSION)
    assert device_status.json()["protocolVersion"] == SYNC_PROTOCOL_VERSION

    payload = b"router bytes"
    prepared = anonymous.post(
        "/api/appliance/device-sync/assets/preflight",
        headers=headers,
        json={
            "assetId": "router-asset",
            "scope": "files",
            "path": "router.txt",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    )
    assert prepared.status_code == 200, prepared.text
    session_id = prepared.json()["session"]["sessionId"]
    chunk = anonymous.put(
        f"/api/appliance/device-sync/upload-sessions/{session_id}/chunk",
        headers={**headers, "X-Echo-Upload-Offset": "0"},
        content=payload,
    )
    assert chunk.status_code == 200, chunk.text
    complete = anonymous.post(
        f"/api/appliance/device-sync/upload-sessions/{session_id}/complete",
        headers=headers,
    )
    assert complete.status_code == 200, complete.text
    assert files.file_for_download(complete.json()["target"]).read_bytes() == payload
    assert photos.invalidations == 0

    asyncio.run(link.revoke_device("phone-1"))
    assert anonymous.get("/api/appliance/device-sync", headers=headers).status_code == 401


def test_upload_disconnect_is_a_quiet_client_closed_response(tmp_path) -> None:
    _link, _token, _files, _photos, sync, _coordinator = _services(tmp_path)
    audit = ApplianceAudit.from_data_dir(tmp_path / "audit", jwt_secret=JWT_SECRET)
    approval = HighRiskApprovalService(
        password_hash=PASSWORD_HASH,
        jwt_secret=JWT_SECRET,
        audit=audit,
        boot_nonce=b"s" * 32,
    )
    router = create_device_sync_router(
        sync,
        jwt_secret=JWT_SECRET,
        approval=approval,
        audit=audit,
    )
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/appliance/device-sync/upload-sessions/{session_id}/chunk"
    )

    class _DisconnectedRequest:
        async def stream(self):
            yield b"complete-chunk-prefix"
            raise ClientDisconnect

    with pytest.raises(FastAPIHTTPException) as error:
        asyncio.run(
            endpoint(
                session_id="0" * 32,
                request=_DisconnectedRequest(),
                offset=0,
                device_id="phone-1",
            )
        )

    assert error.value.status_code == 499
    assert error.value.detail == "upload client disconnected"
