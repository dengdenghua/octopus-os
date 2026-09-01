"""User-defined path denylist — Marvis-style "不可读取文件夹".

Three layers compose into a final access verdict:

  1. Global denylist (user-set in Settings → Privacy & Security):
     applies to every agent / sub-agent / tool. Persisted in
     ``data/path_denylist.json``.
  2. Role-specific denylist (operator-configured): each sub-agent
     role can have additional forbidden paths. Reserved for future
     use; the default role policy is "no extra restrictions".
  3. Per-turn ad-hoc denylist (model can add): a sub-agent
     dispatched by the parent can be told "don't touch ``.env`` this
     turn" and the bridge propagates that into the child's
     denylist.

The denylist is a **prefix-match** list of canonicalised filesystem
paths. Any read/write/glob/grep target whose resolved path starts
with one of the denied prefixes is rejected with reason
``denylist_blocked: <prefix>``.

Why this matters for multi-agent
--------------------------------

Without it, "调研项目" can let a researcher sub-agent grep into
``~/.vscode/extensions/`` and exfiltrate other workspaces' tokens,
or "整理桌面" can let an explorer agent walk
``AppData\\Roaming\\Mozilla\\Firefox\\Profiles\\``. Sandbox-root
checks alone don't protect against curiosity inside the sandbox.

The denylist is **above sandbox** — it applies even when the path
sits inside the configured sandbox root. A sandbox says "this is
the working area"; a denylist says "but not these spots inside it
either".
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterable
from contextvars import ContextVar
from pathlib import Path

_log = logging.getLogger("echo.path_denylist")

# Sensible defaults shipped with the product, mirroring Marvis's
# pre-populated list. Users add to / remove from this through the
# UI; the file on disk is the source of truth.
_DEFAULT_DENIES: tuple[str, ...] = (
    # Editor / IDE state
    "%USERPROFILE%/.vscode",
    "~/.vscode",
    "~/.idea",
    "~/.cursor",
    # Browsers / OS state caches
    "%APPDATA%",
    "~/.cache",
    "~/Library/Caches",
    "~/.local/share",
    # Windows program installs
    "C:/Program Files",
    "C:/Program Files (x86)",
    "C:/ProgramData",
    "C:/Windows",
    # Cloud / auth credentials (also caught by path_guard sensitivity
    # check; included here too so a user disabling sensitive can still
    # see them in the UI list)
    "~/.ssh",
    "~/.aws",
    "~/.gcp",
    "~/.kube",
    "~/.gnupg",
    "~/.netrc",
)


def _state_path() -> Path:
    explicit = os.environ.get("ECHO_PATH_DENYLIST_PATH")
    if explicit:
        return Path(explicit).expanduser()
    try:
        from runtime.platform.process.paths import app_paths

        return app_paths().data_dir / "path_denylist.json"
    except Exception:  # noqa: BLE001
        return Path("data") / "path_denylist.json"


def _expand(prefix: str) -> str:
    """Canonicalise a denylist prefix.

    Resolves ``~``, ``$HOME``, and Windows ``%USERPROFILE%`` /
    ``%APPDATA%`` style variables; returns an absolute canonical
    string that ``Path.resolve()`` would produce. Best-effort —
    returns the input unchanged on any error.
    """
    raw = (prefix or "").strip()
    if not raw:
        return ""
    try:
        expanded = os.path.expandvars(os.path.expanduser(raw))
        return str(Path(expanded).resolve(strict=False))
    except (OSError, ValueError):
        return raw


def _load_user_denylist() -> list[str]:
    """Read the user-managed denylist from disk.

    Returns an empty list when the file doesn't exist (first run).
    """
    p = _state_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("path_denylist read failed: %s", exc)
        return []
    raw_list = data.get("paths") if isinstance(data, dict) else data
    if not isinstance(raw_list, list):
        return []
    out: list[str] = []
    for entry in raw_list:
        if isinstance(entry, str) and entry.strip():
            out.append(entry.strip())
    return out


def _save_user_denylist(paths: Iterable[str]) -> None:
    cleaned = [str(p).strip() for p in paths if isinstance(p, str) and p.strip()]
    # Dedupe while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for p in cleaned:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    p_state = _state_path()
    try:
        p_state.parent.mkdir(parents=True, exist_ok=True)
        p_state.write_text(
            json.dumps(
                {"paths": deduped, "set_at": time.time()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        _log.warning("path_denylist persist failed: %s", exc)


def get_user_denylist() -> list[str]:
    """User-facing list of denied paths (raw, unexpanded form).

    The UI uses this to render the list. Apply ``_expand`` before
    matching.
    """
    return _load_user_denylist()


def add_user_denylist_entry(path: str) -> list[str]:
    """Append a path to the user denylist; returns the new list."""
    current = _load_user_denylist()
    if path and path.strip() and path.strip() not in current:
        current.append(path.strip())
        _save_user_denylist(current)
    return current


def remove_user_denylist_entry(path: str) -> list[str]:
    """Remove a path; returns the new list."""
    current = _load_user_denylist()
    target = (path or "").strip()
    new_list = [p for p in current if p != target]
    if len(new_list) != len(current):
        _save_user_denylist(new_list)
    return new_list


# ── Per-turn / per-role denylist (in-memory) ─────────────────


_TURN_DENYLIST: ContextVar[tuple[str, ...]] = ContextVar(
    "turn_denylist",
    default=(),
)


def push_turn_denylist(extra: Iterable[str]) -> object:
    """Add extra denied paths for the duration of a context.

    Returns a token — pass it to :func:`pop_turn_denylist` to
    restore. Used by the sub-agent bridge to layer per-call
    restrictions on top of the user list (e.g. parent agent says
    "don't touch ``.env``" when dispatching a researcher).
    """
    cleaned = tuple(s.strip() for s in extra if isinstance(s, str) and s.strip())
    return _TURN_DENYLIST.set(_TURN_DENYLIST.get() + cleaned)


def pop_turn_denylist(token: object) -> None:
    try:  # noqa: SIM105
        _TURN_DENYLIST.reset(token)  # type: ignore[arg-type]
    except (LookupError, ValueError):  # noqa: BLE001 — pop is best-effort, ignore if token invalid
        pass


# ── Match decision ────────────────────────────────────────────


def is_blocked(resolved: str | Path) -> tuple[bool, str | None]:
    """Whether a *resolved* (canonical absolute) path is blocked.

    Returns ``(True, prefix)`` when blocked, ``(False, None)``
    otherwise. ``prefix`` is the user-readable form of the matched
    denylist entry so callers can build informative error messages.

    Combines:
      * shipped defaults
      * user-saved denylist
      * current per-turn denylist (ContextVar)
    """
    try:
        target = str(Path(resolved).resolve(strict=False))
    except (OSError, ValueError):
        target = str(resolved)
    target_norm = os.path.normcase(target)

    candidates: list[str] = []
    candidates.extend(_DEFAULT_DENIES)
    candidates.extend(_load_user_denylist())
    candidates.extend(_TURN_DENYLIST.get())

    for raw in candidates:
        expanded = _expand(raw)
        if not expanded:
            continue
        prefix_norm = os.path.normcase(expanded)
        # Exact match or directory-prefix match.
        if target_norm == prefix_norm:
            return True, raw
        # Path component match — append the OS separator so
        # ``/foo/bar`` doesn't match ``/foo/barbecue``.
        if target_norm.startswith(prefix_norm + os.sep):
            return True, raw

    return False, None


__all__ = [
    "add_user_denylist_entry",
    "get_user_denylist",
    "is_blocked",
    "pop_turn_denylist",
    "push_turn_denylist",
    "remove_user_denylist_entry",
]
