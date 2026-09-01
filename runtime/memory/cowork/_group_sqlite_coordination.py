"""SQLite journal invariants for atomic Group lifecycle transactions."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path

_DRAIN_MESSAGE = "group storage journal migration requires draining older WAL workers"


def migrate_delete_journals(paths: Iterable[Path]) -> None:
    """Migrate closed legacy WAL databases; fail closed while old workers live."""

    for path in paths:
        if not path.exists():
            continue
        try:
            with closing(sqlite3.connect(str(path), timeout=0.25)) as conn:
                conn.execute("PRAGMA busy_timeout=250")
                checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint is not None and int(checkpoint[0] or 0) != 0:
                    raise RuntimeError(_DRAIN_MESSAGE)
                mode = conn.execute("PRAGMA journal_mode=DELETE").fetchone()
                conn.execute("PRAGMA synchronous=FULL")
        except RuntimeError:
            raise
        except sqlite3.Error as exc:
            raise RuntimeError(_DRAIN_MESSAGE) from exc
        if mode is None or str(mode[0]).lower() != "delete":
            raise RuntimeError(_DRAIN_MESSAGE)


def require_delete_journals(conn: sqlite3.Connection, schemas: Iterable[str]) -> None:
    """Verify every participant immediately before a multi-DB transaction."""

    for schema in schemas:
        if not schema.replace("_", "").isalnum():
            raise ValueError(f"invalid SQLite schema name: {schema!r}")
        try:
            row = conn.execute(  # nosec B608 - validated schema identifier
                f"PRAGMA {schema}.journal_mode=DELETE"
            ).fetchone()
        except sqlite3.Error as exc:
            raise RuntimeError(_DRAIN_MESSAGE) from exc
        if row is None or str(row[0]).lower() != "delete":
            raise RuntimeError(_DRAIN_MESSAGE)
        conn.execute(f"PRAGMA {schema}.synchronous=FULL")  # nosec B608 - validated
        synchronous = conn.execute(f"PRAGMA {schema}.synchronous").fetchone()  # nosec B608
        if synchronous is None or int(synchronous[0]) != 2:
            raise RuntimeError("group storage requires FULL SQLite synchronization")


__all__ = ["migrate_delete_journals", "require_delete_journals"]
