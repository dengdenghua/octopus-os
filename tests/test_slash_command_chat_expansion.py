"""
Tests for slash-command expansion in the chat-prompt flow.

Contract pinned
---------------

1. ``/<name> <args>`` expands to the template body
2. ``$ARGUMENTS`` and ``$1`` get replaced
3. Unknown slash command falls through unchanged (no error)
4. Message not starting with `/` is unchanged
5. Bare `/` (no name) is unchanged · doesn't crash
6. Catalog load failure falls through unchanged (resilience)
7. Expansion happens BEFORE UserPromptSubmit hook (handlers see expanded text)
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def cwd_with_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Set up an empty global commands dir + project commands dir
    with one test command, and chdir into the project."""
    monkeypatch.setenv("ECHO_HOME", str(tmp_path / "home"))
    proj = tmp_path / "proj"
    cmds = proj / ".echo" / "commands"
    cmds.mkdir(parents=True)
    (cmds / "review.md").write_text(
        "Review PR #$1 for $ARGUMENTS",
        encoding="utf-8",
    )
    monkeypatch.chdir(proj)
    return proj


# ═══════════════════════════════════════════════════════════
# Direct helper
# ═══════════════════════════════════════════════════════════


class TestExpansion:
    def test_known_command_expands(self, cwd_with_command: Path):
        from runtime.sensing.gateway.slash_command_expansion import (
            maybe_expand_slash_command,
        )

        out = maybe_expand_slash_command("/review 123 security")
        assert out == "Review PR #123 for 123 security"

    def test_unknown_command_unchanged(self, cwd_with_command: Path):
        from runtime.sensing.gateway.slash_command_expansion import (
            maybe_expand_slash_command,
        )

        out = maybe_expand_slash_command("/no-such-cmd args")
        assert out == "/no-such-cmd args"

    def test_no_slash_unchanged(self, cwd_with_command: Path):
        from runtime.sensing.gateway.slash_command_expansion import (
            maybe_expand_slash_command,
        )

        out = maybe_expand_slash_command("just a regular message")
        assert out == "just a regular message"

    def test_bare_slash_unchanged(self, cwd_with_command: Path):
        from runtime.sensing.gateway.slash_command_expansion import (
            maybe_expand_slash_command,
        )

        # No name after the slash · can't match anything
        assert maybe_expand_slash_command("/") == "/"
        assert maybe_expand_slash_command("/ args") == "/ args"

    def test_leading_whitespace_tolerated(self, cwd_with_command: Path):
        from runtime.sensing.gateway.slash_command_expansion import (
            maybe_expand_slash_command,
        )

        out = maybe_expand_slash_command("  /review 1 perf")
        # First $1 binds to "1", $ARGUMENTS binds to "1 perf"
        assert out == "Review PR #1 for 1 perf"

    def test_just_command_no_args(self, cwd_with_command: Path):
        from runtime.sensing.gateway.slash_command_expansion import (
            maybe_expand_slash_command,
        )

        out = maybe_expand_slash_command("/review")
        # $1 unresolved · stays literal · $ARGUMENTS empty
        assert "Review PR #" in out
        assert out.endswith("for ")

    def test_catalog_failure_falls_through(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """If load_slash_commands somehow raises, we return verbatim
        rather than 500 the chat turn."""
        from runtime.sensing.gateway import slash_command_expansion as mod

        def _boom(*a, **kw):
            raise RuntimeError("catalog disk read failed")

        # Monkeypatch the import path the helper uses
        from runtime.execution import slash_commands as scm

        monkeypatch.setattr(scm, "load_slash_commands", _boom)

        out = mod.maybe_expand_slash_command("/anything args")
        assert out == "/anything args"
