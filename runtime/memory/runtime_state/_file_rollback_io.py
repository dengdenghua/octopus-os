"""Conditional, atomic publication of rollback text on local filesystems.

This is not a compare-and-swap filesystem: another writer can still change an
existing target between the final validation and replacement. An absent target
is published with a genuine no-overwrite primitive. Unsupported filesystems or
permission preservation failures raise; they never fall back to direct writes.

Win32 contracts: ReplaceFileW preserves the replaced file's DACL/attributes;
flags that ignore ACL/merge failures are deliberately not used. Its documented
1176/1177 failures can leave renamed files, so recovery copies are retained and
the original OSError is marked ``rollback_commit_uncertain``. Any failure after
a successful publication is marked ``rollback_committed``. Neither means that
the caller may blindly retry the operation.
"""

from __future__ import annotations

import codecs
import errno
import hashlib
import io
import os
import stat
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

_DIGEST_CHUNK_BYTES = 64 * 1024


class RollbackConflict(RuntimeError):
    """The target no longer satisfies the recorded rollback precondition."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class _Snapshot:
    identity: tuple[int, ...]
    digest: str
    mode: int
    uid: int
    gid: int
    security: Any


def _identity(value: os.stat_result) -> tuple[int, ...]:
    identity = (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    # Windows path-stat and CRT fstat can expose different ctime semantics.
    # File ID/volume identify the file; its permissions are checked separately.
    return identity if os.name == "nt" else (*identity, value.st_ctime_ns)


def _regular(value: os.stat_result) -> bool:
    return stat.S_ISREG(value.st_mode) and not (getattr(value, "st_file_attributes", 0) & 0x400)


def _stream_digest(stream: Any, hash_mode: str) -> str:
    """Hash bounded chunks without closing or wrapping the caller's handle."""
    digest = hashlib.sha256()
    decoder = (
        io.IncrementalNewlineDecoder(codecs.getincrementaldecoder("utf-8")("strict"), True)
        if hash_mode == "text-v1"
        else None
    )
    try:
        while chunk := stream.read(_DIGEST_CHUNK_BYTES):
            digest.update(decoder.decode(chunk).encode("utf-8") if decoder else chunk)
        if decoder:
            digest.update(decoder.decode(b"", final=True).encode("utf-8"))
    except UnicodeError:
        raise RollbackConflict("invalid_legacy_text") from None
    return digest.hexdigest()


def _snapshot(target: Path, hash_mode: str) -> _Snapshot:
    try:
        before = target.lstat()
    except FileNotFoundError:
        raise RollbackConflict("existence_mismatch") from None
    if not _regular(before):
        raise RollbackConflict("not_regular_file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    # A file changed to a FIFO must not block the rollback worker.
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(target, flags)
    except FileNotFoundError:
        raise RollbackConflict("existence_mismatch") from None
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not _regular(opened) or _identity(before) != _identity(opened):
            raise RollbackConflict("identity_mismatch")
        digest = _stream_digest(stream, hash_mode)
        security = _windows_security(target) if os.name == "nt" else _posix_xattrs(stream.fileno())
        if _identity(opened) != _identity(os.fstat(stream.fileno())):
            raise RollbackConflict("identity_mismatch")
    try:
        after = target.lstat()
    except FileNotFoundError:
        raise RollbackConflict("existence_mismatch") from None
    if _identity(before) != _identity(after):
        raise RollbackConflict("identity_mismatch")
    return _Snapshot(
        _identity(before),
        digest,
        stat.S_IMODE(before.st_mode),
        before.st_uid,
        before.st_gid,
        security,
    )


def _posix_xattrs(fd: int) -> tuple[tuple[str, bytes], ...]:
    if not hasattr(os, "listxattr"):
        # Without ACL inspection, preserving mode alone is not sufficient.
        raise OSError(errno.ENOTSUP, "rollback requires extended permission inspection")
    return tuple(sorted((name, os.getxattr(fd, name)) for name in os.listxattr(fd)))


@lru_cache(maxsize=1)
def _windows_api() -> Any:
    import ctypes as c
    from ctypes import wintypes as w

    class SecurityAttributes(c.Structure):
        _fields_ = [("length", w.DWORD), ("descriptor", c.c_void_p), ("inherit", w.BOOL)]

    kernel = c.WinDLL("kernel32", use_last_error=True)
    security = c.WinDLL("advapi32", use_last_error=True)
    specs = (
        (kernel, "GetCurrentProcess", [], w.HANDLE),
        (kernel, "CloseHandle", [w.HANDLE], w.BOOL),
        (kernel, "SetFileInformationByHandle", [w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
        (kernel, "LocalFree", [c.c_void_p], c.c_void_p),
        (kernel, "CreateDirectoryW", [w.LPCWSTR, c.c_void_p], w.BOOL),
        (
            kernel,
            "CreateFileW",
            [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE],
            w.HANDLE,
        ),
        (kernel, "MoveFileExW", [w.LPCWSTR, w.LPCWSTR, w.DWORD], w.BOOL),
        (
            kernel,
            "ReplaceFileW",
            [w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, w.DWORD, c.c_void_p, c.c_void_p],
            w.BOOL,
        ),
        (security, "OpenProcessToken", [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)], w.BOOL),
        (
            security,
            "GetTokenInformation",
            [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)],
            w.BOOL,
        ),
        (security, "ConvertSidToStringSidW", [c.c_void_p, c.POINTER(w.LPWSTR)], w.BOOL),
        (
            security,
            "ConvertStringSecurityDescriptorToSecurityDescriptorW",
            [w.LPCWSTR, w.DWORD, c.POINTER(c.c_void_p), c.POINTER(w.DWORD)],
            w.BOOL,
        ),
        (
            security,
            "ConvertSecurityDescriptorToStringSecurityDescriptorW",
            [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(w.LPWSTR), c.POINTER(w.DWORD)],
            w.BOOL,
        ),
        (
            security,
            "GetFileSecurityW",
            [w.LPCWSTR, w.DWORD, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)],
            w.BOOL,
        ),
        (security, "SetFileSecurityW", [w.LPCWSTR, w.DWORD, c.c_void_p], w.BOOL),
    )
    for library, name, arguments, result in specs:
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, result
    return SimpleNamespace(
        c=c, w=w, kernel=kernel, security=security, attributes=SecurityAttributes
    )


def _win_check(ok: Any) -> None:
    if not ok:
        api = _windows_api()
        raise api.c.WinError(api.c.get_last_error())


def _windows_sid() -> str:
    api = _windows_api()
    token = api.w.HANDLE()
    _win_check(api.security.OpenProcessToken(api.kernel.GetCurrentProcess(), 8, api.c.byref(token)))
    try:
        size = api.w.DWORD()
        api.security.GetTokenInformation(token, 1, None, 0, api.c.byref(size))
        if not size.value:
            _win_check(False)
        buffer = api.c.create_string_buffer(size.value)
        _win_check(api.security.GetTokenInformation(token, 1, buffer, size, api.c.byref(size)))
        sid = api.c.cast(buffer, api.c.POINTER(api.c.c_void_p))[0]
        text = api.w.LPWSTR()
        _win_check(api.security.ConvertSidToStringSidW(sid, api.c.byref(text)))
        try:
            return str(text.value)
        finally:
            api.kernel.LocalFree(text)
    finally:
        api.kernel.CloseHandle(token)


def _windows_security(path: Path, *, information: int = 7) -> tuple[str, bytes]:
    api = _windows_api()
    size = api.w.DWORD()
    api.security.GetFileSecurityW(str(path), information, None, 0, api.c.byref(size))
    if not size.value:
        _win_check(False)
    buffer = api.c.create_string_buffer(size.value)
    _win_check(
        api.security.GetFileSecurityW(str(path), information, buffer, size, api.c.byref(size))
    )
    return _windows_sddl(buffer, information), buffer.raw


def _windows_sddl(descriptor: Any, information: int) -> str:
    api = _windows_api()
    text = api.w.LPWSTR()
    _win_check(
        api.security.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor,
            1,
            information,
            api.c.byref(text),
            None,
        )
    )
    try:
        return str(text.value)
    finally:
        api.kernel.LocalFree(text)


def _private_directory(parent: Path) -> Path:
    if os.name == "posix":
        path = Path(tempfile.mkdtemp(prefix=".echo-rollback-", dir=parent))
        if stat.S_IMODE(path.stat().st_mode) != 0o700:
            path.rmdir()
            raise OSError(errno.EACCES, "rollback staging directory is not private")
        return path
    api = _windows_api()
    sid = _windows_sid()
    expected = f"O:{sid}D:P(A;OICI;FA;;;{sid})"
    descriptor = api.c.c_void_p()
    _win_check(
        api.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            expected,
            1,
            api.c.byref(descriptor),
            None,
        )
    )
    path = parent / (".echo-rollback-" + uuid4().hex)
    try:
        # Canonicalize current/well-known account SIDs to the OS's SDDL aliases.
        expected = _windows_sddl(descriptor, 5)
        attributes = api.attributes(api.c.sizeof(api.attributes), descriptor, False)
        _win_check(api.kernel.CreateDirectoryW(str(path), api.c.byref(attributes)))
    finally:
        api.kernel.LocalFree(descriptor)
    try:
        if _windows_security(path, information=5)[0] != expected:
            raise OSError(errno.EACCES, "rollback staging ACL was not preserved")
    except BaseException:
        path.rmdir()
        raise
    return path


def _write_staged(path: Path, content: str, snapshot: _Snapshot | None) -> None:
    fd = (
        _windows_create_private_file(path)
        if os.name == "nt"
        else os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    )
    with os.fdopen(fd, "wb") as stream:
        stream.write(content.encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
        if snapshot is not None:
            if os.name == "posix":
                current = os.fstat(stream.fileno())
                if (current.st_uid, current.st_gid) != (snapshot.uid, snapshot.gid):
                    os.fchown(stream.fileno(), snapshot.uid, snapshot.gid)
                os.fchmod(stream.fileno(), snapshot.mode)
                for name, value in snapshot.security:
                    os.setxattr(stream.fileno(), name, value)
                current = os.fstat(stream.fileno())
                if (stat.S_IMODE(current.st_mode), current.st_uid, current.st_gid) != (
                    snapshot.mode,
                    snapshot.uid,
                    snapshot.gid,
                ) or _posix_xattrs(stream.fileno()) != snapshot.security:
                    raise OSError(errno.EACCES, "rollback permissions could not be preserved")
            else:
                # Payload is complete before it becomes readable to the original
                # target's ACL audience. Keep the temporary in the same parent:
                # ReplaceFileW otherwise recomputes inheritance from staging.
                api = _windows_api()
                descriptor = api.c.create_string_buffer(snapshot.security[1])
                _win_check(api.security.SetFileSecurityW(str(path), 7, descriptor))
                if _windows_security(path)[0] != snapshot.security[0]:
                    raise OSError(errno.EACCES, "rollback staging permissions differ from original")
        os.fsync(stream.fileno())


def _windows_create_private_file(path: Path) -> int:
    import msvcrt

    api = _windows_api()
    sid = _windows_sid()
    descriptor = api.c.c_void_p()
    _win_check(
        api.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"O:{sid}D:P(A;;FA;;;{sid})",
            1,
            api.c.byref(descriptor),
            None,
        )
    )
    try:
        expected = _windows_sddl(descriptor, 5)
        attributes = api.attributes(api.c.sizeof(api.attributes), descriptor, False)
        handle = api.kernel.CreateFileW(
            str(path), 0x40000000, 3, api.c.byref(attributes), 1, 0x80, None
        )
        if handle == api.c.c_void_p(-1).value:
            _win_check(False)
    finally:
        api.kernel.LocalFree(descriptor)
    try:
        if _windows_security(path, information=5)[0] != expected:
            raise OSError(errno.EACCES, "rollback staging file is not private")
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
    except BaseException:
        api.kernel.CloseHandle(handle)
        raise
    try:
        os.set_inheritable(fd, False)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _protect_windows_evidence(path: Path) -> None:
    if not path.exists():
        return
    api = _windows_api()
    sid = _windows_sid()
    descriptor = api.c.c_void_p()
    _win_check(
        api.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"D:P(A;;FA;;;{sid})",
            1,
            api.c.byref(descriptor),
            None,
        )
    )
    try:
        expected = _windows_sddl(descriptor, 4)
        _win_check(api.security.SetFileSecurityW(str(path), 4, descriptor))
        if _windows_security(path, information=4)[0] != expected:
            raise OSError(errno.EACCES, "rollback recovery evidence ACL could not be protected")
    finally:
        api.kernel.LocalFree(descriptor)


def _publish_absent(staged: Path, target: Path) -> None:
    if os.name == "nt":
        # No REPLACE_EXISTING or COPY_ALLOWED: never clobber a concurrent file.
        api = _windows_api()
        _win_check(api.kernel.MoveFileExW(str(staged), str(target), 8))
    else:
        os.link(staged, target, follow_symlinks=False)


def _replace_existing(staged: Path, target: Path, backup: Path) -> None:
    if os.name == "nt":
        api = _windows_api()
        _win_check(api.kernel.ReplaceFileW(str(target), str(staged), str(backup), 0, None, None))
    else:
        os.replace(staged, target)


def _require_absent(target: Path) -> None:
    try:
        target.lstat()
    except FileNotFoundError:
        return
    raise RollbackConflict("existence_mismatch")


def _cleanup(staged: Path, backup: Path, directory: Path) -> None:
    # Exact owned paths only; never recursively remove a possibly changed tree.
    staged.unlink(missing_ok=True)
    backup.unlink(missing_ok=True)
    directory.rmdir()


def atomic_restore_text(
    target: Path,
    content: str,
    *,
    expected_sha256: str,
    hash_mode: str,
    require_absent: bool,
) -> None:
    """Restore UTF-8 text if the recorded target preconditions still hold.

    Parent directories must already exist. ``bytes-v1`` compares actual bytes;
    ``text-v1`` supports old universal-newline hashes. The latter does not alter
    the restored content. Originals and staging evidence survive an uncertain
    Win32 replacement failure for explicit operator reconciliation.
    """
    if os.name not in {"nt", "posix"}:
        raise OSError(errno.ENOTSUP, "atomic rollback is unsupported on this platform")
    if hash_mode not in {"bytes-v1", "text-v1"}:
        raise ValueError("unsupported rollback hash mode")
    if not isinstance(content, str) or not isinstance(require_absent, bool):
        raise TypeError("rollback requires text content and a boolean existence condition")
    target = Path(target).absolute()
    if os.name == "nt" and any(":" in part for part in target.parts[1:]):
        raise RollbackConflict("unsupported_target")
    if require_absent:
        _require_absent(target)
        snapshot = None
    else:
        if not expected_sha256:
            raise RollbackConflict("missing_precondition")
        snapshot = _snapshot(target, hash_mode)
        if snapshot.digest != expected_sha256:
            raise RollbackConflict("hash_mismatch")
    directory = _private_directory(target.parent)
    if os.name == "nt":
        staged = directory.with_suffix(".tmp")
        backup = directory.with_suffix(".bak")
    else:
        staged, backup = directory / "content.tmp", directory / "original.bak"
    committed = False
    try:
        _write_staged(staged, content, snapshot)
        if snapshot is None:
            _require_absent(target)
            try:
                _publish_absent(staged, target)
            except FileExistsError:
                raise RollbackConflict("existence_mismatch") from None
        else:
            current = _snapshot(target, hash_mode)
            if current.digest != expected_sha256:
                raise RollbackConflict("hash_mismatch")
            if current != snapshot:
                raise RollbackConflict("identity_or_permissions_changed")
            _replace_existing(staged, target, backup)
        committed = True
        if snapshot is not None and os.name == "nt":
            # ReplaceFileW may materialize inherited ACEs and change control
            # bits. Restore the exact original descriptor, then verify it.
            api = _windows_api()
            descriptor = api.c.create_string_buffer(snapshot.security[1])
            _win_check(api.security.SetFileSecurityW(str(target), 7, descriptor))
            if _windows_security(target)[0] != snapshot.security[0]:
                raise OSError(errno.EACCES, "published rollback permissions differ from original")
    except BaseException as exc:
        uncertain = os.name == "nt" and getattr(exc, "winerror", None) in {1176, 1177}
        if committed or uncertain:
            setattr(exc, "rollback_committed" if committed else "rollback_commit_uncertain", True)
            exc.rollback_backup_path = str(backup)
            exc.rollback_temporary_path = str(staged)
            _protect_retained_evidence(exc, staged, backup)
        else:
            try:
                _cleanup(staged, backup, directory)
            except OSError as cleanup_error:
                exc.add_note(f"rollback staging cleanup failed: {type(cleanup_error).__name__}")
                exc.rollback_backup_path = str(backup)
                exc.rollback_temporary_path = str(staged)
                _protect_retained_evidence(exc, staged, backup)
        raise
    try:
        _cleanup(staged, backup, directory)
    except OSError as exc:
        exc.rollback_committed = True
        exc.rollback_backup_path = str(backup)
        exc.rollback_temporary_path = str(staged)
        _protect_retained_evidence(exc, staged, backup)
        raise


def _protect_retained_evidence(exc: BaseException, staged: Path, backup: Path) -> None:
    if os.name != "nt":
        return
    try:
        _protect_windows_evidence(staged)
        _protect_windows_evidence(backup)
        exc.rollback_evidence_private = True
    except OSError as protection_error:
        exc.rollback_evidence_private = False
        exc.add_note(f"rollback evidence ACL recovery failed: {type(protection_error).__name__}")


def _mark_guarded_file_deleted(handle: int) -> None:
    api = _windows_api()
    disposition = api.w.BOOL(True)
    # FileDispositionInfo: deletion is committed when the exclusive handle
    # closes. No readonly override, POSIX emulation or path-based fallback.
    _win_check(
        api.kernel.SetFileInformationByHandle(
            handle,
            4,
            api.c.byref(disposition),
            api.c.sizeof(disposition),
        )
    )


def _close_guarded_stream(stream: Any) -> None:
    stream.close()


def guarded_remove_supported() -> bool:
    """Whether this runtime has a guarded deletion implementation.

    A supported platform may still deny a particular file (ACL, sharing or
    filesystem errors). Preview must not promise deletion on other platforms.
    """
    return os.name == "nt"


def guarded_remove_created_file(
    target: Path,
    *,
    expected_sha256: str,
    hash_mode: str,
) -> None:
    """Remove the verified Windows file through its exclusive open handle.

    Ordinary Windows writers, writable mappings, rename and deletion cannot
    coexist with this shareMode=0 handle. Busy files therefore fail closed,
    including files held by readers. This does not authorize kernel/raw-volume
    writers, and it does not claim physical power-loss durability.

    Portable POSIX has no unlink-by-verified-fd primitive. Until a supported
    platform implementation exists, POSIX raises ENOTSUP without changing any
    path; rename/check/unlink is deliberately not used as a substitute.
    """
    if hash_mode not in {"bytes-v1", "text-v1"}:
        raise ValueError("unsupported rollback hash mode")
    if not expected_sha256:
        raise RollbackConflict("missing_precondition")
    if not guarded_remove_supported():
        raise OSError(errno.ENOTSUP, "guarded rollback deletion is unsupported on this platform")
    import msvcrt

    target = Path(target).absolute()
    if any(":" in part for part in target.parts[1:]):
        raise RollbackConflict("unsupported_target")
    try:
        before = target.lstat()
    except FileNotFoundError:
        raise RollbackConflict("existence_mismatch") from None
    if not _regular(before):
        raise RollbackConflict("not_regular_file")
    api = _windows_api()
    handle = api.kernel.CreateFileW(
        str(target),
        0x80000000 | 0x00010000,
        0,
        None,
        3,
        0x00200000,
        None,
    )
    if handle == api.c.c_void_p(-1).value:
        error = api.c.get_last_error()
        if error in {2, 3}:
            raise RollbackConflict("existence_mismatch") from None
        raise api.c.WinError(error)
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        api.kernel.CloseHandle(handle)
        raise
    try:
        stream = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    marked = False
    primary_error: BaseException | None = None
    try:
        opened = os.fstat(fd)
        if not _regular(opened) or _identity(opened) != _identity(before):
            raise RollbackConflict("identity_mismatch")
        digest = _stream_digest(stream, hash_mode)
        if _identity(os.fstat(fd)) != _identity(opened):
            raise RollbackConflict("identity_mismatch")
        if digest != expected_sha256:
            raise RollbackConflict("hash_mismatch")
        # From this point through close, the same validated OS handle is used;
        # there is deliberately no final target.unlink()/DeleteFileW by name.
        _mark_guarded_file_deleted(handle)
        marked = True
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            _close_guarded_stream(stream)
        except BaseException as close_error:
            if marked:
                close_error.rollback_commit_uncertain = True
            if primary_error is None:
                raise
            primary_error.add_note(f"rollback handle close failed: {type(close_error).__name__}")


__all__ = [
    "RollbackConflict",
    "atomic_restore_text",
    "guarded_remove_created_file",
    "guarded_remove_supported",
]
