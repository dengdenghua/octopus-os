#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("echo_data_protection.py")
SPEC = importlib.util.spec_from_file_location("echo_data_protection", MODULE_PATH)
assert SPEC and SPEC.loader
protection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(protection)


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.keys: dict[str, set[str]] = {label: {"factory"} for label in protection.PARTITIONS}
        self.tpm: set[str] = set()
        self.duplicate_tpm: set[str] = set()
        self.fail_tpm_for: str | None = None
        self.fail_tpm_wipe_for: str | None = None
        self.fail_add_for: str | None = None
        self.fail_remove_for: str | None = None

    @staticmethod
    def result(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")

    @staticmethod
    def key_name(key_path: Path) -> str:
        if "factory" in key_path.name:
            return "factory"
        if "new-recovery" in key_path.name:
            return "new-recovery"
        return "recovery"

    def __call__(self, command: list[str] | tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        command = tuple(str(item) for item in command)
        self.commands.append(command)
        device = Path(command[-1]).name if command else ""
        if command[:3] == (protection.CRYPTSETUP, "isLuks", "--type"):
            return self.result()
        if command[:3] == (protection.FINDMNT, "-rn", "--source"):
            return self.result(1)
        if command[:2] == (protection.SWAPON, "--show=NAME"):
            return self.result()
        if command[:3] == (
            protection.CRYPTSETUP,
            "open",
            "--test-passphrase",
        ):
            if "--token-only" in command:
                return self.result(0 if device in self.tpm else 1)
            key_path = Path(command[command.index("--key-file") + 1])
            key_name = self.key_name(key_path)
            return self.result(0 if key_name in self.keys[device] else 1)
        if command[:2] == (protection.CRYPTSETUP, "luksAddKey"):
            device = Path(command[-2]).name
            if device == self.fail_add_for:
                return self.result(1)
            self.keys[device].add(self.key_name(Path(command[-1])))
            return self.result()
        if command[0] == protection.CRYPTENROLL:
            device = Path(command[1]).name
            if len(command) == 3 and command[2] == "--wipe-slot=tpm2":
                if device == self.fail_tpm_wipe_for:
                    return self.result(1)
                self.tpm.discard(device)
                self.duplicate_tpm.discard(device)
                return self.result()
            if device == self.fail_tpm_for:
                return self.result(1)
            self.tpm.add(device)
            return self.result()
        if command[:3] == (
            protection.CRYPTSETUP,
            "luksDump",
            "--dump-json-metadata",
        ):
            if device in self.duplicate_tpm:
                token = '{"tokens":{"0":{"type":"systemd-tpm2","keyslots":["1"]},"1":{"type":"systemd-tpm2","keyslots":["2"]}}}'
            elif device in self.tpm:
                token = '{"tokens":{"0":{"type":"systemd-tpm2","keyslots":["1"]}}}'
            else:
                token = '{"tokens":{}}'
            return self.result(stdout=token)
        if command[:2] == (protection.CRYPTSETUP, "luksRemoveKey"):
            device = Path(command[-2]).name
            if device == self.fail_remove_for:
                return self.result(1)
            self.keys[device].discard(self.key_name(Path(command[-1])))
            return self.result()
        raise AssertionError(f"unexpected command: {command}")


class DataProtectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.factory = self.root / "factory.key"
        self.factory.write_bytes(b"f" * 32)
        self.factory.chmod(0o600)
        self.recovery = self.root / "recovery.key"
        self.recovery.write_text(
            "01234567-89abcdef-01234567-89abcdef-01234567-89abcdef-01234567-89abcdef",
            encoding="ascii",
        )
        self.recovery.chmod(0o600)
        self.new_recovery = self.root / "new-recovery.key"
        self.new_recovery.write_text(
            "fedcba98-76543210-fedcba98-76543210-fedcba98-76543210-fedcba98-76543210",
            encoding="ascii",
        )
        self.new_recovery.chmod(0o600)
        self.public_key = self.root / "pcr-policy-public.pem"
        self.public_key.write_text(
            "-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----\n",
            encoding="ascii",
        )
        self.public_key.chmod(0o644)
        self.runner = FakeRunner()
        self.devices = {label: self.root / label for label in protection.PARTITIONS}
        self.protector = protection.DataProtector(
            runner=self.runner,
            resolver=self.devices.__getitem__,
            tpm2_public_key=self.public_key,
        )

    def test_generated_recovery_key_is_private_exact_and_exclusive(self) -> None:
        output = self.root / "generated.key"
        protection.generate_recovery_key(output)
        value = output.read_text(encoding="ascii")
        self.assertRegex(value, protection.RECOVERY_KEY)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError):
            protection.generate_recovery_key(output)

    def test_generated_factory_key_is_private_exact_and_exclusive(self) -> None:
        output = self.root / "generated-factory.key"
        protection.generate_factory_key(output)
        value = output.read_bytes()
        self.assertRegex(value.decode("ascii"), r"^[0-9a-f]{64}$")
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertNotIn(b"\n", value)
        with self.assertRaises(FileExistsError):
            protection.generate_factory_key(output)

    def test_secret_files_reject_symlinks_permissions_and_terminators(self) -> None:
        linked = self.root / "linked.key"
        linked.symlink_to(self.factory)
        with self.assertRaises(protection.DataProtectionError):
            protection.read_secret(linked, recovery=False)
        self.factory.chmod(0o640)
        with self.assertRaises(protection.DataProtectionError):
            protection.read_secret(self.factory, recovery=False)
        self.factory.chmod(0o600)
        self.factory.write_bytes(b"f" * 32 + b"\n")
        with self.assertRaises(protection.DataProtectionError):
            protection.read_secret(self.factory, recovery=False)

    def test_factory_and_recovery_keys_must_be_independent(self) -> None:
        self.factory.write_bytes(self.recovery.read_bytes())
        with self.assertRaisesRegex(protection.DataProtectionError, "independent"):
            self.protector.enroll(self.factory, self.recovery)

    def test_public_policy_key_rejects_writable_or_non_rsa_input(self) -> None:
        self.public_key.chmod(0o666)
        with self.assertRaises(protection.DataProtectionError):
            protection.check_tpm2_public_key(self.public_key)
        self.public_key.chmod(0o644)
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="not RSA")
        with mock.patch.object(protection, "run_command", return_value=failed):
            with self.assertRaisesRegex(protection.DataProtectionError, "PEM-encoded RSA"):
                protection.check_tpm2_public_key(self.public_key)

    def test_enroll_adds_both_paths_everywhere_before_removing_factory(self) -> None:
        self.protector.enroll(self.factory, self.recovery)
        for label in protection.PARTITIONS:
            self.assertEqual(self.runner.keys[label], {"recovery"})
            self.assertIn(label, self.runner.tpm)
        removal_indexes = [
            index
            for index, command in enumerate(self.runner.commands)
            if command[:2] == (protection.CRYPTSETUP, "luksRemoveKey")
        ]
        tpm_test_indexes = [
            index
            for index, command in enumerate(self.runner.commands)
            if command[:3] == (protection.CRYPTSETUP, "open", "--test-passphrase")
            and "--token-only" in command
        ]
        self.assertEqual(len(removal_indexes), len(protection.PARTITIONS))
        self.assertGreater(min(removal_indexes), sorted(tpm_test_indexes)[2])
        enrollments = [
            command
            for command in self.runner.commands
            if command and command[0] == protection.CRYPTENROLL
        ]
        self.assertEqual(len(enrollments), len(protection.PARTITIONS))
        for command in enrollments:
            self.assertIn("--tpm2-pcrs=", command)
            self.assertIn(f"--tpm2-public-key={self.public_key}", command)
            self.assertIn("--tpm2-public-key-pcrs=11", command)
            self.assertNotIn("--tpm2-pcrs=7", command)

    def test_failure_before_all_tpm_paths_preserves_every_factory_key(self) -> None:
        self.runner.fail_tpm_for = "echo-swap"
        with self.assertRaises(protection.DataProtectionError):
            self.protector.enroll(self.factory, self.recovery)
        self.assertFalse(
            any(
                command[:2] == (protection.CRYPTSETUP, "luksRemoveKey")
                for command in self.runner.commands
            )
        )
        for label in protection.PARTITIONS:
            self.assertIn("factory", self.runner.keys[label])
            self.assertIn("recovery", self.runner.keys[label])

    def test_idempotent_recovery_enrollment_does_not_add_duplicate_slot(self) -> None:
        for label in protection.PARTITIONS:
            self.runner.keys[label].add("recovery")
        self.protector.enroll(self.factory, self.recovery)
        self.assertFalse(
            any(
                command[:2] == (protection.CRYPTSETUP, "luksAddKey")
                for command in self.runner.commands
            )
        )

    def test_verify_requires_recovery_and_tpm_for_every_partition(self) -> None:
        for label in protection.PARTITIONS:
            self.runner.keys[label].add("recovery")
            self.runner.tpm.add(label)
        self.protector.verify(self.recovery)
        self.runner.tpm.remove("echo-home")
        with self.assertRaisesRegex(protection.DataProtectionError, "TPM2 token"):
            self.protector.verify(self.recovery)

    def test_factory_reset_enrolls_tpm_from_the_durable_recovery_key(self) -> None:
        for label in protection.PARTITIONS:
            self.runner.keys[label] = {"recovery"}
        self.protector.enroll_recovery(self.recovery)
        enrollments = [
            command
            for command in self.runner.commands
            if command and command[0] == protection.CRYPTENROLL
        ]
        self.assertEqual(len(enrollments), len(protection.PARTITIONS))
        self.assertTrue(
            all(f"--unlock-key-file={self.recovery}" in command for command in enrollments)
        )
        self.assertFalse(
            any(
                command[:2] == (protection.CRYPTSETUP, "luksRemoveKey")
                for command in self.runner.commands
            )
        )

    def test_tpm2_rebind_explicitly_wipes_before_same_policy_enrollment(self) -> None:
        for label in protection.PARTITIONS:
            self.runner.keys[label] = {"recovery"}
            self.runner.tpm.add(label)
        self.protector.rebind_tpm2(self.recovery)
        for label in protection.PARTITIONS:
            self.assertIn(label, self.runner.tpm)
            wipe = (
                protection.CRYPTENROLL,
                str(self.devices[label]),
                "--wipe-slot=tpm2",
            )
            wipe_index = self.runner.commands.index(wipe)
            enroll_index = next(
                index
                for index, command in enumerate(self.runner.commands)
                if command[0] == protection.CRYPTENROLL
                and command[1] == str(self.devices[label])
                and f"--unlock-key-file={self.recovery}" in command
            )
            self.assertLess(wipe_index, enroll_index)

    def test_tpm2_rebind_failure_never_removes_recovery_access(self) -> None:
        for label in protection.PARTITIONS:
            self.runner.keys[label] = {"recovery"}
            self.runner.tpm.add(label)
        self.runner.fail_tpm_for = "echo-swap"
        with self.assertRaises(protection.DataProtectionError):
            self.protector.rebind_tpm2(self.recovery)
        for label in protection.PARTITIONS:
            self.assertIn("recovery", self.runner.keys[label])
        self.assertIn("echo-var", self.runner.tpm)
        self.assertNotIn("echo-swap", self.runner.tpm)
        self.assertIn("echo-home", self.runner.tpm)

    def test_offline_srk_enrollment_records_tokens_without_claiming_unseal(self) -> None:
        device_key = self.root / "srk.public"
        device_key.write_bytes(b"public-tpm2b-key")
        device_key.chmod(0o644)
        offline = protection.DataProtector(
            runner=self.runner,
            resolver=self.devices.__getitem__,
            tpm2_public_key=self.public_key,
            tpm2_device_key=device_key,
        )
        offline.enroll(self.factory, self.recovery)
        self.assertTrue(
            all(
                any(f"--tpm2-device-key={device_key}" == item for item in command)
                for command in self.runner.commands
                if command and command[0] == protection.CRYPTENROLL
            )
        )
        self.assertFalse(
            any(
                "--token-only" in command
                for command in self.runner.commands
                if command[:2] == (protection.CRYPTSETUP, "open")
            )
        )

    def test_recovery_rotation_establishes_every_new_key_before_revocation(self) -> None:
        for label in protection.PARTITIONS:
            self.runner.keys[label] = {"recovery"}
            self.runner.tpm.add(label)
        self.protector.rotate_recovery(self.recovery, self.new_recovery)
        for label in protection.PARTITIONS:
            self.assertEqual(self.runner.keys[label], {"new-recovery"})
            self.assertIn(label, self.runner.tpm)
        add_indexes = [
            index
            for index, command in enumerate(self.runner.commands)
            if command[:2] == (protection.CRYPTSETUP, "luksAddKey")
        ]
        remove_indexes = [
            index
            for index, command in enumerate(self.runner.commands)
            if command[:2] == (protection.CRYPTSETUP, "luksRemoveKey")
        ]
        self.assertEqual(len(add_indexes), len(protection.PARTITIONS))
        self.assertEqual(len(remove_indexes), len(protection.PARTITIONS))
        self.assertLess(max(add_indexes), min(remove_indexes))

    def test_recovery_rotation_add_failure_keeps_old_key_everywhere(self) -> None:
        for label in protection.PARTITIONS:
            self.runner.keys[label] = {"recovery"}
            self.runner.tpm.add(label)
        self.runner.fail_add_for = "echo-swap"
        with self.assertRaisesRegex(
            protection.DataProtectionError, "cannot add the new recovery key"
        ):
            self.protector.rotate_recovery(self.recovery, self.new_recovery)
        self.assertFalse(
            any(
                command[:2] == (protection.CRYPTSETUP, "luksRemoveKey")
                for command in self.runner.commands
            )
        )
        for label in protection.PARTITIONS:
            self.assertIn("recovery", self.runner.keys[label])

    def test_recovery_rotation_resumes_after_partial_old_key_removal(self) -> None:
        for label in protection.PARTITIONS:
            self.runner.keys[label] = {"recovery", "new-recovery"}
            self.runner.tpm.add(label)
        self.runner.keys["echo-var"] = {"new-recovery"}
        self.protector.rotate_recovery(self.recovery, self.new_recovery)
        for label in protection.PARTITIONS:
            self.assertEqual(self.runner.keys[label], {"new-recovery"})
        self.assertFalse(
            any(
                command[:2] == (protection.CRYPTSETUP, "luksAddKey")
                for command in self.runner.commands
            )
        )

    def test_recovery_rotation_can_retry_a_failed_revocation_phase(self) -> None:
        for label in protection.PARTITIONS:
            self.runner.keys[label] = {"recovery"}
            self.runner.tpm.add(label)
        self.runner.fail_remove_for = "echo-swap"
        with self.assertRaisesRegex(
            protection.DataProtectionError, "cannot revoke the old recovery key"
        ):
            self.protector.rotate_recovery(self.recovery, self.new_recovery)
        for label in protection.PARTITIONS:
            self.assertIn("new-recovery", self.runner.keys[label])
        self.assertEqual(self.runner.keys["echo-var"], {"new-recovery"})
        self.assertIn("recovery", self.runner.keys["echo-swap"])
        self.assertIn("recovery", self.runner.keys["echo-home"])

        self.runner.fail_remove_for = None
        self.protector.rotate_recovery(self.recovery, self.new_recovery)
        for label in protection.PARTITIONS:
            self.assertEqual(self.runner.keys[label], {"new-recovery"})

    def test_recovery_rotation_rejects_missing_tpm_before_any_write(self) -> None:
        for label in protection.PARTITIONS:
            self.runner.keys[label] = {"recovery"}
            self.runner.tpm.add(label)
        self.runner.tpm.remove("echo-home")
        with self.assertRaisesRegex(protection.DataProtectionError, "exactly one TPM2 token"):
            self.protector.rotate_recovery(self.recovery, self.new_recovery)
        self.assertFalse(
            any(
                command[:2]
                in {
                    (protection.CRYPTSETUP, "luksAddKey"),
                    (protection.CRYPTSETUP, "luksRemoveKey"),
                }
                for command in self.runner.commands
            )
        )

    def test_recovery_rotation_rejects_duplicate_tpm_tokens_before_write(self) -> None:
        for label in protection.PARTITIONS:
            self.runner.keys[label] = {"recovery"}
            self.runner.tpm.add(label)
        self.runner.duplicate_tpm.add("echo-swap")
        with self.assertRaisesRegex(protection.DataProtectionError, "exactly one TPM2 token"):
            self.protector.rotate_recovery(self.recovery, self.new_recovery)
        self.assertFalse(
            any(
                command[:2]
                in {
                    (protection.CRYPTSETUP, "luksAddKey"),
                    (protection.CRYPTSETUP, "luksRemoveKey"),
                }
                for command in self.runner.commands
            )
        )

    def test_recovery_rotation_rejects_identical_key_material(self) -> None:
        self.new_recovery.write_bytes(self.recovery.read_bytes())
        with self.assertRaisesRegex(protection.DataProtectionError, "independent"):
            self.protector.rotate_recovery(self.recovery, self.new_recovery)

    def test_cli_enrollment_requires_root(self) -> None:
        with mock.patch.object(os, "geteuid", return_value=501):
            with self.assertRaises(protection.DataProtectionError):
                protection.require_root()


if __name__ == "__main__":
    unittest.main()
