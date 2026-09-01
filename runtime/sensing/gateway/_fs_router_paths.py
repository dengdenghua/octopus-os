"""Path / root-resolution helpers for the filesystem router.

Extracted from ``fs_router.py`` (god-file reduction). Computes the set of
roots under which destructive fs endpoints are allowed and enforces that a
candidate path stays within them (fail-closed to the process-wide roots).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import HTTPException


def _allowed_fs_roots() -> list[Path]:
    """Return the set of roots under which destructive fs endpoints are allowed.

    Policy (union):

    1. ``ECHO_FS_ALLOWED_ROOTS`` env var — colon- (POSIX) or
       semicolon- (Windows) separated absolute paths. Empty entries are
       skipped. Non-existent paths are silently dropped.
    2. ``$ECHO_DATA_DIR`` (if set). The dev runtime stashes
       per-thread workspaces under this root.
    3. ``$ECHO_HOME`` (if set).
    4. Current working directory — covers ``make dev`` / ``pytest``
       from the repo root, where the desktop UI file browser legitimately
       needs to edit the project itself.
    """
    sep = ";" if os.name == "nt" else ":"
    explicit = os.environ.get("ECHO_FS_ALLOWED_ROOTS", "")
    entries: list[Path] = []
    for raw in explicit.split(sep):
        raw = raw.strip()
        if not raw:
            continue
        try:
            p = Path(raw).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if p.is_dir():
            entries.append(p)
    for env_key in ("ECHO_DATA_DIR", "ECHO_HOME"):
        env_value = os.environ.get(env_key)
        if env_value:
            try:
                p = Path(env_value).expanduser().resolve()
                if p.is_dir():
                    entries.append(p)
            except (OSError, RuntimeError):  # noqa: BLE001 — fs entry inaccessible; skip
                pass
    try:
        from runtime.platform.process.paths import project_root

        entries.append(project_root())
    except (ImportError, OSError, RuntimeError):  # noqa: BLE001
        entries.append(Path.cwd().resolve())
    # de-dupe while preserving order
    seen: set[Path] = set()
    out: list[Path] = []
    for p in entries:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _safe_relative_parts(value: str) -> list[str]:
    parts: list[str] = []
    for raw_part in re.split(r"[\\/]+", value):
        part = raw_part.strip()
        if not part or part in {".", ".."}:
            continue
        cleaned = re.sub(r'[<>:"|?*\x00-\x1f]', "_", part)
        if cleaned:
            parts.append(cleaned[:160])
    return parts


def _assert_within_allowed_roots(candidate: Path) -> Path:
    """Raise HTTPException(403) if ``candidate`` falls outside the
    allowed roots.

    Always resolves symlinks so ``/allowed/link`` → ``/etc/shadow``
    cannot slip past the prefix check.
    """
    try:
        resolved = candidate.expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(400, f"invalid path: {exc}") from exc
    roots = _allowed_fs_roots()
    for root in roots:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return resolved
    raise HTTPException(
        403,
        f"path {resolved} is outside allowed fs roots "
        f"({', '.join(str(r) for r in roots)}); "
        "set ECHO_FS_ALLOWED_ROOTS to grant access",
    )
