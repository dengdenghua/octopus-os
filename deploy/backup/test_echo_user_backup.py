#!/usr/bin/env python3
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("echo-user-backup")
LOADER = importlib.machinery.SourceFileLoader("echo_user_backup", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
LOADER.exec_module(MODULE)


class EchoUserBackupTests(unittest.TestCase):
    def test_offline_gate_rejects_any_remaining_user_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            proc_root = Path(directory)
            root_process = proc_root / "1"
            root_process.mkdir()
            (root_process / "status").write_text(
                "Name:\tinit\nUid:\t0\t0\t0\t0\n", encoding="ascii"
            )
            MODULE.ensure_no_user_processes(proc_root)
            user_process = proc_root / "42"
            user_process.mkdir()
            (user_process / "status").write_text(
                "Name:\tworker\nUid:\t1000\t1000\t1000\t1000\n", encoding="ascii"
            )
            with self.assertRaises(MODULE.BackupError):
                MODULE.ensure_no_user_processes(proc_root)

    def test_agent_is_restarted_when_stop_reports_a_partial_failure(self) -> None:
        active = True
        calls: list[tuple[str, ...]] = []

        def fake_systemctl(arguments: list[str]) -> object:
            nonlocal active
            calls.append(tuple(arguments))
            if arguments[0] == "is-active":
                return SimpleNamespace(returncode=0 if active else 3)
            if arguments[0] == "stop":
                active = False
                return SimpleNamespace(returncode=1)
            if arguments[0] == "start":
                active = True
                return SimpleNamespace(returncode=0)
            raise AssertionError(arguments)

        with mock.patch.object(MODULE, "systemctl", fake_systemctl), mock.patch.object(
            MODULE, "agent_is_healthy", return_value=True
        ):
            with self.assertRaises(MODULE.BackupError):
                with MODULE.stopped_agent():
                    self.fail("partial stop must not enter the backup body")
        self.assertIn(("start", "echo-agent.service"), calls)
        self.assertTrue(active)

    def test_login_manager_is_restarted_after_operation_failure(self) -> None:
        active = True
        calls: list[tuple[str, ...]] = []

        def fake_systemctl(arguments: list[str]) -> object:
            nonlocal active
            calls.append(tuple(arguments))
            if arguments[0] == "is-active":
                return SimpleNamespace(returncode=0 if active else 3)
            if arguments[0] == "stop":
                active = False
                return SimpleNamespace(returncode=0)
            if arguments[0] == "start":
                active = True
                return SimpleNamespace(returncode=0)
            raise AssertionError(arguments)

        with mock.patch.object(MODULE, "systemctl", fake_systemctl), mock.patch.object(
            MODULE, "active_user_session", return_value=False
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                with MODULE.stopped_login_manager():
                    raise RuntimeError("fixture failure")
        self.assertIn(("start", "sddm.service"), calls)
        self.assertTrue(active)

    def test_any_echo_session_blocks_offline_access(self) -> None:
        responses = iter(
            (
                SimpleNamespace(returncode=0, stdout="17 1000 echo seat0 tty2\n"),
                SimpleNamespace(
                    returncode=0,
                    stdout="Name=echo\nClass=background\nRemote=yes\nState=closing\n",
                ),
            )
        )

        def runner(*_args: object, **_kwargs: object) -> object:
            return next(responses)

        self.assertTrue(MODULE.active_user_session(runner))

    def test_accepts_only_fixed_external_posix_block_mount(self) -> None:
        MODULE.validate_mount_record(
            MODULE.MountRecord("/dev/sdb1", "ext4", MODULE.BACKUP_MOUNT)
        )
        for invalid in (
            MODULE.MountRecord("server:/backup", "nfs", MODULE.BACKUP_MOUNT),
            MODULE.MountRecord("/dev/mapper/echo-home", "ext4", MODULE.BACKUP_MOUNT),
            MODULE.MountRecord("/dev/sdb1", "vfat", MODULE.BACKUP_MOUNT),
            MODULE.MountRecord("/dev/sdb1", "ext4", Path("/tmp/backup")),
        ):
            with self.assertRaises(MODULE.BackupError):
                MODULE.validate_mount_record(invalid)

    def test_password_never_accepts_short_or_multiline_input(self) -> None:
        self.assertEqual(
            MODULE.validate_password("correct horse battery staple"),
            b"correct horse battery staple",
        )
        for invalid in ("too-short", "valid length but\nmultiline", "nul\0in-password"):
            with self.assertRaises(MODULE.BackupError):
                MODULE.validate_password(invalid)

    def test_backup_command_has_fixed_sources_and_no_device_identity(self) -> None:
        command = MODULE.backup_arguments(17, "echo-0123456789abcdef")
        self.assertEqual(command[0], "/usr/bin/restic")
        self.assertIn("/proc/self/fd/17", command)
        self.assertIn("/home/echo", command)
        self.assertIn("/var/lib/echo-agent", command)
        self.assertIn("--one-file-system", command)
        self.assertIn("--json", command)
        self.assertIn("offline", command)
        joined = " ".join(command)
        for forbidden in (
            "/etc",
            "/var/lib/echo-os",
            "NetworkManager",
            "shadow",
            "machine-id",
            "crypttab",
            "tpm",
        ):
            self.assertNotIn(forbidden, joined)

    def test_completed_backup_snapshot_comes_from_json_summary(self) -> None:
        snapshot = "d" * 64
        output = "\n".join(
            (
                '{"message_type":"status","percent_done":0.5}',
                '{"message_type":"summary","snapshot_id":"' + snapshot + '"}',
            )
        )
        self.assertEqual(MODULE.snapshot_from_backup_output(output), snapshot)
        for invalid in (
            "",
            '{"message_type":"summary","snapshot_id":"short"}',
            output + "\n" + output.splitlines()[-1],
        ):
            with self.assertRaises(MODULE.BackupError):
                MODULE.snapshot_from_backup_output(invalid)

    def test_restore_is_staged_and_never_deletes_or_overwrites(self) -> None:
        snapshot = "a" * 64
        target = Path("/home/echo/.echo-restore-staging/fixed")
        command = MODULE.restore_arguments(21, snapshot, target)
        self.assertEqual(command[-2:], ["--overwrite", "never"])
        self.assertIn(str(target), command)
        self.assertNotIn("--delete", command)
        self.assertNotIn("/home/echo", command[:-3])

    def test_restore_state_records_the_exact_staging_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state" / "user-backup-state.json"
            state_file.parent.mkdir(mode=0o700)
            staging_name = "20260826T120000Z-" + "a" * 12
            with mock.patch.object(MODULE, "STATE_FILE", state_file), mock.patch.object(
                MODULE, "validate_directory"
            ), mock.patch.object(MODULE.os, "chown"):
                MODULE.write_state(
                    "b" * 64,
                    "a" * 64,
                    "restore-staged",
                    staging_name=staging_name,
                )
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], 2)
            self.assertEqual(payload["staging_name"], staging_name)
            self.assertEqual(state_file.stat().st_mode & 0o777, 0o600)
            with mock.patch.object(MODULE, "STATE_FILE", state_file), mock.patch.object(
                MODULE, "validate_directory"
            ), mock.patch.object(MODULE.os, "chown"):
                with self.assertRaises(MODULE.BackupError):
                    MODULE.write_state(
                        "b" * 64,
                        "a" * 64,
                        "restore-staged",
                        staging_name="../escape",
                    )

    def test_snapshot_selection_is_exact_and_unambiguous(self) -> None:
        first = {"id": "a" * 64, "time": "2026-01-01T00:00:00Z", "tags": [MODULE.TAG]}
        second = {"id": "b" * 64, "time": "2026-02-01T00:00:00Z", "tags": [MODULE.TAG]}
        self.assertEqual(MODULE.select_snapshot("latest", [first, second]), second["id"])
        self.assertEqual(MODULE.select_snapshot("a" * 12, [first, second]), first["id"])
        with self.assertRaises(MODULE.BackupError):
            MODULE.select_snapshot("c" * 12, [first, second])
        with self.assertRaises(MODULE.BackupError):
            MODULE.select_snapshot("a" * 12, [first, first])

    def test_host_identity_is_one_way_and_not_the_machine_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            machine_id = Path(directory) / "machine-id"
            raw = "0123456789abcdef0123456789abcdef"
            machine_id.write_text(raw + "\n", encoding="ascii")
            host = MODULE.pseudonymous_host(machine_id)
            self.assertRegex(host, r"^echo-[0-9a-f]{16}$")
            self.assertNotIn(raw, host)

    def test_restored_tree_rejects_absolute_and_escaping_symlinks(self) -> None:
        user = MODULE.LocalUser(os.getuid(), os.getgid(), Path.home())
        for link_target in ("/etc/shadow", "../../../../etc/shadow"):
            with self.subTest(link_target=link_target), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                home = target / "home" / MODULE.USER_NAME
                agent = target / "var" / "lib" / "echo-agent"
                home.mkdir(parents=True)
                agent.mkdir(parents=True)
                (home / "unsafe").symlink_to(link_target)
                with self.assertRaises(MODULE.BackupError):
                    MODULE.validate_restored_tree(target, user)


if __name__ == "__main__":
    unittest.main()
