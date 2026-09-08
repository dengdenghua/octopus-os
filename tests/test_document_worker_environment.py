"""Worker environment contracts; real frozen Linux coverage runs separately."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from runtime.execution.misc import document_extraction as extraction


def _seed(monkeypatch):
    monkeypatch.setattr(
        extraction.os,
        "environ",
        {
            "LANG": "C.UTF-8",
            "TEMP": "/tmp",
            "OPENAI_API_KEY": "synthetic-secret",
            "PATH": "/untrusted/bin",
            "PYTHONPATH": "/untrusted/python",
            "LD_LIBRARY_PATH": "/untrusted/lib:/another/lib",
            "LD_LIBRARY_PATH_ORIG": "/inherited/lib",
            "LD_PRELOAD": "/untrusted/inject.so",
            "LD_AUDIT": "/untrusted/audit.so",
            "_PYI_ARCHIVE_FILE": "/opt/synthetic/service",
            "_PYI_PARENT_PROCESS_LEVEL": "1",
            "_PYI_APPLICATION_HOME_DIR": "/tmp/bootloader-owned",
            "PYINSTALLER_RESET_ENVIRONMENT": "1",
        },
    )


def test_source_environment_never_inherits_loader_or_bootloader_variables(monkeypatch):
    _seed(monkeypatch)
    monkeypatch.setattr(extraction.sys, "frozen", False, raising=False)
    assert extraction._worker_environment() == {"LANG": "C.UTF-8", "TEMP": "/tmp"}


def test_windows_frozen_environment_keeps_existing_reset_contract(monkeypatch):
    _seed(monkeypatch)
    monkeypatch.setattr(extraction.sys, "frozen", True, raising=False)
    monkeypatch.setattr(extraction.sys, "platform", "win32")
    assert extraction._worker_environment() == {
        "LANG": "C.UTF-8",
        "TEMP": "/tmp",
        "_PYI_ARCHIVE_FILE": "/opt/synthetic/service",
        "_PYI_PARENT_PROCESS_LEVEL": "1",
        "_PYI_APPLICATION_HOME_DIR": "/tmp/bootloader-owned",
        "PYINSTALLER_RESET_ENVIRONMENT": "1",
    }


@pytest.mark.skipif(sys.platform != "linux", reason="requires real Linux ownership and mode")
def test_linux_frozen_environment_reuses_bootloader_and_only_trusted_loader_path(
    tmp_path, monkeypatch
):
    _seed(monkeypatch)
    bundle = tmp_path / "_MEI-private"
    bundle.mkdir(mode=0o700)
    monkeypatch.setattr(extraction.sys, "frozen", True, raising=False)
    monkeypatch.setattr(extraction.sys, "_MEIPASS", str(bundle), raising=False)
    value = extraction._worker_environment()
    assert value == {
        "LANG": "C.UTF-8",
        "TEMP": "/tmp",
        "_PYI_ARCHIVE_FILE": "/opt/synthetic/service",
        "_PYI_PARENT_PROCESS_LEVEL": "1",
        "_PYI_APPLICATION_HOME_DIR": "/tmp/bootloader-owned",
        "LD_LIBRARY_PATH": str(bundle),
    }


@pytest.mark.skipif(sys.platform != "linux", reason="requires real Linux ownership and mode")
@pytest.mark.parametrize(
    "kind",
    ["missing", "relative", "file", "symlink", "writable", "parent_writable", "colon", "foreign"],
)
def test_linux_frozen_environment_rejects_untrusted_bundle(tmp_path, monkeypatch, kind):
    _seed(monkeypatch)
    bundle = tmp_path / "_MEI-private"
    bundle.mkdir(mode=0o700)
    value = str(bundle)
    if kind == "missing":
        value = str(tmp_path / "absent")
    elif kind == "relative":
        value = "relative-bundle"
    elif kind == "file":
        (tmp_path / "file").write_text("synthetic")
        value = str(tmp_path / "file")
    elif kind == "symlink":
        (tmp_path / "link").symlink_to(bundle, target_is_directory=True)
        value = str(tmp_path / "link")
    elif kind == "writable":
        bundle.chmod(0o777)
    elif kind == "parent_writable":
        tmp_path.chmod(0o777)
    elif kind == "colon":
        value = str(bundle) + ":/untrusted"
    elif kind == "foreign":
        original_stat = Path.stat

        def other_owner(path, *args, **kwargs):
            metadata = original_stat(path, *args, **kwargs)
            if path == bundle:
                values = list(metadata)
                values[4] = os.geteuid() + 12345
                return os.stat_result(values)
            return metadata

        monkeypatch.setattr(Path, "stat", other_owner)
    monkeypatch.setattr(extraction.sys, "frozen", True, raising=False)
    monkeypatch.setattr(extraction.sys, "_MEIPASS", value, raising=False)
    with pytest.raises((OSError, ValueError)):
        extraction._worker_environment()
