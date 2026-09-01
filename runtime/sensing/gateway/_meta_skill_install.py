"""Skill install / uninstall filesystem helpers for the meta router.

Extracted from ``meta_router.py`` in the god-file split campaign. These
helpers own the ``skills/public`` directory mutation used by the
``/api/skills/install`` and ``/api/skills/{name}/uninstall`` endpoints.
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

_SAFE_SKILL_INSTALL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _require_safe_skill_install_name(value: str, *, label: str = "skill name") -> str:
    name = str(value or "").strip()
    if not _SAFE_SKILL_INSTALL_NAME_RE.fullmatch(name):
        raise ValueError(
            f"invalid {label}: only alphanumeric characters, hyphens, and underscores are allowed"
        )
    return name


def _ensure_real_directory(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} must be a real directory: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"{label} must be a directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{label} must be a real directory: {path}")
    return path


def _contains_symlink(directory: Path) -> bool:
    return any(child.is_symlink() for child in directory.rglob("*"))


def _install_public_skill_dir(skill_root: Path, skill_name: str) -> Path:
    name = _require_safe_skill_install_name(skill_name)
    if skill_root.is_symlink() or not skill_root.is_dir():
        raise ValueError(f"skill source must be a real directory: {skill_root}")
    if _contains_symlink(skill_root):
        raise ValueError(f"skill source must not contain symlinks: {skill_root}")
    if not (skill_root / "SKILL.md").is_file():
        raise FileNotFoundError(f"skill source missing SKILL.md: {skill_root}")

    skills_root = _ensure_real_directory(Path("skills/public"), "skills root")
    target = skills_root / name
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise FileExistsError(f"skill target is not a real directory: {name}")

    stage_parent: Path | None = None
    backup = skills_root / f".{name}.backup-{time.time_ns()}"
    backup_created = False
    try:
        import tempfile

        with tempfile.TemporaryDirectory(prefix=f".{name}.staged-", dir=skills_root) as tmpdir:
            stage_parent = Path(tmpdir)
            staged = stage_parent / name
            shutil.copytree(skill_root, staged)
            if target.exists():
                target.rename(backup)
                backup_created = True
            try:
                staged.rename(target)
            except OSError:
                if backup_created and backup.exists() and not target.exists():
                    backup.rename(target)
                raise
    finally:
        if backup_created and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        if stage_parent is not None and stage_parent.exists():
            shutil.rmtree(stage_parent, ignore_errors=True)
    return target


def _uninstall_public_skill_dir(skill_name: str) -> Path:
    name = _require_safe_skill_install_name(skill_name)
    skills_root = _ensure_real_directory(Path("skills/public"), "skills root")
    target = skills_root / name
    if target.is_symlink():
        raise FileExistsError(f"skill target is not a real directory: {name}")
    if not target.exists():
        raise FileNotFoundError(f"skill directory not found: {name}")
    if not target.is_dir():
        raise NotADirectoryError(f"not a directory: {name}")
    shutil.rmtree(target)
    return target
