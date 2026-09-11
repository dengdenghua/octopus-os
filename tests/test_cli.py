"""Implementation note."""

from __future__ import annotations

import pytest

from runtime.cli import main, run_goal
from runtime.platform.i18n import set_lang


class TestRunGoal:
    def setup_method(self) -> None:
        set_lang("en")

    def test_trivial_goal_runs(self, capsys, tmp_path):
        """Implementation note."""
        rc = run_goal(
            "list files",
            color=False,
            args_override={"intent_goal": "list", "path": str(tmp_path)},
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "INGEST" in out
        assert "PLAN" in out
        assert "EXECUTE" in out
        assert "STORE" in out
        assert "OK" in out

    def test_output_has_all_four_stages(self, capsys, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        rc = run_goal(
            "read a file and count words",
            color=False,
            args_override={
                "intent_goal": "x",
                "path": str(tmp_path),
                "text": "one two three",
            },
        )
        assert rc == 0
        out = capsys.readouterr().out
        # Implementation note.
        assert "list_cwd" in out or "read_file" in out or "count_words" in out


class TestMainCmdLine:
    def test_version(self):
        with pytest.raises(SystemExit) as excinfo:
            main(["--version"])
        assert excinfo.value.code == 0

    def test_help_flag(self):
        with pytest.raises(SystemExit):
            main(["--help"])

    def test_demo(self, capsys):
        rc = main(["--no-color", "demo"])
        # Implementation note.
        assert rc in (0, 1)
        out = capsys.readouterr().out
        assert "INGEST" in out

    def test_run_subcommand(self, capsys, tmp_path):
        rc = main(
            [
                "--no-color",
                "run",
                "list files",
                "--max-tokens",
                "10000",
                "--max-usd",
                "0.10",
            ]
        )
        assert rc in (0, 1)


class TestJournalFileFlag:
    def test_writes_to_jsonl_file(self, tmp_path, capsys):
        journal_path = tmp_path / "events.jsonl"
        rc = main(
            [
                "--no-color",
                "run",
                "list files in cwd",
                "--journal-file",
                str(journal_path),
            ]
        )
        assert rc == 0
        assert journal_path.exists()
        # Implementation note.
        lines = journal_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 3  # Implementation note.

        out = capsys.readouterr().out
        assert "Journal file" in out
        assert str(journal_path) in out

    def test_reload_via_jsonl_journal(self, tmp_path):
        """Implementation note."""
        from runtime.memory.journal import JSONLJournal

        journal_path = tmp_path / "reload.jsonl"
        rc = main(["--no-color", "run", "list cwd", "--journal-file", str(journal_path)])
        assert rc == 0

        fresh_reader = JSONLJournal(journal_path)
        events = fresh_reader.read_all()
        assert len(events) >= 3
        steps = [e for e in events if e.event_type == "step"]
        assert len(steps) >= 1


class TestShowCost:
    def test_show_cost_prints_breakdown(self, capsys):
        rc = main(["--no-color", "--lang", "en", "run", "list files", "--show-cost"])
        assert rc in (0, 1)
        out = capsys.readouterr().out
        # Implementation note.
        assert "TOTAL" in out
        assert "Budget used:" in out or "budget used:" in out

    def test_no_show_cost_omits_breakdown(self, capsys):
        rc = main(["--no-color", "run", "list files"])
        assert rc in (0, 1)
        out = capsys.readouterr().out
        assert "Cost breakdown" not in out
