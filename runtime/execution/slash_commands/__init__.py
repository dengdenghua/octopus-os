"""
Slash commands · user-defined prompt templates loaded from markdown
files. A `/review-pr 123` input expands to a prompt assembled
from the matching `.md` file.

Directory precedence (later wins on name collision)::

    1. ~/.echo/commands/*.md      (global · user-level)
    2. <cwd>/.echo/commands/*.md  (project-level overrides global)

File format · optional YAML frontmatter + markdown body::

    ---
    description: Review a GitHub PR
    argument-hint: <pr-number>
    allowed-tools: fetch_url, read_file
    model: claude-opus-4
    ---
    Review PR #$1 · focus on security and performance.
    Full request: $ARGUMENTS

Template expansion rules::

    $ARGUMENTS   →  entire arg string (verbatim)
    $1 $2 $3 ... →  positional tokens (shlex-split)
    $<name>      →  named arg when caller passes a dict

Public API::

    load_slash_commands(project_dir=None) -> list[SlashCommand]
    expand(cmd, args_str | args_list | args_dict) -> str
"""

from __future__ import annotations

from .loader import SlashCommand, expand, load_slash_commands

__all__ = ["SlashCommand", "load_slash_commands", "expand"]
