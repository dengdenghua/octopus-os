"""safe_rm — file protection mechanism for shell commands.

Intercepts dangerous file operations (rm, del, mv, cp, Out-File,
Set-Content, etc.) and enforces path-based allow/deny lists.
Implemented as a Python module that wraps commands before
execution.

Protection levels
~~~~~~~~~~~~~~~~~
- ``strict``: Block ALL dangerous commands, no exceptions.
- ``moderate``: Allow only paths in the allow list, block denied paths.
- ``lenient``: Only block explicitly denied paths.

Supported shells
~~~~~~~~~~~~~~~~
- bash / zsh (POSIX)
- PowerShell / pwsh
- cmd.exe
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

_logger = logging.getLogger(__name__)

# ── Dangerous commands per shell ──────────────────────────────

_POSIX_DANGEROUS = {
    "rm",
    "rmdir",
    "unlink",
    "mv",
    "cp",
    "dd",
    "chmod",
    "chown",
    "chgrp",
    "truncate",
    "shred",
    "tee",
}

_POWERSHELL_DANGEROUS = {
    "Remove-Item",
    "ri",
    "del",
    "rm",
    "rmdir",
    "rd",
    "Move-Item",
    "mi",
    "move",
    "mv",
    "Copy-Item",
    "ci",
    "copy",
    "cp",
    "cpi",
    "Out-File",
    ">",
    "Set-Content",
    "sc",
    "Clear-Content",
    "clc",
    "Add-Content",
    "ac",
}

_CMD_DANGEROUS = {
    "del",
    "erase",
    "rd",
    "rmdir",
    "move",
    "mv",
    "copy",
    "xcopy",
    "format",
    "diskpart",
}

# ── Default deny list (always blocked) ────────────────────────

_DEFAULT_DENY_PATHS = [
    "/",
    "/System",
    "/Windows",
    "/Program Files",
    "/Program Files (x86)",
    "/usr/bin",
    "/usr/sbin",
    "/bin",
    "/sbin",
    "/etc",
    "/boot",
    "/var",
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\ProgramData",
]

_DEFAULT_ALLOW_PATHS = [
    "./",
    "../",
    "/tmp",  # nosec B108 — default allow-path entry, not a temp file operation
    "/var/tmp",  # nosec B108 — default allow-path entry, not a temp file operation
    "$TMPDIR",
    "$TMP",
    "$TEMP",
    "C:\\Users",
    "/Users",
    "/home",
]


class ProtectionLevel:
    STRICT = "strict"
    MODERATE = "moderate"
    LENIENT = "lenient"


@dataclass
class SafeRmConfig:
    """Configuration for safe_rm protection."""

    enabled: bool = True
    level: str = ProtectionLevel.MODERATE
    deny_paths: list[str] = field(default_factory=lambda: list(_DEFAULT_DENY_PATHS))
    allow_paths: list[str] = field(default_factory=lambda: list(_DEFAULT_ALLOW_PATHS))
    blocked_commands: set[str] = field(default_factory=set)
    shell_type: str = "bash"

    def __post_init__(self) -> None:
        if self.blocked_commands:
            return
        if self.shell_type in ("bash", "zsh"):
            self.blocked_commands = set(_POSIX_DANGEROUS)
        elif self.shell_type in ("pwsh", "powershell"):
            self.blocked_commands = set(_POWERSHELL_DANGEROUS)
        elif self.shell_type == "cmd":
            self.blocked_commands = set(_CMD_DANGEROUS)


class SafeRmProtector:
    """Intercepts and blocks dangerous file operations.

    Usage:
        protector = SafeRmProtector()
        wrapped = protector.wrap_command("rm -rf /")
        if wrapped != original:
            print("Command was blocked or modified")
    """

    def __init__(self, config: SafeRmConfig | None = None) -> None:
        self._config = config or SafeRmConfig()
        self._blocked_count = 0

    @property
    def blocked_count(self) -> int:
        return self._blocked_count

    def wrap_command(self, user_command: str) -> str:
        """Wrap or block a command based on protection rules.

        Returns:
            The original command if allowed, or a blocking echo if denied.
        """
        if not self._config.enabled:
            return user_command

        if self._is_dangerous_command(user_command):
            return self._block_command(user_command)

        if self._config.level != ProtectionLevel.STRICT and self._has_denied_path(user_command):
            return self._block_command(user_command)

        if self._config.level == ProtectionLevel.MODERATE and not self._has_allowed_path(
            user_command
        ):
            return self._block_command(user_command)

        return user_command

    def _is_dangerous_command(self, command: str) -> bool:
        """Check if the command is in the blocked commands list."""
        cmd_stripped = command.strip()
        parts = cmd_stripped.split()
        first_word = parts[0] if parts else ""
        base_command = os.path.basename(first_word)

        blocked_lower = {c.lower() for c in self._config.blocked_commands}
        return base_command.lower() in blocked_lower or first_word.lower() in blocked_lower

    def _has_denied_path(self, command: str) -> bool:
        """Check if the command references any denied paths."""
        cmd_lower = command.lower()
        for deny_path in self._config.deny_paths:
            if deny_path.lower() in cmd_lower:
                _logger.warning(
                    "safe_rm: blocked command referencing denied path: %s",
                    command[:100],
                )
                return True
        return False

    def _has_allowed_path(self, command: str) -> bool:
        """Check if the command references any allowed paths."""
        return any(allow_path in command for allow_path in self._config.allow_paths)

    def _block_command(self, original: str) -> str:
        """Replace a dangerous command with a blocking message."""
        self._blocked_count += 1
        _logger.warning(
            "safe_rm: blocked dangerous command: %s",
            original[:100],
        )

        if self._config.shell_type in ("bash", "zsh"):
            return (
                f'echo "🛡️ safe_rm: blocked dangerous command: '
                f'{self._escape(original[:80])}" && exit 1'
            )
        if self._config.shell_type in ("pwsh", "powershell"):
            return (
                f'Write-Error "safe_rm: blocked dangerous command: '
                f'{self._escape_ps(original[:80])}"; exit 1'
            )
        return f'echo "safe_rm: blocked: {self._escape(original[:80])}" && exit 1'

    @staticmethod
    def _escape(s: str) -> str:
        """Escape for bash echo."""
        return s.replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")

    @staticmethod
    def _escape_ps(s: str) -> str:
        """Escape for PowerShell."""
        return s.replace('"', '`"').replace("$", "`$").replace("`", "``")
