"""Tests for the dev-time BlockWatcher (P4 · hot reload)."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from runtime.execution.suckers.registry import SkillRegistry
from runtime.platform.plugins.plugin_hub import PluginHub
from runtime.platform.process.block_watcher import BlockWatcher
from runtime.platform.process.service_bus import ServiceBus

NAME = "watcher_plug"

PLUGIN_BODY = """\
from runtime.platform.plugins.plugin_base import ModulePlugin

class WatcherPlugin(ModulePlugin):
    name = "{name}"
    def register_skills(self):
        pass
    def register_channels(self):
        pass
    def register_routes(self):
        pass
"""


def _write_plugin(root: Path, name: str = NAME) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.yaml").write_text(
        f"name: {name}\nkind: arm\nversion: 1.0.0\ndescription: watcher test\n"
        f"provides:\n  - {name}.skills\n",
        encoding="utf-8",
    )
    (d / "__init__.py").write_text(
        PLUGIN_BODY.format(name=name),
        encoding="utf-8",
    )
    sys.modules.pop(name, None)
    return d


def _make_env(root: Path):
    registry = SkillRegistry()
    bus = ServiceBus()
    hub = PluginHub(plugin_dir=root, skill_registry=registry, service_bus=bus)
    return hub, bus


def test_scan_loads_added_block(tmp_path: Path):
    hub, bus = _make_env(tmp_path)
    _write_plugin(tmp_path)
    watcher = BlockWatcher(tmp_path, hub)

    result = watcher.scan()
    assert result["loaded"] == [NAME]
    assert hub.get_plugin(NAME) is not None
    assert bus.has(f"{NAME}.skills")


def test_first_scan_baselines_existing_blocks(tmp_path: Path):
    hub, bus = _make_env(tmp_path)
    _write_plugin(tmp_path)
    assert hub.load_all() == [NAME]

    watcher = BlockWatcher(tmp_path, hub)
    result = watcher.scan()
    # No reload: the watcher baselines already-loaded blocks.
    assert result == {"loaded": [], "reloaded": [], "unloaded": []}
    assert hub.get_plugin(NAME) is not None


def test_touch_triggers_reload(tmp_path: Path):
    hub, bus = _make_env(tmp_path)
    plugin_dir = _write_plugin(tmp_path)
    watcher = BlockWatcher(tmp_path, hub)
    watcher.scan()
    before = hub.get_plugin(NAME)
    assert before is not None

    # Bump the entrypoint mtime (ensure a distinct timestamp).
    time.sleep(0.01)
    (plugin_dir / "__init__.py").write_text(
        PLUGIN_BODY.format(name=NAME) + "\n# touched\n",
        encoding="utf-8",
    )

    result = watcher.scan()
    assert result["reloaded"] == [NAME]
    after = hub.get_plugin(NAME)
    assert after is not None
    assert after is not before  # fresh instance after reload
    assert bus.has(f"{NAME}.skills")  # services re-bound


def test_removed_block_is_unloaded(tmp_path: Path):
    hub, bus = _make_env(tmp_path)
    plugin_dir = _write_plugin(tmp_path)
    watcher = BlockWatcher(tmp_path, hub)
    watcher.scan()
    assert hub.get_plugin(NAME) is not None

    # Remove the plugin dir (safe delete via shutil).
    import shutil

    shutil.rmtree(plugin_dir)

    result = watcher.scan()
    assert result["unloaded"] == [NAME]
    assert hub.get_plugin(NAME) is None
    assert not bus.has(f"{NAME}.skills")


def test_no_change_is_idempotent(tmp_path: Path):
    hub, bus = _make_env(tmp_path)
    _write_plugin(tmp_path)
    watcher = BlockWatcher(tmp_path, hub)
    watcher.scan()
    assert watcher.scan() == {"loaded": [], "reloaded": [], "unloaded": []}


def test_run_loop_respects_stop_event(tmp_path: Path):
    hub, bus = _make_env(tmp_path)
    _write_plugin(tmp_path)
    watcher = BlockWatcher(tmp_path, hub, interval=0.05)
    stop = threading.Event()

    thread = threading.Thread(target=watcher.run, args=(stop,), daemon=True)
    thread.start()
    time.sleep(0.15)
    stop.set()
    thread.join(timeout=2.0)

    assert hub.get_plugin(NAME) is not None
    assert not thread.is_alive()

