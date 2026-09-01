#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("verify-linux-image-runner.py")
SPEC = importlib.util.spec_from_file_location("verify_linux_image_runner", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def healthy_facts(*, same_device: bool = True) -> object:
    workspace_device = 11
    scratch_device = workspace_device if same_device else 12
    workspace_free = 256 * MODULE.GIB if same_device else 80 * MODULE.GIB
    scratch_free = 256 * MODULE.GIB if same_device else 192 * MODULE.GIB
    return MODULE.RunnerFacts(
        system="Linux",
        machine="x86_64",
        uid=0,
        effective_cpus=8.0,
        effective_memory_bytes=32 * MODULE.GIB,
        storage=(
            MODULE.StorageFact(
                "workspace",
                str(MODULE.EXPECTED_WORKSPACE),
                workspace_device,
                workspace_free,
                MODULE.WORKSPACE_REQUIRED_BYTES,
            ),
            MODULE.StorageFact(
                "scratch",
                str(MODULE.EXPECTED_SCRATCH),
                scratch_device,
                scratch_free,
                MODULE.SCRATCH_REQUIRED_BYTES,
            ),
        ),
        kvm_device_ready=True,
        qemu_kvm_supported=True,
        free_loop_devices=8,
        free_nbd_devices=4,
        secure_boot_firmware_descriptors=2,
        available_tools=MODULE.REQUIRED_TOOLS,
    )


class LinuxImageRunnerTests(unittest.TestCase):
    def test_complete_runner_emits_one_bounded_marker(self) -> None:
        marker = MODULE.success_marker(healthy_facts())
        self.assertEqual(
            marker,
            "ECHO_IMAGE_RUNNER_READY arch=x86_64 cpu=8 memory-gib=32 "
            "storage-margin-gib=48 kvm=ready loops=8 nbd=4 "
            "secure-boot-firmware=2",
        )

    def test_platform_architecture_and_root_are_required(self) -> None:
        facts = healthy_facts()
        for replacement, expected in (
            ({"system": "Darwin"}, "Linux kernel"),
            ({"machine": "aarch64"}, "x86-64"),
            ({"uid": 1000}, "privileged root"),
        ):
            broken = MODULE.RunnerFacts(**{**facts.__dict__, **replacement})
            self.assertTrue(any(expected in item for item in MODULE.validate_facts(broken)))

    def test_effective_cgroup_cpu_and_memory_have_hard_floors(self) -> None:
        facts = healthy_facts()
        broken = MODULE.RunnerFacts(
            **{
                **facts.__dict__,
                "effective_cpus": 3.9,
                "effective_memory_bytes": 15 * MODULE.GIB,
            }
        )
        errors = MODULE.validate_facts(broken)
        self.assertTrue(any("four effective CPUs" in item for item in errors))
        self.assertTrue(any("16 GiB" in item for item in errors))

    def test_same_filesystem_accumulates_workspace_and_scratch_capacity(self) -> None:
        facts = healthy_facts()
        storage = tuple(
            MODULE.StorageFact(
                item.role,
                item.path,
                item.device_id,
                207 * MODULE.GIB,
                item.required_bytes,
            )
            for item in facts.storage
        )
        broken = MODULE.RunnerFacts(**{**facts.__dict__, "storage": storage})
        self.assertIn(
            "runner filesystem does not have the required free space",
            MODULE.validate_facts(broken),
        )

    def test_distinct_filesystems_enforce_each_role_independently(self) -> None:
        facts = healthy_facts(same_device=False)
        for storage in (
            (
                MODULE.StorageFact(
                    "workspace",
                    str(MODULE.EXPECTED_WORKSPACE),
                    11,
                    47 * MODULE.GIB,
                    48 * MODULE.GIB,
                ),
                MODULE.StorageFact(
                    "scratch",
                    str(MODULE.EXPECTED_SCRATCH),
                    12,
                    160 * MODULE.GIB,
                    160 * MODULE.GIB,
                ),
            ),
            (
                MODULE.StorageFact(
                    "workspace",
                    str(MODULE.EXPECTED_WORKSPACE),
                    11,
                    48 * MODULE.GIB,
                    48 * MODULE.GIB,
                ),
                MODULE.StorageFact(
                    "scratch",
                    str(MODULE.EXPECTED_SCRATCH),
                    12,
                    159 * MODULE.GIB,
                    160 * MODULE.GIB,
                ),
            ),
        ):
            with self.subTest(storage=storage):
                broken = MODULE.RunnerFacts(**{**facts.__dict__, "storage": storage})
                self.assertIn(
                    "runner filesystem does not have the required free space",
                    MODULE.validate_facts(broken),
                )

    def test_kvm_and_secure_boot_firmware_are_mandatory(self) -> None:
        facts = healthy_facts()
        broken = MODULE.RunnerFacts(
            **{
                **facts.__dict__,
                "kvm_device_ready": False,
                "qemu_kvm_supported": False,
                "secure_boot_firmware_descriptors": 0,
            }
        )
        errors = MODULE.validate_facts(broken)
        self.assertTrue(any("KVM" in item for item in errors))
        self.assertTrue(any("Secure-Boot" in item for item in errors))

    def test_loop_nbd_and_complete_toolchain_are_mandatory(self) -> None:
        facts = healthy_facts()
        broken = MODULE.RunnerFacts(
            **{
                **facts.__dict__,
                "free_loop_devices": 3,
                "free_nbd_devices": 1,
                "available_tools": tuple(item for item in MODULE.REQUIRED_TOOLS if item != "mkosi"),
            }
        )
        errors = MODULE.validate_facts(broken)
        self.assertTrue(any("loop" in item for item in errors))
        self.assertTrue(any("NBD" in item for item in errors))
        self.assertTrue(any("mkosi" in item for item in errors))

    def test_storage_roles_and_numeric_facts_are_fail_closed(self) -> None:
        facts = healthy_facts()
        storage = (
            MODULE.StorageFact("workspace", str(MODULE.EXPECTED_WORKSPACE), -1, -1, 0),
            MODULE.StorageFact("workspace", str(MODULE.EXPECTED_WORKSPACE), -1, -1, 0),
        )
        broken = MODULE.RunnerFacts(**{**facts.__dict__, "storage": storage})
        errors = MODULE.validate_facts(broken)
        self.assertTrue(any("workspace and scratch" in item for item in errors))
        self.assertTrue(any("storage facts are invalid" in item for item in errors))

    def test_storage_facts_and_collection_are_bound_to_the_runner_layout(self) -> None:
        facts = healthy_facts()
        storage = tuple(
            MODULE.StorageFact(
                item.role,
                "/tmp/elsewhere",
                item.device_id,
                item.free_bytes,
                item.required_bytes,
            )
            for item in facts.storage
        )
        broken = MODULE.RunnerFacts(**{**facts.__dict__, "storage": storage})
        self.assertTrue(
            any("outside the dedicated layout" in item for item in MODULE.validate_facts(broken))
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(MODULE.RunnerPreflightError, "dedicated"),
        ):
            MODULE._storage_fact(
                directory,
                "workspace",
                MODULE.WORKSPACE_REQUIRED_BYTES,
                MODULE.EXPECTED_WORKSPACE,
            )

    def test_evidence_is_private_atomic_and_cannot_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve() / "runner.json"
            payload = MODULE.evidence_payload(healthy_facts())
            self.assertEqual(payload["schema"], 2)
            self.assertEqual(
                [item["path"] for item in payload["facts"]["storage"]],
                [str(MODULE.EXPECTED_WORKSPACE), str(MODULE.EXPECTED_SCRATCH)],
            )
            MODULE.write_evidence(output, payload)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), payload)
            with self.assertRaises(MODULE.RunnerPreflightError):
                MODULE.write_evidence(output, payload)


if __name__ == "__main__":
    unittest.main()
