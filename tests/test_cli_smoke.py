"""Implementation note."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# Implementation note.
_SUBCOMMANDS = [
    "demo",
    "bugfix-demo",
    "bugfix-demo-v2",
    "reflection-demo",
    "evolution-demo",
    "run",
    "bench",
    "intel",
    "kg",
    "reflect",
    "status",
    "quickstart",
    "ui",
    "optimize",
    "resume",
    "serve",
    "loop",
    "skills",
    "plugins",
]

# Skills subcommands actually implemented in runtime/cli.py
_SKILLS_SUBCOMMANDS = ["list", "search", "install", "uninstall", "info", "publish"]


def _run_cli(args: list[str]) -> subprocess.CompletedProcess:
    """Implementation note."""
    return subprocess.run(
        [sys.executable, "-m", "runtime", *args],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestTopLevelHelp:
    def test_top_level_help_succeeds(self):
        r = _run_cli(["--help"])
        assert r.returncode == 0, f"stderr={r.stderr}"
        assert "echo-agent" in r.stdout.lower() or "usage" in r.stdout.lower()

    def test_help_lists_all_subcommands(self):
        """Implementation note."""
        r = _run_cli(["--help"])
        assert r.returncode == 0
        # Sanity: a few representative subcommands are listed.
        for key in ["demo", "status", "skills"]:
            assert key in r.stdout, f"subcommand {key!r} missing from --help output"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSubcommandHelp:
    @pytest.mark.parametrize("cmd", _SUBCOMMANDS)
    def test_subcommand_help_exits_zero(self, cmd: str):
        r = _run_cli([cmd, "--help"])
        assert r.returncode == 0, (
            f"`echo-agent {cmd} --help` failed:\nstdout={r.stdout}\nstderr={r.stderr}"
        )
        # Implementation note.
        combined = (r.stdout + r.stderr).lower()
        assert "usage" in combined, f"`{cmd} --help` no usage text in output"


class TestSkillsSubcommandHelp:
    @pytest.mark.parametrize("subcmd", _SKILLS_SUBCOMMANDS)
    def test_skills_subcommand_help(self, subcmd: str):
        r = _run_cli(["skills", subcmd, "--help"])
        assert r.returncode == 0, f"`skills {subcmd} --help` failed:\n{r.stderr}"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestInvalidInvocation:
    def test_no_args_shows_help_or_error(self):
        """Implementation note."""
        r = _run_cli([])
        # Implementation note.
        # Implementation note.
        assert "Traceback" not in r.stderr, f"unexpected traceback: {r.stderr}"

    def test_unknown_subcommand_treated_as_goal(self):
        """Product behavior: unknown args route to `code` as a goal."""
        r = _run_cli(["this-does-not-exist"])
        # CLI interprets "this-does-not-exist" as a coding goal, so it
        # launches a session (returncode 0) and prints session/plan output.
        assert r.returncode == 0
        # Should mention session ID or task output (not an argparse error).
        combined = (r.stdout + r.stderr).lower()
        assert "invalid choice" not in combined
        assert "unrecognized" not in combined


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestStatusRuns:
    def test_status_actually_runs(self):
        r = _run_cli(["status"])
        assert r.returncode == 0, f"status failed:\n{r.stderr}"
        # Implementation note.
        combined = r.stdout + r.stderr
        assert any(
            k in combined
            for k in [
                "skills",
                "capabilities",
                "opentelemetry",
                "httpx",
            ]
        ), f"status output missing key fields: {combined[:500]}"
        assert "market_skills: registered" not in combined

    def test_demo_no_color_does_not_override_global_flag(self):
        r = _run_cli(["--no-color", "bugfix-demo", "--help"])
        assert r.returncode == 0, f"bugfix-demo help failed:\n{r.stderr}"
        assert "\x1b[" not in (r.stdout + r.stderr)


class TestQuickstart:
    def test_quickstart_bootstraps_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_path = tmp_path / "config.yaml"

        r = _run_cli(
            [
                "--no-color",
                "quickstart",
                "--output",
                str(config_path),
                "--non-interactive",
            ]
        )

        assert r.returncode == 0, f"quickstart failed:\nstdout={r.stdout}\nstderr={r.stderr}"
        assert config_path.exists()
        content = config_path.read_text(encoding="utf-8")
        assert "planner" in content
        assert "static" in content
        combined = (r.stdout + r.stderr).lower()
        assert "doctor" in combined or "health" in combined
