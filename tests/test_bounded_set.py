"""BoundedSet · FIFO-capped dedup ledger used for thread_started dedup.

Locks the memory-bound guarantee: a dedup ledger fed an unbounded id
space must not grow past its cap, and eviction is oldest-inserted first.
"""

from __future__ import annotations

import pytest

from runtime.platform.process.bounded_set import BoundedSet


def test_membership_and_add():
    s = BoundedSet(maxsize=4)
    assert "x" not in s
    s.add("x")
    assert "x" in s
    assert len(s) == 1


def test_add_is_idempotent_and_does_not_reorder():
    s = BoundedSet(maxsize=2)
    s.add("a")
    s.add("b")
    s.add("a")  # already present — must not evict "a" as if it were new
    assert len(s) == 2
    s.add("c")  # over cap → oldest-inserted ("a") is evicted, not "b"
    assert "a" not in s
    assert "b" in s and "c" in s


def test_evicts_oldest_first_over_cap():
    s = BoundedSet(maxsize=3)
    for x in ("a", "b", "c", "d", "e"):
        s.add(x)
    assert len(s) == 3
    assert "a" not in s and "b" not in s
    assert "c" in s and "d" in s and "e" in s


def test_stays_bounded_under_unbounded_ids():
    s = BoundedSet(maxsize=100)
    for i in range(10_000):
        s.add(f"thread-{i}")
    assert len(s) == 100  # O(maxsize) regardless of ids seen


def test_discard_and_clear():
    s = BoundedSet(maxsize=4)
    s.add("a")
    s.add("b")
    s.discard("a")
    assert "a" not in s and "b" in s
    s.discard("missing")  # no error
    s.clear()
    assert len(s) == 0


def test_rejects_bad_maxsize():
    with pytest.raises(ValueError):
        BoundedSet(maxsize=0)

