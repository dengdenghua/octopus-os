"""Read-only, bounded invoice organization previews with sealed source evidence."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import os
import stat
import threading
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from appliance.agent_api.documents import extract_invoice_document
from appliance.data_access import DataAccessDenied, DataAccessScope
from appliance.files import organization_io as file_io
from appliance.files.invoice_classification import classify_invoice
from appliance.files.manager import FileManager

MAX_SCAN_ENTRIES = 250
MAX_SCAN_DEPTH = 8
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
PLAN_LIFETIME_SECONDS = 15 * 60
_FORMATS = frozenset({"pdf", "docx", "txt", "md", "csv", "tsv"})
_STATE_SCHEMA = "echo.files.organization-state.v1"
_PLAN_SCHEMA = "echo.files.organize.plan.v1"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _hash_payload(record: dict[str, Any]) -> dict[str, Any]:
    plan = copy.deepcopy(record["plan"])
    plan.pop("planId", None)
    plan.pop("result", None)
    plan["approval"].pop("target", None)
    return {
        **{
            key: record[key]
            for key in (
                "schema",
                "owner",
                "workspacePath",
                "rootIdentity",
                "snapshots",
                "createdAtEpoch",
                "expiresAtEpoch",
                "nonce",
            )
        },
        "plan": plan,
    }


def seal_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a sealed deep copy; result/task lifecycle fields are not hashed."""
    sealed = copy.deepcopy(record)
    if sealed["schema"] != _STATE_SCHEMA or sealed["plan"]["schema"] != _PLAN_SCHEMA:
        raise ValueError("invalid organization schema")
    for key in ("createdAtEpoch", "expiresAtEpoch"):
        value = sealed[key]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("invalid organization timestamp")
    if sealed["expiresAtEpoch"] <= sealed["createdAtEpoch"]:
        raise ValueError("invalid organization expiry")
    if not isinstance(sealed["nonce"], str) or not sealed["nonce"]:
        raise ValueError("invalid organization nonce")
    identifier = hashlib.sha256(_canonical(_hash_payload(sealed))).hexdigest()
    sealed["plan"]["planId"] = identifier
    sealed["plan"]["approval"]["target"] = identifier
    return sealed


def verify_record(record: Any) -> bool:
    """Check the immutable plan identity without accessing its files or store."""
    try:
        if not isinstance(record, dict):
            return False
        actual = record["plan"]["planId"]
        sealed = seal_record(record)
        return (
            isinstance(actual, str)
            and hmac.compare_digest(actual, sealed["plan"]["planId"])
            and record["plan"]["approval"]["target"] == actual
        )
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError, AttributeError):
        return False


def _relative(path: str) -> str:
    if path == "":
        return ""
    if not isinstance(path, str) or path != path.strip():
        raise file_io.OrganizationIOError("invalid_path")
    parts = file_io._parts(path)
    if any(part.casefold().startswith(".echo-") for part in parts):
        raise file_io.OrganizationIOError("invalid_path")
    try:
        path.encode("utf-8")
    except UnicodeError as exc:
        raise file_io.OrganizationIOError("invalid_path") from exc
    return "/".join(parts)


def _join(parent: str, name: str) -> str:
    return f"{parent}/{name}" if parent else name


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _directory_snapshot(root: Path, relative: str) -> dict[str, int]:
    probe = _join(relative, ".organization-preview-probe")
    with file_io._parents(root, (probe,)) as parents:
        path = root.joinpath(*relative.split("/")) if relative else root
        info = path.stat() if os.name == "nt" else os.fstat(file_io._parent_fd(parents, path / "x"))
        if not stat.S_ISDIR(info.st_mode) or _is_link(info):
            raise file_io.OrganizationIOError("unsafe_directory")
        return {"dev": info.st_dev, "ino": info.st_ino, "mtime_ns": info.st_mtime_ns}


def _target_conflict(root: Path, relative: str, *, source_device: int) -> str | None:
    parts = file_io._parts(relative)
    parent = ""
    for index, part in enumerate(parts):
        candidate = root.joinpath(*parts[: index + 1])
        with file_io._parents(root, (_join(parent, ".organization-preview-probe"),)) as parents:
            try:
                info = file_io._stat(parents, candidate)
            except FileNotFoundError:
                parent_info = (
                    candidate.parent.stat()
                    if os.name == "nt"
                    else os.fstat(file_io._parent_fd(parents, candidate))
                )
                return "cross_device_move" if parent_info.st_dev != source_device else None
            if _is_link(info):
                return "unsafe_target"
            if index == len(parts) - 1:
                return "target_exists"
            if not stat.S_ISDIR(info.st_mode):
                return "target_parent_not_directory"
        parent = _join(parent, part)
    return None


def build_organization_record(
    *,
    manager: FileManager,
    actor: str,
    scope: DataAccessScope,
    path: str,
    now: float | None = None,
    deadline: float | None = None,
    cancelled: threading.Event | None = None,
) -> dict[str, Any]:
    """Scan an authorized tree, read bounded snapshots, and propose exact moves.

    No directories, metadata, receipts, or files are written. The caller must
    resolve current authorization again when persisting/applying this preview.
    """
    if not isinstance(actor, str) or not actor or len(actor) > 256 or scope.actor != actor:
        raise DataAccessDenied("organization actor is not authorized")
    selected = _relative(path)
    root = file_io._root(manager.root)
    if scope.root is not None and Path(scope.root).resolve() != root:
        raise DataAccessDenied("organization scope does not match storage root")
    scope.require_read_tree(selected)
    initial_root = _directory_snapshot(root, selected)
    created = time.time() if now is None else now
    if type(created) not in (int, float) or not math.isfinite(created):
        raise ValueError("invalid organization timestamp")
    expires = created + PLAN_LIFETIME_SECONDS
    entries: list[dict[str, Any]] = []
    snapshots: dict[str, Any] = {}
    directories: dict[str, dict[str, int]] = {}
    blockers: set[str] = set()
    visited = 0
    read_bytes = 0

    def exhausted() -> bool:
        if cancelled is not None and cancelled.is_set():
            blockers.add("scan_cancelled")
            return True
        if deadline is not None and time.monotonic() >= deadline:
            blockers.add("scan_time_limit")
            return True
        return False

    def record_file(relative: str, info: os.stat_result) -> None:
        nonlocal read_bytes
        if exhausted():
            return
        scope.require_read(relative)
        name = PurePosixPath(relative).name
        extension = PurePosixPath(name).suffix.lower().lstrip(".")
        entry_id = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24]
        classification = classify_invoice(name=name, text=None, format=extension)
        item: dict[str, Any] = {
            "entryId": entry_id,
            "source": relative,
            "target": None,
            **{
                key: classification[key]
                for key in (
                    "status",
                    "reason",
                    "date",
                    "title",
                    "amount",
                    "currency",
                    "evidence",
                )
            },
        }
        entries.append(item)
        if _is_link(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            item.update(status="conflict", reason="unsafe_source")
            return
        if extension not in _FORMATS:
            return
        if info.st_size > MAX_FILE_BYTES:
            blockers.add("file_size_limit")
            item.update(status="needs_review", reason="file_too_large")
            return
        if info.st_size > MAX_TOTAL_BYTES - read_bytes:
            blockers.add("total_read_limit")
            item.update(status="needs_review", reason="total_read_limit")
            return
        try:
            raw, snapshot = file_io.read_file_snapshot(
                root, relative, max_bytes=min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - read_bytes)
            )
        except OSError as exc:
            blockers.add("file_read_failed")
            reason = (
                exc.reason if isinstance(exc, file_io.OrganizationIOError) else "file_read_failed"
            )
            item.update(status="needs_review", reason=reason)
            return
        read_bytes += len(raw)
        scope.require_read(relative)
        snapshots[entry_id] = snapshot
        extracted = extract_invoice_document(raw, extension)
        if extracted.get("outcome") in {
            "timed_out",
            "resource_limited",
            "worker_failed",
            "cancelled",
        }:
            item.update(status="needs_review", reason=f"extraction_{extracted['outcome']}")
            exhausted()
            return
        classification = classify_invoice(
            name=name,
            text=extracted["text"],
            format=extension,
            truncated=extracted["truncated"],
            extraction_available=extracted["available"],
        )
        item.update(
            {
                key: classification[key]
                for key in (
                    "status",
                    "reason",
                    "date",
                    "title",
                    "amount",
                    "currency",
                    "evidence",
                )
            }
        )
        if classification["status"] != "ready":
            return
        target = _join(selected, f"{classification['yearMonth']}/{name}")
        item["target"] = target
        if os.path.normcase(relative) == os.path.normcase(target):
            item.update(status="already_organized", reason="already_organized")
            return
        try:
            scope.require_write(relative)
            scope.require_write(target)
        except DataAccessDenied:
            item.update(status="conflict", reason="write_access_denied")
            return
        try:
            conflict = _target_conflict(root, target, source_device=snapshot["identity"]["dev"])
        except OSError:
            blockers.add("target_check_failed")
            item.update(status="conflict", reason="target_check_failed")
            return
        if conflict:
            item.update(status="conflict", reason=conflict)

    def scan(relative: str, depth: int) -> None:
        nonlocal visited
        if exhausted():
            return
        scope.require_read_tree(relative)
        probe = _join(relative, ".organization-preview-probe")
        directory = root.joinpath(*relative.split("/")) if relative else root
        try:
            directories[relative] = _directory_snapshot(root, relative)
            with file_io._parents(root, (probe,)) as parents:
                scan_path = (
                    directory if os.name == "nt" else file_io._parent_fd(parents, directory / "x")
                )
                rows = []
                with os.scandir(scan_path) as iterator:
                    for entry in iterator:
                        if exhausted():
                            break
                        visited += 1
                        if visited > MAX_SCAN_ENTRIES:
                            blockers.add("scan_limit")
                            break
                        if manager._is_internal_name(entry.name) or entry.name.startswith(".echo-"):
                            continue
                        child = _join(relative, entry.name)
                        try:
                            _relative(child)
                            # Windows DirEntry.stat may leave link/inode fields
                            # zero; use the pinned parent's full no-follow stat.
                            info = file_io._stat(parents, directory / entry.name)
                        except (OSError, ValueError):
                            blockers.add("scan_entry_failed")
                            continue
                        rows.append((entry.name, child, info))
                for _, child, info in sorted(rows):
                    if exhausted():
                        break
                    scope.require_read(child)
                    if stat.S_ISDIR(info.st_mode) and not _is_link(info):
                        if depth >= MAX_SCAN_DEPTH:
                            blockers.add("depth_limit")
                        elif "scan_limit" not in blockers:
                            scan(child, depth + 1)
                    else:
                        record_file(child, info)
        except DataAccessDenied:
            raise
        except OSError:
            blockers.add("scan_failed")

    scan(selected, 0)
    exhausted()
    # A late ACL denial must not publish earlier captured file content.
    scope.require_read_tree(selected)
    for item in entries:
        scope.require_read(item["source"])
        if item["status"] == "ready":
            try:
                scope.require_write(item["source"])
                scope.require_write(item["target"])
            except DataAccessDenied:
                item.update(status="conflict", reason="write_access_denied")
    for directory, expected in directories.items():
        try:
            if _directory_snapshot(root, directory) != expected:
                blockers.add("scan_changed")
        except OSError:
            blockers.add("scan_changed")
    try:
        root_changed = _directory_snapshot(root, selected) != initial_root
    except OSError:
        root_changed = True
    if root_changed:
        blockers.add("scan_changed")

    targets: dict[str, list[dict[str, Any]]] = {}
    for item in entries:
        if item["status"] == "ready":
            targets.setdefault(os.path.normcase(item["target"]), []).append(item)
    for matches in targets.values():
        if len(matches) > 1:
            for item in matches:
                item.update(status="conflict", reason="target_collision")
    entries.sort(key=lambda item: item["source"])
    summary = {
        "scanned": len(entries),
        **{
            label: sum(item["status"] == status for item in entries)
            for label, status in (
                ("ready", "ready"),
                ("needsReview", "needs_review"),
                ("alreadyOrganized", "already_organized"),
                ("conflicts", "conflict"),
                ("unsupported", "unsupported"),
            )
        },
    }
    complete = not blockers
    plan = {
        "schema": _PLAN_SCHEMA,
        "path": selected,
        "createdAt": datetime.fromtimestamp(created, UTC).isoformat().replace("+00:00", "Z"),
        "expiresAt": datetime.fromtimestamp(expires, UTC).isoformat().replace("+00:00", "Z"),
        "direction": "apply",
        "scanComplete": complete,
        "ready": complete and summary["ready"] > 0,
        "requiresApproval": True,
        "approval": {"action": "files.organize.apply"},
        "entries": entries,
        "summary": summary,
        "blockers": sorted(blockers),
    }
    return seal_record(
        {
            "schema": _STATE_SCHEMA,
            "owner": actor,
            "plan": plan,
            "workspacePath": str(root.joinpath(*selected.split("/")) if selected else root),
            "taskId": str(uuid4()),
            "snapshots": snapshots,
            "rootIdentity": {key: initial_root[key] for key in ("dev", "ino")},
            "createdAtEpoch": created,
            "expiresAtEpoch": expires,
            "nonce": uuid4().hex,
        }
    )


__all__ = ["build_organization_record", "seal_record", "verify_record"]
