"""BoundedSet · an insertion-ordered set with a hard capacity.

A plain ``set`` used as a "have I seen this id?" ledger grows without
bound when the id space is effectively unbounded (one entry per thread
id, per source ip, …). ``BoundedSet`` caps that: once ``maxsize`` is
reached, adding a new member evicts the oldest-inserted one (FIFO), so
the memory footprint is O(maxsize) regardless of how many distinct ids
flow through over the process lifetime.

Callers use it exactly like the ``set`` it replaces — ``x in s`` and
``s.add(x)`` — so it is a drop-in for dedup ledgers where re-seeing an
evicted member is harmless (at worst the caller redoes cheap idempotent
work). It is NOT thread/async-safe on its own; hold the caller's lock,
just as the ``set`` it replaces required.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator


class BoundedSet:
    __slots__ = ("_items", "_maxsize")

    def __init__(self, maxsize: int = 4096) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._maxsize = maxsize
        # value -> None; OrderedDict preserves insertion order for FIFO eviction.
        self._items: OrderedDict[str, None] = OrderedDict()

    def add(self, value: str) -> None:
        if value in self._items:
            return
        self._items[value] = None
        while len(self._items) > self._maxsize:
            self._items.popitem(last=False)  # evict oldest-inserted

    def __contains__(self, value: object) -> bool:
        return value in self._items

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def discard(self, value: str) -> None:
        self._items.pop(value, None)

    def clear(self) -> None:
        self._items.clear()
