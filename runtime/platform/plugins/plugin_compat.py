from __future__ import annotations

import logging
from typing import Any

from runtime.platform.plugins.plugin_loader import EchoPlugin, PluginContext
from runtime.platform.plugins.plugins import HookPoint, Plugin

_LOG = logging.getLogger(__name__)

# Adapter for the pre-EventBus plugin ABI. New plugins should implement
# EchoPlugin directly; this shim remains for externally installed plugins.
COMPATIBILITY_STATUS = "legacy-plugin-abi"

_HOOK_TO_EVENT: dict[str, str] = {
    HookPoint.ON_INIT: "plugin.on_init",
    HookPoint.ON_PLAN: "plugin.on_plan",
    HookPoint.ON_EXECUTE: "plugin.on_execute",
    HookPoint.ON_REFLECT: "plugin.on_reflect",
    HookPoint.ON_SHUTDOWN: "plugin.on_shutdown",
    HookPoint.ON_ERROR: "plugin.on_error",
    HookPoint.ON_SKILL_REGISTER: "plugin.on_skill_register",
    HookPoint.ON_CONFIG_LOAD: "plugin.on_config_load",
}


class PluginAdapter(EchoPlugin):
    def __init__(self, legacy: Plugin) -> None:
        self._legacy = legacy
        self.name = legacy.name
        self.version = legacy.version
        self.description = legacy.description
        self.author = legacy.author

    def on_load(self, ctx: PluginContext) -> None:
        from runtime.platform.plugins.plugins import HookContext

        hook_ctx = HookContext(hook=HookPoint.ON_INIT, data={"plugin_name": self.name})
        self._legacy.on_init(hook_ctx)

    def on_start(self, ctx: PluginContext) -> None:
        from runtime.platform.plugins.plugins import HookContext

        hook_ctx = HookContext(hook=HookPoint.ON_EXECUTE, data={"plugin_name": self.name})
        self._legacy.on_execute(hook_ctx)
        self._wire_hooks(ctx)

    def on_stop(self, ctx: PluginContext) -> None:
        from runtime.platform.plugins.plugins import HookContext

        hook_ctx = HookContext(hook=HookPoint.ON_SHUTDOWN, data={"plugin_name": self.name})
        self._legacy.on_shutdown(hook_ctx)

    def on_unload(self, ctx: PluginContext) -> None:
        pass

    def on_event(self, event: Any) -> None:
        event_type = getattr(event, "event_type", "")
        for hook_point, mapped_event in _HOOK_TO_EVENT.items():
            if event_type == mapped_event:
                from runtime.platform.plugins.plugins import HookContext

                hook_ctx = HookContext(hook=HookPoint(hook_point))
                handler = getattr(self._legacy, hook_point, None)
                if handler:
                    handler(hook_ctx)
                break

    def _wire_hooks(self, ctx: PluginContext) -> None:
        if ctx.event_bus is None:
            return
        meta = self._legacy.meta
        for hook in meta.hooks:
            event_type = _HOOK_TO_EVENT.get(hook)
            if event_type:
                ctx.event_bus.subscribe(event_type, self.on_event)


def adapt_legacy_plugin(legacy: Plugin) -> PluginAdapter:
    return PluginAdapter(legacy)


__all__ = ["PluginAdapter", "adapt_legacy_plugin"]
