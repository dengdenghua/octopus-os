from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_LOG = logging.getLogger("echo.platform.plugin_loader")


class PluginState(StrEnum):
    DISCOVERED = "discovered"
    LOADED = "loaded"
    STARTED = "started"
    STOPPED = "stopped"
    ERROR = "error"


class PluginLifecycleState(StrEnum):
    """User-facing package/runtime lifecycle shared by every plugin kind."""

    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    INSTALLED = "installed"
    ENABLING = "enabling"
    ENABLED = "enabled"
    DISABLING = "disabling"
    DISABLED = "disabled"
    UNINSTALLING = "uninstalling"
    UPDATE_AVAILABLE = "update_available"
    BROKEN = "broken"
    INCOMPATIBLE = "incompatible"


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=1)
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    requires: list[str] = Field(default_factory=list)
    provides: list[str] = Field(default_factory=list)
    subscribes: list[str] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    contributes: dict[str, Any] = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=list)
    host_api: str | None = None
    priority: int = 0


@dataclass
class PluginInstance:
    name: str
    manifest: PluginManifest
    plugin_dir: Path
    state: PluginState = PluginState.DISCOVERED
    instance: Any = None
    error: str = ""


class EchoPlugin:
    name: str = "unnamed"
    version: str = "0.1.0"
    description: str = ""
    author: str = ""

    def on_load(self, ctx: PluginContext) -> None:
        pass

    def on_start(self, ctx: PluginContext) -> None:
        pass

    def on_stop(self, ctx: PluginContext) -> None:
        pass

    def on_unload(self, ctx: PluginContext) -> None:
        pass

    def on_event(self, event: Any) -> None:
        pass


@dataclass
class PluginContext:
    plugin_name: str = ""
    state_store: Any = None
    event_bus: Any = None
    config: dict[str, Any] = field(default_factory=dict)
    logger: Any = None

    def get_state(self, key: str, default: Any = None) -> Any:
        if self.state_store is None:
            return default
        val = self.state_store.get(key, namespace=f"plugin.{self.plugin_name}")
        return val if val is not None else default

    def set_state(self, key: str, value: Any) -> None:
        if self.state_store is not None:
            self.state_store.set(key, value, namespace=f"plugin.{self.plugin_name}")

    def subscribe(self, event_type: str, handler: Callable | None = None) -> None:
        if self.event_bus is None:
            return
        if handler is None:
            handler = self._default_event_handler
        self.event_bus.subscribe(event_type, handler)

    def emit(self, event_type: str, **kwargs: Any) -> int:
        if self.event_bus is None:
            return 0
        return self.event_bus.emit(event_type, agent_id=f"plugin.{self.plugin_name}", **kwargs)

    def _default_event_handler(self, event: Any) -> None:
        if hasattr(self, "on_event") and callable(self.on_event):
            self.on_event(event)


class PluginLoader:
    _instance: PluginLoader | None = None
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def get(cls) -> PluginLoader:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            if cls._instance is not None:
                cls._instance.stop_all()
            cls._instance = None

    def __init__(
        self,
        plugin_dir: str | Path | None = None,
        *,
        state_store: Any = None,
        event_bus: Any = None,
    ) -> None:
        if plugin_dir is None:
            plugin_dir = Path(os.path.expanduser("~/.echo/plugins"))
        self._dir = Path(plugin_dir)
        self._plugins: dict[str, PluginInstance] = {}
        self._sub_ids: dict[str, list[int]] = {}
        self._state_store = state_store
        self._event_bus = event_bus
        self._lock = threading.RLock()

    @property
    def plugins(self) -> dict[str, PluginInstance]:
        return dict(self._plugins)

    def _get_state_store(self) -> Any:
        if self._state_store is not None:
            return self._state_store
        try:
            from runtime.platform.process.state import get_statestore

            return get_statestore()
        except Exception as _exc:
            _LOG.debug("statestore lookup failed: %s", _exc)
            return None

    def _get_event_bus(self) -> Any:
        if self._event_bus is not None:
            return self._event_bus
        try:
            from runtime.platform.process.eventbus import get_eventbus

            return get_eventbus()
        except Exception as _exc:
            _LOG.debug("eventbus lookup failed: %s", _exc)
            return None

    def discover(self) -> list[str]:
        discovered: list[str] = []
        if not self._dir.exists():
            return discovered

        for item in sorted(self._dir.iterdir()):
            if (
                item.is_dir()
                and (item / "__init__.py").exists()
                or item.is_dir()
                and (item / "plugin.yaml").exists()
            ):
                discovered.append(item.name)
            elif item.is_file() and item.suffix == ".py" and item.stem != "__init__":
                discovered.append(item.stem)

        return discovered

    def load(self, name: str) -> PluginInstance | None:
        with self._lock:
            if name in self._plugins:
                existing = self._plugins[name]
                if existing.state in (PluginState.LOADED, PluginState.STARTED):
                    return existing

        plugin_dir = self._dir / name
        if plugin_dir.is_dir():
            return self._load_from_dir(plugin_dir, name)

        single_file = self._dir / f"{name}.py"
        if single_file.exists():
            return self._load_from_file(single_file, name)

        return None

    def load_all(self) -> list[str]:
        loaded: list[str] = []
        for name in self.discover():
            result = self.load(name)
            if result is not None:
                loaded.append(name)
        return loaded

    def start(self, name: str) -> bool:
        with self._lock:
            pi = self._plugins.get(name)
            if pi is None or pi.state != PluginState.LOADED:
                return False

        ctx = self._build_context(name)
        try:
            if pi.instance and hasattr(pi.instance, "on_start"):
                pi.instance.on_start(ctx)
            pi.state = PluginState.STARTED

            self._wire_events(name, pi)

            _LOG.info("plugin started: %s v%s", name, pi.manifest.version)
            return True
        except Exception as exc:
            pi.state = PluginState.ERROR
            pi.error = str(exc)
            _LOG.warning("plugin start failed: %s: %s", name, exc)
            return False

    def start_all(self) -> list[str]:
        started: list[str] = []
        for name, pi in list(self._plugins.items()):
            if pi.state == PluginState.LOADED and self.start(name):
                started.append(name)
        return started

    def stop(self, name: str) -> bool:
        with self._lock:
            pi = self._plugins.get(name)
            if pi is None or pi.state != PluginState.STARTED:
                return False

        ctx = self._build_context(name)
        try:
            if pi.instance and hasattr(pi.instance, "on_stop"):
                pi.instance.on_stop(ctx)
            pi.state = PluginState.STOPPED

            self._unwire_events(name)

            return True
        except Exception as exc:
            pi.state = PluginState.ERROR
            pi.error = str(exc)
            return False

    def stop_all(self) -> list[str]:
        stopped: list[str] = []
        for name, pi in list(self._plugins.items()):
            if pi.state == PluginState.STARTED and self.stop(name):
                stopped.append(name)
        return stopped

    def unload(self, name: str) -> bool:
        with self._lock:
            pi = self._plugins.pop(name, None)
            if pi is None:
                return False

        ctx = self._build_context(name)
        if pi.instance and hasattr(pi.instance, "on_unload"):
            try:
                pi.instance.on_unload(ctx)
            except Exception as _exc:
                _LOG.debug("plugin on_unload failed: %s", _exc)

        self._unwire_events(name)
        return True

    def status(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for name, pi in self._plugins.items():
            result[name] = {
                "state": pi.state.value,
                "version": pi.manifest.version,
                "description": pi.manifest.description,
                "error": pi.error,
                "subscribes": pi.manifest.subscribes,
                "provides": pi.manifest.provides,
            }
        return result

    def _load_from_dir(self, plugin_dir: Path, name: str) -> PluginInstance | None:
        manifest = self._load_manifest(plugin_dir, name)
        if manifest is None:
            return None

        init_file = plugin_dir / "__init__.py"
        if not init_file.exists():
            return None

        try:
            if str(self._dir) not in sys.path:
                sys.path.insert(0, str(self._dir))
            mod = importlib.import_module(name)
            plugin_cls = self._find_plugin_class(mod)
            if plugin_cls is None:
                return None

            instance = plugin_cls()
            pi = PluginInstance(
                name=name,
                manifest=manifest,
                plugin_dir=plugin_dir,
                state=PluginState.LOADED,
                instance=instance,
            )

            ctx = self._build_context(name)
            if hasattr(instance, "on_load"):
                instance.on_load(ctx)

            with self._lock:
                self._plugins[name] = pi

            _LOG.info("plugin loaded: %s v%s", name, manifest.version)
            return pi
        except Exception as exc:
            _LOG.warning("plugin load failed: %s: %s", name, exc)
            pi = PluginInstance(
                name=name,
                manifest=manifest,
                plugin_dir=plugin_dir,
                state=PluginState.ERROR,
                error=str(exc),
            )
            with self._lock:
                self._plugins[name] = pi
            return pi

    def _load_from_file(self, file_path: Path, name: str) -> PluginInstance | None:
        manifest = PluginManifest(name=name)
        try:
            spec = importlib.util.spec_from_file_location(name, str(file_path))
            if spec is None or spec.loader is None:
                return None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            plugin_cls = self._find_plugin_class(mod)
            if plugin_cls is None:
                return None

            instance = plugin_cls()
            if hasattr(instance, "name") and instance.name != "unnamed":
                manifest = PluginManifest(
                    name=instance.name,
                    version=getattr(instance, "version", "0.1.0"),
                    description=getattr(instance, "description", ""),
                    author=getattr(instance, "author", ""),
                )

            pi = PluginInstance(
                name=name,
                manifest=manifest,
                plugin_dir=file_path.parent,
                state=PluginState.LOADED,
                instance=instance,
            )

            ctx = self._build_context(name)
            if hasattr(instance, "on_load"):
                instance.on_load(ctx)

            with self._lock:
                self._plugins[name] = pi

            _LOG.info("plugin loaded: %s v%s", name, manifest.version)
            return pi
        except Exception as exc:
            _LOG.warning("plugin load failed: %s: %s", name, exc)
            pi = PluginInstance(
                name=name,
                manifest=manifest,
                plugin_dir=file_path.parent,
                state=PluginState.ERROR,
                error=str(exc),
            )
            with self._lock:
                self._plugins[name] = pi
            return pi

    def _load_manifest(self, plugin_dir: Path, name: str) -> PluginManifest | None:
        yaml_path = plugin_dir / "plugin.yaml"
        json_path = plugin_dir / "plugin.json"

        if yaml_path.exists():
            try:
                import yaml

                data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                return PluginManifest(**data)
            except Exception as _exc:
                _LOG.debug("eventbus unsubscribe failed: %s", _exc)

        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                return PluginManifest(**data)
            except Exception as _exc:
                _LOG.debug("json manifest load failed: %s", _exc)

        init_path = plugin_dir / "__init__.py"
        if init_path.exists():
            try:
                spec = importlib.util.spec_from_file_location(
                    f"{name}._manifest_check", str(init_path)
                )
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    plugin_cls = self._find_plugin_class(mod)
                    if plugin_cls:
                        inst = plugin_cls()
                        return PluginManifest(
                            name=getattr(inst, "name", name),
                            version=getattr(inst, "version", "0.1.0"),
                            description=getattr(inst, "description", ""),
                            author=getattr(inst, "author", ""),
                        )
            except Exception as _exc:
                _LOG.debug("yaml manifest load failed: %s", _exc)

        return PluginManifest(name=name)

    def _find_plugin_class(self, mod: Any) -> type[EchoPlugin] | None:
        for attr_name in dir(mod):
            if attr_name.startswith("_"):
                continue
            attr = getattr(mod, attr_name)
            if isinstance(attr, type) and issubclass(attr, EchoPlugin) and attr is not EchoPlugin:
                return attr
        return None

    def _build_context(self, name: str) -> PluginContext:
        return PluginContext(
            plugin_name=name,
            state_store=self._get_state_store(),
            event_bus=self._get_event_bus(),
            logger=logging.getLogger(f"echo.plugin.{name}"),
        )

    def _wire_events(self, name: str, pi: PluginInstance) -> None:
        bus = self._get_event_bus()
        if bus is None or not pi.manifest.subscribes:
            return

        sub_ids: list[int] = []
        for event_type in pi.manifest.subscribes:
            if pi.instance and hasattr(pi.instance, "on_event"):
                sid = bus.subscribe(event_type, pi.instance.on_event)
                sub_ids.append(sid)

        if sub_ids:
            self._sub_ids[name] = sub_ids

        try:
            from runtime.platform.process.eventbus import PluginLoadedEvent

            bus.publish(
                PluginLoadedEvent(
                    event_type="plugin.loaded",
                    plugin_name=name,
                    plugin_version=pi.manifest.version,
                )
            )
        except Exception as _exc:
            _LOG.debug("plugin loaded event publish failed: %s", _exc)

    def _unwire_events(self, name: str) -> None:
        bus = self._get_event_bus()
        sub_ids = self._sub_ids.pop(name, [])
        if bus is None:
            return
        for sid in sub_ids:
            try:
                bus.unsubscribe(sid)
            except Exception as _exc:
                _LOG.debug("eventbus unsubscribe failed: %s", _exc)


def get_plugin_loader() -> PluginLoader:
    return PluginLoader.get()


__all__ = [
    "EchoPlugin",
    "PluginContext",
    "PluginLoader",
    "PluginManifest",
    "PluginInstance",
    "PluginState",
    "get_plugin_loader",
]
