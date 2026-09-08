"""Model identities for named image prototypes, stored in existing settings.

Writers use their caller's transaction. Readers neither initialize the schema
nor load models; path-based reads keep vectors and identities in one snapshot.
"""

from __future__ import annotations

import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

PrototypeKind = Literal["person", "category"]
_PREFIX = "prototype_identity:v1:"
_QUERIES = {
    "person": "SELECT name, prototype, threshold FROM image_people ORDER BY name",
    "category": "SELECT name, prototype FROM image_categories ORDER BY name",
}
_TABLES = {"person": "image_people", "category": "image_categories"}


def _prefix(kind: PrototypeKind) -> str:
    if kind not in ("person", "category"):
        raise ValueError("invalid image prototype kind")
    return f"{_PREFIX}{kind}:"


def _identity(value: str | None) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError("invalid image prototype identity")
    return value or None


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None


def bind_prototype(
    conn: sqlite3.Connection, *, kind: PrototypeKind, name: str, identity: str | None
) -> None:
    """Bind the exact named row; do not commit or alter its stored prototype."""
    prefix = _prefix(kind)
    if not isinstance(name, str):
        raise ValueError("invalid image prototype name")
    identity = _identity(identity)
    conn.execute(
        "INSERT OR REPLACE INTO image_index_settings(key, value) VALUES (?, ?)",
        (prefix + name, identity or ""),
    )


def forget_prototype(conn: sqlite3.Connection, *, kind: PrototypeKind, name: str) -> None:
    """Forget identity bindings for case variants during an explicit rename."""
    prefix = _prefix(kind)
    if not isinstance(name, str):
        raise ValueError("invalid image prototype name")
    if not _has_table(conn, "image_index_settings"):
        return
    rows = conn.execute(
        "SELECT key FROM image_index_settings WHERE substr(key, 1, ?)=?",
        (len(prefix), prefix),
    ).fetchall()
    folded = name.casefold()
    for (key,) in rows:
        if isinstance(key, str) and key[len(prefix) :].casefold() == folded:
            conn.execute("DELETE FROM image_index_settings WHERE key=?", (key,))


def read_prototypes(
    conn: sqlite3.Connection, *, kind: PrototypeKind, identity: str | None
) -> list[tuple[Any, ...]]:
    """Return complete rows only from the expected embedding space.

    Legacy unbound rows are compatible only when the expected identity is also
    unknown. A malformed settings table is an error, never a legacy fallback.
    """
    prefix = _prefix(kind)
    expected = _identity(identity)
    if not _has_table(conn, _TABLES[kind]):
        return []
    rows = conn.execute(_QUERIES[kind]).fetchall()
    if not _has_table(conn, "image_index_settings"):
        return [tuple(row) for row in rows] if expected is None else []
    bindings = dict(
        conn.execute(
            "SELECT key, value FROM image_index_settings WHERE substr(key, 1, ?)=?",
            (len(prefix), prefix),
        ).fetchall()
    )
    result = []
    for row in rows:
        name = row[0]
        if not isinstance(name, str):
            continue
        key = prefix + name
        if key not in bindings:
            if expected is None:
                result.append(tuple(row))
            continue
        bound = bindings[key]
        if isinstance(bound, str) and (bound or None) == expected:
            result.append(tuple(row))
    return result


def face_identity(conn: sqlite3.Connection) -> str | None:
    """Read the identity of the currently committed face-vector snapshot."""
    if not _has_table(conn, "image_index_settings"):
        return None
    row = conn.execute(
        "SELECT value FROM image_index_settings WHERE key='faces_identity'"
    ).fetchone()
    return row[0] if row is not None and isinstance(row[0], str) and row[0] else None


@contextmanager
def _read_snapshot(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    path = Path(db_path).expanduser().absolute()
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or (
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        raise OSError("image index is not a regular file")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        yield conn
    finally:
        conn.close()


def read_face_snapshot(
    db_path: str | Path,
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]] | None:
    """Read faces and compatible people from one SQLite read transaction."""
    try:
        with _read_snapshot(db_path) as conn:
            faces = conn.execute(
                "SELECT path, face_index, face_embedding FROM image_faces ORDER BY path, face_index"
            ).fetchall()
            people = read_prototypes(conn, kind="person", identity=face_identity(conn))
            return [tuple(row) for row in faces], people
    except (OSError, TypeError, ValueError, sqlite3.Error):
        return None


def read_category_prototypes(db_path: str | Path, identity: str | None) -> list[tuple[Any, ...]]:
    """Read category prototypes compatible with the caller's loaded model."""
    try:
        with _read_snapshot(db_path) as conn:
            return read_prototypes(conn, kind="category", identity=identity)
    except (OSError, TypeError, ValueError, sqlite3.Error):
        return []


__all__ = [
    "bind_prototype",
    "face_identity",
    "forget_prototype",
    "read_category_prototypes",
    "read_face_snapshot",
    "read_prototypes",
]
