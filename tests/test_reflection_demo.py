"""Implementation note."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from demos.reflection_demo import run_demo

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not on PATH",
)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestReflectionDemo:
    def test_run_succeeds(self, tmp_path: Path):
        """Implementation note."""
        result = run_demo(
            workdir=tmp_path,
            runs=3,
            color=False,
            verbose=False,
        )
        assert result["success"] is True

    def test_journal_has_enough_events(self, tmp_path: Path):
        """Implementation note."""
        result = run_demo(
            workdir=tmp_path,
            runs=3,
            color=False,
            verbose=False,
        )
        assert result["event_count"] >= 30, (
            f"expected ≥30 events from 3 runs, got {result['event_count']}"
        )

    def test_at_least_half_producers_non_empty(self, tmp_path: Path):
        """Implementation note."""
        result = run_demo(
            workdir=tmp_path,
            runs=3,
            color=False,
            verbose=False,
        )
        assert result["non_empty_producers"] >= 3, (
            f"only {result['non_empty_producers']}/6 producers gave output"
        )

    def test_kg_produces_triples(self, tmp_path: Path):
        """Implementation note."""
        result = run_demo(
            workdir=tmp_path,
            runs=3,
            color=False,
            verbose=False,
        )
        assert result["kg_triples"] >= 1

    def test_memory_clusters_repeated_pattern(self, tmp_path: Path):
        """Implementation note."""
        result = run_demo(
            workdir=tmp_path,
            runs=3,
            color=False,
            verbose=False,
        )
        assert result["memories_count"] >= 1

    def test_skillforge_proposes_candidate(self, tmp_path: Path):
        """Implementation note."""
        result = run_demo(
            workdir=tmp_path,
            runs=3,
            color=False,
            verbose=False,
        )
        assert result["forge_candidates_count"] >= 1

    def test_rewriter_emits_proposal(self, tmp_path: Path):
        """Implementation note."""
        result = run_demo(
            workdir=tmp_path,
            runs=3,
            color=False,
            verbose=False,
        )
        assert result["rewrite_proposals_count"] >= 1

    def test_single_run_fewer_producers(self, tmp_path: Path):
        """Implementation note."""
        result = run_demo(
            workdir=tmp_path,
            runs=1,
            color=False,
            verbose=False,
        )
        # Implementation note.
        assert result["kg_triples"] >= 1
        # Implementation note.
        assert result["forge_candidates_count"] == 0
        assert result["memories_count"] == 0
