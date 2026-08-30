"""
Tests for runtime.execution.slash_commands.

Contract pinned
---------------

1. Load .md files from ~/.echo/commands (via $ECHO_HOME)
2. Frontmatter parsed · description / argument-hint / allowed-tools / model
3. Files without frontmatter still load (empty meta · full body)
4. Project dir overrides global dir for same-named commands
5. $ARGUMENTS → raw arg string
6. $1 $2 → shlex-split positional tokens
7. Named dict args → $<key> expansion
8. Malformed files skipped silently (one bad file doesn't kill list)
9. Missing frontmatter closing delimiter: treat as no frontmatter
10. Nested directories ignored (only top-level *.md)
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    d = tmp_path / "commands"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / ".echo" / "commands").mkdir(parents=True)
    return p


# ═══════════════════════════════════════════════════════════
# Loading
# ═══════════════════════════════════════════════════════════


class TestLoading:
    def test_global_loads_simple_file(self, home: Path):
        (home / "hello.md").write_text("Hello $ARGUMENTS!", encoding="utf-8")
        from runtime.execution.slash_commands import load_slash_commands

        cmds = load_slash_commands()
        command = {item.name: item for item in cmds}["hello"]
        assert command.body == "Hello $ARGUMENTS!"
        assert command.source == "global"

    def test_frontmatter_parsed(self, home: Path):
        (home / "review.md").write_text(
            "---\n"
            "description: Review a PR\n"
            "argument-hint: <pr-number>\n"
            "allowed-tools: fetch_url, read_file\n"
            "model: claude-opus-4\n"
            "---\n"
            "Review PR #$1\n",
            encoding="utf-8",
        )
        from runtime.execution.slash_commands import load_slash_commands

        cmds = load_slash_commands()
        c = {item.name: item for item in cmds}["review"]
        assert c.description == "Review a PR"
        assert c.argument_hint == "<pr-number>"
        assert c.allowed_tools == ("fetch_url", "read_file")
        assert c.model == "claude-opus-4"
        assert c.body.startswith("Review PR")

    def test_no_frontmatter_loads_whole_body(self, home: Path):
        (home / "bare.md").write_text("Just a body $1", encoding="utf-8")
        from runtime.execution.slash_commands import load_slash_commands

        cmds = load_slash_commands()
        command = {item.name: item for item in cmds}["bare"]
        assert command.body == "Just a body $1"
        assert command.description == ""

    def test_project_overrides_global(
        self,
        home: Path,
        project: Path,
    ):
        (home / "dup.md").write_text("global version", encoding="utf-8")
        (project / ".echo" / "commands" / "dup.md").write_text(
            "project version",
            encoding="utf-8",
        )
        from runtime.execution.slash_commands import load_slash_commands

        cmds = load_slash_commands(project_dir=project)
        command = {item.name: item for item in cmds}["dup"]
        assert command.body == "project version"
        assert command.source == "project"

    def test_project_and_global_coexist(
        self,
        home: Path,
        project: Path,
    ):
        (home / "g.md").write_text("global", encoding="utf-8")
        (project / ".echo" / "commands" / "p.md").write_text(
            "proj",
            encoding="utf-8",
        )
        from runtime.execution.slash_commands import load_slash_commands

        cmds = load_slash_commands(project_dir=project)
        names = {c.name for c in cmds}
        assert names == {"project", "g", "p"}

    def test_missing_dirs_return_empty(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("ECHO_HOME", str(tmp_path / "nope"))
        from runtime.execution.slash_commands import load_slash_commands

        assert {command.name for command in load_slash_commands()} == {"project"}

    def test_nested_md_files_ignored(self, home: Path):
        sub = home / "sub"
        sub.mkdir()
        (sub / "nested.md").write_text("nested", encoding="utf-8")
        (home / "top.md").write_text("top", encoding="utf-8")
        from runtime.execution.slash_commands import load_slash_commands

        cmds = load_slash_commands()
        assert {c.name for c in cmds} == {"project", "top"}


# ═══════════════════════════════════════════════════════════
# Expansion
# ═══════════════════════════════════════════════════════════


class TestExpansion:
    def test_arguments_token(self):
        from runtime.execution.slash_commands import SlashCommand, expand

        c = SlashCommand(name="x", body="Echo: $ARGUMENTS end")
        assert expand(c, "foo bar baz") == "Echo: foo bar baz end"

    def test_positional_tokens(self):
        from runtime.execution.slash_commands import SlashCommand, expand

        c = SlashCommand(name="x", body="first=$1 second=$2")
        assert expand(c, "alpha beta") == "first=alpha second=beta"

    def test_shlex_respects_quotes(self):
        from runtime.execution.slash_commands import SlashCommand, expand

        c = SlashCommand(name="x", body="[$1] [$2]")
        assert expand(c, '"hello world" next') == "[hello world] [next]"

    def test_missing_positional_kept_verbatim(self):
        from runtime.execution.slash_commands import SlashCommand, expand

        c = SlashCommand(name="x", body="$1 $2 $3")
        assert expand(c, "only-one") == "only-one $2 $3"

    def test_list_args(self):
        from runtime.execution.slash_commands import SlashCommand, expand

        c = SlashCommand(name="x", body="a=$1 all=$ARGUMENTS")
        assert expand(c, ["alpha", "beta"]) == "a=alpha all=alpha beta"

    def test_dict_args(self):
        from runtime.execution.slash_commands import SlashCommand, expand

        c = SlashCommand(name="x", body="pr=$pr by=$user")
        out = expand(c, {"pr": "123", "user": "alice"})
        assert out == "pr=123 by=alice"

    def test_none_args(self):
        from runtime.execution.slash_commands import SlashCommand, expand

        c = SlashCommand(name="x", body="empty=[$ARGUMENTS]")
        assert expand(c, None) == "empty=[]"

    def test_unmatched_quote_falls_back_to_split(self):
        """shlex raises on unmatched quote · we must not crash."""
        from runtime.execution.slash_commands import SlashCommand, expand

        c = SlashCommand(name="x", body="$1")
        # Should not raise · just splits on whitespace
        out = expand(c, 'bad"quote here')
        assert out  # non-empty

    def test_unknown_named_kept_verbatim(self):
        from runtime.execution.slash_commands import SlashCommand, expand

        c = SlashCommand(name="x", body="$known $unknown")
        out = expand(c, {"known": "yes"})
        assert "yes" in out
        assert "$unknown" in out  # not substituted


# ═══════════════════════════════════════════════════════════
# Serialization (HTTP list endpoint contract)
# ═══════════════════════════════════════════════════════════


class TestSerialization:
    def test_as_dict_projection(self):
        from runtime.execution.slash_commands import SlashCommand

        c = SlashCommand(
            name="review",
            body="x",
            description="Review a PR",
            argument_hint="<pr>",
            allowed_tools=("a", "b"),
            model="m",
            source="global",
            path="/tmp/x",
        )
        d = c.as_dict()
        assert d["name"] == "review"
        assert d["allowed_tools"] == ["a", "b"]
        assert "body" not in d  # body is NOT sent to the UI list
        assert "path" not in d  # path is internal

    def test_malformed_frontmatter_no_closing_delimiter(self, home: Path):
        # Opens --- but never closes · should be treated as no frontmatter
        (home / "bad.md").write_text(
            "---\ndescription: x\n\nrest of file without closing ---",
            encoding="utf-8",
        )
        from runtime.execution.slash_commands import load_slash_commands

        cmds = load_slash_commands()
        command = {item.name: item for item in cmds}["bad"]
        # Description not parsed because frontmatter block was incomplete
        assert command.description == ""
