"""Pin authorized organization directories without rewriting their permissions.

Creation inherits the parent filesystem's normal permissions. Linux uses pinned
directory descriptors, Windows holds non-delete-shared ancestor handles. These
guards reject links; they do not prevent every same-user namespace mutation on
Linux. Callers keep their selected directory pinned across file operations and
recheck authorization at the operation boundary.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from appliance.files.organization_io import OrganizationIOError, _parents, _parts, _root


def _relative_directory(relative: str) -> tuple[str, ...]:
    return () if relative == "" else _parts(relative)


def _identity(info: os.stat_result) -> dict[str, int]:
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise OrganizationIOError("not_regular_directory")
    return {"dev": info.st_dev, "ino": info.st_ino}


def _check_identity(actual: dict[str, int], expected: dict[str, Any] | None) -> None:
    if expected is not None and (
        not isinstance(expected, dict)
        or any(expected.get(key) != actual[key] for key in ("dev", "ino"))
    ):
        raise OrganizationIOError("directory_identity_changed")


@contextmanager
def pinned_directory(
    root: Path, relative: str = "", *, expected: dict[str, Any] | None = None
) -> Iterator[dict[str, Any]]:
    """Yield ``{path: Path, identity: {dev, ino}}`` for one existing directory."""
    root = _root(root)
    parts = _relative_directory(relative)
    path = root.joinpath(*parts)
    sentinel = "/".join((*parts, ".organization-directory-sentinel"))
    with _parents(root, (sentinel,)) as parents:
        info = path.lstat() if os.name == "nt" else os.fstat(parents[str(path)])
        identity = _identity(info)
        _check_identity(identity, expected)
        yield {"path": path, "identity": identity}


def require_directory(root: Path, relative: str = "") -> dict[str, Any]:
    """Read a strict no-link identity snapshot; this does not retain the pin."""
    with pinned_directory(root, relative) as snapshot:
        return snapshot


def ensure_parent_directories(
    root: Path,
    relative_file: str,
    *,
    authorize: Callable[[str], None],
    expected_root: dict[str, Any] | None = None,
) -> list[str]:
    """Create only missing parents, authorizing each NAS-relative directory.

    Return newly created directory names. On failure, ``created_directories``
    on the exception reports any preceding creations; they are never removed
    blindly because another actor may already be using them.
    """
    root = _root(root)
    parts = _parts(relative_file)[:-1]
    if not callable(authorize):
        raise TypeError("directory authorization callback is required")
    created: list[str] = []
    try:
        with ExitStack() as stack:
            stack.enter_context(pinned_directory(root, expected=expected_root))
            current = root
            for index, part in enumerate(parts):
                relative = "/".join(parts[: index + 1])
                authorize(relative)
                # Re-open the parent through its no-link chain before each mkdir.
                sentinel = "/".join((*parts[:index], ".organization-directory-sentinel"))
                with _parents(root, (sentinel,)) as parents:
                    if os.name == "nt":
                        try:
                            (current / part).mkdir()
                            created.append(relative)
                        except FileExistsError:
                            pass
                    else:
                        try:
                            os.mkdir(part, dir_fd=parents[str(current)])
                            created.append(relative)
                        except FileExistsError:
                            pass
                    # Keep each child pinned for the rest of this operation.
                    child = stack.enter_context(pinned_directory(root, relative))
                current = child["path"]
        return created
    except BaseException as exc:
        exc.created_directories = list(created)
        raise


__all__ = ["ensure_parent_directories", "pinned_directory", "require_directory"]
