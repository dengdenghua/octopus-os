from __future__ import annotations

from collections.abc import Iterator
from typing import Generic, TypeVar

from .enforce import InvariantViolation

K = TypeVar("K")
V = TypeVar("V")
T = TypeVar("T")


class AppendOnlyList(Generic[T]):
    __slots__ = ("_data", "_rule_id")

    def __init__(self, initial: list[T] | None = None, rule_id: str = "APPEND_ONLY") -> None:
        self._data: list[T] = list(initial) if initial else []
        self._rule_id = rule_id

    def append(self, item: T) -> None:
        self._data.append(item)

    def extend(self, items: list[T]) -> None:
        self._data.extend(items)

    def drop_oldest(self, n: int) -> None:
        """Ring-buffer capacity policy: drop the oldest ``n`` items.

        This is the explicit eviction counterpart to ``append`` — used by
        bounded journals (audit R-04) so an in-memory event log cannot grow
        without limit. ``append``/``extend`` remain pure appends; eviction is
        a separate operation callers invoke only when they intend a ring.
        """
        if n <= 0:
            return
        del self._data[: int(n)]

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[T]:
        return iter(self._data)

    def __getitem__(self, i: int) -> T:
        return self._data[i]

    def __contains__(self, item: object) -> bool:
        return item in self._data

    def __repr__(self) -> str:
        return f"AppendOnlyList(n={len(self._data)})"

    def snapshot(self) -> list[T]:
        return list(self._data)

    def __setitem__(self, *args: object, **kwargs: object) -> None:
        raise InvariantViolation(self._rule_id, "item assignment forbidden on AppendOnlyList")

    def __delitem__(self, *args: object, **kwargs: object) -> None:
        raise InvariantViolation(self._rule_id, "item deletion forbidden on AppendOnlyList")

    def pop(self, *args: object, **kwargs: object) -> T:
        raise InvariantViolation(self._rule_id, "pop forbidden on AppendOnlyList")

    def remove(self, *args: object, **kwargs: object) -> None:
        raise InvariantViolation(self._rule_id, "remove forbidden on AppendOnlyList")

    def clear(self) -> None:
        raise InvariantViolation(self._rule_id, "clear forbidden on AppendOnlyList")

    def reverse(self) -> None:
        raise InvariantViolation(self._rule_id, "in-place reverse forbidden on AppendOnlyList")

    def sort(self, *args: object, **kwargs: object) -> None:
        raise InvariantViolation(self._rule_id, "in-place sort forbidden on AppendOnlyList")


class AppendOnlyMapping(Generic[K, V]):
    __slots__ = ("_data", "_rule_id")

    def __init__(self, rule_id: str = "APPEND_ONLY_MAP") -> None:
        self._data: dict[K, V] = {}
        self._rule_id = rule_id

    def put(self, key: K, value: V) -> None:
        if key in self._data:
            raise InvariantViolation(
                self._rule_id,
                f"key {key!r} already exists (immutable after first put)",
            )
        self._data[key] = value

    def get(self, key: K) -> V:
        return self._data[key]

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[K]:
        return iter(self._data)

    def items(self) -> list[tuple[K, V]]:
        return list(self._data.items())

    def keys(self) -> list[K]:
        return list(self._data.keys())

    def values(self) -> list[V]:
        return list(self._data.values())

    def __repr__(self) -> str:
        return f"AppendOnlyMapping(n={len(self._data)})"

    def __setitem__(self, *args: object, **kwargs: object) -> None:
        raise InvariantViolation(self._rule_id, "use put() not []=")

    def __delitem__(self, *args: object, **kwargs: object) -> None:
        raise InvariantViolation(self._rule_id, "deletion forbidden")

    def pop(self, *args: object, **kwargs: object) -> V:
        raise InvariantViolation(self._rule_id, "pop forbidden")

    def clear(self) -> None:
        raise InvariantViolation(self._rule_id, "clear forbidden")

    def update(self, *args: object, **kwargs: object) -> None:
        raise InvariantViolation(
            self._rule_id,
            "update() forbidden; use individual put() calls",
        )
