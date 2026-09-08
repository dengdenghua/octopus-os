"""Native Windows boundaries for private appliance state and process leases.

Credentials are created with a protected current-user DACL before any bytes
are written. Existing state is secured and its ACL read back through the same
verified handle. Reparse points, alternate streams and multiply linked files
are rejected; parent handles prevent directory replacement during an operation.
No Win32 APIs are loaded on POSIX hosts.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any


@lru_cache(maxsize=1)
def _api() -> Any:
    import ctypes as c
    from ctypes import wintypes as w

    class FileInfo(c.Structure):
        _fields_ = [
            ("attributes", w.DWORD),
            ("creation", w.FILETIME),
            ("access", w.FILETIME),
            ("write", w.FILETIME),
            ("volume", w.DWORD),
            ("size_high", w.DWORD),
            ("size_low", w.DWORD),
            ("links", w.DWORD),
            ("index_high", w.DWORD),
            ("index_low", w.DWORD),
        ]

    class SecurityAttributes(c.Structure):
        _fields_ = [("length", w.DWORD), ("descriptor", c.c_void_p), ("inherit", w.BOOL)]

    class Overlapped(c.Structure):
        _fields_ = [
            ("internal", c.c_size_t),
            ("internal_high", c.c_size_t),
            ("offset", w.DWORD),
            ("offset_high", w.DWORD),
            ("event", w.HANDLE),
        ]

    class Acl(c.Structure):
        _fields_ = [
            ("revision", w.BYTE),
            ("reserved", w.BYTE),
            ("size", w.WORD),
            ("count", w.WORD),
            ("reserved2", w.WORD),
        ]

    kernel = c.WinDLL("kernel32", use_last_error=True)
    security = c.WinDLL("advapi32", use_last_error=True)
    specs = (
        (kernel, "GetCurrentProcess", [], w.HANDLE),
        (kernel, "CloseHandle", [w.HANDLE], w.BOOL),
        (kernel, "LocalFree", [c.c_void_p], c.c_void_p),
        (
            kernel,
            "CreateFileW",
            [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE],
            w.HANDLE,
        ),
        (kernel, "CreateDirectoryW", [w.LPCWSTR, c.c_void_p], w.BOOL),
        (kernel, "GetFileInformationByHandle", [w.HANDLE, c.POINTER(FileInfo)], w.BOOL),
        (kernel, "GetFileType", [w.HANDLE], w.DWORD),
        (kernel, "GetFinalPathNameByHandleW", [w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD], w.DWORD),
        (
            kernel,
            "LockFileEx",
            [w.HANDLE, w.DWORD, w.DWORD, w.DWORD, w.DWORD, c.POINTER(Overlapped)],
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
            "GetSecurityDescriptorOwner",
            [c.c_void_p, c.POINTER(c.c_void_p), c.POINTER(w.BOOL)],
            w.BOOL,
        ),
        (
            security,
            "GetSecurityDescriptorDacl",
            [c.c_void_p, c.POINTER(w.BOOL), c.POINTER(c.c_void_p), c.POINTER(w.BOOL)],
            w.BOOL,
        ),
        (
            security,
            "GetSecurityDescriptorControl",
            [c.c_void_p, c.POINTER(w.WORD), c.POINTER(w.DWORD)],
            w.BOOL,
        ),
        (
            security,
            "GetSecurityInfo",
            [
                w.HANDLE,
                c.c_int,
                w.DWORD,
                c.c_void_p,
                c.c_void_p,
                c.c_void_p,
                c.c_void_p,
                c.POINTER(c.c_void_p),
            ],
            w.DWORD,
        ),
        (
            security,
            "SetSecurityInfo",
            [w.HANDLE, c.c_int, w.DWORD, c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p],
            w.DWORD,
        ),
        (security, "SetFileSecurityW", [w.LPCWSTR, w.DWORD, c.c_void_p], w.BOOL),
        (security, "GetAce", [c.c_void_p, w.DWORD, c.POINTER(c.c_void_p)], w.BOOL),
    )
    for library, name, arguments, result in specs:
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, result
    return SimpleNamespace(
        c=c,
        w=w,
        kernel=kernel,
        security=security,
        FileInfo=FileInfo,
        SecurityAttributes=SecurityAttributes,
        Overlapped=Overlapped,
        Acl=Acl,
    )


def _check(ok: Any) -> None:
    if not ok:
        api = _api()
        raise api.c.WinError(api.c.get_last_error())


def _sid_text(sid: Any) -> str:
    api = _api()
    text = api.w.LPWSTR()
    _check(api.security.ConvertSidToStringSidW(sid, api.c.byref(text)))
    try:
        return str(text.value)
    finally:
        api.kernel.LocalFree(text)


@lru_cache(maxsize=1)
def current_user_sid() -> str:
    """The process user's SID, never a username or an Administrators-group grant."""
    api = _api()
    token = api.w.HANDLE()
    _check(api.security.OpenProcessToken(api.kernel.GetCurrentProcess(), 0x8, api.c.byref(token)))
    try:
        size = api.w.DWORD()
        api.security.GetTokenInformation(token, 1, None, 0, api.c.byref(size))  # TokenUser
        if not size.value:
            raise OSError("cannot identify the Windows state owner")
        buffer = api.c.create_string_buffer(size.value)
        _check(api.security.GetTokenInformation(token, 1, buffer, size, api.c.byref(size)))
        return _sid_text(api.c.cast(buffer, api.c.POINTER(api.c.c_void_p))[0])
    finally:
        api.kernel.CloseHandle(token)


@contextlib.contextmanager
def _descriptor(*, directory: bool) -> Iterator[Any]:
    api = _api()
    pointer = api.c.c_void_p()
    sid = current_user_sid()
    flags = "OICI" if directory else ""
    sddl = f"O:{sid}D:P(A;{flags};FA;;;{sid})"
    _check(
        api.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, api.c.byref(pointer), None
        )
    )
    try:
        yield pointer
    finally:
        api.kernel.LocalFree(pointer)


def _security_parts(descriptor: Any) -> tuple[Any, Any]:
    api = _api()
    owner, dacl = api.c.c_void_p(), api.c.c_void_p()
    defaulted, present = api.w.BOOL(), api.w.BOOL()
    _check(
        api.security.GetSecurityDescriptorOwner(
            descriptor, api.c.byref(owner), api.c.byref(defaulted)
        )
    )
    _check(
        api.security.GetSecurityDescriptorDacl(
            descriptor, api.c.byref(present), api.c.byref(dacl), api.c.byref(defaulted)
        )
    )
    if not present.value or not dacl.value:
        raise OSError("Windows state requires a non-null DACL")
    return owner, dacl


def _verify_private(handle: int, *, directory: bool) -> None:
    api = _api()
    descriptor = api.c.c_void_p()
    error = api.security.GetSecurityInfo(
        handle, 1, 0x5, None, None, None, None, api.c.byref(descriptor)
    )
    if error:
        raise api.c.WinError(error)
    try:
        owner, dacl = _security_parts(descriptor)
        control, revision = api.w.WORD(), api.w.DWORD()
        _check(
            api.security.GetSecurityDescriptorControl(
                descriptor, api.c.byref(control), api.c.byref(revision)
            )
        )
        acl = api.c.cast(dacl, api.c.POINTER(api.Acl)).contents
        if _sid_text(owner) != current_user_sid() or not control.value & 0x1000 or acl.count != 1:
            raise OSError("Windows state owner or protected DACL is not private")
        ace = api.c.c_void_p()
        _check(api.security.GetAce(dacl, 0, api.c.byref(ace)))
        address = ace.value
        assert address is not None
        kind = api.c.c_ubyte.from_address(address).value
        flags = api.c.c_ubyte.from_address(address + 1).value
        mask = api.w.DWORD.from_address(address + 4).value
        if (
            kind != 0
            or flags != (0x3 if directory else 0)
            or mask != 0x1F01FF
            or _sid_text(address + 8) != current_user_sid()
        ):
            raise OSError("Windows state ACL grants access beyond its current user")
    finally:
        api.kernel.LocalFree(descriptor)


def _protect(handle: int, *, directory: bool, pinned_path: Path | None = None) -> None:
    api = _api()
    with _descriptor(directory=directory) as descriptor:
        owner, dacl = _security_parts(descriptor)
        if directory:
            # SetSecurityInfo recursively propagates to EXISTING children,
            # potentially changing an outside inode reached by a hard link.
            # This narrowly scoped legacy API changes only the named object.
            # All ancestors and this exact directory are pinned without delete
            # sharing, so its verified pathname cannot be replaced meanwhile.
            # Newly created children still inherit its OI/CI rule normally.
            assert pinned_path is not None
            _check(
                api.security.SetFileSecurityW("\\\\?\\" + str(pinned_path), 0x80000005, descriptor)
            )
        else:
            error = api.security.SetSecurityInfo(handle, 1, 0x80000005, owner, None, dacl, None)
            if error:
                raise api.c.WinError(error)
    _verify_private(handle, directory=directory)


def _absolute(path: Path | str) -> Path:
    path = Path(os.path.abspath(path))  # lexical only: never resolve a junction
    if not path.drive or path.drive.startswith("\\"):
        raise ValueError("private Windows state requires a local drive")
    if len(path.parts) < 2:
        raise ValueError("the Windows drive root cannot be an appliance state directory")
    for part in path.parts[1:]:
        stem = part.split(".", 1)[0].upper()
        if (
            ":" in part
            or part.endswith((" ", "."))
            or "\0" in part
            or stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
            or (len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3] in "123456789¹²³")
        ):
            raise ValueError("Windows state path contains a stream or device alias")
    return path


def _open(
    path: Path, *, directory: bool, access: int, create: int = 3, descriptor: Any = None
) -> int:
    api = _api()
    attributes = None
    if descriptor is not None:
        attributes = api.SecurityAttributes(api.c.sizeof(api.SecurityAttributes), descriptor, False)
    raw = "\\\\?\\" + str(path)
    handle = api.kernel.CreateFileW(
        raw, access, 0x3, api.c.byref(attributes) if attributes else None, create, 0x02200000, None
    )
    if handle == api.c.c_void_p(-1).value:
        raise api.c.WinError(api.c.get_last_error())
    try:
        info = api.FileInfo()
        _check(api.kernel.GetFileInformationByHandle(handle, api.c.byref(info)))
        if (
            info.attributes & 0x400
            or bool(info.attributes & 0x10) != directory
            or api.kernel.GetFileType(handle) != 1
            or (not directory and info.links != 1)
        ):
            raise OSError("Windows state contains a link or unexpected file type")
        buffer = api.c.create_unicode_buffer(32768)
        length = api.kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if (
            not length
            or length >= len(buffer)
            or buffer.value.rstrip("\\").casefold() != raw.rstrip("\\").casefold()
        ):
            raise OSError("Windows state path identity changed")
        return handle
    except BaseException:
        api.kernel.CloseHandle(handle)
        raise


@contextlib.contextmanager
def private_state_directory(
    path: Path | str, *, create: bool = False, protect: bool = True
) -> Iterator[Path]:
    """Pin all ancestors; secure only the explicitly selected state directory."""
    api = _api()
    target = _absolute(path)
    held = []
    current = Path(target.anchor)
    try:
        for part in (None, *target.parts[1:]):
            if part is not None:
                current /= part
            final = current == target
            if create and part is not None:
                with _descriptor(directory=True) as descriptor:
                    attributes = api.SecurityAttributes(
                        api.c.sizeof(api.SecurityAttributes), descriptor, False
                    )
                    if not api.kernel.CreateDirectoryW(
                        "\\\\?\\" + str(current), api.c.byref(attributes)
                    ):
                        error = api.c.get_last_error()
                        if error != 183:  # ERROR_ALREADY_EXISTS; verified through the handle below
                            raise api.c.WinError(error)
            access = 0x80000000 | (0x000E0000 if final and protect else 0)
            handle = _open(current, directory=True, access=access)
            held.append(handle)
            if final and protect:
                _protect(handle, directory=True, pinned_path=current)
        yield target
    finally:
        for handle in reversed(held):
            api.kernel.CloseHandle(handle)


def open_private_file(
    path: Path | str, *, create_new: bool = False, lock_file: bool = False
) -> int:
    """Return a non-inheritable fd owning a vetted, private regular file handle.

    The caller must hold private_state_directory(path.parent) throughout use.
    """
    import msvcrt

    api = _api()
    access = 0x800E0000 | (0x40000000 if create_new or lock_file else 0)
    with _descriptor(directory=False) as descriptor:
        handle = _open(
            _absolute(path),
            directory=False,
            access=access,
            create=1 if create_new else 4 if lock_file else 3,
            descriptor=descriptor,
        )
    try:
        _protect(handle, directory=False)
        fd = msvcrt.open_osfhandle(
            handle, (os.O_RDWR if create_new or lock_file else os.O_RDONLY) | os.O_BINARY
        )
        handle = None
        try:
            os.set_inheritable(fd, False)
        except BaseException:
            os.close(fd)
            raise
        return fd
    finally:
        if handle is not None:
            api.kernel.CloseHandle(handle)


def lock_descriptor(descriptor: int, *, exclusive: bool) -> None:
    import msvcrt

    api = _api()
    overlap = api.Overlapped()
    _check(
        api.kernel.LockFileEx(
            msvcrt.get_osfhandle(descriptor),
            0x1 | (0x2 if exclusive else 0),
            0,
            1,
            0,
            api.c.byref(overlap),
        )
    )


__all__ = ["current_user_sid", "lock_descriptor", "open_private_file", "private_state_directory"]
