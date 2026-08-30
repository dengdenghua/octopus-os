"""Cross-process serialization for full-snapshot thread mutations."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO


@dataclass(frozen=True, slots=True)
class PersistedThread:
    found: bool
    thread: dict[str, Any] | None
    revision: int = 0
    source_path: Path | None = None


def iter_jsonl_records_reverse(
    path: Path,
    *,
    block_size: int = 64 * 1024,
) -> Iterator[dict[str, Any]]:
    """Yield valid JSON-object records from *path*, newest first.

    Thread journals contain full snapshots and can become large during a long
    task.  The mutation path only needs the newest durable operation, so
    ``Path.read_text().splitlines()`` needlessly copied the entire journal on
    every update.  Reading fixed-size blocks from the tail keeps that hot path
    bounded by the size of the newest record while preserving tolerant replay
    of blank, partial, or malformed lines.
    """

    if block_size < 1:
        raise ValueError("block_size must be positive")
    try:
        handle = path.open("rb")
    except OSError:
        return
    with handle:
        try:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
        except OSError:
            return
        suffix = b""
        while position > 0:
            read_size = min(block_size, position)
            position -= read_size
            try:
                handle.seek(position)
                chunk = handle.read(read_size)
            except OSError:
                return
            parts = (chunk + suffix).split(b"\n")
            suffix = parts[0]
            for raw_line in reversed(parts[1:]):
                record = _decode_json_object(raw_line)
                if record is not None:
                    yield record
        record = _decode_json_object(suffix)
        if record is not None:
            yield record


def _decode_json_object(raw_line: bytes) -> dict[str, Any] | None:
    raw_line = raw_line.strip()
    if not raw_line:
        return None
    try:
        record = json.loads(raw_line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    return record if isinstance(record, dict) else None


def _lock_root(journal_path: Path | None, per_agent_base: Path | None) -> Path | None:
    if per_agent_base is not None:
        return per_agent_base / "data" / "sessions" / ".thread-mutation-locks"
    if journal_path is not None:
        return journal_path.parent / f".{journal_path.name}.thread-mutation-locks"
    return None


def _lock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(  # type: ignore[attr-defined]
            handle.fileno(),
            msvcrt.LK_LOCK,  # type: ignore[attr-defined]
            1,
        )
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(  # type: ignore[attr-defined]
            handle.fileno(),
            msvcrt.LK_UNLCK,  # type: ignore[attr-defined]
            1,
        )
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def thread_mutation_lock(
    *,
    journal_path: Path | None,
    per_agent_base: Path | None,
    thread_id: str,
) -> Iterator[None]:
    """Hold one authoritative lock for a logical thread across processes."""

    root = _lock_root(journal_path, per_agent_base)
    if root is None:
        yield
        return
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
    path = root / f"{digest}.lock"
    with path.open("a+b") as handle:
        try:
            _lock_file(handle)
        except (ImportError, OSError) as exc:
            raise RuntimeError("thread mutation lock unavailable") from exc
        try:
            yield
        finally:
            _unlock_file(handle)


def _candidate_paths(
    journal_path: Path | None,
    per_agent_base: Path | None,
    thread_id: str,
) -> list[Path]:
    if journal_path is not None:
        return [journal_path] if journal_path.exists() else []
    if per_agent_base is None or not per_agent_base.exists():
        return []
    filename = f"{thread_id}.jsonl"
    roots = [per_agent_base / "data" / "sessions" / "misc"]
    roots.extend(path / "sessions" for path in (per_agent_base / "agents").glob("*"))
    roots.extend(path / "sessions" for path in (per_agent_base / "teams").glob("*"))
    return [
        path
        for root in roots
        if root.exists()
        for path in root.rglob("*.jsonl")
        if path.name == filename
    ]


def latest_persisted_thread(
    *,
    journal_path: Path | None,
    per_agent_base: Path | None,
    thread_id: str,
    source_hint: Path | None = None,
) -> PersistedThread:
    """Read the newest durable operation for ``thread_id`` while its lock is held."""

    if source_hint is not None and source_hint.exists():
        hinted = _latest_candidate_from_path(source_hint, thread_id)
        if hinted is not None:
            revision, _operation_at, _updated_at, thread = hinted
            return PersistedThread(
                found=True,
                thread=thread,
                revision=revision,
                source_path=source_hint,
            )

    candidates: list[tuple[int, str, str, dict[str, Any] | None, Path]] = []
    for path in _candidate_paths(journal_path, per_agent_base, thread_id):
        candidate = _latest_candidate_from_path(path, thread_id)
        if candidate is not None:
            candidates.append((*candidate, path))
    if not candidates:
        return PersistedThread(found=False, thread=None)
    revision, _operation_at, _updated, thread, source_path = max(
        candidates,
        key=lambda item: (item[0], item[1], item[2], str(item[4])),
    )
    return PersistedThread(
        found=True,
        thread=thread,
        revision=revision,
        source_path=source_path,
    )


def _latest_candidate_from_path(
    path: Path,
    thread_id: str,
) -> tuple[int, str, str, dict[str, Any] | None] | None:
    for record in iter_jsonl_records_reverse(path):
        if record.get("thread_id") != thread_id:
            continue
        raw_revision = record.get("revision")
        revision = raw_revision if isinstance(raw_revision, int) and raw_revision >= 0 else 0
        operation_at = str(record.get("operation_at") or "")
        if record.get("op") == "delete":
            return revision, operation_at, "", None
        thread = record.get("thread")
        if isinstance(thread, dict):
            return revision, operation_at, str(thread.get("updated_at") or ""), thread
    return None


def remove_stale_thread_copies(
    *,
    journal_path: Path | None,
    per_agent_base: Path | None,
    thread_id: str,
    keep_path: Path | None,
) -> None:
    """Keep exactly one per-agent journal after a serialized mutation."""

    if per_agent_base is None or keep_path is None:
        return
    for path in _candidate_paths(journal_path, per_agent_base, thread_id):
        if path == keep_path:
            continue
        path.unlink(missing_ok=True)


__all__ = [
    "PersistedThread",
    "iter_jsonl_records_reverse",
    "latest_persisted_thread",
    "remove_stale_thread_copies",
    "thread_mutation_lock",
]
