from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any


@dataclass(frozen=True)
class FileTransactionSummary:
    operation_count: int = 0
    path_count: int = 0
    created: int = 0
    written: int = 0
    edited: int = 0
    deleted: int = 0
    renamed: int = 0
    bytes_delta: int = 0
    paths: tuple[str, ...] = field(default_factory=tuple)
    risky_paths: tuple[str, ...] = field(default_factory=tuple)
    contested_paths: tuple[str, ...] = field(default_factory=tuple)
    large_diff_paths: tuple[str, ...] = field(default_factory=tuple)
    reversible: int = 0
    rollback_unavailable: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_count": self.operation_count,
            "path_count": self.path_count,
            "created": self.created,
            "written": self.written,
            "edited": self.edited,
            "deleted": self.deleted,
            "renamed": self.renamed,
            "bytes_delta": self.bytes_delta,
            "paths": list(self.paths),
            "risky_paths": list(self.risky_paths),
            "contested_paths": list(self.contested_paths),
            "large_diff_paths": list(self.large_diff_paths),
            "reversible": self.reversible,
            "rollback_unavailable": self.rollback_unavailable,
        }


@dataclass(frozen=True)
class FileRollbackEntry:
    path: str
    action: str
    expected_current_sha256: str = ""
    content: str | None = None
    source_event_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "action": self.action,
            "expected_current_sha256": self.expected_current_sha256,
            "content": self.content,
            "source_event_id": self.source_event_id,
        }


@dataclass(frozen=True)
class FileRollbackResult:
    applied: int = 0
    skipped: int = 0
    failed: int = 0
    entries: tuple[FileRollbackEntry, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "skipped": self.skipped,
            "failed": self.failed,
            "entries": [entry.to_dict() for entry in self.entries],
            "errors": list(self.errors),
        }


def summarize_file_ops(events: list[Any]) -> FileTransactionSummary:
    counts = defaultdict(int)
    paths: list[str] = []
    actors_by_path: dict[str, set[str]] = defaultdict(set)
    risky: list[str] = []
    large_diffs: list[str] = []
    bytes_delta = 0
    reversible = 0
    rollback_unavailable = 0

    for event in events:
        if getattr(event, "event_type", None) != "file_op":
            continue
        path = str(getattr(event, "path", "") or "")
        if not path:
            continue
        action = str(getattr(event, "action", "") or "write")
        counts[action] += 1
        if path not in paths:
            paths.append(path)
        actor = (
            str(getattr(event, "actor", "") or "")
            or str(getattr(event, "agent_id", "") or "")
            or str(getattr(event, "arm_id", "") or "")
            or "unknown"
        )
        actors_by_path[path].add(actor)
        bytes_delta += int(getattr(event, "bytes_delta", 0) or 0)
        if _is_risky_path(path) and path not in risky:
            risky.append(path)
        diff = getattr(event, "diff", None)
        if isinstance(diff, str) and len(diff) > 20_000 and path not in large_diffs:
            large_diffs.append(path)
        rollback = getattr(event, "rollback", None)
        if isinstance(rollback, dict) and rollback.get("reversible") is True:
            reversible += 1
        elif rollback is not None:
            rollback_unavailable += 1

    contested = [
        path
        for path, actors in actors_by_path.items()
        if len({actor for actor in actors if actor}) > 1
    ]

    return FileTransactionSummary(
        operation_count=sum(counts.values()),
        path_count=len(paths),
        created=counts["create"],
        written=counts["write"],
        edited=counts["edit"],
        deleted=counts["delete"],
        renamed=counts["rename"],
        bytes_delta=bytes_delta,
        paths=tuple(paths),
        risky_paths=tuple(risky),
        contested_paths=tuple(contested),
        large_diff_paths=tuple(large_diffs),
        reversible=reversible,
        rollback_unavailable=rollback_unavailable,
    )


def build_file_rollback_ledger(events: list[Any]) -> tuple[FileRollbackEntry, ...]:
    """Return reversible file operations in the order they should be applied."""

    entries: list[FileRollbackEntry] = []
    for event in reversed(events):
        if getattr(event, "event_type", None) != "file_op":
            continue
        rollback = getattr(event, "rollback", None)
        if not isinstance(rollback, dict) or rollback.get("reversible") is not True:
            continue
        action = str(rollback.get("action") or "")
        if action not in {"write", "delete"}:
            continue
        path = str(rollback.get("path") or getattr(event, "path", "") or "")
        if not path:
            continue
        content = rollback.get("content")
        if content is not None and not isinstance(content, str):
            continue
        entries.append(
            FileRollbackEntry(
                path=path,
                action=action,
                expected_current_sha256=str(rollback.get("expected_current_sha256") or ""),
                content=content,
                source_event_id=str(getattr(event, "event_id", "") or ""),
            )
        )
    return tuple(entries)


def apply_file_rollback_ledger(
    events: list[Any],
    *,
    project_root: str | Path | None = None,
    dry_run: bool = False,
) -> FileRollbackResult:
    """Apply a reversible file-op ledger with optimistic hash checks."""

    root = Path(project_root).resolve() if project_root is not None else None
    entries = build_file_rollback_ledger(events)
    applied = 0
    skipped = 0
    failed = 0
    errors: list[str] = []

    for entry in entries:
        try:
            target = _resolve_rollback_path(entry.path, root)
            if target is None:
                skipped += 1
                errors.append(f"outside_project:{entry.path}")
                continue
            current_hash = _hash_file_text(target)
            if entry.expected_current_sha256 and current_hash != entry.expected_current_sha256:
                skipped += 1
                errors.append(f"hash_mismatch:{entry.path}")
                continue
            if dry_run:
                applied += 1
                continue
            if entry.action == "delete":
                if target.exists():
                    if not target.is_file():
                        failed += 1
                        errors.append(f"not_file:{entry.path}")
                        continue
                    target.unlink()
                applied += 1
                continue
            if entry.action == "write":
                if entry.content is None:
                    failed += 1
                    errors.append(f"missing_content:{entry.path}")
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(entry.content, encoding="utf-8", newline="")
                applied += 1
                continue
            skipped += 1
            errors.append(f"unsupported_action:{entry.action}:{entry.path}")
        except OSError as exc:
            failed += 1
            errors.append(f"os_error:{entry.path}:{exc}")

    return FileRollbackResult(
        applied=applied,
        skipped=skipped,
        failed=failed,
        entries=entries,
        errors=tuple(errors),
    )


def _resolve_rollback_path(path: str, root: Path | None) -> Path | None:
    raw = Path(path)
    target = raw if raw.is_absolute() else (root / raw if root is not None else raw)
    try:
        resolved = target.resolve()
    except OSError:
        resolved = target.absolute()
    if root is None:
        return resolved
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_file_text(path: Path) -> str:
    if not path.exists():
        return ""
    if not path.is_file():
        return "non-file"
    try:
        return _hash_text(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return "unreadable"


def _is_risky_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    name = PurePath(normalized).name
    if normalized.startswith("/") or ":" in PurePath(path).drive:
        return True
    if ".." in normalized.split("/"):
        return True
    return name in {
        ".env",
        ".env.local",
        ".env.production",
        "id_rsa",
        "id_ed25519",
        "known_hosts",
        "credentials.json",
    }
