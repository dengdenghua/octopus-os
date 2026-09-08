from __future__ import annotations

import hashlib
import os
import stat
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
    hash_mode: str = "text-v1"
    expected_current_exists: bool | None = None
    unavailable_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "action": self.action,
            "expected_current_sha256": self.expected_current_sha256,
            "content": self.content,
            "source_event_id": self.source_event_id,
            "hash_mode": self.hash_mode,
            "expected_current_exists": self.expected_current_exists,
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True)
class FileRollbackOutcome:
    source_event_id: str
    path: str
    status: str
    reason: str | None = None
    committed: bool | None = False
    recovery_paths: tuple[str, ...] = field(default_factory=tuple)
    evidence_private: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_event_id": self.source_event_id,
            "path": self.path,
            "status": self.status,
            "reason": self.reason,
            "committed": self.committed,
            "recovery_paths": list(self.recovery_paths),
            "evidence_private": self.evidence_private,
        }


@dataclass(frozen=True)
class FileRollbackResult:
    applied: int = 0
    skipped: int = 0
    failed: int = 0
    entries: tuple[FileRollbackEntry, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)
    outcomes: tuple[FileRollbackOutcome, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "skipped": self.skipped,
            "failed": self.failed,
            "entries": [entry.to_dict() for entry in self.entries],
            "errors": list(self.errors),
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
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
    """Return reverse operations, retaining unavailable events as path barriers."""

    entries: list[FileRollbackEntry] = []
    for event in reversed(events):
        if getattr(event, "event_type", None) != "file_op":
            continue
        raw_rollback = getattr(event, "rollback", None)
        rollback = raw_rollback if isinstance(raw_rollback, dict) else {}
        path = str(rollback.get("path") or getattr(event, "path", "") or "")
        if not path:
            continue
        action = str(rollback.get("action") or "unavailable")
        content = rollback.get("content")
        hash_mode = rollback.get("hash_mode", "text-v1")
        expected_exists = rollback.get("expected_current_exists")
        expected_hash = str(rollback.get("expected_current_sha256") or "")
        reason = None
        if rollback.get("reversible") is not True:
            reason = "not_reversible"
        elif action not in {"write", "delete"}:
            reason = "unsupported_action"
        elif action == "write" and not isinstance(content, str):
            reason = "missing_content"
        elif not isinstance(hash_mode, str) or hash_mode not in {"text-v1", "bytes-v1"}:
            reason = "invalid_hash_mode"
        elif expected_exists is not None and not isinstance(expected_exists, bool):
            reason = "invalid_precondition"
        elif expected_hash and (
            len(expected_hash) != 64 or any(c not in "0123456789abcdef" for c in expected_hash)
        ):
            reason = "invalid_hash"
        elif (
            action == "delete"
            and not expected_hash
            or expected_exists is True
            and not expected_hash
        ):
            reason = "missing_hash"
        elif expected_exists is False and expected_hash:
            reason = "invalid_precondition"
        if isinstance(content, str):
            try:
                content.encode("utf-8")
            except UnicodeEncodeError:
                reason = "invalid_content"
        if expected_exists is None and action == "write" and not expected_hash:
            # Legacy deleted-file recovery also means "the path is absent",
            # never unconditional permission to overwrite a recreated file.
            expected_exists = False
        entries.append(
            FileRollbackEntry(
                path=path,
                action=action,
                expected_current_sha256=expected_hash,
                content=content,
                source_event_id=str(getattr(event, "event_id", "") or ""),
                hash_mode=hash_mode if isinstance(hash_mode, str) else "text-v1",
                expected_current_exists=expected_exists
                if isinstance(expected_exists, bool)
                else None,
                unavailable_reason=reason,
            )
        )
    return tuple(entries)


def apply_file_rollback_ledger(
    events: list[Any],
    *,
    project_root: str | Path | None = None,
    dry_run: bool = False,
) -> FileRollbackResult:
    """Undo in reverse order; a conflict blocks older operations on that path.

    Preview simulates every successful step, without touching disk. Hash checks
    protect later edits, but do not lock out arbitrary external applications.
    """

    root = Path(project_root).resolve() if project_root is not None else None
    from ._file_rollback_io import (
        RollbackConflict,
        atomic_restore_text,
        guarded_remove_created_file,
        guarded_remove_supported,
    )

    entries = build_file_rollback_ledger(events)
    applied = 0
    skipped = 0
    failed = 0
    errors: list[str] = []
    outcomes: list[FileRollbackOutcome] = []
    blocked: set[Path] = set()
    simulated: dict[Path, str | None] = {}

    for entry in entries:
        target: Path | None = None
        path_key: Path | None = None
        reason: str | None = None
        outcome = "skipped"
        committed: bool | None = False
        recovery_paths: tuple[str, ...] = ()
        evidence_private: bool | None = None
        try:
            raw = Path(entry.path)
            path_key = Path(os.path.abspath(root / raw if root is not None else raw))
            target = _resolve_rollback_path(entry.path, root)
            if target is None:
                reason = "outside_project"
            elif path_key in blocked:
                reason = "newer_operation_blocked"
            elif entry.unavailable_reason:
                reason = entry.unavailable_reason
            else:
                if dry_run and target in simulated:
                    value = simulated[target]
                    exists = value is not None
                    current_hash = _hash_rollback_text(value, entry.hash_mode) if exists else ""
                else:
                    exists = target.exists()
                    current_hash = _hash_file_text(target, hash_mode=entry.hash_mode)
                if (
                    entry.expected_current_exists is not None
                    and exists != entry.expected_current_exists
                ):
                    reason = "existence_mismatch"
                elif current_hash in {"non-file", "unreadable"}:
                    reason = current_hash.replace("-", "_")
                elif (
                    entry.expected_current_sha256 and current_hash != entry.expected_current_sha256
                ):
                    reason = "hash_mismatch"
                elif entry.action == "delete" and not guarded_remove_supported():
                    reason = "guarded_delete_unsupported"
                elif dry_run:
                    simulated[target] = entry.content if entry.action == "write" else None
                    outcome = "would_apply"
                elif entry.action == "delete":
                    try:
                        guarded_remove_created_file(
                            target,
                            expected_sha256=entry.expected_current_sha256,
                            hash_mode=entry.hash_mode,
                        )
                    except RollbackConflict as exc:
                        reason = exc.reason
                    else:
                        committed = True
                        outcome = "applied"
                elif entry.action == "write":
                    # The IO implementation performs a second identity/hash
                    # check immediately before the atomic publish operation.
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        atomic_restore_text(
                            target,
                            entry.content,
                            expected_sha256=entry.expected_current_sha256,
                            hash_mode=entry.hash_mode,
                            require_absent=not exists,
                        )
                    except RollbackConflict as exc:
                        reason = exc.reason
                    else:
                        committed = True
                        outcome = "applied"
        except OSError as exc:
            outcome = "failed"
            reason = "os_error"
            recovery_paths = tuple(
                str(value)
                for name in ("rollback_backup_path", "rollback_temporary_path")
                if (value := getattr(exc, name, None)) is not None
            )
            privacy = getattr(exc, "rollback_evidence_private", None)
            evidence_private = privacy if isinstance(privacy, bool) else None
            if getattr(exc, "rollback_commit_uncertain", False):
                committed = None
                outcome = "uncertain"
                reason = "commit_uncertain"
            elif getattr(exc, "rollback_committed", False):
                committed = True
                reason = "committed_finalization_failed"
        except ValueError:
            outcome = "failed"
            reason = "invalid_path"
        if reason is not None:
            if path_key is not None:
                blocked.add(path_key)
            errors.append(f"{reason}:{entry.path}")
        if outcome in {"applied", "would_apply"}:
            applied += 1
        elif outcome in {"failed", "uncertain"}:
            failed += 1
        else:
            skipped += 1
        outcomes.append(
            FileRollbackOutcome(
                entry.source_event_id,
                entry.path,
                outcome,
                reason,
                committed,
                recovery_paths,
                evidence_private,
            )
        )

    return FileRollbackResult(
        applied=applied,
        skipped=skipped,
        failed=failed,
        entries=entries,
        errors=tuple(errors),
        outcomes=tuple(outcomes),
    )


def _resolve_rollback_path(path: str, root: Path | None) -> Path | None:
    raw = Path(path)
    target = raw if raw.is_absolute() else (root / raw if root is not None else raw)
    # Normalize `..` lexically; resolving links first would silently turn a
    # replaced filename into permission to overwrite the linked file instead.
    resolved = Path(os.path.abspath(target))
    if root is not None:
        try:
            resolved.relative_to(root)
        except ValueError:
            return None
    current = resolved
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                return None
        if current == root or current.parent == current:
            break
        current = current.parent
    return resolved


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_rollback_text(text: str, hash_mode: str) -> str:
    if hash_mode == "text-v1":
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _hash_text(text)


def _hash_file_text(path: Path, *, hash_mode: str = "text-v1") -> str:
    if not path.exists():
        return ""
    if not path.is_file():
        return "non-file"
    try:
        digest = hashlib.sha256()
        if hash_mode == "bytes-v1":
            with path.open("rb") as source:
                while chunk := source.read(64 * 1024):
                    digest.update(chunk)
        else:
            with path.open("r", encoding="utf-8", newline=None) as source:
                while chunk := source.read(64 * 1024):
                    digest.update(chunk.encode("utf-8"))
        return digest.hexdigest()
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
