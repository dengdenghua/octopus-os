"""Per-actor rate-limit bucket eviction — bounds the completion gateway's
bucket dicts against a rotating-source-IP flood.

/v1/chat/completions keys its rate-limit state by ``actor:`` or
``anon:<ip>``. Without eviction, one bucket per distinct IP would
accumulate forever. _evict_idle_rate_buckets drops buckets whose 60s
sliding window has emptied out, so steady-state memory is bounded by the
number of *active* keys, not every key ever seen.
"""

from __future__ import annotations

from collections import deque

from runtime.sensing.gateway.openai_gateway_router import _evict_idle_rate_buckets


def test_evicts_empty_window_and_its_semaphore():
    now = 1000.0
    windows = {
        "anon:1.1.1.1": deque([now - 90.0]),  # stale (> 60s old) → empties out
        "actor:alice": deque([now - 10.0]),  # active → kept
    }
    semaphores = {"anon:1.1.1.1": object(), "actor:alice": object()}
    evicted = _evict_idle_rate_buckets(windows, semaphores, cutoff=now - 60.0)
    assert evicted == 1
    assert "anon:1.1.1.1" not in windows
    assert "anon:1.1.1.1" not in semaphores  # paired semaphore dropped too
    assert "actor:alice" in windows
    assert "actor:alice" in semaphores


def test_partial_stale_entries_trimmed_but_bucket_kept():
    now = 1000.0
    # One stale + one fresh timestamp: the stale one is trimmed, the
    # bucket survives because it still has a recent call.
    windows = {"anon:2.2.2.2": deque([now - 120.0, now - 5.0])}
    semaphores = {"anon:2.2.2.2": object()}
    evicted = _evict_idle_rate_buckets(windows, semaphores, cutoff=now - 60.0)
    assert evicted == 0
    assert list(windows["anon:2.2.2.2"]) == [now - 5.0]  # stale trimmed
    assert "anon:2.2.2.2" in semaphores


def test_flood_of_stale_buckets_all_evicted():
    now = 1000.0
    windows = {f"anon:10.0.0.{i}": deque([now - 100.0]) for i in range(500)}
    semaphores = {k: object() for k in windows}
    evicted = _evict_idle_rate_buckets(windows, semaphores, cutoff=now - 60.0)
    assert evicted == 500
    assert not windows
    assert not semaphores


def test_no_op_when_all_active():
    now = 1000.0
    windows = {f"anon:10.0.0.{i}": deque([now - 1.0]) for i in range(10)}
    semaphores = {k: object() for k in windows}
    evicted = _evict_idle_rate_buckets(windows, semaphores, cutoff=now - 60.0)
    assert evicted == 0
    assert len(windows) == 10
    assert len(semaphores) == 10

