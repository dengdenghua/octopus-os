from __future__ import annotations

from types import SimpleNamespace

from runtime.core.hearts.gill_pump import GillCache, GillHeartPump, retrieval_gill_enabled
from runtime.core.nerves.bus import AgentAdded, TypedEventBus
from runtime.execution.suckers import SkillRegistry
from runtime.memory.hemolymph import ContextComposer
from runtime.platform.models import ContextSegment, ParsedIntent
from runtime.platform.process import event_bridge


class _SignalBus:
    def __init__(self) -> None:
        self.handlers = []

    def subscribe(self, topic, handler) -> None:
        self.handlers.append((topic, handler))


class _HookRegistry:
    def __init__(self) -> None:
        self.hooks = []

    def add_hook(self, point, handler) -> None:
        self.hooks.append((point, handler))


def test_signal_bridge_install_is_idempotent(monkeypatch):
    emitted = []
    monkeypatch.setattr(
        event_bridge,
        "get_eventbus",
        lambda: SimpleNamespace(emit=lambda *args, **kwargs: emitted.append((args, kwargs))),
    )
    signal_bus = _SignalBus()

    event_bridge.bridge_signal_bus_to_eventbus(signal_bus)
    event_bridge.bridge_signal_bus_to_eventbus(signal_bus)

    assert len(signal_bus.handlers) == 1
    signal_bus.handlers[0][1](SimpleNamespace(topic="demo", publisher="a", payload={"ok": True}))
    assert emitted[0][0] == ("signal.demo",)


def test_typed_and_hook_bridges_are_idempotent(monkeypatch):
    emitted = []
    monkeypatch.setattr(
        event_bridge,
        "get_eventbus",
        lambda: SimpleNamespace(emit=lambda *args, **kwargs: emitted.append((args, kwargs))),
    )
    typed = TypedEventBus()
    typed.AgentAdded = AgentAdded
    event_bridge.bridge_typed_bus_to_eventbus(typed)
    event_bridge.bridge_typed_bus_to_eventbus(typed)
    assert typed.subscriber_count(AgentAdded) == 1

    hooks = _HookRegistry()
    event_bridge.bridge_hook_registry_to_eventbus(hooks)
    event_bridge.bridge_hook_registry_to_eventbus(hooks)
    assert len(hooks.hooks) == 8


def test_gill_cache_is_bounded_and_pump_stops_cleanly():
    cache = GillCache()
    segment = ContextSegment(bucket="history", content="x", tokens_estimated=1)
    calls = []
    pump = GillHeartPump(
        cache=cache,
        compress_fn=lambda: calls.append("compress") or [segment] * 30,
        interval_s=0.01,
    )
    pump.start()
    assert pump.is_running
    pump._stop_event.wait(0.05)
    pump.stop()

    assert not pump.is_running
    assert calls
    assert len(cache.get_compressed()) == 20


def test_gill_pump_survives_worker_errors():
    cache = GillCache()
    pump = GillHeartPump(cache=cache, compress_fn=lambda: 1 / 0, interval_s=0.01)
    pump.start()
    pump._stop_event.wait(0.02)
    pump.stop()
    assert not pump.is_running


def test_context_composer_uses_only_matching_fresh_gill_memory(monkeypatch):
    cache = GillCache()
    composer = ContextComposer(
        registry=SkillRegistry(),
        journal=SimpleNamespace(),
        gill_cache=cache,
        gill_max_age_s=10,
    )
    segment = ContextSegment(bucket="memory", content="prefetched", tokens_estimated=2)
    calls = []

    def _prefetch(**kwargs):
        calls.append(kwargs)
        return [segment]

    monkeypatch.setattr(composer, "prefetch_memory_segments", _prefetch)
    intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="x")

    first = composer.compose(intent, relevant_skills=[], history_cutoff_n=5)
    second = composer.compose(intent, relevant_skills=[], history_cutoff_n=5)
    changed_window = composer.compose(intent, relevant_skills=[], history_cutoff_n=6)

    assert len(calls) == 2
    assert any(item.content == "prefetched" for item in first.segments)
    assert any(item.content == "prefetched" for item in second.segments)
    assert any(item.content == "prefetched" for item in changed_window.segments)


def test_gill_memory_rejects_wrong_key_and_stale_entry():
    cache = GillCache()
    segment = ContextSegment(bucket="memory", content="x", tokens_estimated=1)
    cache.set_memory([segment], "right")
    assert cache.get_memory("right", max_age_s=10) == [segment]
    assert cache.get_memory("wrong", max_age_s=10) == []
    cache.last_retrieved_ts = 0
    assert cache.get_memory("right", max_age_s=10) == []


def test_retrieval_gill_is_default_on_with_explicit_kill_switch(monkeypatch):
    monkeypatch.delenv("ECHO_GILL_RETRIEVAL", raising=False)
    assert retrieval_gill_enabled() is True
    monkeypatch.setenv("ECHO_GILL_RETRIEVAL", "0")
    assert retrieval_gill_enabled() is False
    monkeypatch.setenv("ECHO_GILL_RETRIEVAL", "off")
    assert retrieval_gill_enabled() is False

