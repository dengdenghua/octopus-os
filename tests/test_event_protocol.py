"""Tests for the typed event-envelope protocol versioning (P4)."""

from __future__ import annotations

import pytest

from runtime.platform.process.eventbus import (
    CURRENT_EVENT_PROTOCOL_VERSION,
    DomainEvent,
    EventBus,
    FitnessComputed,
)


def test_default_protocol_version_stamped():
    event = DomainEvent(event_type="test.event")
    assert event.protocol_version == CURRENT_EVENT_PROTOCOL_VERSION


def test_subclasses_inherit_default_version():
    assert FitnessComputed(combined_score=0.5).protocol_version == CURRENT_EVENT_PROTOCOL_VERSION


def test_explicit_current_version_accepted():
    event = DomainEvent(
        event_type="test.event",
        protocol_version=CURRENT_EVENT_PROTOCOL_VERSION,
    )
    assert event.protocol_version == 1


def test_newer_protocol_version_rejected():
    with pytest.raises(ValueError, match="not supported"):
        DomainEvent(event_type="test.event", protocol_version=99)


def test_zero_protocol_version_rejected():
    with pytest.raises(ValueError):
        DomainEvent(event_type="test.event", protocol_version=0)


def test_bus_publish_preserves_protocol_version():
    """The in-process bus delivers the envelope with its protocol version."""
    EventBus.reset()
    bus = EventBus.get()
    received: list[DomainEvent] = []

    def on_event(event: DomainEvent) -> None:
        received.append(event)

    sub_id = bus.subscribe("fitness.computed", on_event)
    bus.publish(FitnessComputed(combined_score=0.9))
    bus.unsubscribe(sub_id)

    assert received
    assert received[0].protocol_version == CURRENT_EVENT_PROTOCOL_VERSION
    EventBus.reset()

