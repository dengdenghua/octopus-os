"""Default ServiceBus wiring for the composition layer.

Design doc: ``docs/architecture/blocks.md`` §3.3 / §4.

This is the "sockets panel" of the runtime: it binds the kernel services
that composition blocks (plugins, future arm/memory/router blocks) declare
in their ``consumes``. Today it registers the two memory-facing services;
as more blocks are extracted (model router, arms…), their default
implementations register here and the ServiceBus stays the only coupling
point.
"""

from __future__ import annotations

from typing import Any

from runtime.platform.process.service_bus import ServiceBus


def build_default_service_bus(
    *,
    journal: Any = None,
    event_bus: Any = None,
    model_selector: Any = None,
) -> ServiceBus:
    """Build the default runtime ServiceBus with kernel services bound.

    Parameters
    ----------
    journal :
        The append-only ``Journal`` instance (or anything with the journal
        surface). When provided, two services become available:
        ``journal`` (the raw store) and ``memory`` (a
        :class:`~runtime.memory.provider.JournalMemoryProvider` wrapper).
    event_bus :
        Optional duck-typed event bus; ServiceBus delegates ``emit`` /
        ``subscribe`` to it so blocks communicate by events, not imports.
    model_selector :
        Optional :class:`~runtime.platform.models.selector.ModelSelector`
        (defaults to :class:`~runtime.sensing.model_router.selector.DefaultModelSelector`).
        Exposed as the ``model_router`` service so blocks can ask "which
        model for this role/budget" without knowing the policy.

    Returns
    -------
    A ready-to-use :class:`ServiceBus`. Without ``journal`` it still works —
    it simply carries no memory services yet (degrade, don't crash).
    """
    bus = ServiceBus(event_bus=event_bus)
    if model_selector is None:
        from runtime.sensing.model_router.selector import DefaultModelSelector

        model_selector = DefaultModelSelector()
    bus.register("model_router", "runtime.model_router", instance=model_selector)
    if journal is not None:
        from runtime.memory.provider import JournalMemoryProvider

        bus.register("journal", "runtime.journal", instance=journal)
        bus.register("memory", "runtime.memory", instance=JournalMemoryProvider(journal))
    return bus
