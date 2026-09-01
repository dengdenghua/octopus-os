"""KeyedLock · per-key asyncio locks that clean themselves up.

A plain ``dict[str, asyncio.Lock]`` used to serialize work per key (per
thread id, per session, …) grows one entry per distinct key forever. The
naive fixes both have problems:

  * TTL / LRU eviction races: a caller fetches ``locks[key]`` and, before
    it manages to ``async with`` it, the entry is evicted. The next caller
    for the same key creates a *different* lock object → the two run
    concurrently and the mutual exclusion the lock existed for is lost.
  * Never evicting: unbounded memory.

``KeyedLock`` reference-counts instead. ``hold(key)`` pins the entry from
the moment it hands out the lock until the caller's block exits, so any
two concurrent holders of the same key always share one lock object. The
entry is dropped only once no coroutine holds or is waiting on it, so the
map stays O(active keys), not O(keys ever seen).

Usage — a drop-in for ``lock = await lock_for(key); async with lock:``::

    async with keyed.hold(thread_id):
        ...  # serialized per thread_id, entry reclaimed on exit
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass
class _Entry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    refs: int = 0


class KeyedLock:
    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._guard = asyncio.Lock()

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[asyncio.Lock]:
        """Acquire the lock for ``key`` for the duration of the block.

        The map entry is reference-counted: it exists for as long as any
        caller is inside its ``hold(key)`` (holding or waiting), and is
        removed once the last one leaves — so no two callers ever see
        different lock objects for the same key at the same time.
        """
        async with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                entry = _Entry()
                self._entries[key] = entry
            entry.refs += 1
        try:
            async with entry.lock:
                yield entry.lock
        finally:
            async with self._guard:
                entry.refs -= 1
                # Drop only when nobody else holds a reference (refs 0) and
                # the lock isn't held by a waiter that just acquired it. The
                # identity check guards against dropping a replacement entry.
                if entry.refs <= 0 and not entry.lock.locked() and self._entries.get(key) is entry:
                    del self._entries[key]

    def active_keys(self) -> int:
        """Number of live entries — for tests / observability."""
        return len(self._entries)


__all__ = ["KeyedLock"]
