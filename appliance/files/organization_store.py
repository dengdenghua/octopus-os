"""Private file-operation receipts; TaskSupervisor remains the task authority."""

from __future__ import annotations

import json
import os
import re
import stat
import threading
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from appliance.files.manager import _replace_upload_metadata
from appliance.state_lock import StateDirectoryLock, StateLockError

_ID = re.compile(r"[0-9a-f]{64}")
_MAX_BYTES = 4 * 1024 * 1024


class OrganizationStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory).absolute()
        self._mutex = threading.RLock()
        self._local = threading.local()
        with self._directory():
            pass

    @contextmanager
    def _directory(self):
        if os.name == "nt":
            from appliance.windows_state import private_state_directory

            with private_state_directory(self.directory, create=True):
                yield None
            return
        # Never create through an unchecked symlink ancestor.
        if len(self.directory.parts) < 2:
            raise ValueError("organization state cannot be a filesystem root")
        with ExitStack() as stack:
            fd = os.open(self.directory.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, fd)
            for part in self.directory.parts[1:]:
                if part in {".", ".."}:
                    raise ValueError("unsafe organization state path")
                with suppress(FileExistsError):
                    os.mkdir(part, 0o700, dir_fd=fd)
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                stack.callback(os.close, fd)
            if os.fstat(fd).st_uid != os.getuid():
                raise OSError("unsafe file-organization state directory")
            os.fchmod(fd, 0o700)
            yield fd

    @contextmanager
    def lease(self):
        """Nonblocking cross-process lease, reentrant only on this thread/store."""
        if not self._mutex.acquire(blocking=False):
            raise StateLockError("file organization state is already in use")
        try:
            existing = getattr(self._local, "lease", None)
            if existing is not None:
                yield existing
                return
            with (
                self._directory(),
                StateDirectoryLock.acquire(
                    self.directory, exclusive=True, purpose="file organization"
                ) as lease,
            ):
                self._local.lease = lease
                try:
                    yield lease
                finally:
                    self._local.lease = None
        finally:
            self._mutex.release()

    def _path(self, plan_id: str) -> Path:
        if not isinstance(plan_id, str) or _ID.fullmatch(plan_id) is None:
            raise ValueError("invalid organization plan id")
        return self.directory / f"{plan_id}.json"

    def load(self, plan_id: str) -> dict[str, Any]:
        path = self._path(plan_id)
        with self._directory() as directory_fd:
            if os.name == "nt":
                import msvcrt

                from appliance.windows_state import _api, _open, _verify_private

                # Existing receipts are verified, never adopted by changing ACLs.
                handle = _open(path, directory=False, access=0x80020000)
                try:
                    _verify_private(handle, directory=False)
                    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
                    handle = None
                finally:
                    if handle is not None:
                        _api().kernel.CloseHandle(handle)
            else:
                fd = os.open(
                    path.name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=directory_fd,
                )
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_size > _MAX_BYTES
                    or (os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077))
                ):
                    raise OSError("unsafe file-organization receipt")
                payload = source.read(_MAX_BYTES + 1)
        if len(payload) > _MAX_BYTES:
            raise ValueError("oversized file-organization receipt")
        record = json.loads(payload)
        self._validate(record, plan_id)
        return record

    def _validate(self, record: Any, plan_id: str | None = None) -> str:
        if (
            not isinstance(record, dict)
            or record.get("schema") != "echo.files.organization-state.v1"
        ):
            raise ValueError("invalid file-organization receipt")
        plan = record.get("plan")
        if not isinstance(plan, dict):
            raise ValueError("invalid file-organization plan")
        identifier = plan.get("planId")
        self._path(identifier)
        if plan_id is not None and identifier != plan_id:
            raise ValueError("file-organization receipt identity changed")
        if any(
            not isinstance(record.get(key), str) or not record[key].strip()
            for key in ("owner", "taskId", "workspacePath")
        ):
            raise ValueError("invalid file-organization receipt binding")
        return identifier

    def save(self, record: dict[str, Any]) -> None:
        plan_id = self._validate(record)
        destination = self._path(plan_id)
        payload = json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(payload) > _MAX_BYTES:
            raise ValueError("oversized file-organization receipt")
        temporary = self.directory / f".organization-{uuid4().hex}.tmp"
        # Bind the value being committed, not a mutable caller-owned dictionary.
        candidate = json.loads(payload)
        with self.lease(), self._directory() as directory_fd:
            try:
                previous = self.load(plan_id)
            except FileNotFoundError:
                previous = None
            if previous is not None and any(
                previous[key] != candidate[key] for key in ("owner", "taskId", "workspacePath")
            ):
                raise PermissionError("file-organization receipt binding cannot change")
            if os.name == "nt":
                from appliance.windows_state import open_private_file

                fd = open_private_file(temporary, create_new=True)
            else:
                fd = os.open(
                    temporary.name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory_fd,
                )
            error: BaseException | None = None
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(payload)
                    output.flush()
                    os.fsync(output.fileno())
                try:
                    _replace_upload_metadata(temporary, destination)
                except OSError as exc:
                    # Rename can commit before a durability error is reported.
                    # Preserve that distinction for the service's recovery path.
                    try:
                        exc.organization_committed = self.load(plan_id) == candidate
                    except (OSError, ValueError):
                        exc.organization_committed = None
                    raise
            except BaseException as exc:
                error = exc
                if not hasattr(exc, "organization_committed"):
                    exc.organization_committed = False
                raise
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    # The staging file was private before it received content.
                    # Preserve the original commit evidence if cleanup also fails.
                    if error is None:
                        cleanup_error.organization_committed = True
                        cleanup_error.organization_cleanup_failed = True
                        raise
                    error.organization_cleanup_failed = True
                    error.add_note("private organization staging cleanup failed")


__all__ = ["OrganizationStore", "StateLockError"]
