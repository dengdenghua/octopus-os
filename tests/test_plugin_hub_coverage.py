"""Dense coverage for PluginHub lifecycle (audit Q-05)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from runtime.platform.plugins.plugin_hub import PluginHub


def _make_plugin(root: Path, name: str = "testplug") -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "plugin.yaml").write_text(
        f"name: {name}\nversion: 1.0.0\ndescription: test\n", encoding="utf-8"
    )
    (d / "__init__.py").write_text(
        "from runtime.platform.plugins.plugin_base import ModulePlugin\n\n"
        "class TestPlugin(ModulePlugin):\n"
        f"    name = '{name}'\n"
        "    def register_skills(self):\n        pass\n"
        "    def register_channels(self):\n        pass\n"
        "    def register_routes(self):\n        pass\n",
        encoding="utf-8",
    )
    return d


def _make_echo_plugin(root: Path, *, fail_after_register: bool = False) -> Path:
    plugin = root / "echoplug"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        "\n".join(
            [
                "name: echoplug",
                "version: 1.0.0",
                "host_api: '>=1,<2'",
                "permissions:",
                "  - workspace.read",
                "contributes:",
                "  echo:",
                "    tools:",
                "      - echoplug.echo",
                "    prompts:",
                "      - echoplug.identity",
            ]
        ),
        encoding="utf-8",
    )
    failure = "        raise RuntimeError('registration failed')\n" if fail_after_register else ""
    (plugin / "__init__.py").write_text(
        "from runtime.platform.plugins.plugin_base import ModulePlugin\n"
        "from runtime.safety.hooks.events import PreToolUseEvent\n\n"
        "async def echo(args):\n"
        "    return args\n\n"
        "def observe(_event):\n"
        "    return None\n\n"
        "class EchoPlugin(ModulePlugin):\n"
        "    name = 'echoplug'\n"
        "    def register_echo(self):\n"
        "        self.ctx.register_tool(\n"
        "            'echoplug.echo', 'Echo plugin input', {'type': 'object'}, echo\n"
        "        )\n"
        "        self.ctx.register_prompt_section(\n"
        "            'echoplug.identity', text='Echo plugin identity'\n"
        "        )\n"
        "        self.ctx.register_hook(PreToolUseEvent, observe)\n"
        "        self.ctx.register_workflow(\n"
        "            'echoplug.review', {'steps': ['inspect', 'report']}\n"
        "        )\n"
        "        self.ctx.register_ui_surface(\n"
        "            'echoplug.panel', {'route': '/workspace/echoplug'}\n"
        "        )\n" + failure,
        encoding="utf-8",
    )
    return plugin


def test_echo_contributions_are_owned_introspected_and_disposed(tmp_path: Path) -> None:
    from runtime.execution.arms.tool_registry import ToolRegistry
    from runtime.platform.prompts.registry import PromptRegistry
    from runtime.safety.hooks.events import PreToolUseEvent
    from runtime.safety.hooks.registry import HookRegistry

    tool_registry = ToolRegistry()
    prompt_registry = PromptRegistry(tmp_path / "prompts")
    hook_registry = HookRegistry()
    _make_echo_plugin(tmp_path)
    hub = PluginHub(
        plugin_dir=tmp_path,
        tool_registry=tool_registry,
        prompt_registry=prompt_registry,
        hook_registry=hook_registry,
    )

    assert hub.load("echoplug") is not None
    detail = hub.get_plugin_detail("echoplug")
    assert detail is not None
    assert detail["lifecycle_state"] == "enabled"
    assert detail["contributes"]["echo"]["tools"] == ["echoplug.echo"]
    assert "dsh" not in detail["contributes"]
    assert detail["permissions"] == ["workspace.read"]
    assert detail["host_api"] == ">=1,<2"
    assert {cap["type"] for cap in detail["capabilities"]} >= {
        "echo",
        "tool",
        "prompt_section",
        "hook",
        "workflow",
        "ui_surface",
    }
    assert "echoplug.echo" in tool_registry.tool_names
    assert any(row["name"] == "echoplug.identity" for row in prompt_registry.sections())
    assert len(hook_registry.handlers_for(PreToolUseEvent)) == 1
    assert hub.contribution_registry.get("workflow", "echoplug.review") is not None
    assert hub.contribution_registry.get("ui_surface", "echoplug.panel") is not None
    assert {row["kind"] for row in detail["runtime_contributions"]} == {
        "workflow",
        "ui_surface",
    }

    assert hub.unload("echoplug") is True
    assert "echoplug.echo" not in tool_registry.tool_names
    assert not any(row["name"] == "echoplug.identity" for row in prompt_registry.sections())
    assert hook_registry.handlers_for(PreToolUseEvent) == []
    assert hub.contribution_registry.list(owner="echoplug") == []


def test_failed_echo_registration_rolls_back_every_contribution(tmp_path: Path) -> None:
    from runtime.execution.arms.tool_registry import ToolRegistry
    from runtime.platform.prompts.registry import PromptRegistry
    from runtime.safety.hooks.events import PreToolUseEvent
    from runtime.safety.hooks.registry import HookRegistry

    tool_registry = ToolRegistry()
    prompt_registry = PromptRegistry(tmp_path / "prompts")
    hook_registry = HookRegistry()
    _make_echo_plugin(tmp_path, fail_after_register=True)
    hub = PluginHub(
        plugin_dir=tmp_path,
        tool_registry=tool_registry,
        prompt_registry=prompt_registry,
        hook_registry=hook_registry,
    )

    assert hub.load("echoplug") is None
    assert "echoplug.echo" not in tool_registry.tool_names
    assert prompt_registry.sections() == []
    assert hook_registry.handlers_for(PreToolUseEvent) == []
    assert hub.contribution_registry.list(owner="echoplug") == []


def test_legacy_dsh_manifest_and_hook_are_accepted_but_exposed_as_echo(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "legacyplug"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        "name: legacyplug\ncontributes:\n  dsh:\n    tools:\n      - legacyplug.echo\n",
        encoding="utf-8",
    )
    (plugin / "__init__.py").write_text(
        "from runtime.platform.plugins.plugin_base import ModulePlugin\n\n"
        "class LegacyPlugin(ModulePlugin):\n"
        "    name = 'legacyplug'\n"
        "    def register_dsh(self):\n"
        "        self.ctx.register_workflow('legacyplug.flow', {'steps': []})\n",
        encoding="utf-8",
    )

    hub = PluginHub(plugin_dir=tmp_path)
    assert hub.load("legacyplug") is not None
    detail = hub.get_plugin_detail("legacyplug")
    assert detail is not None
    assert detail["contributes"] == {
        "echo": {"tools": ["legacyplug.echo"]},
    }
    assert {cap["type"] for cap in detail["capabilities"]} >= {"echo", "workflow"}


def test_discover_and_manifest(tmp_path: Path) -> None:
    hub = PluginHub(plugin_dir=tmp_path)
    _make_plugin(tmp_path)
    found = hub.discover()
    assert any(p.get("name") == "testplug" for p in found)
    d = tmp_path / "testplug"
    manifest = hub._read_manifest_file(d)
    assert manifest and manifest["name"] == "testplug"
    assert hub._read_manifest_file(tmp_path / "missing") is None


def test_load_unload_lifecycle(tmp_path: Path) -> None:
    hub = PluginHub(plugin_dir=tmp_path)
    _make_plugin(tmp_path)
    plugin = hub.load("testplug")
    assert plugin is not None and plugin.name == "testplug"
    assert hub.load("testplug") is plugin  # cached
    assert hub.get_plugin("testplug") is plugin
    assert hub.unload("testplug") is True
    assert hub.get_plugin("testplug") is None
    assert hub.load("nope") is None


def test_list_and_plugin_dir_resolution(tmp_path: Path) -> None:
    hub = PluginHub(plugin_dir=tmp_path)
    _make_plugin(tmp_path)
    assert hub.load("testplug") is not None
    listed = hub.list_plugins()
    assert any(p.get("name") == "testplug" for p in listed)
    assert hub._resolve_plugin_dir("testplug") == (tmp_path / "testplug")
    assert hub._resolve_plugin_dir("missing") is None


# ── WebSocket route mounting (voice / realtime stream plugins) ──


def _make_ws_plugin(root: Path, name: str = "voiceplug") -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "plugin.yaml").write_text(
        f"name: {name}\nversion: 1.0.0\ndescription: voice\n"
        "websockets:\n"
        "  - path: /ws/voice\n"
        "    handler: handle_voice_ws\n",
        encoding="utf-8",
    )
    (d / "__init__.py").write_text(
        "from runtime.platform.plugins.plugin_base import ModulePlugin\n\n"
        "class VoicePlugin(ModulePlugin):\n"
        f"    name = '{name}'\n"
        "    async def handle_voice_ws(self, websocket):\n"
        "        await websocket.accept()\n"
        "        while True:\n"
        "            try:\n"
        "                data = await websocket.receive_text()\n"
        "            except Exception:\n"
        "                break  # client closed\n"
        "            if data == 'ping':\n"
        "                await websocket.send_text('pong')\n"
        "        # 不主动 close:让 TestClient 的 with 退出负责收尾。\n",
        encoding="utf-8",
    )
    return d


def test_websocket_route_mounts_and_echoes(tmp_path: Path) -> None:
    """A manifest ``websockets`` entry mounts a live WS route on the app."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    app = FastAPI()
    hub = PluginHub(plugin_dir=tmp_path, fastapi_app=app)
    _make_ws_plugin(tmp_path)
    assert hub.load("voiceplug") is not None

    with TestClient(app) as client:
        try:
            with client.websocket_connect("/api/plugins/webhooks/voiceplug/ws/voice") as ws:
                ws.send_text("ping")
                assert ws.receive_text() == "pong"
        except WebSocketDisconnect:
            # 退出 with 时 TestClient 收到服务端 close 帧,框架正常收尾。
            pass

    hub.unload("voiceplug")

    hub.unload("voiceplug")


def test_websocket_missing_handler_closes_4404(tmp_path: Path) -> None:
    """A websockets entry pointing at a missing handler closes cleanly."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    app = FastAPI()
    hub = PluginHub(plugin_dir=tmp_path, fastapi_app=app)
    d = tmp_path / "wsbroken"
    d.mkdir(parents=True)
    (d / "plugin.yaml").write_text(
        "name: wsbroken\nversion: 1.0.0\n"
        "websockets:\n"
        "  - path: /ws/none\n"
        "    handler: missing_handler\n",
        encoding="utf-8",
    )
    (d / "__init__.py").write_text(
        "from runtime.platform.plugins.plugin_base import ModulePlugin\n\n"
        "class BrokenPlugin(ModulePlugin):\n"
        "    name = 'wsbroken'\n",
        encoding="utf-8",
    )
    assert hub.load("wsbroken") is not None

    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/api/plugins/webhooks/wsbroken/ws/none") as ws,
    ):
        ws.receive_text()

    hub.unload("wsbroken")


# ── Persistent workbench activation + route rollback ──────────


def _make_narrative_factory(root: Path, data_dir: Path) -> Path:
    plugin = root / "narrative_studio"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        "\n".join(
            [
                "name: narrative_studio",
                "display_name: Narrative Test",
                "version: 0.2.0",
                "config:",
                f"  data_dir: {data_dir}",
                "  echo_source_path: ''",
            ]
        ),
        encoding="utf-8",
    )
    source_skills = (
        Path(__file__).resolve().parents[1]
        / "runtime"
        / "platform"
        / "plugins"
        / "bundled"
        / "narrative_studio"
        / "skills"
    )
    shutil.copytree(source_skills, plugin / "skills")
    return plugin


def test_factory_disable_persists_and_load_all_only_loads_enabled(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    activation = tmp_path / "data" / "plugins" / "workbench"
    _make_narrative_factory(bundled, tmp_path / "data" / "narrative-studio")

    first = PluginHub(
        plugin_dir=tmp_path / "external",
        bundled_plugin_dir=bundled,
        activation_root=activation,
        data_root=tmp_path / "data",
    )
    assert first.load_all() == ["narrative_studio"]
    disabled = first.disable_plugin("narrative_studio")
    assert disabled["installed"] is True
    assert disabled["enabled"] is False
    assert disabled["loaded"] is False

    restarted = PluginHub(
        plugin_dir=tmp_path / "external",
        bundled_plugin_dir=bundled,
        activation_root=activation,
        data_root=tmp_path / "data",
    )
    assert restarted.load_all() == []
    detail = restarted.get_plugin_detail("narrative_studio")
    assert detail is not None
    assert detail["state"] == "disabled"

    enabled = restarted.enable_plugin("narrative_studio")
    assert enabled["enabled"] is True
    assert enabled["loaded"] is True
    assert enabled["started"] is True


def test_factory_disable_removes_narrative_routes_and_enable_does_not_duplicate(
    tmp_path: Path,
) -> None:
    from collections import Counter

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    bundled = tmp_path / "bundled"
    data_root = tmp_path / "data"
    _make_narrative_factory(bundled, data_root / "narrative-studio")
    app = FastAPI()
    app.get("/host-route")(lambda: {"host": True})
    hub = PluginHub(
        plugin_dir=tmp_path / "external",
        bundled_plugin_dir=bundled,
        activation_root=data_root / "plugins" / "workbench",
        data_root=data_root,
        fastapi_app=app,
    )

    assert hub.load("narrative_studio") is not None

    def narrative_routes() -> Counter[tuple[str, tuple[str, ...]]]:
        return Counter(
            (
                path,
                tuple(sorted(operations)),
            )
            for path, operations in app.openapi()["paths"].items()
            if path.startswith("/api/plugins/narrative-studio")
        )

    original = narrative_routes()
    assert original
    client = TestClient(app)
    assert client.get("/api/plugins/narrative-studio/status").status_code == 200
    disabled = hub.disable_plugin("narrative_studio")
    assert disabled["loaded"] is False
    assert narrative_routes() == Counter()
    assert client.get("/api/plugins/narrative-studio/status").status_code == 404
    assert any(getattr(route, "path", None) == "/host-route" for route in app.routes)

    enabled = hub.enable_plugin("narrative_studio")
    assert enabled["loaded"] is True
    assert narrative_routes() == original
    assert client.get("/api/plugins/narrative-studio/status").status_code == 200


def test_uninstall_keeps_narrative_works_and_can_reinstall(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    data_root = tmp_path / "data"
    activation = data_root / "plugins" / "workbench"
    _make_narrative_factory(bundled, data_root / "narrative-studio")
    works = data_root / "narrative-studio"
    works.mkdir(parents=True)
    (works / "draft.txt").write_text("keep", encoding="utf-8")
    hub = PluginHub(
        plugin_dir=tmp_path / "external",
        bundled_plugin_dir=bundled,
        activation_root=activation,
        data_root=data_root,
    )
    assert hub.load("narrative_studio") is not None

    removed = hub.uninstall_plugin("narrative_studio")
    assert removed["installed"] is False
    assert removed["data"]["status"] == "kept"
    assert (works / "draft.txt").exists()
    assert hub.load("narrative_studio") is None

    restarted = PluginHub(
        plugin_dir=tmp_path / "external",
        bundled_plugin_dir=bundled,
        activation_root=activation,
        data_root=data_root,
    )
    assert restarted.load_all() == []
    detail = restarted.get_plugin_detail("narrative_studio")
    assert detail is not None and detail["state"] == "uninstalled"

    installed = restarted.install_plugin("narrative_studio")
    assert installed["installed"] is True
    assert installed["loaded"] is True
    assert (works / "draft.txt").exists()


def test_install_runtime_failure_rolls_back_persisted_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = tmp_path / "bundled"
    data_root = tmp_path / "data"
    activation = data_root / "plugins" / "workbench"
    _make_narrative_factory(bundled, data_root / "narrative-studio")
    hub = PluginHub(
        plugin_dir=tmp_path / "external",
        bundled_plugin_dir=bundled,
        activation_root=activation,
        data_root=data_root,
    )
    hub.uninstall_plugin("narrative_studio")
    monkeypatch.setattr(hub, "load", lambda _name: None)

    with pytest.raises(RuntimeError, match="failed to load"):
        hub.install_plugin("narrative_studio")

    state = hub._activation_store.state("narrative_studio")
    assert state["installed"] is False
    assert state["enabled"] is False


def test_unload_removes_only_routes_registered_by_plugin(tmp_path: Path) -> None:
    from fastapi import FastAPI

    app = FastAPI()
    app.get("/host-route")(lambda: {"host": True})
    plugin = tmp_path / "routeplug"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text("name: routeplug\n", encoding="utf-8")
    (plugin / "__init__.py").write_text(
        "from runtime.platform.plugins.plugin_base import ModulePlugin\n"
        "class RoutePlugin(ModulePlugin):\n"
        "    name = 'routeplug'\n"
        "    def register_routes(self):\n"
        "        @self.ctx.fastapi_app.get('/plugin-route')\n"
        "        def plugin_route():\n"
        "            return {'plugin': True}\n",
        encoding="utf-8",
    )
    hub = PluginHub(plugin_dir=tmp_path, fastapi_app=app)
    assert "/plugin-route" not in app.openapi()["paths"]

    assert hub.load("routeplug") is not None
    assert sum(getattr(route, "path", None) == "/plugin-route" for route in app.routes) == 1
    assert "/plugin-route" in app.openapi()["paths"]
    assert hub.unload("routeplug") is True
    assert not any(getattr(route, "path", None) == "/plugin-route" for route in app.routes)
    assert any(getattr(route, "path", None) == "/host-route" for route in app.routes)
    assert "/plugin-route" not in app.openapi()["paths"]

    assert hub.load("routeplug") is not None
    assert sum(getattr(route, "path", None) == "/plugin-route" for route in app.routes) == 1


def test_failed_load_rolls_back_skill_and_direct_route(tmp_path: Path) -> None:
    from fastapi import FastAPI

    class Registry:
        def __init__(self) -> None:
            self.names: set[str] = set()

        def register(self, skill, *, verify_tests: bool = False) -> None:
            del verify_tests
            self.names.add(skill.name)

        def unregister(self, name: str) -> None:
            self.names.discard(name)

    app = FastAPI()
    registry = Registry()
    plugin = tmp_path / "failplug"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text("name: failplug\n", encoding="utf-8")
    (plugin / "__init__.py").write_text(
        "from runtime.platform.plugins.plugin_base import ModulePlugin\n"
        "class DemoSkill:\n"
        "    name = 'failplug.skill'\n"
        "class FailPlugin(ModulePlugin):\n"
        "    name = 'failplug'\n"
        "    def on_load(self, ctx):\n"
        "        self.ctx = ctx\n"
        "        ctx.register_skill(DemoSkill())\n"
        "        @ctx.fastapi_app.get('/partial-route')\n"
        "        def partial_route():\n"
        "            return {}\n"
        "        raise RuntimeError('boom')\n",
        encoding="utf-8",
    )
    hub = PluginHub(plugin_dir=tmp_path, fastapi_app=app, skill_registry=registry)

    assert hub.load("failplug") is None
    assert registry.names == set()
    assert not any(getattr(route, "path", None) == "/partial-route" for route in app.routes)
    assert hub.get_plugin("failplug") is None

