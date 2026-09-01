#!/usr/bin/env python3
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

MODULE_PATH = Path(__file__).with_name("echo-region-state")
LOADER = importlib.machinery.SourceFileLoader("echo_region_state", str(MODULE_PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC and SPEC.loader
region = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(region)

CATALOGS = {
    "locale": frozenset({"C.UTF-8", "en_US.UTF-8", "zh_CN.UTF-8"}),
    "keymap": frozenset({"us", "de", "jp106"}),
    "timezone": frozenset({"UTC", "Asia/Shanghai", "Europe/Berlin"}),
}
TEST_REGION = {"locale": "zh_CN.UTF-8", "keymap": "us", "timezone": "Asia/Shanghai"}


class ValidationTests(unittest.TestCase):
    def test_locale_catalog_adds_the_standard_utf8_spelling(self) -> None:
        with mock.patch.object(region, "run_output", return_value="C\nzh_CN.utf8\n"):
            self.assertIn("zh_CN.UTF-8", region.available_catalog("locale"))

    def test_exact_catalog_membership_rejects_paths_and_shell_text(self) -> None:
        self.assertEqual(
            region.validate_region(**TEST_REGION, catalogs=CATALOGS),
            TEST_REGION,
        )
        for kind, value in (
            ("locale", "zh_CN.UTF-8;reboot"),
            ("keymap", "../../etc/shadow"),
            ("timezone", "../UTC"),
            ("timezone", "Asia/Shanghai\nUTC"),
        ):
            with self.subTest(kind=kind, value=value), self.assertRaises(region.InputError):
                region.validate_choice(kind, value, CATALOGS[kind])

    def test_state_schema_rejects_extra_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "region-state.json"
            state_file.write_text(
                json.dumps({"schema": 1, **TEST_REGION, "command": "reboot"}),
                encoding="utf-8",
            )
            with (
                mock.patch.object(region, "STATE_FILE", state_file),
                mock.patch.object(region, "require_private_root_file"),
                mock.patch.object(region, "available_catalog", side_effect=CATALOGS.__getitem__),
                self.assertRaises(RuntimeError),
            ):
                region.read_state()

    def test_persistent_state_requires_root_owned_regular_mode_0600(self) -> None:
        invalid = (
            SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1000),
            SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0),
            SimpleNamespace(st_mode=stat.S_IFDIR | 0o600, st_uid=0),
        )
        for metadata in invalid:
            with (
                self.subTest(metadata=metadata),
                mock.patch.object(region.Path, "stat", return_value=metadata),
                self.assertRaises(RuntimeError),
            ):
                region.require_private_root_file(Path("/var/lib/echo-os/region-state.json"))


class StateLifecycleTests(unittest.TestCase):
    def test_active_files_are_parsed_without_executing_configuration_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            zoneinfo = root / "zoneinfo"
            timezone = zoneinfo / "Asia" / "Shanghai"
            timezone.parent.mkdir(parents=True)
            timezone.write_text("TZif-test", encoding="utf-8")
            locale_conf = root / "locale.conf"
            locale_conf.write_text('LANG="zh_CN.UTF-8"\nIGNORED=$(reboot)\n', encoding="utf-8")
            vconsole_conf = root / "vconsole.conf"
            vconsole_conf.write_text("KEYMAP=us\n", encoding="utf-8")
            localtime = root / "localtime"
            localtime.symlink_to(timezone)
            with (
                mock.patch.object(region, "LOCALE_CONF", locale_conf),
                mock.patch.object(region, "VCONSOLE_CONF", vconsole_conf),
                mock.patch.object(region, "LOCALTIME", localtime),
                mock.patch.object(region, "ZONEINFO_DIRECTORY", zoneinfo),
                mock.patch.object(region, "available_catalog", side_effect=CATALOGS.__getitem__),
            ):
                self.assertEqual(region.current_region(), TEST_REGION)

    def test_atomic_state_contains_only_validated_region_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_directory = Path(directory)
            state_file = state_directory / "region-state.json"
            with (
                mock.patch.object(region, "STATE_DIRECTORY", state_directory),
                mock.patch.object(region, "STATE_FILE", state_file),
                mock.patch.object(region, "available_catalog", side_effect=CATALOGS.__getitem__),
            ):
                region.write_state(TEST_REGION)
            payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(payload, {"schema": 1, **TEST_REGION})
            self.assertEqual(os.stat(state_file).st_mode & 0o777, 0o600)
            self.assertEqual(list(state_directory.glob("*.tmp")), [])

    def test_missing_state_initializes_from_active_root_without_system_mutation(self) -> None:
        with (
            mock.patch.object(region.os, "geteuid", return_value=0),
            mock.patch.object(region.Path, "exists", return_value=False),
            mock.patch.object(region, "current_region", return_value=TEST_REGION),
            mock.patch.object(region, "write_state") as write_state,
            mock.patch.object(region, "apply_region") as apply_region,
            mock.patch("builtins.print"),
        ):
            self.assertEqual(region.restore_or_initialize(), 0)
        write_state.assert_called_once_with(TEST_REGION)
        apply_region.assert_not_called()

    def test_existing_state_is_applied_and_verified(self) -> None:
        with (
            mock.patch.object(region.os, "geteuid", return_value=0),
            mock.patch.object(region.Path, "exists", return_value=True),
            mock.patch.object(region, "read_state", return_value=TEST_REGION),
            mock.patch.object(region, "apply_region") as apply_region,
            mock.patch.object(region, "current_region", return_value=TEST_REGION),
            mock.patch("builtins.print"),
        ):
            self.assertEqual(region.restore_or_initialize(), 0)
        apply_region.assert_called_once_with(TEST_REGION)

    def test_restore_fails_when_active_state_does_not_match(self) -> None:
        wrong = {**TEST_REGION, "timezone": "UTC"}
        with (
            mock.patch.object(region.os, "geteuid", return_value=0),
            mock.patch.object(region.Path, "exists", return_value=True),
            mock.patch.object(region, "read_state", return_value=TEST_REGION),
            mock.patch.object(region, "apply_region"),
            mock.patch.object(region, "current_region", return_value=wrong),
            self.assertRaises(RuntimeError),
        ):
            region.restore_or_initialize()

    def test_apply_uses_fixed_systemd_commands_without_a_shell(self) -> None:
        with (
            mock.patch.object(region, "available_catalog", side_effect=CATALOGS.__getitem__),
            mock.patch.object(region, "run_checked") as run_checked,
        ):
            region.apply_region(TEST_REGION)
        self.assertEqual(
            [call.args[0] for call in run_checked.call_args_list],
            [
                ["/usr/bin/localectl", "set-locale", "LANG=zh_CN.UTF-8"],
                ["/usr/bin/localectl", "set-keymap", "us"],
                ["/usr/bin/timedatectl", "set-timezone", "Asia/Shanghai"],
            ],
        )

    def test_noninteractive_oem_values_use_the_same_catalog_and_persistence_path(self) -> None:
        with (
            mock.patch.object(region.os, "geteuid", return_value=0),
            mock.patch.object(region, "available_catalog", side_effect=CATALOGS.__getitem__),
            mock.patch.object(region, "apply_region") as apply_region,
            mock.patch.object(region, "current_region", return_value=TEST_REGION),
            mock.patch.object(region, "write_state") as write_state,
            mock.patch.object(region, "readiness") as readiness,
        ):
            self.assertEqual(
                region.main(
                    [
                        "--configure-values",
                        TEST_REGION["locale"],
                        TEST_REGION["keymap"],
                        TEST_REGION["timezone"],
                    ]
                ),
                0,
            )
        apply_region.assert_called_once_with(TEST_REGION)
        write_state.assert_called_once_with(TEST_REGION)
        readiness.assert_called_once_with(TEST_REGION, "oem-credential")

    def test_noninteractive_oem_values_reject_non_catalog_input_before_apply(self) -> None:
        with (
            mock.patch.object(region.os, "geteuid", return_value=0),
            mock.patch.object(region, "available_catalog", side_effect=CATALOGS.__getitem__),
            mock.patch.object(region, "apply_region") as apply_region,
            mock.patch.object(region.sys, "stderr", new=io.StringIO()),
        ):
            self.assertEqual(
                region.main(
                    [
                        "--configure-values",
                        "zh_CN.UTF-8;reboot",
                        "us",
                        "Asia/Shanghai",
                    ]
                ),
                1,
            )
        apply_region.assert_not_called()


if __name__ == "__main__":
    unittest.main()
