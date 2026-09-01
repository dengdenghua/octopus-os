"""Implementation note."""

from __future__ import annotations

import threading
import time
from typing import cast

import pytest
from pydantic import ValidationError
from runtime.platform.models import ArmId
from runtime.safety.chromatophores import (
    STANDARD_TOPICS,
    TOPIC_ALERT_BUDGET,
    TOPIC_ARM_BUSY,
    TOPIC_ARM_IDLE,
    TOPIC_ARM_MAILBOX,
    TOPIC_SUCKER_GRABBED,
    BoidsArbitrator,
    ResourceClaim,
    SignalBus,
    SignalEvent,
)

# ─── SignalBus ─────────────────────────────────────────────


class TestSignalBus:
    def test_publish_returns_signal_event(self):
        bus = SignalBus()
        ev = bus.publish(TOPIC_ARM_BUSY, {"task_id": "t1"}, publisher="arm:a1")
        assert isinstance(ev, SignalEvent)
        assert ev.topic == TOPIC_ARM_BUSY
        assert ev.payload == {"task_id": "t1"}
        assert ev.publisher == "arm:a1"
        assert ev.ts is not None

    def test_subscribe_handler_called_on_match(self):
        bus = SignalBus()
        seen: list[SignalEvent] = []
        bus.subscribe(TOPIC_ARM_BUSY, seen.append)
        bus.publish(TOPIC_ARM_BUSY, {"k": 1})
        assert len(seen) == 1
        assert seen[0].topic == TOPIC_ARM_BUSY
        assert seen[0].payload["k"] == 1

    def test_topic_pattern_wildcard(self):
        bus = SignalBus()
        seen: list[SignalEvent] = []
        bus.subscribe("arm.*", seen.append)
        bus.publish(TOPIC_ARM_BUSY, {})
        bus.publish(TOPIC_ARM_IDLE, {})
        bus.publish(TOPIC_ALERT_BUDGET, {})  # Implementation note.
        topics = [e.topic for e in seen]
        assert TOPIC_ARM_BUSY in topics
        assert TOPIC_ARM_IDLE in topics
        assert TOPIC_ALERT_BUDGET not in topics
        assert len(seen) == 2

    def test_multiple_subscribers_all_receive(self):
        bus = SignalBus()
        a: list[SignalEvent] = []
        b: list[SignalEvent] = []
        c: list[SignalEvent] = []
        bus.subscribe(TOPIC_ARM_BUSY, a.append)
        bus.subscribe("arm.*", b.append)
        bus.subscribe("*", c.append)
        bus.publish(TOPIC_ARM_BUSY, {})
        assert len(a) == 1
        assert len(b) == 1
        assert len(c) == 1

    def test_handler_exception_does_not_block_others(self):
        bus = SignalBus()
        seen: list[SignalEvent] = []

        def bad(_ev: SignalEvent) -> None:
            raise RuntimeError("boom")

        bus.subscribe(TOPIC_ARM_BUSY, bad)
        bus.subscribe(TOPIC_ARM_BUSY, seen.append)
        bus.publish(TOPIC_ARM_BUSY, {})
        # Implementation note.
        assert len(seen) == 1
        # Implementation note.
        errs = bus.handler_errors
        assert len(errs) == 1
        assert "RuntimeError" in errs[0]
        assert "boom" in errs[0]

    def test_unsubscribe_stops_delivery(self):
        bus = SignalBus()
        seen: list[SignalEvent] = []
        sid = bus.subscribe(TOPIC_ARM_BUSY, seen.append)
        bus.publish(TOPIC_ARM_BUSY, {})
        assert len(seen) == 1

        ok = bus.unsubscribe(sid)
        assert ok is True
        bus.publish(TOPIC_ARM_BUSY, {})
        assert len(seen) == 1  # Implementation note.

        # Implementation note.
        assert bus.unsubscribe(sid) is False

    def test_history_snapshot_is_isolated(self):
        bus = SignalBus()
        bus.publish(TOPIC_ARM_BUSY, {"i": 1})
        bus.publish(TOPIC_ARM_IDLE, {"i": 2})
        snap = bus.history()
        assert len(snap) == 2
        # Implementation note.
        snap.clear()
        assert len(bus.history()) == 2

    def test_history_records_all_publishes(self):
        bus = SignalBus()
        for i in range(5):
            bus.publish(TOPIC_ARM_BUSY, {"i": i})
        h = bus.history()
        assert len(h) == 5
        assert [e.payload["i"] for e in h] == [0, 1, 2, 3, 4]

    def test_subscribe_empty_pattern_raises(self):
        bus = SignalBus()
        with pytest.raises(ValueError):
            bus.subscribe("", lambda _e: None)

    def test_subscribe_non_callable_raises(self):
        bus = SignalBus()
        with pytest.raises(TypeError):
            bus.subscribe(TOPIC_ARM_BUSY, cast(object, 42))  # type: ignore[arg-type]

    def test_signal_event_is_frozen(self):
        ev = SignalEvent(
            topic="x",
            payload={},
            publisher="system",
            ts=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        with pytest.raises(ValidationError):
            ev.topic = "y"  # type: ignore[misc]

    def test_standard_topics_exported(self):
        assert TOPIC_ARM_BUSY in STANDARD_TOPICS
        assert TOPIC_ARM_IDLE in STANDARD_TOPICS
        assert TOPIC_ARM_MAILBOX in STANDARD_TOPICS
        assert TOPIC_SUCKER_GRABBED in STANDARD_TOPICS
        assert TOPIC_ALERT_BUDGET in STANDARD_TOPICS
        assert len(STANDARD_TOPICS) == 6

    def test_concurrent_publish(self):
        bus = SignalBus()
        seen_counts: list[int] = []
        lock = threading.Lock()

        def handler(_ev: SignalEvent) -> None:
            with lock:
                seen_counts.append(1)

        bus.subscribe("*", handler)

        def worker(tid: int) -> None:
            for i in range(10):
                bus.publish(TOPIC_ARM_BUSY, {"tid": tid, "i": i})

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert len(bus.history()) == 100
        assert sum(seen_counts) == 100


# ─── BoidsArbitrator ───────────────────────────────────────


class TestBoidsArbitrator:
    def test_first_claim_wins(self):
        arb = BoidsArbitrator()
        claim = ResourceClaim(
            arm_id=ArmId("arm:a1"),
            resource_uri="file://repo.git",
            priority=50,
        )
        assert arb.arbitrate(claim) == "win"
        assert len(arb.active_claims()) == 1

    def test_same_arm_repeat_claim_is_idempotent(self):
        arb = BoidsArbitrator()
        c1 = ResourceClaim(
            arm_id=ArmId("arm:a1"),
            resource_uri="file://repo.git",
            priority=50,
        )
        assert arb.arbitrate(c1) == "win"
        c2 = ResourceClaim(
            arm_id=ArmId("arm:a1"),
            resource_uri="file://repo.git",
            priority=50,
        )
        assert arb.arbitrate(c2) == "win"
        assert len(arb.active_claims()) == 1

    def test_lower_priority_loses(self):
        arb = BoidsArbitrator()
        hi = ResourceClaim(
            arm_id=ArmId("arm:hi"),
            resource_uri="file://repo.git",
            priority=80,
        )
        assert arb.arbitrate(hi) == "win"

        lo = ResourceClaim(
            arm_id=ArmId("arm:lo"),
            resource_uri="file://repo.git",
            priority=30,
        )
        assert arb.arbitrate(lo) == "lose"
        # Implementation note.
        actives = arb.active_claims()
        assert len(actives) == 1
        assert actives[0].arm_id == ArmId("arm:hi")

    def test_higher_priority_preempts(self):
        arb = BoidsArbitrator()
        lo = ResourceClaim(
            arm_id=ArmId("arm:lo"),
            resource_uri="file://repo.git",
            priority=20,
        )
        assert arb.arbitrate(lo) == "win"

        hi = ResourceClaim(
            arm_id=ArmId("arm:hi"),
            resource_uri="file://repo.git",
            priority=90,
        )
        assert arb.arbitrate(hi) == "win"  # Implementation note.
        actives = arb.active_claims()
        assert len(actives) == 1
        assert actives[0].arm_id == ArmId("arm:hi")

    def test_tie_priority_earlier_wins(self):
        arb = BoidsArbitrator()
        c1 = ResourceClaim(
            arm_id=ArmId("arm:a1"),
            resource_uri="file://repo.git",
            priority=50,
        )
        assert arb.arbitrate(c1) == "win"
        # Implementation note.
        time.sleep(0.005)
        c2 = ResourceClaim(
            arm_id=ArmId("arm:a2"),
            resource_uri="file://repo.git",
            priority=50,
        )
        assert arb.arbitrate(c2) == "lose"

    def test_readonly_resource_coexists(self):
        arb = BoidsArbitrator()
        c1 = ResourceClaim(
            arm_id=ArmId("arm:a1"),
            resource_uri="readonly:config.yaml",
            priority=50,
        )
        c2 = ResourceClaim(
            arm_id=ArmId("arm:a2"),
            resource_uri="readonly:config.yaml",
            priority=50,
        )
        assert arb.arbitrate(c1) == "coexist"
        assert arb.arbitrate(c2) == "coexist"
        actives = arb.active_claims()
        assert len(actives) == 2

    def test_readonly_suffix_also_coexists(self):
        arb = BoidsArbitrator()
        c1 = ResourceClaim(
            arm_id=ArmId("arm:a1"),
            resource_uri="mcp://fs:read",
            priority=50,
        )
        c2 = ResourceClaim(
            arm_id=ArmId("arm:a2"),
            resource_uri="mcp://fs:read",
            priority=50,
        )
        assert arb.arbitrate(c1) == "coexist"
        assert arb.arbitrate(c2) == "coexist"

    def test_ttl_expiry_clears_claim(self):
        arb = BoidsArbitrator()
        c1 = ResourceClaim(
            arm_id=ArmId("arm:a1"),
            resource_uri="file://repo.git",
            priority=50,
            ttl_ms=50,
        )
        assert arb.arbitrate(c1) == "win"
        time.sleep(0.08)
        # Implementation note.
        c2 = ResourceClaim(
            arm_id=ArmId("arm:a2"),
            resource_uri="file://repo.git",
            priority=10,
        )
        assert arb.arbitrate(c2) == "win"

    def test_clear_expired_returns_count(self):
        arb = BoidsArbitrator()
        c1 = ResourceClaim(
            arm_id=ArmId("arm:a1"),
            resource_uri="file://r1",
            priority=50,
            ttl_ms=30,
        )
        c2 = ResourceClaim(
            arm_id=ArmId("arm:a2"),
            resource_uri="file://r2",
            priority=50,
            ttl_ms=30,
        )
        arb.arbitrate(c1)
        arb.arbitrate(c2)
        time.sleep(0.06)
        n = arb.clear_expired()
        assert n == 2
        assert arb.active_claims() == []

    def test_release_removes_claim(self):
        arb = BoidsArbitrator()
        c1 = ResourceClaim(
            arm_id=ArmId("arm:a1"),
            resource_uri="file://repo.git",
            priority=50,
        )
        arb.arbitrate(c1)
        assert len(arb.active_claims()) == 1
        arb.release(ArmId("arm:a1"), "file://repo.git")
        assert arb.active_claims() == []

    def test_release_readonly_removes_only_one(self):
        arb = BoidsArbitrator()
        arb.arbitrate(
            ResourceClaim(
                arm_id=ArmId("arm:a1"),
                resource_uri="readonly:x",
                priority=50,
            )
        )
        arb.arbitrate(
            ResourceClaim(
                arm_id=ArmId("arm:a2"),
                resource_uri="readonly:x",
                priority=50,
            )
        )
        arb.release(ArmId("arm:a1"), "readonly:x")
        actives = arb.active_claims()
        assert len(actives) == 1
        assert actives[0].arm_id == ArmId("arm:a2")

    def test_release_noop_when_not_holding(self):
        arb = BoidsArbitrator()
        # Implementation note.
        arb.release(ArmId("arm:ghost"), "file://nowhere")
        assert arb.active_claims() == []

    def test_signal_bus_receives_grabbed_on_win(self):
        bus = SignalBus()
        seen: list[SignalEvent] = []
        bus.subscribe(TOPIC_SUCKER_GRABBED, seen.append)
        arb = BoidsArbitrator(signal_bus=bus)
        claim = ResourceClaim(
            arm_id=ArmId("arm:a1"),
            resource_uri="file://repo.git",
            priority=50,
        )
        assert arb.arbitrate(claim) == "win"
        assert len(seen) == 1
        assert seen[0].topic == TOPIC_SUCKER_GRABBED
        assert seen[0].payload["resource_uri"] == "file://repo.git"
        assert seen[0].payload["arm_id"] == "arm:a1"

    def test_signal_bus_no_event_on_lose(self):
        bus = SignalBus()
        seen: list[SignalEvent] = []
        bus.subscribe(TOPIC_SUCKER_GRABBED, seen.append)
        arb = BoidsArbitrator(signal_bus=bus)
        arb.arbitrate(
            ResourceClaim(
                arm_id=ArmId("arm:hi"),
                resource_uri="file://r",
                priority=80,
            )
        )
        arb.arbitrate(
            ResourceClaim(
                arm_id=ArmId("arm:lo"),
                resource_uri="file://r",
                priority=10,
            )
        )
        # Implementation note.
        assert len(seen) == 1

    def test_resource_claim_is_frozen(self):
        c = ResourceClaim(
            arm_id=ArmId("arm:a1"),
            resource_uri="x",
            priority=10,
        )
        with pytest.raises(ValidationError):
            c.priority = 99  # type: ignore[misc]


# Implementation note.


class TestConcurrency:
    def test_concurrent_claim_single_winner(self):
        arb = BoidsArbitrator()
        uri = "file://hotspot"
        barrier = threading.Barrier(10)
        verdicts: list[str] = []
        lock = threading.Lock()

        def worker(tid: int) -> None:
            barrier.wait()
            claim = ResourceClaim(
                arm_id=ArmId(f"arm:{tid}"),
                resource_uri=uri,
                priority=50,
            )
            v = arb.arbitrate(claim)
            with lock:
                verdicts.append(v)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        # Implementation note.
        actives = arb.active_claims()
        assert len(actives) == 1
        # Implementation note.
        assert "win" in verdicts
        # Implementation note.
        assert verdicts.count("win") >= 1

    def test_concurrent_subscribe_publish_is_safe(self):
        bus = SignalBus()
        seen: list[SignalEvent] = []
        lock = threading.Lock()

        def handler(ev: SignalEvent) -> None:
            with lock:
                seen.append(ev)

        def subscriber_worker() -> None:
            for _ in range(20):
                sid = bus.subscribe("arm.*", handler)
                bus.unsubscribe(sid)

        def publisher_worker() -> None:
            for _ in range(20):
                bus.publish(TOPIC_ARM_BUSY, {})

        threads = [
            threading.Thread(target=subscriber_worker),
            threading.Thread(target=publisher_worker),
            threading.Thread(target=subscriber_worker),
            threading.Thread(target=publisher_worker),
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        # Implementation note.
        assert len(bus.history()) == 40
