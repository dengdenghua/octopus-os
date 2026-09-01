from __future__ import annotations

import contextlib
import importlib
import os
import sys
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HookPoint(StrEnum):
    ON_INIT = "on_init"
    ON_PLAN = "on_plan"
    ON_EXECUTE = "on_execute"
    ON_REFLECT = "on_reflect"
    ON_SHUTDOWN = "on_shutdown"
    ON_ERROR = "on_error"
    ON_SKILL_REGISTER = "on_skill_register"
    ON_CONFIG_LOAD = "on_config_load"


class HookContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    hook: HookPoint
    agent_id: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    error: Exception | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


class PluginMeta(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1)
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    hooks: list[HookPoint] = Field(default_factory=list)
    cli_commands: bool = False


class Plugin:
    name: str = "unnamed-plugin"
    version: str = "0.1.0"
    description: str = ""
    author: str = ""

    def on_init(self, ctx: HookContext) -> None:
        pass

    def on_plan(self, ctx: HookContext) -> None:
        pass

    def on_execute(self, ctx: HookContext) -> None:
        pass

    def on_reflect(self, ctx: HookContext) -> None:
        pass

    def on_shutdown(self, ctx: HookContext) -> None:
        pass

    def on_error(self, ctx: HookContext) -> None:
        pass

    def on_skill_register(self, ctx: HookContext) -> None:
        pass

    def on_config_load(self, ctx: HookContext) -> None:
        pass

    @property
    def meta(self) -> PluginMeta:
        hooks = []
        for hp in HookPoint:
            method = getattr(self, hp.value, None)
            if method and method.__func__ is not getattr(Plugin, hp.value, None):
                hooks.append(hp)
        return PluginMeta(
            name=self.name,
            version=self.version,
            description=self.description,
            author=self.author,
            hooks=hooks,
        )


HookCallback = Callable[[HookContext], None]


class PluginManager:
    def __init__(self, plugin_dir: str | Path | None = None) -> None:
        if plugin_dir is None:
            plugin_dir = Path(os.path.expanduser("~/.echo/plugins"))
        self._dir = Path(plugin_dir)
        self._plugins: dict[str, Plugin] = {}
        self._extra_hooks: dict[HookPoint, list[HookCallback]] = {}
        self._disabled: set[str] = set()

    @property
    def plugins(self) -> dict[str, Plugin]:
        return dict(self._plugins)

    def register(self, plugin: Plugin) -> None:
        if plugin.name in self._disabled:
            return
        if plugin.name in self._plugins:
            self.unregister(plugin.name)
        self._plugins[plugin.name] = plugin

    def unregister(self, name: str) -> None:
        self._plugins.pop(name, None)

    def disable(self, name: str) -> None:
        self._disabled.add(name)
        self._plugins.pop(name, None)

    def enable(self, name: str) -> None:
        self._disabled.discard(name)

    def add_hook(self, point: HookPoint, callback: HookCallback) -> None:
        self._extra_hooks.setdefault(point, []).append(callback)

    def fire(self, point: HookPoint, ctx: HookContext | None = None) -> HookContext:
        if ctx is None:
            ctx = HookContext(hook=point)
        ctx.data["hook_point"] = point.value

        for plugin in self._plugins.values():
            handler = getattr(plugin, point.value, None)
            if handler is None:
                continue
            try:
                handler(ctx)
            except Exception as exc:
                if point != HookPoint.ON_ERROR:
                    self.fire(
                        HookPoint.ON_ERROR,
                        HookContext(
                            hook=HookPoint.ON_ERROR,
                            error=exc,
                            data={"source_plugin": plugin.name, "original_hook": point.value},
                        ),
                    )

        for callback in self._extra_hooks.get(point, []):
            with contextlib.suppress(Exception):
                callback(ctx)

        return ctx

    def discover(self) -> list[str]:
        discovered: list[str] = []
        if not self._dir.exists():
            return discovered

        for item in sorted(self._dir.iterdir()):
            if item.is_dir() and (item / "__init__.py").exists():
                discovered.append(item.name)
            elif item.is_file() and item.suffix == ".py" and item.stem != "__init__":
                discovered.append(item.stem)

        return discovered

    def load(self, name: str) -> Plugin | None:
        if name in self._plugins:
            return self._plugins[name]

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
            plugin = self.load(name)
            if plugin is not None:
                loaded.append(name)
        return loaded

    def _load_from_dir(self, plugin_dir: Path, name: str) -> Plugin | None:
        init_file = plugin_dir / "__init__.py"
        if not init_file.exists():
            return None

        try:
            if str(self._dir) not in sys.path:
                sys.path.insert(0, str(self._dir))
            mod = importlib.import_module(f"{name}")
            plugin_cls = self._find_plugin_class(mod)
            if plugin_cls is None:
                return None
            plugin = plugin_cls()
            self.register(plugin)
            return plugin
        except (ImportError, AttributeError, OSError, ValueError, TypeError):
            return None

    def _load_from_file(self, file_path: Path, name: str) -> Plugin | None:
        try:
            spec = importlib.util.spec_from_file_location(name, str(file_path))
            if spec is None or spec.loader is None:
                return None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            plugin_cls = self._find_plugin_class(mod)
            if plugin_cls is None:
                return None
            plugin = plugin_cls()
            self.register(plugin)
            return plugin
        except (ImportError, TypeError, AttributeError, OSError, ValueError):
            return None

    @staticmethod
    def _find_plugin_class(mod: Any) -> type[Plugin] | None:
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if isinstance(attr, type) and issubclass(attr, Plugin) and attr is not Plugin:
                return attr
        return None

    def list_plugins(self) -> list[PluginMeta]:
        return [p.meta for p in self._plugins.values()]
