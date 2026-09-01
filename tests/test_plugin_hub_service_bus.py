"""Integration: PluginHub + ServiceBus composition layer (P1).

Covers the additive contract:
  * with a ServiceBus injected, ``load_all`` resolves a topological order
    from plugin ``provides``/``consumes``;
  * missing dependencies block a plugin (skipped, never fatal);
  * a dependency cycle skips the affected plugins without crashing;
  * unload unbinds provided services;
  * without a ServiceBus, the hub keeps legacy discovery-order behaviour.
"""

from __future__ import annotations

import sys
from pathlib import Path

from runtime.platform.plugins.plugin_hub import PluginHub
from runtime.platform.process.block_manifest import BlockManifest
from runtime.platform.process.service_bus import ServiceBus

_MODULE_TEMPLATE = """\
from runtime.platform.plugins.plugin_base import ModulePlugin

{body}
"""


def _write_plugin(
    root: Path,
    name: str,
    *,
    provides: list[str] | None = None,
    consumes: list[str] | None = None,
    body: str = "",
) -> Path:
    """Create a minimal plugin dir with plugin.yaml + __init__.py."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    lines = [f"name: {name}", "version: 1.0.0", "description: test"]
    if provides:
        lines.append("provides:")
        lines += [f"  - {p}" for p in provides]
    if consumes:
        lines.append("consumes:")
        lines += [f"  - {c}" for c in consumes]
    (d / "plugin.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if not body:
        body = (
            f"class {name.title().replace('_', '')}Plugin(ModulePlugin):\n"
            f"    name = '{name}'\n"
            "    def register_skills(self):\n        pass\n"
            "    def register_channels(self):\n        pass\n"
            "    def register_routes(self):\n        pass\n"
        )
    (d / "__init__.py").write_text(
        _MODULE_TEMPLATE.format(body=body),
        encoding="utf-8",
    )
    # Ensure a fresh import per test (plugin module name == plugin name).
    sys.modules.pop(name, None)
    return d


def _make_hub(tmp_path: Path, service_bus: ServiceBus | None = None) -> PluginHub:
    return PluginHub(plugin_dir=tmp_path, service_bus=service_bus)


def test_consumer_loads_after_provider_in_topo_order(tmp_path: Path) -> None:
    """Even when the consumer sorts first by name, topology loads the provider first."""
    bus = ServiceBus()
    _write_plugin(
        tmp_path,
        "a_consumer",
        consumes=["shared.svc"],
        body=(
            "class AConsumerPlugin(ModulePlugin):\n"
            "    name = 'a_consumer'\n"
            "    def on_load(self, ctx):\n"
            "        self.resolved = ctx.service_bus.require('shared.svc')\n"
            "        self.ctx = ctx\n"
            "        super().on_load(ctx)\n"
        ),
    )
    _write_plugin(
        tmp_path,
        "z_provider",
        provides=["shared.svc"],
        body=(
            "class ZProviderPlugin(ModulePlugin):\n"
            "    name = 'z_provider'\n"
            "    def on_load(self, ctx):\n"
            "        self.ctx = ctx\n"
            "        super().on_load(ctx)\n"
        ),
    )

    hub = _make_hub(tmp_path, bus)
    loaded = hub.load_all()
    assert "z_provider" in loaded and "a_consumer" in loaded

    provider = hub.get_plugin("z_provider")
    consumer = hub.get_plugin("a_consumer")
    assert consumer.resolved is provider  # consumer saw the provider instance
    assert bus.require("shared.svc") is provider


def test_blocked_plugin_is_skipped_not_fatal(tmp_path: Path) -> None:
    bus = ServiceBus()
    _write_plugin(tmp_path, "ok_plugin", provides=["ok.svc"])
    _write_plugin(tmp_path, "ghost_plugin", consumes=["missing.svc"])

    hub = _make_hub(tmp_path, bus)
    loaded = hub.load_all()
    assert "ok_plugin" in loaded
    assert "ghost_plugin" not in loaded
    assert bus.has("ok.svc")
    assert not bus.has("missing.svc")

    # Direct load of the blocked plugin is refused with a clear reason.
    assert hub.load("ghost_plugin") is None


def test_cycle_skips_both_without_crash(tmp_path: Path) -> None:
    bus = ServiceBus()
    _write_plugin(tmp_path, "cyc_a", provides=["a.svc"], consumes=["b.svc"])
    _write_plugin(tmp_path, "cyc_b", provides=["b.svc"], consumes=["a.svc"])

    hub = _make_hub(tmp_path, bus)
    loaded = hub.load_all()
    assert loaded == []
    assert hub.get_plugin("cyc_a") is None
    assert hub.get_plugin("cyc_b") is None
    assert not bus.has("a.svc") and not bus.has("b.svc")


def test_unload_unbinds_services(tmp_path: Path) -> None:
    bus = ServiceBus()
    _write_plugin(tmp_path, "svc_plug", provides=["svc.x", "svc.y"])
    hub = _make_hub(tmp_path, bus)
    assert hub.load_all() == ["svc_plug"]
    assert bus.has("svc.x") and bus.has("svc.y")

    assert hub.unload("svc_plug") is True
    assert not bus.has("svc.x") and not bus.has("svc.y")
    assert bus.bound_plugins() == []


def test_legacy_hub_without_service_bus_unchanged(tmp_path: Path) -> None:
    """No ServiceBus => every plugin loads in discovery order, no blocking."""
    _write_plugin(tmp_path, "a_consumer", consumes=["missing.svc"])
    _write_plugin(tmp_path, "z_provider", provides=["shared.svc"])

    hub = _make_hub(tmp_path, None)  # legacy path
    loaded = hub.load_all()
    assert loaded == ["a_consumer", "z_provider"]  # name order, both load


def test_manifest_maps_to_block_via_from_plugin_manifest(tmp_path: Path) -> None:
    d = _write_plugin(tmp_path, "mix_plug", provides=["p.one", "p.two"], consumes=["c.one"])
    data = (d / "plugin.yaml").read_text(encoding="utf-8")
    assert "consumes:" in data
    manifest = BlockManifest.from_yaml(d / "plugin.yaml")
    assert manifest.name == "mix_plug"
    assert manifest.provides == ["p.one", "p.two"]
    assert manifest.consumes == ["c.one"]

