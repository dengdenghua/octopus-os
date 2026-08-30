"""
Regression · when a task writes multiple swarm aggregates under the
same ``task_id`` (resume / retry path) · both the memory consolidator
AND the context composer must pick the NEWEST aggregate, not the
oldest.

Pre-fix: both paths used ``next(... for e in bucket if ...)`` which
returns the first match (= oldest append). That let a failed or
partial early swarm aggregate permanently shadow the successful
aggregate that followed.

Impact:

* Memory consolidator · stale failure weights propagate into the
  agent's long-term memory, depressing strategy scores for tasks
  that actually succeeded.
* Context composer · planner sees the obsolete failure summary in
  its recent-history context, biasing next-turn decisions against
  tasks that recovered.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

# ═══════════════════════════════════════════════════════════
# Fixture · two aggregates under same task_id
# ═══════════════════════════════════════════════════════════


def _make_swarm_trajectory(task_id, *, success: bool, usd: float):
    """Build a minimal Trajectory marked strategy_id='swarm'."""
    from runtime.platform.models import (
        ArmId,
        CostEntry,
        Trajectory,
        TrajectoryOutcome,
    )

    return Trajectory(
        task_id=task_id,
        arm_id=ArmId("swarm-aggregate"),
        steps=[],
        outcome=TrajectoryOutcome(
            success=success,
            cost=CostEntry(tokens_in=0, tokens_out=0, usd=usd),
        ),
        strategy_id="swarm",
    )


def _write_two_aggregates(journal):
    """Write an OLD failed aggregate first, then a NEW successful
    one for the same task_id. Manipulate TrajectoryEvent timestamps
    so the new one has a later ts."""
    from runtime.memory.journal.journal import TrajectoryEvent
    from runtime.platform.models import TaskId

    tid = TaskId(uuid4())
    old_traj = _make_swarm_trajectory(tid, success=False, usd=0.01)
    new_traj = _make_swarm_trajectory(tid, success=True, usd=0.02)

    t0 = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)
    old_event = TrajectoryEvent(ts=t0, trajectory=old_traj, agent_id=None)
    new_event = TrajectoryEvent(ts=t1, trajectory=new_traj, agent_id=None)

    # Append old first · then new · mirror journal ordering
    journal._events.append(old_event)
    journal._events.append(new_event)
    return tid, old_traj, new_traj


# ═══════════════════════════════════════════════════════════
# Memory consolidator picks the newest swarm aggregate
# ═══════════════════════════════════════════════════════════


class TestConsolidatorNewest:
    def test_picks_latest_swarm_aggregate(self):
        from runtime.memory.journal import InMemoryJournal
        from runtime.safety.recovery.memory_consolidator import (
            MemoryConsolidator,
        )

        journal = InMemoryJournal()
        tid, old_traj, new_traj = _write_two_aggregates(journal)

        consolidator = MemoryConsolidator(journal=journal)
        tagged = consolidator._collect_trajectories()
        # Exactly one entry for this task_id · the newer one.
        task_entries = [(traj, agent) for traj, agent in tagged if traj.task_id == tid]
        assert len(task_entries) == 1
        picked_traj, _ = task_entries[0]
        assert picked_traj.outcome.success is True
        assert picked_traj.outcome.cost.usd == pytest.approx(0.02)


# ═══════════════════════════════════════════════════════════
# Context composer picks the newest swarm aggregate
# ═══════════════════════════════════════════════════════════


class TestComposerNewest:
    def test_render_recent_trajectories_uses_latest(self):
        from runtime.execution.suckers import SkillRegistry
        from runtime.memory.hemolymph import ContextComposer
        from runtime.memory.journal import InMemoryJournal

        journal = InMemoryJournal()
        tid, old_traj, new_traj = _write_two_aggregates(journal)

        composer = ContextComposer(
            journal=journal,
            registry=SkillRegistry(),
        )
        # Private helper · signature is (n, token_budget, arm_id=None)
        blurbs = composer._render_recent_trajectories(
            n=5,
            arm_id=None,
            budget_for_bucket=4000,
        )
        # Exactly one blurb for this task_id · content reflects the
        # newer (successful) trajectory.
        joined = " ".join(b for b, _ in blurbs)
        # The old trajectory had success=False / the new one
        # success=True. Composer's blurb format · "ok=yes|no".
        assert joined.count(str(tid)) == 1
        assert "ok=yes" in joined
        assert "ok=no" not in joined
