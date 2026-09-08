from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from echo_runtime.resource_identity import photo_library_id, storage_file_resource_id
from runtime.storage.service import create_storage_app

TOKEN = "test-storage-private-token-123456789"


@pytest.fixture
def storage(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    (root / "notes.md").write_bytes(b"one\r\ntwo\r\n")
    client = TestClient(create_storage_app(root, token=TOKEN))
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    resource = storage_file_resource_id(photo_library_id(root), "notes.md")
    return client, root, f"/v1/files/{resource}"


def test_full_document_diff_save_and_stale_revision(storage):
    client, root, url = storage
    document = client.get(url + "/text").json()
    assert document["text"] == "one\r\ntwo\r\n"
    assert document["complete"] is True
    change = {"text": "one\r\ninserted\r\ntwo\r\n", "expected_revision": document["revision"]}
    diff = client.post(url + "/diff", json=change)
    assert diff.status_code == 200
    assert "+inserted\r\n" in diff.json()["patch"]
    assert "-two" not in diff.json()["patch"]
    assert (root / "notes.md").read_bytes() == b"one\r\ntwo\r\n"
    saved = client.put(url + "/text", json=change)
    assert saved.status_code == 200
    assert saved.json()["revision"] != document["revision"]
    assert (root / "notes.md").read_bytes() == change["text"].encode()
    assert client.put(url + "/text", json={**change, "text": "stale"}).status_code == 409
    assert client.post(url + "/diff", json=change).status_code == 409
    assert client.get(url + "/text").json() == saved.json()
    assert not list(root.glob(".echo-upload-*.part"))


def test_authentication_and_resource_root_boundary(storage):
    client, root, url = storage
    revision = client.get(url + "/text").json()["revision"]
    change = {"text": "wrong", "expected_revision": revision}
    for suffix, method in (("text", "get"), ("text", "put"), ("diff", "post")):
        kwargs = {"json": change} if method != "get" else {}
        response = getattr(client, method)(
            url + "/" + suffix, headers={"Authorization": "Bearer wrong"}, **kwargs
        )
        assert response.status_code == 401
    other = storage_file_resource_id("different-root", "notes.md")
    assert client.put(f"/v1/files/{other}/text", json=change).status_code == 404
    internal = storage_file_resource_id(photo_library_id(root), ".echo-trash/notes.md")
    assert client.get(f"/v1/files/{internal}/text").status_code == 400
    assert (root / "notes.md").read_bytes() == b"one\r\ntwo\r\n"


def test_concurrent_clients_only_one_commit(storage):
    client, root, url = storage
    revision = client.get(url + "/text").json()["revision"]

    def save(text):
        return client.put(url + "/text", json={"text": text, "expected_revision": revision})

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(save, ["first", "second"]))
    assert sorted(reply.status_code for reply in replies) == [200, 409]
    accepted = next(reply for reply in replies if reply.status_code == 200)
    assert (root / "notes.md").read_text() == accepted.json()["text"]


def test_never_edits_truncated_or_binary_preview(storage):
    client, root, url = storage
    # Larger than the old 200k-character preview, but still fully editable.
    text = "字" * 250_000 + "\nEND"
    (root / "notes.md").write_bytes(text.encode())
    document = client.get(url + "/text").json()
    assert document["text"] == text
    assert (
        client.put(
            url + "/text", json={"text": text + "!", "expected_revision": document["revision"]}
        ).status_code
        == 200
    )
    assert (root / "notes.md").read_bytes().endswith(b"END!")
    (root / "notes.md").write_bytes(b"a" * 1_000_001)
    assert client.get(url + "/text").status_code == 413
    (root / "notes.md").write_bytes(b"\xff\x00")
    assert client.get(url + "/text").status_code == 415


def test_required_revision_and_bounded_diff(storage):
    client, root, url = storage
    assert client.put(url + "/text", json={"text": "missing revision"}).status_code == 422
    doc = client.get(url + "/text").json()
    assert (
        client.post(
            url + "/diff", json={"text": "a\n" * 4001, "expected_revision": doc["revision"]}
        ).status_code
        == 413
    )
    assert (
        client.put(
            url + "/text", json={"text": "字" * 400_000, "expected_revision": doc["revision"]}
        ).status_code
        == 413
    )
    assert (root / "notes.md").read_bytes() == b"one\r\ntwo\r\n"


def test_manifest_and_browse_share_resource_identity(storage):
    client, root, url = storage
    assert "text-edit.v1" in client.get("/v1/manifest").json()["capabilities"]
    item = client.get("/v1/browse").json()[0]
    assert url == f"/v1/files/{item['resource_id']}"
    assert client.get(url + "/content").content == (root / "notes.md").read_bytes()
    assert client.post("/v1/search", json={"query": "notes"}).json()["hits"]


def test_gateway_to_real_service_preserves_protocol(storage, monkeypatch):
    import httpx
    from fastapi import FastAPI

    from runtime.execution.suckers import storage_skills
    from runtime.sensing.gateway.storage_proxy_router import create_storage_proxy_router

    service, root, url = storage
    monkeypatch.setattr(storage_skills, "_base_url", lambda: "http://127.0.0.1:8767")
    monkeypatch.setattr(storage_skills, "_storage_token", lambda: TOKEN)
    upstream = httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app), trust_env=False)
    gateway = FastAPI()
    gateway.include_router(create_storage_proxy_router(http_client=upstream))
    with TestClient(gateway) as browser:
        url = "/api/storage" + url
        doc = browser.get(url + "/text").json()
        change = {"text": "saved through gateway", "expected_revision": doc["revision"]}
        assert browser.post(url + "/diff", json=change).status_code == 200
        reply = browser.put(url + "/text", json=change)
        assert reply.status_code == 200
        assert browser.get(url + "/text").json() == reply.json()
        assert browser.put(url + "/text", json=change).status_code == 409
    assert (root / "notes.md").read_text() == change["text"]


def test_service_refuses_second_writer_process_for_root(tmp_path):
    from appliance.state_lock import StateLockError

    first = create_storage_app(tmp_path, token=TOKEN)
    second = create_storage_app(tmp_path, token=TOKEN)
    with TestClient(first), pytest.raises(StateLockError), TestClient(second):
        pass
    # Closing the server releases the lock, allowing a normal restart.
    with TestClient(second) as client:
        assert (
            client.get("/v1/manifest", headers={"Authorization": f"Bearer {TOKEN}"}).status_code
            == 200
        )


def test_standalone_command_over_real_http(tmp_path):
    import os
    import socket
    import subprocess
    import sys
    import time
    from pathlib import Path

    import httpx

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    (tmp_path / "smoke.txt").write_bytes(b"before")
    env = {**os.environ, "ECHO_STORAGE_TOKEN": TOKEN}
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "runtime.storage.service",
            "serve",
            "--root",
            str(tmp_path),
            "--port",
            str(port),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            trust_env=False,
            timeout=1,
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as client:
            for _ in range(80):
                try:
                    if client.get("/v1/manifest").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert process.poll() is None, "Storage process exited during startup"
                time.sleep(0.1)
            else:
                pytest.fail("standalone Storage was not ready")
            files = client.get("/v1/files").json()
            resource = next(item["resource_id"] for item in files if item["name"] == "smoke.txt")
            url = f"/v1/files/{resource}/text"
            revision = client.get(url).json()["revision"]
            reply = client.put(url, json={"text": "after", "expected_revision": revision})
            assert reply.status_code == 200
            assert (tmp_path / "smoke.txt").read_bytes() == b"after"
            assert client.get(url).json() == reply.json()
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_gateway_edits_require_registered_operator_and_never_fallback(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI

    from runtime.execution.suckers import storage_skills
    from runtime.safety.auth.identity import Identity, IdentityStore
    from runtime.sensing.gateway.storage_proxy_router import create_storage_proxy_router

    (tmp_path / "notes.md").write_text("embedded original")
    monkeypatch.setenv("ECHO_DESKTOP", "1")
    monkeypatch.setenv("ECHO_DESKTOP_FILES_ROOT", str(tmp_path))
    monkeypatch.setattr(storage_skills, "_base_url", lambda: "http://127.0.0.1:8767")
    invoked = []

    def offline(request):
        invoked.append(request)
        raise httpx.ReadTimeout("response lost", request=request)

    identity = IdentityStore()
    identity.add(Identity("member", roles=("member",)), api_key_plaintext="member-key")
    identity.add(Identity("admin", roles=("admin",)), api_key_plaintext="admin-key")
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(offline), trust_env=False)
    app = FastAPI()
    app.include_router(
        create_storage_proxy_router(
            http_client=upstream,
            identity_store=identity,
            require_auth=True,
        )
    )
    url = "/api/storage/v1/files/" + storage_file_resource_id(
        photo_library_id(tmp_path), "notes.md"
    )
    with TestClient(app) as browser:
        change = {"text": "wrong write", "expected_revision": "0" * 64}
        assert browser.put(url + "/text", json=change).status_code == 401
        assert (
            browser.put(
                url + "/text", json=change, headers={"Authorization": "Bearer member-key"}
            ).status_code
            == 403
        )
        assert not invoked
        assert (
            browser.put(
                url + "/text", json=change, headers={"Authorization": "Bearer admin-key"}
            ).status_code
            == 503
        )
    assert (tmp_path / "notes.md").read_text() == "embedded original"
