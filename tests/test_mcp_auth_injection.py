"""MCP 代理认证注入测试 — 认证编排层(headers/env)接入 MCP 客户端。

覆盖:
  - mcp_injection_for_server:仅 已安装+已启用+已连接 的连接器才注入
  - HttpMCPClient._transport:headers 合并 + 用户手填优先级最高
  - StdioMCPClient._stdio_env:config env + connector env 合并
"""

from __future__ import annotations

from pathlib import Path

from runtime.adapters.mcp_client import HttpMCPClient, MCPServerConfig, StdioMCPClient
from runtime.platform.connectors.auth_orchestrator import (
    AuthOrchestrator,
    mcp_injection_for_server,
)
from runtime.platform.connectors.connector_registry import ConnectorRegistry
from runtime.platform.connectors.credential_store import CredentialStore

FORK = Path(__file__).resolve().parents[1] / "extensions" / "workbuddy-connectors"


def _setup(
    tmp_path, connector_id: str = "westock-mcp"
) -> tuple[ConnectorRegistry, AuthOrchestrator]:
    reg = ConnectorRegistry(
        marketplace_root=FORK,
        skills_root=tmp_path / "skills",
        state_file=tmp_path / "state.json",
    )
    creds = CredentialStore(
        root=tmp_path,
        master_key_file=tmp_path / "key",
        credentials_file=tmp_path / "cred.json",
    )
    orch = AuthOrchestrator(credentials=creds)
    return reg, orch


class TestMcpInjectionResolver:
    def test_injects_only_installed_enabled_connected(self, tmp_path):
        reg, orch = _setup(tmp_path)
        conn = reg.get("westock-mcp")
        # 未安装 → 空
        assert mcp_injection_for_server("westock-mcp", registry=reg, orchestrator=orch) == {
            "headers": {},
            "env": {},
        }

        installed = reg.install("westock-mcp")  # installed + enabled=False
        assert mcp_injection_for_server("westock-mcp", registry=reg, orchestrator=orch) == {
            "headers": {},
            "env": {},
        }

        reg.grant_permissions("westock-mcp", installed["permissions"])
        reg.set_enabled("westock-mcp", True)  # enabled, 但未连接
        assert mcp_injection_for_server("westock-mcp", registry=reg, orchestrator=orch) == {
            "headers": {},
            "env": {},
        }

        orch.connect(conn, tokens={"access_token": "tok-secret", "MCP_TOKEN": "env-secret"})
        out = mcp_injection_for_server("westock-mcp", registry=reg, orchestrator=orch)
        assert out["headers"].get("Authorization") == "Bearer tok-secret"
        assert out["env"].get("MCP_TOKEN") == "env-secret"

    def test_no_match_for_unknown_server(self, tmp_path):
        reg, orch = _setup(tmp_path)
        installed = reg.install("westock-mcp")
        reg.grant_permissions("westock-mcp", installed["permissions"])
        reg.set_enabled("westock-mcp", True)
        assert mcp_injection_for_server("totally-unknown", registry=reg, orchestrator=orch) == {
            "headers": {},
            "env": {},
        }

    def test_disabled_connector_not_injected(self, tmp_path):
        reg, orch = _setup(tmp_path)
        reg.install("westock-mcp")
        orch.connect(reg.get("westock-mcp"), tokens={"access_token": "tok"})
        reg.set_enabled("westock-mcp", False)
        assert mcp_injection_for_server("westock-mcp", registry=reg, orchestrator=orch) == {
            "headers": {},
            "env": {},
        }


class TestHttpTransportInjection:
    def test_headers_merged_with_connector_auth(self, monkeypatch):
        from runtime.adapters.mcp_client import client as client_mod

        monkeypatch.setattr(
            client_mod,
            "_connector_headers_for",
            lambda name: {"Authorization": "Bearer conn-tok", "x-api-key": "k"},
        )
        client = HttpMCPClient(
            MCPServerConfig(name="westock-mcp", transport="http", url="http://x/mcp")
        )
        # MCP 2.x: headers 注入到 httpx2.AsyncClient 并传给 streamable_http_client。
        # _transport 返回 async CM;这里检查构造参数,不真正连网。
        import mcp.client.streamable_http as mod

        captured: dict = {}

        def fake_factory(url, *, http_client=None, **kw):
            captured["http_client"] = http_client
            return _FakeCtx()

        monkeypatch.setattr(mod, "streamable_http_client", fake_factory)
        _ = client._transport()
        assert captured["http_client"] is not None
        assert captured["http_client"].headers.get("Authorization") == "Bearer conn-tok"
        assert captured["http_client"].headers.get("x-api-key") == "k"
        client.close()

    def test_user_headers_win_over_connector(self, monkeypatch):
        from runtime.adapters.mcp_client import client as client_mod

        monkeypatch.setattr(
            client_mod,
            "_connector_headers_for",
            lambda name: {"Authorization": "Bearer conn-tok"},
        )
        client = HttpMCPClient(
            MCPServerConfig(
                name="westock-mcp",
                transport="http",
                url="http://x/mcp",
                headers={"Authorization": "Bearer manual"},
            )
        )
        import mcp.client.streamable_http as mod

        captured: dict = {}

        def fake_factory(url, *, http_client=None, **kw):
            captured["http_client"] = http_client
            return _FakeCtx()

        monkeypatch.setattr(mod, "streamable_http_client", fake_factory)
        _ = client._transport()
        assert captured["http_client"] is not None
        assert captured["http_client"].headers.get("Authorization") == "Bearer manual"
        client.close()


class _FakeCtx:
    async def __aenter__(self):
        return (object(), object(), None)

    async def __aexit__(self, *_a):
        return False


class TestStdioEnvInjection:
    def test_config_and_connector_env_merged(self, monkeypatch):
        from runtime.adapters.mcp_client import client as client_mod

        monkeypatch.setattr(
            client_mod,
            "_connector_env_for",
            lambda name: {"MCP_TOKEN": "env-secret", "SHARED": "connector"},
        )
        client = StdioMCPClient(
            MCPServerConfig(
                name="westock-mcp",
                command="npx",
                env={"SHARED": "config", "CONFIG_ONLY": "yes"},
            )
        )
        env = client._stdio_env()
        assert env["MCP_TOKEN"] == "env-secret"
        assert env["CONFIG_ONLY"] == "yes"
        # config env 优先
        assert env["SHARED"] == "config"
        client.close()

    def test_none_when_no_env(self, monkeypatch):
        from runtime.adapters.mcp_client import client as client_mod

        monkeypatch.setattr(client_mod, "_connector_env_for", lambda name: {})
        client = StdioMCPClient(MCPServerConfig(name="x", command="npx"))
        assert client._stdio_env() is None
        client.close()
