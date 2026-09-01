"""Launching chromium when only some of its builds are installed.

Playwright ships chromium as two separate downloads: the full browser
(``chromium``) and a slimmer headless-only build
(``chromium_headless_shell``). A headless ``launch()`` prefers the shell and
raises ``Executable doesn't exist`` when it is absent — even though the full
chromium sitting next to it can do the same job.

That is not a corner case. ``playwright install chromium`` in an environment
where the shell download failed or was skipped leaves exactly this state, and
every browser capability then fails with an error that reads like a missing
install rather than a missing variant.

So: try the default first (the shell is smaller and faster to start), and on a
missing-executable error retry against the full browser via
``channel="chromium"``. Any other launch failure propagates untouched — a
sandbox denial or a crash must not be silently retried into something else.
"""

from __future__ import annotations

from typing import Any

_MISSING_EXECUTABLE = "executable doesn't exist"


def _is_missing_executable(exc: Exception) -> bool:
    return _MISSING_EXECUTABLE in str(exc).lower()


def launch_chromium(chromium: Any, /, **kwargs: Any) -> Any:
    """``chromium.launch(**kwargs)``, falling back to the full browser build."""
    try:
        return chromium.launch(**kwargs)
    except Exception as exc:  # noqa: BLE001 — re-raised unless it is the known gap
        if not _is_missing_executable(exc) or kwargs.get("channel"):
            raise
        return chromium.launch(**{**kwargs, "channel": "chromium"})


def launch_persistent_chromium(chromium: Any, /, **kwargs: Any) -> Any:
    """Same fallback for ``launch_persistent_context``."""
    try:
        return chromium.launch_persistent_context(**kwargs)
    except Exception as exc:  # noqa: BLE001 — re-raised unless it is the known gap
        if not _is_missing_executable(exc) or kwargs.get("channel"):
            raise
        return chromium.launch_persistent_context(**{**kwargs, "channel": "chromium"})


__all__ = ["launch_chromium", "launch_persistent_chromium"]
