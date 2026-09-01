#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("verify-linux-image-runner-host.py")
SPEC = importlib.util.spec_from_file_location("verify_linux_image_runner_host", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def healthy_facts() -> object:
    return MODULE.HostFacts(
        system="Linux",
        machine="x86_64",
        uid=1001,
        effective_cpus=8.0,
        effective_memory_bytes=32 * MODULE.GIB,
        work_root="/srv/echo-os-image-runner",
        work_root_device_id=23,
        work_root_free_bytes=256 * MODULE.GIB,
        work_root_private=True,
        docker_client_present=True,
        docker_server_version="28.3.3",
        docker_context="default",
        docker_environment_clean=True,
        docker_socket_ready=True,
        docker_security_options_valid=True,
        docker_security_options=("name=seccomp,profile=builtin", "name=cgroupns"),
        kvm_device_ready=True,
        kernel_modules_tree_ready=True,
        loop_max=64,
        nbd_max=16,
        nbd_max_part=16,
    )


class LinuxImageRunnerHostTests(unittest.TestCase):
    def test_complete_host_emits_one_bounded_marker(self) -> None:
        self.assertEqual(
            MODULE.success_marker(healthy_facts()),
            "ECHO_IMAGE_RUNNER_HOST_READY arch=x86_64 cpu=8 memory-gib=32 "
            "work-free-gib=256 work-device=23 docker=28.3.3 "
            "docker-context=default docker-mode=rootful kvm=ready loop-max=64 "
            "nbd-max=16 nbd-max-part=16",
        )

    def test_linux_x86_and_non_root_service_account_are_required(self) -> None:
        facts = healthy_facts()
        for replacement, expected in (
            ({"system": "Darwin"}, "Linux kernel"),
            ({"machine": "aarch64"}, "x86-64"),
            ({"uid": 0}, "non-root"),
        ):
            broken = MODULE.HostFacts(**{**facts.__dict__, **replacement})
            self.assertTrue(any(expected in item for item in MODULE.validate_facts(broken)))

    def test_cpu_memory_and_combined_work_disk_have_hard_floors(self) -> None:
        facts = healthy_facts()
        broken = MODULE.HostFacts(
            **{
                **facts.__dict__,
                "effective_cpus": 3.9,
                "effective_memory_bytes": 15 * MODULE.GIB,
                "work_root_free_bytes": 207 * MODULE.GIB,
            }
        )
        errors = MODULE.validate_facts(broken)
        self.assertTrue(any("four effective CPUs" in item for item in errors))
        self.assertTrue(any("16 GiB" in item for item in errors))
        self.assertTrue(any("208 GiB" in item for item in errors))

    def test_private_work_root_and_live_docker_server_are_required(self) -> None:
        facts = healthy_facts()
        broken = MODULE.HostFacts(
            **{
                **facts.__dict__,
                "work_root_private": False,
                "docker_client_present": False,
                "docker_server_version": "permission denied",
            }
        )
        errors = MODULE.validate_facts(broken)
        self.assertTrue(any("mode 0700" in item for item in errors))
        self.assertTrue(any("Docker" in item for item in errors))

    def test_host_evidence_is_bound_to_the_dedicated_work_root(self) -> None:
        facts = healthy_facts()
        broken = MODULE.HostFacts(**{**facts.__dict__, "work_root": "/tmp/echo-os-image-runner"})
        self.assertTrue(
            any(MODULE.RUNNER_WORK_ROOT in item for item in MODULE.validate_facts(broken))
        )
        with self.assertRaisesRegex(MODULE.HostPreflightError, "dedicated"):
            MODULE.collect_facts("/tmp/echo-os-image-runner")

    def test_remote_or_rootless_docker_cannot_host_privileged_device_jobs(self) -> None:
        facts = healthy_facts()
        broken = MODULE.HostFacts(
            **{
                **facts.__dict__,
                "docker_context": "remote-builder",
                "docker_environment_clean": False,
                "docker_socket_ready": False,
                "docker_security_options": ("name=rootless",),
            }
        )
        errors = MODULE.validate_facts(broken)
        self.assertTrue(any("local default Docker socket" in item for item in errors))
        self.assertTrue(any("rootful" in item for item in errors))

        rootful_without_optional_security_features = MODULE.HostFacts(
            **{**facts.__dict__, "docker_security_options": ()}
        )
        self.assertFalse(
            any(
                "rootful" in item
                for item in MODULE.validate_facts(rootful_without_optional_security_features)
            )
        )

    def test_kvm_module_tree_and_block_device_capacity_are_required(self) -> None:
        facts = healthy_facts()
        broken = MODULE.HostFacts(
            **{
                **facts.__dict__,
                "kvm_device_ready": False,
                "kernel_modules_tree_ready": False,
                "loop_max": 63,
                "nbd_max": 15,
                "nbd_max_part": 15,
            }
        )
        errors = MODULE.validate_facts(broken)
        self.assertTrue(any("KVM" in item for item in errors))
        self.assertTrue(any("module tree" in item for item in errors))
        self.assertTrue(any("max_loop" in item for item in errors))
        self.assertTrue(any("nbds_max" in item for item in errors))
        self.assertTrue(any("max_part" in item for item in errors))

    def test_evidence_is_private_atomic_and_cannot_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve() / "host.json"
            payload = MODULE.evidence_payload(healthy_facts())
            MODULE.write_evidence(output, payload)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), payload)
            with self.assertRaises(MODULE.HostPreflightError):
                MODULE.write_evidence(output, payload)

    def test_host_configurator_has_no_runner_registration_or_secret_path(self) -> None:
        script = MODULE_PATH.with_name("configure-linux-image-runner-host.sh").read_text(
            encoding="utf-8"
        )
        for required in (
            "docker.io",
            "coreutils",
            "echo-os-image-runner-cleanup.py",
            "echo-os-image-runner-job-hook.sh",
            "echo-os-image-runner-registration.py",
            "echo-os-image-runner.modules.conf",
            "echo-os-image-runner.modprobe.conf",
            '[[ "$WORK_ROOT" == /srv/echo-os-image-runner ]]',
            'find "$WORK_ROOT" -mindepth 1 -maxdepth 1 -print -quit',
            "work root must be empty before host configuration",
            'grep -Eq "^${RUNNER_USER}:" /etc/passwd',
            "usermod -aG docker,kvm",
            "systemctl enable --now docker.service",
            "modprobe loop max_loop=64",
            "modprobe nbd nbds_max=16 max_part=16",
            'runuser -u "$RUNNER_USER"',
        ):
            self.assertIn(required, script)
        for forbidden in (
            "--token",
            "RUNNER_TOKEN",
            "registration-token",
            "curl ",
            "wget ",
            "config.sh",
        ):
            self.assertNotIn(forbidden, script)

    def test_host_configurator_rejects_a_system_directory_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake_bin = Path(directory) / "bin"
            fake_bin.mkdir()
            fake_uname = fake_bin / "uname"
            fake_uname.write_text(
                "#!/bin/sh\n"
                'case "$1" in\n'
                "  -s) printf 'Linux\\n' ;;\n"
                "  -m) printf 'x86_64\\n' ;;\n"
                "  *) exit 1 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            fake_id = fake_bin / "id"
            fake_id.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = -u ] && [ "$#" -eq 1 ]; then\n'
                "  printf '0\\n'\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_uname.chmod(0o755)
            fake_id.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            completed = subprocess.run(  # noqa: S603
                [
                    str(MODULE_PATH.with_name("configure-linux-image-runner-host.sh")),
                    "/usr",
                    "echo-runner",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
                timeout=10,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("dedicated /srv/echo-os-image-runner", completed.stderr)

    def test_registered_runner_hook_configuration_is_bounded_and_credential_free(self) -> None:
        image_directory = MODULE_PATH.parent
        hook = (image_directory / "runner-host/echo-os-image-runner-job-hook.sh").read_text(
            encoding="utf-8"
        )
        configurator = (image_directory / "configure-linux-image-runner-hooks.sh").read_text(
            encoding="utf-8"
        )
        for required in (
            "ACTIONS_RUNNER_HOOK_JOB_STARTED",
            "ACTIONS_RUNNER_HOOK_JOB_COMPLETED",
            "ECHO_IMAGE_RUNNER_HOOKS_READY",
            '[[ "$RUNNER_APPLICATION_DIR" == /opt/actions-runner ]]',
            '"$RUNNER_APPLICATION_DIR/.env"',
            "stat -c '%s' \"$ENV_FILE\"",
            "-le 65536",
            "chmod 0600",
            ".credentials .credentials_rsaparams .service",
            "echo-os-image-runner-registration.py",
            'runuser -u "$RUNNER_USER"',
        ):
            self.assertIn(required, configurator)
        for required in (
            "/usr/bin/timeout --foreground --signal=TERM 300s",
            "/usr/bin/python3",
            "HOST_WORK_ROOT=/srv/echo-os-image-runner",
            'EXPECTED_WORKSPACE="$HOST_WORK_ROOT/echo-os/echo-os"',
            'EXPECTED_SCRATCH="$HOST_WORK_ROOT/_temp"',
            '"${GITHUB_WORKSPACE:-}"',
            '"${RUNNER_TEMP:-}"',
        ):
            self.assertIn(required, hook)
        for text in (hook, configurator):
            for forbidden in (
                "RUNNER_TOKEN",
                "--token",
                "registration-token",
                "curl ",
                "wget ",
            ):
                self.assertNotIn(forbidden, text)

    def test_registered_runner_hook_rejects_unrelated_paths_before_cleanup(self) -> None:
        hook = MODULE_PATH.parent / "runner-host/echo-os-image-runner-job-hook.sh"
        environment = os.environ.copy()
        environment.update(
            {
                "CI": "true",
                "GITHUB_ACTIONS": "true",
                "GITHUB_WORKSPACE": "/srv/echo-os-image-runner/echo-os/echo-os",
                "RUNNER_TEMP": "/__w/_temp",
            }
        )
        completed = subprocess.run(  # noqa: S603
            [str(hook)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=10,
        )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("dedicated /srv/echo-os-image-runner layout", completed.stderr)
        self.assertNotIn("cleanup executable is unavailable", completed.stderr)

    def test_hook_configurator_rejects_an_unrelated_owned_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake_bin = Path(directory) / "bin"
            fake_bin.mkdir()
            fake_id = fake_bin / "id"
            fake_id.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = -u ] && [ "$#" -eq 1 ]; then\n'
                "  printf '0\\n'\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_id.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            completed = subprocess.run(  # noqa: S603
                [
                    str(MODULE_PATH.with_name("configure-linux-image-runner-hooks.sh")),
                    "/usr",
                    "echo-runner",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
                timeout=10,
            )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("dedicated /opt/actions-runner", completed.stderr)


if __name__ == "__main__":
    unittest.main()
