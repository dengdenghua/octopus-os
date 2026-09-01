#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).with_name("echo_update_trust.py")
VERIFIER_PATH = (
    Path(__file__).parents[1] / "installer" / "verify_public_keyring.py"
).resolve()
SPEC = importlib.util.spec_from_file_location("echo_update_trust", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
TRUST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRUST)


def packet(tag: int, body: bytes) -> bytes:
    if len(body) >= 192:
        raise ValueError("fixture body is too large")
    return bytes((0xC0 | tag, len(body))) + body


class EchoUpdateTrustTests(unittest.TestCase):
    old = "A" * 40
    new = "B" * 40

    def keyring(self, *labels: str) -> bytes:
        return b"".join(packet(6, b"\x04" + label.encode()) for label in labels)

    def materialize_system(
        self,
        root: Path,
        generation: int,
        keyring: bytes,
        trusted: list[str],
        retired: list[str],
    ) -> tuple[Path, Path]:
        keyring_path = root / "system-keyring.gpg"
        policy_path = root / "system-policy.json"
        keyring_path.unlink(missing_ok=True)
        policy_path.unlink(missing_ok=True)
        keyring_path.write_bytes(keyring)
        policy = TRUST.make_policy(keyring, generation, trusted, retired)
        policy_path.write_bytes(TRUST.canonical_policy(policy))
        keyring_path.chmod(0o444)
        policy_path.chmod(0o444)
        return policy_path, keyring_path

    def promote(
        self,
        policy: Path,
        keyring: Path,
        state: Path,
    ) -> tuple[str, dict[str, object]]:
        return TRUST.promote(
            policy,
            keyring,
            state,
            VERIFIER_PATH,
            expected_uid=os.getuid(),
        )

    def test_bridge_then_retirement_survives_root_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            state = root / "state"
            policy1, keyring1 = self.materialize_system(
                root, 1, self.keyring("old"), [self.old], []
            )
            source, first = self.promote(policy1, keyring1, state)
            self.assertEqual((source, first["generation"]), ("bootstrap", 1))

            policy2, keyring2 = self.materialize_system(
                root, 2, self.keyring("old", "new"), [self.old, self.new], []
            )
            source, bridge = self.promote(policy2, keyring2, state)
            self.assertEqual((source, bridge["generation"]), ("promoted", 2))

            policy3, keyring3 = self.materialize_system(
                root, 3, self.keyring("new"), [self.new], [self.old]
            )
            source, retired = self.promote(policy3, keyring3, state)
            self.assertEqual((source, retired["generation"]), ("promoted", 3))

            # Simulate booting the old generation-1 root after an A/B rollback.
            policy1, keyring1 = self.materialize_system(
                root, 1, self.keyring("old"), [self.old], []
            )
            selected_source, selected, selected_keyring = TRUST.select_keyring(
                policy1,
                keyring1,
                state,
                VERIFIER_PATH,
                expected_uid=os.getuid(),
            )
            self.assertEqual(selected_source, "managed")
            self.assertEqual(selected["generation"], 3)
            self.assertEqual(selected["trusted_fingerprints"], [self.new])
            self.assertEqual(selected["retired_fingerprints"], [self.old])
            self.assertEqual(selected_keyring.read_bytes(), self.keyring("new"))

            source, retained = self.promote(policy1, keyring1, state)
            self.assertEqual((source, retained["generation"]), ("retained", 3))

    def test_transition_rejects_gaps_unannounced_drop_and_unretirement(self) -> None:
        previous = TRUST.make_policy(self.keyring("old"), 1, [self.old], [])
        gap = TRUST.make_policy(self.keyring("old", "new"), 3, [self.old, self.new], [])
        with self.assertRaisesRegex(TRUST.TrustError, "advance exactly once"):
            TRUST.validate_transition(previous, gap)

        dropped = TRUST.make_policy(self.keyring("new"), 2, [self.new], [])
        with self.assertRaisesRegex(TRUST.TrustError, "explicitly retired"):
            TRUST.validate_transition(previous, dropped)

        retired = TRUST.make_policy(self.keyring("new"), 2, [self.new], [self.old])
        reintroduced = TRUST.make_policy(
            self.keyring("old", "new"), 3, [self.old, self.new], []
        )
        with self.assertRaisesRegex(TRUST.TrustError, "cannot become unretired"):
            TRUST.validate_transition(retired, reintroduced)

    def test_same_generation_cannot_change_its_keyring(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            state = root / "state"
            policy, keyring = self.materialize_system(
                root, 1, self.keyring("old"), [self.old], []
            )
            self.promote(policy, keyring, state)
            conflict_policy, conflict_keyring = self.materialize_system(
                root, 1, self.keyring("new"), [self.new], []
            )
            with self.assertRaisesRegex(TRUST.TrustError, "conflicting policies"):
                self.promote(conflict_policy, conflict_keyring, state)

    def test_promotion_lock_serializes_and_retry_cleans_atomic_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            state = root / "state"
            policy, keyring = self.materialize_system(
                root, 1, self.keyring("old"), [self.old], []
            )
            self.promote(policy, keyring, state)
            interrupted = state / ".pending-keyring.gpg.interrupted"
            interrupted.write_bytes(b"partial")
            interrupted.chmod(0o400)
            source, current = self.promote(policy, keyring, state)
            self.assertEqual((source, current["generation"]), ("current", 1))
            self.assertFalse(interrupted.exists())

            with TRUST.promotion_lock(state, os.getuid()), self.assertRaisesRegex(
                TRUST.TrustError, "another update trust promotion"
            ):
                self.promote(policy, keyring, state)

    def test_pending_keyring_then_policy_recovers_after_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            state = root / "state"
            policy1, keyring1 = self.materialize_system(
                root, 1, self.keyring("old"), [self.old], []
            )
            self.promote(policy1, keyring1, state)
            candidate_keyring = self.keyring("old", "new")
            candidate = TRUST.make_policy(candidate_keyring, 2, [self.old, self.new], [])
            paths = TRUST.state_paths(state)
            TRUST.atomic_write(paths["pending_keyring"], candidate_keyring, 0o400)
            TRUST.atomic_write(
                paths["pending_policy"], TRUST.canonical_policy(candidate), 0o400
            )

            source, selected_policy, selected_keyring = TRUST.select_keyring(
                policy1,
                keyring1,
                state,
                VERIFIER_PATH,
                expected_uid=os.getuid(),
            )
            self.assertEqual(source, "managed")
            self.assertEqual(selected_policy["generation"], 2)
            self.assertEqual(selected_keyring.read_bytes(), candidate_keyring)
            self.assertFalse(paths["pending_policy"].exists())
            self.assertFalse(paths["pending_keyring"].exists())

    def test_policy_only_recovery_finishes_after_keyring_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            state = root / "state"
            policy1, keyring1 = self.materialize_system(
                root, 1, self.keyring("old"), [self.old], []
            )
            self.promote(policy1, keyring1, state)
            candidate_keyring = self.keyring("old", "new")
            candidate = TRUST.make_policy(candidate_keyring, 2, [self.old, self.new], [])
            paths = TRUST.state_paths(state)
            TRUST.atomic_write(paths["pending_keyring"], candidate_keyring, 0o400)
            TRUST.atomic_write(
                paths["pending_policy"], TRUST.canonical_policy(candidate), 0o400
            )
            os.replace(paths["pending_keyring"], paths["keyring"])

            TRUST.recover_pending(
                state,
                TRUST.load_verifier(VERIFIER_PATH),
                expected_uid=os.getuid(),
            )
            current = TRUST.load_current(
                state,
                TRUST.load_verifier(VERIFIER_PATH),
                expected_uid=os.getuid(),
            )
            assert current is not None
            self.assertEqual(current[0]["generation"], 2)
            self.assertEqual(current[1], candidate_keyring)

    def test_primary_fingerprint_extraction_rejects_revoked_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            gpg = root / "gpg"
            keyring = root / "keyring.gpg"
            gpg.write_bytes(b"gpg")
            keyring.write_bytes(self.keyring("old"))
            gpg.chmod(0o755)
            accepted = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=f"pub:-:::::::::\nfpr:::::::::{self.old}:\n".encode(), stderr=b""
            )
            with mock.patch.object(TRUST.subprocess, "run", return_value=accepted):
                self.assertEqual(TRUST.primary_fingerprints(gpg, keyring), [self.old])

            revoked = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=b"pub:r:::::::::\n", stderr=b""
            )
            with mock.patch.object(TRUST.subprocess, "run", return_value=revoked), self.assertRaisesRegex(
                TRUST.TrustError, "revoked"
            ):
                TRUST.primary_fingerprints(gpg, keyring)

    def test_create_policy_cli_binds_keyring_fingerprints_and_retirement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            keyring = root / "keyring.gpg"
            gpg = root / "gpg"
            output = root / "policy.json"
            keyring.write_bytes(self.keyring("new"))
            gpg.write_bytes(b"gpg")
            gpg.chmod(0o755)
            with mock.patch.object(
                TRUST, "primary_fingerprints", return_value=[self.new]
            ):
                self.assertEqual(
                    TRUST.main(
                        (
                            "create-policy",
                            "--keyring",
                            str(keyring),
                            "--generation",
                            "3",
                            "--retired-fingerprint",
                            self.old,
                            "--gpg",
                            str(gpg),
                            "--verifier",
                            str(VERIFIER_PATH),
                            "--output",
                            str(output),
                        )
                    ),
                    0,
                )
            policy = TRUST.parse_policy(output.read_bytes())
            self.assertEqual(policy["generation"], 3)
            self.assertEqual(policy["trusted_fingerprints"], [self.new])
            self.assertEqual(policy["retired_fingerprints"], [self.old])


if __name__ == "__main__":
    unittest.main()
