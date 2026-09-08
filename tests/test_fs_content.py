"""Real HTTP and SQLite coverage for authorized original-file content."""

from __future__ import annotations

import base64
import builtins
import hashlib
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.threads import ThreadStateStore
from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway import _fs_router_content as content
from runtime.sensing.gateway.fs_router import create_fs_router
from runtime.sensing.gateway.thread_workspace import ensure_managed_thread_workspace
from runtime.sensing.server.mount_backend import LocalMountBackend, MountBackendRegistry
from runtime.workspace import WorkspaceStore, crypto


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ECHO_FS_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setenv("ECHO_PATH_DENYLIST_PATH", str(tmp_path / "denylist.json"))
    monkeypatch.setattr(crypto, "_CIPHER_CACHE", None)
    monkeypatch.setattr(crypto, "_CIPHER_KEY_CACHE", None)
    monkeypatch.setattr(crypto, "_MACHINE_ID_CACHE", None)


def _client(**kwargs):
    app = FastAPI()
    app.include_router(create_fs_router(**kwargs))
    return TestClient(app)


def _identities():
    identities = IdentityStore()
    for actor, tenant in (("alice", "tenant-a"), ("bob", "tenant-a"), ("carol", "tenant-b")):
        identities.add(
            Identity(actor_id=actor, metadata={"tenant_id": tenant}),
            api_key_plaintext=f"synthetic-{actor}",
        )
    return identities


def _headers(actor="alice"):
    return {"Authorization": f"Bearer synthetic-{actor}"}


@pytest.mark.parametrize(
    "name,mime",
    [
        ("原件.pdf", "application/pdf"),
        ("original.png", "image/png"),
        ("report.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("book.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("deck.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ],
)
def test_binary_content_is_exact_original(tmp_path, name, mime):
    target = tmp_path / name
    raw = b"original\x00\xff\xfe\r\nno text decoding\n"
    target.write_bytes(raw)
    response = _client().get("/api/fs/content", params={"path": str(target)})
    assert response.status_code == 200
    assert response.content == raw
    assert response.headers["content-type"] == mime
    assert int(response.headers["content-length"]) == len(raw)
    assert response.headers["etag"] == '"' + hashlib.sha256(raw).hexdigest() + '"'
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "range_value,expected,content_range",
    [
        ("bytes=0-3", b"0123", "bytes 0-3/10"),
        ("bytes=5-", b"56789", "bytes 5-9/10"),
        ("bytes=-3", b"789", "bytes 7-9/10"),
        ("bytes=0-999", b"0123456789", "bytes 0-9/10"),
    ],
)
def test_range_selects_from_exact_original(tmp_path, range_value, expected, content_range):
    target = tmp_path / "original.pdf"
    target.write_bytes(b"0123456789")
    response = _client().get(
        "/api/fs/content", params={"path": str(target)}, headers={"Range": range_value}
    )
    assert response.status_code == 206
    assert response.content == expected
    assert response.headers["content-length"] == str(len(expected))
    assert response.headers["content-range"] == content_range


@pytest.mark.parametrize(
    "range_value",
    [
        "bytes=",
        "bytes=999-",
        "bytes=3-2",
        "bytes=-0",
        "items=0-1",
        "bytes=0-1,3-4",
        "bytes=" + "9" * 5000 + "-",
    ],
)
def test_invalid_or_unsatisfiable_range(tmp_path, range_value):
    target = tmp_path / "original.pdf"
    target.write_bytes(b"0123456789")
    response = _client().get(
        "/api/fs/content", params={"path": str(target)}, headers={"Range": range_value}
    )
    assert response.status_code == 416
    assert response.content == b""
    assert response.headers["content-range"] == "bytes */10"


def test_empty_head_and_stale_if_range(tmp_path):
    target = tmp_path / "original.pdf"
    target.write_bytes(b"pdf-original")
    client = _client()
    head = client.head("/api/fs/content", params={"path": str(target)})
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == "12"
    stale = client.get(
        "/api/fs/content",
        params={"path": str(target)},
        headers={"Range": "bytes=0-2", "If-Range": '"previous-version"'},
    )
    assert stale.status_code == 200
    assert stale.content == b"pdf-original"
    target.write_bytes(b"")
    empty = client.get("/api/fs/content", params={"path": str(target)})
    assert empty.status_code == 200 and empty.content == b""
    unsatisfiable = client.get(
        "/api/fs/content", params={"path": str(target)}, headers={"Range": "bytes=0-1"}
    )
    assert unsatisfiable.status_code == 416


def test_openapi_operation_ids_are_unique_and_get_head_both_work(tmp_path):
    target = tmp_path / "original.pdf"
    raw = b"original PDF bytes"
    target.write_bytes(raw)
    client = _client()
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        response = client.get("/openapi.json")
    assert response.status_code == 200
    assert not any("Duplicate Operation ID" in str(warning.message) for warning in emitted)
    paths = response.json()["paths"]
    ids = [
        operation["operationId"]
        for item in paths.values()
        for operation in item.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    assert len(ids) == len(set(ids))
    assert paths["/api/fs/content"]["get"]["operationId"] == "api_fs_content_get"
    assert paths["/api/fs/content"]["head"]["operationId"] == "api_fs_content_head"
    get = client.get("/api/fs/content", params={"path": str(target)})
    head = client.head("/api/fs/content", params={"path": str(target)})
    assert get.status_code == head.status_code == 200
    assert get.content == raw and head.content == b""
    assert get.headers["content-length"] == head.headers["content-length"] == str(len(raw))
    assert get.headers["content-type"] == head.headers["content-type"] == "application/pdf"


def test_missing_original_never_returns_same_named_file(tmp_path):
    (tmp_path / "same.pdf").write_bytes(b"unrelated attachment")
    response = _client().get(
        "/api/fs/content", params={"path": str(tmp_path / "missing" / "same.pdf")}
    )
    assert response.status_code == 404
    assert b"unrelated attachment" not in response.content
    assert _client().get("/api/fs/content", params={"path": str(tmp_path)}).status_code == 404


def test_relative_paths_need_an_explicit_workspace(tmp_path):
    target = tmp_path / "nested" / "original.pdf"
    target.parent.mkdir()
    target.write_bytes(b"nested original")
    client = _client()
    assert client.get("/api/fs/content", params={"path": "nested/original.pdf"}).status_code == 400
    response = client.get(
        "/api/fs/content",
        params={
            "path": "nested/original.pdf",
            "workspace_path": str(tmp_path),
        },
    )
    assert response.status_code == 200 and response.content == b"nested original"
    denied = client.get(
        "/api/fs/content",
        params={
            "path": "../outside.pdf",
            "workspace_path": str(tmp_path),
        },
    )
    assert denied.status_code == 403


def _managed_client(tmp_path):
    store = ThreadStateStore()
    workspace_root = tmp_path / "managed"
    store.ensure_thread(
        "alice-thread", metadata={"owner_actor_id": "alice", "tenant_id": "tenant-a"}
    )
    directory = ensure_managed_thread_workspace(
        workspace_root,
        thread_id="alice-thread",
        actor_id="alice",
        tenant_id="tenant-a",
        store=store,
    )
    client = _client(
        thread_store=store,
        workspace_root=workspace_root,
        identity_store=_identities(),
        require_auth=True,
    )
    return client, store, directory


def test_thread_owner_tenant_scope_and_relative_root(tmp_path):
    client, store, directory = _managed_client(tmp_path)
    target = directory / "nested" / "original.pdf"
    target.parent.mkdir()
    target.write_bytes(b"owned original")
    params = {"path": str(target), "thread_id": "alice-thread"}
    assert client.get("/api/fs/content", params=params).status_code == 401
    for actor in ("bob", "carol"):
        assert (
            client.get("/api/fs/content", params=params, headers=_headers(actor)).status_code == 404
        )
    assert (
        client.get("/api/fs/content", params={"path": str(target)}, headers=_headers()).status_code
        == 403
    )
    response = client.get(
        "/api/fs/content",
        params={
            "path": "nested/original.pdf",
            "thread_id": "alice-thread",
        },
        headers=_headers(),
    )
    assert response.status_code == 200 and response.content == b"owned original"
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside document")
    for query in (
        {"path": str(outside), "thread_id": "alice-thread"},
        {"path": str(outside), "thread_id": "alice-thread", "workspace_path": str(tmp_path)},
    ):
        assert client.get("/api/fs/content", params=query, headers=_headers()).status_code == 403
    store.update_state(
        "alice-thread", metadata={**store.get("alice-thread")["metadata"], "tenant_id": "tenant-b"}
    )
    assert client.get("/api/fs/content", params=params, headers=_headers()).status_code == 404


def test_scope_rechecked_after_local_read(tmp_path, monkeypatch):
    client, store, directory = _managed_client(tmp_path)
    target = directory / "original.pdf"
    target.write_bytes(b"private document")
    original = content._read_local_snapshot

    def revoke(path):
        raw = original(path)
        store.update_state(
            "alice-thread",
            metadata={**store.get("alice-thread")["metadata"], "owner_actor_id": "bob"},
        )
        return raw

    monkeypatch.setattr(content, "_read_local_snapshot", revoke)
    response = client.get(
        "/api/fs/content",
        params={"path": str(target), "thread_id": "alice-thread"},
        headers=_headers(),
    )
    assert response.status_code == 404 and b"private document" not in response.content


def test_safe_mime_download_and_real_office_preview(tmp_path):
    client = _client()
    dangerous = tmp_path / "active.html"
    dangerous.write_text("<script>alert(1)</script>", encoding="utf-8")
    response = client.get("/api/fs/content", params={"path": str(dangerous)})
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert "sandbox" in response.headers["content-security-policy"]
    csv = tmp_path / "report.csv"
    csv.write_text("name,value\noriginal,17\n", encoding="utf-8")
    preview = client.get("/api/fs/content", params={"path": str(csv), "office_preview": True})
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("text/html")
    assert "original" in preview.text and "17" in preview.text
    assert "script-src 'nonce-" in preview.headers["content-security-policy"]
    download = client.get(
        "/api/fs/content", params={"path": str(csv), "download": True, "office_preview": True}
    )
    assert download.content == csv.read_bytes()
    assert download.headers["content-type"] == "text/csv"
    assert download.headers["content-disposition"].startswith("attachment;")


def test_fidelity_receives_authorized_snapshot_and_cleans_original(tmp_path, monkeypatch):
    target = tmp_path / "report.docx"
    target.write_bytes(b"synthetic OOXML original")
    observed = []

    def render(path):
        assert path != target and path.name == target.name
        assert path.read_bytes() == b"synthetic OOXML original"
        observed.append(path)
        return "<html>fidelity fixture</html>"

    monkeypatch.setattr(content, "render_office_fidelity_preview", render)
    response = _client().get(
        "/api/fs/content", params={"path": str(target), "office_fidelity_preview": True}
    )
    assert response.status_code == 200
    assert response.headers["x-echo-office-preview"] == "fidelity"
    assert "script-src" not in response.headers["content-security-policy"]
    assert observed and not observed[0].exists()


def _remote_client(tmp_path, *, options=None):
    mount = tmp_path / "remote"
    mount.mkdir()
    store = WorkspaceStore(tmp_path / "workspaces.db")
    store.create_workspace(
        name="originals",
        mount_type="local",
        mount_target=str(mount),
        mount_options=options or {},
        owner_id="alice",
        workspace_id="ws-original",
        tenant_id="tenant-a",
    )
    registry = MountBackendRegistry()
    registry.register("local", LocalMountBackend)
    client = _client(
        workspace_store=store,
        mount_registry=registry,
        identity_store=_identities(),
        require_auth=True,
    )
    return client, store, registry, mount


def test_real_remote_backend_acl_tenant_and_range(tmp_path):
    client, store, _registry, mount = _remote_client(tmp_path)
    (mount / "original.pdf").write_bytes(b"remote\x00\xfforiginal")
    params = {"path": "ws-original:original.pdf"}
    response = client.get("/api/fs/content", params=params, headers=_headers())
    assert response.status_code == 200 and response.content == b"remote\x00\xfforiginal"
    assert client.get("/api/fs/content", params=params, headers=_headers("bob")).status_code == 403
    store.add_member("ws-original", "bob", role="viewer")
    partial = client.get(
        "/api/fs/content",
        params={"path": "original.pdf", "workspace_id": "ws-original"},
        headers={**_headers("bob"), "Range": "bytes=0-5"},
    )
    assert partial.status_code == 206 and partial.content == b"remote"
    store.add_member("ws-original", "carol", role="viewer")
    assert (
        client.get("/api/fs/content", params=params, headers=_headers("carol")).status_code == 404
    )
    assert (
        client.get(
            "/api/fs/content", params={**params, "user_id": "alice"}, headers=_headers("bob")
        ).status_code
        == 403
    )
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"out of mount")
    assert (
        client.get(
            "/api/fs/content", params={"path": "ws-original:../outside.pdf"}, headers=_headers()
        ).status_code
        == 403
    )
    store.remove_member("ws-original", "bob")
    assert client.get("/api/fs/content", params=params, headers=_headers("bob")).status_code == 403


def test_remote_revoke_during_read_returns_no_bytes(tmp_path, monkeypatch):
    client, store, registry, mount = _remote_client(tmp_path)
    (mount / "original.pdf").write_bytes(b"private original")
    store.add_member("ws-original", "bob", role="viewer")
    backend = LocalMountBackend(mount)
    original = backend.read_file

    async def read_then_revoke(path):
        raw = await original(path)
        store.remove_member("ws-original", "bob")
        return raw

    monkeypatch.setattr(backend, "read_file", read_then_revoke)
    registry._instances["ws-original"] = backend
    response = client.get(
        "/api/fs/content", params={"path": "ws-original:original.pdf"}, headers=_headers("bob")
    )
    assert response.status_code == 403 and b"private original" not in response.content


@pytest.mark.parametrize("failure", ["key", "dependency"])
def test_remote_key_failure_never_calls_cached_backend_or_local_reader(
    tmp_path, monkeypatch, failure
):
    monkeypatch.setenv("ECHO_WORKSPACE_KEY", base64.urlsafe_b64encode(b"a" * 32).decode())
    client, store, registry, mount = _remote_client(
        tmp_path, options={"password": "synthetic-password"}
    )
    (mount / "original.pdf").write_bytes(b"must not escape failed key")
    calls = []

    class Cached(LocalMountBackend):
        async def stat(self, path):
            calls.append("cached backend")
            return await super().stat(path)

    registry._instances["ws-original"] = Cached(mount)
    monkeypatch.setattr(content, "_read_local_snapshot", lambda path: calls.append("local"))
    monkeypatch.setenv("ECHO_WORKSPACE_KEY", base64.urlsafe_b64encode(b"b" * 32).decode())
    monkeypatch.setattr(crypto, "_CIPHER_CACHE", None)
    monkeypatch.setattr(crypto, "_CIPHER_KEY_CACHE", None)
    if failure == "dependency":
        original_import = builtins.__import__

        def missing_dependency(name, *args, **kwargs):
            if name == "cryptography.fernet":
                raise ImportError("synthetic optional dependency unavailable")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", missing_dependency)
    params = {"path": "ws-original:original.pdf"}
    response = client.get("/api/fs/content", params=params, headers=_headers())
    assert response.status_code == 503
    assert "workspace_" in response.json()["detail"]["error"]
    assert "synthetic-password" not in response.text
    assert calls == []
    assert client.get("/api/fs/content", params=params, headers=_headers("bob")).status_code == 403


def test_unknown_remote_and_backend_failure_do_not_fallback(tmp_path, monkeypatch):
    client, store, registry, mount = _remote_client(tmp_path)
    (mount / "original.pdf").write_bytes(b"original")
    calls = []
    monkeypatch.setattr(content, "_read_local_snapshot", lambda path: calls.append("local"))
    missing = client.get(
        "/api/fs/content", params={"path": "unknown:original.pdf"}, headers=_headers()
    )
    assert missing.status_code == 404
    assert (
        _client().get("/api/fs/content", params={"path": "unknown:original.pdf"}).status_code == 503
    )
    conflict = client.get(
        "/api/fs/content",
        params={"path": "ws-original:original.pdf", "workspace_id": "other"},
        headers=_headers(),
    )
    assert conflict.status_code == 400
    registry._backends.clear()
    unavailable = client.get(
        "/api/fs/content", params={"path": "ws-original:original.pdf"}, headers=_headers()
    )
    assert unavailable.status_code == 503 and calls == []


def test_local_size_limit_and_permission_failure(tmp_path, monkeypatch):
    target = tmp_path / "original.pdf"
    target.write_bytes(b"0123456789")
    monkeypatch.setattr(content, "_MAX_CONTENT_BYTES", 4)
    assert _client().get("/api/fs/content", params={"path": str(target)}).status_code == 413

    def denied(path):
        raise PermissionError("synthetic private error")

    monkeypatch.setattr(content, "_read_local_snapshot", denied)
    response = _client().get("/api/fs/content", params={"path": str(target)})
    assert response.status_code == 403 and "synthetic private error" not in response.text


def test_docx_preview_extracts_real_original_and_escapes_html(tmp_path):
    target = tmp_path / "original.docx"
    with ZipFile(target, "w") as archive:
        archive.writestr(
            "word/document.xml",
            (
                '<w:document xmlns:w="urn:w"><w:body><w:p><w:r>'
                "<w:t>Original &lt;script&gt;alert(1)&lt;/script&gt;</w:t>"
                "</w:r></w:p></w:body></w:document>"
            ),
        )
    response = _client().get(
        "/api/fs/content", params={"path": str(target), "office_preview": True}
    )
    assert response.status_code == 200
    assert "Original &lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert 'class="document-page"' in response.text


def test_remote_acl_rechecked_after_office_conversion(tmp_path, monkeypatch):
    client, store, registry, mount = _remote_client(tmp_path)
    target = mount / "original.docx"
    target.write_bytes(b"private office original")
    store.add_member("ws-original", "bob", role="viewer")

    def render_and_revoke(path):
        assert path.read_bytes() == b"private office original"
        store.remove_member("ws-original", "bob")
        return "<html>private preview</html>"

    monkeypatch.setattr(content, "render_office_fidelity_preview", render_and_revoke)
    response = client.get(
        "/api/fs/content",
        params={
            "path": "ws-original:original.docx",
            "office_fidelity_preview": True,
        },
        headers=_headers("bob"),
    )
    assert response.status_code == 403 and b"private preview" not in response.content


@pytest.mark.parametrize("scope", ["local", "remote"])
@pytest.mark.parametrize("preview_available", [True, False])
def test_revoke_while_converter_is_blocked_rejects_preview_and_raw_fallback(
    tmp_path,
    monkeypatch,
    scope,
    preview_available,
):
    if scope == "remote":
        client, store, _registry, directory = _remote_client(tmp_path)
        store.add_member("ws-original", "bob", role="viewer")
        params = {"path": "ws-original:original.docx", "office_fidelity_preview": True}
        headers = _headers("bob")
        expected_status = 403

        def revoke():
            store.remove_member("ws-original", "bob")
    else:
        client, store, directory = _managed_client(tmp_path)
        params = {
            "path": "original.docx",
            "thread_id": "alice-thread",
            "office_fidelity_preview": True,
        }
        headers = _headers()
        expected_status = 404

        def revoke():
            store.update_state(
                "alice-thread",
                metadata={
                    **store.get("alice-thread")["metadata"],
                    "owner_actor_id": "bob",
                },
            )

    raw = b"private office original"
    (directory / "original.docx").write_bytes(raw)
    entered = threading.Event()
    release = threading.Event()

    def blocked_converter(path):
        assert path.read_bytes() == raw
        entered.set()
        assert release.wait(10), "test did not release conversion"
        return "<html>private preview</html>" if preview_available else None

    monkeypatch.setattr(content, "render_office_fidelity_preview", blocked_converter)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.get, "/api/fs/content", params=params, headers=headers)
        try:
            assert entered.wait(10), "request never reached the converter"
            assert not future.done()
            revoke()
        finally:
            release.set()
        response = future.result(timeout=10)
    assert response.status_code == expected_status
    assert raw not in response.content and b"private preview" not in response.content


def test_open_handle_replacement_is_rejected(tmp_path, monkeypatch):
    target = tmp_path / "original.pdf"
    target.write_bytes(b"authorized original")
    original_open = content.os.open

    def substituted_open(path, flags, *args, **kwargs):
        if Path(path) == target:
            target.rename(tmp_path / "original-before.pdf")
            target.write_bytes(b"replacement file")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(content.os, "open", substituted_open)
    response = _client().get("/api/fs/content", params={"path": str(target)})
    assert response.status_code == 403 and b"replacement file" not in response.content


def test_backend_failure_never_exposes_connection_details(tmp_path, monkeypatch):
    client, store, registry, mount = _remote_client(tmp_path)
    (mount / "original.pdf").write_bytes(b"original")
    backend = LocalMountBackend(mount)

    async def unavailable(path):
        raise RuntimeError("synthetic connection password=private")

    monkeypatch.setattr(backend, "read_file", unavailable)
    registry._instances["ws-original"] = backend
    response = client.get(
        "/api/fs/content", params={"path": "ws-original:original.pdf"}, headers=_headers()
    )
    assert response.status_code == 503
    assert "password" not in response.text and "private" not in response.text
