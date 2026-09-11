"""status subcommand smoke tests。"""

from __future__ import annotations

import pytest

from runtime.cli import main, run_status
from runtime.platform.i18n import set_lang


class TestRunStatusFunction:
    def setup_method(self) -> None:
        set_lang("en")

    def test_returns_zero(self, capsys):
        rc = run_status(color=False)
        assert rc == 0
        out = capsys.readouterr().out
        # Implementation note.
        assert "capability status" in out
        assert "Runtime" in out
        assert "LLM providers" in out
        assert "MockModelRouter" in out
        assert "AnthropicModelRouter" in out
        assert "External tooling" in out
        assert "cli.status.check_llm" not in out
        assert "cli.status.check_external" not in out
        assert "httpx" in out.lower()
        assert "playwright" in out.lower()
        assert "Builtin skills" in out
        assert "Reflection producers" in out
        assert "CLI subcommands" in out

    def test_lists_all_6_reflection_producers(self, capsys):
        run_status(color=False)
        out = capsys.readouterr().out
        for name in [
            "SkillForge",
            "RuleExtractor",
            "KGUpdater",
            "MemoryConsolidator",
            "WorkflowRewriter",
            "RecipeEvaluator",
        ]:
            assert name in out, f"missing {name!r} in status output"

    def test_lists_all_7_cli_commands(self, capsys):
        run_status(color=False)
        out = capsys.readouterr().out
        for cmd in ["demo", "run", "bench", "intel", "kg", "reflect", "status"]:
            assert cmd in out

    def test_lists_builtin_skills(self, capsys):
        run_status(color=False)
        out = capsys.readouterr().out
        for name in ["list_cwd", "read_file", "count_words", "hash_text", "file_stats"]:
            assert name in out


class TestMainCLIStatus:
    def test_status_subcommand_works(self, capsys):
        rc = main(["--no-color", "status"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "capability status" in out

    def test_status_in_help(self):

        with pytest.raises(SystemExit):
            main(["--help"])
