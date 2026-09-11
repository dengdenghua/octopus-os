"""Implementation note."""

from __future__ import annotations

import pytest

from runtime.safety.invariants import (
    AppendOnlyList,
    AppendOnlyMapping,
    InvariantViolation,
    append_only,
    enforces,
    ensure,
    monotonic,
    require,
)


class TestEnforcesMetadata:
    def test_enforces_attaches_rule_ids(self):
        @enforces("BDG-I1", "CC-8")
        def commit(self) -> None:
            pass

        assert commit.__enforces__ == ("BDG-I1", "CC-8")

    def test_enforces_stackable(self):
        @enforces("A")
        @enforces("B")
        def fn() -> None:
            pass

        assert "A" in fn.__enforces__
        assert "B" in fn.__enforces__


class TestRequire:
    def test_precondition_blocks_bad_call(self):
        @require(lambda x: x > 0, "TEST")
        def must_positive(x: int) -> int:
            return x * 2

        assert must_positive(5) == 10
        with pytest.raises(InvariantViolation) as exc_info:
            must_positive(-1)
        assert exc_info.value.rule_id == "TEST"

    def test_error_message(self):
        @require(lambda x: x > 0, "R-01", message="x must be positive")
        def fn(x: int) -> int:
            return x

        with pytest.raises(InvariantViolation, match="x must be positive"):
            fn(-1)


class TestEnsure:
    def test_postcondition_blocks_bad_return(self):
        @ensure(lambda ret: ret > 0, "TEST-POST")
        def halve(x: int) -> int:
            return x // 2

        assert halve(10) == 5
        with pytest.raises(InvariantViolation):
            halve(1)  # Implementation note.


class TestMonotonic:
    def test_only_up_allows_increase(self):
        class Counter:
            def __init__(self) -> None:
                self.val = 0

            @monotonic("val", direction="up", rule_id="BDG-I1-test")
            def step(self) -> None:
                self.val += 1

        c = Counter()
        c.step()
        c.step()
        assert c.val == 2

    def test_only_up_rejects_decrease(self):
        class Counter:
            def __init__(self) -> None:
                self.val = 10

            @monotonic("val", direction="up", rule_id="BDG-I1-test")
            def decrement(self) -> None:
                self.val -= 1

        c = Counter()
        with pytest.raises(InvariantViolation, match="BDG-I1-test"):
            c.decrement()

    def test_nondec_allows_equal(self):
        class State:
            def __init__(self) -> None:
                self.v = 5

            @monotonic("v", direction="nondec")
            def noop(self) -> None:
                pass  # Implementation note.

        s = State()
        s.noop()


class TestAppendOnlyDecorator:
    def test_blocks_shrinkage(self):
        class Journal:
            def __init__(self) -> None:
                self.events = [1, 2, 3]

            @append_only("events", rule_id="MEM-I1")
            def append(self, x: int) -> None:
                self.events.append(x)

            @append_only("events", rule_id="MEM-I1")
            def bad_pop(self) -> int:
                return self.events.pop()  # Implementation note.

        j = Journal()
        j.append(4)
        assert len(j.events) == 4

        with pytest.raises(InvariantViolation, match="MEM-I1"):
            j.bad_pop()

    def test_blocks_mutation_of_existing(self):
        class Journal:
            def __init__(self) -> None:
                self.events = ["a", "b"]

            @append_only("events")
            def mutate_first(self) -> None:
                self.events[0] = "CHANGED"

        j = Journal()
        with pytest.raises(InvariantViolation):
            j.mutate_first()


class TestAppendOnlyList:
    def test_append_works(self):
        lst = AppendOnlyList[int]()
        lst.append(1)
        lst.append(2)
        assert len(lst) == 2
        assert lst[0] == 1
        assert list(lst) == [1, 2]

    def test_pop_forbidden(self):
        lst = AppendOnlyList[int]([1, 2, 3])
        with pytest.raises(InvariantViolation):
            lst.pop()

    def test_remove_forbidden(self):
        lst = AppendOnlyList[int]([1, 2])
        with pytest.raises(InvariantViolation):
            lst.remove(1)

    def test_clear_forbidden(self):
        lst = AppendOnlyList[int]([1])
        with pytest.raises(InvariantViolation):
            lst.clear()

    def test_delitem_forbidden(self):
        lst = AppendOnlyList[int]([1])
        with pytest.raises(InvariantViolation):
            del lst[0]

    def test_setitem_forbidden(self):
        lst = AppendOnlyList[int]([1])
        with pytest.raises(InvariantViolation):
            lst[0] = 999

    def test_sort_forbidden(self):
        lst = AppendOnlyList[int]([3, 1, 2])
        with pytest.raises(InvariantViolation):
            lst.sort()

    def test_snapshot_is_copy(self):
        lst = AppendOnlyList[int]([1, 2])
        s = lst.snapshot()
        s.append(99)
        assert len(lst) == 2  # Implementation note.


class TestAppendOnlyMapping:
    def test_put_and_get(self):
        m = AppendOnlyMapping[str, int]()
        m.put("a", 1)
        m.put("b", 2)
        assert m.get("a") == 1
        assert "b" in m
        assert len(m) == 2

    def test_put_existing_key_forbidden(self):
        m = AppendOnlyMapping[str, int]()
        m.put("a", 1)
        with pytest.raises(InvariantViolation, match="already exists"):
            m.put("a", 2)

    def test_setitem_forbidden(self):
        m = AppendOnlyMapping[str, int]()
        with pytest.raises(InvariantViolation):
            m["a"] = 1

    def test_pop_forbidden(self):
        m = AppendOnlyMapping[str, int]()
        m.put("a", 1)
        with pytest.raises(InvariantViolation):
            m.pop("a")

    def test_update_forbidden(self):
        m = AppendOnlyMapping[str, int]()
        with pytest.raises(InvariantViolation):
            m.update({"a": 1})
