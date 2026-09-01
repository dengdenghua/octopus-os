#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

MODULE_PATH = Path(__file__).with_name("echo_oem_setup.py")
SPEC = importlib.util.spec_from_file_location("echo_oem_setup", MODULE_PATH)
assert SPEC and SPEC.loader
oem = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oem)
TEST_HASH = "$y$j9T$testsalt$0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
CURRENT_HASH = "$y$j9T$current$ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz"
VALID_OEM_CREDENTIAL = {
    "schema": 1,
    "display_name": "Echo CI",
    "hostname": "echo-oem-ci",
    "password": "correct horse battery 42",
    "locale": "zh_CN.UTF-8",
    "keymap": "us",
    "timezone": "Asia/Shanghai",
}


class InputValidationTests(unittest.TestCase):
    def test_display_name_normalizes_and_rejects_gecos_delimiters(self) -> None:
        self.assertEqual(oem.validate_display_name("  Écho 用户  "), "Écho 用户")
        for value in ("", "bad:name", "bad,name", "bad\nname", "x" * 65):
            with self.subTest(value=value), self.assertRaises(oem.InputError):
                oem.validate_display_name(value)

    def test_hostname_is_a_single_safe_dns_label(self) -> None:
        self.assertEqual(oem.validate_hostname(" Echo-Lab-01 "), "echo-lab-01")
        self.assertEqual(oem.validate_hostname("x" * 15), "x" * 15)
        for value in ("", "-echo", "echo-", "echo.local", "echo_os", "x" * 16, "x" * 64):
            with self.subTest(value=value), self.assertRaises(oem.InputError):
                oem.validate_hostname(value)

    def test_password_has_length_control_and_predictability_guards(self) -> None:
        self.assertEqual(oem.validate_password("correct horse 电池 42"), "correct horse 电池 42")
        for value in (
            "short",
            "a" * 12,
            "passwordpassword",
            "echo-admin-42",
            "good-password\n42",
        ):
            with self.subTest(value=value), self.assertRaises(oem.InputError):
                oem.validate_password(value)


class MarkerTests(unittest.TestCase):
    def test_completion_marker_is_atomic_private_and_contains_no_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with (
                mock.patch.object(oem, "STATE_DIRECTORY", state),
                mock.patch.object(oem, "COMPLETE_MARKER", state / "oem-complete.json"),
                mock.patch.object(oem, "current_image_version", return_value="0.2.0"),
            ):
                oem.write_complete_marker("Echo User", "echo-lab")
                marker = state / "oem-complete.json"
                payload = json.loads(marker.read_text(encoding="utf-8"))
                self.assertEqual(payload["account"], "echo")
                self.assertEqual(payload["hostname"], "echo-lab")
                self.assertEqual(payload["root_version"], "0.2.0")
                self.assertNotIn("password", payload)
                self.assertEqual(os.stat(marker).st_mode & 0o777, 0o600)
                self.assertEqual(list(state.glob("*.tmp")), [])

    def test_password_hash_uses_a_separate_private_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with (
                mock.patch.object(oem, "STATE_DIRECTORY", state),
                mock.patch.object(oem, "SHADOW_STATE", state / "local-account.shadow"),
            ):
                oem.write_shadow_state(TEST_HASH)
                secret = state / "local-account.shadow"
                self.assertEqual(secret.read_text(encoding="utf-8"), TEST_HASH + "\n")
                self.assertEqual(os.stat(secret).st_mode & 0o777, 0o600)

    def test_locked_or_malformed_password_hash_is_never_persisted(self) -> None:
        for value in (
            "",
            "!",
            "!*",
            "*",
            "bad:hash",
            "short",
            "$6$bad hash with enough characters to pass length",
        ):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                oem.validate_password_hash(value)

    def test_persistent_identity_files_must_be_root_owned_mode_0600(self) -> None:
        path = Path("/var/lib/echo-os/local-account.shadow")
        invalid = (
            SimpleNamespace(st_mode=oem.stat.S_IFREG | 0o600, st_uid=1000),
            SimpleNamespace(st_mode=oem.stat.S_IFREG | 0o644, st_uid=0),
            SimpleNamespace(st_mode=oem.stat.S_IFDIR | 0o600, st_uid=0),
        )
        for metadata in invalid:
            with (
                self.subTest(metadata=metadata),
                mock.patch.object(oem.Path, "stat", return_value=metadata),
                self.assertRaises(RuntimeError),
            ):
                oem.require_private_root_file(path)


class SystemCredentialTests(unittest.TestCase):
    def test_private_systemd_credential_is_strictly_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            credential = Path(directory) / oem.OEM_CREDENTIAL_NAME
            credential.write_text(json.dumps(VALID_OEM_CREDENTIAL), encoding="utf-8")
            credential.chmod(0o600)
            with mock.patch.dict(oem.os.environ, {"CREDENTIALS_DIRECTORY": directory}, clear=False):
                self.assertEqual(
                    oem.read_oem_credential(),
                    {
                        key: str(value)
                        for key, value in VALID_OEM_CREDENTIAL.items()
                        if key != "schema"
                    },
                )

    def test_missing_systemd_credential_keeps_interactive_setup(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.dict(oem.os.environ, {"CREDENTIALS_DIRECTORY": directory}, clear=False),
        ):
            self.assertIsNone(oem.read_oem_credential())

    def test_credential_rejects_extra_duplicate_non_text_and_weak_secret_fields(self) -> None:
        malformed = (
            json.dumps({**VALID_OEM_CREDENTIAL, "command": "reboot"}),
            (
                '{"schema":1,"schema":1,"display_name":"Echo CI",'
                '"hostname":"echo-oem-ci","password":"correct horse battery 42",'
                '"locale":"zh_CN.UTF-8","keymap":"us",'
                '"timezone":"Asia/Shanghai"}'
            ),
            json.dumps({**VALID_OEM_CREDENTIAL, "display_name": 42}),
            json.dumps({**VALID_OEM_CREDENTIAL, "password": "passwordpassword"}),
        )
        for payload in malformed:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                credential = Path(directory) / oem.OEM_CREDENTIAL_NAME
                credential.write_text(payload, encoding="utf-8")
                credential.chmod(0o600)
                with (
                    mock.patch.dict(
                        oem.os.environ, {"CREDENTIALS_DIRECTORY": directory}, clear=False
                    ),
                    self.assertRaises(oem.InputError),
                ):
                    oem.read_oem_credential()

    def test_credential_rejects_group_readable_or_oversized_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            credential = Path(directory) / oem.OEM_CREDENTIAL_NAME
            credential.write_text(json.dumps(VALID_OEM_CREDENTIAL), encoding="utf-8")
            credential.chmod(0o640)
            with (
                mock.patch.dict(oem.os.environ, {"CREDENTIALS_DIRECTORY": directory}, clear=False),
                self.assertRaises(oem.InputError),
            ):
                oem.read_oem_credential()
            credential.write_bytes(b"x" * (oem.MAX_OEM_CREDENTIAL_SIZE + 1))
            credential.chmod(0o600)
            with (
                mock.patch.dict(oem.os.environ, {"CREDENTIALS_DIRECTORY": directory}, clear=False),
                self.assertRaises(oem.InputError),
            ):
                oem.read_oem_credential()


class ProvisioningFlowTests(unittest.TestCase):
    def base_patches(self):
        return (
            mock.patch.object(oem.os, "geteuid", return_value=0),
            mock.patch.object(oem.Path, "exists", return_value=False),
            mock.patch.object(
                oem.pwd,
                "getpwnam",
                return_value=SimpleNamespace(pw_uid=1000, pw_dir="/home/echo"),
            ),
            mock.patch.object(oem.socket, "gethostname", return_value="echo-os"),
            mock.patch.object(oem, "read_oem_credential", return_value=None),
            mock.patch.object(
                oem,
                "prompt_validated",
                side_effect=["Echo User", "echo-lab"],
            ),
            mock.patch.object(oem, "prompt_password", return_value="correct horse 42"),
        )

    def test_password_is_only_sent_to_chpasswd_stdin(self) -> None:
        patches = self.base_patches()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            mock.patch.object(oem, "run_checked") as run_checked,
            mock.patch.object(oem, "write_complete_marker") as write_marker,
            mock.patch.object(oem, "shadow_entry", return_value=TEST_HASH),
            mock.patch.object(oem, "write_shadow_state") as write_shadow,
            mock.patch.object(oem.sys, "stdout", new=io.StringIO()),
            mock.patch.object(oem.sys, "stderr", new=io.StringIO()),
        ):
            self.assertEqual(oem.main([]), 0)

        commands = [call.args[0] for call in run_checked.call_args_list]
        self.assertEqual(
            commands[0],
            ["/usr/bin/python3", "/usr/lib/echo-os/echo-region-state", "--configure"],
        )
        self.assertEqual(commands[-1], ["/usr/sbin/chpasswd"])
        self.assertFalse(
            any("correct horse 42" in argument for command in commands for argument in command)
        )
        self.assertEqual(
            run_checked.call_args_list[-1].kwargs["stdin"], "echo:correct horse 42\n"
        )
        write_shadow.assert_called_once_with(TEST_HASH)
        write_marker.assert_called_once_with("Echo User", "echo-lab")

    def test_system_command_failure_never_commits_completion_marker(self) -> None:
        patches = self.base_patches()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            mock.patch.object(
                oem,
                "run_checked",
                side_effect=subprocess.CalledProcessError(1, ["/usr/sbin/usermod"]),
            ),
            mock.patch.object(oem, "write_complete_marker") as write_marker,
            mock.patch.object(oem.sys, "stdout", new=io.StringIO()),
            mock.patch.object(oem.sys, "stderr", new=io.StringIO()),
        ):
            self.assertEqual(oem.main([]), 1)
        write_marker.assert_not_called()

    def test_system_credential_runs_the_real_provisioning_path_without_prompting(self) -> None:
        credential = {
            key: str(value) for key, value in VALID_OEM_CREDENTIAL.items() if key != "schema"
        }
        stdout = io.StringIO()
        with (
            mock.patch.object(oem.os, "geteuid", return_value=0),
            mock.patch.object(oem.Path, "exists", return_value=False),
            mock.patch.object(
                oem.pwd,
                "getpwnam",
                return_value=SimpleNamespace(pw_uid=1000, pw_dir="/home/echo"),
            ),
            mock.patch.object(oem, "read_oem_credential", return_value=credential),
            mock.patch.object(oem, "prompt_validated") as prompt_validated,
            mock.patch.object(oem, "prompt_password") as prompt_password,
            mock.patch.object(oem, "run_checked") as run_checked,
            mock.patch.object(oem, "write_complete_marker") as write_marker,
            mock.patch.object(oem, "shadow_entry", return_value=TEST_HASH),
            mock.patch.object(oem, "write_shadow_state") as write_shadow,
            mock.patch.object(oem.sys, "stdout", new=stdout),
            mock.patch.object(oem.sys, "stderr", new=io.StringIO()),
        ):
            self.assertEqual(oem.main([]), 0)

        prompt_validated.assert_not_called()
        prompt_password.assert_not_called()
        commands = [call.args[0] for call in run_checked.call_args_list]
        self.assertEqual(
            commands[0],
            [
                "/usr/bin/python3",
                "/usr/lib/echo-os/echo-region-state",
                "--configure-values",
                "zh_CN.UTF-8",
                "us",
                "Asia/Shanghai",
            ],
        )
        password = str(VALID_OEM_CREDENTIAL["password"])
        self.assertFalse(any(password in argument for command in commands for argument in command))
        self.assertEqual(run_checked.call_args_list[-1].kwargs["stdin"], f"echo:{password}\n")
        self.assertNotIn(password, stdout.getvalue())
        self.assertIn(
            "ECHO_OEM_PROVISIONED account=echo source=system-credential",
            stdout.getvalue(),
        )
        write_shadow.assert_called_once_with(TEST_HASH)
        write_marker.assert_called_once_with("Echo CI", "echo-oem-ci")

    def test_locked_new_root_restores_hash_without_putting_it_in_argv(self) -> None:
        account = SimpleNamespace(
            pw_uid=1000,
            pw_dir="/home/echo",
            pw_gecos="Echo User",
        )
        marker = {
            "schema": 2,
            "account": "echo",
            "display_name": "Echo User",
            "hostname": "echo-lab",
            "completed_unix": 1,
            "root_version": "0.1.0",
        }
        with (
            mock.patch.object(oem.os, "geteuid", return_value=0),
            mock.patch.object(oem.pwd, "getpwnam", return_value=account),
            mock.patch.object(oem, "read_complete_marker", return_value=marker),
            mock.patch.object(oem, "read_shadow_state", return_value=TEST_HASH),
            mock.patch.object(oem, "shadow_entry", side_effect=["!", TEST_HASH]),
            mock.patch.object(oem, "current_image_version", return_value="0.2.0"),
            mock.patch.object(oem, "capture_account_state", return_value=0) as capture,
            mock.patch.object(oem, "run_checked") as run_checked,
            mock.patch.object(oem.sys, "stdout", new=io.StringIO()),
        ):
            self.assertEqual(oem.main(["--restore"]), 0)

        commands = [call.args[0] for call in run_checked.call_args_list]
        self.assertEqual(commands[-1], ["/usr/sbin/chpasswd", "--encrypted"])
        self.assertFalse(any(TEST_HASH in argument for command in commands for argument in command))
        self.assertEqual(run_checked.call_args_list[-1].kwargs["stdin"], f"echo:{TEST_HASH}\n")
        capture.assert_called_once_with()

    def test_unlocked_current_root_captures_new_password_instead_of_reverting_it(self) -> None:
        with (
            mock.patch.object(oem.os, "geteuid", return_value=0),
            mock.patch.object(oem, "account_record"),
            mock.patch.object(oem, "read_complete_marker", return_value={}),
            mock.patch.object(oem, "read_shadow_state", return_value=TEST_HASH) as read_state,
            mock.patch.object(oem, "shadow_entry", return_value=CURRENT_HASH),
            mock.patch.object(oem, "capture_account_state", return_value=0) as capture,
            mock.patch.object(oem, "run_checked") as run_checked,
        ):
            self.assertEqual(oem.main(["--restore"]), 0)
        capture.assert_called_once_with()
        read_state.assert_not_called()
        run_checked.assert_not_called()

    def test_locked_account_on_same_root_is_not_automatically_unlocked(self) -> None:
        marker = {"root_version": "0.2.0"}
        with (
            mock.patch.object(oem.os, "geteuid", return_value=0),
            mock.patch.object(oem, "account_record"),
            mock.patch.object(oem, "read_complete_marker", return_value=marker),
            mock.patch.object(oem, "shadow_entry", return_value="!"),
            mock.patch.object(oem, "current_image_version", return_value="0.2.0"),
            mock.patch.object(oem, "read_shadow_state") as read_state,
            mock.patch.object(oem, "run_checked") as run_checked,
            mock.patch.object(oem.sys, "stderr", new=io.StringIO()),
        ):
            self.assertEqual(oem.main(["--restore"]), 1)
        read_state.assert_not_called()
        run_checked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
