from __future__ import annotations

import queue
import threading
from typing import Any

import pytest

from runtime.platform.plugins.bundled.paper_trading.quote_hub import (
    QUOTE_FIELDS,
    CallbackQuoteSourceAdapter,
    PollingQuoteSource,
    QuoteHub,
    normalize_quote,
)


class FakeClock:
    def __init__(self, value: float = 1_800_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeSource:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.callback = None
        self.codes: set[str] = set()

    def subscribe(self, codes, callback) -> None:
        self.calls.append(("subscribe", list(codes)))
        self.codes.update(codes)
        self.callback = callback

    def unsubscribe(self, codes, callback) -> None:
        self.calls.append(("unsubscribe", list(codes)))
        self.codes.difference_update(codes)
        if self.callback == callback and not self.codes:
            self.callback = None

    def push(self, rows: list[dict[str, Any]]) -> None:
        assert self.callback is not None
        self.callback("kLineRealTime", {"code": 1, "data": rows})


class FailingSource(FakeSource):
    def __init__(self) -> None:
        super().__init__()
        self.fail = True

    def subscribe(self, codes, callback) -> None:
        if self.fail:
            self.calls.append(("subscribe", list(codes)))
            raise RuntimeError("upstream handshake failed")
        super().subscribe(codes, callback)


class FakeEventClient:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def subscribe(self, event, params, callback) -> None:
        self.calls.append(("subscribe", event, list(params), callback))

    def unsubscribe(self, event, callback) -> None:
        self.calls.append(("unsubscribe", event, callback))


def _row(code: str, price: float, exchange: str = "SH") -> dict[str, Any]:
    return {
        "stockCode": code,
        "stockName": f"stock-{code}",
        "exchangeType": exchange,
        "currentPrice": price,
        "yClose": price - 1,
        "lastUpdateDate": "08-25 09:30:01",
    }


def test_normalize_quote_has_exact_stable_schema() -> None:
    quote = normalize_quote(
        _row("600000", 10.5),
        source="platform",
        received_epoch=1_800_000_000,
        seq=7,
    )

    assert tuple(quote) == QUOTE_FIELDS
    assert quote["code"] == "600000.sh"
    assert quote["price"] == 10.5
    assert quote["source"] == "platform"
    assert quote["source_ts"] == "08-25 09:30:01"
    assert quote["received_at"].endswith("Z")
    assert quote["seq"] == 7
    assert quote["stale"] is False


def test_callback_source_adapter_translates_incremental_codes_to_event_client() -> None:
    client = FakeEventClient()
    adapter = CallbackQuoteSourceAdapter(client)

    def callback(event, data) -> None:  # noqa: ARG001
        return None

    adapter.subscribe(["600000.sh"], callback)
    adapter.subscribe(["000001.sz"], callback)
    adapter.unsubscribe(["600000.sh"], callback)
    adapter.unsubscribe(["000001.sz"], callback)

    assert client.calls == [
        ("subscribe", "kLineRealTime", ["600000.sh"], callback),
        ("subscribe", "kLineRealTime", ["000001.sz", "600000.sh"], callback),
        ("subscribe", "kLineRealTime", ["000001.sz"], callback),
        ("unsubscribe", "kLineRealTime", callback),
    ]


def test_subscriptions_merge_codes_and_reference_count_upstream() -> None:
    source = FakeSource()
    hub = QuoteHub({"platform": source})

    a = hub.subscribe(["600000.sh", "000001.sz"], subscriber_id="a", replay=False)
    b = hub.subscribe(["600000.sh"], subscriber_id="b", replay=False)

    assert source.calls[-1] == ("subscribe", ["000001.sz", "600000.sh"])
    status = hub.status()
    assert status["ref_counts"] == {"000001.sz": 1, "600000.sh": 2}
    assert status["subscribed_code_count"] == 2

    assert hub.unsubscribe(a) is True
    assert source.calls[-1] == ("unsubscribe", ["000001.sz"])
    assert hub.status()["ref_counts"] == {"600000.sh": 1}

    b.close()
    assert source.calls[-1] == ("unsubscribe", ["600000.sh"])
    assert hub.status()["subscriber_count"] == 0


def test_reusing_subscriber_id_replaces_codes_without_double_counting() -> None:
    source = FakeSource()
    hub = QuoteHub({"platform": source})

    first = hub.subscribe(["600000.sh"], subscriber_id="web", replay=False)
    second = hub.subscribe(["000001.sz"], subscriber_id="web", replay=False)

    assert first is second
    assert second.codes == frozenset({"000001.sz"})
    assert hub.status()["ref_counts"] == {"000001.sz": 1}


def test_fanout_filters_by_subscriber_and_snapshot_is_rest_ready() -> None:
    source = FakeSource()
    clock = FakeClock()
    hub = QuoteHub({"platform": source}, clock=clock)
    sh = hub.subscribe(["600000.sh"], replay=False)
    sz = hub.subscribe(["000001.sz"], replay=False)

    source.push([_row("600000", 10.5, "SH"), _row("000001", 12.5, "SZ")])

    sh_event = sh.get_nowait()
    sz_event = sz.get_nowait()
    assert [q["code"] for q in sh_event["quotes"]] == ["600000.sh"]
    assert [q["code"] for q in sz_event["quotes"]] == ["000001.sz"]
    assert sh_event["type"] == "quotes"
    assert sh_event["source"] == "platform"

    snapshot = hub.snapshot(["600000.sh"])
    assert snapshot["type"] == "snapshot"
    assert snapshot["source"] == "platform"
    assert snapshot["quotes"][0]["price"] == 10.5
    assert snapshot["quotes"][0]["stale"] is False


def test_bounded_subscriber_queue_drops_oldest_and_keeps_latest() -> None:
    source = FakeSource()
    hub = QuoteHub({"platform": source}, subscriber_queue_size=2)
    subscription = hub.subscribe(["600000.sh"], replay=False)

    source.push([_row("600000", 10.0)])
    source.push([_row("600000", 11.0)])
    source.push([_row("600000", 12.0)])

    events = subscription.drain()
    assert [event["quotes"][0]["price"] for event in events] == [11.0, 12.0]
    assert subscription.pending == 0
    assert subscription.dropped == 1


def test_primary_breaker_fails_over_and_stable_recovery_fails_back() -> None:
    primary = FakeSource()
    backup = FakeSource()
    clock = FakeClock()
    hub = QuoteHub(
        {"platform": primary, "tdx": backup},
        primary="platform",
        failure_threshold=3,
        recovery_threshold=2,
        clock=clock,
    )
    subscription = hub.subscribe(["600000.sh"], replay=False)
    primary.push([_row("600000", 10.0)])
    assert subscription.get_nowait()["quotes"][0]["source"] == "platform"

    assert hub.report_failure("platform", "one") is False
    assert hub.report_failure("platform", "two") is False
    assert hub.report_failure("platform", "three") is True
    assert hub.active_source == "tdx"
    assert backup.calls[-1] == ("subscribe", ["600000.sh"])
    switched = subscription.get_nowait()
    assert switched["type"] == "source_changed"
    assert switched["quotes"][0]["stale"] is True

    backup.push([_row("600000", 10.1)])
    assert subscription.get_nowait()["quotes"][0]["source"] == "tdx"

    # The failed primary remains attached. Two consecutive valid callbacks are
    # required before it may reclaim active-source status.
    primary.push([_row("600000", 10.2)])
    assert hub.active_source == "tdx"
    primary.push([_row("600000", 10.3)])
    assert hub.active_source == "platform"
    failback = subscription.get_nowait()
    assert failback["type"] == "source_changed"
    assert backup.calls[-1] == ("unsubscribe", ["600000.sh"])
    recovered_quote = subscription.get_nowait()
    assert recovered_quote["quotes"][0]["price"] == 10.3
    assert recovered_quote["quotes"][0]["source"] == "platform"

    status = hub.status()
    assert status["switch_count"] == 2
    assert status["sources"]["platform"]["state"] == "healthy"
    assert status["sources"]["tdx"]["attached"] is False


def test_subscription_handshake_failure_is_degraded_and_can_fail_over() -> None:
    unavailable = FailingSource()
    hub = QuoteHub({"platform": unavailable}, failure_threshold=3)

    hub.subscribe(["600000.sh"], replay=False)

    status = hub.status()
    assert status["state"] == "degraded"
    assert status["degraded"] is True
    assert status["sources"]["platform"]["state"] == "degraded"
    assert status["sources"]["platform"]["total_failures"] == 1
    assert status["sources"]["platform"]["attached"] is False

    clock = FakeClock()
    primary = FailingSource()
    fallback = FakeSource()
    failover_hub = QuoteHub(
        {"platform": primary, "fallback": fallback},
        failure_threshold=1,
        recovery_threshold=1,
        clock=clock,
    )
    subscription = failover_hub.subscribe(["600000.sh"], replay=False)

    assert failover_hub.active_source == "fallback"
    assert fallback.codes == {"600000.sh"}
    assert subscription.get_nowait()["type"] == "source_changed"

    primary.fail = False
    clock.advance(10)
    failover_hub.check_health()
    assert primary.codes == {"600000.sh"}
    primary.push([_row("600000", 10.5)])
    assert failover_hub.active_source == "platform"


def test_recovery_must_be_consecutive() -> None:
    primary = FakeSource()
    backup = FakeSource()
    hub = QuoteHub(
        {"platform": primary, "tdx": backup},
        failure_threshold=1,
        recovery_threshold=2,
    )
    hub.subscribe(["600000.sh"], replay=False)
    hub.report_failure("platform", "down")

    hub.report_success("platform")
    hub.report_failure("platform", "flapped")
    hub.report_success("platform")
    assert hub.active_source == "tdx"
    hub.report_success("platform")
    assert hub.active_source == "platform"


def test_watchdog_style_health_checks_trip_silent_active_source() -> None:
    primary = FakeSource()
    backup = FakeSource()
    clock = FakeClock()
    hub = QuoteHub(
        {"platform": primary, "tdx": backup},
        failure_threshold=2,
        stale_after=10,
        health_check_interval=1,
        clock=clock,
    )
    hub.subscribe(["600000.sh"], replay=False)

    clock.advance(11)
    hub.check_health()
    assert hub.active_source == "platform"
    clock.advance(1)
    hub.check_health()
    assert hub.active_source == "tdx"


def test_snapshot_marks_old_or_previous_source_quotes_stale() -> None:
    source = FakeSource()
    clock = FakeClock()
    hub = QuoteHub({"platform": source}, stale_after=5, clock=clock)
    hub.subscribe(["600000.sh"], replay=False)
    source.push([_row("600000", 10.0)])

    clock.advance(6)
    assert hub.snapshot()["quotes"][0]["stale"] is True
    assert hub.status()["stale_quote_count"] == 1


def test_subscription_close_wakes_waiter_and_is_idempotent() -> None:
    source = FakeSource()
    hub = QuoteHub({"platform": source})
    subscription = hub.subscribe(["600000.sh"], replay=False)
    received: list[dict[str, Any]] = []

    worker = threading.Thread(target=lambda: received.append(subscription.get(timeout=1)))
    worker.start()
    subscription.close()
    worker.join(timeout=1)

    assert received == [{"type": "closed", "subscriber_id": subscription.id}]
    assert subscription.closed is True
    assert hub.unsubscribe(subscription) is False


def test_invalid_configuration_and_empty_subscription_are_rejected() -> None:
    source = FakeSource()
    with pytest.raises(ValueError, match="at least one source"):
        QuoteHub({})
    with pytest.raises(ValueError, match="unknown primary"):
        QuoteHub({"platform": source}, primary="missing")

    hub = QuoteHub({"platform": source})
    with pytest.raises(ValueError, match="at least one quote code"):
        hub.subscribe([])
    with pytest.raises(queue.Empty):
        # Also proves queue.Empty remains the standard timeout signal for SSE.
        hub.subscribe(["600000.sh"], replay=False).get_nowait()


def test_subscription_limits_are_enforced_atomically() -> None:
    source = FakeSource()
    hub = QuoteHub(
        {"platform": source},
        max_subscribers=1,
        max_codes_per_subscriber=2,
        max_union_codes=2,
    )
    hub.subscribe(["600000.sh", "000001.sz"], subscriber_id="one", replay=False)

    with pytest.raises(RuntimeError, match="subscriber capacity"):
        hub.subscribe(["600000.sh"], subscriber_id="two", replay=False)
    with pytest.raises(ValueError, match="at most 2"):
        hub.subscribe(
            ["600000.sh", "000001.sz", "300001.sz"],
            subscriber_id="one",
            replay=False,
        )

    assert hub.status()["subscriber_count"] == 1
    assert hub.status()["subscribed_code_count"] == 2


def test_primary_recovery_waits_for_stability_window() -> None:
    primary = FakeSource()
    backup = FakeSource()
    clock = FakeClock()
    hub = QuoteHub(
        {"platform": primary, "tdx": backup},
        failure_threshold=1,
        recovery_threshold=2,
        primary_recovery_seconds=120,
        clock=clock,
    )
    hub.subscribe(["600000.sh"], replay=False)
    hub.report_failure("platform", "down")

    hub.report_success("platform")
    hub.report_success("platform")
    assert hub.active_source == "tdx"
    clock.advance(119)
    hub.report_success("platform")
    assert hub.active_source == "tdx"
    clock.advance(1)
    hub.report_success("platform")
    assert hub.active_source == "platform"


def test_health_gate_prevents_false_failover_outside_trading_hours() -> None:
    primary = FakeSource()
    backup = FakeSource()
    clock = FakeClock()
    trading = False
    hub = QuoteHub(
        {"platform": primary, "tdx": backup},
        failure_threshold=1,
        stale_after=5,
        health_check_enabled=lambda: trading,
        clock=clock,
    )
    hub.subscribe(["600000.sh"], replay=False)
    clock.advance(30)

    hub.check_health()

    assert hub.active_source == "platform"
    assert hub.status()["sources"]["platform"]["total_failures"] == 0


def test_polling_source_batches_union_and_stops_when_detached() -> None:
    called = threading.Event()
    fetches: list[list[str]] = []

    def fetcher(codes: list[str]) -> list[dict[str, Any]]:
        fetches.append(codes)
        called.set()
        return [_row("600000", 10.5)]

    source = PollingQuoteSource(fetcher, interval=60)
    received: list[dict[str, Any]] = []

    def callback(_event: str, payload: dict[str, Any]) -> None:
        received.append(payload)

    source.subscribe(["600000.sh", "000001.sz"], callback)
    assert called.wait(1)
    source.unsubscribe(["600000.sh", "000001.sz"], callback)
    for _attempt in range(100):
        if not source.running:
            break
        threading.Event().wait(0.01)

    assert fetches[0] == ["000001.sz", "600000.sh"]
    assert received[0]["data"][0]["currentPrice"] == 10.5
    assert source.running is False

