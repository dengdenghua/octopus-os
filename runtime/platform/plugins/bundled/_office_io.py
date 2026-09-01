"""Crash-safe file writes shared by first-party Office plugins."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def atomic_package_save(target: Path, save: Callable[[Path], None]) -> None:
    """Save a package/PDF beside *target* and atomically replace it.

    OOXML libraries write ZIP containers incrementally. Writing directly to
    the live artifact can leave a corrupt file if the process exits halfway.
    A same-directory temporary keeps ``os.replace`` on one filesystem.
    """

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_raw = tempfile.mkstemp(
        prefix=f".{target.stem}-",
        suffix=target.suffix,
        dir=target.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_raw)
    try:
        save(temporary)
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise OSError("save produced an empty artifact")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def create_versioned_backup(path: Path) -> Path:
    """Copy *path* to a unique adjacent backup without overwriting history."""

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    candidate = path.with_name(f"{path.name}.{stamp}.bak")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.{stamp}.{counter}.bak")
        counter += 1
    shutil.copy2(path, candidate)
    return candidate


def replace_text_preserving_runs(
    paragraph: Any,
    replacements: list[tuple[str, str]],
    counts: list[int],
) -> bool:
    """Replace paragraph text while retaining unaffected run formatting.

    OOXML commonly splits one visible sentence across multiple runs for bold,
    links, colours, language changes, and revision boundaries. Reassigning
    ``paragraph.text`` flattens those runs. This helper mutates only the runs
    intersecting each exact match; text outside the match keeps its original
    run and formatting.
    """

    runs = list(getattr(paragraph, "runs", ()) or ())
    if not runs:
        original = str(getattr(paragraph, "text", "") or "")
        updated = original
        for index, (old, new) in enumerate(replacements):
            found = updated.count(old)
            if found:
                updated = updated.replace(old, new)
                counts[index] += found
        if updated == original:
            return False
        paragraph.text = updated
        return True

    changed = False
    for replacement_index, (old, new) in enumerate(replacements):
        search_from = 0
        while True:
            current = "".join(str(run.text or "") for run in runs)
            start = current.find(old, search_from)
            if start < 0:
                break
            end = start + len(old)
            offsets: list[tuple[int, int]] = []
            cursor = 0
            for run in runs:
                next_cursor = cursor + len(str(run.text or ""))
                offsets.append((cursor, next_cursor))
                cursor = next_cursor
            first = next(index for index, (_begin, finish) in enumerate(offsets) if start < finish)
            last = next(
                index for index, (begin, finish) in enumerate(offsets) if begin < end <= finish
            )
            first_begin, _first_end = offsets[first]
            last_begin, _last_end = offsets[last]
            first_text = str(runs[first].text or "")
            last_text = str(runs[last].text or "")
            prefix = first_text[: start - first_begin]
            suffix = last_text[end - last_begin :]
            if first == last:
                runs[first].text = prefix + new + suffix
            else:
                runs[first].text = prefix + new
                for run_index in range(first + 1, last):
                    runs[run_index].text = ""
                runs[last].text = suffix
            counts[replacement_index] += 1
            changed = True
            search_from = start + len(new)
    return changed


def scoped_path_denial(path: Path, *, write: bool) -> str | None:
    """Return a denial reason when an Office plugin path escapes its turn scope."""

    if write:
        try:
            from runtime.safety.auth import check_file_write

            verdict = check_file_write(path)
            if not verdict.allow:
                return str(verdict.reason or "file safety denied the target")
        except (ImportError, AttributeError):
            pass
    try:
        from runtime.platform.process.scope import resolve_execution_scope
        from runtime.platform.process.session import current_session
    except ImportError:
        return None
    try:
        session = current_session()
    except (AttributeError, RuntimeError) as exc:
        return f"cannot resolve the active execution session: {exc}"
    if session is None:
        return None
    try:
        scope = resolve_execution_scope(session)
        allowed = scope.allows_write(path) if write else scope.allows_read(path)
    except (AttributeError, RuntimeError) as exc:
        return f"cannot verify the active workspace scope: {exc}"
    if allowed:
        return None
    mode = "write" if write else "read"
    return f"{mode} path escapes the active workspace scope: {path}"


__all__ = [
    "atomic_package_save",
    "create_versioned_backup",
    "replace_text_preserving_runs",
    "scoped_path_denial",
]
