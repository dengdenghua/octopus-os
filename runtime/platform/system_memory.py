"""Best-effort physical memory detection without requiring optional psutil."""

from __future__ import annotations

import ctypes
import platform
import subprocess
from pathlib import Path


class _MemoryStatusEx(ctypes.Structure):
    # Fixed widths keep the Windows ABI intact, including in cross-platform tests.
    # https://learn.microsoft.com/windows/win32/api/sysinfoapi/ns-sysinfoapi-memorystatusex
    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def _windows_memory_status() -> _MemoryStatusEx:
    query = ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx
    query.argtypes = [ctypes.POINTER(_MemoryStatusEx)]
    query.restype = ctypes.c_int
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(status)
    if not query(ctypes.byref(status)):
        raise OSError("GlobalMemoryStatusEx failed")
    return status


def _windows_total_memory_bytes() -> int:
    return int(_windows_memory_status().ullTotalPhys)


def available_memory_gb() -> float | None:
    """Currently available physical RAM, keeping unknown distinct from zero."""
    try:
        import psutil

        return max(0, int(psutil.virtual_memory().available)) / (1024**3)
    except Exception:
        pass
    try:
        if platform.system() == "Windows":
            return _windows_memory_status().ullAvailPhys / (1024**3)
        if platform.system() == "Linux":
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemAvailable:"):
                    return max(0, int(line.split()[1])) / (1024**2)
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=2.5, check=False
            )
            if result.returncode == 0:
                import re

                page = re.search(r"page size of (\d+) bytes", result.stdout)
                counters = dict(re.findall(r"(Pages [\w ]+):\s+(\d+)", result.stdout))
                if page and "Pages free" in counters:
                    # Conservative: do not count active, wired or compressed pages.
                    pages = sum(
                        int(counters.get(k, 0))
                        for k in ("Pages free", "Pages inactive", "Pages speculative")
                    )
                    return pages * int(page[1]) / (1024**3)
    except (OSError, ValueError, IndexError, AttributeError, subprocess.SubprocessError):
        pass
    return None


def total_memory_gb() -> float:
    """Return total physical RAM in GiB, or 0 when detection is unavailable.

    Both model recommendations and AI-mode device summaries use this probe.
    It reports capacity, not current free memory or an inference safety limit.
    """
    try:
        import psutil  # type: ignore[import-untyped]

        total = int(psutil.virtual_memory().total)
        if total > 0:
            return total / (1024**3)
    except Exception:  # Optional dependency may be absent or unable to query the OS.
        pass

    try:
        system = platform.system()
        if system == "Windows":
            return max(0, _windows_total_memory_bytes()) / (1024**3)
        if system == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=2.5,
                check=False,
            )
            if result.returncode == 0:
                return max(0, int(result.stdout.strip())) / (1024**3)
        if system == "Linux":
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    return max(0, int(line.split()[1])) / (1024**2)
    except (OSError, ValueError, IndexError, AttributeError, subprocess.SubprocessError):
        pass
    return 0.0
