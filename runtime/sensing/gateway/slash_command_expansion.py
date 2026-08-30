"""Slash-command expansion for realtime chat input."""

from __future__ import annotations


def maybe_expand_slash_command(goal: str) -> str:
    """Expand a leading ``/<name>`` command into its configured template."""
    stripped = goal.lstrip()
    if not stripped.startswith("/"):
        return goal
    head, _, args = stripped[1:].partition(" ")
    name = head.strip()
    if not name:
        return goal
    # Project OS control surface: ``/project ...`` is an explicit intent handled
    # by the Project OS command driver, independently of the group's response
    # mode. A bundled ``project`` command exists so composer typeahead can
    # surface it; its body must never replace the user's raw control text.
    if name == "project":
        return goal
    try:
        import os

        from runtime.execution.slash_commands import expand, load_slash_commands

        commands = load_slash_commands(project_dir=os.getcwd())
    except (ImportError, OSError, ValueError, TypeError, RuntimeError):
        return goal
    match = next((command for command in commands if command.name == name), None)
    if match is None:
        return goal
    try:
        return expand(match, args)
    except (OSError, ValueError, TypeError, RuntimeError):
        return goal


__all__ = ["maybe_expand_slash_command"]
