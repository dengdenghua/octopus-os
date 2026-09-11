"""Implementation note."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from runtime.core.hearts import (
    FileLockCoordinator,
    Hearts,
    InMemoryCoordinator,
    LeaderGuard,
    Lease,
)

# ═══════════════════════════════════════════════════════════
# InMemoryCoordinator
# ═══════════════════════════════════════════════════════════


class TestInMemoryLeaseBasics:
    def test_acquire_then_current(self):
        c = InMemoryCoordinator(holder_id="a")
        lease = c.acquire_lease("scope1", ttl=10.0)
        assert lease is not None
        assert lease.scope == "scope1"
        assert lease.holder_id == "a"
        assert lease.ttl_remaining > 0
        # Implementation note.
        cur = c.current_lease("scope1")
        assert cur is not None
        assert cur.fencing_token == lease.fencing_token

    def test_acquire_when_held_by_other_returns_none(self):
        c1 = InMemoryCoordinator(holder_id="a")
        lease = c1.acquire_lease("s", ttl=10.0)
        assert lease is not None
        # Implementation note.
        c1.holder_id = "b"
        second = c1.acquire_lease("s", ttl=10.0)
        assert second is None

    def test_self_reacquire_is_renew_like(self):
        c = InMemoryCoordinator(holder_id="a")
        first = c.acquire_lease("s", ttl=10.0)
        assert first is not None
        # Implementation note.
        second = c.acquire_lease("s", ttl=10.0)
        assert second is not None

    def test_ttl_zero_rejected(self):
        c = InMemoryCoordinator()
        with pytest.raises(ValueError):
            c.acquire_lease("s", ttl=0)
        with pytest.raises(ValueError):
            c.acquire_lease("s", ttl=-1)


class TestInMemoryExpiry:
    def test_expired_lease_auto_cleaned(self):
        c = InMemoryCoordinator(holder_id="a")
        lease = c.acquire_lease("s", ttl=0.05)
        assert lease is not None
        time.sleep(0.1)
        assert c.current_lease("s") is None

    def test_expired_lease_lets_others_acquire(self):
        c = InMemoryCoordinator(holder_id="a")
        lease = c.acquire_lease("s", ttl=0.05)
        assert lease is not None
        time.sleep(0.1)
        c.holder_id = "b"
        new = c.acquire_lease("s", ttl=10.0)
        assert new is not None
        assert new.holder_id == "b"


class TestInMemoryRenew:
    def test_renew_extends_ttl(self):
        c = InMemoryCoordinator(holder_id="a")
        lease = c.acquire_lease("s", ttl=0.5)
        assert lease is not None
        renewed = c.renew_lease(lease, ttl=5.0)
        assert renewed is not None
        assert renewed.expires_at > lease.expires_at
        # Implementation note.
        assert renewed.fencing_token == lease.fencing_token

    def test_renew_with_stale_fencing_token_fails(self):
        c = InMemoryCoordinator(holder_id="a")
        lease1 = c.acquire_lease("s", ttl=10.0)
        assert lease1 is not None
        # Implementation note.
        stale_lease = Lease(
            scope=lease1.scope,
            holder_id=lease1.holder_id,
            acquired_at=lease1.acquired_at,
            expires_at=lease1.expires_at,
            fencing_token=lease1.fencing_token - 1,
        )
        assert c.renew_lease(stale_lease, ttl=5.0) is None

    def test_renew_not_owner_returns_none(self):
        c = InMemoryCoordinator(holder_id="a")
        lease = c.acquire_lease("s", ttl=10.0)
        assert lease is not None
        # Implementation note.
        other = Lease(
            scope=lease.scope,
            holder_id="other",
            acquired_at=lease.acquired_at,
            expires_at=lease.expires_at,
            fencing_token=lease.fencing_token,
        )
        assert c.renew_lease(other, ttl=5.0) is None


class TestInMemoryRelease:
    def test_release_owner(self):
        c = InMemoryCoordinator(holder_id="a")
        lease = c.acquire_lease("s", ttl=10.0)
        assert lease is not None
        assert c.release_lease(lease) is True
        assert c.current_lease("s") is None

    def test_release_non_owner_noop(self):
        c = InMemoryCoordinator(holder_id="a")
        lease = c.acquire_lease("s", ttl=10.0)
        assert lease is not None
        # Implementation note.
        fake = Lease(
            scope="s",
            holder_id="other",
            acquired_at=0,
            expires_at=99999,
            fencing_token=lease.fencing_token,
        )
        assert c.release_lease(fake) is False
        # Implementation note.
        assert c.current_lease("s") is not None

    def test_release_with_stale_token_noop(self):
        c = InMemoryCoordinator(holder_id="a")
        lease = c.acquire_lease("s", ttl=10.0)
        assert lease is not None
        stale = Lease(
            scope="s",
            holder_id="a",
            acquired_at=0,
            expires_at=99999,
            fencing_token=lease.fencing_token - 1,
        )
        assert c.release_lease(stale) is False


class TestInMemoryConcurrency:
    def test_threaded_contention(self):
        """Implementation note."""
        c = InMemoryCoordinator(holder_id="shared")
        # Implementation note.
        # Implementation note.
        results: list[Lease | None] = []

        def worker():
            results.append(c.acquire_lease("s", ttl=5.0))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Implementation note.
        assert all(r is not None for r in results)
        assert len(results) == 10


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestFileLockBasics:
    def test_acquire_then_current(self, tmp_path: Path):
        c = FileLockCoordinator(tmp_path / "locks", holder_id="a")
        lease = c.acquire_lease("scope1", ttl=5.0)
        assert lease is not None
        assert lease.holder_id == "a"
        assert lease.fencing_token >= 1
        assert c.current_lease("scope1") is not None

    def test_release_deletes_file(self, tmp_path: Path):
        c = FileLockCoordinator(tmp_path / "locks", holder_id="a")
        lease = c.acquire_lease("s", ttl=5.0)
        assert lease is not None
        assert c.release_lease(lease) is True
        assert c.current_lease("s") is None

    def test_fencing_token_monotonic(self, tmp_path: Path):
        c = FileLockCoordinator(tmp_path / "locks", holder_id="a")
        tokens = []
        for i in range(3):
            lease = c.acquire_lease(f"s{i}", ttl=5.0)
            assert lease is not None
            tokens.append(lease.fencing_token)
        # Implementation note.
        assert tokens == sorted(tokens)
        assert len(set(tokens)) == 3


class TestFileLockTTL:
    def test_expired_lease_not_returned(self, tmp_path: Path):
        c = FileLockCoordinator(tmp_path / "locks", holder_id="a")
        lease = c.acquire_lease("s", ttl=0.1)
        assert lease is not None
        time.sleep(0.15)
        assert c.current_lease("s") is None

    def test_expired_allows_other_to_acquire(self, tmp_path: Path):
        c1 = FileLockCoordinator(tmp_path / "locks", holder_id="a")
        c2 = FileLockCoordinator(tmp_path / "locks", holder_id="b")
        lease = c1.acquire_lease("s", ttl=0.1)
        assert lease is not None
        time.sleep(0.15)
        new = c2.acquire_lease("s", ttl=5.0)
        assert new is not None
        assert new.holder_id == "b"


class TestFileLockCrossInstance:
    def test_two_coordinators_same_dir_exclusive(self, tmp_path: Path):
        c1 = FileLockCoordinator(tmp_path / "locks", holder_id="a")
        c2 = FileLockCoordinator(tmp_path / "locks", holder_id="b")
        lease_a = c1.acquire_lease("s", ttl=10.0)
        assert lease_a is not None
        # Implementation note.
        lease_b = c2.acquire_lease("s", ttl=10.0)
        assert lease_b is None
        # Implementation note.
        c1.release_lease(lease_a)
        lease_b = c2.acquire_lease("s", ttl=10.0)
        assert lease_b is not None


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


_SUBPROC_SCRIPT = textwrap.dedent("""
    import sys, json, time
    from runtime.core.hearts import FileLockCoordinator
    lock_dir = sys.argv[1]
    holder_id = sys.argv[2]
    scope = sys.argv[3]
    ttl = float(sys.argv[4])
    c = FileLockCoordinator(lock_dir, holder_id=holder_id)
    lease = c.acquire_lease(scope, ttl=ttl)
    if lease is None:
        print(json.dumps({"ok": False}))
    else:
        print(json.dumps({
            "ok": True,
            "holder_id": lease.holder_id,
            "fencing_token": lease.fencing_token,
        }))
""")


@pytest.mark.skipif(
    os.environ.get("CI_SKIP_SUBPROC") == "1",
    reason="subprocess coordination test",
)
class TestFileLockRealProcesses:
    def _spawn(
        self,
        lock_dir: Path,
        holder: str,
        scope: str,
        ttl: float,
    ) -> dict:
        proc = subprocess.run(
            [sys.executable, "-c", _SUBPROC_SCRIPT, str(lock_dir), holder, scope, str(ttl)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip())

    def test_real_subprocess_mutex(self, tmp_path: Path):
        lock_dir = tmp_path / "locks"
        lock_dir.mkdir()

        # Implementation note.
        c_main = FileLockCoordinator(lock_dir, holder_id="main")
        lease = c_main.acquire_lease("real_test", ttl=5.0)
        assert lease is not None

        # Implementation note.
        result = self._spawn(lock_dir, "subp1", "real_test", 5.0)
        assert result["ok"] is False

        # Implementation note.
        c_main.release_lease(lease)
        result = self._spawn(lock_dir, "subp2", "real_test", 5.0)
        assert result["ok"] is True
        assert result["holder_id"] == "subp2"


# ═══════════════════════════════════════════════════════════
# LeaderGuard
# ═══════════════════════════════════════════════════════════


class TestLeaderGuard:
    def test_enters_as_leader(self):
        c = InMemoryCoordinator(holder_id="a")
        with LeaderGuard(c, scope="s", ttl=5.0) as g:
            assert g.is_leader

    def test_second_guard_not_leader(self):
        c = InMemoryCoordinator(holder_id="a")
        with LeaderGuard(c, scope="s", ttl=5.0) as g1:
            assert g1.is_leader
            # Implementation note.
            InMemoryCoordinator(holder_id="a")  # Implementation note.
            # Implementation note.
            # Implementation note.
            InMemoryCoordinator(holder_id="b")
            # Implementation note.
            # Implementation note.
            c.holder_id = "b"
            with LeaderGuard(c, scope="s", ttl=5.0) as g2:
                assert not g2.is_leader

    def test_releases_on_exit(self):
        c = InMemoryCoordinator(holder_id="a")
        with LeaderGuard(c, scope="s", ttl=5.0):
            pass
        # Implementation note.
        c.holder_id = "b"
        new = c.acquire_lease("s", ttl=5.0)
        assert new is not None

    def test_releases_on_exception(self):
        c = InMemoryCoordinator(holder_id="a")
        with pytest.raises(RuntimeError), LeaderGuard(c, scope="s", ttl=5.0):
            raise RuntimeError("boom")
        # Implementation note.
        c.holder_id = "b"
        assert c.acquire_lease("s", ttl=5.0) is not None


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestHeartsLeader:
    def test_no_coordinator_always_leader(self):
        h = Hearts()  # Implementation note.
        assert h.is_leader("anything") is True
        with h.acquire_leadership("x") as g:
            assert g.is_leader

    def test_with_coordinator_leader_gate(self):
        h = Hearts(coordinator=InMemoryCoordinator(holder_id="a"))
        # Implementation note.
        assert h.is_leader("reflection") is False
        with h.acquire_leadership("reflection", ttl=5) as g:
            assert g.is_leader
            assert h.is_leader("reflection") is True
        # Implementation note.
        assert h.is_leader("reflection") is False

    def test_coordinator_mutual_exclusion(self):
        """Implementation note."""
        c = InMemoryCoordinator(holder_id="a")
        h = Hearts(coordinator=c)
        with h.acquire_leadership("x", ttl=10) as g1:
            assert g1.is_leader
            # Implementation note.
            c.holder_id = "b"
            with h.acquire_leadership("x", ttl=10) as g2:
                assert not g2.is_leader

    def test_hearts_imports(self):
        """Implementation note."""
        from runtime.core.hearts import (
            Coordinator,
            FileLockCoordinator,
            InMemoryCoordinator,
            LeaderGuard,
            Lease,
        )

        # Implementation note.
        assert Coordinator is not None
        assert InMemoryCoordinator is not None
        assert FileLockCoordinator is not None
        assert LeaderGuard is not None
        assert Lease is not None
