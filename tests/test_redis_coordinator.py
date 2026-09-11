"""Implementation note."""

from __future__ import annotations

import time
from typing import Any

import pytest

from runtime.core.hearts.coordinator import Lease
from runtime.core.hearts.redis_coordinator import RedisCoordinator

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class _FakeScript:
    def __init__(self, script: str, store: _FakeRedis):
        self.script = script
        self.store = store

    def __call__(self, keys: list[str], args: list[Any]) -> int:
        # Implementation note.
        if "PEXPIRE" in self.script:
            # Implementation note.
            k = keys[0]
            v = self.store.get(k)
            expected = args[0].encode("utf-8") if isinstance(args[0], str) else args[0]
            if v == expected:
                self.store.pexpire(k, int(args[1]))
                return 1
            return 0
        if "DEL" in self.script:
            k = keys[0]
            v = self.store.get(k)
            expected = args[0].encode("utf-8") if isinstance(args[0], str) else args[0]
            if v == expected:
                self.store.delete(k)
                return 1
            return 0
        return 0


class _FakeRedis:
    """Implementation note."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self._ttl: dict[str, float] = {}  # key → expires_at (monotonic-like)
        self._counter = 0

    def _expired(self, key: str) -> bool:
        if key not in self._ttl:
            return False
        if time.time() > self._ttl[key]:
            self._store.pop(key, None)
            self._ttl.pop(key, None)
            return True
        return False

    def get(self, key: str) -> bytes | None:
        if self._expired(key):
            return None
        return self._store.get(key)

    def set(
        self,
        key: str,
        value: str | bytes,
        *,
        nx: bool = False,
        px: int | None = None,
    ) -> bool:
        if self._expired(key):
            pass  # Implementation note.
        if nx and key in self._store:
            return False
        self._store[key] = value.encode("utf-8") if isinstance(value, str) else value
        if px is not None:
            self._ttl[key] = time.time() + px / 1000.0
        return True

    def delete(self, key: str) -> int:
        existed = key in self._store
        self._store.pop(key, None)
        self._ttl.pop(key, None)
        return 1 if existed else 0

    def incr(self, key: str) -> int:
        if self._expired(key):
            pass
        current = int(self._store.get(key, b"0"))
        current += 1
        self._store[key] = str(current).encode()
        return current

    def pexpire(self, key: str, ms: int) -> int:
        if key not in self._store:
            return 0
        self._ttl[key] = time.time() + ms / 1000.0
        return 1

    def pttl(self, key: str) -> int:
        if key not in self._store:
            return -2
        if key not in self._ttl:
            return -1
        remaining_ms = int((self._ttl[key] - time.time()) * 1000)
        if remaining_ms <= 0:
            self._store.pop(key, None)
            self._ttl.pop(key, None)
            return -2
        return remaining_ms

    def register_script(self, script: str) -> _FakeScript:
        return _FakeScript(script, self)


# ═══════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def client() -> _FakeRedis:
    return _FakeRedis()


@pytest.fixture
def coord(client: _FakeRedis) -> RedisCoordinator:
    return RedisCoordinator(client, key_prefix="test:", holder_id="node-A")


class TestAcquire:
    def test_fresh_acquire_succeeds(self, coord: RedisCoordinator):
        lease = coord.acquire_lease("scope1", ttl=10)
        assert lease is not None
        assert lease.scope == "scope1"
        assert lease.holder_id == "node-A"
        assert lease.fencing_token > 0

    def test_second_holder_cannot_acquire(self, client: _FakeRedis):
        a = RedisCoordinator(client, key_prefix="t:", holder_id="A")
        b = RedisCoordinator(client, key_prefix="t:", holder_id="B")
        la = a.acquire_lease("scope1", ttl=10)
        assert la is not None
        lb = b.acquire_lease("scope1", ttl=10)
        assert lb is None

    def test_same_holder_reacquire_acts_as_renew(self, coord: RedisCoordinator):
        l1 = coord.acquire_lease("scope1", ttl=10)
        l2 = coord.acquire_lease("scope1", ttl=20)
        assert l2 is not None
        # Implementation note.
        assert l2.fencing_token == l1.fencing_token

    def test_rejects_zero_ttl(self, coord: RedisCoordinator):
        with pytest.raises(ValueError):
            coord.acquire_lease("scope1", ttl=0)

    def test_fencing_token_monotonic(self, coord: RedisCoordinator):
        l1 = coord.acquire_lease("s1", ttl=10)
        l2 = coord.acquire_lease("s2", ttl=10)
        assert l2.fencing_token > l1.fencing_token


class TestRenew:
    def test_renew_extends_ttl(self, coord: RedisCoordinator, client: _FakeRedis):
        lease = coord.acquire_lease("s", ttl=1.0)
        assert lease is not None
        renewed = coord.renew_lease(lease, ttl=10)
        assert renewed is not None
        assert renewed.fencing_token == lease.fencing_token
        pttl = client.pttl("test:s")
        assert pttl > 5000  # Implementation note.

    def test_renew_wrong_holder_returns_none(self, client: _FakeRedis):
        a = RedisCoordinator(client, key_prefix="t:", holder_id="A")
        b = RedisCoordinator(client, key_prefix="t:", holder_id="B")
        la = a.acquire_lease("scope", ttl=10)
        # Implementation note.
        fake = Lease(
            scope="scope",
            holder_id="A",
            acquired_at=la.acquired_at,
            expires_at=la.expires_at,
            fencing_token=la.fencing_token,
        )
        # Implementation note.
        r = b.renew_lease(fake, ttl=10)
        assert r is None

    def test_renew_after_expiry_returns_none(
        self,
        coord: RedisCoordinator,
        client: _FakeRedis,
    ):
        lease = coord.acquire_lease("s", ttl=0.01)
        assert lease is not None
        time.sleep(0.05)  # Implementation note.
        # Implementation note.
        r = coord.renew_lease(lease, ttl=10)
        assert r is None

    def test_renew_after_other_takeover_fails(self, client: _FakeRedis):
        a = RedisCoordinator(client, key_prefix="t:", holder_id="A")
        b = RedisCoordinator(client, key_prefix="t:", holder_id="B")
        la = a.acquire_lease("scope", ttl=0.01)
        time.sleep(0.05)
        lb = b.acquire_lease("scope", ttl=10)
        assert lb is not None
        # Implementation note.
        r = a.renew_lease(la, ttl=10)
        assert r is None


class TestRelease:
    def test_release_own_lease(self, coord: RedisCoordinator, client: _FakeRedis):
        lease = coord.acquire_lease("s", ttl=10)
        ok = coord.release_lease(lease)
        assert ok is True
        assert client.get("test:s") is None

    def test_release_non_matching_returns_false(self, client: _FakeRedis):
        a = RedisCoordinator(client, key_prefix="t:", holder_id="A")
        b = RedisCoordinator(client, key_prefix="t:", holder_id="B")
        la = a.acquire_lease("s", ttl=10)
        # Implementation note.
        ok = b.release_lease(la)
        assert ok is False
        # Implementation note.
        assert client.get("t:s") is not None


class TestCurrentLease:
    def test_none_when_empty(self, coord: RedisCoordinator):
        assert coord.current_lease("nope") is None

    def test_returns_lease_with_holder(self, coord: RedisCoordinator):
        acquired = coord.acquire_lease("s", ttl=10)
        current = coord.current_lease("s")
        assert current is not None
        assert current.holder_id == "node-A"
        assert current.fencing_token == acquired.fencing_token

    def test_none_after_expiry(self, coord: RedisCoordinator):
        coord.acquire_lease("s", ttl=0.01)
        time.sleep(0.05)
        assert coord.current_lease("s") is None


class TestCrossHolder:
    def test_different_scopes_dont_conflict(self, client: _FakeRedis):
        a = RedisCoordinator(client, holder_id="A")
        b = RedisCoordinator(client, holder_id="B")
        la = a.acquire_lease("scope-x", ttl=10)
        lb = b.acquire_lease("scope-y", ttl=10)
        assert la is not None
        assert lb is not None

    def test_failover_after_holder_dies(self, client: _FakeRedis):
        """Implementation note."""
        a = RedisCoordinator(client, holder_id="A")
        b = RedisCoordinator(client, holder_id="B")
        la = a.acquire_lease("failover", ttl=0.01)
        assert la is not None
        assert b.acquire_lease("failover", ttl=10) is None
        time.sleep(0.05)
        lb = b.acquire_lease("failover", ttl=10)
        assert lb is not None
        assert lb.holder_id == "B"


class TestClientDuckTyping:
    def test_accepts_non_redis_lib_client(self, client: _FakeRedis):
        """Implementation note."""
        # Implementation note.
        coord = RedisCoordinator(client)
        assert coord.acquire_lease("s", ttl=5) is not None

    def test_rejects_incompatible_client(self):
        class Broken:
            pass

        with pytest.raises(ImportError):
            # Implementation note.
            # Implementation note.
            from runtime.core.hearts import redis_coordinator as rc

            if rc.REDIS_AVAILABLE:
                pytest.skip("redis is installed · duck check bypassed")
            RedisCoordinator(Broken())
