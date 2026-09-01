#!/usr/bin/env python3
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("echo-sddm-accessibility")
LOADER = importlib.machinery.SourceFileLoader("echo_sddm_accessibility", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
LOADER.exec_module(MODULE)


class SddmAccessibilityTests(unittest.TestCase):
    def test_accepts_only_the_fixed_local_display_and_private_xauthority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            authority = Path(directory) / "xauth"
            authority.write_text("cookie", encoding="utf-8")
            os.chmod(authority, 0o600)
            MODULE.validate_x11_environment(":0", authority, os.getuid())
            with self.assertRaises(ValueError):
                MODULE.validate_x11_environment("remote:0", authority, os.getuid())

    def test_rejects_symlinked_xauthority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authority = root / "xauth"
            authority.write_text("cookie", encoding="utf-8")
            link = root / "redirect"
            link.symlink_to(authority)
            with self.assertRaises(ValueError):
                MODULE.validate_x11_environment(":0", link, os.getuid())

    def test_identifies_only_local_sddm_greeter_properties(self) -> None:
        valid = "Name=sddm\nClass=greeter\nSeat=seat0\nRemote=no\nState=active\n"
        self.assertTrue(MODULE.is_sddm_greeter_properties(valid))
        self.assertFalse(
            MODULE.is_sddm_greeter_properties(valid.replace("Class=greeter", "Class=user"))
        )
        self.assertFalse(
            MODULE.is_sddm_greeter_properties(valid.replace("Remote=no", "Remote=yes"))
        )
        self.assertFalse(
            MODULE.is_sddm_greeter_properties(valid.replace("State=active", "State=closing"))
        )

    def test_orca_environment_uses_only_private_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory)
            runtime = runtime_root / str(os.getuid())
            runtime.mkdir(mode=0o700)
            os.chmod(runtime, 0o700)
            bus = socket.socket(socket.AF_UNIX)
            try:
                bus.bind(str(runtime / "bus"))
                environment = MODULE.build_orca_environment(
                    {"DISPLAY": ":0", "XAUTHORITY": "/tmp/fixed"},
                    os.getuid(),
                    runtime_root,
                )
            finally:
                bus.close()
            private = runtime / "echo-sddm-accessibility"
            self.assertEqual(environment["DBUS_SESSION_BUS_ADDRESS"], f"unix:path={runtime}/bus")
            self.assertEqual(environment["GSETTINGS_BACKEND"], "memory")
            self.assertEqual(environment["XDG_CONFIG_HOME"], str(private / "config"))
            self.assertEqual(environment["XDG_CACHE_HOME"], str(private / "cache"))

    def test_orca_command_is_absolute_and_contains_no_shell(self) -> None:
        self.assertEqual(MODULE.ORCA_COMMAND[0], "/usr/bin/orca")
        self.assertEqual(MODULE.ORCA_COMMAND[1:], (
            "--replace", "--no-setup", "--disable", "splash-window"
        ))
        self.assertNotIn("sh", MODULE.ORCA_COMMAND)
        self.assertNotIn("-c", MODULE.ORCA_COMMAND)

    def test_orca_start_fails_closed_before_invoking_without_private_runtime(self) -> None:
        invoked = False

        def forbidden_popen(*_args: object, **_kwargs: object) -> object:
            nonlocal invoked
            invoked = True
            raise AssertionError("Orca was invoked without its private runtime")

        with tempfile.TemporaryDirectory() as directory:
            controller = MODULE.OrcaController(
                os.getuid(),
                {"DISPLAY": ":0", "XAUTHORITY": "/tmp/fixed"},
                popen_factory=forbidden_popen,
                runtime_root=Path(directory),
            )
            self.assertFalse(controller.start())
            self.assertFalse(invoked)


if __name__ == "__main__":
    unittest.main()
