"""Persist the last photo-index lifecycle; recovery requires a committed receipt."""

from __future__ import annotations

import errno
import json
import math
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

from appliance.state_lock import LOCK_FILENAME, StateDirectoryLock, StateLockError

_SCHEMA = "echo.photos.index-job-state.v1"
_MAX_BYTES = 16_384


def idle_job() -> dict[str, Any]:
    return {
        "state": "idle",
        "jobId": None,
        "planId": None,
        "includeFaces": False,
        "startedAt": None,
        "completedAt": None,
        "result": None,
        "error": None,
    }


def _job(value: Any) -> dict[str, Any]:
    """Only bounded public fields belong in this journal or the status response."""

    if not isinstance(value, dict) or value.get("state") not in {
        "idle",
        "running",
        "pausing",
        "paused",
        "cancelling",
        "cancelled",
        "succeeded",
        "failed",
    }:
        raise ValueError("invalid photo job state")
    result = idle_job()
    result["state"] = value["state"]
    for name, length in (("jobId", 24), ("planId", 64)):
        item = value.get(name)
        if item is not None and (
            not isinstance(item, str) or re.fullmatch(rf"[0-9a-f]{{{length}}}", item) is None
        ):
            raise ValueError("invalid photo job identity")
        result[name] = item
    if type(value.get("includeFaces")) is not bool:
        raise ValueError("invalid photo job face setting")
    result["includeFaces"] = value["includeFaces"]
    if "cleanupOnly" in value:
        if type(value["cleanupOnly"]) is not bool:
            raise ValueError("invalid photo job cleanup setting")
        result["cleanupOnly"] = value["cleanupOnly"]
    for name in ("startedAt", "completedAt"):
        item = value.get(name)
        if item is not None and (
            type(item) not in (int, float) or not math.isfinite(item) or item < 0
        ):
            raise ValueError("invalid photo job timestamp")
        result[name] = item
    error = value.get("error")
    if error is not None and (
        not isinstance(error, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", error) is None
    ):
        error = "index_build_failed"
    result["error"] = error
    if isinstance(value.get("result"), dict):
        public: dict[str, Any] = {}
        for name in ("indexed", "faces", "failed", "skipped", "reused", "embedded", "removed"):
            item = value["result"].get(name)
            if type(item) is int and 0 <= item <= 1_000_000:
                public[name] = item
        for name in (
            "ok",
            "semantic",
            "face_capable",
            "partial",
            "cancelled",
            "paused",
            "retained_previous",
            "resource_limited",
        ):
            item = value["result"].get(name)
            if type(item) is bool:
                public[name] = item
        result["result"] = public
    return result


class PhotoJobStore:
    def __init__(self, directory: Path) -> None:
        self.path = directory / "index-job.json"

    def try_lease(self) -> StateDirectoryLock | _WindowsJobLease | None:
        """Hold an OS lock, not a PID record, for the entire index operation."""

        if os.name != "nt":
            try:
                return StateDirectoryLock.acquire(
                    self.path.parent, exclusive=True, purpose="photo indexing"
                )
            except StateLockError as exc:
                cause = exc.__cause__
                if isinstance(cause, OSError) and cause.errno in {errno.EACCES, errno.EAGAIN}:
                    return None
                raise OSError("photo index lock unavailable") from exc
        return _WindowsJobLease.acquire(self.path.parent / LOCK_FILENAME)

    def load(self) -> dict[str, Any]:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return idle_job()
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES:
            raise OSError("unsafe photo job state")
        fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise OSError("unsafe photo job state")
            raw = stream.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError("oversized photo job state")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("schema") != _SCHEMA:
            raise ValueError("unknown photo job state schema")
        return _job(payload.get("job"))

    def save(self, job: dict[str, Any]) -> dict[str, Any]:
        clean = _job(job)
        if self.path.is_symlink() or (self.path.exists() and not self.path.is_file()):
            raise OSError("unsafe photo job state")
        payload = json.dumps({"schema": _SCHEMA, "job": clean}, separators=(",", ":"))
        fd, temporary = tempfile.mkstemp(prefix=".echo-photo-job-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            if hasattr(os, "O_DIRECTORY"):
                directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return clean


class _WindowsJobLease:
    """Windows counterpart of StateDirectoryLock's nonblocking flock contract."""

    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor

    @classmethod
    def acquire(cls, path: Path) -> _WindowsJobLease | None:
        import msvcrt

        if path.is_symlink():
            raise OSError("unsafe photo index lock")
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise OSError("photo index lock is not a file")
            # locking supports a region beyond EOF. Never rewrite/truncate the
            # lock file: its persistent identity must be shared by all openers.
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            os.close(descriptor)
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return None
            raise
        return cls(descriptor)

    def release(self) -> None:
        descriptor = self._descriptor
        if descriptor < 0:
            return
        self._descriptor = -1
        os.close(descriptor)

    def __del__(self) -> None:
        self.release()


__all__ = ["PhotoJobStore", "idle_job"]
