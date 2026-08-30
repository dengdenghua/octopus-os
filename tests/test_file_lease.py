"""Tests for runtime.platform.io.lease."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from runtime.platform.io.lease import (
    FileLease,
    LeaseConflictError,
    LeaseNotFoundError,
    LeaseStore,
)


def _store(tmp_path: Path) -> LeaseStore:
    return LeaseStore(db_path=tmp_path / "file_leases.db")


def _backdate(store: LeaseStore, lease_id: str, seconds_ago: float = 10.0) -> None:
    """Set a lease's ``expires_at`` to the past, simulating expiry.

    Opens a side connection so the store's own lock isn't held — the
    store reads the row fresh on its next call.
    """
    conn = sqlite3.connect(str(store._db_path))  # noqa: SLF001 — test fixture
    try:
        conn.execute(
            "UPDATE file_leases SET expires_at = ? WHERE lease_id = ?",
            (time.time() - seconds_ago, lease_id),
        )
        conn.commit()
    finally:
        conn.close()


# ─── acquire ──────────────────────────────────────────────────────────────────


def test_acquire_success(tmp_path: Path) -> None:
    store = _store(tmp_path)
    lease = store.acquire("ws1", "src/main.py", "alice", ttl_seconds=60)
    assert isinstance(lease, FileLease)
    assert lease.workspace_id == "ws1"
    assert lease.file_path == "src/main.py"
    assert lease.holder_id == "alice"
    assert lease.kind == "exclusive"
    assert lease.lease_id
    assert lease.expires_at > lease.acquired_at
    assert lease.expires_at - lease.acquired_at == pytest.approx(60, abs=1)


def test_acquire_conflict(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.acquire("ws1", "src/main.py", "alice", ttl_seconds=60)
    with pytest.raises(LeaseConflictError) as exc_info:
        store.acquire("ws1", "src/main.py", "bob", ttl_seconds=60)
    conflict = exc_info.value
    assert conflict.lease.holder_id == "alice"
    assert conflict.lease.file_path == "src/main.py"
    # Different workspaces don't conflict.
    other = store.acquire("ws2", "src/main.py", "bob", ttl_seconds=60)
    assert other.holder_id == "bob"


def test_acquire_same_holder_renew(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.acquire("ws1", "src/main.py", "alice", ttl_seconds=60)
    time.sleep(0.01)
    second = store.acquire("ws1", "src/main.py", "alice", ttl_seconds=120)
    assert second.lease_id == first.lease_id  # same lease, renewed
    assert second.expires_at > first.expires_at  # extended
    assert second.acquired_at == first.acquired_at  # preserved
    # No duplicate rows — only one active lease.
    active = store.list_active("ws1")
    assert len(active) == 1


# ─── renew ────────────────────────────────────────────────────────────────────


def test_renew_success(tmp_path: Path) -> None:
    store = _store(tmp_path)
    lease = store.acquire("ws1", "src/main.py", "alice", ttl_seconds=60)
    time.sleep(0.01)
    renewed = store.renew(lease.lease_id, ttl_seconds=120)
    assert renewed.lease_id == lease.lease_id
    assert renewed.expires_at > lease.expires_at
    assert renewed.acquired_at == lease.acquired_at  # preserved
    assert renewed.holder_id == lease.holder_id


def test_renew_not_found(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # Nonexistent lease id.
    with pytest.raises(LeaseNotFoundError):
        store.renew("nonexistent-lease-id", ttl_seconds=60)
    # Expired lease is treated as not found.
    lease = store.acquire("ws1", "src/main.py", "alice", ttl_seconds=60)
    _backdate(store, lease.lease_id)
    with pytest.raises(LeaseNotFoundError):
        store.renew(lease.lease_id, ttl_seconds=60)
    # The expired row was purged as part of the renew call.
    assert store.list_active("ws1") == []


# ─── release ──────────────────────────────────────────────────────────────────


def test_release_success(tmp_path: Path) -> None:
    store = _store(tmp_path)
    lease = store.acquire("ws1", "src/main.py", "alice", ttl_seconds=60)
    assert store.release(lease.lease_id) is True
    assert store.get_by_path("ws1", "src/main.py") is None
    # Releasing an already-released (or unknown) lease returns False.
    assert store.release(lease.lease_id) is False
    assert store.release("never-existed") is False


# ─── queries ──────────────────────────────────────────────────────────────────


def test_get_by_path(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # No lease initially.
    assert store.get_by_path("ws1", "src/main.py") is None
    lease = store.acquire("ws1", "src/main.py", "alice", ttl_seconds=60)
    fetched = store.get_by_path("ws1", "src/main.py")
    assert fetched is not None
    assert fetched.lease_id == lease.lease_id
    # Different workspace: nothing.
    assert store.get_by_path("ws2", "src/main.py") is None
    # Expired lease is not returned.
    _backdate(store, lease.lease_id)
    assert store.get_by_path("ws1", "src/main.py") is None


def test_get_by_holder(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.acquire("ws1", "a.py", "alice", ttl_seconds=60)
    store.acquire("ws1", "b.py", "alice", ttl_seconds=60)
    store.acquire("ws1", "c.py", "bob", ttl_seconds=60)
    alice_leases = store.get_by_holder("alice")
    assert len(alice_leases) == 2
    assert {lease.file_path for lease in alice_leases} == {"a.py", "b.py"}
    bob_leases = store.get_by_holder("bob")
    assert len(bob_leases) == 1
    assert bob_leases[0].file_path == "c.py"
    # Expired leases are excluded.
    _backdate(store, alice_leases[0].lease_id)
    alice_active = store.get_by_holder("alice")
    assert len(alice_active) == 1
    assert alice_active[0].file_path == "b.py"


# ─── maintenance ──────────────────────────────────────────────────────────────


def test_cleanup_expired(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.acquire("ws1", "a.py", "alice", ttl_seconds=60)
    store.acquire("ws1", "b.py", "bob", ttl_seconds=60)
    # Nothing expired yet.
    assert store.cleanup_expired() == 0
    _backdate(store, a.lease_id)
    removed = store.cleanup_expired()
    assert removed == 1
    # Only bob's lease survives.
    active = store.list_active("ws1")
    assert len(active) == 1
    assert active[0].file_path == "b.py"
    # A second sweep finds nothing more.
    assert store.cleanup_expired() == 0


def test_list_active(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.acquire("ws1", "a.py", "alice", ttl_seconds=60)
    store.acquire("ws2", "b.py", "bob", ttl_seconds=60)
    # All active.
    all_active = store.list_active()
    assert len(all_active) == 2
    # Filtered by workspace.
    ws1_active = store.list_active("ws1")
    assert len(ws1_active) == 1
    assert ws1_active[0].workspace_id == "ws1"
    ws2_active = store.list_active("ws2")
    assert len(ws2_active) == 1
    assert ws2_active[0].workspace_id == "ws2"
    # Unknown workspace → empty.
    assert store.list_active("ws3") == []
    # Expired leases are excluded.
    _backdate(store, ws1_active[0].lease_id)
    assert store.list_active("ws1") == []
    assert len(store.list_active()) == 1


# ─── shared lease ─────────────────────────────────────────────────────────────


def test_shared_lease(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.acquire("ws1", "shared.py", "alice", ttl_seconds=60, kind="shared")
    b = store.acquire("ws1", "shared.py", "bob", ttl_seconds=60, kind="shared")
    assert a.lease_id != b.lease_id
    assert a.kind == "shared"
    assert b.kind == "shared"
    # Both coexist on the same file.
    active = store.list_active("ws1")
    assert len(active) == 2
    holders = {lease.holder_id for lease in active}
    assert holders == {"alice", "bob"}

