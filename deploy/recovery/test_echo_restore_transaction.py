#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import echo_restore_transaction as restore


class InjectedFailure(RuntimeError):
    pass


def copy_tree_fixture(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)


class RestoreTransactionTests(unittest.TestCase):
    snapshot = "a" * 64
    repository = "b" * 64
    staging_name = "20260826T120000Z-" + "a" * 12

    def fixture(self, directory: str) -> tuple[restore.RestoreRoots, dict[str, bytes]]:
        root = Path(directory)
        home_root = root / "home"
        var_root = root / "var"
        home_root.mkdir(mode=0o755)
        var_root.mkdir(mode=0o755)
        uid = os.getuid()
        gid = os.getgid()
        roots = restore.RestoreRoots(home_root, var_root, uid, gid, uid)

        active_home = roots.active_home
        active_agent = roots.active_agent
        active_home.mkdir(mode=0o750)
        active_agent.mkdir(mode=0o700, parents=True)
        old_home = b"old-home\n"
        old_agent = b"old-agent\n"
        new_home = b"new-home\n"
        new_agent = b"new-agent\n"
        (active_home / "document.txt").write_bytes(old_home)
        (active_agent / "state.json").write_bytes(old_agent)

        restore_root = active_home / restore.RESTORE_DIRECTORY
        restore_root.mkdir(mode=0o700)
        stage = restore_root / self.staging_name
        staged_home = stage / "home" / "echo"
        staged_agent = stage / "var" / "lib" / "echo-agent"
        staged_home.mkdir(mode=0o750, parents=True)
        staged_agent.mkdir(mode=0o700, parents=True)
        stage.chmod(0o700)
        (staged_home / "document.txt").write_bytes(new_home)
        sparse = staged_home / "sparse.bin"
        with sparse.open("wb") as stream:
            stream.seek(1024 * 1024)
            stream.write(b"x")
        (staged_home / "document-link").symlink_to("document.txt")
        (staged_agent / "state.json").write_bytes(new_agent)

        roots.backup_state.parent.mkdir(mode=0o700, parents=True)
        roots.backup_state.write_text(
            json.dumps(
                {
                    "schema": 2,
                    "action": "restore-staged",
                    "repository_id": self.repository,
                    "snapshot_id": self.snapshot,
                    "staging_name": self.staging_name,
                    "verified_full_read": True,
                }
            ),
            encoding="utf-8",
        )
        roots.backup_state.chmod(0o600)
        return roots, {
            "old_home": old_home,
            "old_agent": old_agent,
            "new_home": new_home,
            "new_agent": new_agent,
        }

    def engine(
        self,
        roots: restore.RestoreRoots,
        hook: object | None = None,
    ) -> restore.RestoreTransaction:
        return restore.RestoreTransaction(
            roots,
            copier=copy_tree_fixture,
            hook=hook,  # type: ignore[arg-type]
            syncer=lambda: None,
        )

    def test_promote_is_two_filesystem_atomic_and_boot_allows_only_complete_trial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            roots, data = self.fixture(directory)
            transaction = self.engine(roots)
            plan = transaction.plan()
            self.assertRegex(restore.promotion_token(plan), r"^PROMOTE-ECHO-RESTORE-[0-9a-f]{24}$")
            with self.assertRaises(restore.RestoreTransactionError):
                transaction.promote("PROMOTE-ECHO-RESTORE-wrong")
            promoted = transaction.promote(restore.promotion_token(plan))
            self.assertEqual(promoted["phase"], "promoted")
            self.assertEqual((roots.active_home / "document.txt").read_bytes(), data["new_home"])
            self.assertEqual((roots.active_agent / "state.json").read_bytes(), data["new_agent"])
            rollback_home = roots.rollback_home(str(plan["transaction_id"]))
            rollback_agent = roots.rollback_agent(str(plan["transaction_id"]))
            self.assertEqual((rollback_home / "document.txt").read_bytes(), data["old_home"])
            self.assertEqual((rollback_agent / "state.json").read_bytes(), data["old_agent"])
            self.assertTrue((roots.active_home / restore.RESTORE_DIRECTORY).is_dir())
            self.assertEqual(
                roots.rollback_home_container(str(plan["transaction_id"])).stat().st_mode & 0o777,
                0o700,
            )
            self.assertEqual(
                roots.rollback_agent_container(str(plan["transaction_id"])).stat().st_mode & 0o777,
                0o700,
            )
            self.assertIn("phase=promoted", transaction.health())

    def test_every_rename_boundary_can_resume_without_mixing_old_and_new(self) -> None:
        events = (
            "agent-copy-container:after-create",
            "agent-copy:after-copy",
            "home-retire-container:after-create",
            "home-retire:after-rename",
            "home-install:after-rename",
            "staging-transfer:after-rename",
            "agent-retire-container:after-create",
            "agent-retire:after-rename",
            "agent-install:after-rename",
        )
        for event in events:
            with self.subTest(event=event), tempfile.TemporaryDirectory() as directory:
                roots, data = self.fixture(directory)
                plan = self.engine(roots).plan()
                fired = False

                def fail_once(current: str, expected_event: str = event) -> None:
                    nonlocal fired
                    if current == expected_event and not fired:
                        fired = True
                        raise InjectedFailure(current)

                with self.assertRaises(InjectedFailure):
                    self.engine(roots, fail_once).promote(restore.promotion_token(plan))
                if roots.journal.exists():
                    with self.assertRaises(restore.RestoreTransactionError):
                        self.engine(roots).health()
                resumed = self.engine(roots).promote(restore.promotion_token(plan))
                self.assertEqual(resumed["phase"], "promoted")
                self.assertEqual(
                    (roots.active_home / "document.txt").read_bytes(), data["new_home"]
                )
                self.assertEqual(
                    (roots.active_agent / "state.json").read_bytes(), data["new_agent"]
                )

    def test_trial_can_rollback_without_deleting_trial_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            roots, data = self.fixture(directory)
            transaction = self.engine(roots)
            plan = transaction.plan()
            promoted = transaction.promote(restore.promotion_token(plan))
            (roots.active_home / "trial.txt").write_text("trial-home\n", encoding="utf-8")
            (roots.active_agent / "trial.json").write_text("trial-agent\n", encoding="utf-8")
            rolled_back = transaction.rollback(restore.rollback_token(promoted))
            self.assertEqual(rolled_back["phase"], "rolled-back")
            self.assertEqual((roots.active_home / "document.txt").read_bytes(), data["old_home"])
            self.assertEqual((roots.active_agent / "state.json").read_bytes(), data["old_agent"])
            staged_trial = (
                roots.active_home
                / restore.RESTORE_DIRECTORY
                / self.staging_name
                / "home"
                / "echo"
                / "trial.txt"
            )
            self.assertEqual(staged_trial.read_text(encoding="utf-8"), "trial-home\n")
            rejected_agent = roots.rejected_agent(str(plan["transaction_id"]))
            self.assertEqual(
                (rejected_agent / "trial.json").read_text(encoding="utf-8"),
                "trial-agent\n",
            )
            self.assertFalse(roots.journal.exists())
            state = json.loads(roots.backup_state.read_text(encoding="utf-8"))
            self.assertEqual(state["action"], "restore-rolled-back")
            self.assertIn("rejected_agent", state)

    def test_commit_requires_explicit_token_and_reclaims_only_old_live_trees(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            roots, data = self.fixture(directory)
            transaction = self.engine(roots)
            plan = transaction.plan()
            promoted = transaction.promote(restore.promotion_token(plan))
            with self.assertRaises(restore.RestoreTransactionError):
                transaction.commit("COMMIT-ECHO-RESTORE-wrong")
            committed = transaction.commit(restore.commit_token(promoted))
            self.assertEqual(committed["phase"], "committed")
            self.assertEqual((roots.active_home / "document.txt").read_bytes(), data["new_home"])
            self.assertEqual((roots.active_agent / "state.json").read_bytes(), data["new_agent"])
            self.assertFalse(roots.rollback_home(str(plan["transaction_id"])).exists())
            self.assertFalse(roots.rollback_agent(str(plan["transaction_id"])).exists())
            self.assertTrue((roots.active_home / restore.RESTORE_DIRECTORY).is_dir())
            self.assertFalse(roots.journal.exists())
            state = json.loads(roots.backup_state.read_text(encoding="utf-8"))
            self.assertEqual(state["action"], "restore-committed")

    def test_rollback_and_commit_resume_after_destructive_boundary_interruptions(self) -> None:
        rollback_events = (
            "rollback-agent-save:after-rename",
            "rollback-agent-restore:after-rename",
            "rollback-staging-return:after-rename",
            "rollback-home-save:after-rename",
            "rollback-home-restore:after-rename",
            "rollback-home-container:after-remove",
            "rollback-trial-preserve:after-rename",
            "rollback-state:after-update",
        )
        for event in rollback_events:
            with (
                self.subTest(action="rollback", event=event),
                tempfile.TemporaryDirectory() as directory,
            ):
                roots, data = self.fixture(directory)
                transaction = self.engine(roots)
                plan = transaction.plan()
                promoted = transaction.promote(restore.promotion_token(plan))
                fired = False

                def fail_once(current: str, expected_event: str = event) -> None:
                    nonlocal fired
                    if current == expected_event and not fired:
                        fired = True
                        raise InjectedFailure(current)

                with self.assertRaises(InjectedFailure):
                    self.engine(roots, fail_once).rollback(restore.rollback_token(promoted))
                resumed = self.engine(roots).rollback(restore.rollback_token(promoted))
                self.assertEqual(resumed["phase"], "rolled-back")
                self.assertEqual(
                    (roots.active_home / "document.txt").read_bytes(), data["old_home"]
                )
                self.assertEqual(
                    (roots.active_agent / "state.json").read_bytes(), data["old_agent"]
                )

        commit_events = (
            "commit-home:after-remove",
            "commit-agent:after-remove",
            "commit-state:after-update",
        )
        for event in commit_events:
            with (
                self.subTest(action="commit", event=event),
                tempfile.TemporaryDirectory() as directory,
            ):
                roots, data = self.fixture(directory)
                transaction = self.engine(roots)
                plan = transaction.plan()
                promoted = transaction.promote(restore.promotion_token(plan))
                fired = False

                def fail_once(current: str, expected_event: str = event) -> None:
                    nonlocal fired
                    if current == expected_event and not fired:
                        fired = True
                        raise InjectedFailure(current)

                with self.assertRaises(InjectedFailure):
                    self.engine(roots, fail_once).commit(restore.commit_token(promoted))
                resumed = self.engine(roots).commit(restore.commit_token(promoted))
                self.assertEqual(resumed["phase"], "committed")
                self.assertEqual(
                    (roots.active_home / "document.txt").read_bytes(), data["new_home"]
                )
                self.assertEqual(
                    (roots.active_agent / "state.json").read_bytes(), data["new_agent"]
                )

    def test_unsafe_stage_or_private_state_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            roots, _ = self.fixture(directory)
            roots.backup_state.chmod(0o644)
            with self.assertRaises(restore.RestoreTransactionError):
                self.engine(roots).plan()
        with tempfile.TemporaryDirectory() as directory:
            roots, _ = self.fixture(directory)
            unsafe = (
                roots.active_home
                / restore.RESTORE_DIRECTORY
                / self.staging_name
                / "home"
                / "echo"
                / "unsafe"
            )
            unsafe.symlink_to("../../../../../../etc/shadow")
            with self.assertRaises(restore.RestoreTransactionError):
                self.engine(roots).plan()
        with tempfile.TemporaryDirectory() as directory:
            roots, _ = self.fixture(directory)
            staged_home = (
                roots.active_home
                / restore.RESTORE_DIRECTORY
                / self.staging_name
                / "home"
                / "echo"
            )
            os.link(roots.active_home / "document.txt", staged_home / "external-hardlink")
            with self.assertRaises(restore.RestoreTransactionError):
                self.engine(roots).plan()
        with tempfile.TemporaryDirectory() as directory:
            roots, _ = self.fixture(directory)
            state = json.loads(roots.backup_state.read_text(encoding="utf-8"))
            state["schema"] = True
            roots.backup_state.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaises(restore.RestoreTransactionError):
                self.engine(roots).plan()
        with tempfile.TemporaryDirectory() as directory:
            roots, _ = self.fixture(directory)
            reserved = (
                roots.active_home
                / restore.RESTORE_DIRECTORY
                / self.staging_name
                / "home"
                / "echo"
                / restore.RESTORE_DIRECTORY
            )
            reserved.mkdir()
            with self.assertRaises(restore.RestoreTransactionError):
                self.engine(roots).plan()

    def test_deep_or_cyclic_restore_tree_and_exposed_trial_container_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            roots, _ = self.fixture(directory)
            deep = (
                roots.active_home
                / restore.RESTORE_DIRECTORY
                / self.staging_name
                / "home"
                / "echo"
            )
            for _ in range(restore.MAX_TREE_DEPTH + 1):
                deep /= "d"
                deep.mkdir()
            with self.assertRaises(restore.RestoreTransactionError):
                self.engine(roots).plan()
        with tempfile.TemporaryDirectory() as directory:
            roots, _ = self.fixture(directory)
            staged_home = (
                roots.active_home
                / restore.RESTORE_DIRECTORY
                / self.staging_name
                / "home"
                / "echo"
            )
            (staged_home / "loop-a").symlink_to("loop-b")
            (staged_home / "loop-b").symlink_to("loop-a")
            with self.assertRaises(restore.RestoreTransactionError):
                self.engine(roots).plan()
        with tempfile.TemporaryDirectory() as directory:
            roots, _ = self.fixture(directory)
            transaction = self.engine(roots)
            plan = transaction.plan()
            transaction.promote(restore.promotion_token(plan))
            roots.rollback_home_container(str(plan["transaction_id"])).chmod(0o755)
            with self.assertRaises(restore.RestoreTransactionError):
                transaction.health()


if __name__ == "__main__":
    unittest.main()
