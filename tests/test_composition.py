"""Tests for the default composition-layer ServiceBus wiring."""

from __future__ import annotations

from runtime.memory.provider import JournalMemoryProvider, MemoryProvider
from runtime.platform.models.selector import ModelSelector
from runtime.platform.process.composition import build_default_service_bus


class _FakeJournal:
    def write(self, _event) -> None:
        pass

    def read_all(self, *, scope=None):
        return []

    def read_by_session(self, _session_id: str):
        return []


def test_default_bus_binds_journal_and_memory():
    journal = _FakeJournal()
    bus = build_default_service_bus(journal=journal)

    assert bus.has("journal")
    assert bus.has("memory")
    assert bus.require("journal") is journal
    memory = bus.require("memory")
    assert isinstance(memory, MemoryProvider)
    assert isinstance(memory, JournalMemoryProvider)
    assert bus.require("memory") is memory  # singleton


def test_default_bus_without_journal_still_works():
    bus = build_default_service_bus(journal=None)
    assert not bus.has("journal")
    assert not bus.has("memory")
    # require() still raises for unknown services (programming error);
    # get() with default is the degrade path.
    assert bus.get("missing") is None


def test_default_bus_exposes_model_router_service():
    bus = build_default_service_bus(journal=None)
    assert bus.has("model_router")
    selector = bus.require("model_router")
    assert isinstance(selector, ModelSelector)
    assert selector.select(role="r", default_model="gpt-5").model == "gpt-5"


def test_default_bus_respects_custom_model_selector():
    class Custom:
        def select(self, **kwargs):
            return "custom-model"

    bus = build_default_service_bus(journal=None, model_selector=Custom())
    assert bus.require("model_router").select(role="r", default_model="x") == "custom-model"


def test_default_bus_delegates_events_to_injected_bus():
    class FakeEventBus:
        def __init__(self):
            self.emitted = []

        def emit(self, event_type, **kwargs):
            self.emitted.append((event_type, kwargs))
            return 1

        def subscribe(self, *_args):
            return None

    event_bus = FakeEventBus()
    bus = build_default_service_bus(journal=_FakeJournal(), event_bus=event_bus)
    assert bus.emit("memory.recalled", thread_id="t") == 1
    assert event_bus.emitted == [("memory.recalled", {"thread_id": "t"})]

