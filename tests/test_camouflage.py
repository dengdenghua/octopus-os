"""Implementation note."""

from __future__ import annotations

from collections import Counter
from uuid import uuid4

import pytest

from runtime.safety.experiments import ABSplitter, Variant

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestConstruction:
    def test_empty_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            ABSplitter([])

    def test_duplicate_names_raise(self):
        with pytest.raises(ValueError, match="duplicate"):
            ABSplitter(
                [
                    Variant("a", payload=1),
                    Variant("a", payload=2),
                ]
            )

    def test_variant_empty_name_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            Variant("", payload=1)

    def test_variant_zero_weight_raises(self):
        with pytest.raises(ValueError):
            Variant("x", payload=1, weight=0)

    def test_variant_negative_weight_raises(self):
        with pytest.raises(ValueError):
            Variant("x", payload=1, weight=-0.1)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSingleVariant:
    def test_single_variant_always_chosen(self):
        s = ABSplitter([Variant("only", payload="P")])
        for _ in range(20):
            v = s.next_variant()
            assert v.name == "only"
        assert s.stats["only"].assignments == 20


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestWeightedAssignment:
    def test_rough_proportion_80_20(self):
        s = ABSplitter(
            [
                Variant("A", payload="a", weight=0.8),
                Variant("B", payload="b", weight=0.2),
            ],
            seed=42,  # Implementation note.
        )
        n = 10_000
        counts = Counter(s.next_variant().name for _ in range(n))
        # Implementation note.
        ratio_a = counts["A"] / n
        assert 0.77 <= ratio_a <= 0.83

    def test_weights_need_not_sum_to_one(self):
        """Implementation note."""
        s = ABSplitter(
            [
                Variant("A", payload="a", weight=3),
                Variant("B", payload="b", weight=1),
            ],
            seed=7,
        )
        n = 10_000
        counts = Counter(s.next_variant().name for _ in range(n))
        ratio_a = counts["A"] / n
        assert 0.72 <= ratio_a <= 0.78

    def test_seed_reproducible(self):
        def _run():
            s = ABSplitter(
                [Variant("A", payload=1), Variant("B", payload=2)],
                seed=123,
            )
            return [s.next_variant().name for _ in range(50)]

        assert _run() == _run()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestStickyAssignment:
    def test_same_key_same_variant(self):
        s = ABSplitter(
            [
                Variant("A", payload=1, weight=0.5),
                Variant("B", payload=2, weight=0.5),
            ]
        )
        key = "task-abc-123"
        picks = {s.assign_for(key).name for _ in range(30)}
        assert len(picks) == 1  # Implementation note.

    def test_different_keys_distribute(self):
        s = ABSplitter(
            [
                Variant("A", payload=1, weight=0.5),
                Variant("B", payload=2, weight=0.5),
            ]
        )
        counts = Counter(s.assign_for(f"task-{i}").name for i in range(2000))
        assert 900 <= counts["A"] <= 1100
        assert 900 <= counts["B"] <= 1100

    def test_bytes_key_accepted(self):
        s = ABSplitter([Variant("A", payload=1)])
        v = s.assign_for(b"\x01\x02\x03")
        assert v.name == "A"

    def test_stats_counted_for_sticky(self):
        s = ABSplitter(
            [
                Variant("A", payload=1, weight=0.5),
                Variant("B", payload=2, weight=0.5),
            ]
        )
        for i in range(10):
            s.assign_for(f"k{i}")
        total = s.stats["A"].assignments + s.stats["B"].assignments
        assert total == 10


# ═══════════════════════════════════════════════════════════
# record_outcome
# ═══════════════════════════════════════════════════════════


class TestRecordOutcome:
    def test_success_rate_computed(self):
        s = ABSplitter([Variant("A", payload=1), Variant("B", payload=2)])
        for _ in range(8):
            s.record_outcome("A", success=True)
        for _ in range(2):
            s.record_outcome("A", success=False)
        assert s.stats["A"].success_rate == 0.8
        assert s.stats["A"].successes == 8
        assert s.stats["A"].failures == 2

    def test_unknown_variant_raises(self):
        s = ABSplitter([Variant("A", payload=1)])
        with pytest.raises(KeyError):
            s.record_outcome("ghost", success=True)

    def test_get_variant_by_name(self):
        s = ABSplitter([Variant("A", payload=42), Variant("B", payload=99)])
        assert s.get("A").payload == 42
        with pytest.raises(KeyError):
            s.get("missing")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPlannerVariants:
    def test_variant_payload_is_usable_planner(self):
        from runtime.core.cerebrum import StaticPlanner
        from runtime.core.cerebrum.planner import Rule
        from runtime.platform.models import BudgetSpec, ParsedIntent, SkillId

        planner_a = StaticPlanner(
            rules=[
                Rule(
                    name="ra",
                    intent_types=["task"],
                    skill_sequence=[SkillId("list_cwd")],
                )
            ],
            default_budget=BudgetSpec(tokens=1000, usd=0.01),
        )
        planner_b = StaticPlanner(
            rules=[
                Rule(
                    name="rb",
                    intent_types=["task"],
                    skill_sequence=[SkillId("hash_text")],
                )
            ],
            default_budget=BudgetSpec(tokens=1000, usd=0.01),
        )
        s = ABSplitter(
            [
                Variant("baseline", payload=planner_a, weight=0.5),
                Variant("experiment", payload=planner_b, weight=0.5),
            ],
            seed=5,
        )

        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="x")
        seen_rules: list[str] = []
        for _ in range(20):
            v = s.next_variant()
            graph = v.payload.plan(intent)
            seen_rules.append(graph.strategy)

        # Implementation note.
        assert "ra" in seen_rules
        assert "rb" in seen_rules

    def test_sticky_task_id_keeps_variant_for_trajectory(self):
        """Implementation note."""
        s = ABSplitter(
            [
                Variant("A", payload=1, weight=0.5),
                Variant("B", payload=2, weight=0.5),
            ]
        )
        task_id = str(uuid4())
        v1 = s.assign_for(task_id)
        v2 = s.assign_for(task_id)
        v3 = s.assign_for(task_id)
        assert v1.name == v2.name == v3.name


# ═══════════════════════════════════════════════════════════
# names / introspection
# ═══════════════════════════════════════════════════════════


class TestIntrospection:
    def test_names_listed_in_order(self):
        s = ABSplitter(
            [
                Variant("x", payload=1),
                Variant("y", payload=2),
                Variant("z", payload=3),
            ]
        )
        assert s.names == ["x", "y", "z"]
