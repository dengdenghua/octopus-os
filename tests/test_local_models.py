"""
Integration tests for the local-model scan + import endpoints.

Purpose
-------
Backs the "本地模型" collapsible in the settings page. The scan
endpoint probes a small set of well-known ports in parallel and
reports what's reachable; import takes one of those rows and
writes it into ``custom_models_state`` (the same on-disk shape
that ``api_upsert_custom_model`` produces).

What we cover
-------------

* ``scan`` with no reachable service → empty services list, 200
* ``scan`` with the ``targets=`` override pointing at a fixture
  HTTP server → service discovered with the expected model list
* ``import`` rejects the obvious bad inputs (no base_url, empty
  models list)
* ``import`` happy path writes a complete entry into
  ``custom_models_state`` and the merged listing surfaces it

Design notes
------------

* The fixture HTTP server uses Python's stdlib ``http.server`` so
  we don't pull in any new dependencies. It's a tiny thread-served
  instance that returns a static OpenAI-compat ``/v1/models`` JSON
  payload.
* ``isolated_cwd`` redirects CWD so the persistence path under
  test lands in a scratch dir. Same pattern as the other config
  endpoint tests.
* No planner stack injected — the ``_register`` step degrades to
  ``{"ok": False}`` without one, which is the path we exercise.
  The persistence side is what matters here.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runtime.platform.ui.app import create_app

# ─── Fixtures ─────────────────────────────────────────────


@pytest.fixture
def isolated_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Redirect CWD so ``Path("data/custom_models.json")`` lands in
    a scratch dir. Same pattern as the other config endpoint tests."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def client(isolated_cwd: Path) -> TestClient:
    """TestClient over a minimally-configured app — no stack, just
    enough to serve the config endpoints."""
    app = create_app()
    return TestClient(app)


class _StubModelsHandler(BaseHTTPRequestHandler):
    """Returns a fixed OpenAI-compat ``/v1/models`` payload. The
    body is supplied by the test through the bound class
    attribute ``payload``."""

    payload: bytes = b'{"data":[]}'

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        if self.path == "/v1/models":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(self.payload)))
            self.end_headers()
            self.wfile.write(self.payload)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # Silence stderr noise during tests; the default impl
        # writes to stderr which pollutes pytest -s output.
        return


@pytest.fixture
def stub_openai_server() -> Iterator[str]:
    """Spin up a stdlib HTTP server on an ephemeral port, return
    the base URL. Binds ``_StubModelsHandler.payload`` to a
    realistic OpenAI-compat response with two model ids."""
    _StubModelsHandler.payload = json.dumps(
        {
            "object": "list",
            "data": [
                {"id": "qwen2.5-7b", "object": "model"},
                {"id": "llama-3.1-8b", "object": "model"},
            ],
        },
    ).encode("utf-8")
    server = HTTPServer(("127.0.0.1", 0), _StubModelsHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


# ─── scan ─────────────────────────────────────────────────


def test_scan_with_no_reachable_services_returns_empty(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the default candidate list pointed at dead ports, the
    response is well-formed (200) but contains no entries — we
    deliberately *don't* surface "port 8001 is dead" rows because
    every dev box has dozens of silent ports."""
    # Point the scanner at a definitely-dead port range to avoid
    # any flake from a real local service appearing mid-test.
    monkeypatch.setattr(
        "runtime.sensing.gateway.config_router.os.environ",
        {**__import__("os").environ, "ECHO_TEST_NO_REACH": "1"},
        raising=False,
    )
    # Use the targets override to point all candidates at a
    # port nothing is listening on (1-1023 are reserved and
    # nothing userland binds there).
    resp = client.get(
        "/api/config/local-models/scan",
        params={"targets": "http://127.0.0.1:1,http://127.0.0.1:2"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "services" in body
    # Both targets are connection-refused, so neither should
    # surface — silent ports are dropped by design.
    assert body["services"] == []


def test_scan_with_targets_override_discovers_service(
    client: TestClient,
    stub_openai_server: str,
) -> None:
    """Pointing the scanner at a stub OpenAI-compat server
    surfaces one service with the expected model list."""
    resp = client.get(
        "/api/config/local-models/scan",
        params={"targets": stub_openai_server},
    )
    assert resp.status_code == 200, resp.text
    services = resp.json()["services"]
    assert len(services) == 1
    svc = services[0]
    assert svc["status"] == "ok"
    assert svc["base_url"] == f"{stub_openai_server}/v1"
    assert svc["models"] == ["qwen2.5-7b", "llama-3.1-8b"]
    assert "error" not in svc


def test_scan_reports_empty_status_when_no_models_listed(
    client: TestClient,
    stub_openai_server: str,
) -> None:
    """A service that responds 200 but with an empty ``data`` list
    is surfaced with status=empty rather than status=ok, so the UI
    can show a different message ("service is up but no models
    pulled yet")."""
    _StubModelsHandler.payload = b'{"object":"list","data":[]}'
    resp = client.get(
        "/api/config/local-models/scan",
        params={"targets": stub_openai_server},
    )
    assert resp.status_code == 200, resp.text
    services = resp.json()["services"]
    assert len(services) == 1
    assert services[0]["status"] == "empty"
    assert services[0]["models"] == []


# ─── import ───────────────────────────────────────────────


def test_import_rejects_missing_base_url(client: TestClient) -> None:
    resp = client.post(
        "/api/config/local-models/import",
        json={"models": ["qwen2.5-7b"]},
    )
    assert resp.status_code == 200  # explicit ok:false body
    assert resp.json()["ok"] is False
    assert "base_url" in resp.json()["error"]


def test_import_rejects_empty_models_list(client: TestClient) -> None:
    resp = client.post(
        "/api/config/local-models/import",
        json={"base_url": "http://127.0.0.1:1234/v1", "models": []},
    )
    assert resp.json()["ok"] is False
    assert "model" in resp.json()["error"]


def test_import_happy_path_writes_entry_and_lists_it(
    client: TestClient,
) -> None:
    """Importing a row produces a complete entry in
    ``custom_models_state``; the merged list endpoint surfaces it
    with ``has_api_key: false`` and the new ``models`` shape."""
    body = {
        "base_url": "http://127.0.0.1:1234/v1",
        "models": ["qwen2.5-7b", "llama-3.1-8b"],
        "display_name": "LM Studio (local)",
    }
    resp = client.post("/api/config/local-models/import", json=body)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["ok"] is True
    # Stable id derived from the host:port so re-imports
    # overwrite the same row instead of stacking duplicates.
    assert payload["model_id"] == "local-127-0-0-1-1234"
    entry = payload["entry"]
    assert entry["base_url"] == "http://127.0.0.1:1234/v1"
    assert entry["models"] == ["qwen2.5-7b", "llama-3.1-8b"]
    assert entry["display_name"] == "LM Studio (local)"
    # The merged listing picks it up.
    listed = client.get("/api/config/custom-models").json()["models"]
    assert any(m["id"] == "local-127-0-0-1-1234" for m in listed)


def test_import_uses_explicit_id_when_supplied(
    client: TestClient,
) -> None:
    """The caller can pass ``id`` to control the dispatch key
    (useful when the same base_url hosts multiple logical
    services, e.g. a vLLM cluster)."""
    resp = client.post(
        "/api/config/local-models/import",
        json={
            "base_url": "http://127.0.0.1:8000/v1",
            "models": ["mixtral-8x7b"],
            "id": "vllm-mixtral",
            "display_name": "vLLM Mixtral",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["model_id"] == "vllm-mixtral"


def test_import_idempotent_on_same_base_url(
    client: TestClient,
) -> None:
    """Re-importing a row that already exists (same base_url)
    should update the existing entry in place rather than
    creating a duplicate with a numeric suffix."""
    body = {
        "base_url": "http://127.0.0.1:1234/v1",
        "models": ["qwen2.5-7b"],
    }
    first = client.post("/api/config/local-models/import", json=body).json()
    second = client.post(
        "/api/config/local-models/import",
        json={**body, "models": ["qwen2.5-7b", "llama-3.1-8b"]},
    ).json()
    assert first["model_id"] == second["model_id"] == "local-127-0-0-1-1234"
    # Second import updated the models list in place.
    assert second["entry"]["models"] == ["qwen2.5-7b", "llama-3.1-8b"]
