"""Tests for EnterpriseDecisionCache (enterprise Ganglion layer)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from runtime.execution.arms.enterprise_cache import EnterpriseDecisionCache

# ─── lifecycle ──────────────────────────────────────────────


class TestEnterpriseDecisionCacheLifecycle:
    def test_start_stop(self):
        cache = EnterpriseDecisionCache(sync_interval=10)
        cache.start()
        assert cache._running is True
        assert cache._thread is not None
        cache.stop()
        assert cache._running is False

    def test_start_idempotent(self):
        cache = EnterpriseDecisionCache(sync_interval=10)
        cache.start()
        cache.start()  # second start should be no-op
        assert cache._running is True
        cache.stop()

    def test_stop_idempotent(self):
        cache = EnterpriseDecisionCache()
        cache.stop()  # stop without start should be safe


# ─── cache operations ───────────────────────────────────────


class TestEnterpriseDecisionCacheOps:
    def test_put_and_get(self):
        cache = EnterpriseDecisionCache()
        cache._put("tasks:proj1::20", {"ok": True, "tasks": []})
        result = cache.get_tasks("proj1")
        assert result is not None
        assert result["ok"] is True

    def test_get_approvals(self):
        cache = EnterpriseDecisionCache()
        cache._put("approvals::20", {"ok": True, "approvals": []})
        result = cache.get_approvals()
        assert result is not None
        assert result["ok"] is True

    def test_get_persons(self):
        cache = EnterpriseDecisionCache()
        cache._put("persons:50", {"ok": True, "persons": []})
        result = cache.get_persons()
        assert result is not None
        assert result["ok"] is True

    def test_get_missing_returns_none(self):
        cache = EnterpriseDecisionCache()
        assert cache.get_tasks("nonexistent") is None
        assert cache.get_approvals(status="pending") is None
        assert cache.get_persons(limit=10) is None

    def test_cache_size(self):
        cache = EnterpriseDecisionCache()
        assert cache.cache_size == 0
        cache._put("a", {"x": 1})
        assert cache.cache_size == 1
        cache._put("b", {"y": 2})
        assert cache.cache_size == 2

    def test_is_fresh(self):
        cache = EnterpriseDecisionCache(ttl=1)
        cache._put("key", {"data": True})
        assert cache._is_fresh("key") is True
        time.sleep(1.1)
        assert cache._is_fresh("key") is False

    def test_service_ok_default(self):
        cache = EnterpriseDecisionCache()
        assert cache.is_service_ok() is False


# ─── sync ────────────────────────────────────────────────────


class TestEnterpriseDecisionCacheSync:
    def test_sync_once_service_down(self):
        cache = EnterpriseDecisionCache()
        with patch("runtime.execution.arms.enterprise_cache._request", return_value=None):
            cache._sync_once()
        assert cache.is_service_ok() is False
        assert cache.cache_size == 0

    def test_sync_once_service_ok(self):
        cache = EnterpriseDecisionCache()
        with patch(
            "runtime.execution.arms.enterprise_cache._request",
            side_effect=lambda path, **kw: {
                "/health": {"status": "ok"},
                "/approvals?skip=0&limit=20": {"data": [], "total": 0},
                "/persons?skip=0&limit=50": {"data": [], "total": 0},
            }.get(path),
        ):
            cache._sync_once()
        assert cache.is_service_ok() is True
        assert cache.cache_size == 2

    def test_sync_loop_runs_and_stops(self):
        cache = EnterpriseDecisionCache(sync_interval=1)
        call_count = 0
        original = cache._sync_once

        def counting_sync():
            nonlocal call_count
            call_count += 1
            original()

        cache._sync_once = counting_sync
        cache.start()
        time.sleep(2.5)
        cache.stop()
        assert call_count >= 1


# ─── Worker decision_cache integration ──────────────────────


class TestWorkerDecisionCache:
    def test_worker_default_no_cache(self):
        from runtime.execution.arms.base import Worker
        from runtime.platform.models import ArmId, SkillId

        runtime = MagicMock()
        w = Worker(
            arm_id=ArmId("test_arm"),
            affinity=["test"],
            allowed_skills=[SkillId("test_skill")],
            runtime=runtime,
        )
        assert w.decision_cache is None

    def test_worker_with_decision_cache(self):
        from runtime.execution.arms.base import Worker
        from runtime.platform.models import ArmId, SkillId

        runtime = MagicMock()
        cache = EnterpriseDecisionCache()
        w = Worker(
            arm_id=ArmId("test_arm"),
            affinity=["test"],
            allowed_skills=[SkillId("test_skill")],
            runtime=runtime,
            decision_cache=cache,
        )
        assert w.decision_cache is cache

