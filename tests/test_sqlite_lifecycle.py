"""Regression coverage for short-lived SQLite connection lifecycle."""

from __future__ import annotations

import sqlite3

import pytest

from runtime.platform.io.sqlite import connect_closing


def test_connect_closing_commits_and_closes(tmp_path) -> None:
    path = tmp_path / "lifecycle.sqlite3"
    with connect_closing(path) as conn:
        conn.execute("CREATE TABLE items (value TEXT NOT NULL)")
        conn.execute("INSERT INTO items(value) VALUES (?)", ("ok",))

    with sqlite3.connect(path) as check:
        assert check.execute("SELECT value FROM items").fetchone() == ("ok",)

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


def test_connect_closing_rolls_back_and_closes_on_error(tmp_path) -> None:
    path = tmp_path / "rollback.sqlite3"
    with sqlite3.connect(path) as setup:
        setup.execute("CREATE TABLE items (value TEXT NOT NULL)")

    with pytest.raises(RuntimeError, match="abort"), connect_closing(path) as conn:
        conn.execute("INSERT INTO items(value) VALUES (?)", ("discard",))
        raise RuntimeError("abort")

    with sqlite3.connect(path) as check:
        assert check.execute("SELECT COUNT(*) FROM items").fetchone() == (0,)

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")
