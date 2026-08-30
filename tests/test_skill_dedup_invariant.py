"""Invariant: no physical-duplicate skill packages under all_skills/.

Two skill packages that share byte-identical scripts and near-identical
prose are flagged for consolidation via SKILL.md ``aliases:``. This test
locks in that contract so a regression (someone re-adding an EN/CN twin
as a separate directory) fails CI.

The first time this lands the repo still carries ≥1 known pair — the
test is parametrised to xfail those names so contributors see the
backlog without the test being green-lit dishonestly. Each pair removed
should also be removed from ``_KNOWN_BACKLOG``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools" / "lint"))
from skill_dedup_check import find_duplicates  # noqa: E402

# Pairs we know about as of 2026-05-28 — to be drained as consolidation
# lands. When ``find_duplicates`` returns no pair listed here, that
# pair should be removed; the assertion below catches the case where
# a NEW pair appears that isn't on this list.
_KNOWN_BACKLOG: frozenset[frozenset[str]] = frozenset()


def test_no_unexpected_duplicate_skill_packages() -> None:
    pairs = find_duplicates()
    seen = {frozenset({a.name, b.name}) for a, b, _ in pairs}
    new_pairs = seen - _KNOWN_BACKLOG
    assert not new_pairs, (
        "New duplicate skill package(s) introduced — consolidate via "
        "SKILL.md `aliases:` instead of cloning the directory. Pairs:\n  "
        + "\n  ".join(" ↔ ".join(sorted(p)) for p in new_pairs)
    )


def test_known_backlog_is_still_present() -> None:
    """Reverse guard: when a pair is consolidated, its entry must be
    removed from ``_KNOWN_BACKLOG``. This test fails when the backlog
    set lists pairs that no longer exist on disk so contributors keep
    the list current.
    """
    pairs = find_duplicates()
    seen = {frozenset({a.name, b.name}) for a, b, _ in pairs}
    stale = _KNOWN_BACKLOG - seen
    assert not stale, (
        "Backlog list out of date — these pairs are already merged "
        "(or never existed). Remove them from _KNOWN_BACKLOG in "
        f"{__file__!s}:\n  " + "\n  ".join(" ↔ ".join(sorted(p)) for p in stale)
    )


@pytest.mark.parametrize("pair", sorted([" ↔ ".join(sorted(p)) for p in _KNOWN_BACKLOG]))
def test_each_backlog_pair_xfail(pair: str) -> None:
    """One xfail per known backlog pair — gives contributors a visible
    todo list in the test output without hiding the count."""
    pytest.xfail(
        f"duplicate skill package pair pending alias-consolidation: {pair}",
    )
