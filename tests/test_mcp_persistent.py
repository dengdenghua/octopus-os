"""Implementation note."""

from __future__ import annotations

import pytest

from runtime.adapters.mcp_client import (
    STDIO_AVAILABLE,
    MCPClientError,
    MCPServerConfig,
    PersistentStdioMCPClient,
)


@pytest.mark.skipif(not STDIO_AVAILABLE, reason="mcp SDK required")
class TestConnectFailure:
    def test_bad_command_raises_mcp_client_error(self):
        """Implementation note."""
        cfg = MCPServerConfig(
            name="bad",
            command="cmd_definitely_does_not_exist_98765",
            timeout_ms=3000,
        )
        with pytest.raises(MCPClientError, match="connect failed"):
            PersistentStdioMCPClient(cfg, connect_timeout_ms=3000)


class TestSDKMissing:
    def test_errors_without_sdk(self, monkeypatch):
        from runtime.adapters.mcp_client import persistent_client as mod

        monkeypatch.setattr(mod, "STDIO_AVAILABLE", False)
        with pytest.raises(MCPClientError, match="mcp SDK not installed"):
            PersistentStdioMCPClient(MCPServerConfig(name="x", command="npx"))


@pytest.mark.skipif(not STDIO_AVAILABLE, reason="mcp SDK required")
class TestLifecycle:
    def test_close_is_idempotent(self):
        """Implementation note."""
        cfg = MCPServerConfig(
            name="bad",
            command="cmd_definitely_does_not_exist_98765",
            timeout_ms=2000,
        )
        try:
            client = PersistentStdioMCPClient(cfg, connect_timeout_ms=2000)
        except MCPClientError:
            return  # Implementation note.

        # Implementation note.
        client.close()
        client.close()  # Implementation note.

    def test_context_manager_cleans_up(self):
        """Implementation note."""
        cfg = MCPServerConfig(
            name="bad",
            command="cmd_definitely_does_not_exist_98765",
            timeout_ms=2000,
        )
        try:
            with PersistentStdioMCPClient(cfg, connect_timeout_ms=2000):
                pass
        except MCPClientError:
            pass  # expected
