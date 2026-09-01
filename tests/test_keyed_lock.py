"""KeyedLock · reference-counted per-key locks with self-cleanup.

Locks two guarantees: (1) it still serializes work per key exactly like
the ``dict[str, Lock]`` it replaces, and (2) the map is reclaimed once no
coroutine holds or waits on a key — so it stays O(active keys) without the
lost-mutual-exclusion race a TTL/LRU eviction would introduce.
"""

from __future__ import annotations

import asyncio

import pytest

from runtime.platform.process.keyed_lock import KeyedLock


@pytest.mark.asyncio
async def test_serializes_same_key():
    kl = KeyedLock()
    order: list[str] = []

    async def worker(tag: str, hold_s: float):
        async with kl.hold("k"):
            order.append(f"{tag}-enter")
            await asyncio.sleep(hold_s)
            order.append(f"{tag}-exit")

    await asyncio.gather(worker("a", 0.02), worker("b", 0.0))
    # Whichever ran first, the other must not interleave: enter/exit pairs
    # are never nested for the same key.
    assert order in (
        ["a-enter", "a-exit", "b-enter", "b-exit"],
        ["b-enter", "b-exit", "a-enter", "a-exit"],
    )


@pytest.mark.asyncio
async def test_cleans_up_after_all_release():
    kl = KeyedLock()
    async with kl.hold("k"):
        assert kl.active_keys() == 1
    assert kl.active_keys() == 0  # reclaimed


@pytest.mark.asyncio
async def test_entry_pinned_while_a_waiter_is_blocked():
    kl = KeyedLock()
    started = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with kl.hold("k"):
            started.set()
            await release.wait()

    async def waiter():
        await started.wait()
        async with kl.hold("k"):
            pass

    ht = asyncio.create_task(holder())
    await started.wait()
    wt = asyncio.create_task(waiter())
    await asyncio.sleep(0.01)  # let waiter block inside hold()
    # Holder + blocked waiter → exactly one live entry, still pinned.
    assert kl.active_keys() == 1
    release.set()
    await asyncio.gather(ht, wt)
    assert kl.active_keys() == 0


@pytest.mark.asyncio
async def test_concurrent_holders_share_one_lock_object():
    kl = KeyedLock()
    seen: list[int] = []
    release = asyncio.Event()
    started = asyncio.Event()

    async def holder():
        async with kl.hold("k") as lock:
            seen.append(id(lock))
            started.set()
            await release.wait()

    async def waiter():
        await started.wait()
        async with kl.hold("k") as lock:
            seen.append(id(lock))

    ht = asyncio.create_task(holder())
    await started.wait()
    wt = asyncio.create_task(waiter())
    await asyncio.sleep(0.01)
    release.set()
    await asyncio.gather(ht, wt)
    # Both callers, overlapping in time, saw the SAME lock object.
    assert len(seen) == 2 and seen[0] == seen[1]


@pytest.mark.asyncio
async def test_different_keys_do_not_block():
    kl = KeyedLock()
    both_in = asyncio.Barrier(2)

    async def worker(key: str):
        async with kl.hold(key):
            # If distinct keys shared a lock, this barrier would deadlock.
            await asyncio.wait_for(both_in.wait(), timeout=1.0)

    await asyncio.gather(worker("a"), worker("b"))
    assert kl.active_keys() == 0


@pytest.mark.asyncio
async def test_exception_in_block_still_cleans_up():
    kl = KeyedLock()
    with pytest.raises(ValueError):
        async with kl.hold("k"):
            raise ValueError("boom")
    assert kl.active_keys() == 0


@pytest.mark.asyncio
async def test_many_distinct_keys_leave_no_residue():
    kl = KeyedLock()
    for i in range(1000):
        async with kl.hold(f"thread-{i}"):
            pass
    assert kl.active_keys() == 0  # O(active), not O(seen)

