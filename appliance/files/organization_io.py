"""Conditional, same-filesystem moves for a durable organization plan.

The caller persists its prepared record before entering this module and owns
authorization, plan locking and directory creation. No operation copies bytes,
overwrites a destination, deletes a path, or silently changes permissions.
Windows renames the verified, exclusively opened file handle. Linux captures a
name with renameat2(NOREPLACE), verifies the captured object, then publishes it.
Linux has a final pathname-check/rename race against uncooperative writers;
post-publication evidence can report uncertainty, not eliminate that race.
Process-crash recovery is supported; this is not a physical power-loss claim.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import stat
import sys
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO

_CHUNK_BYTES = 64 * 1024


class OrganizationIOError(OSError):
    """A stable, non-secret reason for an unsupported or changed input."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _parts(relative: str) -> tuple[str, ...]:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise OrganizationIOError("invalid_path")
    parts = tuple(relative.split("/"))
    if len(parts) > 256:
        raise OrganizationIOError("invalid_path")
    for part in parts:
        stem = part.split(".", 1)[0].upper()
        if (
            not part
            or part in {".", ".."}
            or ":" in part
            or "\0" in part
            or part.endswith((" ", "."))
            or stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
            or (len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3] in "123456789¹²³")
        ):
            raise OrganizationIOError("invalid_path")
    return parts


def _root(root: Path | str) -> Path:
    result = Path(root)
    if not result.is_absolute() or any(part in {".", ".."} for part in result.parts):
        raise OrganizationIOError("invalid_root")
    if os.name == "nt":
        from appliance.windows_state import _absolute

        result = _absolute(result)
    return result


def _identity(info: os.stat_result) -> dict[str, int]:
    # ctime changes during rename on POSIX and differs between Windows APIs.
    return {
        "dev": info.st_dev,
        "ino": info.st_ino,
        "mtime_ns": info.st_mtime_ns,
        "mode": stat.S_IMODE(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
    }


def _regular(info: os.stat_result) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
        or info.st_nlink != 1
    ):
        raise OrganizationIOError("not_regular_single_link_file")


def _windows_permissions(stream: BinaryIO) -> dict[str, Any]:
    """Observe ACLs on the verified handle; never change access permissions.

    Windows can protect inherited ACEs as explicit ACEs when changing parent.
    Ignore only INHERITED_ACE provenance. Only consecutive standard ACEs of the
    same allow/deny type commute; never reorder across those types or interpret
    opaque object/conditional ACEs as equivalent.
    """
    import msvcrt

    from appliance.windows_state import _api, _check, _sid_text

    api = _api()
    descriptor, owner, group, dacl = (api.c.c_void_p() for _ in range(4))
    code = api.security.GetSecurityInfo(
        msvcrt.get_osfhandle(stream.fileno()),
        1,
        7,
        api.c.byref(owner),
        api.c.byref(group),
        api.c.byref(dacl),
        None,
        api.c.byref(descriptor),
    )
    if code:
        raise api.c.WinError(code)
    try:
        aces: list[tuple[int, str]] = []
        if dacl.value:
            acl = api.c.cast(dacl, api.c.POINTER(api.Acl)).contents
            for index in range(acl.count):
                pointer = api.c.c_void_p()
                _check(api.security.GetAce(dacl, index, api.c.byref(pointer)))
                header = api.c.string_at(pointer, 4)
                size = int.from_bytes(header[2:4], "little")
                if size < 4:
                    raise OrganizationIOError("permissions_unavailable")
                raw = bytearray(api.c.string_at(pointer, size))
                raw[1] &= ~0x10  # INHERITED_ACE; preserve every access-affecting bit.
                aces.append((raw[0], raw.hex()))
        normalized: list[str] = []
        index = 0
        while index < len(aces):
            end = index + 1
            if aces[index][0] in {0, 1}:
                while end < len(aces) and aces[end][0] == aces[index][0]:
                    end += 1
            normalized.extend(sorted(value for _, value in aces[index:end]))
            index = end
        return {
            "owner": _sid_text(owner),
            "group": _sid_text(group),
            "acl": normalized if dacl.value else None,
        }
    finally:
        api.kernel.LocalFree(descriptor)


def _snapshot_stream(
    stream: BinaryIO, *, max_bytes: int | None, collect: bool = False
) -> tuple[bytes, dict[str, Any]]:
    if max_bytes is not None and (not isinstance(max_bytes, int) or max_bytes < 0):
        raise ValueError("max_bytes must be a nonnegative integer")
    before = os.fstat(stream.fileno())
    _regular(before)
    permissions = _windows_permissions(stream) if os.name == "nt" else None
    if max_bytes is not None and before.st_size > max_bytes:
        raise OrganizationIOError("file_too_large")
    stream.seek(0)
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    size = 0
    while chunk := stream.read(_CHUNK_BYTES):
        size += len(chunk)
        if max_bytes is not None and size > max_bytes:
            raise OrganizationIOError("file_too_large")
        digest.update(chunk)
        if collect:
            chunks.append(chunk)
    after = os.fstat(stream.fileno())
    _regular(after)
    if _identity(before) != _identity(after) or before.st_size != size or after.st_size != size:
        raise OrganizationIOError("file_changed_during_read")
    snapshot = {
        "sha256": digest.hexdigest(),
        "size": size,
        "identity": _identity(after),
    }
    if os.name == "nt":
        if _windows_permissions(stream) != permissions:
            raise OrganizationIOError("permissions_changed_during_read")
        snapshot["permissions"] = permissions
    return b"".join(chunks), snapshot


@contextlib.contextmanager
def _parents(root: Path, relatives: tuple[str, ...]) -> Iterator[dict[str, Any]]:
    """Pin existing parent directories, refusing links in every ancestor."""
    with contextlib.ExitStack() as stack:
        result: dict[str, Any] = {}
        for relative in relatives:
            parent = root.joinpath(*_parts(relative)[:-1])
            key = str(parent)
            if key in result:
                continue
            if os.name == "nt":
                from appliance.windows_state import private_state_directory

                stack.enter_context(private_state_directory(parent, protect=False))
                result[key] = None
            else:
                if not sys.platform.startswith("linux"):
                    raise OrganizationIOError("atomic_move_unsupported")
                fd = os.open(parent.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                stack.callback(os.close, fd)
                for part in parent.parts[1:]:
                    fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    stack.callback(os.close, fd)
                result[key] = fd
        yield result


def _parent_fd(parents: dict[str, Any], path: Path) -> int | None:
    return parents[str(path.parent)]


def _stat(parents: dict[str, Any], path: Path) -> os.stat_result:
    if os.name == "nt":
        return path.lstat()
    return os.stat(path.name, dir_fd=_parent_fd(parents, path), follow_symlinks=False)


@contextlib.contextmanager
def _open_file(parents: dict[str, Any], path: Path, *, moving: bool = False) -> Iterator[BinaryIO]:
    if os.name == "nt":
        import msvcrt

        from appliance.windows_state import _api, _check

        api = _api()
        access = 0x80000000 | (0x10000 if moving else 0)  # READ | DELETE
        handle = api.kernel.CreateFileW("\\\\?\\" + str(path), access, 0, None, 3, 0x00200000, None)
        if handle == api.c.c_void_p(-1).value:
            code = api.c.get_last_error()
            if code in {2, 3}:
                raise FileNotFoundError(errno.ENOENT, "organization file not found")
            raise api.c.WinError(code)
        try:
            info = api.FileInfo()
            _check(api.kernel.GetFileInformationByHandle(handle, api.c.byref(info)))
            if info.attributes & (0x400 | 0x10) or info.links != 1:
                raise OrganizationIOError("not_regular_single_link_file")
            if api.kernel.GetFileType(handle) != 1:
                raise OrganizationIOError("not_regular_single_link_file")
            buffer = api.c.create_unicode_buffer(32768)
            length = api.kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
            if (
                not length
                or length >= len(buffer)
                or buffer.value.casefold() != ("\\\\?\\" + str(path)).casefold()
            ):
                raise OrganizationIOError("path_identity_changed")
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            handle = None
        finally:
            if handle is not None:
                api.kernel.CloseHandle(handle)
    else:
        fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=_parent_fd(parents, path),
        )
    try:
        stream = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    with stream:
        _regular(os.fstat(fd))
        yield stream


def _named_snapshot(
    parents: dict[str, Any], path: Path, *, max_bytes: int | None, collect: bool = False
) -> tuple[bytes, dict[str, Any]]:
    with _open_file(parents, path) as stream:
        data, snapshot = _snapshot_stream(stream, max_bytes=max_bytes, collect=collect)
        if _identity(_stat(parents, path)) != snapshot["identity"]:
            raise OrganizationIOError("path_identity_changed")
        return data, snapshot


def snapshot_file(
    root: Path | str, relative: str, *, max_bytes: int | None = None
) -> dict[str, Any]:
    root = _root(root)
    path = root.joinpath(*_parts(relative))
    with _parents(root, (relative,)) as parents:
        return _named_snapshot(parents, path, max_bytes=max_bytes)[1]


def read_file_snapshot(
    root: Path | str, relative: str, *, max_bytes: int
) -> tuple[bytes, dict[str, Any]]:
    if not isinstance(max_bytes, int) or max_bytes < 0:
        raise ValueError("max_bytes must be a nonnegative integer")
    root = _root(root)
    path = root.joinpath(*_parts(relative))
    with _parents(root, (relative,)) as parents:
        return _named_snapshot(parents, path, max_bytes=max_bytes, collect=True)


@lru_cache(maxsize=1)
def _rename_api() -> Any:
    if os.name == "nt":
        from appliance.windows_state import _api

        api = _api()
        rename = api.kernel.SetFileInformationByHandle
        rename.argtypes = [api.w.HANDLE, api.c.c_int, api.c.c_void_p, api.w.DWORD]
        rename.restype = api.w.BOOL

        class RenameInfo(api.c.Structure):
            _fields_ = [
                ("flags", api.w.DWORD),
                ("root", api.w.HANDLE),
                ("length", api.w.DWORD),
                ("name", api.w.WCHAR * 1),
            ]

        return rename, RenameInfo
    if not sys.platform.startswith("linux"):
        raise OrganizationIOError("atomic_move_unsupported")
    import ctypes

    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if rename is None:
        raise OrganizationIOError("atomic_move_unsupported")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    return rename


def _rename_noreplace(
    parents: dict[str, Any], source: Path, target: Path, *, stream: BinaryIO
) -> None:
    """One no-overwrite rename boundary; no copy or unlink fallback."""
    if os.name == "nt":
        import msvcrt

        from appliance.windows_state import _api, _check

        api = _api()
        rename, info_type = _rename_api()
        name = ("\\\\?\\" + str(target)).encode("utf-16-le")
        buffer = api.c.create_string_buffer(info_type.name.offset + len(name) + 2)
        info = info_type.from_buffer(buffer)
        info.flags = 0  # FileRenameInfo: ReplaceIfExists FALSE.
        info.root = None
        info.length = len(name)
        api.c.memmove(api.c.addressof(buffer) + info_type.name.offset, name, len(name))
        _check(rename(msvcrt.get_osfhandle(stream.fileno()), 3, buffer, len(buffer)))
    else:
        import ctypes

        rename = _rename_api()
        if rename(
            _parent_fd(parents, source),
            os.fsencode(source.name),
            _parent_fd(parents, target),
            os.fsencode(target.name),
            1,
        ):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))


def _sync_directories(parents: dict[str, Any], *paths: Path) -> None:
    if os.name != "nt":
        for fd in {_parent_fd(parents, path) for path in paths}:
            os.fsync(fd)


def _matches(snapshot: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(
        snapshot.get(key) == expected.get(key)
        for key in ("sha256", "size", "identity", "permissions")
    )


def _observe(parents: dict[str, Any], path: Path, *, max_bytes: int) -> dict[str, Any] | None:
    try:
        info = _stat(parents, path)
        try:
            _regular(info)
        except OrganizationIOError as exc:
            # A conflicting link/FIFO/directory is an existing name, never a
            # reason to open it or mistake it for a missing destination.
            return {"invalid": exc.reason}
        if info.st_size > max_bytes:
            return {"invalid": "file_too_large"}
        try:
            return _named_snapshot(parents, path, max_bytes=max_bytes)[1]
        except OrganizationIOError as exc:
            return {"invalid": exc.reason}
    except FileNotFoundError:
        return None


def _result(
    status: str, committed: bool | None, reason: str, paths: tuple[str, ...]
) -> dict[str, Any]:
    return {
        "status": status,
        "committed": committed,
        "reason": reason,
        "recoveryPaths": list(paths),
    }


def _state_result(
    states: tuple[dict[str, Any] | None, ...], expected: dict[str, Any], relatives: tuple[str, ...]
) -> dict[str, Any] | None:
    source, target, pending = states
    present = tuple(
        relative for relative, state in zip(relatives, states, strict=True) if state is not None
    )
    if target is not None:
        if _matches(target, expected):
            if source is None and pending is None:
                return _result("moved", True, "already_moved", present)
            return _result("conflict", True, "other_path_present_after_move", present)
        if (source is not None and _matches(source, expected)) or (
            pending is not None and _matches(pending, expected)
        ):
            return _result("conflict", False, "target_exists", present)
        return _result("uncertain", None, "target_changed_or_unrelated", present)
    if pending is not None:
        if not _matches(pending, expected):
            return _result("conflict", False, "pending_conflict", present)
        if source is not None:
            return _result("conflict", False, "source_present_with_pending", present)
        return None
    if source is not None:
        if not _matches(source, expected):
            return _result("conflict", False, "source_changed", present)
        return None
    return _result("uncertain", None, "source_missing", present)


def _check_opened_name(
    parents: dict[str, Any], path: Path, stream: BinaryIO, expected: dict[str, Any]
) -> None:
    snapshot = _snapshot_stream(stream, max_bytes=expected["size"])[1]
    if not _matches(snapshot, expected):
        raise OrganizationIOError("source_changed")
    named = _stat(parents, path)
    _regular(named)
    if _identity(named) != snapshot["identity"] or named.st_size != snapshot["size"]:
        raise OrganizationIOError("path_identity_changed")


def _failure_reason(error: OSError) -> str:
    if isinstance(error, OrganizationIOError):
        return error.reason
    if error.errno == errno.EXDEV or getattr(error, "winerror", None) == 17:
        return "cross_device_move"
    if error.errno in {errno.ENOSYS, errno.ENOTSUP, errno.EINVAL}:
        return "atomic_move_unsupported"
    if isinstance(error, FileExistsError) or getattr(error, "winerror", None) in {80, 183}:
        return "target_exists"
    return "io_error"


def _move_inputs(root, relatives, expected):
    root = _root(root)
    paths = tuple(root.joinpath(*_parts(value)) for value in relatives)
    if len(set(paths)) != 3 or paths[0].parent != paths[2].parent:
        raise OrganizationIOError("invalid_move_paths")
    if (
        not isinstance(expected, dict)
        or not isinstance(expected.get("sha256"), str)
        or len(expected["sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in expected["sha256"])
        or type(expected.get("size")) is not int
        or expected["size"] < 0
        or not isinstance(expected.get("identity"), dict)
        or set(expected["identity"]) != {"dev", "ino", "mtime_ns", "mode", "uid", "gid"}
        or not all(type(value) is int for value in expected["identity"].values())
    ):
        raise OrganizationIOError("invalid_precondition")
    return root, paths


def inspect_move(
    root: Path | str, source: str, target: str, *, expected: dict[str, Any], pending: str
) -> dict[str, Any]:
    """Only observe recovery evidence; never rename, create, or load a model.

    Missing destination parents are an absent target, not a reason to create
    directories. A read is bounded by the original, server-recorded file size.
    Separate observations are not a transaction against outside writers.
    """
    relatives = (source, target, pending)
    try:
        root, paths = _move_inputs(root, relatives, expected)
        states = []
        with _parents(root, (".organization-inspect-sentinel",)):
            for relative, path in zip(relatives, paths, strict=True):
                try:
                    with _parents(root, (relative,)) as parents:
                        states.append(_observe(parents, path, max_bytes=expected["size"]))
                except FileNotFoundError:
                    states.append(None)
        result = _state_result(tuple(states), expected, relatives)
        if result is not None:
            return result
        captured = states[2] is not None
        return _result(
            "pending",
            False,
            "captured_pending_ready" if captured else "source_ready",
            (pending if captured else source,),
        )
    except (OSError, ValueError) as exc:
        reason = _failure_reason(exc) if isinstance(exc, OSError) else "invalid_path"
        if isinstance(exc, OSError) and not isinstance(exc, OrganizationIOError):
            return _result("uncertain", None, reason, relatives)
        return _result("conflict", False, reason, relatives)


def move_file(
    root: Path | str, source: str, target: str, *, expected: dict[str, Any], pending: str
) -> dict[str, Any]:
    """Execute/recover one prepared move; return evidence, never a guessed retry.

    Target parents must exist. ``pending`` is caller-reserved in the source's
    parent and must be persisted with ``expected`` before the first call.
    ``committed`` means the verified original was observed at target, or this
    call published it. Later changes can make the result uncertain despite
    that committed fact. A conflict can also be committed (for example an
    external process recreated source).
    """
    relatives = (source, target, pending)
    try:
        root, paths = _move_inputs(root, relatives, expected)
        src, dst, temp = paths
        _rename_api()
        with _parents(root, relatives) as parents:
            states = tuple(_observe(parents, path, max_bytes=expected["size"]) for path in paths)
            result = _state_result(states, expected, relatives)
            if result is not None:
                return result
            # No syscall is permitted to cross devices, even if a platform
            # implementation would otherwise offer a copy/delete convenience.
            destination_device = (
                dst.parent.stat().st_dev
                if os.name == "nt"
                else os.fstat(_parent_fd(parents, dst)).st_dev
            )
            if destination_device != expected["identity"]["dev"]:
                raise OrganizationIOError("cross_device_move")
            active = temp if states[2] is not None else src
            mutated = False
            published = False
            failure: OSError | None = None
            try:
                with _open_file(parents, active, moving=True) as stream:
                    _check_opened_name(parents, active, stream, expected)
                    if active == src:
                        _rename_noreplace(parents, src, temp, stream=stream)
                        mutated = True
                        _sync_directories(parents, src, temp)
                        _check_opened_name(parents, temp, stream, expected)
                    _rename_noreplace(parents, temp, dst, stream=stream)
                    mutated = True
                    # Windows names the verified exclusive handle. Linux
                    # names a directory entry, so first verify what arrived.
                    published = os.name == "nt"
                    _sync_directories(parents, temp, dst)
                    _check_opened_name(parents, dst, stream, expected)
                    published = True
            except OSError as exc:
                failure = exc
            # Close the exclusive Windows handle before reopening observations.
            # A syscall can have succeeded even when its wrapper/close failed.
            try:
                after = tuple(_observe(parents, path, max_bytes=expected["size"]) for path in paths)
            except OSError:
                return _result(
                    "uncertain", True if published else None, "observation_unavailable", relatives
                )
            result = _state_result(after, expected, relatives)
            if result is not None and result["committed"] is True:
                if failure is None:
                    if result["status"] == "moved":
                        result["reason"] = "moved"
                    return result
                return _result(
                    "uncertain", True, _failure_reason(failure), tuple(result["recoveryPaths"])
                )
            if published:
                return _result("uncertain", True, "target_changed_after_publish", relatives)
            if failure is not None:
                present = tuple(
                    value
                    for value, state in zip(relatives, after, strict=True)
                    if state is not None
                )
                # A known, preserved pending original is safe to reconcile on
                # the next call; it is not a completed move.
                if after[2] is not None and _matches(after[2], expected):
                    return _result("conflict", False, _failure_reason(failure), present)
                if not mutated and after[0] is not None and _matches(after[0], expected):
                    return _result("conflict", False, _failure_reason(failure), present)
                return _result("uncertain", None, _failure_reason(failure), present)
            return result or _result("uncertain", None, "post_move_state_changed", relatives)
    except (OSError, ValueError) as exc:
        reason = _failure_reason(exc) if isinstance(exc, OSError) else "invalid_path"
        if isinstance(exc, OSError) and not isinstance(exc, OrganizationIOError):
            return _result("uncertain", None, reason, relatives)
        return _result("conflict", False, reason, relatives)


__all__ = [
    "OrganizationIOError",
    "inspect_move",
    "move_file",
    "read_file_snapshot",
    "snapshot_file",
]
