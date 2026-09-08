"""Native RAM fallbacks must work in installs without optional psutil."""

from __future__ import annotations

import ctypes
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from runtime.platform import system_memory


@pytest.fixture(autouse=True)
def no_psutil(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", None)


def _windows_api(monkeypatch, *, total=32 * 1024**3, success=True):
    def query(pointer):
        status = ctypes.cast(pointer, ctypes.POINTER(system_memory._MemoryStatusEx)).contents
        assert status.dwLength == 64
        status.ullTotalPhys = total
        status.ullAvailPhys = 2 * 1024**3
        return int(success)

    monkeypatch.setattr(system_memory.platform, "system", lambda: "Windows")
    loader = Mock(return_value=SimpleNamespace(GlobalMemoryStatusEx=Mock(side_effect=query)))
    monkeypatch.setattr(ctypes, "WinDLL", loader, raising=False)
    return loader


def test_windows_without_psutil_supplies_both_recommenders(monkeypatch):
    from runtime.core.cerebrum import ai_mode
    from runtime.sensing.model_router import hwfit

    loader = _windows_api(monkeypatch)
    monkeypatch.setattr(system_memory, "available_memory_gb", lambda: 24.0)
    monkeypatch.setattr(hwfit, "_detect_nvidia", lambda: (0.0, None))
    monkeypatch.setattr(hwfit, "_detect_apple", lambda: (None, None))
    assert ai_mode._detect_ram_gb() == 32.0
    hardware = hwfit.detect_hardware()
    assert hardware.ram_gb == 32.0
    assert hardware.backend == "cpu"
    assert hwfit.recommend(hardware)
    loader.assert_called_with("kernel32", use_last_error=True)


@pytest.mark.parametrize("success,total", [(False, 32 * 1024**3), (True, 0)])
def test_failed_windows_probe_does_not_report_a_capacity(monkeypatch, success, total):
    _windows_api(monkeypatch, total=total, success=success)
    assert system_memory.total_memory_gb() == 0.0


def test_psutil_failure_uses_native_fallback(monkeypatch):
    _windows_api(monkeypatch)
    monkeypatch.setitem(
        sys.modules, "psutil", SimpleNamespace(virtual_memory=Mock(side_effect=OSError("denied")))
    )
    assert system_memory.total_memory_gb() == 32.0


def test_psutil_capacity_takes_precedence(monkeypatch):
    loader = _windows_api(monkeypatch)
    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(virtual_memory=lambda: SimpleNamespace(total=16 * 1024**3)),
    )
    assert system_memory.total_memory_gb() == 16.0
    loader.assert_not_called()


@pytest.mark.parametrize(
    "contents,expected", [("MemTotal: 8388608 kB\n", 8.0), ("MemTotal:\n", 0.0)]
)
def test_linux_fallback(monkeypatch, contents, expected):
    monkeypatch.setattr(system_memory.platform, "system", lambda: "Linux")
    monkeypatch.setattr(system_memory.Path, "read_text", lambda *args, **kwargs: contents)
    assert system_memory.total_memory_gb() == expected


def test_macos_fallback(monkeypatch):
    monkeypatch.setattr(system_memory.platform, "system", lambda: "Darwin")
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=str(24 * 1024**3)))
    monkeypatch.setattr(system_memory.subprocess, "run", run)
    assert system_memory.total_memory_gb() == 24.0
    assert run.call_args.args == (["sysctl", "-n", "hw.memsize"],)
    assert run.call_args.kwargs["timeout"] == 2.5


def test_native_probe_error_returns_unknown(monkeypatch):
    loader = _windows_api(monkeypatch)
    loader.side_effect = OSError("probe unavailable")
    assert system_memory.total_memory_gb() == 0.0
