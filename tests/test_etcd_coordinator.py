"""Implementation note."""

from __future__ import annotations

import time
from typing import Any

import pytest

from runtime.core.hearts.coordinator import Lease
from runtime.core.hearts.etcd_coordinator import EtcdCoordinator

# ═══════════════════════════════════════════════════════════
# Fake etcd3 client
# ═══════════════════════════════════════════════════════════


class _FakeMeta:
    def __init__(self, mod_revision: int):
        self.mod_revision = mod_revision


class _FakeLease:
    _next_id = 1000

    def __init__(self, store: _FakeEtcd, ttl: int):
        self.id = _FakeLease._next_id
        _FakeLease._next_id += 1
        self.ttl = ttl
        self.expires_at = time.time() + ttl
        self._store = store

    def revoke(self):
        self._store._revoke_lease(self.id)


class _Cmp:
    """Simulate etcd3 transactions comparison objects."""

    def __init__(self, kind: str, key: str, expected: Any = None):
        self.kind = kind  # "create" | "value"
        self.key = key
        self.expected = expected


class _Op:
    """Simulate transactions put/delete op."""

    def __init__(self, kind: str, key: str, value: Any = None, lease: Any = None):
        self.kind = kind
        self.key = key
        self.value = value
        self.lease = lease


class _FakeTransactions:
    def create(self, key: str):
        class _Eq:
            def __eq__(self, other):  # noqa: N807
                return _Cmp("create", key, other)

        return _Eq()

    def value(self, key: str):
        class _Eq:
            def __eq__(self, other):  # noqa: N807
                return _Cmp("value", key, other)

        return _Eq()

    def put(self, key: str, value: str, lease: Any = None) -> _Op:
        return _Op("put", key, value, lease)

    def delete(self, key: str) -> _Op:
        return _Op("delete", key)


class _FakeEtcd:
    def __init__(self):
        self._store: dict[
            str, tuple[bytes, int, int | None]
        ] = {}  # key → (value, mod_rev, lease_id)
        self._revision = 0
        self._leases: dict[int, _FakeLease] = {}
        self.transactions = _FakeTransactions()

    def _bump(self) -> int:
        self._revision += 1
        return self._revision

    def _gc_expired(self):
        now = time.time()
        to_delete = []
        for lid, lease in list(self._leases.items()):
            if lease.expires_at <= now:
                to_delete.append(lid)
        for lid in to_delete:
            self._revoke_lease(lid)

    def _revoke_lease(self, lease_id: int):
        if lease_id in self._leases:
            del self._leases[lease_id]
        # Implementation note.
        keys = [k for k, (_v, _r, lid) in self._store.items() if lid == lease_id]
        for k in keys:
            del self._store[k]

    def get(self, key: str):
        self._gc_expired()
        if key not in self._store:
            return (None, None)
        v, r, _l = self._store[key]
        return (v, _FakeMeta(r))

    def put(self, key: str, value: str, lease: Any = None):
        self._gc_expired()
        rev = self._bump()
        lid = lease.id if lease is not None else None
        val_bytes = value.encode("utf-8") if isinstance(value, str) else value
        self._store[key] = (val_bytes, rev, lid)

    def delete(self, key: str):
        self._store.pop(key, None)

    def lease(self, ttl: int) -> _FakeLease:
        lease = _FakeLease(self, ttl)
        self._leases[lease.id] = lease
        return lease

    def refresh_lease(self, lease_id: int):
        self._gc_expired()
        if lease_id not in self._leases:
            raise RuntimeError("lease not found")
        lease = self._leases[lease_id]
        lease.expires_at = time.time() + lease.ttl
        return [("ok", lease_id)]

    def revoke_lease(self, lease_id: int):
        self._revoke_lease(lease_id)

    def transaction(self, *, compare: list, success: list, failure: list):
        self._gc_expired()
        # Implementation note.
        all_true = True
        for cmp in compare:
            if cmp.kind == "create":
                existing = self._store.get(cmp.key)
                0 if existing is None else existing[1]  # Implementation note.
                if cmp.expected == 0 and existing is not None:
                    all_true = False
                    break
            elif cmp.kind == "value":
                existing = self._store.get(cmp.key)
                if existing is None:
                    all_true = False
                    break
                current_value = existing[0]
                expected = cmp.expected.encode() if isinstance(cmp.expected, str) else cmp.expected
                if current_value != expected:
                    all_true = False
                    break

        ops = success if all_true else failure
        for op in ops:
            if op.kind == "put":
                self.put(op.key, op.value, op.lease)
            elif op.kind == "delete":
                self.delete(op.key)
        return (all_true, [])


# ═══════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def client() -> _FakeEtcd:
    return _FakeEtcd()


@pytest.fixture
def coord(client: _FakeEtcd) -> EtcdCoordinator:
    return EtcdCoordinator(client, key_prefix="/t/", holder_id="A")


class TestAcquire:
    def test_fresh_acquire(self, coord: EtcdCoordinator):
        lease = coord.acquire_lease("s", ttl=10)
        assert lease is not None
        assert lease.holder_id == "A"
        assert lease.fencing_token > 0

    def test_second_holder_rejected(self, client: _FakeEtcd):
        a = EtcdCoordinator(client, key_prefix="/t/", holder_id="A")
        b = EtcdCoordinator(client, key_prefix="/t/", holder_id="B")
        assert a.acquire_lease("s", ttl=10) is not None
        assert b.acquire_lease("s", ttl=10) is None

    def test_self_reacquire_renews(self, coord: EtcdCoordinator):
        l1 = coord.acquire_lease("s", ttl=5)
        l2 = coord.acquire_lease("s", ttl=10)
        assert l2 is not None
        assert l2.fencing_token == l1.fencing_token

    def test_different_scopes_isolated(self, coord: EtcdCoordinator):
        l1 = coord.acquire_lease("s1", ttl=5)
        l2 = coord.acquire_lease("s2", ttl=5)
        assert l1 is not None and l2 is not None
        assert l1.fencing_token != l2.fencing_token

    def test_ttl_must_be_positive(self, coord: EtcdCoordinator):
        with pytest.raises(ValueError):
            coord.acquire_lease("s", ttl=0)


class TestRenew:
    def test_renew_keeps_token(self, coord: EtcdCoordinator):
        lease = coord.acquire_lease("s", ttl=5)
        renewed = coord.renew_lease(lease, ttl=10)
        assert renewed is not None
        assert renewed.fencing_token == lease.fencing_token

    def test_renew_wrong_holder(self, client: _FakeEtcd):
        a = EtcdCoordinator(client, holder_id="A")
        b = EtcdCoordinator(client, holder_id="B")
        la = a.acquire_lease("s", ttl=10)
        r = b.renew_lease(la, ttl=10)
        assert r is None

    def test_renew_no_active_lease(self, coord: EtcdCoordinator):
        fake = Lease(
            scope="nope",
            holder_id="A",
            acquired_at=0,
            expires_at=0,
            fencing_token=1,
        )
        assert coord.renew_lease(fake, ttl=10) is None


class TestRelease:
    def test_release_own(self, coord: EtcdCoordinator, client: _FakeEtcd):
        lease = coord.acquire_lease("s", ttl=10)
        ok = coord.release_lease(lease)
        assert ok is True
        v, _ = client.get("/t/s")
        assert v is None

    def test_release_other_holder_fails(self, client: _FakeEtcd):
        a = EtcdCoordinator(client, holder_id="A")
        b = EtcdCoordinator(client, holder_id="B")
        la = a.acquire_lease("s", ttl=10)
        ok = b.release_lease(la)
        assert ok is False


class TestCurrentLease:
    def test_none_when_missing(self, coord: EtcdCoordinator):
        assert coord.current_lease("nope") is None

    def test_reports_holder(self, coord: EtcdCoordinator):
        coord.acquire_lease("s", ttl=10)
        c = coord.current_lease("s")
        assert c is not None
        assert c.holder_id == "A"


class TestFailover:
    def test_b_takes_over_after_a_ttl_expires(self, client: _FakeEtcd):
        a = EtcdCoordinator(client, holder_id="A")
        b = EtcdCoordinator(client, holder_id="B")
        la = a.acquire_lease("failover", ttl=1)
        assert la is not None
        # Implementation note.
        assert b.acquire_lease("failover", ttl=5) is None

        # Implementation note.
        lease_id = list(client._leases.keys())[0]
        client._leases[lease_id].expires_at = time.time() - 1

        lb = b.acquire_lease("failover", ttl=10)
        assert lb is not None
        assert lb.holder_id == "B"


class TestDuckTyping:
    def test_rejects_non_etcd_client(self):
        class Broken:
            pass

        from runtime.core.hearts import etcd_coordinator as ec

        if not ec.ETCD3_AVAILABLE:
            # Implementation note.
            with pytest.raises(ImportError):
                EtcdCoordinator(Broken())
        else:
            # Implementation note.
            with pytest.raises(TypeError):
                EtcdCoordinator(Broken())
