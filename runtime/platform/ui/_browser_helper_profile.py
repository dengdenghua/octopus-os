"""Module-level profile helpers for the browser router.

Pure structural split of ``_browser_router_helpers``: the crash-sentinel
constant and the ``secure_profile_dir`` / ``mark_session_active`` /
``mark_session_closed`` helpers. No logic changes.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

# Written into the profile dir while a persistent browser context is
# live; removed on clean shutdown. A leftover sentinel on the next
# launch means the previous session crashed (or the process was
# killed) — surfaced as ``session["recovered_from_crash"]`` so callers
# can re-validate login state instead of silently trusting it.
_SESSION_SENTINEL_NAME = ".echo_session_active"


def secure_profile_dir(profile_dir: Path) -> None:
    """Owner-only access for the browser profile.

    The persistent context stores cookies and localStorage in
    PLAINTEXT here; the default mkdir mode (umask, typically 755)
    let every local user read login tokens.
    """
    with contextlib.suppress(OSError):
        os.chmod(profile_dir, 0o700)


def mark_session_active(profile_dir: Path) -> bool:
    """Write the crash sentinel. Returns True when a stale sentinel
    was already present (= previous session did not shut down
    cleanly)."""
    sentinel = profile_dir / _SESSION_SENTINEL_NAME
    crashed = sentinel.exists()
    with contextlib.suppress(OSError):
        sentinel.write_text(f"{os.getpid()} {time.time():.0f}\n", encoding="utf-8")
    return crashed


def mark_session_closed(profile_dir: Path | str | None) -> None:
    """Remove the crash sentinel on clean shutdown."""
    if not profile_dir:
        return
    with contextlib.suppress(OSError):
        (Path(profile_dir) / _SESSION_SENTINEL_NAME).unlink(missing_ok=True)
