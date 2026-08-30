"""
Integration tests for the MCP endpoint group.

Pre-split baseline for
``runtime/platform/ui/app.py`` → ``runtime/sensing/siphon/mcp_router.py``.
Follows the same lock-then-refactor pattern as
``test_app_config_endpoints`` and ``test_app_meta_endpoints``.

Covered
-------

    GET  /api/mcp/config        · declared-server state
    PUT  /api/mcp/config        · enable/disable servers

These tests assert the **shape** of the response, not whether
subprocess spawning succeeds — the MCP SDK path is behind an
``ImportError`` guard in the handler, so tests that don't install
``mcp`` stay green while still pinning the UI contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runtime.platform.ui.app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.chdir(tmp_path)
    return TestClient(create_app())


class TestMcpConfigGet:
    def test_returns_mcp_servers_field(self, client: TestClient) -> None:
        r = client.get("/api/mcp/config")
        assert r.status_code == 200
        data = r.json()
        assert "mcp_servers" in data
        assert isinstance(data["mcp_servers"], dict)


class TestMcpConfigPut:
    def test_rejects_non_object_mcp_servers(
        self,
        client: TestClient,
    ) -> None:
        r = client.put(
            "/api/mcp/config",
            json={"mcp_servers": "not-a-dict"},
        )
        assert r.status_code == 400

    def test_disabled_entry_is_stored(self, client: TestClient) -> None:
        r = client.put(
            "/api/mcp/config",
            json={
                "mcp_servers": {
                    "demo-server": {
                        "enabled": False,
                        "description": "a demo MCP",
                    },
                },
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["mcp_servers"]["demo-server"]["enabled"] is False
        assert data["mcp_servers"]["demo-server"]["description"] == "a demo MCP"
        # GET reflects the PUT
        listing = client.get("/api/mcp/config").json()
        assert "demo-server" in listing["mcp_servers"]

    def test_enabled_without_command_surfaces_error(
        self,
        client: TestClient,
    ) -> None:
        """Enabling a server that isn't a known preset AND has no
        explicit command should report a meaningful error in
        ``_status`` rather than silently pretend it's running."""
        r = client.put(
            "/api/mcp/config",
            json={
                "mcp_servers": {
                    "unknown-server": {"enabled": True},
                },
            },
        )
        assert r.status_code == 200
        data = r.json()
        status = data.get("_status", {}).get("unknown-server", {})
        # Either ``ok: False`` with a reason, or ``enabled`` gets
        # flipped back to False + an ``error`` field on the entry.
        if status:
            assert status.get("ok") is False
        # Entry reflects the failure
        entry = data["mcp_servers"]["unknown-server"]
        assert entry.get("enabled") is False or "error" in entry

    def test_carries_through_command_args_env(
        self,
        client: TestClient,
    ) -> None:
        """When the caller supplies command/args/env explicitly, the
        stored entry echoes them back — lets the UI re-render the
        form with current values after refresh."""
        payload = {
            "mcp_servers": {
                "custom-srv": {
                    "enabled": False,  # off to avoid actual spawn
                    "command": "node",
                    "args": ["server.js"],
                    "env": {"FOO": "bar"},
                },
            },
        }
        r = client.put("/api/mcp/config", json=payload)
        assert r.status_code == 200
        stored = r.json()["mcp_servers"]["custom-srv"]
        assert stored["command"] == "node"
        assert stored["args"] == ["server.js"]
        assert stored["env"] == {"FOO": "bar"}
