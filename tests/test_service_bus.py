"""Tests for the composition-layer ServiceBus."""

from __future__ import annotations

import pytest

from runtime.platform.process.block_manifest import BlockManifest
from runtime.platform.process.service_bus import (
    BlockDependencyCycleError,
    ServiceBus,
    resolve_load_order,
)


def _manifest(name: str, provides: list[str] | None = None, consumes: list[str] | None = None):
    return BlockManifest(
        name=name,
        provides=provides or [],
        consumes=consumes or [],
    )


# ── resolve_load_order ──────────────────────────────────────


def test_independent_blocks_preserve_name_order():
    manifests = [_manifest("zeta"), _manifest("alpha")]
    ordered, blocked = resolve_load_order(manifests)
    assert blocked == []
    assert [m.name for m in ordered] == ["alpha", "zeta"]


def test_consumer_loads_after_provider():
    provider = _manifest("journal", provides=["journal"])
    consumer = _manifest("memory", consumes=["journal"])
    ordered, blocked = resolve_load_order([consumer, provider])
    assert blocked == []
    assert [m.name for m in ordered] == ["journal", "memory"]


def test_chain_of_three():
    a = _manifest("a", provides=["svc_a"])
    b = _manifest("b", provides=["svc_b"], consumes=["svc_a"])
    c = _manifest("c", consumes=["svc_b"])
    ordered, _ = resolve_load_order([c, a, b])
    assert [m.name for m in ordered] == ["a", "b", "c"]


def test_missing_provider_reports_blocked_not_fatal():
    ok = _manifest("ok")
    bad = _manifest("bad", consumes=["ghost"])
    ordered, blocked = resolve_load_order([ok, bad])
    assert [m.name for m in ordered] == ["ok"]
    assert [m.name for m in blocked] == ["bad"]


def test_kernel_services_seed_the_resolver():
    """A block consuming a kernel service (memory/journal/...) is not blocked."""
    consumer = _manifest("arm", provides=["arm.skills"], consumes=["memory"])
    # Without seeding, `memory` is not provided by any manifest → blocked.
    ordered, blocked = resolve_load_order([consumer])
    assert blocked == [consumer]
    # With the bus's bound services as seed, it loads immediately.
    ordered, blocked = resolve_load_order(
        [consumer],
        available_services={"memory", "journal"},
    )
    assert blocked == []
    assert [m.name for m in ordered] == ["arm"]


def test_cycle_raises():
    a = _manifest("a", provides=["x"], consumes=["y"])
    b = _manifest("b", provides=["y"], consumes=["x"])
    with pytest.raises(BlockDependencyCycleError, match="cycle"):
        resolve_load_order([a, b])


def test_partial_cycle_and_missing_mix():
    a = _manifest("a", provides=["x"], consumes=["y"])
    b = _manifest("b", provides=["y"], consumes=["x"])
    ghost = _manifest("ghost", consumes=["nope"])
    with pytest.raises(BlockDependencyCycleError):
        resolve_load_order([a, b, ghost])


# ── ServiceBus lifecycle ────────────────────────────────────


def test_register_and_get_instance():
    bus = ServiceBus()
    bus.register("memory", "echo.memory", instance=object())
    assert bus.has("memory")
    assert bus.get("memory") is not None
    assert bus.get("missing") is None
    with pytest.raises(KeyError):
        bus.require("missing")


def test_factory_is_lazy():
    calls = []

    def make():
        calls.append(1)
        return "svc"

    bus = ServiceBus()
    bus.register("svc", "p", factory=make)
    assert calls == []
    assert bus.require("svc") == "svc"
    assert len(calls) == 1
    # resolve caches the instance for subsequent gets
    assert bus.require("svc") == "svc"
    assert len(calls) == 1


def test_bind_validates_consumes():
    bus = ServiceBus()
    bus.register("journal", "echo.journal", instance={})
    manifest = _manifest("memory", provides=["memory"], consumes=["journal"])
    bus.bind(manifest, instance="memory-impl")
    assert bus.require("memory") == "memory-impl"

    blocked = _manifest("ghost", provides=["g"], consumes=["nowhere"])
    with pytest.raises(KeyError, match="missing services"):
        bus.bind(blocked, instance="x")


def test_can_bind_reports_missing():
    bus = ServiceBus()
    ok, missing = bus.can_bind(_manifest("m", consumes=["journal"]))
    assert ok is False
    assert missing == ["journal"]
    bus.register("journal", "echo.journal", instance={})
    ok, missing = bus.can_bind(_manifest("m", consumes=["journal"]))
    assert ok is True
    assert missing == []


def test_unbind_removes_provided_services():
    bus = ServiceBus()
    bus.register("a", "p", instance=1)
    bus.register("b", "p", instance=2)
    bus.register("c", "other", instance=3)
    removed = bus.unbind("p")
    assert sorted(removed) == ["a", "b"]
    assert not bus.has("a") and not bus.has("b")
    assert bus.has("c")
    assert bus.bound_plugins() == ["other"]


def test_bind_requires_provider_for_provides():
    bus = ServiceBus()
    manifest = _manifest("m", provides=["svc"])
    with pytest.raises(ValueError, match="no instance/factory"):
        bus.bind(manifest)


def test_events_delegate_to_injected_bus():
    class FakeBus:
        def __init__(self):
            self.handlers = {}
            self.emitted = []

        def subscribe(self, event_type, handler):
            self.handlers[event_type] = handler

        def emit(self, event_type, **kwargs):
            self.emitted.append((event_type, kwargs))
            return 1

    fake = FakeBus()
    bus = ServiceBus(event_bus=fake)
    handler = lambda _e: None  # noqa: E731
    bus.subscribe("turn.started", handler)
    assert fake.handlers["turn.started"] is handler
    assert bus.emit("turn.started", thread_id="t") == 1
    assert fake.emitted == [("turn.started", {"thread_id": "t"})]


def test_events_noop_without_bus():
    bus = ServiceBus()
    assert bus.emit("anything", x=1) == 0
    bus.subscribe("anything", lambda _e: None)  # must not raise

