"""Bounded commit receipts for the local image index.

The receipt is written by the same transaction as its vectors. A worker journal
alone cannot tell whether a process died before or after SQLite committed.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

RECEIPT_KEY = "last_job_receipt"


def job_identity(job_id: str | None, plan_id: str | None) -> bool:
    return (
        isinstance(job_id, str)
        and re.fullmatch(r"[0-9a-f]{24}", job_id) is not None
        and isinstance(plan_id, str)
        and re.fullmatch(r"[0-9a-f]{64}", plan_id) is not None
    )


def write_receipt(
    conn: sqlite3.Connection,
    *,
    root: Path,
    job_id: str | None,
    plan_id: str | None,
    include_faces: bool,
    result: dict[str, Any],
) -> None:
    # Every committed rebuild supersedes the previous snapshot, even when a
    # generic image tool did not supply an appliance job identity.
    conn.execute("DELETE FROM image_index_settings WHERE key=?", (RECEIPT_KEY,))
    if job_identity(job_id, plan_id):
        receipt = {
            "schema": "echo.photos.index-commit.v1",
            "root": str(root.resolve()),
            "jobId": job_id,
            "planId": plan_id,
            "includeFaces": include_faces,
            "result": result,
        }
        conn.execute(
            "INSERT INTO image_index_settings VALUES (?, ?)",
            (RECEIPT_KEY, json.dumps(receipt, separators=(",", ":"))),
        )


def index_job_receipt(
    root: str | Path,
    *,
    db_path: str | Path,
    job_id: str,
    plan_id: str,
    include_faces: bool,
) -> dict[str, Any] | None:
    """Read only the currently committed snapshot's exact matching receipt.

    No schema creation, model initialization, or journal mutation occurs here.
    Missing/legacy/corrupt receipts are not evidence of successful completion.
    """
    if not job_identity(job_id, plan_id) or type(include_faces) is not bool:
        return None
    path = Path(db_path)
    if path.is_symlink() or not path.is_file():
        return None
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM image_index_settings WHERE key=?", (RECEIPT_KEY,)
            ).fetchone()
            if row is None or not isinstance(row[0], str) or len(row[0]) > 4096:
                return None
            receipt = json.loads(row[0])
            root_row = conn.execute(
                "SELECT value FROM image_index_settings WHERE key='root'"
            ).fetchone()
        finally:
            conn.close()
        if not isinstance(receipt, dict) or (
            receipt.get("schema") != "echo.photos.index-commit.v1"
            or receipt.get("root") != str(Path(root).resolve())
            or root_row != (receipt.get("root"),)
            or receipt.get("jobId") != job_id
            or receipt.get("planId") != plan_id
            or receipt.get("includeFaces") is not include_faces
        ):
            return None
        result = receipt.get("result")
        if not isinstance(result, dict) or result.get("ok") is not True:
            return None
        public: dict[str, Any] = {}
        for name in ("indexed", "faces", "reused", "embedded", "removed", "skipped"):
            count = result.get(name)
            if type(count) is not int or not 0 <= count <= 1_000_000:
                return None
            public[name] = count
        for name in ("ok", "semantic", "face_capable"):
            value = result.get(name)
            if type(value) is not bool:
                return None
            public[name] = value
        return public
    except (OSError, ValueError, sqlite3.Error):
        return None
