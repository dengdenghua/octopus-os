"""SQLite lifecycle helpers for short-lived application operations.

``sqlite3.Connection`` commits or rolls back when used as a context manager,
but it does not close the underlying handle. That distinction is easy to miss
in request-scoped stores and can accumulate file descriptors over a long-lived
appliance process. ``connect_closing`` keeps normal transaction semantics and
closes the handle on every exit path.
"""

from __future__ import annotations

import sqlite3
from os import PathLike
from typing import Any


class ClosingConnection(sqlite3.Connection):
    """Commit or roll back, then close when leaving a ``with`` block."""

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()


def connect_closing(
    database: str | bytes | PathLike[str],
    *args: Any,
    **kwargs: Any,
) -> ClosingConnection:
    """Open a connection that closes after its transaction context exits."""

    kwargs["factory"] = ClosingConnection
    return sqlite3.connect(database, *args, **kwargs)


__all__ = ["ClosingConnection", "connect_closing"]
