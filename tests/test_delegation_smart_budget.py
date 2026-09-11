"""Smart-budget regression tests for delegation_skills (2026-06).

Pins the smart-budget rules:
  - Absolute cap: 5 calls/turn
  - Success → counts
  - First-time failure → FREE (fingerprint recorded)
  - Repeat failure (same agent + same normalized prompt) → counts
  - Whitespace/case differences don't bypass the fingerprint

These tests directly poke the budget primitives. Higher-level tests in
``test_delegation_enhancements.py`` cover the integration with
``_call_agent`` and ``_call_agent_parallel``.
"""

from __future__ import annotations

import pytest

from runtime.execution.suckers.delegation_budget import (
    _PER_TURN_ABSOLUTE_LIMIT,
    _TURN_DELEGATIONS,
    _TURN_FAILED_FINGERPRINTS,
)
from runtime.execution.suckers.delegation_budget import (
    check_absolute_cap as _check_absolute_cap,
)
from runtime.execution.suckers.delegation_budget import (
    compute_fingerprint as _compute_fingerprint,
)
from runtime.execution.suckers.delegation_budget import (
    record_delegation as _record_delegation,
)


@pytest.fixture(autouse=True)
def reset_state():
    """Each test gets a clean budget slate."""
    _TURN_DELEGATIONS.clear()
    _TURN_FAILED_FINGERPRINTS.clear()
    yield
    _TURN_DELEGATIONS.clear()
    _TURN_FAILED_FINGERPRINTS.clear()


# ── Fingerprint normalization ────────────────────────────


def test_fingerprint_stable_across_whitespace():
    """Adding whitespace doesn't change fingerprint."""
    fp1 = _compute_fingerprint("researcher", "Investigate sleep patterns")
    fp2 = _compute_fingerprint("researcher", "  Investigate   sleep   patterns  ")
    fp3 = _compute_fingerprint("researcher", "Investigate\nsleep\tpatterns")
    assert fp1 == fp2 == fp3


def test_fingerprint_case_insensitive():
    """Case differences don't change fingerprint."""
    fp1 = _compute_fingerprint("researcher", "Investigate Sleep")
    fp2 = _compute_fingerprint("researcher", "investigate sleep")
    fp3 = _compute_fingerprint("researcher", "INVESTIGATE SLEEP")
    assert fp1 == fp2 == fp3


def test_fingerprint_distinguishes_different_prompts():
    """Different prompts → different fingerprints."""
    fp1 = _compute_fingerprint("researcher", "Investigate sleep")
    fp2 = _compute_fingerprint("researcher", "Investigate diet")
    assert fp1 != fp2


def test_fingerprint_distinguishes_different_agents():
    """Same prompt, different agent → different fingerprints."""
    fp1 = _compute_fingerprint("researcher", "Same task")
    fp2 = _compute_fingerprint("debugger", "Same task")
    assert fp1 != fp2


def test_fingerprint_preserves_punctuation():
    """Punctuation differences DO change fingerprint (not aggressive normalization)."""
    fp1 = _compute_fingerprint("researcher", "Find: causes of insomnia.")
    fp2 = _compute_fingerprint("researcher", "Find causes of insomnia")
    assert fp1 != fp2


# ── Absolute cap ─────────────────────────────────────────


def test_absolute_cap_allows_under_limit():
    """Under the 5-call cap, _check_absolute_cap returns within=True."""
    turn_id = "turn-A"
    cur, within = _check_absolute_cap(turn_id)
    assert cur == 0
    assert within is True

    # Bump 4 times via _record_delegation(succeeded=True)
    for i in range(4):
        _record_delegation(turn_id, f"fp-{i}", succeeded=True)
    cur, within = _check_absolute_cap(turn_id)
    assert cur == 4
    assert within is True  # 4 < 5


def test_absolute_cap_rejects_at_limit():
    """At/over the 5-call cap, _check_absolute_cap returns within=False."""
    turn_id = "turn-B"
    for i in range(5):
        _record_delegation(turn_id, f"fp-{i}", succeeded=True)
    cur, within = _check_absolute_cap(turn_id)
    assert cur == 5
    assert within is False


def test_absolute_cap_off_when_no_turn_id():
    """No turn_id (raw unit tests) → enforcement OFF."""
    cur, within = _check_absolute_cap(None)
    assert cur == 0
    assert within is True


# ── Smart-budget rules ───────────────────────────────────


def test_success_counts_against_budget():
    """A successful delegation bumps the counter."""
    turn_id = "turn-success"
    _record_delegation(turn_id, "fp-x", succeeded=True)
    assert _TURN_DELEGATIONS[turn_id] == 1


def test_first_time_failure_is_free():
    """A first-time failure does NOT bump the counter — fingerprint
    recorded so a repeat would count."""
    turn_id = "turn-fail"
    _record_delegation(turn_id, "fp-x", succeeded=False)
    # Counter unchanged
    assert turn_id not in _TURN_DELEGATIONS
    # But fingerprint recorded
    assert "fp-x" in _TURN_FAILED_FINGERPRINTS[turn_id]


def test_repeat_failure_counts():
    """Repeat failure (same fingerprint) DOES count — prevents loops."""
    turn_id = "turn-repeat"
    # First failure: free
    _record_delegation(turn_id, "fp-x", succeeded=False)
    assert turn_id not in _TURN_DELEGATIONS
    # Repeat: counts
    _record_delegation(turn_id, "fp-x", succeeded=False)
    assert _TURN_DELEGATIONS[turn_id] == 1
    # Repeat again: counts again
    _record_delegation(turn_id, "fp-x", succeeded=False)
    assert _TURN_DELEGATIONS[turn_id] == 2


def test_different_failures_each_get_a_free_pass():
    """Multiple distinct failed fingerprints each get one free try."""
    turn_id = "turn-multi-fail"
    _record_delegation(turn_id, "fp-A", succeeded=False)
    _record_delegation(turn_id, "fp-B", succeeded=False)
    _record_delegation(turn_id, "fp-C", succeeded=False)
    # No counter bump: all 3 were first-time failures
    assert turn_id not in _TURN_DELEGATIONS
    # 3 distinct fingerprints recorded
    assert len(_TURN_FAILED_FINGERPRINTS[turn_id]) == 3


def test_mixed_success_and_failure():
    """Mix: 2 successes + 1 first-fail + 1 repeat-fail = counter at 3."""
    turn_id = "turn-mixed"
    _record_delegation(turn_id, "fp-A", succeeded=True)  # +1
    _record_delegation(turn_id, "fp-B", succeeded=False)  # +0 (first-time)
    _record_delegation(turn_id, "fp-C", succeeded=True)  # +1
    _record_delegation(turn_id, "fp-B", succeeded=False)  # +1 (repeat)
    assert _TURN_DELEGATIONS[turn_id] == 3


def test_fingerprints_isolated_per_turn():
    """Failed fingerprints from turn-A don't leak into turn-B."""
    _record_delegation("turn-A", "fp-x", succeeded=False)
    # turn-B fresh slate: same fingerprint counts as first-time-free
    _record_delegation("turn-B", "fp-x", succeeded=False)
    assert "turn-A" not in _TURN_DELEGATIONS
    assert "turn-B" not in _TURN_DELEGATIONS
    # Fingerprints recorded per-turn
    assert "fp-x" in _TURN_FAILED_FINGERPRINTS["turn-A"]
    assert "fp-x" in _TURN_FAILED_FINGERPRINTS["turn-B"]


def test_no_turn_id_skips_recording():
    """No turn_id → _record_delegation is a no-op (dev mode)."""
    _record_delegation(None, "fp-x", succeeded=True)
    _record_delegation(None, "fp-y", succeeded=False)
    # Nothing recorded
    assert len(_TURN_DELEGATIONS) == 0
    assert len(_TURN_FAILED_FINGERPRINTS) == 0


# ── Integration: cap + smart-budget ──────────────────────


def test_smart_budget_lets_5_unique_failures_through():
    """5 unique failed specs should NOT trip the absolute cap (each
    is first-time-free) — gives the LLM lots of room to iterate."""
    turn_id = "turn-iterate"
    for i in range(5):
        _record_delegation(turn_id, f"fp-{i}", succeeded=False)
    cur, within = _check_absolute_cap(turn_id)
    assert cur == 0  # No counter bump
    assert within is True


def test_5_repeated_failures_trip_the_cap():
    """5 repeats of the same fingerprint DO trip the cap (4 repeats
    + 1 first-time-free = 4 against budget; first failure was free)."""
    turn_id = "turn-loop"
    # First call: free
    _record_delegation(turn_id, "fp-loop", succeeded=False)
    # Calls 2-6: each counts as repeat (same fingerprint)
    for _ in range(5):
        _record_delegation(turn_id, "fp-loop", succeeded=False)
    cur, within = _check_absolute_cap(turn_id)
    assert cur == 5
    assert within is False  # cap exceeded


def test_constant_is_5():
    """Pin the cap value — if anyone tweaks it, this test forces them
    to acknowledge in CHANGELOG."""
    assert _PER_TURN_ABSOLUTE_LIMIT == 5
