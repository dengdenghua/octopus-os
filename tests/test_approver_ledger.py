"""Tests for ``runtime.safety.gene_locks.approver_ledger``.

Covers the quorum approval ring buffer: recording, distinct counting
within a time window, audit views, ring-buffer overflow, thread safety,
and the module singleton.
"""

from __future__ import annotations

import threading
import time

import pytest

from runtime.safety.gene_locks.approver_ledger import (
    DEFAULT_WINDOW_S,
    MAX_ENTRIES,
    ApprovalRecord,
    ApproverLedger,
    get_ledger,
)

# ── ApprovalRecord ────────────────────────────────────────────


class TestApprovalRecord:
    def test_matches_same_kind_target(self) -> None:
        r = ApprovalRecord(approver="alice", kind="promote", target="rec-1", ts=0.0)
        assert r.matches("promote", "rec-1") is True

    def test_matches_different_kind(self) -> None:
        r = ApprovalRecord(approver="alice", kind="promote", target="rec-1", ts=0.0)
        assert r.matches("demote", "rec-1") is False

    def test_matches_different_target(self) -> None:
        r = ApprovalRecord(approver="alice", kind="promote", target="rec-1", ts=0.0)
        assert r.matches("promote", "rec-2") is False


# ── ApproverLedger.record ────────────────────────────────────


class TestRecord:
    def test_record_basic(self) -> None:
        ledger = ApproverLedger()
        ledger.record("alice", "promote", "rec-1")
        assert ledger.count_distinct("promote", "rec-1") == 1

    def test_record_strips_whitespace(self) -> None:
        ledger = ApproverLedger()
        ledger.record("  alice  ", "promote", "rec-1")
        assert ledger.distinct_approvers("promote", "rec-1") == ["alice"]

    def test_record_empty_approver_skipped(self) -> None:
        ledger = ApproverLedger()
        ledger.record("", "promote", "rec-1")
        ledger.record("   ", "promote", "rec-1")
        assert ledger.count_distinct("promote", "rec-1") == 0

    def test_record_none_approver_skipped(self) -> None:
        ledger = ApproverLedger()
        ledger.record(None, "promote", "rec-1")  # type: ignore[arg-type]
        assert ledger.count_distinct("promote", "rec-1") == 0


# ── ApproverLedger.count_distinct ────────────────────────────


class TestCountDistinct:
    def test_distinct_approvers_counted(self) -> None:
        ledger = ApproverLedger()
        ledger.record("alice", "promote", "rec-1")
        ledger.record("bob", "promote", "rec-1")
        ledger.record("carol", "promote", "rec-1")
        assert ledger.count_distinct("promote", "rec-1") == 3

    def test_duplicate_approver_counted_once(self) -> None:
        ledger = ApproverLedger()
        ledger.record("alice", "promote", "rec-1")
        ledger.record("alice", "promote", "rec-1")
        ledger.record("alice", "promote", "rec-1")
        assert ledger.count_distinct("promote", "rec-1") == 1

    def test_different_target_not_counted(self) -> None:
        ledger = ApproverLedger()
        ledger.record("alice", "promote", "rec-1")
        ledger.record("bob", "promote", "rec-2")
        assert ledger.count_distinct("promote", "rec-1") == 1
        assert ledger.count_distinct("promote", "rec-2") == 1

    def test_different_kind_not_counted(self) -> None:
        ledger = ApproverLedger()
        ledger.record("alice", "promote", "rec-1")
        ledger.record("bob", "demote", "rec-1")
        assert ledger.count_distinct("promote", "rec-1") == 1
        assert ledger.count_distinct("demote", "rec-1") == 1

    def test_expired_entries_excluded(self) -> None:
        ledger = ApproverLedger(window_s=100)
        now = time.time()
        # Manually append an expired record
        with ledger._lock:
            ledger._entries.append(
                ApprovalRecord(
                    approver="alice",
                    kind="promote",
                    target="rec-1",
                    ts=now - 200,  # 200s ago, outside 100s window
                )
            )
            ledger._entries.append(
                ApprovalRecord(
                    approver="bob",
                    kind="promote",
                    target="rec-1",
                    ts=now - 50,  # 50s ago, inside window
                )
            )
        assert ledger.count_distinct("promote", "rec-1", now=now) == 1

    def test_boundary_ts_included(self) -> None:
        """Entry exactly at cutoff boundary should be included (>=)."""
        ledger = ApproverLedger(window_s=100)
        now = 1000.0
        cutoff = now - 100  # exactly window_s ago
        with ledger._lock:
            ledger._entries.append(
                ApprovalRecord(
                    approver="alice",
                    kind="promote",
                    target="rec-1",
                    ts=cutoff,
                )
            )
        assert ledger.count_distinct("promote", "rec-1", now=now) == 1

    def test_empty_ledger_returns_zero(self) -> None:
        ledger = ApproverLedger()
        assert ledger.count_distinct("promote", "rec-1") == 0


# ── ApproverLedger.distinct_approvers ────────────────────────


class TestDistinctApprovers:
    def test_returns_sorted_names(self) -> None:
        ledger = ApproverLedger()
        ledger.record("carol", "promote", "rec-1")
        ledger.record("alice", "promote", "rec-1")
        ledger.record("bob", "promote", "rec-1")
        assert ledger.distinct_approvers("promote", "rec-1") == ["alice", "bob", "carol"]

    def test_empty_returns_empty_list(self) -> None:
        ledger = ApproverLedger()
        assert ledger.distinct_approvers("promote", "rec-1") == []

    def test_respects_time_window(self) -> None:
        ledger = ApproverLedger(window_s=50)
        now = time.time()
        with ledger._lock:
            ledger._entries.append(
                ApprovalRecord(
                    approver="alice",
                    kind="promote",
                    target="rec-1",
                    ts=now - 100,
                )
            )
            ledger._entries.append(
                ApprovalRecord(
                    approver="bob",
                    kind="promote",
                    target="rec-1",
                    ts=now - 10,
                )
            )
        result = ledger.distinct_approvers("promote", "rec-1", now=now)
        assert result == ["bob"]


# ── ApproverLedger.recent ────────────────────────────────────


class TestRecent:
    def test_returns_most_recent_first(self) -> None:
        ledger = ApproverLedger()
        ledger.record("alice", "promote", "rec-1")
        time.sleep(0.01)
        ledger.record("bob", "promote", "rec-2")
        time.sleep(0.01)
        ledger.record("carol", "promote", "rec-3")
        recent = ledger.recent()
        assert len(recent) == 3
        assert recent[0]["approver"] == "carol"
        assert recent[1]["approver"] == "bob"
        assert recent[2]["approver"] == "alice"

    def test_respects_limit(self) -> None:
        ledger = ApproverLedger()
        for i in range(10):
            ledger.record(f"user-{i}", "promote", "rec-1")
        recent = ledger.recent(limit=3)
        assert len(recent) == 3
        # Most recent first → user-9, user-8, user-7
        assert recent[0]["approver"] == "user-9"
        assert recent[2]["approver"] == "user-7"

    def test_includes_stale_entries(self) -> None:
        """recent() does NOT filter by window — audit wants full history."""
        ledger = ApproverLedger(window_s=1)
        with ledger._lock:
            ledger._entries.append(
                ApprovalRecord(
                    approver="alice",
                    kind="promote",
                    target="rec-1",
                    ts=0.0,
                )
            )
        # Even though ts=0 is far outside the window, recent() shows it
        recent = ledger.recent()
        assert len(recent) == 1
        assert recent[0]["approver"] == "alice"

    def test_record_fields_in_output(self) -> None:
        ledger = ApproverLedger()
        ledger.record("alice", "promote", "rec-1")
        recent = ledger.recent()
        assert recent[0]["approver"] == "alice"
        assert recent[0]["kind"] == "promote"
        assert recent[0]["target"] == "rec-1"
        assert "ts" in recent[0]
        assert isinstance(recent[0]["ts"], float)

    def test_empty_returns_empty_list(self) -> None:
        ledger = ApproverLedger()
        assert ledger.recent() == []


# ── ApproverLedger.clear ─────────────────────────────────────


class TestClear:
    def test_clear_returns_dropped_count(self) -> None:
        ledger = ApproverLedger()
        ledger.record("alice", "promote", "rec-1")
        ledger.record("bob", "promote", "rec-1")
        dropped = ledger.clear()
        assert dropped == 2

    def test_clear_empties_ledger(self) -> None:
        ledger = ApproverLedger()
        ledger.record("alice", "promote", "rec-1")
        ledger.clear()
        assert ledger.count_distinct("promote", "rec-1") == 0
        assert ledger.recent() == []

    def test_clear_on_empty_returns_zero(self) -> None:
        ledger = ApproverLedger()
        assert ledger.clear() == 0


# ── Ring buffer overflow ─────────────────────────────────────


class TestRingBufferOverflow:
    def test_max_entries_cap(self) -> None:
        ledger = ApproverLedger()
        # Overflow the ring buffer
        for i in range(MAX_ENTRIES + 100):
            ledger.record(f"user-{i}", "promote", "rec-1")
        # Should only keep MAX_ENTRIES
        assert len(ledger.recent(limit=10_000)) == MAX_ENTRIES

    def test_old_entries_evicted_not_new(self) -> None:
        ledger = ApproverLedger()
        for i in range(MAX_ENTRIES + 10):
            ledger.record(f"user-{i}", "promote", "rec-1")
        recent = ledger.recent(limit=10_000)
        # Most recent should be user-(MAX_ENTRIES+9)
        assert recent[0]["approver"] == f"user-{MAX_ENTRIES + 9}"
        # Oldest kept should be user-10 (0-9 evicted)
        assert recent[-1]["approver"] == "user-10"


# ── Thread safety ────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_records(self) -> None:
        ledger = ApproverLedger()
        n_threads = 10
        n_per_thread = 50

        def worker(tid: int) -> None:
            for i in range(n_per_thread):
                ledger.record(f"t{tid}-u{i}", "promote", "rec-1")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        expected = n_threads * n_per_thread
        assert len(ledger.recent(limit=10_000)) == expected
        assert ledger.count_distinct("promote", "rec-1") == expected


# ── Module singleton ─────────────────────────────────────────


class TestSingleton:
    def test_get_ledger_returns_same_instance(self) -> None:
        l1 = get_ledger()
        l2 = get_ledger()
        assert l1 is l2

    def test_singleton_is_approver_ledger(self) -> None:
        assert isinstance(get_ledger(), ApproverLedger)

    def test_default_window(self) -> None:
        assert DEFAULT_WINDOW_S == 600

    def test_default_max_entries(self) -> None:
        assert MAX_ENTRIES == 500


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

