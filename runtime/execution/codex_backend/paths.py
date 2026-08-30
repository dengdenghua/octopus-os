"""Shared filesystem roots for Codex control and execution planes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from runtime.platform.process.paths import app_paths

from .types import ConfigurationError


def _platform_codex_state_root() -> Path:
    """Return a user-private state root outside a source checkout."""

    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Echo" / "codex_backend"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return base / "Echo" / "codex_backend"
    base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / "echo" / "codex_backend"


def _path_contains(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_codex_state_root(explicit: str | Path | None = None) -> Path:
    """Resolve one state root without creating it.

    The environment override is server-owned and wins over the application
    data default. Control-plane login and execution must call this same helper;
    execution separately rejects overlap with its selected workspace.
    """

    raw = explicit if explicit is not None else os.environ.get("ECHO_CODEX_STATE_DIR")
    if raw is None or not str(raw).strip():
        paths = app_paths()
        root = paths.data_dir / "codex_backend"
        # Source development historically placed runtime data inside the repo.
        # Once that repo is selected as the Coder workspace, exposing the same
        # tree would let the sidecar read its own account/session secrets. The
        # packaged app already supplies an external ECHO_DATA_DIR; only the
        # overlapping fallback needs the platform-owned location.
        project_root = paths.root.resolve(strict=False)
        if _path_contains(project_root, root.resolve(strict=False)):
            root = _platform_codex_state_root()
    else:
        root = Path(str(raw).strip()).expanduser()
        if not root.is_absolute():
            raise ConfigurationError("Codex state root override must be absolute")
    resolved = root.resolve(strict=False)
    if resolved.parent == resolved:
        raise ConfigurationError("filesystem root cannot be Codex state root")
    return resolved


__all__ = ["resolve_codex_state_root"]
