#!/usr/bin/python3
"""Security-boundary tests for the windowless Klipper host."""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import io
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr


HOST_PATH = Path(__file__).with_name("echo-clipboard-host")
SPEC = importlib.util.spec_from_loader(
    "echo_clipboard_host", SourceFileLoader("echo_clipboard_host", str(HOST_PATH))
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {HOST_PATH}")
HOST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOST)


class ClipboardHostPolicyTests(unittest.TestCase):
    def make_runtime(self, root: Path) -> Path:
        runtime = root / "runtime"
        runtime.mkdir(mode=0o700)
        os.chmod(runtime, 0o700)
        return runtime

    def test_accepts_only_fixed_runtime_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = self.make_runtime(Path(temp))
            database = runtime / "echo-os" / "clipboard" / "history3.sqlite"
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
                self.assertEqual(HOST.prepare_runtime_database(str(database)), database)
            self.assertEqual((runtime / "echo-os").stat().st_mode & 0o777, 0o700)
            self.assertEqual(database.parent.stat().st_mode & 0o777, 0o700)

    def test_rejects_persistent_home_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = self.make_runtime(root)
            persistent = root / "home" / "klipper" / "history3.sqlite"
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        HOST.prepare_runtime_database(str(persistent))

    def test_rejects_symlinked_runtime_child_without_chmodding_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = self.make_runtime(root)
            outside = root / "outside"
            outside.mkdir(mode=0o755)
            os.chmod(outside, 0o755)
            (runtime / "echo-os").symlink_to(outside, target_is_directory=True)
            database = runtime / "echo-os" / "clipboard" / "history3.sqlite"
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        HOST.prepare_runtime_database(str(database))
            self.assertEqual(outside.stat().st_mode & 0o777, 0o755)

    def test_rejects_world_readable_existing_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = self.make_runtime(Path(temp))
            database = runtime / "echo-os" / "clipboard" / "history3.sqlite"
            database.parent.mkdir(parents=True, mode=0o700)
            database.write_bytes(b"")
            os.chmod(database, 0o644)
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
                with redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        HOST.prepare_runtime_database(str(database))

    def test_wayland_environment_requires_owned_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = self.make_runtime(Path(temp))
            socket_path = runtime / "wayland-echo-test"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(socket_path))
                with mock.patch.dict(
                    os.environ,
                    {
                        "XDG_RUNTIME_DIR": str(runtime),
                        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/echo-test-bus",
                        "QT_QPA_PLATFORM": "wayland",
                        "WAYLAND_DISPLAY": socket_path.name,
                    },
                    clear=True,
                ):
                    HOST.validate_session_environment("wayland")
            finally:
                listener.close()


if __name__ == "__main__":
    unittest.main()
