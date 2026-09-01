"""Focused unit tests for delegation_budget.py (standalone module).

This module was extracted from delegation_skills.py in 2026-06. The
smart-budget tests in ``test_delegation_smart_budget.py`` are the
canonical regression suite; this file is a lightweight sanity check
that the module can be imported and used independently.
"""

from __future__ import annotations

from runtime.execution.suckers.delegation_budget import (
    _PER_TURN_ABSOLUTE_LIMIT,
    check_absolute_cap,
    compute_fingerprint,
    record_delegation,
)


def test_module_constants():
    """Pin the module-level constants."""
    assert _PER_TURN_ABSOLUTE_LIMIT == 5


def test_compute_fingerprint_basic():
    """Fingerprint normalizes whitespace + case."""
    fp1 = compute_fingerprint("researcher", "Investigate sleep")
    fp2 = compute_fingerprint("researcher", "  investigate   SLEEP  ")
    assert fp1 == fp2
    assert len(fp1) == 16  # [:16] from sha256 hex


def test_check_absolute_cap_no_turn():
    """When turn_id is None, enforcement is OFF."""
    cur, within = check_absolute_cap(None)
    assert cur == 0
    assert within is True


def test_record_delegation_success_increments():
    """Success bumps the counter."""
    from runtime.execution.suckers.delegation_budget import _TURN_DELEGATIONS

    _TURN_DELEGATIONS.clear()
    record_delegation("t1", "fp-x", succeeded=True)
    assert _TURN_DELEGATIONS["t1"] == 1


def test_record_delegation_first_failure_free():
    """First-time failure doesn't bump counter."""
    from runtime.execution.suckers.delegation_budget import (
        _TURN_DELEGATIONS,
        _TURN_FAILED_FINGERPRINTS,
    )

    _TURN_DELEGATIONS.clear()
    _TURN_FAILED_FINGERPRINTS.clear()
    record_delegation("t2", "fp-y", succeeded=False)
    assert "t2" not in _TURN_DELEGATIONS
    assert "fp-y" in _TURN_FAILED_FINGERPRINTS["t2"]


def test_record_delegation_repeat_failure_counts():
    """Repeat failure DOES bump counter."""
    from runtime.execution.suckers.delegation_budget import (
        _TURN_DELEGATIONS,
        _TURN_FAILED_FINGERPRINTS,
    )

    _TURN_DELEGATIONS.clear()
    _TURN_FAILED_FINGERPRINTS.clear()
    record_delegation("t3", "fp-z", succeeded=False)  # First: free
    record_delegation("t3", "fp-z", succeeded=False)  # Repeat: counts
    assert _TURN_DELEGATIONS["t3"] == 1
