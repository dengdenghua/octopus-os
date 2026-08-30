"""Shell environment state snapshot model.

Captures the full state of a shell environment (env vars, working
directory, aliases, functions) so it can be serialized, transferred
across process boundaries, and restored in a new subprocess.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShellEnvState:
    """Immutable snapshot of a shell environment.

    Attributes:
        cwd: Current working directory.
        env_vars: Frozen dict of environment variables.
        aliases: Frozen dict of shell aliases (bash/zsh only).
        functions: Frozen dict of shell function definitions (bash/zsh only).
    """

    cwd: str = "/"
    env_vars: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    functions: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(
            {
                "cwd": self.cwd,
                "env_vars": self.env_vars,
                "aliases": self.aliases,
                "functions": self.functions,
            }
        )

    def to_base64(self) -> str:
        """Serialize to base64-encoded JSON (safe for pipe transport)."""
        return base64.b64encode(self.to_json().encode("utf-8")).decode("ascii")

    @classmethod
    def from_json(cls, json_str: str) -> ShellEnvState:
        """Deserialize from JSON string."""
        data = json.loads(json_str)
        return cls(
            cwd=data.get("cwd", "/"),
            env_vars=data.get("env_vars", {}),
            aliases=data.get("aliases", {}),
            functions=data.get("functions", {}),
        )

    @classmethod
    def from_base64(cls, b64_str: str) -> ShellEnvState:
        """Deserialize from base64-encoded JSON."""
        try:
            json_bytes = base64.b64decode(b64_str)
            return cls.from_json(json_bytes.decode("utf-8"))
        except (OSError, TypeError, ValueError):
            _logger.warning("failed to decode base64 shell state")
            return cls()

    def merge_env(self, base_env: dict[str, str] | None) -> dict[str, str]:
        """Merge snapshot env vars onto a base environment.

        Args:
            base_env: Base environment (e.g. os.environ). If None, uses
                the current process environment.

        Returns:
            New dict with base env overlaid by snapshot env vars.
        """
        import os

        merged = dict(base_env or os.environ)
        merged.update(self.env_vars)
        merged["PWD"] = self.cwd
        return merged
