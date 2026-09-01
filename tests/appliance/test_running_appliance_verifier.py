from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "deploy" / "appliance" / "verify-running-appliance.py"
)
_SPEC = importlib.util.spec_from_file_location("echo_running_appliance_verifier", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
verifier = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(verifier)


def _family_fixture() -> dict:
    return {
        "schemaVersion": 1,
        "kind": "echo.family-isolation-acceptance.v1",
        "members": [
            {
                "username": "alice",
                "password": "Alice-physical-Echo-42!",
                "visibleRoots": ["Shared", "Alice"],
                "hiddenRoots": ["Bob"],
                "readableFile": "Alice/allowed.txt",
                "deniedFile": "Bob/denied.txt",
                "readablePhoto": "Alice/allowed.jpg",
                "deniedPhoto": "Bob/denied.jpg",
            },
            {
                "username": "bob",
                "password": "Bob-physical-Echo-84!",
                "visibleRoots": ["Shared", "Bob"],
                "hiddenRoots": ["Alice"],
                "readableFile": "Bob/allowed.txt",
                "deniedFile": "Alice/denied.txt",
                "readablePhoto": "Bob/allowed.jpg",
                "deniedPhoto": "Alice/denied.jpg",
            },
        ],
    }


def test_family_isolation_fixture_drives_two_real_identity_projections(
    tmp_path,
    monkeypatch,
) -> None:
    fixture = tmp_path / "family.json"
    fixture.write_text(json.dumps(_family_fixture()))
    fixture.chmod(0o400)

    def fake_http(method, url, *, payload=None, token=None, **_kwargs):
        path = urlsplit(url).path
        query = parse_qs(urlsplit(url).query)
        if path == "/api/auth/local/login":
            username = payload["username"]
            return (
                200,
                json.dumps(
                    {
                        "success": True,
                        "access_token": f"token-{username}",
                        "actor_id": f"local:{username}",
                    }
                ).encode(),
                {},
            )
        username = str(token).removeprefix("token-")
        if path == "/api/appliance/accounts":
            return (
                200,
                json.dumps(
                    {
                        "canManage": False,
                        "accounts": [{"username": username}],
                    }
                ).encode(),
                {},
            )
        if path == "/api/appliance/files/list":
            roots = ["Shared", username.title()]
            return 200, json.dumps({"entries": [{"name": item} for item in roots]}).encode(), {}
        if path == "/api/appliance/accounts/status/plan":
            return 403, b'{"detail":"administrator required"}', {}
        requested = query["path"][0]
        allowed = requested.startswith(f"{username.title()}/")
        return (200 if allowed else 403), b"probe", {}

    monkeypatch.setattr(verifier, "_http", fake_http)
    result = verifier._assert_family_isolation_contract(
        "http://127.0.0.1:8000",
        fixture_path=str(fixture),
    )

    assert result == {
        "verified": True,
        "memberCount": 2,
        "identitySetSha256": hashlib.sha256(b"alice\nbob").hexdigest(),
        "policySetSha256": result["policySetSha256"],
        "accountDirectoryIsolated": True,
        "fileProjectionVerified": True,
        "photoProjectionVerified": True,
        "memberManagementRejected": True,
        "secretsReturned": False,
    }
    serialized = json.dumps(result)
    assert "Alice-physical" not in serialized
    assert "Bob-physical" not in serialized


def test_family_isolation_fixture_rejects_public_or_duplicate_secret_files(tmp_path) -> None:
    fixture = tmp_path / "family.json"
    fixture.write_text(json.dumps(_family_fixture()))
    fixture.chmod(0o644)
    with pytest.raises(verifier.VerificationError, match="permissions are unsafe"):
        verifier._read_family_isolation_fixture(str(fixture))

    fixture.write_text(
        '{"schemaVersion":1,"schemaVersion":1,"kind":"echo.family-isolation-acceptance.v1","members":[]}'
    )
    fixture.chmod(0o400)
    with pytest.raises(verifier.VerificationError, match="strict JSON"):
        verifier._read_family_isolation_fixture(str(fixture))


def _status(*, uid: int = 1000, gid: int = 1000) -> dict[str, list[str]]:
    return {
        "Uid": [str(uid)] * 4,
        "Gid": [str(gid)] * 4,
        "CapEff": ["0000000000000000"],
        "NoNewPrivs": ["1"],
    }


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://nas.invalid/config",
        "http://user:password@nas.invalid/api/health",
        "http://nas.invalid:/api/health",
        "http://nas.invalid/api/health#fragment",
    ],
)
def test_http_probe_rejects_every_non_origin_url(url) -> None:
    with pytest.raises(verifier.VerificationError, match="invalid HTTP verification URL"):
        verifier._http("GET", url)


def test_http_probe_uses_only_the_validated_http_target(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class _Response:
        status = 401

        @staticmethod
        def read() -> bytes:
            return b'{"detail":"unauthorized"}'

        @staticmethod
        def getheaders() -> list[tuple[str, str]]:
            return [("Content-Type", "application/json")]

    class _Connection:
        def __init__(self, host, port, timeout):
            observed["connection"] = (host, port, timeout)

        def request(self, method, target, *, body, headers):
            observed["request"] = (method, target, body, headers)

        @staticmethod
        def getresponse():
            return _Response()

        @staticmethod
        def close() -> None:
            observed["closed"] = True

    monkeypatch.setattr(verifier.http.client, "HTTPConnection", _Connection)

    status, body, headers = verifier._http(
        "POST",
        "http://127.0.0.1:8000/api/auth/local/login?mode=local",
        payload={"username": "admin"},
        token="test-token",
        timeout=7,
    )

    assert (status, body, headers) == (
        401,
        b'{"detail":"unauthorized"}',
        {"Content-Type": "application/json"},
    )
    assert observed["connection"] == ("127.0.0.1", 8000, 7)
    assert observed["request"] == (
        "POST",
        "/api/auth/local/login?mode=local",
        b'{"username": "admin"}',
        {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "Bearer test-token",
        },
    )
    assert observed["closed"] is True


def test_stream_probe_hashes_incrementally_without_buffering_response(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class _Response:
        status = 206

        def __init__(self):
            self.chunks = [b"echo", b" nas", b""]

        def read(self, size: int) -> bytes:
            observed.setdefault("readSizes", []).append(size)
            return self.chunks.pop(0)

        @staticmethod
        def getheaders() -> list[tuple[str, str]]:
            return [("Content-Range", "bytes 4-11/12")]

    class _Connection:
        def __init__(self, host, port, timeout):
            observed["connection"] = (host, port, timeout)

        def request(self, method, target, *, body, headers):
            observed["request"] = (method, target, body, headers)

        @staticmethod
        def getresponse():
            return _Response()

        @staticmethod
        def close() -> None:
            observed["closed"] = True

    monkeypatch.setattr(verifier.http.client, "HTTPConnection", _Connection)

    result = verifier._http_stream_sha256(
        "http://127.0.0.1:8000/api/appliance/files/download?path=report.bin",
        token="browser-token",
        extra_headers={"Range": "bytes=4-"},
        timeout=90,
    )

    assert result == (
        206,
        8,
        hashlib.sha256(b"echo nas").hexdigest(),
        {"Content-Range": "bytes 4-11/12"},
    )
    assert observed["connection"] == ("127.0.0.1", 8000, 90)
    assert observed["request"] == (
        "GET",
        "/api/appliance/files/download?path=report.bin",
        None,
        {
            "Accept": "application/octet-stream",
            "Authorization": "Bearer browser-token",
            "Range": "bytes=4-",
        },
    )
    assert observed["readSizes"] == [1024 * 1024] * 3
    assert observed["closed"] is True


def test_photo_contract_is_read_only_path_safe_and_agent_backed(monkeypatch) -> None:
    plan_id = "a" * 64

    def response(status: int, payload: object, headers=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return status, body, headers or {"Content-Type": "application/json"}

    def http(method, url, **kwargs):
        if url.endswith("/library?limit=1") and not kwargs.get("token"):
            return response(401, {"detail": "unauthorized"})
        if url.endswith("/library?limit=5"):
            return response(
                200,
                {
                    "schema": "echo.photos.library.v1",
                    "total": 1,
                    "items": [{"path": "family/summer.jpg", "name": "summer.jpg"}],
                },
            )
        if url.endswith("/status"):
            return response(
                200,
                {
                    "schema": "echo.photos.status.v1",
                    "index": {"backendAvailable": True, "maxFiles": 4000},
                },
            )
        if url.endswith("/plans/index") and method == "POST":
            assert kwargs["payload"] == {"includeFaces": False}
            return response(
                200,
                {
                    "schema": "echo.photos.index-plan.v1",
                    "planId": plan_id,
                    "approvalAction": "photos.index.build",
                    "approvalTarget": plan_id,
                    "requiresApproval": True,
                    "ready": True,
                },
            )
        if "path=../outside.jpg" in url:
            return response(400, {"detail": "invalid photo path"})
        if "/thumbnail?" in url:
            return response(
                200,
                b"RIFFxxxxWEBP",
                {"Content-Type": "image/webp", "ETag": '"digest"'},
            )
        raise AssertionError((method, url, kwargs))

    monkeypatch.setattr(verifier, "_http", http)

    result = verifier._assert_photos_contract("http://127.0.0.1:8000", "token")

    assert result == {
        "library": 1,
        "listed": 1,
        "thumbnailVerified": True,
        "agentIndexBackend": True,
        "indexReady": True,
        "indexPlanId": plan_id,
        "writeExecuted": False,
    }


def test_photo_contract_rejects_an_absolute_library_path(monkeypatch) -> None:
    def http(method, url, **kwargs):
        if url.endswith("/library?limit=1"):
            return 401, b"{}", {}
        if url.endswith("/library?limit=5"):
            return (
                200,
                json.dumps(
                    {
                        "schema": "echo.photos.library.v1",
                        "total": 1,
                        "items": [{"path": "/data/nas/private.jpg"}],
                    }
                ).encode(),
                {},
            )
        raise AssertionError((method, url, kwargs))

    monkeypatch.setattr(verifier, "_http", http)

    with pytest.raises(verifier.VerificationError, match="unsafe or absolute path"):
        verifier._assert_photos_contract("http://127.0.0.1:8000", "token")


def _storage_usage_payload() -> dict:
    return {
        "schema": "echo.storage.usage.v1",
        "readOnly": True,
        "generatedAt": 1,
        "disk": {
            "totalBytes": 100,
            "usedBytes": 40,
            "freeBytes": 60,
            "reserveBytes": 10,
            "availableForUploadsBytes": 48,
            "usedPercent": 40.0,
        },
        "library": {
            "logicalBytes": 30,
            "files": 3,
            "directories": 2,
            "scannedEntries": 5,
            "maxEntries": 200_000,
            "truncated": False,
            "skippedLinks": 1,
        },
        "categories": [
            {"id": "photos", "bytes": 20, "files": 2},
            {"id": "videos", "bytes": 10, "files": 1},
            {"id": "audio", "bytes": 0, "files": 0},
            {"id": "documents", "bytes": 0, "files": 0},
            {"id": "archives", "bytes": 0, "files": 0},
            {"id": "other", "bytes": 0, "files": 0},
        ],
        "topFolders": [{"name": "Family", "bytes": 30, "files": 3}],
        "trash": {"bytes": 4, "files": 1},
        "uploads": {"reservedBytes": 2, "active": 1},
        "quotas": [],
    }


def test_storage_usage_contract_is_authenticated_bounded_and_read_only(monkeypatch) -> None:
    observed: list[tuple[str, str, str | None]] = []

    def http(method, url, **kwargs):
        observed.append((method, url, kwargs.get("token")))
        if url.endswith("/usage"):
            return 401, b"{}", {}
        if url.endswith("/usage?fresh=true"):
            return 200, json.dumps(_storage_usage_payload()).encode(), {}
        raise AssertionError((method, url, kwargs))

    monkeypatch.setattr(verifier, "_http", http)

    result = verifier._assert_storage_usage_contract("http://127.0.0.1:8000", "browser-token")

    assert result == {
        "diskUsedPercent": 40.0,
        "libraryBytes": 30,
        "files": 3,
        "scanBounded": True,
        "writeExecuted": False,
    }
    assert observed == [
        ("GET", "http://127.0.0.1:8000/api/appliance/files/usage", None),
        (
            "GET",
            "http://127.0.0.1:8000/api/appliance/files/usage?fresh=true",
            "browser-token",
        ),
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["topFolders"].__setitem__(
                0, {"name": "../outside", "bytes": 30, "files": 3}
            ),
            "unsafe top-level folder",
        ),
        (
            lambda payload: payload["library"].__setitem__("logicalBytes", 31),
            "logical byte total",
        ),
        (
            lambda payload: payload.__setitem__("root", "/data/nas"),
            "host filesystem field",
        ),
    ],
)
def test_storage_usage_contract_rejects_unsafe_or_unbound_projection(
    monkeypatch,
    mutate,
    message,
) -> None:
    payload = _storage_usage_payload()
    mutate(payload)

    def http(_method, url, **_kwargs):
        if url.endswith("/usage"):
            return 401, b"{}", {}
        return 200, json.dumps(payload).encode(), {}

    monkeypatch.setattr(verifier, "_http", http)

    with pytest.raises(verifier.VerificationError, match=message):
        verifier._assert_storage_usage_contract("http://127.0.0.1:8000", "token")


def _device_link_payload() -> dict:
    return {
        "schema": "echo.device-link.v1",
        "enabled": True,
        "listenerActive": True,
        "mode": "echo-managed",
        "scope": "lan",
        "wsPort": 8765,
        "canManageListener": True,
        "canPair": True,
        "pairedDeviceCount": 1,
        "onlineDeviceCount": 1,
        "devices": [
            {
                "id": "phone-1",
                "online": True,
                "individuallyRevocable": True,
            }
        ],
        "startupError": "",
        "transport": {
            "protocol": "websocket",
            "encrypted": False,
            "authenticated": True,
        },
        "remoteAccess": {
            "schema": "echo.remote-access.v1",
            "provider": "none",
            "available": False,
            "mode": "not-configured",
            "configured": False,
            "state": "not-configured",
            "scope": "none",
            "endpoint": None,
            "lastCheckedAt": None,
            "transport": {
                "protocol": "none",
                "encrypted": False,
                "tailnetOnly": False,
            },
            "features": {
                "desktopWeb": False,
                "deviceLink": False,
                "fileSync": False,
                "photoSync": False,
            },
            "reason": "private network or relay is not configured",
        },
    }


def test_device_link_contract_is_authenticated_and_never_reveals_pairing_secret(
    monkeypatch,
) -> None:
    observed: list[str | None] = []

    def http(_method, _url, **kwargs):
        observed.append(kwargs.get("token"))
        if not kwargs.get("token"):
            return 401, b"{}", {}
        return 200, json.dumps(_device_link_payload()).encode(), {}

    monkeypatch.setattr(verifier, "_http", http)

    result = verifier._assert_device_link_contract("http://127.0.0.1:8000", "browser-token")

    assert result == {
        "mode": "echo-managed",
        "enabled": True,
        "listenerActive": True,
        "pairedDevices": 1,
        "onlineDevices": 1,
        "remoteAccess": False,
        "remoteProvider": "none",
        "writeExecuted": False,
    }
    assert observed == [None, "browser-token"]


def test_device_link_contract_accepts_tailscale_web_without_remote_tentacle(
    monkeypatch,
) -> None:
    payload = _device_link_payload()
    payload["remoteAccess"] = {
        "schema": "echo.remote-access.v1",
        "provider": "tailscale",
        "available": True,
        "mode": "sidecar",
        "configured": True,
        "state": "connected",
        "scope": "private-network",
        "endpoint": "https://echo-os.example.ts.net",
        "lastCheckedAt": 2_000,
        "transport": {
            "protocol": "wireguard+https",
            "encrypted": True,
            "tailnetOnly": True,
        },
        "features": {
            "desktopWeb": True,
            "deviceLink": False,
            "fileSync": False,
            "photoSync": False,
        },
        "reason": "connected",
    }

    def http(_method, _url, **kwargs):
        if not kwargs.get("token"):
            return 401, b"{}", {}
        return 200, json.dumps(payload).encode(), {}

    monkeypatch.setattr(verifier, "_http", http)

    result = verifier._assert_device_link_contract("http://127.0.0.1:8000", "browser-token")

    assert result["remoteAccess"] is True
    assert result["remoteProvider"] == "tailscale"
    assert payload["transport"]["encrypted"] is False


def test_device_sync_contract_is_authenticated_read_only_and_credential_free(
    monkeypatch,
) -> None:
    payload = {
        "schema": "echo.device-sync.v1",
        "available": True,
        "mode": "echo-managed",
        "conflictPolicy": "keep-both",
        "roots": {
            "photos": "Mobile Uploads/<device>/Photos",
            "files": "Mobile Uploads/<device>/Files",
        },
        "devices": [
            {
                "id": "phone-1",
                "name": "Echo Pocket",
                "online": False,
                "grants": {"photos": True, "files": False},
                "summary": {
                    "photos": {
                        "committed": 2,
                        "uploading": 1,
                        "conflicts": 1,
                        "bytes": 42,
                    },
                    "files": {
                        "committed": 0,
                        "uploading": 0,
                        "conflicts": 0,
                        "bytes": 0,
                    },
                },
            }
        ],
    }

    def http(_method, _url, **kwargs):
        if not kwargs.get("token"):
            return 401, b"{}", {}
        return 200, json.dumps(payload).encode(), {}

    monkeypatch.setattr(verifier, "_http", http)

    result = verifier._assert_device_sync_contract("http://127.0.0.1:8000", "browser-token")

    assert result == {
        "available": True,
        "mode": "echo-managed",
        "devices": 1,
        "conflictPolicy": "keep-both",
        "writeExecuted": False,
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload.__setitem__("pairingToken", "secret"),
            "credential-shaped field",
        ),
        (
            lambda payload: payload["devices"][0].__setitem__("id", "../phone"),
            "device is invalid",
        ),
        (
            lambda payload: (
                payload["remoteAccess"].__setitem__("available", True),
                payload["remoteAccess"]["transport"].__setitem__("encrypted", False),
            ),
            "without a safe endpoint",
        ),
    ],
)
def test_device_link_contract_rejects_unsafe_claims(monkeypatch, mutate, message) -> None:
    payload = _device_link_payload()
    mutate(payload)

    def http(_method, _url, **kwargs):
        if not kwargs.get("token"):
            return 401, b"{}", {}
        return 200, json.dumps(payload).encode(), {}

    monkeypatch.setattr(verifier, "_http", http)

    with pytest.raises(verifier.VerificationError, match=message):
        verifier._assert_device_link_contract("http://127.0.0.1:8000", "token")


def test_nas_transfer_restart_uses_fixed_docker_args_then_health_gate(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def run(args, **kwargs):
        observed["run"] = (args, kwargs)
        return SimpleNamespace(stdout="echo-os\n")

    def wait(base_url, wait_seconds):
        observed["wait"] = (base_url, wait_seconds)
        return {"agent_bundle": {"verified": True}}

    monkeypatch.setattr(verifier.subprocess, "run", run)
    monkeypatch.setattr(verifier, "_wait_for_bundle", wait)

    verifier._restart_main_for_nas_transfer(
        "echo-os",
        base_url="http://127.0.0.1:8000",
        wait_seconds=45,
    )

    assert observed["run"] == (
        ["docker", "restart", "echo-os"],
        {
            "check": True,
            "text": True,
            "capture_output": True,
            "timeout": 45,
        },
    )
    assert observed["wait"] == ("http://127.0.0.1:8000", 45)


def _nas_transfer_http():
    state: dict[str, object] = {
        "sessions": {},
        "completed": set(),
        "trash": [],
        "requests": [],
    }

    def response(status: int, payload: object):
        return status, json.dumps(payload).encode(), {"Content-Type": "application/json"}

    def http(method, url, **kwargs):
        state["requests"].append((method, url, kwargs))
        endpoint = "/api" + url.split("/api", 1)[1]
        sessions = state["sessions"]
        if method == "POST" and endpoint == "/api/appliance/files/upload/preflight":
            payload = kwargs["payload"]
            target = "/".join(
                part for part in (payload["path"].strip("/"), payload["filename"]) if part
            )
            return response(
                200,
                {
                    "target": target,
                    "expectedBytes": payload["size"],
                    "availableBytes": 10_000,
                    "reserveBytes": 512,
                },
            )
        if method == "POST" and endpoint == "/api/appliance/files/upload/sessions":
            payload = kwargs["payload"]
            session_id = f"{len(sessions) + 1:032x}"
            target = "/".join(
                part for part in (payload["path"].strip("/"), payload["filename"]) if part
            )
            sessions[session_id] = {
                "id": session_id,
                "target": target,
                "size": payload["size"],
                "uploaded": 0,
                "sha256": payload["sha256"],
                "digest": hashlib.sha256(),
            }
            return response(
                200,
                {
                    "ok": True,
                    "sessionId": session_id,
                    "target": target,
                    "expectedBytes": payload["size"],
                    "uploadedBytes": 0,
                    "chunkBytes": verifier._NAS_TRANSFER_CHUNK_BYTES,
                    "sha256Expected": True,
                },
            )
        match = verifier.re.search(
            r"/upload/sessions/([0-9a-f]{32})(?:/(chunk|complete))?$", endpoint
        )
        if match:
            session_id, operation = match.groups()
            session = sessions.get(session_id)
            if method == "GET" and operation is None:
                if session is None:
                    return response(404, {"detail": "not found"})
                return response(200, {"uploadedBytes": session["uploaded"]})
            if method == "DELETE" and operation is None:
                if session is None:
                    return response(404, {"detail": "not found"})
                sessions.pop(session_id)
                return response(200, {"cancelled": True, "sessionId": session_id})
            if method == "PUT" and operation == "chunk":
                assert session is not None
                raw = kwargs["raw_body"]
                headers = kwargs["extra_headers"]
                offset = int(headers["Upload-Offset"])
                if offset != session["uploaded"]:
                    return response(
                        409,
                        {"detail": {"uploadedBytes": session["uploaded"]}},
                    )
                assert hashlib.sha256(raw).hexdigest() == headers["Upload-Chunk-SHA256"]
                session["digest"].update(raw)
                session["uploaded"] += len(raw)
                return response(200, {"uploadedBytes": session["uploaded"]})
            if method == "POST" and operation == "complete":
                assert session is not None
                if (
                    session["uploaded"] != session["size"]
                    or session["digest"].hexdigest() != session["sha256"]
                ):
                    return response(422, {"detail": "hash mismatch"})
                sessions.pop(session_id)
                state["completed"].add(session["target"])
                return response(
                    200,
                    {
                        "sha256": session["sha256"],
                        "hashVerified": True,
                        "entry": {
                            "path": session["target"],
                            "size": session["size"],
                        },
                    },
                )
        if method == "POST" and endpoint == "/api/appliance/files/trash":
            target = kwargs["payload"]["path"]
            if target not in state["completed"]:
                return response(404, {"detail": "not found"})
            state["completed"].remove(target)
            trash_id = f"{len(state['trash']) + 1:032x}"
            item = {
                "id": trash_id,
                "original": target,
                "name": target.rsplit("/", 1)[-1],
            }
            state["trash"].append(item)
            return response(200, {"trashed": item})
        if method == "GET" and endpoint == "/api/appliance/files/trash":
            return response(200, {"entries": state["trash"]})
        if method == "POST" and endpoint == "/api/appliance/files/trash/restore":
            trash_id = kwargs["payload"]["id"]
            matching = [item for item in state["trash"] if item["id"] == trash_id]
            if len(matching) != 1:
                return response(404, {"detail": "not found"})
            item = matching[0]
            state["trash"].remove(item)
            state["completed"].add(item["original"])
            return response(
                200,
                {
                    "ok": True,
                    "entry": {
                        "path": item["original"],
                        "size": next(iter(sessions.values()), {"size": 16})["size"],
                    },
                },
            )
        if method == "GET" and endpoint.startswith("/api/appliance/files/list?"):
            entries = [
                {"name": target.rsplit("/", 1)[-1], "path": target} for target in state["completed"]
            ]
            return response(200, {"entries": entries})
        raise AssertionError((method, endpoint, kwargs))

    return state, http


def test_nas_large_transfer_preview_is_zero_write_and_exactly_bound(monkeypatch) -> None:
    monkeypatch.setattr(
        verifier,
        "_http",
        lambda *_args, **_kwargs: pytest.fail("preview must not make an HTTP request"),
    )

    preview = verifier._assert_nas_large_transfer(
        "http://127.0.0.1:8000",
        token="browser-token",
        directory="verification",
        size=verifier._MIN_NAS_TRANSFER_TEST_BYTES,
        confirmation=None,
        require_write=False,
    )

    assert preview["writeExecuted"] is False
    assert preview["confirmationRequired"] == (
        f"VERIFY ECHO NAS TRANSFER {verifier._MIN_NAS_TRANSFER_TEST_BYTES} verification "
        "ON http://127.0.0.1:8000"
    )


def test_nas_large_transfer_rejects_bad_confirmation_before_http(monkeypatch) -> None:
    monkeypatch.setattr(
        verifier,
        "_http",
        lambda *_args, **_kwargs: pytest.fail("bad confirmation must not make an HTTP request"),
    )

    with pytest.raises(verifier.VerificationError, match="confirmation does not match"):
        verifier._assert_nas_large_transfer(
            "http://127.0.0.1:8000",
            token="browser-token",
            directory="verification",
            size=verifier._MIN_NAS_TRANSFER_TEST_BYTES,
            confirmation="VERIFY ECHO NAS TRANSFER 1 verification ON http://127.0.0.1:8000",
            require_write=True,
        )


def test_nas_transfer_restart_is_previewed_and_requires_bound_callback(monkeypatch) -> None:
    monkeypatch.setattr(
        verifier,
        "_http",
        lambda *_args, **_kwargs: pytest.fail("restart preview must remain zero-write"),
    )
    size = verifier._MIN_NAS_TRANSFER_TEST_BYTES
    confirmation = (
        f"VERIFY ECHO NAS TRANSFER {size} ROOT ON http://127.0.0.1:8000 AND RESTART echo-os"
    )

    preview = verifier._assert_nas_large_transfer(
        "http://127.0.0.1:8000",
        token="browser-token",
        directory="",
        size=size,
        confirmation=None,
        require_write=False,
        restart_container="echo-os",
    )

    assert preview["confirmationRequired"] == confirmation
    assert preview["restartMain"] == "echo-os"
    with pytest.raises(verifier.VerificationError, match="without a restart callback"):
        verifier._assert_nas_large_transfer(
            "http://127.0.0.1:8000",
            token="browser-token",
            directory="",
            size=size,
            confirmation=confirmation,
            require_write=True,
            restart_container="echo-os",
        )


def test_nas_large_transfer_verifies_resume_range_cancel_and_recoverable_cleanup(
    monkeypatch,
) -> None:
    monkeypatch.setattr(verifier, "_MIN_NAS_TRANSFER_TEST_BYTES", 16)
    monkeypatch.setattr(verifier, "_NAS_TRANSFER_CHUNK_BYTES", 8)
    state, http = _nas_transfer_http()
    monkeypatch.setattr(verifier, "_http", http)
    stream_ranges: list[str] = []
    restart_calls: list[str] = []

    def stream(_url, *, token, extra_headers=None, timeout):
        assert token == "browser-token"
        assert timeout > 0
        range_header = (extra_headers or {}).get("Range", "")
        stream_ranges.append(range_header)
        if not range_header:
            return (
                200,
                16,
                verifier._nas_transfer_sha256(0, 16),
                {"Content-Length": "16"},
            )
        start = int(range_header.removeprefix("bytes=").removesuffix("-"))
        return (
            206,
            16 - start,
            verifier._nas_transfer_sha256(start, 16 - start),
            {"Content-Range": f"bytes {start}-15/16"},
        )

    monkeypatch.setattr(verifier, "_http_stream_sha256", stream)

    result = verifier._assert_nas_large_transfer(
        "http://127.0.0.1:8000",
        token="browser-token",
        directory="verification",
        size=16,
        confirmation=(
            "VERIFY ECHO NAS TRANSFER 16 verification ON http://127.0.0.1:8000 AND RESTART echo-os"
        ),
        require_write=True,
        restart_container="echo-os",
        restart_callback=lambda: restart_calls.append("echo-os"),
    )

    assert result["writeExecuted"] is True
    assert result["offsetRecovery"] == 8
    assert result["restartVerified"] is True
    assert result["fullDownload"] == 16
    assert result["rangeBytes"] == 16 - result["rangeStart"]
    assert result["cancelVerified"] is True
    assert result["recycleRestoreVerified"] is True
    assert result["restoredSha256"] == result["sha256"]
    assert result["physicallyDeleted"] is False
    assert stream_ranges == ["", "bytes=0-", ""]
    assert restart_calls == ["echo-os"]
    assert state["sessions"] == {}
    assert state["completed"] == set()
    assert len(state["trash"]) == 1


def test_nas_large_transfer_failure_still_moves_committed_probe_to_trash(monkeypatch) -> None:
    monkeypatch.setattr(verifier, "_MIN_NAS_TRANSFER_TEST_BYTES", 16)
    monkeypatch.setattr(verifier, "_NAS_TRANSFER_CHUNK_BYTES", 8)
    state, http = _nas_transfer_http()
    monkeypatch.setattr(verifier, "_http", http)
    monkeypatch.setattr(
        verifier,
        "_http_stream_sha256",
        lambda *_args, **_kwargs: (200, 16, "0" * 64, {"Content-Length": "16"}),
    )

    with pytest.raises(verifier.VerificationError, match="full download"):
        verifier._assert_nas_large_transfer(
            "http://127.0.0.1:8000",
            token="browser-token",
            directory="verification",
            size=16,
            confirmation=("VERIFY ECHO NAS TRANSFER 16 verification ON http://127.0.0.1:8000"),
            require_write=True,
        )

    assert state["sessions"] == {}
    assert state["completed"] == set()
    assert len(state["trash"]) == 1


@pytest.mark.parametrize(
    ("method", "target"),
    [
        ("PUT", "/health"),
        ("GET", "health"),
        ("GET", "/health\r\nX-Injected: true"),
        ("GET", "/health\nX-Injected: true"),
    ],
)
def test_unix_http_probe_rejects_untrusted_request_parts(method, target) -> None:
    with pytest.raises(verifier.VerificationError, match="invalid Unix socket"):
        verifier._unix_http_status("/run/echo-omv/omv.sock", method, target)


def test_session_cookie_contract_is_httponly_lax_and_host_only() -> None:
    cookie = verifier._assert_session_cookie(
        {
            "Set-Cookie": (
                "echo_session=token; HttpOnly; Max-Age=2592000; Path=/; SameSite=lax; Secure"
            )
        },
        require_secure=True,
    )
    assert cookie == "echo_session=token"


def test_session_cookie_contract_accepts_the_previous_agent_cookie_name() -> None:
    cookie = verifier._assert_session_cookie(
        {"Set-Cookie": ("echo_session=token; HttpOnly; Max-Age=2592000; Path=/; SameSite=lax")},
        require_secure=False,
    )

    assert cookie == "echo_session=token"


@pytest.mark.parametrize(
    ("cookie", "require_secure"),
    [
        ("echo_session=token; Path=/; SameSite=lax", False),
        ("echo_session=token; HttpOnly; Path=/; SameSite=lax; Domain=example", False),
        ("echo_session=token; HttpOnly; Path=/; SameSite=lax", True),
    ],
)
def test_session_cookie_contract_fails_closed(cookie, require_secure) -> None:
    with pytest.raises(verifier.VerificationError):
        verifier._assert_session_cookie(
            {"set-cookie": cookie},
            require_secure=require_secure,
        )


def test_browser_boundary_probes_origin_and_host_guards(monkeypatch) -> None:
    observed: list[tuple[str, str, dict[str, object]]] = []

    def _http(method, url, **kwargs):
        observed.append((method, url, kwargs))
        headers = kwargs.get("extra_headers") or {}
        if not headers:
            return (
                200,
                b"{}",
                {
                    "Content-Security-Policy": (
                        "default-src 'self'; object-src 'none'; "
                        "frame-ancestors 'self'; "
                        "script-src 'self' 'wasm-unsafe-eval'; "
                        "frame-src 'self' https://approved.home"
                    ),
                    "X-Frame-Options": "SAMEORIGIN",
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer",
                },
            )
        if "Origin" in headers:
            return 403, b"", {}
        if "Host" in headers:
            return 400, b"", {}
        raise AssertionError("unexpected probe")

    monkeypatch.setattr(verifier, "_http", _http)

    verifier._assert_browser_boundary("http://127.0.0.1:8000", "bearer-token")

    assert observed == [
        (
            "GET",
            "http://127.0.0.1:8000/api/appliance/config",
            {},
        ),
        (
            "POST",
            f"http://127.0.0.1:8000/api/appliance/apps/{'a' * 12}/start",
            {
                "token": "bearer-token",
                "extra_headers": {"Origin": "https://attacker.invalid"},
            },
        ),
        (
            "GET",
            "http://127.0.0.1:8000/api/appliance/config",
            {"extra_headers": {"Host": "rebind.attacker.invalid"}},
        ),
    ]


def test_login_rate_limit_probe_requires_429_and_retry_after(monkeypatch) -> None:
    statuses = iter([401, 401, 401, 401, 429])
    observed_users: list[str] = []

    def _http(_method, _url, **kwargs):
        observed_users.append(kwargs["payload"]["username"])
        status = next(statuses)
        headers = {"retry-after": "60"} if status == 429 else {}
        return status, b"", headers

    monkeypatch.setattr(verifier, "_http", _http)
    monkeypatch.setattr(verifier.secrets, "token_hex", lambda _length: "abcdef123456")

    verifier._assert_login_rate_limit("http://127.0.0.1:8000")

    assert observed_users == ["rate-probe-abcdef123456"] * 5


def test_web_surface_probe_requires_one_echo_frontend(monkeypatch) -> None:
    responses = iter(
        [
            (200, b"<!doctype html><title>Echo OS</title>", {}),
            (200, b'<svg aria-label="Echo"></svg>', {}),
            (
                200,
                json.dumps({"agent_ui_base": None, "agent_workspace_url": None}).encode(),
                {},
            ),
        ]
    )
    observed: list[tuple[str, str]] = []

    def _http(method, url, **_kwargs):
        observed.append((method, url))
        return next(responses)

    monkeypatch.setattr(verifier, "_http", _http)

    verifier._assert_web_surfaces("http://127.0.0.1:8000")

    assert observed == [
        ("GET", "http://127.0.0.1:8000/"),
        ("GET", "http://127.0.0.1:8000/images/echo.svg"),
        ("GET", "http://127.0.0.1:8000/api/appliance/config"),
    ]


def test_agent_asset_probe_requires_auth_and_public_bounded_fields(monkeypatch) -> None:
    payload = {
        "schema": "echo.agent-assets.v6",
        "available": True,
        "plugins": [
            {
                "id": "documents",
                "plugin": "documents",
                "kind": "workbench",
                "name_zh": "文档助手",
                "description": "创建与整理文档",
                "release_summary": "1.1.0：新增受信版本说明。",
                "permissions": ["content.read"],
                "authModes": ["oauth"],
                "dependencies": [],
                "runtimeDependencies": [],
                "connectors": ["documents-app"],
            }
        ],
        "skills": [{"name": "photo-organizer", "author": "Echo"}],
        "installed": {"plugins": ["documents"], "skills": []},
        "pluginStates": [
            {
                "id": "documents",
                "catalogId": "documents",
                "kind": "workbench",
                "source": "cloud",
                "state": "update_available",
                "installed": True,
                "enabled": True,
                "rollbackAvailable": True,
                "recoveryCount": 1,
                "trustLevel": "publisher",
                "integrityVerified": True,
                "publisherVerified": True,
                "publisher": "Echo Publisher",
                "compatibility": "compatible",
                "hostApi": ">=0.2,<0.3",
                "releaseSummary": "1.1.0：新增受信版本说明。",
                "version": "1.0.0",
                "availableVersion": "1.1.0",
                "permissions": ["content.read"],
                "permissionsGranted": ["content.read"],
                "permissionReviewRequired": False,
                "permissionActive": True,
                "authModes": ["oauth"],
                "dependencies": [],
                "runtimeDependencies": [],
                "connectors": ["documents-app"],
            }
        ],
        "unavailableSources": [],
    }
    responses = iter(
        [
            (401, b'{"detail":"unauthorized"}', {}),
            (200, json.dumps(payload, ensure_ascii=False).encode(), {}),
        ]
    )
    observed: list[dict] = []

    def http(method, url, **kwargs):
        observed.append({"method": method, "url": url, **kwargs})
        return next(responses)

    monkeypatch.setattr(verifier, "_http", http)

    result = verifier._assert_agent_assets_contract("http://127.0.0.1:8000", "token")

    assert result == {
        "available": True,
        "plugins": 1,
        "skills": 1,
        "installed": 1,
        "workbenches": 1,
        "updates": 1,
        "attention": 0,
        "publisherVerified": 1,
        "unverifiedInstalled": 0,
        "incompatible": 0,
        "privateFieldsExposed": False,
    }
    assert observed[0].get("token") is None
    assert observed[1]["token"] == "token"


def test_agent_asset_probe_rejects_unknown_private_fields(monkeypatch) -> None:
    payload = {
        "schema": "echo.agent-assets.v6",
        "available": True,
        "plugins": [
            {
                "id": "documents",
                "plugin": "documents",
                "privateDatabasePath": "/data/agent.sqlite",
            }
        ],
        "skills": [],
        "installed": {"plugins": [], "skills": []},
        "pluginStates": [],
        "unavailableSources": [],
    }
    responses = iter(
        [
            (401, b"{}", {}),
            (200, json.dumps(payload).encode(), {}),
        ]
    )
    monkeypatch.setattr(verifier, "_http", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(verifier.VerificationError, match="unsafe fields"):
        verifier._assert_agent_assets_contract("http://127.0.0.1:8000", "token")


def test_agent_asset_probe_rejects_private_lifecycle_fields(monkeypatch) -> None:
    payload = {
        "schema": "echo.agent-assets.v6",
        "available": True,
        "plugins": [
            {
                "id": "documents",
                "plugin": "documents",
                "kind": "workbench",
                "name_zh": "文档助手",
                "permissions": [],
                "authModes": [],
                "dependencies": [],
                "runtimeDependencies": [],
                "connectors": [],
            }
        ],
        "skills": [],
        "installed": {"plugins": ["documents"], "skills": []},
        "pluginStates": [
            {
                "id": "documents",
                "catalogId": "documents",
                "kind": "workbench",
                "source": "cloud",
                "state": "enabled",
                "installed": True,
                "enabled": True,
                "rollbackAvailable": False,
                "recoveryCount": 0,
                "trustLevel": "local_integrity",
                "integrityVerified": True,
                "publisherVerified": False,
                "compatibility": "compatible",
                "hostApi": ">=0.2,<0.3",
                "version": "1.0.0",
                "path": "/data/agent/plugins/documents",
                "permissions": [],
                "permissionsGranted": [],
                "permissionReviewRequired": False,
                "permissionActive": True,
                "authModes": [],
                "dependencies": [],
                "runtimeDependencies": [],
                "connectors": [],
            }
        ],
        "unavailableSources": [],
    }
    responses = iter(
        [
            (401, b"{}", {}),
            (200, json.dumps(payload, ensure_ascii=False).encode(), {}),
        ]
    )
    monkeypatch.setattr(verifier, "_http", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(verifier.VerificationError, match="lifecycle exposed unsafe fields"):
        verifier._assert_agent_assets_contract("http://127.0.0.1:8000", "token")


@pytest.mark.parametrize(
    ("service_status", "service_detail", "available"),
    [
        (
            404,
            {"code": "CAPABILITY_NOT_FOUND", "message": "capability was not found"},
            True,
        ),
        (
            503,
            {
                "code": "AGENT_CAPABILITY_UNAVAILABLE",
                "message": "Agent capability service is unavailable",
            },
            False,
        ),
    ],
)
def test_agent_capability_probe_is_read_only_bounded_and_step_up_protected(
    monkeypatch,
    service_status,
    service_detail,
    available,
) -> None:
    responses = iter(
        [
            (401, b'{"detail":"authentication required"}', {}),
            (service_status, json.dumps({"detail": service_detail}).encode(), {}),
            (403, b'{"detail":"approval required"}', {}),
            (422, b'{"detail":"invalid permission"}', {}),
            (422, b'{"detail":"invalid credential"}', {}),
        ]
    )
    observed: list[dict] = []

    def http(method, url, **kwargs):
        observed.append({"method": method, "url": url, **kwargs})
        return next(responses)

    monkeypatch.setattr(verifier, "_http", http)

    result = verifier._assert_agent_capabilities_contract(
        "http://127.0.0.1:8000",
        "browser-token",
    )

    assert result == {
        "available": available,
        "authenticationRequired": True,
        "stepUpRequired": True,
        "permissionAllowlist": True,
        "credentialBounds": True,
        "boundedErrors": True,
        "writeExecuted": False,
    }
    assert observed[0].get("token") is None
    assert all(call.get("token") == "browser-token" for call in observed[1:])
    assert [call["method"] for call in observed] == ["GET", "GET", "POST", "POST", "POST"]


def _hub_detail_payload() -> dict:
    return {
        "schema": "echo.hub.app-detail.v1",
        "catalogDigest": "a" * 64,
        "architecture": "amd64",
        "runtime": {"available": True, "error": None},
        "appRuntime": {
            "schema": "echo.hub.runtime.v1",
            "status": "not-installed",
            "summary": {
                "serviceCount": 0,
                "runningServices": 0,
                "healthyServices": 0,
                "restartCount": 0,
                "cpuPercent": None,
                "memoryUsageBytes": None,
                "memoryLimitBytes": None,
                "pids": None,
            },
            "services": [],
        },
        "diagnostics": {
            "schema": "echo.hub.diagnostics.v1",
            "status": "not-installed",
            "incidents": [],
        },
        "app": {"id": "jellyfin", "package": {"runtime": {"memoryMiB": 3072}}},
        "resourcePreflight": {
            "schema": "echo.hub.resource-preflight.v1",
            "readyForInstall": True,
            "blockingIssues": [],
            "checks": [
                {"id": "architecture", "status": "pass", "blocking": True},
                {"id": "docker-runtime", "status": "pass", "blocking": True},
                {"id": "docker-storage", "status": "pass", "blocking": True},
                {"id": "ports", "status": "pass", "blocking": True},
                {"id": "providers", "status": "pass", "blocking": True},
                {"id": "nas-capacity", "status": "observed", "blocking": False},
            ],
            "runtime": {
                "serviceCount": 1,
                "memoryLimitMiB": 3072,
                "pidsLimit": 512,
                "shmLimitMiB": 256,
                "healthcheckedServices": 0,
            },
            "network": {
                "mode": "bridge",
                "ports": [
                    {
                        "container": 8096,
                        "host": 8096,
                        "protocol": "tcp",
                        "status": "available",
                    }
                ],
                "requiredProviders": [],
                "providersReady": True,
            },
            "storage": {
                "appDataVolumes": 2,
                "nasVolumes": 1,
                "nasAccess": "read-only",
                "snapshotVolumes": 2,
                "nasCapacity": {
                    "status": "observed",
                    "totalBytes": 2 * 1024**4,
                    "freeBytes": 1024**4,
                    "usedPercent": 50.0,
                },
                "imageStorage": {
                    "status": "sufficient",
                    "downloadBytes": 689739878,
                    "blobCount": 11,
                    "requiredFreeBytes": 2069219634,
                    "reservePolicy": "compressed-times-three-or-plus-512MiB",
                    "capacity": {
                        "schema": "echo.hub.docker-storage.v1",
                        "status": "observed",
                        "totalBytes": 128 * 1024**3,
                        "freeBytes": 64 * 1024**3,
                        "usedPercent": 50.0,
                    },
                },
            },
            "notices": ["NAS_READ_ONLY"],
        },
    }


def test_hub_preflight_probe_requires_auth_real_capacity_and_no_fake_estimate(
    monkeypatch,
) -> None:
    payload = _hub_detail_payload()
    responses = iter(
        [
            (401, b'{"detail":"unauthorized"}', {}),
            (200, json.dumps(payload).encode(), {}),
        ]
    )
    observed: list[str | None] = []

    def http(_method, _url, **kwargs):
        observed.append(kwargs.get("token"))
        return next(responses)

    monkeypatch.setattr(verifier, "_http", http)

    result = verifier._assert_hub_resource_preflight("http://127.0.0.1:8000", "browser-token")

    assert result == {
        "appId": "jellyfin",
        "memoryLimitMiB": 3072,
        "port": 8096,
        "portStatus": "available",
        "nasCapacityObserved": True,
        "dockerCapacityObserved": True,
        "imageDownloadBytes": 689739878,
        "imageStorageSufficient": True,
        "runtimeStatus": "not-installed",
        "runtimeSecretFieldsExposed": False,
        "diagnosticsStatus": "not-installed",
        "diagnosticIncidentCount": 0,
        "diagnosticsSecretFieldsExposed": False,
    }
    assert observed == [None, "browser-token"]


def test_hub_preflight_probe_rejects_image_bytes_not_bound_to_catalog_digest(monkeypatch) -> None:
    payload = _hub_detail_payload()
    payload["resourcePreflight"]["storage"]["imageStorage"]["downloadBytes"] = 123
    responses = iter([(401, b"{}", {}), (200, json.dumps(payload).encode(), {})])
    monkeypatch.setattr(verifier, "_http", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(verifier.VerificationError, match="Docker capacity preflight"):
        verifier._assert_hub_resource_preflight("http://127.0.0.1:8000", "token")


def test_hub_preflight_probe_rejects_runtime_fields_that_could_carry_logs(monkeypatch) -> None:
    payload = _hub_detail_payload()
    payload["appRuntime"]["logs"] = "PASSWORD=must-not-cross-boundary"
    responses = iter([(401, b"{}", {}), (200, json.dumps(payload).encode(), {})])
    monkeypatch.setattr(verifier, "_http", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(verifier.VerificationError, match="runtime health"):
        verifier._assert_hub_resource_preflight("http://127.0.0.1:8000", "token")


def test_hub_preflight_probe_rejects_diagnostic_text_or_raw_logs(monkeypatch) -> None:
    payload = _hub_detail_payload()
    payload["diagnostics"] = {
        "schema": "echo.hub.diagnostics.v1",
        "status": "attention",
        "incidents": [
            {
                "code": "CRASHED",
                "severity": "error",
                "serviceId": "app",
                "recovery": "restart",
                "rawLog": "PASSWORD=must-not-cross-boundary",
            }
        ],
    }
    responses = iter([(401, b"{}", {}), (200, json.dumps(payload).encode(), {})])
    monkeypatch.setattr(verifier, "_http", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(verifier.VerificationError, match="diagnostics contract"):
        verifier._assert_hub_resource_preflight("http://127.0.0.1:8000", "token")


def test_hub_operations_probe_requires_auth_and_excludes_one_time_secrets(monkeypatch) -> None:
    operation = {
        "schema": "echo.hub.operation.v1",
        "operationId": "1" * 32,
        "operation": "install",
        "appId": "jellyfin",
        "planId": "a" * 64,
        "catalogDigest": "b" * 64,
        "status": "succeeded",
        "createdAt": "2026-08-29T00:00:00.000Z",
        "updatedAt": "2026-08-29T00:01:00.000Z",
        "startedAt": "2026-08-29T00:00:01.000Z",
        "finishedAt": "2026-08-29T00:01:00.000Z",
        "error": None,
        "warning": None,
        "progress": {
            "schema": "echo.hub.progress.v1",
            "stage": "completed",
            "step": "finished",
            "completed": None,
            "total": None,
            "unit": None,
            "item": None,
            "items": None,
            "sequence": 8,
        },
        "credentialsAvailable": True,
        "result": {"schema": "echo.hub.install-result.v1", "appId": "jellyfin"},
    }
    responses = iter(
        [
            (401, b"{}", {}),
            (
                200,
                json.dumps(
                    {
                        "schema": "echo.hub.operations.v1",
                        "operations": [operation],
                        "total": 1,
                    }
                ).encode(),
                {},
            ),
        ]
    )
    observed: list[str | None] = []

    def http(_method, _url, **kwargs):
        observed.append(kwargs.get("token"))
        return next(responses)

    monkeypatch.setattr(verifier, "_http", http)

    assert verifier._assert_hub_operations_contract("http://127.0.0.1:8000", "browser-token") == {
        "authenticated": True,
        "operations": 1,
        "oneTimeCredentialsExcluded": True,
    }
    assert observed == [None, "browser-token"]


def test_hub_operations_probe_rejects_revealed_secrets(monkeypatch) -> None:
    operation = {
        "schema": "echo.hub.operation.v1",
        "operationId": "1" * 32,
        "operation": "install",
        "appId": "jellyfin",
        "planId": "a" * 64,
        "catalogDigest": "b" * 64,
        "status": "succeeded",
        "createdAt": "now",
        "updatedAt": "now",
        "startedAt": "now",
        "finishedAt": "now",
        "error": None,
        "warning": None,
        "progress": {
            "schema": "echo.hub.progress.v1",
            "stage": "completed",
            "step": "finished",
            "completed": None,
            "total": None,
            "unit": None,
            "item": None,
            "items": None,
            "sequence": 8,
        },
        "credentialsAvailable": True,
        "result": {"revealedSecrets": {"admin-password": "unsafe"}},
    }
    responses = iter(
        [
            (401, b"{}", {}),
            (
                200,
                json.dumps(
                    {
                        "schema": "echo.hub.operations.v1",
                        "operations": [operation],
                        "total": 1,
                    }
                ).encode(),
                {},
            ),
        ]
    )
    monkeypatch.setattr(verifier, "_http", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(verifier.VerificationError, match="entry is unsafe"):
        verifier._assert_hub_operations_contract("http://127.0.0.1:8000", "token")


def test_high_risk_probe_requires_step_up_replay_protection_and_audit(monkeypatch) -> None:
    target = "a" * 64
    approval_token = "one-shot.signature"
    events = {
        "verification": {"ok": True},
        "events": [
            {
                "kind": "appliance_action",
                "payload": {
                    "actor": "local:admin",
                    "action": "approval",
                    "target": f"app.stop:{target}",
                    "outcome": outcome,
                },
            }
            for outcome in ("denied", "issued", "consumed", "denied")
        ]
        + [
            {
                "kind": "appliance_action",
                "payload": {
                    "actor": "local:admin",
                    "action": "app.stop",
                    "target": target,
                    "outcome": outcome,
                },
            }
            for outcome in ("attempted", "failed")
        ],
    }
    responses = iter(
        [
            (403, b"{}", {}),
            (200, json.dumps({"approvalToken": approval_token}).encode(), {}),
            (403, b"{}", {}),
            (403, b"{}", {}),
            (200, b'{"ok":true,"entriesChecked":6}', {}),
            (200, json.dumps(events).encode(), {}),
        ]
    )
    observed: list[tuple[str, str, dict[str, object]]] = []

    def _http(method, url, **kwargs):
        observed.append((method, url, kwargs))
        return next(responses)

    monkeypatch.setattr(verifier, "_http", _http)

    result = verifier._assert_high_risk_approval(
        "http://127.0.0.1:8000",
        token="browser-token",
        password="device-password",
        protected_container_id=target,
    )

    assert result == {
        "approval": 200,
        "protected_stop": 403,
        "approval_replay": 403,
        "audit_verify": 200,
    }
    assert observed[1][2]["payload"] == {
        "action": "app.stop",
        "target": target,
        "password": "device-password",
    }
    assert observed[2][2]["extra_headers"] == {
        "X-Echo-Approval": approval_token,
    }


def test_process_status_reads_container_pid_one(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def _run(args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return SimpleNamespace(
            stdout=(
                "Name:\tpython\n"
                "Uid:\t1000\t1000\t1000\t1000\n"
                "Gid:\t1000\t1000\t1000\t1000\n"
                "CapEff:\t0000000000000000\n"
                "NoNewPrivs:\t1\n"
            )
        )

    monkeypatch.setattr(verifier.subprocess, "run", _run)

    fields = verifier._process_status("echo-main")

    assert observed["args"] == [
        "docker",
        "exec",
        "echo-main",
        "cat",
        "/proc/1/status",
    ]
    assert observed["kwargs"] == {
        "check": True,
        "text": True,
        "capture_output": True,
    }
    assert fields["Uid"] == ["1000"] * 4
    assert fields["CapEff"] == ["0000000000000000"]
    assert fields["NoNewPrivs"] == ["1"]


@pytest.mark.parametrize(
    ("host", "container", "expected"),
    [
        ("x86_64", "x86_64\n", "amd64"),
        ("arm64", "aarch64\n", "arm64"),
    ],
)
def test_runtime_architecture_normalizes_x86_and_arm(
    host,
    container,
    expected,
    monkeypatch,
) -> None:
    monkeypatch.setattr(verifier.platform, "machine", lambda: host)
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=container),
    )

    assert verifier._assert_runtime_architecture("echo-main", expected) == expected


def test_expected_identity_requires_zero_capabilities_and_no_new_privileges(
    monkeypatch,
) -> None:
    monkeypatch.setattr(verifier, "_process_status", lambda _container: _status())

    verifier._assert_runtime_identity("echo-main", 1000, 1000)
    verifier._assert_nonroot_runtime_identity("echo-proxy")


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("CapEff", ["0000000000000001"], "retained effective Linux capabilities"),
        ("NoNewPrivs", ["0"], "does not have NoNewPrivs enabled"),
        ("CapEff", [], "incomplete /proc/1/status"),
    ],
)
def test_privilege_drop_verification_fails_closed(field, value, match) -> None:
    fields = _status()
    fields[field] = value

    with pytest.raises(verifier.VerificationError, match=match):
        verifier._assert_permanent_privilege_drop("echo-main", fields)


def test_nonroot_proxy_check_rejects_any_root_identity(monkeypatch) -> None:
    fields = _status()
    fields["Uid"] = ["991", "991", "991", "0"]
    monkeypatch.setattr(verifier, "_process_status", lambda _container: fields)

    with pytest.raises(verifier.VerificationError, match="still runs as root"):
        verifier._assert_nonroot_runtime_identity("echo-proxy")


def test_omv_real_device_probe_checks_socket_mount_auth_and_redaction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    socket_path = tmp_path / "omv.sock"
    monkeypatch.setattr(
        verifier.os,
        "lstat",
        lambda _path: SimpleNamespace(
            st_mode=verifier.stat.S_IFSOCK | 0o660,
            st_gid=os.getgid(),
        ),
    )
    monkeypatch.setattr(
        verifier,
        "_unix_http_status",
        lambda _path, method, _target: 200 if method == "GET" else 405,
    )

    share_uuid = "11111111-2222-4333-8444-555555555555"

    def _http(method, url, **kwargs):
        token = kwargs.get("token")
        if token is None:
            return 401, b'{"detail":"unauthorized"}', {}
        if url.endswith("/status"):
            payload = {
                "configured": True,
                "available": True,
                "readOnly": False,
                "adminUrl": "https://nas.example.test",
                "capabilities": [
                    "shared-folder.create.simple.v1",
                    "shared-folder.privilege.simple.v1",
                    "smb.share.desired.v1",
                    "nfs.share.private-network.v1",
                    "filesystem.quota.user-group.v1",
                ],
            }
            return 200, json.dumps(payload).encode(), {}
        if url.endswith("/health"):
            payload = {
                "schemaVersion": 1,
                "state": "healthy",
                "stale": False,
                "checkedAt": "2026-08-26T01:00:00Z",
                "lastSuccessfulAt": "2026-08-26T01:00:00Z",
                "intervalSeconds": 300,
                "persistenceHealthy": True,
                "monitoring": True,
                "activeAlerts": [],
                "events": [],
                "summary": {"critical": 0, "warning": 0, "total": 0},
                "readOnly": True,
            }
            return 200, json.dumps(payload).encode(), {}
        if url.endswith("/filesystems"):
            payload = {"filesystems": [], "readOnly": True}
            return 200, json.dumps(payload).encode(), {}
        if url.endswith("/smart/devices"):
            payload = {"devices": [{"devicefile": "/dev/sda"}], "readOnly": True}
            return 200, json.dumps(payload).encode(), {}
        if url.endswith("/topology"):
            payload = {
                "devices": [{"devicefile": "/dev/sda", "type": "disk"}],
                "arrays": [],
                "readOnly": True,
            }
            return 200, json.dumps(payload).encode(), {}
        if url.endswith("/sharing") and method == "GET":
            payload = {
                "sharedFolders": [{"uuid": share_uuid, "name": "Family"}],
                "sharedFolderTargets": [],
                "users": [{"name": "alice"}],
                "groups": [],
                "smb": {"enabled": True, "shares": []},
                "nfs": {"enabled": False, "shares": []},
                "readOnly": True,
            }
            return 200, json.dumps(payload).encode(), {}
        if url.endswith(f"/{share_uuid}/privileges"):
            payload = {
                "privileges": [{"type": "user", "name": "alice", "permission": "readWrite"}],
                "readOnly": True,
            }
            return 200, json.dumps(payload).encode(), {}
        if "devicefile=" in url or "not-a-uuid" in url:
            return 422, b"{}", {}
        if url.endswith("/sharing") and method == "POST":
            return 405, b"{}", {}
        if url.endswith("/sharing/folders/plan") and method == "POST":
            return 422, b"{}", {}
        if url.endswith("/sharing/privileges/plan") and method == "POST":
            return 422, b"{}", {}
        if url.endswith("/sharing/smb/plan") and method == "POST":
            return 422, b"{}", {}
        if url.endswith("/quota/plan") and method == "POST":
            return 422, b"{}", {}
        raise AssertionError((method, url, kwargs))

    monkeypatch.setattr(verifier, "_http", _http)
    main = {"Mounts": [{"Destination": "/run/echo-omv", "RW": False}]}
    proxy = {"Mounts": []}
    result = verifier._assert_omv_integration(
        "http://127.0.0.1:8000",
        token="browser-token",
        main=main,
        proxy=proxy,
        socket_path=str(socket_path),
        expected_gid=os.getgid(),
    )

    assert result == {
        "available": True,
        "socket_mode": "0660",
        "socket_gid": os.getgid(),
        "bridge_post": 405,
        "api_post": 405,
        "shared_folder_control": True,
        "share_privilege_control": True,
        "smb_control": True,
        "quota_control": True,
        "physical_devices": 1,
        "topology_devices": 1,
        "shared_folders": 1,
        "health_state": "healthy",
        "active_alerts": 0,
        "alert_history_events": 0,
        "alert_state_persistent": True,
        "sensitive_fields_exposed": False,
    }


def _reversible_smb_http(*, fail_first_restore: bool = False):
    folder_uuid = "11111111-2222-4333-8444-555555555555"
    original = {
        "schema": "echo.omv.smb-share-desired.v1",
        "sharedFolderRef": folder_uuid,
        "enabled": True,
        "readOnly": False,
        "browseable": True,
        "recycleBin": False,
        "comment": "Original family comment",
    }
    state = {
        "current": dict(original),
        "approvals": {},
        "events": [],
        "applyComments": [],
        "restoreFailures": 0,
    }

    def plan(desired):
        fields = ("enabled", "readOnly", "browseable", "recycleBin", "comment")
        changes = [
            {
                "field": field,
                "before": state["current"][field],
                "after": desired[field],
            }
            for field in fields
            if state["current"][field] != desired[field]
        ]
        digest = hashlib.sha256(
            json.dumps(
                {"current": state["current"], "desired": desired},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return {
            "schema": "echo.omv.smb-share-plan.v1",
            "planId": digest,
            "baseRevision": "b" * 64,
            "operation": "update" if changes else "none",
            "requiresApproval": bool(changes),
            "shareUuid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "sharedFolder": {"uuid": folder_uuid, "name": "Family", "status": "OK"},
            "desired": desired,
            "changes": changes,
            "safety": {
                "guestAccess": "disabled",
                "advancedOptions": "notManaged",
                "acl": "notManaged",
            },
        }

    def response(status, payload):
        return status, json.dumps(payload).encode(), {}

    def http(method, url, **kwargs):
        payload = kwargs.get("payload") or {}
        if method == "GET" and url.endswith("/api/appliance/omv/sharing"):
            current = state["current"]
            return response(
                200,
                {
                    "sharedFolders": [{"uuid": folder_uuid, "name": "Family"}],
                    "sharedFolderTargets": [],
                    "smb": {
                        "enabled": True,
                        "shares": [
                            {
                                "uuid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                                "sharedFolderRef": folder_uuid,
                                "sharedFolderName": "Family",
                                "enabled": current["enabled"],
                                "readOnly": current["readOnly"],
                                "browseable": current["browseable"],
                                "recycleBin": current["recycleBin"],
                                "guest": "no",
                                "comment": current["comment"],
                            }
                        ],
                    },
                },
            )
        if method == "POST" and url.endswith("/api/appliance/omv/sharing/smb/plan"):
            return response(200, plan(payload))
        if method == "POST" and url.endswith("/api/appliance/approvals"):
            approval_token = f"approval-{len(state['approvals']) + 1}"
            state["approvals"][approval_token] = (
                payload["target"],
                payload["intentId"],
            )
            return response(
                200,
                {
                    "approvalToken": approval_token,
                    "target": payload["target"],
                },
            )
        if method == "POST" and url.endswith("/api/appliance/omv/sharing/smb/apply"):
            desired = payload["desired"]
            expected_plan = plan(desired)
            headers = kwargs.get("extra_headers") or {}
            binding = state["approvals"].pop(headers.get("X-Echo-Approval", ""), None)
            if payload["planId"] != expected_plan["planId"] or binding != (
                payload["planId"],
                headers.get("X-Echo-Intent"),
            ):
                return response(403, {"detail": "approval rejected"})
            if (
                fail_first_restore
                and desired["comment"] == original["comment"]
                and state["restoreFailures"] == 0
            ):
                state["restoreFailures"] += 1
                return response(503, {"detail": "simulated restore failure"})
            state["current"] = dict(desired)
            state["applyComments"].append(desired["comment"])
            for outcome in ("attempted", "succeeded"):
                state["events"].append(
                    {
                        "kind": "appliance_action",
                        "payload": {
                            "action": "omv.smb.apply",
                            "outcome": outcome,
                            "metadata": {"intentId": headers["X-Echo-Intent"]},
                        },
                    }
                )
            return response(
                200,
                {**expected_plan, "applied": True, "verified": True},
            )
        if method == "GET" and "/api/appliance/audit/events" in url:
            return response(
                200,
                {
                    "verification": {"ok": True},
                    "events": state["events"],
                },
            )
        raise AssertionError((method, url, kwargs))

    return folder_uuid, original, state, http


def test_omv_smb_write_preview_is_read_only_and_emits_exact_confirmation(
    monkeypatch,
) -> None:
    folder_uuid, original, state, http = _reversible_smb_http()
    monkeypatch.setattr(verifier, "_http", http)

    result = verifier._assert_omv_smb_reversible_write(
        "http://127.0.0.1:8000",
        token="browser-token",
        password="device-password",
        folder_uuid=folder_uuid,
        confirmation=None,
        require_write=False,
    )

    assert result["writeExecuted"] is False
    assert result["changeFields"] == ["comment"]
    assert result["confirmationRequired"] == f"VERIFY ECHO OMV SMB WRITE {folder_uuid}"
    assert state["current"] == original
    assert state["applyComments"] == []


def test_omv_smb_write_applies_a_comment_only_probe_and_restores_exactly(
    monkeypatch,
) -> None:
    folder_uuid, original, state, http = _reversible_smb_http()
    monkeypatch.setattr(verifier, "_http", http)

    result = verifier._assert_omv_smb_reversible_write(
        "http://127.0.0.1:8000",
        token="browser-token",
        password="device-password",
        folder_uuid=folder_uuid,
        confirmation=f"VERIFY ECHO OMV SMB WRITE {folder_uuid}",
        require_write=True,
    )

    assert result["writeExecuted"] is True
    assert result["restored"] is True
    assert result["auditVerified"] is True
    assert state["current"] == original
    assert len(state["applyComments"]) == 2
    assert state["applyComments"][0].startswith("Echo reversible verification ")
    assert state["applyComments"][1] == original["comment"]


def test_omv_smb_write_uses_emergency_restore_but_still_reports_failure(
    monkeypatch,
) -> None:
    folder_uuid, original, state, http = _reversible_smb_http(fail_first_restore=True)
    monkeypatch.setattr(verifier, "_http", http)

    with pytest.raises(verifier.VerificationError, match="OMV SMB apply failed"):
        verifier._assert_omv_smb_reversible_write(
            "http://127.0.0.1:8000",
            token="browser-token",
            password="device-password",
            folder_uuid=folder_uuid,
            confirmation=f"VERIFY ECHO OMV SMB WRITE {folder_uuid}",
            require_write=True,
        )

    assert state["restoreFailures"] == 1
    assert state["current"] == original
    assert state["applyComments"][-1] == original["comment"]


def test_omv_smb_write_rejects_mismatched_confirmation_before_any_request(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        verifier,
        "_http",
        lambda *_args, **_kwargs: pytest.fail("HTTP must not run for bad confirmation"),
    )

    with pytest.raises(verifier.VerificationError, match="confirmation does not match"):
        verifier._assert_omv_smb_reversible_write(
            "http://127.0.0.1:8000",
            token="browser-token",
            password="device-password",
            folder_uuid="11111111-2222-4333-8444-555555555555",
            confirmation=("VERIFY ECHO OMV SMB WRITE 99999999-2222-4333-8444-555555555555"),
            require_write=True,
        )


def test_omv_smb_write_requires_both_confirmation_and_require_flag(monkeypatch) -> None:
    monkeypatch.setattr(
        verifier,
        "_http",
        lambda *_args, **_kwargs: pytest.fail("HTTP must not run without both write keys"),
    )
    folder_uuid = "11111111-2222-4333-8444-555555555555"

    with pytest.raises(verifier.VerificationError, match="also requires"):
        verifier._assert_omv_smb_reversible_write(
            "http://127.0.0.1:8000",
            token="browser-token",
            password="device-password",
            folder_uuid=folder_uuid,
            confirmation=f"VERIFY ECHO OMV SMB WRITE {folder_uuid}",
            require_write=False,
        )


def _reversible_quota_http(*, fail_first_restore: bool = False):
    filesystem_uuid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    subject_type = "user"
    subject_name = "echoverify"
    original_limit = 8 * 1024**3
    state = {
        "currentLimit": original_limit,
        "approvals": {},
        "events": [],
        "appliedLimits": [],
        "restoreFailures": 0,
        "requests": [],
    }

    def plan(desired):
        current_limit = state["currentLimit"]
        changed = current_limit != desired["hardLimitBytes"]
        digest = hashlib.sha256(
            json.dumps(
                {"currentLimit": current_limit, "desired": desired},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return {
            "schema": "echo.omv.filesystem-quota-plan.v1",
            "planId": digest,
            "baseRevision": "b" * 64,
            "operation": "update" if changed else "none",
            "requiresApproval": changed,
            "filesystem": {
                "uuid": filesystem_uuid,
                "label": "verification-volume",
                "type": "ext4",
                "readOnly": False,
                "supportsQuota": True,
            },
            "subject": {
                "type": subject_type,
                "name": subject_name,
                "hardLimitBytes": current_limit,
                "used": "1048576",
            },
            "desired": desired,
            "changes": (
                [
                    {
                        "field": "hardLimitBytes",
                        "before": current_limit,
                        "after": desired["hardLimitBytes"],
                    }
                ]
                if changed
                else []
            ),
            "safety": {
                "scope": "filesystemUserOrGroup",
                "protocolCoverage": ["local", "SMB", "NFS"],
                "sharedFolderQuota": "notSupportedByOmvQuotaRpc",
                "minimumUnitBytes": 1024,
            },
        }

    def response(status, payload):
        return status, json.dumps(payload).encode(), {}

    def http(method, url, **kwargs):
        payload = kwargs.get("payload") or {}
        state["requests"].append((method, url, payload))
        if method == "POST" and url.endswith("/api/appliance/omv/quota/plan"):
            return response(200, plan(payload))
        if method == "POST" and url.endswith("/api/appliance/approvals"):
            if payload.get("action") != "omv.quota.apply":
                return response(422, {"detail": "wrong action"})
            approval_token = f"quota-approval-{len(state['approvals']) + 1}"
            state["approvals"][approval_token] = (
                payload["target"],
                payload["intentId"],
            )
            return response(
                200,
                {
                    "approvalToken": approval_token,
                    "target": payload["target"],
                },
            )
        if method == "POST" and url.endswith("/api/appliance/omv/quota/apply"):
            desired = payload["desired"]
            expected_plan = plan(desired)
            headers = kwargs.get("extra_headers") or {}
            binding = state["approvals"].pop(headers.get("X-Echo-Approval", ""), None)
            if payload["planId"] != expected_plan["planId"] or binding != (
                payload["planId"],
                headers.get("X-Echo-Intent"),
            ):
                return response(403, {"detail": "approval rejected"})
            if (
                fail_first_restore
                and desired["hardLimitBytes"] == original_limit
                and state["restoreFailures"] == 0
            ):
                state["restoreFailures"] += 1
                return response(503, {"detail": "simulated quota restore failure"})
            state["currentLimit"] = desired["hardLimitBytes"]
            state["appliedLimits"].append(desired["hardLimitBytes"])
            for outcome in ("attempted", "succeeded"):
                state["events"].append(
                    {
                        "kind": "appliance_action",
                        "payload": {
                            "action": "omv.quota.apply",
                            "outcome": outcome,
                            "metadata": {"intentId": headers["X-Echo-Intent"]},
                        },
                    }
                )
            return response(200, {**expected_plan, "applied": True, "verified": True})
        if method == "GET" and "/api/appliance/audit/events" in url:
            return response(
                200,
                {
                    "verification": {"ok": True},
                    "events": state["events"],
                },
            )
        raise AssertionError((method, url, kwargs))

    return filesystem_uuid, subject_type, subject_name, original_limit, state, http


def test_omv_quota_write_preview_is_read_only_and_emits_bound_confirmation(
    monkeypatch,
) -> None:
    filesystem_uuid, subject_type, subject_name, original_limit, state, http = (
        _reversible_quota_http()
    )
    monkeypatch.setattr(verifier, "_http", http)
    probe_limit = 4 * 1024**3

    result = verifier._assert_omv_quota_reversible_write(
        "http://127.0.0.1:8000",
        token="browser-token",
        password="device-password",
        filesystem_uuid=filesystem_uuid,
        subject_type=subject_type,
        subject_name=subject_name,
        probe_limit_bytes=probe_limit,
        confirmation=None,
        require_write=False,
    )

    assert result["writeExecuted"] is False
    assert result["originalHardLimitBytes"] == original_limit
    assert result["probeHardLimitBytes"] == probe_limit
    assert result["confirmationRequired"] == (
        f"VERIFY ECHO OMV QUOTA WRITE {filesystem_uuid} user {subject_name} "
        f"FROM {original_limit} TO {probe_limit}"
    )
    assert state["currentLimit"] == original_limit
    assert state["appliedLimits"] == []


def test_omv_quota_write_applies_stricter_probe_and_restores_exactly(monkeypatch) -> None:
    filesystem_uuid, subject_type, subject_name, original_limit, state, http = (
        _reversible_quota_http()
    )
    monkeypatch.setattr(verifier, "_http", http)
    probe_limit = 4 * 1024**3
    confirmation = (
        f"VERIFY ECHO OMV QUOTA WRITE {filesystem_uuid} user {subject_name} "
        f"FROM {original_limit} TO {probe_limit}"
    )

    result = verifier._assert_omv_quota_reversible_write(
        "http://127.0.0.1:8000",
        token="browser-token",
        password="device-password",
        filesystem_uuid=filesystem_uuid,
        subject_type=subject_type,
        subject_name=subject_name,
        probe_limit_bytes=probe_limit,
        confirmation=confirmation,
        require_write=True,
    )

    assert result["writeExecuted"] is True
    assert result["restored"] is True
    assert result["auditVerified"] is True
    assert state["currentLimit"] == original_limit
    assert state["appliedLimits"] == [probe_limit, original_limit]


def test_omv_quota_write_uses_emergency_restore_but_still_reports_failure(
    monkeypatch,
) -> None:
    filesystem_uuid, subject_type, subject_name, original_limit, state, http = (
        _reversible_quota_http(fail_first_restore=True)
    )
    monkeypatch.setattr(verifier, "_http", http)
    probe_limit = 4 * 1024**3
    confirmation = (
        f"VERIFY ECHO OMV QUOTA WRITE {filesystem_uuid} user {subject_name} "
        f"FROM {original_limit} TO {probe_limit}"
    )

    with pytest.raises(verifier.VerificationError, match="OMV quota apply failed"):
        verifier._assert_omv_quota_reversible_write(
            "http://127.0.0.1:8000",
            token="browser-token",
            password="device-password",
            filesystem_uuid=filesystem_uuid,
            subject_type=subject_type,
            subject_name=subject_name,
            probe_limit_bytes=probe_limit,
            confirmation=confirmation,
            require_write=True,
        )

    assert state["restoreFailures"] == 1
    assert state["currentLimit"] == original_limit
    assert state["appliedLimits"][-1] == original_limit


def test_omv_quota_write_rejects_looser_probe_and_missing_second_key(monkeypatch) -> None:
    filesystem_uuid, subject_type, subject_name, original_limit, state, http = (
        _reversible_quota_http()
    )
    monkeypatch.setattr(verifier, "_http", http)

    with pytest.raises(verifier.VerificationError, match="temporarily tighten"):
        verifier._assert_omv_quota_reversible_write(
            "http://127.0.0.1:8000",
            token="browser-token",
            password="device-password",
            filesystem_uuid=filesystem_uuid,
            subject_type=subject_type,
            subject_name=subject_name,
            probe_limit_bytes=original_limit * 2,
            confirmation=None,
            require_write=False,
        )

    probe_limit = original_limit // 2
    confirmation = (
        f"VERIFY ECHO OMV QUOTA WRITE {filesystem_uuid} user {subject_name} "
        f"FROM {original_limit} TO {probe_limit}"
    )
    with pytest.raises(verifier.VerificationError, match="also requires"):
        verifier._assert_omv_quota_reversible_write(
            "http://127.0.0.1:8000",
            token="browser-token",
            password="device-password",
            filesystem_uuid=filesystem_uuid,
            subject_type=subject_type,
            subject_name=subject_name,
            probe_limit_bytes=probe_limit,
            confirmation=confirmation,
            require_write=False,
        )

    assert state["currentLimit"] == original_limit
    assert state["appliedLimits"] == []


def test_omv_quota_write_rejects_stale_confirmation_before_approval(monkeypatch) -> None:
    filesystem_uuid, subject_type, subject_name, original_limit, state, http = (
        _reversible_quota_http()
    )
    monkeypatch.setattr(verifier, "_http", http)
    probe_limit = original_limit // 2

    with pytest.raises(verifier.VerificationError, match="does not match"):
        verifier._assert_omv_quota_reversible_write(
            "http://127.0.0.1:8000",
            token="browser-token",
            password="device-password",
            filesystem_uuid=filesystem_uuid,
            subject_type=subject_type,
            subject_name=subject_name,
            probe_limit_bytes=probe_limit,
            confirmation=(
                f"VERIFY ECHO OMV QUOTA WRITE {filesystem_uuid} user {subject_name} "
                f"FROM 0 TO {probe_limit}"
            ),
            require_write=True,
        )

    assert state["currentLimit"] == original_limit
    assert state["appliedLimits"] == []
    assert [url for _method, url, _payload in state["requests"]] == [
        "http://127.0.0.1:8000/api/appliance/omv/quota/plan"
    ]


@pytest.mark.parametrize(
    ("filesystem_uuid", "subject_type", "subject_name", "limit", "match"),
    [
        ("not-a-uuid", "user", "echoverify", 1024, "exact UUID"),
        ("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "role", "echoverify", 1024, "user or group"),
        ("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "user", "bad name", 1024, "POSIX"),
        ("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "user", "echoverify", 1025, "multiple"),
    ],
)
def test_omv_quota_write_rejects_ambiguous_target_before_http(
    monkeypatch,
    filesystem_uuid,
    subject_type,
    subject_name,
    limit,
    match,
) -> None:
    monkeypatch.setattr(
        verifier,
        "_http",
        lambda *_args, **_kwargs: pytest.fail("invalid target must not make an HTTP request"),
    )

    with pytest.raises(verifier.VerificationError, match=match):
        verifier._assert_omv_quota_reversible_write(
            "http://127.0.0.1:8000",
            token="browser-token",
            password="device-password",
            filesystem_uuid=filesystem_uuid,
            subject_type=subject_type,
            subject_name=subject_name,
            probe_limit_bytes=limit,
            confirmation=None,
            require_write=False,
        )


def test_omv_host_install_uses_root_managed_code_path(tmp_path: Path) -> None:
    code_root = tmp_path / "usr" / "lib" / "echo-os" / "omv-bridge"
    package = code_root / "appliance"
    package.mkdir(parents=True)
    code_root.chmod(0o755)
    package.chmod(0o755)
    for name in ("__init__.py", "omv_bridge.py"):
        target = package / name
        target.write_text("# managed bridge\n")
        target.chmod(0o644)
    unit = tmp_path / "echo-omv-bridge.service"
    unit.write_text(
        f"[Service]\nWorkingDirectory={code_root}\nEnvironment=PYTHONPATH={code_root}\n"
    )
    unit.chmod(0o644)

    result = verifier._assert_omv_host_install(
        str(unit),
        str(code_root),
        expected_uid=os.getuid(),
        supported_host_check=lambda: {
            "distribution": "debian",
            "distribution_version": "13",
            "omv_version": "8.0.4-1",
            "omv_major": 8,
            "support_matrix": "debian-13+omv-8",
        },
    )

    assert result["repository_executed_as_root"] is False
    assert result["code_root"] == str(code_root)
    assert result["support_matrix"] == "debian-13+omv-8"


def test_omv_host_install_rejects_user_repository_unit(tmp_path: Path) -> None:
    code_root = tmp_path / "managed"
    package = code_root / "appliance"
    package.mkdir(parents=True)
    code_root.chmod(0o755)
    package.chmod(0o755)
    for name in ("__init__.py", "omv_bridge.py"):
        target = package / name
        target.write_text("# managed bridge\n")
        target.chmod(0o644)
    unit = tmp_path / "echo-omv-bridge.service"
    unit.write_text(
        "[Service]\nWorkingDirectory=/opt/echo-os\nEnvironment=PYTHONPATH=/opt/echo-os\n"
    )
    unit.chmod(0o644)

    with pytest.raises(verifier.VerificationError, match="root-only code path"):
        verifier._assert_omv_host_install(
            str(unit),
            str(code_root),
            expected_uid=os.getuid(),
        )


def _supported_omv_host_files(tmp_path: Path) -> tuple[Path, Path]:
    os_release = tmp_path / "usr" / "lib" / "os-release"
    os_release.parent.mkdir(parents=True)
    os_release.write_text('ID=debian\nVERSION_ID="13"\n')
    os_release.chmod(0o644)
    dpkg_query = tmp_path / "usr" / "bin" / "dpkg-query"
    dpkg_query.parent.mkdir(parents=True)
    dpkg_query.write_text("fixture\n")
    dpkg_query.chmod(0o755)
    return os_release, dpkg_query


def test_omv_runtime_verifier_confirms_supported_host_matrix(tmp_path: Path) -> None:
    os_release, dpkg_query = _supported_omv_host_files(tmp_path)
    observed: list[list[str]] = []

    def run(command):
        observed.append(command)
        return subprocess.CompletedProcess(command, 0, "1:8.0.4-1~bpo13+1\n", "")

    result = verifier._assert_omv_supported_host(
        os_release_path=os_release,
        dpkg_query_path=dpkg_query,
        expected_uid=os.getuid(),
        command_runner=run,
    )

    assert result == {
        "distribution": "debian",
        "distribution_version": "13",
        "omv_version": "1:8.0.4-1~bpo13+1",
        "omv_major": 8,
        "support_matrix": "debian-13+omv-8",
    }
    assert observed == [[str(dpkg_query), "-W", "-f=${Version}", "openmediavault"]]


def test_omv_runtime_verifier_rejects_debian_12_before_package_query(
    tmp_path: Path,
) -> None:
    os_release, dpkg_query = _supported_omv_host_files(tmp_path)
    os_release.write_text('ID=debian\nVERSION_ID="12"\n')

    with pytest.raises(verifier.VerificationError, match="Debian 13"):
        verifier._assert_omv_supported_host(
            os_release_path=os_release,
            dpkg_query_path=dpkg_query,
            expected_uid=os.getuid(),
            command_runner=lambda _command: pytest.fail("package query must not run"),
        )


def test_omv_runtime_verifier_rejects_unsupported_omv_major(tmp_path: Path) -> None:
    os_release, dpkg_query = _supported_omv_host_files(tmp_path)

    with pytest.raises(verifier.VerificationError, match="OMV 8"):
        verifier._assert_omv_supported_host(
            os_release_path=os_release,
            dpkg_query_path=dpkg_query,
            expected_uid=os.getuid(),
            command_runner=lambda command: subprocess.CompletedProcess(command, 0, "9.0.0-1\n", ""),
        )


def test_omv_unit_auto_detection_distinguishes_native_and_managed_install(
    tmp_path: Path,
    monkeypatch,
) -> None:
    native = tmp_path / "usr/lib/systemd/system/echo-omv-bridge.service"
    managed = tmp_path / "etc/systemd/system/echo-omv-bridge.service"
    native.parent.mkdir(parents=True)
    managed.parent.mkdir(parents=True)
    monkeypatch.setattr(verifier, "_OMV_NATIVE_UNIT", native)
    monkeypatch.setattr(verifier, "_OMV_MANAGED_UNIT", managed)

    with pytest.raises(verifier.VerificationError, match="exactly one"):
        verifier._resolve_omv_unit_path("auto")

    native.write_text("native\n")
    assert verifier._resolve_omv_unit_path("auto") == (
        str(native),
        "nativePluginPackage",
    )
    native.unlink()
    managed.write_text("managed\n")
    assert verifier._resolve_omv_unit_path("auto") == (
        str(managed),
        "managedHostBundle",
    )

    native.write_text("native\n")
    with pytest.raises(verifier.VerificationError, match="exactly one"):
        verifier._resolve_omv_unit_path("auto")


def test_omv_unit_explicit_path_must_be_absolute() -> None:
    with pytest.raises(verifier.VerificationError, match="absolute or auto"):
        verifier._resolve_omv_unit_path("relative/echo-omv-bridge.service")
    assert verifier._resolve_omv_unit_path("/opt/test/echo-omv-bridge.service") == (
        "/opt/test/echo-omv-bridge.service",
        "explicit",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"serialnumber": "secret-drive"},
        {"users": [{"sshpubkeys": ["secret-key"]}]},
        {"devicefile": "/dev/disk/by-id/ata-secret"},
    ],
)
def test_omv_real_device_probe_rejects_sensitive_bridge_fields(payload) -> None:
    with pytest.raises(verifier.VerificationError, match="exposed"):
        verifier._assert_no_omv_secrets(payload)
