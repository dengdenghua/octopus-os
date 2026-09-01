"""The live ReAct loop refreshes the planner's recipe self-assessment from
accumulating experience (throttled), so the losing-recipe signal reflects this
session's performance rather than only the startup snapshot. Parallels the
already-wired per-turn rules/memory/KG learning.
"""

from __future__ import annotations

from typing import Any

from runtime.core.cerebrum.react_execution import (
    _RECIPE_REFRESH_EVERY,
    _react_recipe_throttle,
    _reset_recipe_throttle_for_tests,
)


class _Journal:
    """Throttle keys on id(journal); any stable object works."""


class _RecipePlanner:
    def __init__(self) -> None:
        self.assess_calls = 0

    def assess_recipe_from_journal(self, journal: Any) -> None:
        self.assess_calls += 1


def test_fires_only_every_nth_turn() -> None:
    _reset_recipe_throttle_for_tests()
    planner = _RecipePlanner()
    journal = _Journal()

    for _ in range(_RECIPE_REFRESH_EVERY - 1):
        _react_recipe_throttle(journal, planner)
    assert planner.assess_calls == 0  # throttled — not yet

    _react_recipe_throttle(journal, planner)  # the Nth call fires
    assert planner.assess_calls == 1

    # counter reset — the next N-1 are throttled again, then one more fires
    for _ in range(_RECIPE_REFRESH_EVERY - 1):
        _react_recipe_throttle(journal, planner)
    assert planner.assess_calls == 1
    _react_recipe_throttle(journal, planner)
    assert planner.assess_calls == 2


def test_noop_when_planner_has_no_recipe_assessment() -> None:
    _reset_recipe_throttle_for_tests()
    planner = object()  # no assess_recipe_from_journal attribute
    journal = _Journal()
    for _ in range(_RECIPE_REFRESH_EVERY + 2):
        _react_recipe_throttle(journal, planner)  # must never raise


def test_assessment_errors_are_swallowed() -> None:
    _reset_recipe_throttle_for_tests()

    class _Boom:
        def assess_recipe_from_journal(self, journal: Any) -> None:
            raise RuntimeError("boom")

    planner = _Boom()
    journal = _Journal()
    # The Nth call invokes assess (which raises); the throttle must swallow it.
    for _ in range(_RECIPE_REFRESH_EVERY):
        _react_recipe_throttle(journal, planner)

