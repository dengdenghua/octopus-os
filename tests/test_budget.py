"""Implementation note."""

from __future__ import annotations

import contextlib
from uuid import uuid4

import pytest

from runtime.platform.models import (
    Budget,
    BudgetLimits,
    CostEntry,
    InsufficientBudget,
    TaskId,
)


class TestBudgetBasics:
    def test_fresh_budget_is_active(self, small_budget: Budget):
        assert small_budget.status == "active"
        assert small_budget.tokens_spent == 0
        assert small_budget.usd_spent == 0.0
        assert small_budget.utilization == 0.0

    def test_reserve_and_commit_happy(self, small_budget: Budget, sample_cost: CostEntry):
        rid = small_budget.reserve(sample_cost)
        assert small_budget.tokens_reserved == sample_cost.tokens
        assert small_budget.tokens_spent == 0  # Implementation note.

        small_budget.commit(rid, sample_cost)
        assert small_budget.tokens_spent == sample_cost.tokens
        assert small_budget.tokens_reserved == 0


class TestBDG_I1_Monotonic:  # noqa: N801 — invariant ID in suite name
    """Implementation note."""

    def test_multiple_commits_strictly_increasing(self, small_budget: Budget):
        trail = []
        for _ in range(5):
            cost = CostEntry(tokens_in=10, tokens_out=5, usd=0.001)
            rid = small_budget.reserve(cost)
            small_budget.commit(rid, cost)
            trail.append(small_budget.tokens_spent)
        # Implementation note.
        assert all(b > a for a, b in zip(trail, trail[1:], strict=False))

    def test_commit_cannot_decrement(self, small_budget: Budget):
        """Implementation note."""
        rid1 = small_budget.reserve(CostEntry(tokens_in=100, tokens_out=0, usd=0.01))
        small_budget.commit(rid1, CostEntry(tokens_in=100, tokens_out=0, usd=0.01))
        spent_after = small_budget.tokens_spent

        rid2 = small_budget.reserve(CostEntry(tokens_in=50, tokens_out=0, usd=0.005))
        small_budget.commit(rid2, CostEntry(tokens_in=50, tokens_out=0, usd=0.005))
        assert small_budget.tokens_spent > spent_after


class TestBDG_I2_Atomic:  # noqa: N801 — invariant ID in suite name
    """Implementation note."""

    def test_reserve_hits_limit_raises(self, small_budget: Budget):
        """Implementation note."""
        small_budget.reserve(CostEntry(tokens_in=900, tokens_out=0, usd=0.0))
        # Implementation note.

        with pytest.raises(InsufficientBudget):
            small_budget.reserve(CostEntry(tokens_in=200, tokens_out=0, usd=0.0))

        # Implementation note.
        assert small_budget.status == "exceeded"

    def test_subsequent_reserves_blocked_after_exceeded(self, small_budget: Budget):
        with contextlib.suppress(InsufficientBudget):
            small_budget.reserve(CostEntry(tokens_in=10_000, tokens_out=0, usd=0.0))
        # Implementation note.
        with pytest.raises(InsufficientBudget):
            small_budget.reserve(CostEntry(tokens_in=1, tokens_out=0, usd=0.0))

    def test_usd_and_tokens_both_enforced(self):
        """Implementation note."""
        b = Budget(task_id=TaskId(uuid4()), limits=BudgetLimits(tokens=1_000_000, usd=0.01))
        # Implementation note.
        with pytest.raises(InsufficientBudget):
            b.reserve(CostEntry(tokens_in=10, tokens_out=0, usd=1.0))


class TestBDG_I3_ReserveCommitPair:  # noqa: N801 — invariant ID in suite name
    """Implementation note."""

    def test_commit_releases_reserved(self, small_budget: Budget):
        rid = small_budget.reserve(CostEntry(tokens_in=100, tokens_out=0, usd=0.01))
        assert small_budget.tokens_reserved == 100

        small_budget.commit(rid, CostEntry(tokens_in=80, tokens_out=0, usd=0.008))
        assert small_budget.tokens_reserved == 0
        assert small_budget.tokens_spent == 80  # Implementation note.

    def test_commit_unknown_reservation_raises(self, small_budget: Budget):
        with pytest.raises(KeyError):
            small_budget.commit(uuid4(), CostEntry(tokens_in=10, tokens_out=0, usd=0.001))

    def test_refund_stale_reservations(self, small_budget: Budget):
        """Implementation note."""
        small_budget.reserve(CostEntry(tokens_in=100, tokens_out=0, usd=0.01))
        assert small_budget.tokens_reserved == 100

        # Implementation note.
        refunded = small_budget.refund_stale_reservations(ttl_seconds=0)
        assert refunded == 1
        assert small_budget.tokens_reserved == 0


class TestFreeze:
    """Implementation note."""

    def test_freeze_rejects_new_reserves(self, small_budget: Budget):
        small_budget.freeze()
        assert small_budget.status == "frozen"
        with pytest.raises(InsufficientBudget):
            small_budget.reserve(CostEntry(tokens_in=1, tokens_out=0, usd=0.0))
