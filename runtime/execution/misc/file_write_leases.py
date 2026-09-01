from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_LEASE_METADATA_KEY = "_file_write_leases"
_HANDOFF_METADATA_KEY = "_file_write_lease_handoffs"
_HISTORY_METADATA_KEY = "_file_write_lease_history"
_SNAPSHOT_METADATA_KEY = "_file_read_snapshots"
_LOCK = threading.RLock()


class FileWriteLeaseConflict(PermissionError):
    """Raised when another owner already controls a file for this turn."""


class WorkspaceContentDriftConflict(PermissionError):
    """Raised when a file changed after the agent inspected it."""


@dataclass(frozen=True)
class FileWriteLease:
    path: str
    owner: str
    acquired_at: float
    reentrant: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "owner": self.owner,
            "acquired_at": self.acquired_at,
            "reentrant": self.reentrant,
        }


def acquire_file_write_lease(
    session: Any,
    path: str | Path | None,
    *,
    owner: str,
) -> FileWriteLease | None:
    """Acquire a per-turn file write lease.

    The lease table lives in ``Session.metadata`` so it naturally expires
    with the turn. Same-owner writes are reentrant; cross-owner writes to
    the same canonical path fail before touching the filesystem.
    """

    if session is None or path is None:
        return None
    metadata = getattr(session, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    path_key = canonical_write_path_key(path)
    if not path_key:
        return None
    clean_owner = str(owner or "unknown").strip() or "unknown"

    with _LOCK:
        leases = metadata.get(_LEASE_METADATA_KEY)
        if not isinstance(leases, dict):
            leases = {}
            metadata[_LEASE_METADATA_KEY] = leases
        existing = leases.get(path_key)
        if isinstance(existing, dict):
            existing_owner = str(existing.get("owner") or "")
            if existing_owner and existing_owner != clean_owner:
                if _consume_handoff(
                    metadata,
                    path_key,
                    from_owner=existing_owner,
                    to_owner=clean_owner,
                ):
                    _append_history(
                        metadata,
                        event="handoff_consumed",
                        path=path_key,
                        owner=clean_owner,
                        from_owner=existing_owner,
                        to_owner=clean_owner,
                    )
                    lease = FileWriteLease(
                        path=path_key,
                        owner=clean_owner,
                        acquired_at=time.time(),
                    )
                    leases[path_key] = lease.to_dict()
                    return lease
                _append_history(
                    metadata,
                    event="conflict",
                    path=path_key,
                    owner=existing_owner,
                    requester=clean_owner,
                )
                raise FileWriteLeaseConflict(
                    "file_write_lease_conflict:"
                    f"{path_key}:owner={existing_owner}:requester={clean_owner}"
                )
            lease = FileWriteLease(
                path=path_key,
                owner=clean_owner,
                acquired_at=float(existing.get("acquired_at") or time.time()),
                reentrant=True,
            )
            leases[path_key] = lease.to_dict()
            _append_history(
                metadata,
                event="reentrant",
                path=path_key,
                owner=clean_owner,
            )
            return lease

        lease = FileWriteLease(
            path=path_key,
            owner=clean_owner,
            acquired_at=time.time(),
        )
        leases[path_key] = lease.to_dict()
        _append_history(
            metadata,
            event="acquire",
            path=path_key,
            owner=clean_owner,
        )
        return lease


def record_file_read_snapshot(
    session: Any,
    path: str | Path | None,
) -> dict[str, Any] | None:
    """Remember the exact file content observed by a successful read.

    The snapshot becomes an optimistic-concurrency token for later writes in
    the same turn.  It complements owner leases: leases stop two Echo
    agents from writing simultaneously, while this token detects edits made
    by an IDE, hook, script, or other process outside the lease table.
    """

    return _record_content_snapshot(session, path, event="read_snapshot")


def record_file_write_snapshot(
    session: Any,
    path: str | Path | None,
) -> dict[str, Any] | None:
    """Advance the optimistic-concurrency token after a successful write."""

    return _record_content_snapshot(session, path, event="write_snapshot")


def verify_file_unchanged_since_read(
    session: Any,
    path: str | Path | None,
) -> bool:
    """Reject a write when current content differs from the last snapshot.

    Missing snapshots remain compatible with internal/legacy callers.  The
    executor's read-before-write guard ensures normal writes to existing
    files have a snapshot; new files legitimately have none.
    """

    if session is None or path is None:
        return True
    metadata = getattr(session, "metadata", None)
    if not isinstance(metadata, dict):
        return True
    path_key = canonical_write_path_key(path)
    snapshots = metadata.get(_SNAPSHOT_METADATA_KEY)
    if not path_key or not isinstance(snapshots, dict):
        return True
    expected = snapshots.get(path_key)
    if not isinstance(expected, dict):
        return True

    current = _content_fingerprint(path)
    if _same_content_fingerprint(expected, current):
        return True

    with _LOCK:
        _append_history(
            metadata,
            event="external_drift",
            path=path_key,
            owner=str(expected.get("owner") or "workspace"),
            expected_sha256=expected.get("sha256"),
            current_sha256=current.get("sha256"),
            expected_exists=expected.get("exists"),
            current_exists=current.get("exists"),
        )
    raise WorkspaceContentDriftConflict(
        f"workspace_content_drift:{path_key}:content changed after read; re-read before writing"
    )


def canonical_write_path_key(path: str | Path) -> str:
    raw = Path(path).expanduser()
    try:
        resolved = raw.resolve(strict=False)
    except OSError:
        resolved = raw.absolute()
    return str(resolved).casefold()


def authorize_file_write_handoff(
    session: Any,
    path: str | Path,
    *,
    from_owner: str,
    to_owner: str,
) -> bool:
    if session is None:
        return False
    metadata = getattr(session, "metadata", None)
    if not isinstance(metadata, dict):
        return False
    path_key = canonical_write_path_key(path)
    source = str(from_owner or "").strip()
    target = str(to_owner or "").strip()
    if not path_key or not source or not target:
        return False
    with _LOCK:
        handoffs = metadata.get(_HANDOFF_METADATA_KEY)
        if not isinstance(handoffs, dict):
            handoffs = {}
            metadata[_HANDOFF_METADATA_KEY] = handoffs
        handoffs[path_key] = {
            "from_owner": source,
            "to_owner": target,
            "authorized_at": time.time(),
        }
        _append_history(
            metadata,
            event="handoff_authorized",
            path=path_key,
            owner=source,
            from_owner=source,
            to_owner=target,
        )
    return True


def release_file_write_lease(
    session: Any,
    path: str | Path,
    *,
    owner: str,
) -> bool:
    if session is None:
        return False
    metadata = getattr(session, "metadata", None)
    if not isinstance(metadata, dict):
        return False
    path_key = canonical_write_path_key(path)
    clean_owner = str(owner or "").strip()
    if not path_key or not clean_owner:
        return False
    with _LOCK:
        leases = metadata.get(_LEASE_METADATA_KEY)
        if not isinstance(leases, dict):
            return False
        existing = leases.get(path_key)
        if not isinstance(existing, dict):
            return False
        if str(existing.get("owner") or "") != clean_owner:
            return False
        leases.pop(path_key, None)
        _append_history(
            metadata,
            event="release",
            path=path_key,
            owner=clean_owner,
        )
    return True


def file_write_lease_snapshot(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Return a JSON-safe snapshot of file write leases for observability."""

    if not isinstance(metadata, dict):
        metadata = {}
    leases_raw = metadata.get(_LEASE_METADATA_KEY)
    handoffs_raw = metadata.get(_HANDOFF_METADATA_KEY)
    history_raw = metadata.get(_HISTORY_METADATA_KEY)
    snapshots_raw = metadata.get(_SNAPSHOT_METADATA_KEY)
    leases = _dict_values(leases_raw)
    handoffs = _dict_values(handoffs_raw)
    history = [
        dict(item)
        for item in (history_raw if isinstance(history_raw, list) else [])
        if isinstance(item, dict)
    ]
    return {
        "lease_count": len(leases),
        "pending_handoff_count": len(handoffs),
        "handoff_count": sum(
            1 for item in history if str(item.get("event") or "").startswith("handoff_")
        ),
        "conflict_count": sum(1 for item in history if item.get("event") == "conflict"),
        "external_drift_count": sum(1 for item in history if item.get("event") == "external_drift"),
        "content_snapshot_count": len(_dict_values(snapshots_raw)),
        "leases": leases,
        "pending_handoffs": handoffs,
        "history": history,
    }


def _record_content_snapshot(
    session: Any,
    path: str | Path | None,
    *,
    event: str,
) -> dict[str, Any] | None:
    if session is None or path is None:
        return None
    metadata = getattr(session, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    path_key = canonical_write_path_key(path)
    if not path_key:
        return None
    fingerprint = _content_fingerprint(path)
    fingerprint["path"] = path_key
    fingerprint["captured_at"] = time.time()
    with _LOCK:
        snapshots = metadata.get(_SNAPSHOT_METADATA_KEY)
        if not isinstance(snapshots, dict):
            snapshots = {}
            metadata[_SNAPSHOT_METADATA_KEY] = snapshots
        snapshots[path_key] = fingerprint
        _append_history(
            metadata,
            event=event,
            path=path_key,
            owner="workspace",
            sha256=fingerprint.get("sha256"),
            exists=fingerprint.get("exists"),
        )
    return dict(fingerprint)


def _content_fingerprint(path: str | Path) -> dict[str, Any]:
    target = Path(path).expanduser()
    try:
        stat = target.stat()
    except FileNotFoundError:
        return {"exists": False, "kind": "missing", "size": 0, "sha256": None}
    except OSError as exc:
        return {
            "exists": True,
            "kind": "unreadable",
            "size": None,
            "sha256": None,
            "error": type(exc).__name__,
        }
    if not target.is_file():
        return {
            "exists": True,
            "kind": "non_file",
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": None,
        }
    digest = hashlib.sha256()
    try:
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        final_stat = target.stat()
    except OSError as exc:
        return {
            "exists": True,
            "kind": "unreadable",
            "size": stat.st_size,
            "sha256": None,
            "error": type(exc).__name__,
        }
    return {
        "exists": True,
        "kind": "file",
        "size": final_stat.st_size,
        "mtime_ns": final_stat.st_mtime_ns,
        "sha256": digest.hexdigest(),
        "stable": (stat.st_size, stat.st_mtime_ns) == (final_stat.st_size, final_stat.st_mtime_ns),
    }


def _same_content_fingerprint(
    expected: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    comparable = ("exists", "kind", "size", "sha256")
    return (
        bool(expected.get("stable", True))
        and bool(current.get("stable", True))
        and all(expected.get(key) == current.get(key) for key in comparable)
    )


def _consume_handoff(
    metadata: dict[str, Any],
    path_key: str,
    *,
    from_owner: str,
    to_owner: str,
) -> bool:
    handoffs = metadata.get(_HANDOFF_METADATA_KEY)
    if not isinstance(handoffs, dict):
        return False
    handoff = handoffs.get(path_key)
    if not isinstance(handoff, dict):
        return False
    if (
        str(handoff.get("from_owner") or "") != from_owner
        or str(handoff.get("to_owner") or "") != to_owner
    ):
        return False
    handoffs.pop(path_key, None)
    return True


def _append_history(
    metadata: dict[str, Any],
    *,
    event: str,
    path: str,
    owner: str,
    **extra: Any,
) -> None:
    history = metadata.get(_HISTORY_METADATA_KEY)
    if not isinstance(history, list):
        history = []
        metadata[_HISTORY_METADATA_KEY] = history
    item: dict[str, Any] = {
        "event": event,
        "path": path,
        "owner": owner,
        "at": time.time(),
    }
    item.update({key: value for key, value in extra.items() if value is not None and value != ""})
    history.append(item)


def _dict_values(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    return [dict(item) for item in value.values() if isinstance(item, dict)]


__all__ = [
    "FileWriteLease",
    "FileWriteLeaseConflict",
    "WorkspaceContentDriftConflict",
    "acquire_file_write_lease",
    "authorize_file_write_handoff",
    "canonical_write_path_key",
    "file_write_lease_snapshot",
    "record_file_read_snapshot",
    "record_file_write_snapshot",
    "release_file_write_lease",
    "verify_file_unchanged_since_read",
]
