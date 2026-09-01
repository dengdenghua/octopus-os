from __future__ import annotations

import contextlib
import logging
import re
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("echo.gepa.addendum")


_LEGACY_DIR_NAME = "gepa_addendums"  # pre-rebrand path
_DIR_NAME = "forge_addendums"  # current name (parallels SkillForge)


def _root() -> Path:
    """Project-root-relative dir · single source of truth.

    On first access we migrate the legacy ``data/gepa_addendums/``
    directory to ``data/forge_addendums/`` if it exists and the
    new dir doesn't. Idempotent · subsequent calls skip the
    migration because the old dir no longer exists.
    """
    root = Path("data") / _DIR_NAME
    legacy = Path("data") / _LEGACY_DIR_NAME
    if legacy.is_dir() and not root.exists():
        try:
            legacy.rename(root)
            _LOG.info("migrated %s → %s", legacy, root)
        except OSError as exc:
            _LOG.warning(
                "legacy addendum dir migration failed · %s · falling "
                "back to %s (new writes land here; legacy reads still "
                "work via the legacy path below)",
                exc,
                legacy,
            )
            return legacy
    return root


def _safe_filename(recipe_id: str) -> str:
    """Turn ``llm@a3b7c2d1`` into ``llm_a3b7c2d1`` so Windows is happy.
    Other punctuation is also flattened out · we want filenames that
    survive a `git ls-files` and a `tar | sha256sum` cleanly."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", recipe_id.strip()) or "unknown"


def addendum_path(recipe_id: str) -> Path:
    return _root() / f"{_safe_filename(recipe_id)}.md"


def legacy_global_path() -> Path:
    """Single-file global addendum path · returns the current name
    (``data/forge_planner_addendum.md``). Also auto-migrates the
    old ``data/gepa_planner_addendum.md`` file on first access so
    existing deployments don't lose their applied prompt.
    """
    current = Path("data") / "forge_planner_addendum.md"
    legacy = Path("data") / "gepa_planner_addendum.md"
    if legacy.is_file() and not current.exists():
        try:
            legacy.rename(current)
            _LOG.info("migrated %s → %s", legacy, current)
        except OSError as exc:
            _LOG.warning(
                "legacy global addendum migration failed · %s · falling back to %s",
                exc,
                legacy,
            )
            return legacy
    return current


# ═══════════════════════════════════════════════════════════
# Read · loader called from llm_planner.plan()
# ═══════════════════════════════════════════════════════════


def load_for_recipe(recipe_id: str | None) -> str:
    """Return the per-recipe addendum content as a string · ``""``
    when no file exists OR ``recipe_id`` is None / empty.

    Cheap (one stat + one read) · the planner calls this on every
    plan() turn so we keep it allocation-light. The file is small
    (< 2 KB typical) so OS-cache hits make repeated reads near-free.
    """
    if not recipe_id:
        return ""
    path = addendum_path(recipe_id)
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        _LOG.warning("addendum read failed for %s · %s", recipe_id, exc)
        return ""


def load_global() -> str:
    """Return the legacy global addendum · ``""`` when not present.
    Kept separate from ``load_for_recipe`` so the planner can
    deliberately concatenate both (global first, per-recipe last so
    its instructions take priority by recency in the prompt)."""
    path = legacy_global_path()
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        _LOG.warning("legacy global addendum read failed · %s", exc)
        return ""


# ═══════════════════════════════════════════════════════════
# Write · called from /api/evolution/gepa/apply
# ═══════════════════════════════════════════════════════════


def save_for_recipe(recipe_id: str, content: str) -> Path:
    """Atomic write · tmp file + rename. Caller already added the
    "## GEPA-optimized addendum" header + metadata · we just
    persist the bytes."""
    if not recipe_id:
        raise ValueError("recipe_id is required")
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    target = addendum_path(recipe_id)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target)
        return target
    except OSError:
        # Cleanup tmp on failure · don't leak partial files.
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def delete_for_recipe(recipe_id: str) -> bool:
    """Remove a per-recipe addendum · returns True if a file was
    deleted, False if there was nothing there to begin with."""
    if not recipe_id:
        return False
    target = addendum_path(recipe_id)
    if not target.is_file():
        return False
    try:
        target.unlink()
        return True
    except OSError as exc:
        _LOG.warning("addendum delete failed for %s · %s", recipe_id, exc)
        return False


# ═══════════════════════════════════════════════════════════
# List · for the admin UI's "Addendums" sub-card
# ═══════════════════════════════════════════════════════════


def list_all() -> list[dict[str, Any]]:
    """Return one entry per stored addendum (per-recipe + legacy
    global). Each entry has enough metadata for the panel to render
    a row without further fetches.
    """
    out: list[dict[str, Any]] = []
    # Legacy global first · operators expect to see it at the top
    # since it's the "fallback for everything" entry.
    glob = legacy_global_path()
    if glob.is_file():
        try:
            stat = glob.stat()
            content = glob.read_text(encoding="utf-8")
            out.append(
                {
                    "scope": "global",
                    "recipe_id": None,
                    "path": str(glob),
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "preview": content[:400],
                }
            )
        except OSError:  # noqa: BLE001 — addendum file lock best-effort
            pass
    # Per-recipe entries.
    root = _root()
    if root.is_dir():
        for f in sorted(root.glob("*.md")):
            # Variant files are managed by gepa_variants.py and tracked through
            # a manifest. Listing them as per-recipe addendums makes the UI's
            # rollback button delete the file while leaving a dangling manifest.
            if "__" in f.stem:
                base, _variant = f.stem.split("__", 1)
                if (root / f"{base}_manifest.json").is_file():
                    continue
            try:
                stat = f.stat()
                content = f.read_text(encoding="utf-8")
                # Recipe_id = filename stem with our sanitiser
                # reversed · we lose the original "@" but the panel
                # shows the safe form alongside any header in the
                # file content, so the operator can reconstruct.
                stem = f.stem
                # Convention: we sanitise "llm@hex" → "llm_hex" on
                # write · best-effort reverse for display.
                display_id = stem.replace("_", "@", 1) if "_" in stem else stem
                out.append(
                    {
                        "scope": "per_recipe",
                        "recipe_id": display_id,
                        "path": str(f),
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "preview": content[:400],
                    }
                )
            except OSError:
                continue
    return out


__all__ = [
    "addendum_path",
    "legacy_global_path",
    "load_for_recipe",
    "load_global",
    "save_for_recipe",
    "delete_for_recipe",
    "list_all",
]
