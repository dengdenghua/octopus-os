"""Implementation note."""

from __future__ import annotations

import threading

import pytest

from runtime.core.nerves import (
    AgentAdded,
    BudgetPressure,
    NervesEvent,
    SkillRegistered,
    TypedEventBus,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSubscribe:
    def test_subscribe_and_count(self):
        bus = TypedEventBus()
        bus.subscribe(SkillRegistered, lambda e: None)
        assert bus.subscriber_count(SkillRegistered) == 1
        assert bus.subscriber_count(AgentAdded) == 0

    def test_subscribe_same_handler_twice_is_idempotent(self):
        bus = TypedEventBus()

        def h(e):
            pass

        bus.subscribe(SkillRegistered, h)
        bus.subscribe(SkillRegistered, h)
        assert bus.subscriber_count(SkillRegistered) == 1

    def test_unsubscribe_returns_true_when_present(self):
        bus = TypedEventBus()

        def h(e):
            pass

        bus.subscribe(SkillRegistered, h)
        assert bus.unsubscribe(SkillRegistered, h) is True
        assert bus.subscriber_count(SkillRegistered) == 0

    def test_unsubscribe_returns_false_when_absent(self):
        bus = TypedEventBus()

        def h(e):
            pass

        assert bus.unsubscribe(SkillRegistered, h) is False

    def test_subscribe_non_event_type_rejected(self):
        bus = TypedEventBus()
        with pytest.raises(TypeError, match="NervesEvent"):
            bus.subscribe(str, lambda e: None)  # type: ignore[arg-type]

    def test_subscribe_non_callable_rejected(self):
        bus = TypedEventBus()
        with pytest.raises(TypeError, match="callable"):
            bus.subscribe(SkillRegistered, "not callable")  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPublish:
    def test_publish_calls_subscribers_in_order(self):
        bus = TypedEventBus()
        order: list[int] = []
        bus.subscribe(SkillRegistered, lambda e: order.append(1))
        bus.subscribe(SkillRegistered, lambda e: order.append(2))
        bus.subscribe(SkillRegistered, lambda e: order.append(3))
        n = bus.publish(SkillRegistered(skill_name="x"))
        assert n == 3
        assert order == [1, 2, 3]

    def test_publish_only_calls_matching_type(self):
        bus = TypedEventBus()
        seen_skill: list[str] = []
        seen_agent: list[str] = []
        bus.subscribe(SkillRegistered, lambda e: seen_skill.append(e.skill_name))
        bus.subscribe(AgentAdded, lambda e: seen_agent.append(e.agent_id))

        bus.publish(SkillRegistered(skill_name="s1"))
        bus.publish(AgentAdded(agent_id="a1"))
        bus.publish(SkillRegistered(skill_name="s2"))

        assert seen_skill == ["s1", "s2"]
        assert seen_agent == ["a1"]

    def test_publish_with_no_subscribers_returns_zero(self):
        bus = TypedEventBus()
        assert bus.publish(SkillRegistered(skill_name="x")) == 0

    def test_publish_non_event_rejected(self):
        bus = TypedEventBus()
        with pytest.raises(TypeError, match="NervesEvent"):
            bus.publish({"not": "an event"})  # type: ignore[arg-type]

    def test_publish_carries_full_event_data(self):
        bus = TypedEventBus()
        received: list[SkillRegistered] = []
        bus.subscribe(SkillRegistered, lambda e: received.append(e))
        bus.publish(
            SkillRegistered(
                skill_name="demo",
                trusted_source="skill://forged/abc",
                forged=True,
                candidate_id="sig1",
            )
        )
        assert received[0].skill_name == "demo"
        assert received[0].forged is True
        assert received[0].candidate_id == "sig1"


# ═══════════════════════════════════════════════════════════
# crash resilience
# ═══════════════════════════════════════════════════════════


class TestCrashResilience:
    def test_resilient_mode_swallows_exception(self, caplog):
        bus = TypedEventBus(crash_resilient=True)
        called = []

        def bad(e):
            raise RuntimeError("boom")

        def good(e):
            called.append(e)

        bus.subscribe(SkillRegistered, bad)
        bus.subscribe(SkillRegistered, good)
        n = bus.publish(SkillRegistered(skill_name="x"))
        # Implementation note.
        assert len(called) == 1
        # Implementation note.
        assert n == 1

    def test_strict_mode_raises(self):
        bus = TypedEventBus(crash_resilient=False)
        bus.subscribe(SkillRegistered, lambda e: (_ for _ in ()).throw(RuntimeError("x")))
        with pytest.raises(RuntimeError):
            bus.publish(SkillRegistered(skill_name="x"))


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestDecorator:
    def test_on_decorator(self):
        bus = TypedEventBus()
        received: list[str] = []

        @bus.on(BudgetPressure)
        def _(event: BudgetPressure) -> None:
            received.append(event.task_id)

        bus.publish(BudgetPressure(task_id="t1", percent_used=0.85))
        assert received == ["t1"]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestThreadSafety:
    def test_handler_can_unsubscribe_itself(self):
        """Implementation note."""
        bus = TypedEventBus()
        calls = []

        def self_unsubscribe(event):
            calls.append(event.skill_name)
            bus.unsubscribe(SkillRegistered, self_unsubscribe)

        bus.subscribe(SkillRegistered, self_unsubscribe)
        bus.publish(SkillRegistered(skill_name="once"))
        # Implementation note.
        bus.publish(SkillRegistered(skill_name="twice"))
        assert calls == ["once"]

    def test_concurrent_publish_and_subscribe(self):
        bus = TypedEventBus()
        events_seen = []
        lock = threading.Lock()

        def sub(e):
            with lock:
                events_seen.append(e.skill_name)

        bus.subscribe(SkillRegistered, sub)

        def publisher():
            for i in range(50):
                bus.publish(SkillRegistered(skill_name=f"s{i}"))

        threads = [threading.Thread(target=publisher) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Implementation note.
        assert len(events_seen) == 200


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestClear:
    def test_clear_removes_all(self):
        bus = TypedEventBus()
        bus.subscribe(SkillRegistered, lambda e: None)
        bus.subscribe(AgentAdded, lambda e: None)
        bus.clear()
        assert bus.subscriber_count(SkillRegistered) == 0
        assert bus.subscriber_count(AgentAdded) == 0


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestCustomEventType:
    def test_user_defined_event(self):
        class CacheInvalidated(NervesEvent):
            cache_name: str
            reason: str = ""

        bus = TypedEventBus()
        hits: list[str] = []
        bus.subscribe(CacheInvalidated, lambda e: hits.append(e.cache_name))
        bus.publish(CacheInvalidated(cache_name="skill_list", reason="forge"))
        assert hits == ["skill_list"]
