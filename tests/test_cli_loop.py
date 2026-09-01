"""Implementation note."""

from __future__ import annotations

from pathlib import Path

from runtime.cli import main, run_loop
from runtime.platform.i18n import set_lang

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _write_config(
    tmp_path: Path,
    *,
    planner_type: str,
    mock_response: str | None = None,
) -> Path:
    path = tmp_path / "cfg.yaml"
    if planner_type == "llm":
        path.write_text(
            "planner:\n"
            "  type: llm\n"
            "  model: mock/loop\n"
            f"  mock_response: '{mock_response}'\n"
            "budget:\n"
            "  max_tokens: 5000\n"
            "  max_usd: 0.05\n",
            encoding="utf-8",
        )
    else:
        path.write_text(
            "planner:\n  type: static\nbudget:\n  max_tokens: 5000\n  max_usd: 0.05\n",
            encoding="utf-8",
        )
    return path


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLoopWithLLMPlanner:
    def setup_method(self) -> None:
        set_lang("en")

    def test_multiple_iterations_success(self, tmp_path: Path, capsys):
        cfg = _write_config(
            tmp_path,
            planner_type="llm",
            mock_response='{"reasoning":"r","nodes":[{"skill":"list_cwd","args":{}}]}',
        )
        journal = tmp_path / "events.jsonl"

        rc = run_loop(
            goal="list files",
            config_path=cfg,
            journal_path=journal,
            iterations=3,
            color=False,
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "[Iteration 1/3]" in out
        assert "[Iteration 3/3]" in out
        # Implementation note.
        # Implementation note.
        # Implementation note.
        assert "succeeded" in out
        # Implementation note.
        assert "learn" in out.lower() or "Learn" in out
        assert "verdict=" in out
        # Implementation note.
        assert journal.exists()
        assert journal.stat().st_size > 0

    def test_journal_accumulates(self, tmp_path: Path, capsys):
        """Implementation note."""
        cfg = _write_config(
            tmp_path,
            planner_type="llm",
            mock_response='{"reasoning":"r","nodes":[{"skill":"list_cwd","args":{}}]}',
        )
        journal = tmp_path / "events.jsonl"

        run_loop(
            goal="list files",
            config_path=cfg,
            journal_path=journal,
            iterations=2,
            color=False,
        )
        # Implementation note.
        lines = journal.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 2


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLoopWithStaticPlanner:
    def setup_method(self) -> None:
        set_lang("en")

    def test_static_planner_rewrite_branch_taken(self, tmp_path: Path, capsys):
        """Implementation note."""
        cfg = _write_config(tmp_path, planner_type="static")
        journal = tmp_path / "events.jsonl"

        rc = run_loop(
            goal="list files and hash",
            config_path=cfg,
            journal_path=journal,
            iterations=2,
            color=False,
        )
        # Implementation note.
        assert rc in (0, 1)
        out = capsys.readouterr().out
        assert "rewrite" in out
        assert "applied=" in out
        # Implementation note.
        assert "verdict=" not in out


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLoopErrorPaths:
    def setup_method(self) -> None:
        set_lang("en")

    def test_missing_config_returns_2(self, tmp_path: Path, capsys):
        rc = run_loop(
            goal="x",
            config_path=tmp_path / "no.yaml",
            journal_path=tmp_path / "events.jsonl",
            iterations=1,
            color=False,
        )
        assert rc == 2
        assert "config error" in capsys.readouterr().err


# ═══════════════════════════════════════════════════════════
# CLI argparse
# ═══════════════════════════════════════════════════════════


class TestCLIWiring:
    def setup_method(self) -> None:
        set_lang("en")

    def test_cli_dispatches_to_run_loop(self, tmp_path: Path, capsys):
        cfg = _write_config(
            tmp_path,
            planner_type="llm",
            mock_response='{"reasoning":"r","nodes":[{"skill":"list_cwd","args":{}}]}',
        )
        journal = tmp_path / "events.jsonl"

        rc = main(
            [
                "--no-color",
                "--lang",
                "en",
                "loop",
                "list things",
                "--config",
                str(cfg),
                "--journal",
                str(journal),
                "--iterations",
                "1",
            ]
        )
        assert rc in (0, 1)
        out = capsys.readouterr().out
        assert "[Iteration 1/1]" in out


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLoopClosureEvidence:
    """Implementation note."""

    def setup_method(self) -> None:
        set_lang("en")

    def test_each_iter_sees_prior_events(self, tmp_path: Path, monkeypatch, capsys):
        from runtime.core.cerebrum import LLMPlanner

        observed_counts: list[int] = []
        original = LLMPlanner.learn_memories_from_journal

        def spy(self, journal):
            n_trajs = len(journal.read_by_type("trajectory"))
            observed_counts.append(n_trajs)
            return original(self, journal)

        monkeypatch.setattr(LLMPlanner, "learn_memories_from_journal", spy)

        cfg = _write_config(
            tmp_path,
            planner_type="llm",
            mock_response='{"reasoning":"r","nodes":[{"skill":"list_cwd","args":{}}]}',
        )
        journal = tmp_path / "events.jsonl"

        rc = run_loop(
            goal="probe",
            config_path=cfg,
            journal_path=journal,
            iterations=3,
            color=False,
        )
        assert rc == 0
        # Implementation note.
        assert observed_counts == [0, 1, 2], f"closure broken · observed={observed_counts}"

    def test_planner_state_accumulates_across_iters(self, tmp_path: Path, monkeypatch):
        """Implementation note."""
        from runtime.core.cerebrum import LLMPlanner

        captured: dict = {}
        original_plan = LLMPlanner.plan

        def spy_plan(self, intent):
            captured["last"] = {
                "rules_updated": self._rules_updated_count,
                "memories_updated": self._memories_updated_count,
                "kg_attached": self._kg_attached_count,
                "recipe_assessed": self._recipe_assessed_count,
            }
            return original_plan(self, intent)

        monkeypatch.setattr(LLMPlanner, "plan", spy_plan)

        cfg = _write_config(
            tmp_path,
            planner_type="llm",
            mock_response='{"reasoning":"r","nodes":[{"skill":"list_cwd","args":{}}]}',
        )
        journal = tmp_path / "events.jsonl"

        run_loop(
            goal="probe",
            config_path=cfg,
            journal_path=journal,
            iterations=3,
            color=False,
        )
        # Implementation note.
        last = captured["last"]
        assert last["rules_updated"] == 3
        assert last["memories_updated"] == 3
        assert last["kg_attached"] == 3
        assert last["recipe_assessed"] == 3

    def test_journal_event_counts_grow_monotonically(self, tmp_path: Path):
        """Implementation note."""
        from runtime.memory.journal import JSONLJournal

        cfg = _write_config(
            tmp_path,
            planner_type="llm",
            mock_response='{"reasoning":"r","nodes":[{"skill":"list_cwd","args":{}}]}',
        )
        journal_path = tmp_path / "events.jsonl"

        sizes: list[int] = []
        # Implementation note.
        for _ in range(3):
            run_loop(
                goal="probe",
                config_path=cfg,
                journal_path=journal_path,
                iterations=1,
                color=False,
            )
            sizes.append(len(JSONLJournal(journal_path)))

        # Implementation note.
        assert sizes[0] >= 2
        assert sizes[1] > sizes[0]
        assert sizes[2] > sizes[1]
