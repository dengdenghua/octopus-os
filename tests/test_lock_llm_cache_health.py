"""Tests for distributed lock, LLM response cache, and health checks:

1. Distributed Lock (in-memory backend with fencing tokens)
2. LLM Response Cache (deterministic call dedup with savings telemetry)
3. Health Check Framework (liveness/readiness probes)
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

# ═══════════════════════════════════════════════════════════════
# 1. Distributed Lock
# ═══════════════════════════════════════════════════════════════


class TestDistributedLock:
    def test_acquire_returns_lease(self):
        from runtime.platform.process.distributed_lock import DistributedLock

        lock = DistributedLock("test")
        lease = lock.try_acquire(ttl_seconds=10)
        assert lease is not None
        assert lease.scope == "test"
        assert lease.token > 0

    def test_second_holder_blocked(self):
        from runtime.platform.process.distributed_lock import (
            DistributedLock,
            InMemoryLockBackend,
        )

        backend = InMemoryLockBackend()
        lock_a = DistributedLock("scope", backend=backend, holder_id="A")
        lock_b = DistributedLock("scope", backend=backend, holder_id="B")

        assert lock_a.try_acquire(ttl_seconds=60) is not None
        assert lock_b.try_acquire(ttl_seconds=60) is None

    def test_token_monotonically_increases(self):
        from runtime.platform.process.distributed_lock import (
            DistributedLock,
            InMemoryLockBackend,
        )

        backend = InMemoryLockBackend()
        lock_a = DistributedLock("scope", backend=backend, holder_id="A")
        lock_b = DistributedLock("scope", backend=backend, holder_id="B")

        lease1 = lock_a.try_acquire(ttl_seconds=60)
        lock_a.release()
        lease2 = lock_b.try_acquire(ttl_seconds=60)
        assert lease2.token > lease1.token

    def test_re_entrant_refresh_keeps_same_token(self):
        from runtime.platform.process.distributed_lock import DistributedLock

        lock = DistributedLock("test")
        lease1 = lock.try_acquire(ttl_seconds=60)
        lease2 = lock.try_acquire(ttl_seconds=60)  # same holder, same lock
        assert lease1.token == lease2.token

    def test_expired_lock_available_to_others(self):
        from runtime.platform.process.distributed_lock import (
            DistributedLock,
            InMemoryLockBackend,
        )

        backend = InMemoryLockBackend()
        lock_a = DistributedLock("scope", backend=backend, holder_id="A")
        lock_b = DistributedLock("scope", backend=backend, holder_id="B")

        lock_a.try_acquire(ttl_seconds=0.05)
        time.sleep(0.1)
        lease = lock_b.try_acquire(ttl_seconds=60)
        assert lease is not None
        assert lease.holder_id == "B"

    def test_renew_extends_expiry(self):
        from runtime.platform.process.distributed_lock import DistributedLock

        lock = DistributedLock("test")
        lease = lock.try_acquire(ttl_seconds=1)
        original_expires = lease.expires_at
        renewed = lock.renew(ttl_seconds=60)
        assert renewed is not None
        assert renewed.expires_at > original_expires
        assert renewed.token == lease.token

    def test_renew_fails_if_not_holder(self):
        from runtime.platform.process.distributed_lock import (
            DistributedLock,
            InMemoryLockBackend,
        )

        backend = InMemoryLockBackend()
        lock_a = DistributedLock("scope", backend=backend, holder_id="A")
        lock_a.try_acquire(ttl_seconds=0.05)
        time.sleep(0.1)

        lock_b = DistributedLock("scope", backend=backend, holder_id="B")
        lock_b.try_acquire(ttl_seconds=60)  # Takes over

        # A still has an old lease reference — renew should fail.
        assert lock_a.renew(ttl_seconds=60) is None

    def test_release_frees_scope(self):
        from runtime.platform.process.distributed_lock import (
            DistributedLock,
            InMemoryLockBackend,
        )

        backend = InMemoryLockBackend()
        lock_a = DistributedLock("scope", backend=backend, holder_id="A")
        lock_b = DistributedLock("scope", backend=backend, holder_id="B")

        lock_a.try_acquire(ttl_seconds=60)
        assert lock_a.release() is True
        assert lock_b.try_acquire(ttl_seconds=60) is not None

    def test_is_current_token_fences_stale_writes(self):
        from runtime.platform.process.distributed_lock import (
            DistributedLock,
            InMemoryLockBackend,
        )

        backend = InMemoryLockBackend()
        lock_a = DistributedLock("scope", backend=backend, holder_id="A")
        lease_a = lock_a.try_acquire(ttl_seconds=60)
        assert lock_a.is_current_token(token=lease_a.token)

        lock_a.release()
        lock_b = DistributedLock("scope", backend=backend, holder_id="B")
        lock_b.try_acquire(ttl_seconds=60)

        # A's stale token should no longer match.
        assert not lock_a.is_current_token(token=lease_a.token)

    def test_context_manager_releases_on_exit(self):
        from runtime.platform.process.distributed_lock import DistributedLock

        lock = DistributedLock("test")
        with lock.acquire(ttl_seconds=60) as lease:
            assert lease is not None
        # Should be released.
        assert lock.current_token() is None

    def test_context_manager_yields_none_on_conflict(self):
        from runtime.platform.process.distributed_lock import (
            DistributedLock,
            InMemoryLockBackend,
        )

        backend = InMemoryLockBackend()
        lock_a = DistributedLock("scope", backend=backend, holder_id="A")
        lock_b = DistributedLock("scope", backend=backend, holder_id="B")
        lock_a.try_acquire(ttl_seconds=60)
        with lock_b.acquire(ttl_seconds=60) as lease:
            assert lease is None

    def test_concurrent_acquire_only_one_winner(self):
        from runtime.platform.process.distributed_lock import (
            DistributedLock,
            InMemoryLockBackend,
        )

        backend = InMemoryLockBackend()
        winners: list[str] = []
        lock_lock = threading.Lock()

        def worker(holder_id: str):
            lock = DistributedLock("shared", backend=backend, holder_id=holder_id)
            lease = lock.try_acquire(ttl_seconds=60)
            if lease is not None:
                with lock_lock:
                    winners.append(holder_id)

        threads = [threading.Thread(target=worker, args=(f"holder-{i}",)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(winners) == 1


# ═══════════════════════════════════════════════════════════════
# 2. LLM Response Cache
# ═══════════════════════════════════════════════════════════════


class TestLLMResponseCache:
    @pytest.fixture
    def router_fixture(self):
        from runtime.sensing.model_router.models import MockModelRouter

        return MockModelRouter(response="cached-response")

    def test_make_cache_key_stable(self):
        from runtime.platform.llm_infra.llm_cache import make_cache_key
        from runtime.sensing.model_router.models import Message, ModelRequest

        req1 = ModelRequest(
            model="m",
            max_tokens=10,
            temperature=0.0,
            messages=[Message(role="user", content="hello")],
        )
        req2 = ModelRequest(
            model="m",
            max_tokens=10,
            temperature=0.0,
            messages=[Message(role="user", content="hello")],
        )
        assert make_cache_key(req1) == make_cache_key(req2)

    def test_make_cache_key_differs_on_content(self):
        from runtime.platform.llm_infra.llm_cache import make_cache_key
        from runtime.sensing.model_router.models import Message, ModelRequest

        req1 = ModelRequest(
            model="m",
            max_tokens=10,
            temperature=0.0,
            messages=[Message(role="user", content="hello")],
        )
        req2 = ModelRequest(
            model="m",
            max_tokens=10,
            temperature=0.0,
            messages=[Message(role="user", content="world")],
        )
        assert make_cache_key(req1) != make_cache_key(req2)

    def test_cached_router_hit_avoids_underlying_call(self, router_fixture):
        from runtime.platform.llm_infra.llm_cache import CachedModelRouter
        from runtime.sensing.model_router.models import Message, ModelRequest

        cached = CachedModelRouter(router_fixture)
        req = ModelRequest(
            model="m",
            temperature=0.0,
            messages=[Message(role="user", content="a")],
        )
        r1 = cached.call(req)
        r2 = cached.call(req)
        assert r1.text == r2.text
        # The underlying router only logged one call.
        assert len(router_fixture.call_log) == 1

    def test_cached_router_miss_passes_through(self, router_fixture):
        from runtime.platform.llm_infra.llm_cache import CachedModelRouter
        from runtime.sensing.model_router.models import Message, ModelRequest

        cached = CachedModelRouter(router_fixture)
        req1 = ModelRequest(
            model="m",
            temperature=0.0,
            messages=[Message(role="user", content="a")],
        )
        req2 = ModelRequest(
            model="m",
            temperature=0.0,
            messages=[Message(role="user", content="b")],
        )
        cached.call(req1)
        cached.call(req2)
        assert len(router_fixture.call_log) == 2

    def test_skips_temp_nonzero_by_default(self, router_fixture):
        from runtime.platform.llm_infra.llm_cache import CachedModelRouter
        from runtime.sensing.model_router.models import Message, ModelRequest

        cached = CachedModelRouter(router_fixture)
        req = ModelRequest(
            model="m",
            temperature=0.7,
            messages=[Message(role="user", content="a")],
        )
        cached.call(req)
        cached.call(req)
        # Both calls hit the underlying router — caching was skipped.
        assert len(router_fixture.call_log) == 2
        stats = cached.cache.stats()
        assert stats["skipped"] >= 2

    def test_opt_in_caches_temp_nonzero(self, router_fixture):
        from runtime.platform.llm_infra.llm_cache import CachedModelRouter
        from runtime.sensing.model_router.models import Message, ModelRequest

        cached = CachedModelRouter(router_fixture, cache_temp_zero_only=False)
        req = ModelRequest(
            model="m",
            temperature=0.5,
            messages=[Message(role="user", content="a")],
        )
        cached.call(req)
        cached.call(req)
        assert len(router_fixture.call_log) == 1

    def test_stats_track_hits_and_savings(self, router_fixture):
        from runtime.platform.llm_infra.llm_cache import CachedModelRouter
        from runtime.sensing.model_router.models import Message, ModelRequest

        cached = CachedModelRouter(router_fixture)
        req = ModelRequest(
            model="m",
            temperature=0.0,
            messages=[Message(role="user", content="a")],
        )
        cached.call(req)
        cached.call(req)
        cached.call(req)
        stats = cached.cache.stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["hit_ratio"] > 0.5
        assert stats["tokens_saved"] > 0

    def test_clear_resets_stats(self, router_fixture):
        from runtime.platform.llm_infra.llm_cache import CachedModelRouter
        from runtime.sensing.model_router.models import Message, ModelRequest

        cached = CachedModelRouter(router_fixture)
        req = ModelRequest(
            model="m",
            temperature=0.0,
            messages=[Message(role="user", content="a")],
        )
        cached.call(req)
        cached.call(req)
        cached.cache.clear()
        assert cached.cache.stats()["size"] == 0
        assert cached.cache.stats()["hits"] == 0

    def test_ttl_expiry_refetches(self, router_fixture):
        from runtime.platform.llm_infra.llm_cache import (
            CachedModelRouter,
            InMemoryCacheBackend,
            LLMResponseCache,
        )
        from runtime.sensing.model_router.models import Message, ModelRequest

        backend = InMemoryCacheBackend()
        cache = LLMResponseCache(backend=backend, ttl_seconds=0.05)
        cached = CachedModelRouter(router_fixture, cache=cache)
        req = ModelRequest(
            model="m",
            temperature=0.0,
            messages=[Message(role="user", content="a")],
        )
        cached.call(req)
        time.sleep(0.1)
        cached.call(req)
        assert len(router_fixture.call_log) == 2

    def test_lru_eviction_at_capacity(self):
        from runtime.platform.llm_infra.llm_cache import InMemoryCacheBackend

        backend = InMemoryCacheBackend(max_entries=3)
        for i in range(5):
            backend.set(f"k{i}", f"v{i}", ttl_seconds=60)
        # Only 3 most-recent should be retained.
        assert backend.size() == 3
        assert backend.get("k0") is None
        assert backend.get("k4") == "v4"

    def test_passthrough_attrs_work(self, router_fixture):
        from runtime.platform.llm_infra.llm_cache import CachedModelRouter

        cached = CachedModelRouter(router_fixture)
        # call_log is on the underlying router — should be accessible.
        assert hasattr(cached, "call_log")


# ═══════════════════════════════════════════════════════════════
# 3. Health Check Framework
# ═══════════════════════════════════════════════════════════════


class TestHealthCheck:
    def test_probe_all_pass(self):
        from runtime.platform.observability.health import (
            HealthCheck,
            HealthRegistry,
            HealthStatus,
        )

        reg = HealthRegistry(parallel=False)
        reg.register(HealthCheck(name="a", check=lambda: True, kind="readiness"))
        reg.register(
            HealthCheck(
                name="b",
                check=lambda: HealthStatus(name="b", status="pass"),
                kind="readiness",
            )
        )
        result = reg.probe(kind="readiness")
        assert result["status"] == "pass"
        assert len(result["checks"]) == 2

    def test_probe_fails_on_critical_check(self):
        from runtime.platform.observability.health import HealthCheck, HealthRegistry

        reg = HealthRegistry(parallel=False)
        reg.register(HealthCheck(name="a", check=lambda: True))
        reg.register(HealthCheck(name="b", check=lambda: False, critical=True))
        result = reg.probe()
        assert result["status"] == "fail"

    def test_noncritical_failure_is_warn(self):
        from runtime.platform.observability.health import HealthCheck, HealthRegistry

        reg = HealthRegistry(parallel=False)
        reg.register(HealthCheck(name="a", check=lambda: True))
        reg.register(HealthCheck(name="b", check=lambda: False, critical=False))
        result = reg.probe()
        assert result["status"] == "warn"

    def test_liveness_vs_readiness_isolation(self):
        from runtime.platform.observability.health import HealthCheck, HealthRegistry

        reg = HealthRegistry(parallel=False)
        reg.register(HealthCheck(name="live_ok", check=lambda: True, kind="liveness"))
        reg.register(HealthCheck(name="ready_fail", check=lambda: False, kind="readiness"))
        assert reg.probe(kind="liveness")["status"] == "pass"
        assert reg.probe(kind="readiness")["status"] == "fail"

    def test_timeout_marks_fail(self):
        from runtime.platform.observability.health import HealthCheck, HealthRegistry

        def slow():
            time.sleep(0.5)
            return True

        reg = HealthRegistry(parallel=False)
        reg.register(HealthCheck(name="slow", check=slow, timeout_seconds=0.1))
        result = reg.probe()
        assert result["status"] == "fail"
        assert any("timed out" in c["detail"] for c in result["checks"])

    def test_exception_caught_as_fail(self):
        from runtime.platform.observability.health import HealthCheck, HealthRegistry

        def broken():
            raise RuntimeError("oops")

        reg = HealthRegistry(parallel=False)
        reg.register(HealthCheck(name="broken", check=broken))
        result = reg.probe()
        assert result["status"] == "fail"
        assert "RuntimeError" in result["checks"][0]["detail"]

    def test_parallel_probe_runs_faster_than_sequential(self):
        from runtime.platform.observability.health import HealthCheck, HealthRegistry

        def slow():
            time.sleep(0.05)
            return True

        reg = HealthRegistry(parallel=True)
        for i in range(5):
            reg.register(HealthCheck(name=f"s{i}", check=slow, timeout_seconds=2))
        started = time.time()
        result = reg.probe()
        elapsed = time.time() - started
        assert result["status"] == "pass"
        # Parallel: ~0.05s not ~0.25s.
        assert elapsed < 0.2

    def test_unregister_removes_check(self):
        from runtime.platform.observability.health import HealthCheck, HealthRegistry

        reg = HealthRegistry()
        reg.register(HealthCheck(name="a", check=lambda: True))
        assert reg.unregister("a") is True
        assert reg.names() == []

    def test_disk_check(self, tmp_path):
        from runtime.platform.observability.health import HealthRegistry, disk_check

        reg = HealthRegistry(parallel=False)
        reg.register(disk_check(str(tmp_path), min_free_mb=0))
        result = reg.probe()
        assert result["status"] == "pass"

    def test_disk_check_fails_on_high_min(self, tmp_path):
        from runtime.platform.observability.health import HealthRegistry, disk_check

        reg = HealthRegistry(parallel=False)
        reg.register(disk_check(str(tmp_path), min_free_mb=10_000_000_000))
        result = reg.probe()
        assert result["status"] == "fail"

    def test_redis_check_pass(self):
        from runtime.platform.observability.health import HealthRegistry, redis_check

        client = MagicMock()
        client.ping.return_value = True
        reg = HealthRegistry(parallel=False)
        reg.register(redis_check(client))
        result = reg.probe()
        assert result["status"] == "pass"

    def test_redis_check_fail(self):
        from runtime.platform.observability.health import HealthRegistry, redis_check

        client = MagicMock()
        client.ping.side_effect = ConnectionError("down")
        reg = HealthRegistry(parallel=False)
        reg.register(redis_check(client))
        result = reg.probe()
        assert result["status"] == "fail"

    def test_journal_check_uses_read_all(self):
        from runtime.platform.observability.health import HealthRegistry, journal_check

        journal = MagicMock()
        journal.read_all.return_value = []
        reg = HealthRegistry(parallel=False)
        reg.register(journal_check(journal))
        result = reg.probe()
        assert result["status"] == "pass"

    def test_metrics_integration(self):
        from runtime.platform.observability.health import HealthCheck, HealthRegistry
        from runtime.platform.observability.metrics import MetricsRegistry

        metrics = MetricsRegistry()
        reg = HealthRegistry(parallel=False, metrics_registry=metrics)
        reg.register(HealthCheck(name="x", check=lambda: True))
        reg.probe()
        gauge = metrics.get("echo_health_check_status")
        assert gauge is not None
        assert gauge.value(labels={"name": "x", "kind": "readiness"}) == 1.0

    def test_create_probe_router(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from runtime.platform.observability.health import (
            HealthCheck,
            HealthRegistry,
            create_probe_router,
        )

        reg = HealthRegistry(parallel=False)
        reg.register(HealthCheck(name="x", check=lambda: True, kind="readiness"))
        reg.register(HealthCheck(name="live", check=lambda: True, kind="liveness"))

        app = FastAPI()
        app.include_router(create_probe_router(reg))
        client = TestClient(app)

        live = client.get("/livez")
        assert live.status_code == 200
        assert live.json()["status"] == "pass"

        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["status"] == "pass"

    def test_probe_router_returns_503_on_fail(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from runtime.platform.observability.health import (
            HealthCheck,
            HealthRegistry,
            create_probe_router,
        )

        reg = HealthRegistry(parallel=False)
        reg.register(HealthCheck(name="broken", check=lambda: False, kind="readiness"))

        app = FastAPI()
        app.include_router(create_probe_router(reg))
        client = TestClient(app)

        resp = client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json()["status"] == "fail"
