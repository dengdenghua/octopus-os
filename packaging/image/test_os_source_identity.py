#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("os_source_identity.py")
SPEC = importlib.util.spec_from_file_location("os_source_identity", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OsSourceIdentityTests(unittest.TestCase):
    def repository(self, root: Path) -> tuple[Path, str]:
        repo = root / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("Echo OS\n", encoding="utf-8")
        for arguments in (
            ("init", "-q"),
            ("config", "user.name", "Echo Test"),
            ("config", "user.email", "echo@example.invalid"),
            ("add", "."),
            ("commit", "-qm", "fixture"),
            (
                "remote",
                "add",
                "origin",
                "https://github.com/example/echo-os.git",
            ),
        ):
            subprocess.run(["git", *arguments], cwd=repo, check=True, capture_output=True)
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return repo, commit

    def test_captures_one_clean_commit_outside_the_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo, commit = self.repository(root)
            output = root / "source.json"

            payload = MODULE.capture_identity(repo, commit)
            MODULE.write_identity(output, payload, repo)
            identity = MODULE.load_identity(output)

            self.assertEqual(identity["commit"], commit)
            self.assertEqual(identity["repository"], "https://github.com/example/echo-os.git")
            self.assertFalse(identity["dirty"])
            self.assertRegex(identity["tree"], r"^[0-9a-f]{40}$")
            self.assertRegex(identity["manifest_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o444)
            self.assertEqual(MODULE.verify_repository(repo, identity)["commit"], commit)

            (repo / "README.md").write_text("changed after capture\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.SourceIdentityError, "uncommitted or untracked"):
                MODULE.verify_repository(repo, identity)

    def test_rejects_dirty_source_and_mismatched_expected_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo, commit = self.repository(root)
            (repo / "README.md").write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.SourceIdentityError, "uncommitted or untracked"):
                MODULE.capture_identity(repo, commit)

            subprocess.run(
                ["git", "checkout", "--", "README.md"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            with self.assertRaisesRegex(MODULE.SourceIdentityError, "workflow expected"):
                MODULE.capture_identity(repo, "f" * 40)

    def test_rejects_credentials_unknown_fields_and_dirty_self_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo, commit = self.repository(root)
            subprocess.run(
                [
                    "git",
                    "remote",
                    "set-url",
                    "origin",
                    "https://user:token@github.com/example/echo-os.git",
                ],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            with self.assertRaisesRegex(MODULE.SourceIdentityError, "fields are invalid"):
                MODULE.capture_identity(repo, commit)

            subprocess.run(
                [
                    "git",
                    "remote",
                    "set-url",
                    "origin",
                    "https://github.com/example/echo-os.git",
                ],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            payload = MODULE.capture_identity(repo, commit)
            payload["branch"] = "main"
            manifest = root / "invalid.json"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.SourceIdentityError, "top-level"):
                MODULE.load_identity(manifest)

            payload.pop("branch")
            with self.assertRaisesRegex(MODULE.SourceIdentityError, "outside its Git tree"):
                MODULE.write_identity(repo / "source.json", payload, repo)


if __name__ == "__main__":
    unittest.main()
