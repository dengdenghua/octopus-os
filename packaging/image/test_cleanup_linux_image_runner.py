#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).with_name("cleanup-linux-image-runner.py")
SPEC = importlib.util.spec_from_file_location("cleanup_linux_image_runner", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class LinuxImageRunnerCleanupTests(unittest.TestCase):
    def _roots(self, root: Path) -> tuple[Path, Path]:
        checkout_root = root / "echo-os"
        workspace = checkout_root / "echo-os"
        scratch = root / "_temp"
        workspace.mkdir(parents=True)
        scratch.mkdir()
        return workspace, scratch

    def test_removes_only_bounded_generated_workspace_and_echo_scratch_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            workspace, scratch = self._roots(root)
            generated = workspace / "packaging/image/mkosi.output"
            generated.mkdir(parents=True)
            (generated / "echo.raw").write_bytes(b"raw")
            private_bundle = workspace / "deploy/appliance/agent-dist"
            private_bundle.mkdir(parents=True)
            (private_bundle / "agent.whl").write_bytes(b"wheel")
            (scratch / "echo-secure-boot.123456").mkdir()
            (scratch / "echo-secure-boot.123456/db.key").write_text("private\n", encoding="utf-8")
            (scratch / "echo-os-installed.raw").write_bytes(b"disk")
            preserved_workspace = workspace / "README.md"
            preserved_workspace.write_text("source\n", encoding="utf-8")
            preserved_scratch = scratch / "actions-runner-internal"
            preserved_scratch.write_text("keep\n", encoding="utf-8")

            removed = MODULE.cleanup(
                workspace=workspace,
                scratch=scratch,
                runner_work_root=root,
            )

            self.assertFalse(generated.exists())
            self.assertFalse(private_bundle.exists())
            self.assertFalse((scratch / "echo-secure-boot.123456").exists())
            self.assertFalse((scratch / "echo-os-installed.raw").exists())
            self.assertTrue(preserved_workspace.is_file())
            self.assertTrue(preserved_scratch.is_file())
            self.assertEqual(
                removed,
                (
                    "workspace/deploy/appliance/agent-dist",
                    "workspace/packaging/image/mkosi.output",
                    "scratch/echo-os-installed.raw",
                    "scratch/echo-secure-boot.123456",
                ),
            )

    def test_refuses_a_linked_runner_root_or_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            real_root = root / "real-root"
            self._roots(real_root)
            linked_root = root / "linked-root"
            linked_root.symlink_to(real_root, target_is_directory=True)
            with self.assertRaisesRegex(MODULE.RunnerCleanupError, "non-symlink"):
                MODULE.cleanup(
                    workspace=linked_root / "echo-os" / "echo-os",
                    scratch=linked_root / "_temp",
                    runner_work_root=linked_root,
                )

            workspace = root / "workspace-target"
            workspace.mkdir()
            checkout_root = root / "echo-os"
            checkout_root.mkdir()
            linked_workspace = checkout_root / "echo-os"
            linked_workspace.symlink_to(workspace, target_is_directory=True)
            scratch = root / "_temp"
            scratch.mkdir()
            with self.assertRaisesRegex(MODULE.RunnerCleanupError, "non-symlink"):
                MODULE.cleanup(
                    workspace=linked_workspace,
                    scratch=scratch,
                    runner_work_root=root,
                )

    def test_refuses_workspace_or_scratch_outside_the_dedicated_layout(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            workspace, scratch = self._roots(root)
            other_workspace = root / "other" / "echo-os"
            other_workspace.mkdir(parents=True)
            with self.assertRaisesRegex(
                MODULE.RunnerCleanupError,
                "do not match one dedicated",
            ):
                MODULE.cleanup(
                    workspace=other_workspace,
                    scratch=scratch,
                    runner_work_root=root,
                )

            other_scratch = root / "other-temp"
            other_scratch.mkdir()
            with self.assertRaisesRegex(
                MODULE.RunnerCleanupError,
                "do not match one dedicated",
            ):
                MODULE.cleanup(
                    workspace=workspace,
                    scratch=other_scratch,
                    runner_work_root=root,
                )

    def test_default_cleanup_roots_are_exact_and_cannot_be_mixed(self) -> None:
        host_root, container_root = MODULE.RUNNER_WORK_ROOTS
        self.assertEqual(host_root, Path("/srv/echo-os-image-runner"))
        self.assertEqual(container_root, Path("/__w"))
        with self.assertRaisesRegex(
            MODULE.RunnerCleanupError,
            "do not match one dedicated",
        ):
            MODULE.cleanup(
                workspace=host_root / "echo-os" / "echo-os",
                scratch=container_root / "_temp",
            )

    def test_host_hook_and_container_finalizer_layouts_both_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            host_root = root / "host-root"
            container_root = root / "container-root"
            with mock.patch.object(
                MODULE,
                "RUNNER_WORK_ROOTS",
                (host_root, container_root),
            ):
                for layout_root in (host_root, container_root):
                    with self.subTest(layout_root=layout_root):
                        workspace, scratch = self._roots(layout_root)
                        generated = workspace / "packaging/image/mkosi.output"
                        generated.mkdir(parents=True)
                        scratch_generated = scratch / "echo-job-state"
                        scratch_generated.mkdir()
                        removed = MODULE.cleanup(
                            workspace=workspace,
                            scratch=scratch,
                        )
                        self.assertFalse(generated.exists())
                        self.assertFalse(scratch_generated.exists())
                        self.assertIn(
                            "workspace/packaging/image/mkosi.output",
                            removed,
                        )

    def test_refuses_to_follow_a_linked_generated_or_scratch_target(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            workspace, scratch = self._roots(root)
            outside = root / "outside"
            outside.mkdir()
            secret = outside / "must-survive"
            secret.write_text("outside\n", encoding="utf-8")
            generated = workspace / "packaging/image/mkosi.output"
            generated.parent.mkdir(parents=True)
            generated.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(MODULE.RunnerCleanupError, "refusing linked"):
                MODULE.cleanup(
                    workspace=workspace,
                    scratch=scratch,
                    runner_work_root=root,
                )
            self.assertTrue(secret.is_file())

            generated.unlink()
            scratch_link = scratch / "echo-linked-secret"
            scratch_link.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(MODULE.RunnerCleanupError, "refusing linked"):
                MODULE.cleanup(
                    workspace=workspace,
                    scratch=scratch,
                    runner_work_root=root,
                )
            self.assertTrue(secret.is_file())

    def test_cli_requires_the_real_github_actions_environment(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(MODULE.main([]), 1)

    def test_cli_emits_one_success_marker_after_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            workspace, scratch = self._roots(root)
            with (
                mock.patch.object(MODULE, "RUNNER_WORK_ROOTS", (root,)),
                mock.patch.dict(
                    os.environ,
                    {"CI": "true", "GITHUB_ACTIONS": "true"},
                    clear=True,
                ),
            ):
                self.assertEqual(
                    MODULE.main(["--workspace", str(workspace), "--scratch", str(scratch)]),
                    0,
                )

    def test_both_privileged_workflows_end_with_the_always_cleanup_step(self) -> None:
        repository = MODULE_PATH.parents[2]
        suffix = '''
      - name: Remove generated bundle and whole-disk temporaries
        if: always()
        run: >-
          python3 packaging/image/cleanup-linux-image-runner.py
          --workspace "$GITHUB_WORKSPACE"
          --scratch "$RUNNER_TEMP"'''.strip("\n")
        for relative in (
            ".github/workflows/os-image.yml",
            ".github/workflows/ab-update-smoke.yml",
        ):
            with self.subTest(workflow=relative):
                text = (repository / relative).read_text(encoding="utf-8").rstrip()
                self.assertEqual(text.count(suffix), 1)
                self.assertTrue(text.endswith(suffix))


if __name__ == "__main__":
    unittest.main()
