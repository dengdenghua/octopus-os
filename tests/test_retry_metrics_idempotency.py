"""Tests for retry policy, metrics registry, and idempotency guard:

1. Retry Policy (exponential backoff + jitter)
2. Metrics Registry (counter/gauge/histogram + Prometheus format)
3. Idempotency Guard (content-hash dedup with TTL)
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

# ═══════════════════════════════════════════════════════════════
# 1. Retry Policy
# ═══════════════════════════════════════════════════════════════


class TestRetryPolicy:
    def test_succeeds_on_first_try(self):
        from runtime.platform.runtime_policy.retry import RetryPolicy, retry_call

        policy = RetryPolicy(on=(ValueError,), attempts=3, base_delay=0.01)
        sleep = MagicMock()
        fn = MagicMock(return_value="ok")
        assert retry_call(fn, policy=policy, sleep=sleep) == "ok"
        assert fn.call_count == 1
        sleep.assert_not_called()

    def test_retries_on_whitelisted_exception(self):
        from runtime.platform.runtime_policy.retry import RetryPolicy, retry_call

        calls = [0]

        def flaky():
            calls[0] += 1
            if calls[0] < 3:
                raise ConnectionError("boom")
            return "ok"

        policy = RetryPolicy(on=(ConnectionError,), attempts=5, base_delay=0.001, jitter=0)
        assert retry_call(flaky, policy=policy, sleep=lambda s: None) == "ok"
        assert calls[0] == 3

    def test_raises_non_retryable_immediately(self):
        from runtime.platform.runtime_policy.retry import RetryPolicy, retry_call

        fn = MagicMock(side_effect=ValueError("not retryable"))
        policy = RetryPolicy(on=(ConnectionError,), attempts=5)
        with pytest.raises(ValueError):
            retry_call(fn, policy=policy, sleep=lambda s: None)
        assert fn.call_count == 1

    def test_raises_after_attempts_exhausted(self):
        from runtime.platform.runtime_policy.retry import RetryPolicy, retry_call

        fn = MagicMock(side_effect=ConnectionError("down"))
        policy = RetryPolicy(on=(ConnectionError,), attempts=3, base_delay=0.001, jitter=0)
        with pytest.raises(ConnectionError):
            retry_call(fn, policy=policy, sleep=lambda s: None)
        assert fn.call_count == 3

    def test_backoff_delays_grow_exponentially(self):
        from runtime.platform.runtime_policy.retry import RetryPolicy

        p = RetryPolicy(base_delay=1.0, max_delay=100.0, jitter=0)
        assert p.compute_delay(0) == 1.0
        assert p.compute_delay(1) == 2.0
        assert p.compute_delay(2) == 4.0
        assert p.compute_delay(3) == 8.0

    def test_backoff_clamped_to_max_delay(self):
        from runtime.platform.runtime_policy.retry import RetryPolicy

        p = RetryPolicy(base_delay=1.0, max_delay=5.0, jitter=0)
        assert p.compute_delay(10) == 5.0

    def test_jitter_reduces_delay(self):
        from runtime.platform.runtime_policy.retry import RetryPolicy

        p = RetryPolicy(base_delay=10.0, max_delay=100.0, jitter=0.5)
        for _ in range(100):
            d = p.compute_delay(0)
            assert 5.0 <= d <= 10.0

    def test_retry_if_predicate_overrides_whitelist(self):
        from runtime.platform.runtime_policy.retry import RetryPolicy, retry_call

        calls = [0]

        def flaky():
            calls[0] += 1
            if calls[0] < 2:
                err = RuntimeError("transient")
                err.transient = True  # type: ignore[attr-defined]
                raise err
            return "ok"

        policy = RetryPolicy(
            on=(),
            retry_if=lambda e: getattr(e, "transient", False),
            attempts=3,
            base_delay=0.001,
            jitter=0,
        )
        assert retry_call(flaky, policy=policy, sleep=lambda s: None) == "ok"

    def test_on_retry_callback_fires(self):
        from runtime.platform.runtime_policy.retry import RetryPolicy, retry_call

        events: list[tuple[int, str, float]] = []

        def observer(idx, exc, delay):
            events.append((idx, type(exc).__name__, delay))

        calls = [0]

        def fn():
            calls[0] += 1
            if calls[0] < 3:
                raise ConnectionError("x")
            return "ok"

        policy = RetryPolicy(on=(ConnectionError,), attempts=5, base_delay=0.001, jitter=0)
        retry_call(fn, policy=policy, sleep=lambda s: None, on_retry=observer)
        assert len(events) == 2
        assert events[0][0] == 0
        assert events[0][1] == "ConnectionError"

    def test_decorator_form(self):
        from runtime.platform.runtime_policy.retry import retry

        calls = [0]

        @retry(on=(ValueError,), attempts=3, base_delay=0.001, jitter=0, sleep=lambda s: None)
        def flaky():
            calls[0] += 1
            if calls[0] < 2:
                raise ValueError("x")
            return "ok"

        assert flaky() == "ok"
        assert calls[0] == 2

    def test_is_retryable_http_status(self):
        from runtime.platform.runtime_policy.retry import is_retryable_http_status

        assert is_retryable_http_status(429) is True
        assert is_retryable_http_status(500) is True
        assert is_retryable_http_status(503) is True
        assert is_retryable_http_status(200) is False
        assert is_retryable_http_status(404) is False
        assert is_retryable_http_status(None) is False

    def test_attempts_one_disables_retry(self):
        from runtime.platform.runtime_policy.retry import RetryPolicy, retry_call

        fn = MagicMock(side_effect=ConnectionError("x"))
        policy = RetryPolicy(on=(ConnectionError,), attempts=1)
        with pytest.raises(ConnectionError):
            retry_call(fn, policy=policy, sleep=lambda s: None)
        assert fn.call_count == 1


# ═══════════════════════════════════════════════════════════════
# 2. Metrics Registry
# ═══════════════════════════════════════════════════════════════


class TestMetricsRegistry:
    def test_counter_inc_and_value(self):
        from runtime.platform.observability.metrics import MetricsRegistry

        r = MetricsRegistry()
        c = r.counter("test_calls", "Test calls")
        c.inc()
        c.inc(5)
        assert c.value() == 6.0

    def test_counter_labels_isolated(self):
        from runtime.platform.observability.metrics import MetricsRegistry

        r = MetricsRegistry()
        c = r.counter("calls", labels=["skill"])
        c.inc(labels={"skill": "read_file"})
        c.inc(labels={"skill": "read_file"})
        c.inc(labels={"skill": "web_search"})
        assert c.value(labels={"skill": "read_file"}) == 2.0
        assert c.value(labels={"skill": "web_search"}) == 1.0

    def test_counter_rejects_negative(self):
        from runtime.platform.observability.metrics import MetricsRegistry

        r = MetricsRegistry()
        c = r.counter("calls")
        with pytest.raises(ValueError):
            c.inc(-1)

    def test_gauge_set_inc_dec(self):
        from runtime.platform.observability.metrics import MetricsRegistry

        r = MetricsRegistry()
        g = r.gauge("queue_depth")
        g.set(10)
        g.inc(5)
        g.dec(2)
        assert g.value() == 13.0

    def test_histogram_observe_and_snapshot(self):
        from runtime.platform.observability.metrics import MetricsRegistry

        r = MetricsRegistry()
        h = r.histogram(
            "latency",
            buckets=(0.1, 0.5, 1.0, math.inf),
        )
        h.observe(0.05)
        h.observe(0.3)
        h.observe(0.8)
        h.observe(2.0)

        snap = h.snapshot()
        assert snap["count"] == 4
        assert abs(snap["sum"] - 3.15) < 1e-9
        # Cumulative: 0.1 catches 0.05 (1), 0.5 catches +0.3 (2),
        # 1.0 catches +0.8 (3), +Inf catches +2.0 (4).
        assert snap["buckets"][0.1] == 1
        assert snap["buckets"][0.5] == 2
        assert snap["buckets"][1.0] == 3
        assert snap["buckets"][math.inf] == 4

    def test_prometheus_render_counter(self):
        from runtime.platform.observability.metrics import MetricsRegistry

        r = MetricsRegistry()
        c = r.counter("echo_calls_total", "Total calls", labels=["skill"])
        c.inc(labels={"skill": "read_file"})
        out = r.render_prometheus()
        assert "# HELP echo_calls_total Total calls" in out
        assert "# TYPE echo_calls_total counter" in out
        assert 'echo_calls_total{skill="read_file"} 1' in out

    def test_prometheus_render_histogram(self):
        from runtime.platform.observability.metrics import MetricsRegistry

        r = MetricsRegistry()
        h = r.histogram("lat", buckets=(0.1, 1.0, math.inf))
        h.observe(0.5)
        out = r.render_prometheus()
        assert "# TYPE lat histogram" in out
        assert 'lat_bucket{le="0.1"} 0' in out
        assert 'lat_bucket{le="1"} 1' in out
        assert 'lat_bucket{le="+Inf"} 1' in out
        assert "lat_sum" in out
        assert "lat_count 1" in out

    def test_register_same_name_returns_existing(self):
        from runtime.platform.observability.metrics import MetricsRegistry

        r = MetricsRegistry()
        c1 = r.counter("foo")
        c2 = r.counter("foo")
        assert c1 is c2

    def test_type_mismatch_raises(self):
        from runtime.platform.observability.metrics import MetricsRegistry

        r = MetricsRegistry()
        r.counter("foo")
        with pytest.raises(TypeError):
            r.gauge("foo")

    def test_label_escaping(self):
        from runtime.platform.observability.metrics import MetricsRegistry

        r = MetricsRegistry()
        c = r.counter("events", labels=["msg"])
        c.inc(labels={"msg": 'has "quote" and \\backslash'})
        out = r.render_prometheus()
        assert '\\"quote\\"' in out
        assert "\\\\backslash" in out

    def test_global_registry_singleton(self):
        from runtime.platform.observability.metrics import get_registry

        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_counter_concurrent_inc(self):
        from runtime.platform.observability.metrics import MetricsRegistry

        r = MetricsRegistry()
        c = r.counter("x")

        def worker():
            for _ in range(100):
                c.inc()

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert c.value() == 1000


# ═══════════════════════════════════════════════════════════════
# 3. Idempotency Guard
# ═══════════════════════════════════════════════════════════════


class TestIdempotencyGuard:
    def test_first_call_runs_second_call_cached(self):
        from runtime.platform.runtime_policy.idempotency import IdempotencyGuard

        g = IdempotencyGuard(ttl_seconds=60)
        calls = [0]

        def fn():
            calls[0] += 1
            return "result"

        assert g.run(key="k1", fn=fn) == "result"
        assert g.run(key="k1", fn=fn) == "result"
        assert calls[0] == 1

    def test_different_keys_run_fresh(self):
        from runtime.platform.runtime_policy.idempotency import IdempotencyGuard

        g = IdempotencyGuard(ttl_seconds=60)
        calls = [0]

        def fn():
            calls[0] += 1
            return calls[0]

        a = g.run(key="a", fn=fn)
        b = g.run(key="b", fn=fn)
        assert a == 1
        assert b == 2

    def test_ttl_expiry_runs_fresh(self):
        from runtime.platform.runtime_policy.idempotency import IdempotencyGuard

        now = [0.0]
        g = IdempotencyGuard(ttl_seconds=10, clock=lambda: now[0])
        calls = [0]

        def fn():
            calls[0] += 1
            return calls[0]

        assert g.run(key="k", fn=fn) == 1
        now[0] = 5
        assert g.run(key="k", fn=fn) == 1
        now[0] = 20
        # TTL expired → fresh run.
        assert g.run(key="k", fn=fn) == 2

    def test_errors_not_cached_by_default(self):
        from runtime.platform.runtime_policy.idempotency import IdempotencyGuard

        g = IdempotencyGuard(ttl_seconds=60)
        calls = [0]

        def fn():
            calls[0] += 1
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            g.run(key="k", fn=fn)
        with pytest.raises(RuntimeError):
            g.run(key="k", fn=fn)
        assert calls[0] == 2  # both calls ran

    def test_errors_cached_when_opted_in(self):
        from runtime.platform.runtime_policy.idempotency import IdempotencyGuard

        g = IdempotencyGuard(ttl_seconds=60, cache_errors=True)
        calls = [0]

        def fn():
            calls[0] += 1
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            g.run(key="k", fn=fn)
        with pytest.raises(RuntimeError):
            g.run(key="k", fn=fn)
        assert calls[0] == 1  # second call used cached exception

    def test_concurrent_callers_dedup(self):
        from runtime.platform.runtime_policy.idempotency import IdempotencyGuard

        g = IdempotencyGuard(ttl_seconds=60)
        # Ensures all 5 threads arrive at g.run() before the fn returns,
        # so 4 of them see the pending event and wait.
        start_gate = threading.Event()
        calls = [0]
        results: list[Any] = []
        results_lock = threading.Lock()

        def slow_fn():
            # Hold the owner inside the critical section long enough
            # for waiters to pile up behind.
            start_gate.wait(timeout=2)
            calls[0] += 1
            return calls[0]

        def worker():
            r = g.run(key="shared", fn=slow_fn)
            with results_lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        # Give all 5 workers a chance to enter g.run.
        time.sleep(0.1)
        start_gate.set()
        for t in threads:
            t.join(timeout=5)
        # Exactly one call should have executed.
        assert calls[0] == 1
        # All workers should have seen the same result.
        assert len(results) == 5
        assert all(r == 1 for r in results)

    def test_invalidate_removes_entry(self):
        from runtime.platform.runtime_policy.idempotency import IdempotencyGuard

        g = IdempotencyGuard(ttl_seconds=60)
        calls = [0]

        def fn():
            calls[0] += 1
            return calls[0]

        g.run(key="k", fn=fn)
        g.invalidate("k")
        g.run(key="k", fn=fn)
        assert calls[0] == 2

    def test_stats_tracks_hits_and_misses(self):
        from runtime.platform.runtime_policy.idempotency import IdempotencyGuard

        g = IdempotencyGuard(ttl_seconds=60)
        g.run(key="k", fn=lambda: 1)  # miss
        g.run(key="k", fn=lambda: 1)  # hit
        g.run(key="k", fn=lambda: 1)  # hit
        stats = g.stats()
        assert stats["misses"] == 1
        assert stats["hits"] == 2
        assert stats["hit_ratio_bp"] > 6000  # ≈ 6666 bp

    def test_memoize_decorator(self):
        from runtime.platform.runtime_policy.idempotency import IdempotencyGuard

        g = IdempotencyGuard(ttl_seconds=60)
        calls = [0]

        @g.memoize()
        def expensive(x: int) -> int:
            calls[0] += 1
            return x * 2

        assert expensive(5) == 10
        assert expensive(5) == 10
        assert expensive(6) == 12
        assert calls[0] == 2  # 5 and 6, but 5 was cached the second time

    def test_purge_drops_expired(self):
        from runtime.platform.runtime_policy.idempotency import IdempotencyGuard

        now = [0.0]
        g = IdempotencyGuard(ttl_seconds=10, clock=lambda: now[0])
        g.run(key="a", fn=lambda: 1)
        g.run(key="b", fn=lambda: 2)
        now[0] = 20
        g.run(key="c", fn=lambda: 3)  # still inserts at now=20
        removed = g.purge()
        assert removed == 2  # a and b expired
        assert g.stats()["size"] == 1  # only c remains

    def test_compute_key_stable(self):
        from runtime.platform.runtime_policy.idempotency import compute_key

        k1 = compute_key("webhook", {"id": 1, "b": 2})
        k2 = compute_key("webhook", {"b": 2, "id": 1})  # reordered dict
        assert k1 == k2

    def test_compute_key_differs_on_input(self):
        from runtime.platform.runtime_policy.idempotency import compute_key

        k1 = compute_key("a")
        k2 = compute_key("b")
        assert k1 != k2

    def test_clear_removes_all(self):
        from runtime.platform.runtime_policy.idempotency import IdempotencyGuard

        g = IdempotencyGuard(ttl_seconds=60)
        g.run(key="a", fn=lambda: 1)
        g.run(key="b", fn=lambda: 2)
        assert g.stats()["size"] == 2
        g.clear()
        assert g.stats()["size"] == 0
