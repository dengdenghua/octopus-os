from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

# mcp is an optional extra (pyproject [project.optional-dependencies] mcp).
# Guard the import so collection degrades to a skip where the extra is absent
# rather than failing the whole run, matching tests/test_mcp_oauth_router.py.
_mcp_types = pytest.importorskip("mcp.types")
CallToolResult = _mcp_types.CallToolResult
InitializeResult = _mcp_types.InitializeResult
Tool = _mcp_types.Tool

from runtime.execution.suckers.registry import SkillRegistry  # noqa: E402
from runtime.platform.plugins.bundled.narrative_studio import (  # noqa: E402
    NarrativeStudioPlugin,
)
from runtime.platform.plugins.bundled.narrative_studio.mcp_server import (  # noqa: E402
    LATEST_PROTOCOL_VERSION,
    MCP_ENDPOINT,
)
from runtime.platform.plugins.bundled.narrative_studio.models import (  # noqa: E402
    ProjectCreate,
)
from runtime.platform.plugins.plugin_base import ModuleContext  # noqa: E402
from runtime.platform.plugins.plugin_hub import PluginHub  # noqa: E402
from runtime.safety.auth.principal import CurrentPrincipal  # noqa: E402

PLUGIN_DIR = (
    Path(__file__).resolve().parents[1]
    / "runtime"
    / "platform"
    / "plugins"
    / "bundled"
    / "narrative_studio"
)
PACKAGED_SKILL_NAMES = {
    "narrative_studio.narrative_authoring",
    "narrative_studio.continuity",
    "narrative_studio.editorial_readiness",
}
MCP_TOOL_NAMES = {
    "narrative_list_projects",
    "narrative_get_project",
    "narrative_list_chapters",
    "narrative_build_context_candidate",
    "narrative_create_chapter_candidate",
}


def _load_plugin(
    tmp_path: Path,
    *,
    require_auth: bool = False,
    install_principal_middleware: bool = False,
) -> tuple[NarrativeStudioPlugin, ModuleContext, TestClient, SkillRegistry]:
    app = FastAPI()
    app.state.echo_require_auth = require_auth
    if install_principal_middleware:

        @app.middleware("http")
        async def install_principal(request: Request, call_next):
            if request.headers.get("Authorization") == "Bearer host-token":
                request.state.principal = CurrentPrincipal(
                    tenant_id="tenant-a",
                    actor_id="server-user",
                    roles=frozenset({"member"}),
                    scopes=frozenset(),
                    authn_method="test-host",
                    request_id="request-1",
                )
            return await call_next(request)

    registry = SkillRegistry()
    context = ModuleContext(
        plugin_name="narrative_studio",
        plugin_dir=str(PLUGIN_DIR),
        manifest=None,
        fastapi_app=app,
        skill_registry=registry,
        config={"data_dir": str(tmp_path / "narrative")},
    )
    plugin = NarrativeStudioPlugin()
    plugin.on_load(context)
    return plugin, context, TestClient(app), registry


def _rpc(client: TestClient, request_id: int, method: str, params: dict | None = None):
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post(MCP_ENDPOINT, json=body)


def _mcp_route_count(app: FastAPI) -> int:
    def routes_of(route):
        nested = getattr(getattr(route, "original_router", None), "routes", None)
        return list(nested or [])

    top_level = list(app.routes)
    nested = [child for route in top_level for child in routes_of(route)]
    return sum(getattr(route, "path", None) == MCP_ENDPOINT for route in [*top_level, *nested])


def test_mcp_initialize_status_and_candidate_only_tool_allowlist(tmp_path: Path) -> None:
    _plugin, _context, client, _registry = _load_plugin(tmp_path)

    initialized = _rpc(
        client,
        1,
        "initialize",
        {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "clientInfo": {"name": "pytest", "version": "1"},
            "capabilities": {},
        },
    )
    assert initialized.status_code == 200
    result = initialized.json()["result"]
    InitializeResult.model_validate(result)
    assert result["protocolVersion"] == LATEST_PROTOCOL_VERSION
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert "cannot vote" in result["instructions"]

    listed = _rpc(client, 2, "tools/list").json()["result"]["tools"]
    assert all(Tool.model_validate(tool) for tool in listed)
    assert {tool["name"] for tool in listed} == MCP_TOOL_NAMES
    assert all(tool["annotations"]["destructiveHint"] is False for tool in listed)
    assert not any(
        blocked in tool["name"]
        for tool in listed
        for blocked in ("vote", "resolve", "commit", "promote")
    )

    status = client.get("/api/plugins/narrative-studio/status").json()
    assert status["mcp"]["enabled"] is True
    assert status["mcp"]["endpoint"] == MCP_ENDPOINT
    assert status["mcp"]["transport"] == "json-rpc-http"
    assert status["mcp"]["auth"] == "host_inherited"
    assert status["mcp"]["tool_policy"] == "candidate_only_allowlist"
    assert set(status["mcp"]["tools"]) == MCP_TOOL_NAMES
    assert {item["name"] for item in status["packaged_skills"]} == PACKAGED_SKILL_NAMES

    notification = client.post(
        MCP_ENDPOINT,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert notification.status_code == 202
    assert notification.content == b""


def test_mcp_candidate_write_uses_host_actor_and_rejects_canon_input(tmp_path: Path) -> None:
    plugin, _context, client, _registry = _load_plugin(
        tmp_path,
        require_auth=True,
        install_principal_middleware=True,
    )
    assert plugin.store is not None
    plugin.store.create_project(ProjectCreate(id="mcp-story", title="MCP Story"))
    headers = {"Authorization": "Bearer host-token"}

    created = client.post(
        MCP_ENDPOINT,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "narrative_create_chapter_candidate",
                "arguments": {
                    "project_id": "mcp-story",
                    "id": "chapter-1",
                    "branch_id": "main",
                    "ordinal": 1,
                    "title": "Candidate One",
                    "body": "A reviewable draft.",
                },
            },
        },
    )
    payload = created.json()["result"]
    CallToolResult.model_validate(payload)
    assert payload["isError"] is False
    assert payload["structuredContent"]["result"]["canon_status"] == "candidate"
    revision = plugin.store.list_chapter_revisions("mcp-story", "chapter-1")[0]
    assert revision.actor == "mcp:server-user"
    assert revision.actor_source == "authenticated_principal"

    rejected = client.post(
        MCP_ENDPOINT,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "narrative_create_chapter_candidate",
                "arguments": {
                    "project_id": "mcp-story",
                    "branch_id": "main",
                    "ordinal": 2,
                    "title": "Forbidden promotion",
                    "canon_status": "canon",
                },
            },
        },
    ).json()["result"]
    assert rejected["isError"] is True
    assert "canon_status" in rejected["content"][0]["text"]
    assert len(plugin.store.list_chapters("mcp-story")) == 1


def test_mcp_auth_host_rejects_anonymous_and_caller_identity_spoofing(tmp_path: Path) -> None:
    _plugin, _context, client, _registry = _load_plugin(tmp_path, require_auth=True)
    request = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/list",
        "params": {"actor": "spoofed-user"},
    }
    denied = client.post(
        MCP_ENDPOINT,
        headers={
            "Authorization": "Bearer caller-controlled-token",
            "X-Actor-ID": "spoofed-user",
        },
        json=request,
    )
    assert denied.status_code == 401
    assert denied.json()["detail"] == "authenticated Echo principal required"


def test_packaged_skills_are_instruction_only_and_owner_cleanup_unregisters(
    tmp_path: Path,
) -> None:
    plugin, context, _client, registry = _load_plugin(tmp_path)
    assert PACKAGED_SKILL_NAMES.issubset(set(registry.all_names()))
    assert len(plugin.packaged_skill_assets) == 3

    editorial = registry.get("narrative_studio.editorial_readiness")
    loaded = editorial.handler()
    assert loaded["canon_policy"] == "candidate_only"
    assert "no governance authority" in loaded["instructions"]

    plugin.on_unload(context)
    context.cleanup_registrations()
    assert PACKAGED_SKILL_NAMES.isdisjoint(set(registry.all_names()))


def test_disable_enable_removes_and_restores_single_mcp_route_and_owned_skills(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "app-data"))
    # Narrative Studio is delivered as a remotely installable workbench, not
    # executed from the immutable bundled source tree. Exercise the real
    # installed-plugin lifecycle instead of bypassing that delivery boundary.
    external_root = tmp_path / "external-plugins"
    shutil.copytree(PLUGIN_DIR, external_root / "workbench" / "narrative_studio")
    app = FastAPI()
    registry = SkillRegistry()
    hub = PluginHub(
        plugin_dir=external_root,
        bundled_plugin_dir=PLUGIN_DIR.parent,
        skill_registry=registry,
        fastapi_app=app,
        activation_root=tmp_path / "activations",
        data_root=tmp_path / "app-data",
    )
    assert hub.load("narrative_studio") is not None
    client = TestClient(app)
    assert _rpc(client, 10, "tools/list").status_code == 200
    assert PACKAGED_SKILL_NAMES.issubset(set(registry.all_names()))
    assert _mcp_route_count(app) == 1

    disabled = hub.disable_plugin("narrative_studio")
    assert disabled["enabled"] is False
    assert (
        client.post(
            MCP_ENDPOINT,
            json={"jsonrpc": "2.0", "id": 11, "method": "tools/list"},
        ).status_code
        == 404
    )
    assert PACKAGED_SKILL_NAMES.isdisjoint(set(registry.all_names()))
    assert _mcp_route_count(app) == 0

    enabled = hub.enable_plugin("narrative_studio")
    assert enabled["enabled"] is True
    assert _rpc(client, 12, "tools/list").status_code == 200
    assert PACKAGED_SKILL_NAMES.issubset(set(registry.all_names()))
    assert _mcp_route_count(app) == 1


def test_mcp_unknown_method_and_tool_use_protocol_errors(tmp_path: Path) -> None:
    _plugin, _context, client, _registry = _load_plugin(tmp_path)
    missing_method = _rpc(client, 20, "canon/promote").json()
    assert missing_method["error"]["code"] == -32601

    missing_tool = _rpc(
        client,
        21,
        "tools/call",
        {"name": "narrative_promote_to_canon", "arguments": {}},
    ).json()["result"]
    assert missing_tool["isError"] is True
    assert json.loads(missing_tool["content"][0]["text"])["ok"] is False

