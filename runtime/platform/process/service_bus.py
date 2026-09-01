"""ServiceBus — the typed "socket" of the composition layer.

Design doc: ``docs/architecture/blocks.md`` (§3.2 / §3.3).

What this adds over the existing pieces
---------------------------------------

* ``runtime/platform/process/service_provider.py`` is an untyped string-keyed
  service locator. This module keeps that primitive but wraps it with the
  composition semantics: blocks declare ``provides`` / ``consumes`` in a
  :class:`~runtime.platform.process.block_manifest.BlockManifest`, and the
  bus resolves a **topological load order** so a block only loads once every
  service it consumes is available.
* ``runtime/platform/plugins/plugin_loader.py`` already has lifecycle hooks
  (``on_load/on_start/on_stop/on_unload``); this module owns the *ordering*
  between those hooks (consumers first, providers first, reverse on unload).
* Event coupling stays on the existing duck-typed bus (``subscribe`` /
  ``emit``) so blocks communicate through events, never through direct
  cross-block imports.

Failure semantics (inherited from ``extensions.py`` / the plugin hub):
a block whose dependencies are missing is *blocked*, not fatal — the rest of
the graph still loads. A dependency *cycle* is a programming error and raises.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from runtime.platform.process.block_manifest import BlockManifest


class BlockDependencyCycleError(RuntimeError):
    """Raised when block ``consumes``/``provides`` form a cycle."""


@dataclass(frozen=True)
class ServiceBinding:
    """One provided service, owned by one block."""

    service: str
    plugin_name: str
    factory: Callable[[], Any] | None = None
    instance: Any = None

    def resolve(self) -> Any:
        if self.instance is not None:
            return self.instance
        if self.factory is None:
            raise RuntimeError(f"service {self.service!r} has no instance or factory")
        return self.factory()


def resolve_load_order(
    manifests: list[BlockManifest],
    *,
    available_services: set[str] | None = None,
) -> tuple[list[BlockManifest], list[BlockManifest]]:
    """Topologically order blocks so every ``consumes`` is satisfied.

    Returns ``(ordered, blocked)``:

    * ``ordered`` — a load order where each block appears only after every
      service it consumes has been provided by an earlier block.
    * ``blocked`` — blocks whose ``consumes`` reference a service no block
      in this set provides (missing provider). They are reported, not fatal.

    Raises :class:`BlockDependencyCycleError` when the remaining blocks form
    a cycle (each unloadable block's missing services are all provided by
    other unloadable blocks).

    ``available_services`` seeds the resolver with services already bound on
    the ServiceBus (kernel services such as ``memory`` / ``journal`` /
    ``model_router``). Without it, a block consuming a kernel service would
    be reported blocked even though the bus already provides it.

    Blocks with no consumes are always loadable and preserve input order
    among themselves (stable sort by name).
    """
    kernel = set(available_services or ())
    all_provided = {s for m in manifests for s in m.provides} | kernel
    remaining = list(manifests)
    provided_so_far = set(kernel)
    ordered: list[BlockManifest] = []

    while remaining:
        ready = [m for m in remaining if all(service in provided_so_far for service in m.consumes)]
        if not ready:
            break
        ready.sort(key=lambda m: m.name)
        for manifest in ready:
            remaining.remove(manifest)
            provided_so_far.update(manifest.provides)
            ordered.append(manifest)

    if not remaining:
        return ordered, []

    # Partition the stuck set: missing provider vs genuine cycle.
    blocked = [m for m in remaining if any(service not in all_provided for service in m.consumes)]
    cycle = [m for m in remaining if m not in blocked]
    if cycle:
        names = sorted(m.name for m in cycle)
        missing = sorted({s for m in cycle for s in m.consumes} - provided_so_far)
        raise BlockDependencyCycleError(
            f"block dependency cycle detected among {names}; unfulfilled consumes: {missing}"
        )
    return ordered, blocked


class ServiceBus:
    """Typed service bus + lifecycle orchestrator for composition blocks.

    Thread-safe. A block is bound with :meth:`bind` (which validates its
    manifest against the currently provided services) and removed with
    :meth:`unbind`. Event traffic is delegated to an injected duck-typed
    event bus (``subscribe(event_type, handler)`` / ``emit(event_type, **kw)``)
    so blocks never import each other.
    """

    def __init__(self, event_bus: Any = None) -> None:
        self._event_bus = event_bus
        self._bindings: dict[str, ServiceBinding] = {}
        self._lock = threading.RLock()
        self._plugins: dict[str, set[str]] = {}  # plugin_name -> provided services

    # ── Service registry ─────────────────────────────────────

    def register(
        self,
        service: str,
        plugin_name: str,
        *,
        instance: Any = None,
        factory: Callable[[], Any] | None = None,
    ) -> None:
        """Register one provided service (instance or lazy factory)."""
        if instance is None and factory is None:
            raise ValueError(f"{plugin_name}: service {service!r} needs instance or factory")
        with self._lock:
            self._bindings[service] = ServiceBinding(
                service=service, plugin_name=plugin_name, factory=factory, instance=instance
            )
            self._plugins.setdefault(plugin_name, set()).add(service)

    def get(self, service: str, default: Any = None) -> Any:
        """Resolve a service by name; returns ``default`` when unregistered."""
        with self._lock:
            binding = self._bindings.get(service)
            if binding is None:
                return default
            value = binding.resolve()
            # Memoize lazily-created factories: the first resolve materialises
            # the instance, later gets return the same object (singleton per
            # service, matching ServiceProvider's factory caching).
            if binding.factory is not None and value is not binding.instance:
                self._bindings[service] = ServiceBinding(
                    service=service,
                    plugin_name=binding.plugin_name,
                    instance=value,
                )
            return value

    def require(self, service: str) -> Any:
        """Resolve a service by name; raises ``KeyError`` when unregistered."""
        value = self.get(service)
        if value is None and service not in self._bindings:
            raise KeyError(f"service {service!r} not registered")
        return value

    def has(self, service: str) -> bool:
        with self._lock:
            return service in self._bindings

    # ── Block lifecycle ───────────────────────────────────────

    def bind(
        self,
        manifest: BlockManifest,
        *,
        instance: Any = None,
        factory: Callable[[], Any] | None = None,
    ) -> None:
        """Bind one block: validate its ``consumes`` then register its provides.

        ``instance``/``factory`` is the service provider object for the
        block's *first* provided service (or for ``kind='plugin'`` legacy
        plugins). Missing dependencies raise ``KeyError`` listing them —
        callers that want degrade-not-fail should use :meth:`can_bind` first
        and skip the block.
        """
        missing = [s for s in manifest.consumes if not self.has(s)]
        if missing:
            raise KeyError(f"{manifest.name}: missing services {sorted(missing)} (block blocked)")
        with self._lock:
            provided = list(manifest.provides)
            if provided and instance is None and factory is None:
                raise ValueError(
                    f"{manifest.name}: provides {provided} but no instance/factory given"
                )
            # One block = one provider object. The same instance/factory backs
            # every service the manifest provides, so consumers of any of them
            # resolve to the same block instance.
            for service in provided:
                self.register(
                    service,
                    manifest.name,
                    instance=instance,
                    factory=factory,
                )

    def can_bind(self, manifest: BlockManifest) -> tuple[bool, list[str]]:
        """Check whether ``manifest``'s consumes are all satisfiable now."""
        missing = [s for s in manifest.consumes if not self.has(s)]
        return (not missing), missing

    def unbind(self, plugin_name: str) -> list[str]:
        """Remove every service a block provided; returns the removed names."""
        with self._lock:
            removed = list(self._plugins.pop(plugin_name, set()))
            for service in removed:
                self._bindings.pop(service, None)
            return removed

    def bound_plugins(self) -> list[str]:
        with self._lock:
            return sorted(self._plugins)

    def provided_services(self) -> set[str]:
        """Names of every service currently bound on the bus (kernel + blocks)."""
        with self._lock:
            return set(self._bindings)

    # ── Event coupling (delegated to the injected bus) ────────

    def subscribe(self, event_type: str, handler: Callable[[Any], None]) -> None:
        if self._event_bus is None:
            return
        self._event_bus.subscribe(event_type, handler)

    def emit(self, event_type: str, **kwargs: Any) -> int:
        if self._event_bus is None:
            return 0
        return self._event_bus.emit(event_type, **kwargs)
