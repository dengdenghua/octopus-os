"""Edge-case tests for checkpoint_mirror — covers gaps left by the
two earlier suites:

* ``build_checkpoint_mirror_from_url`` happy path (redis import works,
  client builds OK, returns a CheckpointMirror).
* ``redis.Redis.from_url`` raising → returns None, doesn't propagate.
* ``_CircuitBreaker`` half-open probe: success after cooldown closes
  it; another failure re-opens with a fresh cooldown.
* Breaker reopen across many failure waves.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from runtime.core.cerebrum.checkpoint_mirror import (
    CheckpointMirror,
    _CircuitBreaker,
    build_checkpoint_mirror_from_url,
)

# ══════════════════════════════════════════════════════════════════
# build_checkpoint_mirror_from_url — edges
# ══════════════════════════════════════════════════════════════════


class TestBuildFromUrlHappyPath:
    def test_returns_mirror_when_redis_available(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Inject a fake `redis` module that exposes Redis.from_url.
        captured: dict[str, str] = {}

        class _FakeClient:
            pass

        class _FakeRedisClass:
            @staticmethod
            def from_url(url: str):
                captured["url"] = url
                return _FakeClient()

        fake_module = SimpleNamespace(Redis=_FakeRedisClass)
        monkeypatch.setitem(sys.modules, "redis", fake_module)
        m = build_checkpoint_mirror_from_url("redis://test:6379/0")
        assert m is not None
        assert isinstance(m, CheckpointMirror)
        assert captured["url"] == "redis://test:6379/0"

    def test_from_url_raise_returns_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _BadRedis:
            @staticmethod
            def from_url(url: str):
                raise ValueError(f"bad url: {url}")

        monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=_BadRedis))
        # Must NOT propagate — silent None so cron stays alive.
        assert build_checkpoint_mirror_from_url("redis://broken") is None


# ══════════════════════════════════════════════════════════════════
# _CircuitBreaker half-open probe semantics
# ══════════════════════════════════════════════════════════════════


class TestBreakerHalfOpen:
    def test_probe_success_after_cooldown_closes_breaker(self) -> None:
        # Open the breaker, wait past cooldown, probe with success →
        # breaker should be fully closed (subsequent failures need to
        # re-accumulate).
        clock = [0.0]
        b = _CircuitBreaker(threshold=2, cooldown_s=10, clock=lambda: clock[0])
        b.record_failure()
        b.record_failure()
        assert b.is_open() is True

        clock[0] = 11.0  # past cooldown
        assert b.is_open() is False  # caller may probe

        # Probe succeeds → closed for real.
        b.record_success()
        # Need threshold-many failures again to re-open.
        b.record_failure()
        assert b.is_open() is False  # only 1 failure < threshold
        b.record_failure()
        assert b.is_open() is True  # threshold met, opens again

    def test_probe_failure_after_cooldown_reopens(self) -> None:
        clock = [0.0]
        b = _CircuitBreaker(threshold=1, cooldown_s=5, clock=lambda: clock[0])
        b.record_failure()  # opens immediately (threshold=1)
        assert b.is_open() is True

        clock[0] = 10.0
        assert b.is_open() is False  # cooldown elapsed

        # Probe fails → re-opens with fresh cooldown anchored at NOW.
        b.record_failure()
        assert b.is_open() is True  # opened again

        clock[0] = 12.0  # only 2s past second open; still open
        assert b.is_open() is True
        clock[0] = 16.0  # 6s past second open → cooldown elapsed
        assert b.is_open() is False

    def test_breaker_holds_open_during_cooldown(self) -> None:
        clock = [0.0]
        b = _CircuitBreaker(threshold=1, cooldown_s=5, clock=lambda: clock[0])
        b.record_failure()
        for t in (0.0, 1.0, 2.5, 4.9):
            clock[0] = t
            assert b.is_open() is True

    def test_zero_cooldown_immediately_half_open(self) -> None:
        # cooldown=0 means breaker effectively never blocks reads.
        b = _CircuitBreaker(threshold=1, cooldown_s=0, clock=lambda: 1.0)
        b.record_failure()
        # Even though we just opened, cooldown==0 elapses immediately.
        assert b.is_open() is False


# ══════════════════════════════════════════════════════════════════
# Integration: mirror probe across breaker cycles
# ══════════════════════════════════════════════════════════════════


class _CountingFlakyClient:
    """Fails until ``flip_at`` is reached, then succeeds forever."""

    def __init__(self) -> None:
        self.calls = 0
        self.flip_at = 999_999  # default: never flip
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set] = {}

    def _maybe_fail(self) -> None:
        self.calls += 1
        if self.calls < self.flip_at:
            raise RuntimeError(f"flake {self.calls}")

    def set(self, k, v):
        self._maybe_fail()
        self.kv[k] = v

    def get(self, k):
        self._maybe_fail()
        return self.kv.get(k)

    def sadd(self, k, *m):
        self._maybe_fail()
        self.sets.setdefault(k, set()).update(m)

    def srem(self, k, *m):
        self._maybe_fail()
        self.sets.get(k, set()).difference_update(m)

    def smembers(self, k):
        self._maybe_fail()
        return list(self.sets.get(k, set()))

    def delete(self, *keys):
        self._maybe_fail()
        for k in keys:
            self.kv.pop(k, None)


class TestBreakerMirrorIntegration:
    def test_breaker_recovers_after_cooldown_and_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # 1. Open breaker via consecutive failures.
        # 2. Advance clock past cooldown.
        # 3. Flip the client to succeed.
        # 4. Next put should land — breaker probe succeeds.
        client = _CountingFlakyClient()
        client.flip_at = 999_999  # always-fail mode

        clock = [0.0]
        m = CheckpointMirror(
            client,
            retry_attempts=1,
            retry_sleep_s=0.0,
            breaker_threshold=2,
            breaker_cooldown_s=5,
        )
        # Override the breaker's clock so we can advance time.
        m._breaker._clock = lambda: clock[0]

        # Two failures → breaker opens.
        assert m.put("t1", {"i": 1}) is False
        assert m.put("t2", {"i": 2}) is False
        # Third call short-circuits via breaker.
        before = client.calls
        assert m.put("t3", {"i": 3}) is False
        assert client.calls == before  # client not touched

        # Advance past cooldown + flip client to success mode.
        clock[0] = 6.0
        client.flip_at = 0  # next call succeeds

        # Probe call → breaker is half-open, lets it through; success.
        assert m.put("t4", {"i": 4}) is True
        # Breaker fully closed; subsequent calls also work.
        assert m.put("t5", {"i": 5}) is True
        assert m.get("t4") == {"i": 4}
