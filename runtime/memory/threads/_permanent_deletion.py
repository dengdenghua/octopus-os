"""Durable deletion claims for ThreadStateStore logical threads."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from runtime.platform.io import atomic_write_json


class ThreadPermanentlyDeletedError(RuntimeError):
    """A normal ThreadState writer targeted a deleting/deleted thread."""

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        super().__init__(f"thread is permanently deleting or deleted: {thread_id}")


@dataclass(frozen=True, slots=True)
class ThreadPermanentDeleteLease:
    thread_id: str
    token: str
    resumed: bool
    finalized: bool
    tenant_id: str = ""
    owner_id: str = ""


def deletion_record_path(
    *,
    journal_path: Path | None,
    per_agent_base: Path | None,
    thread_id: str,
) -> Path | None:
    if per_agent_base is not None:
        root = per_agent_base / "data" / "sessions" / ".thread-deletions"
    elif journal_path is not None:
        root = journal_path.parent / f".{journal_path.name}.thread-deletions"
    else:
        return None
    digest = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
    return root / f"{digest}.json"


def read_deletion_record(path: Path | None) -> ThreadPermanentDeleteLease | None:
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("thread deletion record is unreadable") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("thread deletion record is invalid")
    try:
        return ThreadPermanentDeleteLease(
            thread_id=str(payload["thread_id"]),
            token=str(payload["token"]),
            resumed=True,
            finalized=bool(payload.get("finalized", False)),
            tenant_id=str(payload.get("tenant_id") or ""),
            owner_id=str(payload.get("owner_id") or ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("thread deletion record is invalid") from exc


def write_deletion_record(path: Path | None, lease: ThreadPermanentDeleteLease) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, asdict(lease), indent=2)


def deletion_scope_allowed(
    lease: ThreadPermanentDeleteLease,
    *,
    tenant_id: str,
    owner_id: str,
) -> bool:
    if not tenant_id and not owner_id:
        return True
    return lease.tenant_id == tenant_id and lease.owner_id == owner_id


__all__ = [
    "ThreadPermanentDeleteLease",
    "ThreadPermanentlyDeletedError",
    "deletion_record_path",
    "deletion_scope_allowed",
    "read_deletion_record",
    "write_deletion_record",
]
