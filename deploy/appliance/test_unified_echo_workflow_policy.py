#!/usr/bin/env python3
"""Regression checks for the unified Echo source and release workflows."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    ".github/workflows/ci.yml",
    ".github/workflows/os-image.yml",
    ".github/workflows/ab-update-smoke.yml",
    ".github/workflows/appliance-release.yml",
    ".github/workflows/delivery-release-candidate.yml",
)
DEDICATED_IMAGE_RUNNER = "runs-on: [self-hosted, linux, x64, echo-os-image]"


class UnifiedEchoWorkflowPolicyTests(unittest.TestCase):
    def _workflow(self, relative_path: str) -> str:
        return (REPO_ROOT / relative_path).read_text(encoding="utf-8")

    def assert_dedicated_image_runner(self, relative_path: str, job_name: str) -> None:
        workflow = self._workflow(relative_path)
        match = re.search(
            rf"(?ms)^  {re.escape(job_name)}:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
            workflow,
        )
        self.assertIsNotNone(match, f"missing workflow job: {relative_path}: {job_name}")
        assert match is not None
        body = match.group("body")
        self.assertIn(DEDICATED_IMAGE_RUNNER, body)
        self.assertNotIn("vars.ECHO_OS_IMAGE_RUNNER", body)
        self.assertNotIn("ubuntu-24.04", body)

    def test_workflows_use_only_the_current_echo_checkout(self) -> None:
        forbidden = (
            "ECHO_AGENT_READ_TOKEN",
            "checkout-source",
            "verify-source-lock",
            "../echo-agent",
        )
        for relative_path in WORKFLOWS:
            workflow = self._workflow(relative_path)
            for marker in forbidden:
                self.assertNotIn(marker, workflow, f"{relative_path} still contains {marker}")

    def test_release_workflows_build_the_current_echo_bundle(self) -> None:
        for relative_path in (
            ".github/workflows/os-image.yml",
            ".github/workflows/ab-update-smoke.yml",
            ".github/workflows/appliance-release.yml",
            ".github/workflows/ci.yml",
        ):
            self.assertIn("prepare-agent-bundle.sh", self._workflow(relative_path))

    def test_delivery_gate_uses_the_unified_source_preflight(self) -> None:
        for relative_path in (
            ".github/workflows/os-image.yml",
            ".github/workflows/ab-update-smoke.yml",
            ".github/workflows/delivery-release-candidate.yml",
        ):
            workflow = self._workflow(relative_path)
            self.assertIn("delivery_source_preflight.py", workflow)
            self.assertNotIn("secrets.ECHO_AGENT_READ_TOKEN", workflow)

    def test_containerized_online_source_gates_install_static_contract_tools(self) -> None:
        required_packages = {
            "bash",
            "ca-certificates",
            "coreutils",
            "gh",
            "git",
            "grep",
            "mawk",
            "python3",
            "sed",
        }
        for relative_path in (
            ".github/workflows/os-image.yml",
            ".github/workflows/ab-update-smoke.yml",
        ):
            workflow = self._workflow(relative_path)
            dependency_step = workflow.split("- name: Install source-contract dependencies", 1)[
                1
            ].split("- uses:", 1)[0]
            installed_packages = set(re.findall(r"[a-z0-9][a-z0-9+.-]*", dependency_step))
            self.assertTrue(
                required_packages <= installed_packages,
                f"{relative_path} is missing source-contract packages: "
                f"{sorted(required_packages - installed_packages)}",
            )

    def test_privileged_image_jobs_have_no_hosted_runner_fallback(self) -> None:
        self.assert_dedicated_image_runner(".github/workflows/os-image.yml", "build-and-boot")
        self.assert_dedicated_image_runner(
            ".github/workflows/ab-update-smoke.yml", "signed-update-rollback"
        )


if __name__ == "__main__":
    unittest.main()
