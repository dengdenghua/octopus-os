#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).with_name("verify-linux-image-runner-registration.py")
SPEC = importlib.util.spec_from_file_location("echo_runner_registration", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class RegistrationFixture:
    def __init__(self, root: Path) -> None:
        self.uid = os.getuid()
        self.application = root / "opt" / "actions-runner"
        self.work = root / "srv" / "echo-os-image-runner"
        self.units = root / "etc" / "systemd" / "system"
        self.application.mkdir(parents=True, mode=0o755)
        self.work.mkdir(parents=True, mode=0o700)
        self.units.mkdir(parents=True)
        self.service = "actions.runner.dengdenghua-echo-os.echo-image-x64.service"
        settings = {
            "AgentId": 42,
            "AgentName": "echo-image-x64",
            "PoolId": 1,
            "PoolName": "Default",
            "ServerUrl": "https://pipelines.actions.githubusercontent.com/tenant",
            "GitHubUrl": "https://github.com/dengdenghua/echo-os",
            "WorkFolder": str(self.work),
            "UseV2Flow": True,
        }
        self.write_json(self.application / ".runner", settings, 0o600)
        self.write_json(self.application / ".credentials", {"Scheme": "OAuth"}, 0o600)
        self.write_json(self.application / ".credentials_rsaparams", {"D": "hidden"}, 0o600)
        self.write_text(
            self.application / ".env",
            f"{MODULE.HOOK_STARTED}\n{MODULE.HOOK_COMPLETED}\n",
            0o600,
        )
        self.write_text(self.application / ".service", f"{self.service}\n", 0o600)
        self.write_text(
            self.units / self.service,
            "[Service]\n"
            f"ExecStart={self.application}/runsvc.sh\n"
            "User=echo-runner\n"
            f"WorkingDirectory={self.application}\n"
            "KillMode=process\n"
            "KillSignal=SIGTERM\n",
            0o644,
        )
        self.write_json(
            self.work / MODULE.HOST_EVIDENCE_NAME,
            {
                "kind": "echo-os-image-runner-host-preflight",
                "marker": "ECHO_IMAGE_RUNNER_HOST_READY arch=x86_64",
                "facts": {"work_root": str(self.work)},
            },
            0o600,
        )

    @staticmethod
    def write_text(path: Path, value: str, mode: int) -> None:
        path.write_text(value, encoding="utf-8")
        path.chmod(mode)

    def write_json(self, path: Path, value: object, mode: int) -> None:
        self.write_text(path, json.dumps(value), mode)

    def inspect(self) -> MODULE.RegistrationFacts:
        return MODULE.inspect_registration(
            application_dir=self.application,
            work_root=self.work,
            systemd_unit_root=self.units,
            runner_user="echo-runner",
            runner_uid=self.uid,
            unit_owner_uid=self.uid,
            repository="dengdenghua/echo-os",
        )


class RegistrationVerifierTests(unittest.TestCase):
    def test_accepts_the_exact_repository_work_root_service_and_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistrationFixture(Path(directory).resolve())
            with mock.patch.object(MODULE, "_bounded_command", return_value="enabled"):
                facts = fixture.inspect()
        self.assertEqual(facts.repository, "dengdenghua/echo-os")
        self.assertEqual(facts.runner_name, "echo-image-x64")
        self.assertIn("ECHO_IMAGE_RUNNER_REGISTRATION_READY", MODULE.success_marker(facts))
        self.assertIn("hooks=ready", MODULE.success_marker(facts))

    def test_rejects_wrong_scope_work_root_ephemeral_mode_and_old_message_flow(self) -> None:
        mutations = (
            ("GitHubUrl", "https://github.com/other/repository"),
            ("WorkFolder", "/tmp/_work"),
            ("Ephemeral", True),
            ("DisableUpdate", True),
            ("UseV2Flow", False),
        )
        for field, value in mutations:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                fixture = RegistrationFixture(Path(directory).resolve())
                settings_path = fixture.application / ".runner"
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
                settings[field] = value
                fixture.write_json(settings_path, settings, 0o600)
                with (
                    mock.patch.object(MODULE, "_bounded_command", return_value="enabled"),
                    self.assertRaises(MODULE.RegistrationError),
                ):
                    fixture.inspect()

    def test_rejects_public_credentials_redirected_hooks_and_disabled_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistrationFixture(Path(directory).resolve())
            (fixture.application / ".credentials").chmod(0o644)
            with (
                mock.patch.object(MODULE, "_bounded_command", return_value="enabled"),
                self.assertRaisesRegex(MODULE.RegistrationError, "not private"),
            ):
                fixture.inspect()

        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistrationFixture(Path(directory).resolve())
            fixture.write_text(
                fixture.application / ".env",
                f"ACTIONS_RUNNER_HOOK_JOB_STARTED=/tmp/untrusted\n{MODULE.HOOK_COMPLETED}\n",
                0o600,
            )
            with (
                mock.patch.object(MODULE, "_bounded_command", return_value="enabled"),
                self.assertRaisesRegex(MODULE.RegistrationError, "hooks"),
            ):
                fixture.inspect()

        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistrationFixture(Path(directory).resolve())
            fixture.write_text(
                fixture.application / ".env",
                f"{MODULE.HOOK_STARTED}\n{MODULE.HOOK_COMPLETED}\n"
                " ACTIONS_RUNNER_HOOK_JOB_STARTED=/tmp/hidden\n",
                0o600,
            )
            with (
                mock.patch.object(MODULE, "_bounded_command", return_value="enabled"),
                self.assertRaisesRegex(MODULE.RegistrationError, "hooks"),
            ):
                fixture.inspect()

        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistrationFixture(Path(directory).resolve())
            with (
                mock.patch.object(MODULE, "_bounded_command", return_value="disabled"),
                self.assertRaisesRegex(MODULE.RegistrationError, "not enabled"),
            ):
                fixture.inspect()

    def test_rejects_a_symlinked_setting_and_unbound_host_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistrationFixture(Path(directory).resolve())
            settings = fixture.application / ".runner"
            target = fixture.application / "runner.json"
            settings.rename(target)
            settings.symlink_to(target)
            with (
                mock.patch.object(MODULE, "_bounded_command", return_value="enabled"),
                self.assertRaisesRegex(MODULE.RegistrationError, "unsafe"),
            ):
                fixture.inspect()

        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistrationFixture(Path(directory).resolve())
            evidence = fixture.work / MODULE.HOST_EVIDENCE_NAME
            fixture.write_json(
                evidence,
                {
                    "kind": "echo-os-image-runner-host-preflight",
                    "marker": "ECHO_IMAGE_RUNNER_HOST_READY arch=x86_64",
                    "facts": {"work_root": "/tmp/wrong"},
                },
                0o600,
            )
            with (
                mock.patch.object(MODULE, "_bounded_command", return_value="enabled"),
                self.assertRaisesRegex(MODULE.RegistrationError, "host evidence"),
            ):
                fixture.inspect()


if __name__ == "__main__":
    unittest.main()
