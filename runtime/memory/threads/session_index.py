"""Append-only session index for fast thread listing.

An on-disk companion to the per-thread JSONL session files. Without
this index a UI list view has to walk every per-thread file on boot
just to collect titles and timestamps.

Schema · one JSON object per line, append-only:

    {
      "op": "upsert" | "delete",
      "thread_id": str,
      "title": str,
      "status": str,
      "agent_id": str | None,
      "team_id": str | None,
      "created_at": ISO-8601,
      "updated_at": ISO-8601,
      "file": str        # path to per-thread jsonl, repo-relative
    }

A "delete" record carries only ``op`` and ``thread_id``; readers
treat it as a tombstone.

Compaction (offered as an optional helper, not run automatically):
``compact()`` rewrites the file using a snapshot of the current
in-memory state — readers must be paused while it runs, but in
practice that means only "before fork at boot".
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runtime.platform.io import atomic_write_text


@dataclass(frozen=True)
class IndexEntry:
    """One row in ``session_index.jsonl`` (post-fold).

    ``file`` is path-relative to the index's parent directory so the
    index is portable when the project root is moved.
    """

    thread_id: str
    title: str
    status: str
    agent_id: str | None
    team_id: str | None
    created_at: str
    updated_at: str
    file: str
    extra: dict[str, Any] = field(default_factory=dict)

    def to_record(self, *, op: str = "upsert") -> dict[str, Any]:
        rec: dict[str, Any] = {"op": op, "thread_id": self.thread_id}
        if op == "delete":
            return rec
        rec.update(
            title=self.title,
            status=self.status,
            agent_id=self.agent_id,
            team_id=self.team_id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            file=self.file,
        )
        if self.extra:
            rec["extra"] = self.extra
        return rec


def _safe_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def entry_from_thread(
    thread: dict[str, Any],
    *,
    file_path: str,
    extra_keys: tuple[str, ...] = (),
) -> IndexEntry | None:
    """Project a thread dict to its index row.

    Returns ``None`` when the thread is missing the bare-minimum
    fields (no thread_id) — the caller should drop the index update
    in that case.
    """
    thread_id = thread.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        return None
    metadata = thread.get("metadata") or {}
    values = thread.get("values") or {}
    extra = {k: metadata.get(k) for k in extra_keys if metadata.get(k) is not None}
    return IndexEntry(
        thread_id=thread_id,
        title=_safe_str(values.get("title"), ""),
        status=_safe_str(thread.get("status"), "idle"),
        agent_id=_safe_str(metadata.get("agent")) or None,
        team_id=_safe_str(metadata.get("team_id")) or None,
        created_at=_safe_str(thread.get("created_at"), ""),
        updated_at=_safe_str(thread.get("updated_at"), ""),
        file=file_path,
        extra=extra,
    )


class SessionIndex:
    """Append-only on-disk index with a folded in-memory view.

    Each upsert appends a line and refreshes the in-memory map; each
    delete appends a tombstone and removes the entry. Boot replays
    every line in order, applying tombstones, to rebuild the map.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        compaction_threshold: int = 5000,
    ) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._entries: dict[str, IndexEntry] = {}
        # Number of records appended since boot or last compact.
        # Includes tombstones — we use it as a heuristic to decide
        # when the on-disk file's append history dwarfs the live set.
        self._appended_records = 0
        self._compaction_threshold = compaction_threshold
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            tid = rec.get("thread_id")
            if not isinstance(tid, str) or not tid:
                continue
            self._appended_records += 1
            if rec.get("op") == "delete":
                self._entries.pop(tid, None)
                continue
            try:
                self._entries[tid] = IndexEntry(
                    thread_id=tid,
                    title=_safe_str(rec.get("title"), ""),
                    status=_safe_str(rec.get("status"), "idle"),
                    agent_id=rec.get("agent_id"),
                    team_id=rec.get("team_id"),
                    created_at=_safe_str(rec.get("created_at"), ""),
                    updated_at=_safe_str(rec.get("updated_at"), ""),
                    file=_safe_str(rec.get("file"), ""),
                    extra=rec.get("extra") or {},
                )
            except (TypeError, ValueError):
                continue

    def get(self, thread_id: str) -> IndexEntry | None:
        with self._lock:
            return self._entries.get(thread_id)

    def __contains__(self, thread_id: str) -> bool:
        with self._lock:
            return thread_id in self._entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def all(self) -> list[IndexEntry]:
        with self._lock:
            return list(self._entries.values())

    def iter(self) -> Iterator[IndexEntry]:
        # Snapshot so callers can iterate without holding the lock.
        with self._lock:
            snapshot = list(self._entries.values())
        return iter(snapshot)

    def upsert(self, entry: IndexEntry) -> None:
        with self._lock:
            existing = self._entries.get(entry.thread_id)
            # Suppress no-op writes: if every visible field is the
            # same as on disk, don't bloat the file. Keeps the
            # compaction threshold meaningful when the same thread
            # is touched many times with unchanged metadata.
            if existing is not None and existing == entry:
                return
            self._entries[entry.thread_id] = entry
            self._append({"line": entry.to_record(op="upsert")})

    def delete(self, thread_id: str) -> None:
        with self._lock:
            existing = self._entries.pop(thread_id, None)
            if existing is None:
                # Still write tombstone — boot may replay an upsert
                # from an earlier run that this delete must shadow.
                pass
            self._append(
                {
                    "line": {
                        "op": "delete",
                        "thread_id": thread_id,
                    }
                }
            )

    def _append(self, packet: dict[str, Any]) -> None:
        line = packet["line"]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False))
                fh.write("\n")
        except OSError:
            return
        self._appended_records += 1
        if (
            self._compaction_threshold > 0
            and self._appended_records >= self._compaction_threshold
            and self._appended_records > 2 * max(len(self._entries), 1)
        ):
            self._compact_locked()

    def compact(self) -> None:
        """Rewrite the file from the live in-memory set."""
        with self._lock:
            self._compact_locked()

    def _compact_locked(self) -> None:
        # atomic_write_text is durable: tmp → fsync → replace + .bak.
        # Readers will see either the old (full history) or new
        # (snapshot) version, never a partial state.
        snapshot_lines = [
            json.dumps(entry.to_record(op="upsert"), ensure_ascii=False)
            for entry in self._entries.values()
        ]
        atomic_write_text(self._path, "\n".join(snapshot_lines))
        self._appended_records = len(self._entries)
