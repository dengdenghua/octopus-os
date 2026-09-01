"""Factory reset helpers for local runtime state.

This module intentionally clears only generated runtime state. Source-owned
surfaces such as ``agents/``, ``skills/``, ``prompts/``, and the repository
itself are preserved.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FactoryResetResult:
    removed_paths: list[str] = field(default_factory=list)
    skipped_paths: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def perform_factory_reset(
    *,
    project_root: str | Path,
    user_home: str | Path | None = None,
    clear_user_install_state: bool = True,
) -> FactoryResetResult:
    """Remove generated local runtime state.

    ``project_root`` is the repository/workspace root. The function only
    removes a small allow-list under that root plus selected user-level
    Echo metadata. It does not delete source directories.
    """
    root = Path(project_root).expanduser().resolve()
    removed: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for target in _project_runtime_paths(root):
        _remove_path(target, root=root, removed=removed, skipped=skipped, errors=errors)

    if clear_user_install_state:
        home = Path(user_home).expanduser().resolve() if user_home is not None else Path.home()
        install_state = home / ".echo" / "agents-installed.json"
        _remove_user_file(install_state, removed=removed, skipped=skipped, errors=errors)

    return FactoryResetResult(
        removed_paths=removed,
        skipped_paths=skipped,
        errors=errors,
    )


def _project_runtime_paths(root: Path) -> tuple[Path, ...]:
    return (
        root / "data",
        root / ".echo",
        root / ".echo-backend.stdout.log",
        root / ".echo-backend.stderr.log",
        *_nested_session_paths(root / "agents"),
        *_nested_session_paths(root / "teams"),
    )


def _nested_session_paths(container: Path) -> tuple[Path, ...]:
    if not container.exists() or not container.is_dir():
        return ()
    return tuple(
        child / "sessions"
        for child in container.iterdir()
        if child.is_dir() and (child / "sessions").exists()
    )


def _remove_path(
    path: Path,
    *,
    root: Path,
    removed: list[str],
    skipped: list[str],
    errors: list[str],
) -> None:
    try:
        resolved = path.resolve()
    except OSError as exc:
        errors.append(f"{path}: resolve failed: {exc}")
        return
    if not _is_within(resolved, root):
        errors.append(f"{path}: refused outside project root")
        return
    if not resolved.exists():
        skipped.append(str(resolved))
        return
    try:
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink()
        removed.append(str(resolved))
    except OSError as exc:
        errors.append(f"{resolved}: remove failed: {exc}")


def _remove_user_file(
    path: Path,
    *,
    removed: list[str],
    skipped: list[str],
    errors: list[str],
) -> None:
    try:
        resolved = path.resolve()
    except OSError as exc:
        errors.append(f"{path}: resolve failed: {exc}")
        return
    if not resolved.exists():
        skipped.append(str(resolved))
        return
    if not resolved.is_file():
        errors.append(f"{resolved}: expected file")
        return
    try:
        resolved.unlink()
        removed.append(str(resolved))
    except OSError as exc:
        errors.append(f"{resolved}: remove failed: {exc}")


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


__all__ = ["FactoryResetResult", "perform_factory_reset"]
