"""Dynamic extension registry — hot-pluggable skill registration.

Extensions can be registered and torn down at runtime based on
configuration changes (workspace re-mounted, user switched projects,
feature flag toggled).

Key features
~~~~~~~~~~~~
- **Lifecycle management**: register → activate → deactivate → teardown
- **Dependency tracking**: skills can declare dependencies on other skills
- **Configuration-driven**: skills activate/deactivate based on config
- **Error isolation**: one skill's failure doesn't break others

Usage
~~~~~
    registry = ExtensionRegistry()

    # Register a skill
    registry.register(
        skill_id="computer_use",
        factory=create_computer_use_skill,
        config_key="skills.computer_use.enable",
    )

    # Activate all skills whose config is enabled
    await registry.activate_from_config(config)

    # Deactivate a specific skill
    await registry.deactivate("computer_use")
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

_logger = logging.getLogger(__name__)


class ExtensionState(StrEnum):
    """Lifecycle state of a registered extension."""

    REGISTERED = "registered"
    ACTIVATING = "activating"
    ACTIVE = "active"
    DEACTIVATING = "deactivating"
    DEACTIVATED = "deactivated"
    FAILED = "failed"


@dataclass
class ExtensionInfo:
    """Metadata about a registered extension."""

    skill_id: str
    display_name: str
    state: ExtensionState = ExtensionState.REGISTERED
    error: str | None = None
    disposables: list[Any] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    config_key: str | None = None


@dataclass
class ExtensionContext:
    """Context passed to extension activation functions.

    Provides access to global resources and lifecycle management.
    """

    skill_id: str
    config: dict[str, Any]
    global_state: dict[str, Any]
    subscriptions: list[Any] = field(default_factory=list)

    def subscribe(self, disposable: Any) -> None:
        """Add a disposable to be cleaned up on deactivation."""
        self.subscriptions.append(disposable)


# Factory type for extension creation
ExtensionFactory = Callable[
    [ExtensionContext],
    Awaitable[list[Any]],  # Returns list of disposables
]


class ExtensionRegistry:
    """Registry for dynamically loadable extensions/skills.

    Manages the lifecycle of registered extensions, allowing them
    to be activated and deactivated at runtime.
    """

    def __init__(self) -> None:
        self._extensions: dict[str, ExtensionInfo] = {}
        self._factories: dict[str, ExtensionFactory] = {}
        self._activation_promise: dict[str, asyncio.Future[None]] = {}
        self._global_state: dict[str, Any] = {}
        # Retained references for fire-and-forget tasks spawned by
        # ``unregister()``. Without this set, Python 3.12+ can GC the
        # task mid-await, cancelling the deactivation silently (see
        # https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task).
        self._pending_deactivations: set[asyncio.Task[None]] = set()

    @property
    def active_extensions(self) -> list[str]:
        """List of currently active extension IDs."""
        return [
            ext_id for ext_id, ext in self._extensions.items() if ext.state == ExtensionState.ACTIVE
        ]

    @property
    def failed_extensions(self) -> list[str]:
        """List of extensions that failed during activation."""
        return [
            ext_id for ext_id, ext in self._extensions.items() if ext.state == ExtensionState.FAILED
        ]

    def register(
        self,
        skill_id: str,
        display_name: str,
        factory: ExtensionFactory,
        *,
        config_key: str | None = None,
        dependencies: list[str] | None = None,
    ) -> None:
        """Register an extension for later activation.

        Args:
            skill_id: Unique identifier for the extension.
            display_name: Human-readable name.
            factory: Async function that creates the extension.
            config_key: Configuration key that controls activation.
            dependencies: List of skill IDs this extension depends on.
        """
        if skill_id in self._extensions:
            raise ValueError(f"Extension '{skill_id}' is already registered")

        self._extensions[skill_id] = ExtensionInfo(
            skill_id=skill_id,
            display_name=display_name,
            config_key=config_key,
            dependencies=dependencies or [],
        )
        self._factories[skill_id] = factory

        _logger.info("registered extension: %s (%s)", skill_id, display_name)

    def unregister(self, skill_id: str) -> None:
        """Unregister an extension, deactivating it first if active."""
        if skill_id not in self._extensions:
            return

        ext = self._extensions[skill_id]
        if ext.state in (ExtensionState.ACTIVE, ExtensionState.FAILED):
            # Deactivate first. Retain the task reference so Python's GC
            # doesn't collect it mid-await.
            task = asyncio.create_task(self.deactivate(skill_id))
            self._pending_deactivations.add(task)
            task.add_done_callback(self._pending_deactivations.discard)

        del self._extensions[skill_id]
        self._factories.pop(skill_id, None)
        self._activation_promise.pop(skill_id, None)

        _logger.info("unregistered extension: %s", skill_id)

    async def activate(
        self,
        skill_id: str,
        config: dict[str, Any] | None = None,
    ) -> bool:
        """Activate a registered extension.

        Args:
            skill_id: The extension to activate.
            config: Configuration dict for the extension.

        Returns:
            True if activation succeeded, False otherwise.
        """
        if skill_id not in self._extensions:
            _logger.error("extension not registered: %s", skill_id)
            return False

        ext = self._extensions[skill_id]
        if ext.state == ExtensionState.ACTIVE:
            return True

        if ext.state == ExtensionState.ACTIVATING:
            # Wait for in-progress activation
            if skill_id in self._activation_promise:
                try:
                    await self._activation_promise[skill_id]
                    return True
                except (TypeError, ValueError, RuntimeError, OSError):
                    return False
            return False

        # Check dependencies
        for dep_id in ext.dependencies:
            if dep_id not in self._extensions:
                ext.state = ExtensionState.FAILED
                ext.error = f"dependency '{dep_id}' not registered"
                _logger.error("extension %s failed: %s", skill_id, ext.error)
                return False
            dep_ext = self._extensions[dep_id]
            if dep_ext.state != ExtensionState.ACTIVE:
                ext.state = ExtensionState.FAILED
                ext.error = f"dependency '{dep_id}' is not active"
                _logger.error("extension %s failed: %s", skill_id, ext.error)
                return False

        ext.state = ExtensionState.ACTIVATING
        ext.error = None

        loop = asyncio.get_running_loop()
        promise: asyncio.Future[None] = loop.create_future()
        self._activation_promise[skill_id] = promise

        try:
            factory = self._factories[skill_id]
            context = ExtensionContext(
                skill_id=skill_id,
                config=config or {},
                global_state=self._global_state,
            )

            disposables = await factory(context)
            ext.disposables = disposables
            ext.state = ExtensionState.ACTIVE

            _logger.info("extension activated: %s", skill_id)
            promise.set_result(None)
            return True

        except (TypeError, ValueError, RuntimeError, OSError, ImportError) as e:
            ext.state = ExtensionState.FAILED
            ext.error = str(e)
            _logger.exception("extension activation failed: %s", skill_id)
            promise.set_exception(e)
            return False

    async def deactivate(self, skill_id: str) -> bool:
        """Deactivate an active extension.

        Args:
            skill_id: The extension to deactivate.

        Returns:
            True if deactivation succeeded, False otherwise.
        """
        if skill_id not in self._extensions:
            return False

        ext = self._extensions[skill_id]
        if ext.state in (ExtensionState.REGISTERED, ExtensionState.DEACTIVATED):
            return True

        if ext.state == ExtensionState.DEACTIVATING:
            return False

        ext.state = ExtensionState.DEACTIVATING

        try:
            # Dispose all subscriptions
            for disposable in reversed(ext.disposables):
                try:
                    if hasattr(disposable, "dispose"):
                        disposable.dispose()
                    elif hasattr(disposable, "close"):
                        disposable.close()
                    elif asyncio.iscoroutinefunction(disposable):
                        await disposable()
                except Exception as exc:
                    _logger.warning(
                        "failed to dispose extension resource: %s — %s",
                        skill_id,
                        exc,
                    )

            ext.disposables.clear()
            ext.state = ExtensionState.DEACTIVATED

            _logger.info("extension deactivated: %s", skill_id)
            return True

        except Exception as exc:
            ext.error = str(exc)
            ext.state = ExtensionState.FAILED
            _logger.exception("extension deactivation failed: %s", skill_id)
            return False

    async def activate_from_config(
        self,
        config: dict[str, Any],
        changed_keys: set[str] | None = None,
    ) -> dict[str, bool]:
        """Activate/deactivate extensions based on configuration.

        Args:
            config: Full configuration dict.
            changed_keys: Set of config keys that changed. If None,
                all extensions are evaluated.

        Returns:
            Dict of skill_id -> success status.
        """
        results: dict[str, bool] = {}

        for skill_id, ext in self._extensions.items():
            if ext.config_key is None:
                continue

            if changed_keys is not None and ext.config_key not in changed_keys:
                continue

            is_enabled = self._get_nested_config(config, ext.config_key, False)

            if is_enabled and ext.state in (
                ExtensionState.REGISTERED,
                ExtensionState.FAILED,
                ExtensionState.DEACTIVATED,
            ):
                results[skill_id] = await self.activate(skill_id, config)

            elif not is_enabled and ext.state == ExtensionState.ACTIVE:
                results[skill_id] = await self.deactivate(skill_id)

        return results

    def get_status(self) -> dict[str, ExtensionInfo]:
        """Get status of all registered extensions."""
        return dict(self._extensions)

    @staticmethod
    def _get_nested_config(
        config: dict[str, Any],
        key: str,
        default: Any,
    ) -> Any:
        """Get a nested config value using dot notation."""
        parts = key.split(".")
        current: Any = config
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return default
        return current if current is not None else default
