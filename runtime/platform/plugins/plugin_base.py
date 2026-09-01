"""Plugin base classes for the pluggable module (PluginHub) architecture.

A plugin is a directory under ``~/.echo/plugins/<name>/`` with a
``plugin.yaml`` manifest and a ``ModulePlugin`` subclass in ``__init__.py``.

Three progressive registration layers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Layer 1 (minimal)
    Register skill(s) via ``register_skills()`` → ``SkillRegistry``.
    Suitable for pure tool/utility plugins.

Layer 2 (standard)
    Layer 1 + register channel(s) via ``register_channels()`` → ``ChannelManager``.
    Suitable for business plugins with message channels.

Layer 3 (complete)
    Layer 2 + register API routes via ``register_routes()`` → FastAPI app
    + optional frontend config UI component (``config_ui_component``).
    Suitable for plugins with their own management panel.

Naming convention
~~~~~~~~~~~~~~~~~
- Skill name: ``<plugin_name>.<skill_name>`` (e.g. ``openproject.list_projects``)
- Channel ID: ``<plugin_name>_<channel_name>`` (e.g. ``openproject_notifications``)
- API route: ``/api/plugins/<plugin_name>/...``
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from abc import ABC
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

# ── Capability declaration ─────────────────────────────────────


@dataclass
class ProvidedCapability:
    """A capability the plugin provides, shown on the frontend plugin page."""

    type: str  # "skill" | "channel" | "api" | "config_ui"
    name: str
    description: str = ""


# ── Plugin runtime context ─────────────────────────────────────


@dataclass
class ModuleContext:
    """Runtime context injected into a plugin when it is loaded.

    Provides access to external services (SkillRegistry, ChannelManager,
    FastAPI app, event bus) and the plugin's own persisted config.
    """

    plugin_name: str
    plugin_dir: str
    manifest: Any  # PluginManifest from plugin_loader
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    # Injected external services (set by PluginHub before on_load)
    skill_registry: Any = None  # runtime.execution.suckers.registry.SkillRegistry
    channel_manager: Any = None  # runtime.adapters.channels.manager.ChannelManager
    fastapi_app: Any = None  # FastAPI application
    event_bus: Any = None  # runtime.platform.process.eventbus.EventBus
    service_bus: Any = None  # runtime.platform.process.service_bus.ServiceBus
    tool_registry: Any = None  # runtime.execution.arms.tool_registry.ToolRegistry
    prompt_registry: Any = None  # runtime.platform.prompts.registry.PromptRegistry
    hook_registry: Any = None  # runtime.safety.hooks.registry.HookRegistry
    jobs_registry: Any = None  # runtime.execution.jobs.registry.LocalJobRegistry
    contribution_registry: Any = None  # descriptor-oriented Echo capability seams

    # Plugin's own persisted config (from plugin.yaml ``config`` field)
    config: dict[str, Any] = field(default_factory=dict)

    # Tracking for cleanup on unload
    _registered_skill_names: list[str] = field(default_factory=list)
    _registered_channel_ids: list[str] = field(default_factory=list)
    _registration_disposers: list[Callable[[], Any]] = field(default_factory=list)
    _provided_capabilities: list[ProvidedCapability] = field(default_factory=list)
    _jobs_lifecycle_registered: bool = False
    _pending_cleanup_tasks: set[asyncio.Task[Any]] = field(default_factory=set)

    @property
    def job_owner(self) -> str:
        """Stable owner key for every background job created by this plugin."""

        return f"plugin:{self.plugin_name}"

    @property
    def provided_capabilities(self) -> list[ProvidedCapability]:
        """Fresh snapshot of concrete contributions registered by the plugin."""

        return list(self._provided_capabilities)

    def register_cleanup(
        self,
        disposer: Callable[[], Any],
        *,
        capability_type: str | None = None,
        name: str | None = None,
        description: str = "",
    ) -> Callable[[], Any]:
        """Attach a registration to this plugin's unload transaction.

        Cleanup runs in reverse registration order.  This mirrors a resource
        stack: a tool registered after its provider is removed before the
        provider itself, and jobs are drained before their controller token is
        detached.  The same path is also used when ``on_load`` fails halfway.
        """

        if not callable(disposer):
            raise TypeError("plugin registration disposer must be callable")
        self._registration_disposers.append(disposer)
        if capability_type and name:
            self._provided_capabilities.append(
                ProvidedCapability(
                    type=capability_type,
                    name=name,
                    description=description,
                )
            )
        return disposer

    def register_skill(self, skill: Any) -> None:
        """Register a Skill with the SkillRegistry."""
        if self.skill_registry is not None:
            self.skill_registry.register(skill, verify_tests=False)
            self._registered_skill_names.append(skill.name)

    def register_channel(self, channel: Any) -> None:
        """Register a Channel with the ChannelManager."""
        if self.channel_manager is not None:
            self.channel_manager.register(channel)
            self._registered_channel_ids.append(channel.channel_id)

    def register_tool_provider(
        self,
        provider_id: str,
        display_name: str,
        *,
        feature_flags: list[str] | None = None,
    ) -> Any:
        """Register Echo Native tool-provider metadata owned by this plugin."""

        if self.tool_registry is None:
            raise RuntimeError("Echo tool registry is unavailable")
        provider = self.tool_registry.register_provider(
            provider_id,
            display_name,
            feature_flags=feature_flags,
        )
        self.register_cleanup(
            lambda: self.tool_registry.unregister_provider(provider_id),
            capability_type="tool_provider",
            name=provider_id,
            description=display_name,
        )
        return provider

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[[dict[str, Any]], Any],
        **options: Any,
    ) -> Callable[[], None]:
        """Register an Echo tool whose exact definition is removed on unload."""

        if self.tool_registry is None:
            raise RuntimeError("Echo tool registry is unavailable")
        disposer = self.tool_registry.register_tool(
            name,
            description,
            input_schema,
            handler,
            **options,
        )
        return self.register_cleanup(
            disposer,
            capability_type="tool",
            name=name,
            description=description,
        )

    def register_tool_pipeline_handler(
        self,
        stage: str,
        handler: Callable[..., Any],
    ) -> Callable[[], None]:
        """Contribute one disposable handler to the Echo tool pipeline."""

        if self.tool_registry is None:
            raise RuntimeError("Echo tool registry is unavailable")
        methods = {
            "will_call": self.tool_registry.on_will_call_tool,
            "did_call": self.tool_registry.on_did_call_tool,
            "pre_execute": self.tool_registry.on_pre_execute,
            "execute": self.tool_registry.on_execute,
            "post_execute": self.tool_registry.on_post_execute,
            "result": self.tool_registry.on_result,
        }
        register = methods.get(stage)
        if register is None:
            raise ValueError(f"unknown Echo tool pipeline stage: {stage}")
        disposer = register(handler)
        return self.register_cleanup(
            disposer,
            capability_type="tool_pipeline",
            name=f"{self.plugin_name}.{stage}",
        )

    def register_prompt_section(self, name: str, **options: Any) -> Callable[[], None]:
        if self.prompt_registry is None:
            raise RuntimeError("Echo prompt registry is unavailable")
        disposer = self.prompt_registry.register_section(name, **options)
        return self.register_cleanup(
            disposer,
            capability_type="prompt_section",
            name=name,
        )

    def register_prompt_context(self, name: str, **options: Any) -> Callable[[], None]:
        if self.prompt_registry is None:
            raise RuntimeError("Echo prompt registry is unavailable")
        disposer = self.prompt_registry.register_context(name, **options)
        return self.register_cleanup(
            disposer,
            capability_type="prompt_context",
            name=name,
        )

    def register_prompt_variable(
        self,
        name: str,
        provider: Callable[[str | None], str | None],
        **options: Any,
    ) -> Callable[[], None]:
        if self.prompt_registry is None:
            raise RuntimeError("Echo prompt registry is unavailable")
        disposer = self.prompt_registry.register_variable(name, provider, **options)
        return self.register_cleanup(
            disposer,
            capability_type="prompt_variable",
            name=name,
        )

    def register_hook(
        self,
        event_type: type,
        handler: Callable[..., Any],
    ) -> Callable[[], None]:
        """Register one runtime hook and bind its lifetime to this plugin."""

        if self.hook_registry is None:
            raise RuntimeError("Echo hook registry is unavailable")
        disposer = self.hook_registry.register(event_type, handler)
        return self.register_cleanup(
            disposer,
            capability_type="hook",
            name=f"{self.plugin_name}.{event_type.__name__}",
        )

    def start_job(self, spec: Any) -> str:
        """Start a background job that is drained when this plugin unloads."""

        if self.jobs_registry is None:
            raise RuntimeError("Echo jobs registry is unavailable")
        if not self._jobs_lifecycle_registered:
            detach = self.jobs_registry.attach_controller(self.job_owner)
            self.register_cleanup(detach)
            self.register_cleanup(
                lambda: self.jobs_registry.dispose_owned(self.job_owner),
                capability_type="jobs",
                name=self.job_owner,
            )
            self._jobs_lifecycle_registered = True
        return self.jobs_registry.start(replace(spec, owner=self.job_owner))

    def register_contribution(
        self,
        kind: str,
        name: str,
        value: Any,
        *,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Callable[[], None]:
        """Register a descriptor-oriented Echo contribution.

        Use this for agents, workflows, model providers, UI surfaces,
        renderers, commands, and settings schemas. Executable tools, prompts,
        hooks, and jobs use their specialised methods above.
        """

        if self.contribution_registry is None:
            raise RuntimeError("Echo contribution registry is unavailable")
        disposer = self.contribution_registry.register(
            kind=kind,
            name=name,
            owner=self.plugin_name,
            value=value,
            description=description,
            metadata=metadata,
        )
        return self.register_cleanup(
            disposer,
            capability_type=kind,
            name=name,
            description=description,
        )

    def register_agent(
        self,
        name: str,
        descriptor: Any,
        **options: Any,
    ) -> Callable[[], None]:
        return self.register_contribution("agent", name, descriptor, **options)

    def register_workflow(
        self,
        name: str,
        descriptor: Any,
        **options: Any,
    ) -> Callable[[], None]:
        return self.register_contribution("workflow", name, descriptor, **options)

    def register_model_provider(
        self,
        name: str,
        provider: Any,
        **options: Any,
    ) -> Callable[[], None]:
        return self.register_contribution("model_provider", name, provider, **options)

    def register_ui_surface(
        self,
        name: str,
        descriptor: Any,
        **options: Any,
    ) -> Callable[[], None]:
        return self.register_contribution("ui_surface", name, descriptor, **options)

    def cleanup_registrations(self) -> None:
        """Unregister every contribution owned by this plugin.

        Dynamic Echo contributions are disposed first in reverse order, then
        legacy skill/channel registrations are removed.  Awaitable cleanup
        (notably job draining) is completed synchronously when the lifecycle is
        invoked from a worker thread; when already inside an event loop it is
        scheduled on that loop and any failure is contained and logged.
        """

        for disposer in reversed(self._registration_disposers):
            with contextlib.suppress(Exception):
                result = disposer()
                if inspect.isawaitable(result):
                    self._finish_awaitable_cleanup(result)
        self._registration_disposers.clear()
        self._provided_capabilities.clear()
        self._jobs_lifecycle_registered = False

        if self.skill_registry is not None:
            for skill_name in self._registered_skill_names:
                with contextlib.suppress(
                    Exception
                ):  # best-effort plugin teardown; one bad skill shouldn't block the rest
                    self.skill_registry.unregister(skill_name)
            self._registered_skill_names.clear()

        if self.channel_manager is not None:
            for ch_id in self._registered_channel_ids:
                with contextlib.suppress(Exception):  # best-effort channel removal during teardown
                    self.channel_manager._channels.pop(ch_id, None)
            self._registered_channel_ids.clear()

    def _finish_awaitable_cleanup(self, awaitable: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(awaitable)
            return

        task = loop.create_task(awaitable)
        self._pending_cleanup_tasks.add(task)

        def report_failure(done: asyncio.Task[Any]) -> None:
            self._pending_cleanup_tasks.discard(done)
            with contextlib.suppress(asyncio.CancelledError):
                error = done.exception()
                if error is not None:
                    self.logger.warning(
                        "plugin %s async cleanup failed: %s",
                        self.plugin_name,
                        error,
                    )

        task.add_done_callback(report_failure)

    async def wait_for_cleanup(self) -> None:
        """Wait for asynchronous teardown scheduled on the current loop."""

        pending = list(self._pending_cleanup_tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


# ── Plugin base class ──────────────────────────────────────────


class ModulePlugin(ABC):  # noqa: B024
    """Base class for a pluggable module plugin.

    Subclasses **must** provide ``name`` and override ``register_skills()``.
    Optionally override ``register_channels()``, ``register_routes()``,
    ``on_load()``, ``on_start()``, ``on_stop()``, ``on_unload()``,
    and set ``config_ui_component``.

    Lifecycle (driven by PluginHub)::

        on_load(ctx)   → register_skills / register_channels / register_routes
        on_start(ctx)  → start background tasks, open connections
        on_stop(ctx)   → stop background tasks, close connections
        on_unload(ctx) → cleanup
    """

    # ── Metadata (class-level defaults, overridden by plugin.yaml) ──
    name: str = "unnamed-module"
    display_name: str = ""
    version: str = "0.1.0"
    description: str = ""
    author: str = ""

    def __init__(self) -> None:
        self.ctx: ModuleContext | None = None

    # ── Lifecycle hooks ─────────────────────────────────────────

    def on_load(self, ctx: ModuleContext) -> None:
        """Called after the plugin is loaded and context is injected.

        The default implementation calls ``register_skills()``,
        ``register_channels()``, and ``register_routes()``.
        Override to customise the registration order or add logic.
        """
        self.ctx = ctx
        self.register_skills()
        self.register_channels()
        self.register_routes()
        self.register_echo()

    def on_start(self, ctx: ModuleContext) -> None:  # noqa: B027
        """Called when the plugin is started (after all loading).

        Suitable for establishing connections, starting background tasks.
        """

    def on_stop(self, ctx: ModuleContext) -> None:  # noqa: B027
        """Called when the plugin is stopped.

        Suitable for closing connections, stopping background tasks.
        """

    def on_unload(self, ctx: ModuleContext) -> None:  # noqa: B027
        """Called when the plugin is unloaded.

        Suitable for final cleanup.
        """

    # ── Registration hooks (subclasses override) ────────────────

    def register_skills(self) -> None:  # noqa: B027
        """Register the skills this plugin provides.

        Use ``self.ctx.register_skill(skill)`` to register each skill.
        Skill names should follow the ``<plugin_name>.<name>`` convention.
        """

    def register_channels(self) -> None:  # noqa: B027
        """Register the channels this plugin provides.

        Use ``self.ctx.register_channel(channel)`` to register each channel.
        Channel IDs should follow the ``<plugin_name>_<name>`` convention.
        """

    def register_routes(self) -> None:  # noqa: B027
        """Register FastAPI routes for this plugin.

        Access the FastAPI app via ``self.ctx.fastapi_app``.
        Routes should be mounted under ``/api/plugins/<plugin_name>/...``.
        """

    def register_echo(self) -> None:  # noqa: B027
        """Register Echo tools, prompts, hooks, jobs, agents, or workflows.

        Contributions should use the methods on ``self.ctx`` so PluginHub can
        roll them back after a failed load and dispose them atomically during
        disable or uninstall.

        The default delegates to the legacy ``register_dsh`` hook so plugins
        written before the engine-identity migration continue to load.
        """
        self.register_dsh()

    def register_dsh(self) -> None:  # noqa: B027
        """Legacy contribution hook retained for existing third-party plugins.

        New plugins must override :meth:`register_echo`. DSH is an
        implementation-lineage reference, not an Echo runtime identity.
        """

    # ── Frontend configuration UI ───────────────────────────────

    @property
    def config_ui_component(self) -> str | None:
        """Return the relative path to a frontend config UI component.

        The path is relative to the plugin directory.
        Return ``None`` to let the frontend render a generic schema-based form.
        """
        return None

    # ── Introspection ───────────────────────────────────────────

    @property
    def capabilities(self) -> list[ProvidedCapability]:
        """Auto-detect which capabilities this plugin provides."""
        caps: list[ProvidedCapability] = []
        if self.__class__.register_skills is not ModulePlugin.register_skills:
            caps.append(
                ProvidedCapability(
                    type="skill",
                    name=f"{self.name}.skills",
                    description="Registered skills",
                )
            )
        if self.__class__.register_channels is not ModulePlugin.register_channels:
            caps.append(
                ProvidedCapability(
                    type="channel",
                    name=f"{self.name}.channel",
                    description="Registered channels",
                )
            )
        if self.__class__.register_routes is not ModulePlugin.register_routes:
            caps.append(
                ProvidedCapability(
                    type="api",
                    name=f"{self.name}.api",
                    description="Custom API routes",
                )
            )
        if self.config_ui_component is not None:
            caps.append(
                ProvidedCapability(
                    type="config_ui",
                    name=f"{self.name}.config_ui",
                    description="Custom configuration UI",
                )
            )
        if (
            self.__class__.register_echo is not ModulePlugin.register_echo
            or self.__class__.register_dsh is not ModulePlugin.register_dsh
        ):
            caps.append(
                ProvidedCapability(
                    type="echo",
                    name=f"{self.name}.echo",
                    description="Echo Native runtime contributions",
                )
            )
        if self.ctx is not None:
            caps.extend(self.ctx.provided_capabilities)
        return caps


__all__ = [
    "ModuleContext",
    "ModulePlugin",
    "ProvidedCapability",
]
