"""Open one vetted photo without following links or re-opening its pathname.

POSIX uses descriptor-relative O_NOFOLLOW traversal. Windows pins every parent
with a non-delete-sharing handle, opens reparse points themselves, and verifies
the final handle's path/type before converting that handle into a Python stream.
No bytes are read until all checks complete.
"""

from __future__ import annotations

import contextlib
import os
import stat
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO


def is_link_or_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


@lru_cache(maxsize=1)
def _windows_api() -> Any:
    import ctypes
    from ctypes import wintypes

    class FileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation", wintypes.FILETIME),
            ("access", wintypes.FILETIME),
            ("write", wintypes.FILETIME),
            ("volume", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD),
            ("index_low", wintypes.DWORD),
        ]

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    api.CreateFileW.restype = wintypes.HANDLE
    api.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FileInformation),
    ]
    api.GetFileInformationByHandle.restype = wintypes.BOOL
    api.GetFileType.argtypes = [wintypes.HANDLE]
    api.GetFileType.restype = wintypes.DWORD
    api.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    api.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.file_information_type = FileInformation
    return api


def safe_file_access_available() -> bool:
    if os.name == "nt":
        try:
            _windows_api()
            return True
        except (ImportError, AttributeError, OSError):
            return False
    return bool(
        hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd
    )


def _validate_parts(parts: tuple[str, ...]) -> None:
    if not parts or len(parts) > 256:
        raise ValueError("invalid photo path")
    for part in parts:
        if not part or part in {".", ".."} or any(char in part for char in "\0/\\"):
            raise ValueError("invalid photo path component")
        if os.name == "nt":
            stem = part.split(".", 1)[0].upper()
            if (
                ":" in part
                or part.endswith((" ", "."))
                or stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
                or (len(stem) == 4 and stem[:3] in {"COM", "LPT"} and stem[3] in "123456789¹²³")
            ):
                raise ValueError("Windows stream and device aliases are not photo paths")


def _extended_windows_path(path: Path) -> str:
    raw = str(path)
    if raw.startswith("\\\\?\\"):
        return raw
    if raw.startswith("\\\\"):
        return "\\\\?\\UNC\\" + raw[2:]
    return "\\\\?\\" + raw


def _open_windows_component(path: Path, *, directory: bool) -> int:
    import ctypes

    api = _windows_api()
    requested_path = _extended_windows_path(path)
    handle = api.CreateFileW(
        requested_path,
        # Metadata-only access does not participate in Windows share checks.
        # GENERIC_READ makes the no-delete/no-write reservation effective for
        # directories too, so they cannot be swapped before the next open.
        0x80000000,  # GENERIC_READ
        0x1,  # FILE_SHARE_READ; writers, deletion and rename cannot overlap this read
        None,
        3,  # OPEN_EXISTING
        0x00200000 | 0x02000000,  # OPEN_REPARSE_POINT | BACKUP_SEMANTICS
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if error in {2, 3}:
            raise FileNotFoundError("photo not found")
        raise ctypes.WinError(error)
    try:
        info = api.file_information_type()
        if not api.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
            raise OSError("photo path contains a link or unexpected file type")
        if api.GetFileType(handle) != 1:  # FILE_TYPE_DISK
            raise OSError("photo path is not a disk file")
        buffer = ctypes.create_unicode_buffer(32_768)
        length = api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if length == 0 or length >= len(buffer):
            raise OSError("photo path identity could not be verified")
        if buffer.value.rstrip("\\").casefold() != requested_path.rstrip("\\").casefold():
            raise OSError("photo path identity changed")
        return handle
    except BaseException:
        api.CloseHandle(handle)
        raise


def _open_windows(root: Path, parts: tuple[str, ...]) -> int:
    import msvcrt

    api = _windows_api()
    held: list[int] = []
    file_handle: int | None = None
    try:
        # Pin the absolute root's ancestors as well as descendants. Keeping
        # them open without FILE_SHARE_DELETE prevents a pathname swap while
        # the next component is opened and verified.
        current = Path(root.anchor)
        held.append(_open_windows_component(current, directory=True))
        for part in (*root.parts[1:], *parts[:-1]):
            current = current / part
            held.append(_open_windows_component(current, directory=True))
        file_handle = _open_windows_component(current / parts[-1], directory=False)
        descriptor = msvcrt.open_osfhandle(file_handle, os.O_RDONLY | os.O_BINARY)
        file_handle = None  # The descriptor now owns this exact verified handle.
        return descriptor
    finally:
        if file_handle is not None:
            api.CloseHandle(file_handle)
        for handle in reversed(held):
            api.CloseHandle(handle)


def _open_posix(root: Path, parts: tuple[str, ...]) -> int:
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    finally:
        os.close(directory)


def open_safe_photo(
    root: Path, parts: tuple[str, ...], *, max_bytes: int
) -> tuple[BinaryIO, os.stat_result]:
    """Return the verified open object; callers must close the returned stream."""

    _validate_parts(parts)
    if not root.is_absolute() or not safe_file_access_available():
        raise OSError("safe photo access is unavailable")
    descriptor = _open_windows(root, parts) if os.name == "nt" else _open_posix(root, parts)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
            raise OSError("photo is not a bounded regular file")
        stream = os.fdopen(descriptor, "rb")
        descriptor = -1
        return stream, info
    finally:
        if descriptor >= 0:
            with contextlib.suppress(OSError):
                os.close(descriptor)


__all__ = ["is_link_or_reparse", "open_safe_photo", "safe_file_access_available"]
