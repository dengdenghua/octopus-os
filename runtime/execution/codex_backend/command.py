"""Shared executable resolution for Codex control and execution planes."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from runtime.execution.agents.login_shell_path import login_shell_path

from .types import ConfigurationError


def _resolve_codex_command(command: str) -> str | None:
    path = shutil.which(command)
    if path:
        return path
    if os.path.sep in command or (os.path.altsep and os.path.altsep in command):
        return None
    candidates: list[str] = []
    for raw in (os.environ.get("PATH", ""), login_shell_path()):
        candidates.extend(raw.split(os.pathsep))
    candidates.extend(
        (
            str(Path.home() / ".local" / "bin"),
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/Applications/ChatGPT.app/Contents/Resources",
        )
    )
    for directory in dict.fromkeys(item.strip() for item in candidates if item.strip()):
        candidate = Path(directory).expanduser() / command
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate.resolve())
        except OSError:
            continue
    return None


def resolve_codex_app_server_command(executable: str | None = None) -> tuple[str, ...]:
    """Resolve one absolute Codex binary and pin the App Server argv.

    Packaged macOS builds often have no ``codex`` on the service PATH while
    ChatGPT ships it in ``/Applications/ChatGPT.app/Contents/Resources``.
    ``resolve_local_command`` already probes service PATH, login-shell PATH,
    common install bins, and that packaged resource directory. Both account
    login and real Coder turns call this function so they cannot drift to
    different Codex versions.
    """

    candidate = str(os.environ.get("ECHO_CODEX_EXECUTABLE") or executable or "codex").strip()
    if not candidate or "\x00" in candidate:
        raise ConfigurationError("Codex executable is invalid")
    expanded = Path(candidate).expanduser()
    resolved: str | None
    if (
        expanded.is_absolute()
        or os.path.sep in candidate
        or (os.path.altsep is not None and os.path.altsep in candidate)
    ):
        try:
            if not expanded.is_file() or not os.access(expanded, os.X_OK):
                resolved = None
            else:
                resolved = str(expanded.resolve(strict=True))
        except OSError:
            resolved = None
    else:
        resolved = _resolve_codex_command(candidate)
    if resolved is None:
        raise ConfigurationError("Codex executable is unavailable")
    return (resolved, "app-server", "--strict-config", "--listen", "stdio://")


__all__ = ["resolve_codex_app_server_command"]
