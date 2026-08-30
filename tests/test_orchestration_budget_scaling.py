"""Opt-in, budget-driven orchestration depth — default behaviour preserved.

`run_orchestration`'s spawn budget was a fixed `n*rounds` estimate hard-capped
at 48, which throttles a deep verify+synth run (natural usage ~121). This adds
an OPT-IN lever: a trusted token budget (set in session metadata by the
bus/operator) scales the spawn ceiling up to a higher deep-mode cap. With no
budget the conservative `n*rounds`/48 default is unchanged. Both policy helpers
are pure, so this is verified without spawning agents.
"""

from __future__ import annotations

from runtime.execution.suckers.delegation_budget import (
    max_spawns_for_token_budget,
    operator_orchestration_token_budget,
)
from runtime.execution.suckers.delegation_skills import (
    _ORCH_MAX_SPAWNS_CEILING,
    _resolve_max_spawns,
)


class TestMaxSpawnsForTokenBudget:
    def test_missing_or_nonpositive_budget_falls_to_floor(self) -> None:
        assert max_spawns_for_token_budget(None) == 2
        assert max_spawns_for_token_budget(0) == 2
        assert max_spawns_for_token_budget(-5) == 2
        assert max_spawns_for_token_budget("bad") == 2

    def test_budget_scales_linearly_between_floor_and_ceiling(self) -> None:
        # 8000 tokens/spawn default → 400k buys 50 spawns
        assert max_spawns_for_token_budget(400_000) == 50
        assert max_spawns_for_token_budget(16_000) == 2  # 2 spawns, == floor

    def test_huge_budget_is_clamped_to_ceiling(self) -> None:
        assert max_spawns_for_token_budget(10_000_000) == 256
        assert max_spawns_for_token_budget(10_000_000, ceiling=100) == 100


class TestResolveMaxSpawns:
    def test_default_no_budget_allows_the_full_natural_plan(self) -> None:
        # deep verify+synth naturally wants n*rounds + n*rounds*voters + 1 = 121.
        # The old 48 ceiling throttled this mid-fan-out; 256 lets it run whole.
        got = _resolve_max_spawns(None, n=6, rounds=5, verify=True, synthesize=True)
        assert got == 121 < _ORCH_MAX_SPAWNS_CEILING

    def test_default_is_still_clamped_at_the_ceiling(self) -> None:
        # 8*8*4 + 1 = 257 overshoots, so the ceiling still binds
        got = _resolve_max_spawns(None, n=8, rounds=8, verify=True, synthesize=True)
        assert got == _ORCH_MAX_SPAWNS_CEILING == 256

    def test_default_small_run_uses_n_rounds(self) -> None:
        got = _resolve_max_spawns(None, n=3, rounds=2, verify=False, synthesize=False)
        assert got == 6  # n*rounds, no verify/synth

    def test_token_budget_opt_in_scales_to_the_budget(self) -> None:
        got = _resolve_max_spawns(
            None, n=3, rounds=2, verify=True, synthesize=True, token_budget=400_000
        )
        assert got == 50  # budget-driven: 400k / 8k per spawn

    def test_explicit_max_spawns_wins_over_budget(self) -> None:
        got = _resolve_max_spawns(
            10, n=3, rounds=2, verify=True, synthesize=True, token_budget=400_000
        )
        assert got == 10  # explicit wins; budget ignored

    def test_explicit_is_honoured_below_the_ceiling(self) -> None:
        assert _resolve_max_spawns(100, n=3, rounds=2, verify=False, synthesize=False) == 100

    def test_explicit_is_capped_at_the_ceiling(self) -> None:
        # a model-declared max_spawns can reach 256 but never exceed it
        assert _resolve_max_spawns(10_000, n=3, rounds=2, verify=False, synthesize=False) == 256

    def test_budget_never_below_n(self) -> None:
        # tiny budget (→ floor 2) must still allow at least n per round
        got = _resolve_max_spawns(
            None, n=5, rounds=2, verify=False, synthesize=False, token_budget=8_000
        )
        assert got == 5


class TestOperatorEnvBudget:
    _ENV = "ECHO_ORCH_TOKEN_BUDGET"

    def test_unset_is_none(self, monkeypatch) -> None:
        monkeypatch.delenv(self._ENV, raising=False)
        assert operator_orchestration_token_budget() is None

    def test_set_positive_returns_value(self, monkeypatch) -> None:
        monkeypatch.setenv(self._ENV, "400000")
        assert operator_orchestration_token_budget() == 400_000

    def test_invalid_or_nonpositive_is_none(self, monkeypatch) -> None:
        for bad in ("", "  ", "abc", "0", "-100"):
            monkeypatch.setenv(self._ENV, bad)
            assert operator_orchestration_token_budget() is None

    def test_operator_env_drives_resolve_max_spawns(self, monkeypatch) -> None:
        # the operator switch, fed through the resolver, overrides the n*rounds
        # estimate (which would only be 3*2 + 3*2*3 + 1 = 25 here)
        monkeypatch.setenv(self._ENV, "400000")
        budget = operator_orchestration_token_budget()
        got = _resolve_max_spawns(
            None, n=3, rounds=2, verify=True, synthesize=True, token_budget=budget
        )
        assert got == 50
        assert got <= _ORCH_MAX_SPAWNS_CEILING

