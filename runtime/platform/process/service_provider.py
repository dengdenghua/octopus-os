from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


class ServiceProvider:
    def __init__(self) -> None:
        self._instances: dict[str, Any] = {}
        self._factories: dict[str, Callable[[], Any]] = {}
        self._lock = threading.Lock()

    def register_instance(self, key: str, instance: T) -> None:
        with self._lock:
            self._instances[key] = instance
            self._factories.pop(key, None)

    def register_factory(self, key: str, factory: Callable[[], T]) -> None:
        with self._lock:
            self._factories[key] = factory
            self._instances.pop(key, None)

    def get(self, key: str, *, default: T | None = None) -> T | None:
        with self._lock:
            if key in self._instances:
                return self._instances[key]
            if key in self._factories:
                instance = self._factories[key]()
                self._instances[key] = instance
                del self._factories[key]
                return instance
        return default

    def require(self, key: str) -> Any:
        # Services are stored untyped (``_instances: dict[str, Any]``), and
        # nothing in the call binds the TypeVar, so the honest return type is
        # Any — ``-> T`` was unbindable and mypy couldn't check callers.
        result = self.get(key)
        if result is None:
            raise KeyError(f"Service '{key}' not registered")
        return result

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._instances or key in self._factories

    def unregister(self, key: str) -> None:
        with self._lock:
            self._instances.pop(key, None)
            self._factories.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._instances.clear()
            self._factories.clear()


_PROVIDER: ServiceProvider | None = None
_PROVIDER_LOCK = threading.Lock()


def _bootstrap_services(provider: ServiceProvider) -> None:
    if not provider.has("eventbus"):
        from runtime.platform.process.eventbus import EventBus

        provider.register_instance("eventbus", EventBus.get())
    if not provider.has("statestore"):
        from runtime.platform.process.state import StateStore

        provider.register_instance("statestore", StateStore.get_instance())
    if not provider.has("plugin_loader"):
        from runtime.platform.plugins.plugin_loader import PluginLoader

        provider.register_instance("plugin_loader", PluginLoader.get())


def get_provider() -> ServiceProvider:
    global _PROVIDER
    if _PROVIDER is None:
        with _PROVIDER_LOCK:
            if _PROVIDER is None:
                _PROVIDER = ServiceProvider()
                _bootstrap_services(_PROVIDER)
    return _PROVIDER


def reset_provider_for_tests() -> None:
    global _PROVIDER
    with _PROVIDER_LOCK:
        if _PROVIDER is not None:
            _PROVIDER.clear()
        _PROVIDER = None
