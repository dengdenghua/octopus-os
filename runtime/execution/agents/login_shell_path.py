"""Discover the login-shell PATH without caching stale service state."""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=8)
def _login_shell_path_cached(shell: str, inherited_path: str) -> str:
    if not shell or not Path(shell).is_absolute():
        return ""
    env = os.environ.copy()
    env["SHELL"] = shell
    env["PATH"] = inherited_path
    try:
        proc = subprocess.run(
            [shell, "-lc", 'printf "%s" "$PATH"'],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def login_shell_path() -> str:
    """Return login-shell PATH, cached by the current SHELL and PATH."""

    return _login_shell_path_cached(
        os.environ.get("SHELL", "").strip(),
        os.environ.get("PATH", ""),
    )


login_shell_path.cache_clear = _login_shell_path_cached.cache_clear  # type: ignore[attr-defined]


__all__ = ["login_shell_path"]
