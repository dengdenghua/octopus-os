#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
FIXTURE = SCRIPT_DIR / "agent-recovery-task-runs.json"
SPEC = importlib.util.spec_from_file_location(
    "verify_agent_recovery_fixture",
    SCRIPT_DIR / "verify-agent-recovery-fixture.py",
)
assert SPEC is not None and SPEC.loader is not None
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class AgentRecoveryFixtureTests(unittest.TestCase):
    def test_repository_fixture_is_one_inert_expired_task(self) -> None:
        task = verifier.validate(FIXTURE)
        self.assertEqual(task["task_id"], verifier.TASK_ID)
        self.assertEqual(task["latest_checkpoint_id"], 88)
        self.assertEqual(task["lease"]["holder_id"], "worker-before-power-loss")

    def test_exact_observed_copy_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            observed = Path(directory) / "task_runs.json"
            observed.write_bytes(FIXTURE.read_bytes())
            task = verifier.verify_unchanged(FIXTURE, observed)
        self.assertEqual(task["status"], "running")

    def test_semantic_recovery_mutation_is_rejected(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["tasks"][0]["metadata"]["takeover_at"] = "2026-08-26T00:01:00Z"
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "changed.json"
            changed.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "recovery mutations"):
                verifier.validate(changed)

    def test_even_harmless_serialization_change_fails_read_only_proof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "changed.json"
            changed.write_bytes(FIXTURE.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "changed during cold-boot discovery"):
                verifier.verify_unchanged(FIXTURE, changed)

    def test_live_lease_is_rejected(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["tasks"][0]["lease"]["expires_at"] = 4102444800.0
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "live.json"
            changed.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not an expired"):
                verifier.validate(changed)


if __name__ == "__main__":
    unittest.main()
