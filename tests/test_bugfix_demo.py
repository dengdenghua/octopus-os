"""Implementation note."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from demos.bugfix_demo import (
    build_bugfix_graph,
    run_demo,
    setup_buggy_project,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not on PATH",
)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSetupBuggyProject:
    def test_creates_buggy_file(self, tmp_path: Path):
        proj = setup_buggy_project(tmp_path)
        assert proj.is_dir()
        src = (proj / "add.py").read_text(encoding="utf-8")
        assert "return a - b" in src  # Implementation note.
        assert "return a + b" not in src

    def test_test_script_fails_on_buggy_code(self, tmp_path: Path):
        """Implementation note."""
        proj = setup_buggy_project(tmp_path)
        r = subprocess.run(
            [sys.executable, "test_add.py"],
            cwd=str(proj),
            capture_output=True,
            text=True,
        )
        assert r.returncode != 0
        assert "AssertionError" in r.stderr or "AssertionError" in r.stdout

    def test_git_initialized_with_one_commit(self, tmp_path: Path):
        proj = setup_buggy_project(tmp_path)
        r = subprocess.run(
            ["git", "-C", str(proj), "rev-list", "--count", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert r.stdout.strip() == "1"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPlan:
    def test_8_nodes_linear(self, tmp_path: Path):
        proj = setup_buggy_project(tmp_path)
        graph = build_bugfix_graph(proj)
        assert len(graph.nodes) == 8
        assert len(graph.edges) == 7  # linear = n-1 edges
        # Implementation note.
        skills = [n.skill_ref for n in graph.nodes]
        assert str(skills[0]) == "list_cwd"
        assert str(skills[4]) == "edit_text_file"
        assert str(skills[7]) == "git_commit"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRunDemoE2E:
    def test_full_pipeline_succeeds(self, tmp_path: Path):
        result = run_demo(workdir=tmp_path, color=False, verbose=False)
        assert result["success"], f"demo failed · {result}"

    def test_first_test_run_fails(self, tmp_path: Path):
        """Implementation note."""
        result = run_demo(workdir=tmp_path, color=False, verbose=False)
        assert result["test_first_run_exit_code"] is not None
        assert result["test_first_run_exit_code"] != 0

    def test_second_test_run_passes(self, tmp_path: Path):
        """Implementation note."""
        result = run_demo(workdir=tmp_path, color=False, verbose=False)
        assert result["test_second_run_exit_code"] == 0

    def test_commit_actually_created(self, tmp_path: Path):
        result = run_demo(workdir=tmp_path, color=False, verbose=False)
        assert result["commits_after"] == result["commits_before"] + 1

    def test_fix_actually_in_source(self, tmp_path: Path):
        """Implementation note."""
        result = run_demo(workdir=tmp_path, color=False, verbose=False)
        src = (Path(result["project_dir"]) / "add.py").read_text(encoding="utf-8")
        assert "return a + b" in src
        assert "return a - b" not in src

    def test_git_log_shows_fix_commit(self, tmp_path: Path):
        """Implementation note."""
        result = run_demo(workdir=tmp_path, color=False, verbose=False)
        proj = Path(result["project_dir"])
        r = subprocess.run(
            ["git", "-C", str(proj), "log", "--pretty=format:%s"],
            capture_output=True,
            text=True,
            check=True,
        )
        commits = r.stdout.strip().splitlines()
        assert len(commits) == 2
        # Implementation note.
        assert "fix" in commits[0].lower()

    def test_all_steps_succeeded(self, tmp_path: Path):
        """Every step except the intentional red-phase test succeeds."""
        result = run_demo(workdir=tmp_path, color=False, verbose=False)
        for step in result["steps"]:
            if step.node_id == "n2":
                assert not step.success
                assert step.result.error_type == "semantic_error"
                assert step.result.output["exit_code"] != 0
                continue
            assert step.success, (
                f"step {step.node_id} failed · "
                f"skill={step.action.sucker_id} · "
                f"error_type={step.result.error_type}"
            )

    def test_journal_records_all_events(self, tmp_path: Path):
        run_demo(workdir=tmp_path, color=False, verbose=False)
        journal_path = tmp_path / "events.jsonl"
        assert journal_path.exists()
        lines = [
            line for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        # Implementation note.
        assert len(lines) >= 9
