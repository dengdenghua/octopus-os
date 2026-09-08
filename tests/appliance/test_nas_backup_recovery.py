"""NAS restore data and retry behavior with real files and controlled platform/process seams."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from deploy.appliance import nas_data_backup as backup
from deploy.appliance import nas_data_backup_support as support

DATA = b"known-photo-content"
SNAPSHOT = "a" * 64


@contextmanager
def password_file(password):
    with tempfile.TemporaryFile() as secret:
        secret.write(password)
        secret.flush()
        yield secret.fileno()


@pytest.fixture
def restored_library(tmp_path, monkeypatch):
    deployment, repository, target = (
        tmp_path / "deployment",
        tmp_path / "repository",
        tmp_path / "deployment" / "storage",
    )
    target.mkdir(parents=True)
    repository.mkdir()
    receipts = tmp_path / "receipts"
    state = SimpleNamespace(
        commands=[], check_calls=0, fail_check=False, corrupt=False, fail_restore=False
    )

    def private_directory(path):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)

    # This fixture exercises file contents and real receipt I/O on unprivileged
    # CI; it makes no claim about Linux ownership or directory-fsync behavior.
    monkeypatch.setattr(support, "_private_directory", private_directory)
    monkeypatch.setattr(support, "_private_file", lambda info: stat.S_ISREG(info.st_mode))
    monkeypatch.setattr(support, "_sync_directory", lambda _path: None)
    monkeypatch.setattr(backup, "_context", lambda **_kwargs: (repository, target))
    monkeypatch.setattr(backup, "_operation_lock", nullcontext)
    monkeypatch.setattr(backup, "_password_memfd", password_file)
    monkeypatch.setattr(backup, "_restored_root", lambda staging, _original: staging / "source")

    def runner(command, **_kwargs):
        state.commands.append(command)
        output, error, code = "", "", 0
        if command[-2:] == ["cat", "config"]:
            output = json.dumps({"id": "b" * 64})
        elif "snapshots" in command:
            output = json.dumps(
                [
                    {
                        "id": SNAPSHOT,
                        "paths": [str(tmp_path / "source")],
                        "tags": [backup.TAG],
                        "time": "2026-09-05T00:00:00Z",
                    }
                ]
            )
        elif "restore" in command:
            staging = Path(command[command.index("--target") + 1]) / "source"
            staging.mkdir()
            (staging / "photo.bin").write_bytes(DATA)
            if state.fail_restore:
                code, error = 1, "write pack: no space left on device"
        elif "check" in command:
            state.check_calls += 1
            if state.fail_check and state.check_calls == 2:
                code, error = 1, "network is unreachable"
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, code, output, error)

    def exchange(left, right):
        # Controlled exchange on temporary files, not a Linux atomicity test.
        old = left.parent / "empty-swap"
        left.rename(old)
        right.rename(left)
        old.rename(right)
        if state.corrupt:
            (left / "photo.bin").write_bytes(b"X" * len(DATA))

    state.target, state.receipts, state.exchange = target, receipts, exchange
    state.arguments = dict(
        repository=repository,
        repository_mount=tmp_path,
        deployment_root=deployment,
        appliance_env=None,
        selector="latest",
        confirmation=f"RESTORE ECHO NAS {SNAPSHOT} TO {target}",
        password=b"controlled-test-password",
        runner=runner,
        exchange=exchange,
        receipt_directory=receipts,
    )
    return state


@pytest.mark.parametrize(
    "stderr,code",
    [
        ("write pack: no space left on device", "insufficient_space"),
        ("open config: permission denied", "permission_denied"),
        ("dial tcp: network is unreachable", "network_unavailable"),
        ("open repository: no such file or directory", "repository_unavailable"),
        ("unexpected remote response secret=password /private/repository", "unknown"),
    ],
)
def test_engine_diagnostics_use_evidence_without_echoing_sensitive_output(stderr, code):
    def runner(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 1, "", stderr)

    with (
        password_file(b"controlled-test-password") as descriptor,
        pytest.raises(backup.NasDataBackupError) as caught,
    ):
        backup._restic(["restic", "restore"], descriptor, runner)
    diagnostic = caught.value.public()
    assert diagnostic["code"] == code and diagnostic["phase"] == "restore_transfer"
    assert diagnostic["nextStep"]
    assert "password" not in json.dumps(diagnostic) and "/private" not in json.dumps(diagnostic)


def test_same_size_content_change_is_detected_before_reporting_success(restored_library):
    state = restored_library
    state.corrupt = True
    with pytest.raises(backup.NasDataBackupError) as caught:
        backup.restore(**state.arguments)
    assert caught.value.code == "content_mismatch"
    assert caught.value.committed is True
    assert (state.target / "photo.bin").read_bytes() != DATA
    with pytest.raises(backup.NasDataBackupError, match="Backup operation") as retried:
        backup.restore(**state.arguments, verify_only=True)
    assert retried.value.code == "content_mismatch"
    assert len([command for command in state.commands if "restore" in command]) == 1


def test_repository_check_failure_occurs_before_live_target_promotion(restored_library):
    state = restored_library
    state.fail_check = True
    with pytest.raises(backup.NasDataBackupError) as caught:
        backup.restore(**state.arguments)
    assert caught.value.code == "network_unavailable" and caught.value.committed is False
    assert list(state.target.iterdir()) == []
    state.fail_check = False
    result = backup.restore(**state.arguments)
    assert result["contentVerified"] is True
    assert (state.target / "photo.bin").read_bytes() == DATA


@pytest.mark.parametrize("failed_save", [2, 3], ids=["after-exchange", "after-verification"])
def test_late_receipt_failure_can_verify_existing_tree_without_rewriting(
    restored_library, monkeypatch, failed_save
):
    state = restored_library
    original = support.RestoreReceipts.save
    saves = 0

    def save(receipts, value):
        nonlocal saves
        saves += 1
        if saves == failed_save:
            raise OSError(errno.ENOSPC, "private receipt directory is full")
        original(receipts, value)

    monkeypatch.setattr(support.RestoreReceipts, "save", save)
    with pytest.raises(backup.NasDataBackupError) as caught:
        backup.restore(**state.arguments)
    assert caught.value.committed is True
    assert caught.value.public()["nextStep"] == "verify_restore_without_rewriting"
    content = state.target / "photo.bin"
    before = (content.read_bytes(), content.stat().st_mtime_ns)
    result = backup.restore(**state.arguments, verify_only=True)
    assert result["recovery"] == "verified_existing" and result["contentVerified"] is True
    assert (content.read_bytes(), content.stat().st_mtime_ns) == before
    assert len([command for command in state.commands if "restore" in command]) == 1


def test_successful_restore_retry_is_idempotent_and_receipt_does_not_replace_content_checks(
    restored_library,
):
    state = restored_library
    first = backup.restore(**state.arguments)
    second = backup.restore(**state.arguments)
    assert first["treeSha256"] == second["treeSha256"]
    assert second["recovery"] == "verified_existing"
    assert len([command for command in state.commands if "restore" in command]) == 1
    (state.target / "photo.bin").write_bytes(b"Y" * len(DATA))
    with pytest.raises(backup.NasDataBackupError) as caught:
        backup.restore(**state.arguments)
    assert caught.value.code == "content_mismatch"


def test_prepared_receipt_mismatch_cannot_claim_the_promotion_never_happened(
    restored_library, monkeypatch
):
    state = restored_library
    original = support.RestoreReceipts.save
    saves = 0

    def save(receipts, value):
        nonlocal saves
        saves += 1
        if saves == 2:
            raise OSError(errno.ENOSPC, "controlled failure after actual exchange")
        original(receipts, value)

    monkeypatch.setattr(support.RestoreReceipts, "save", save)
    with pytest.raises(backup.NasDataBackupError) as initial:
        backup.restore(**state.arguments)
    assert initial.value.committed is True
    (state.target / "photo.bin").write_bytes(b"Z" * len(DATA))
    with pytest.raises(backup.NasDataBackupError) as recovery:
        backup.restore(**state.arguments, verify_only=True)
    assert recovery.value.code == "content_mismatch"
    assert recovery.value.committed is None
    assert recovery.value.phase == "promotion_unconfirmed"
    assert len([command for command in state.commands if "restore" in command]) == 1


def test_unknown_nonempty_target_is_never_overwritten(restored_library):
    state = restored_library
    (state.target / "existing.bin").write_bytes(b"keep me")
    with pytest.raises(backup.NasDataBackupError) as caught:
        backup.restore(**state.arguments)
    assert caught.value.code == "target_not_empty"
    assert (state.target / "existing.bin").read_bytes() == b"keep me"
    assert not any("restore" in command for command in state.commands)


def test_failed_transfer_retries_into_new_staging_and_leaves_live_data_untouched(restored_library):
    state = restored_library
    state.fail_restore = True
    with pytest.raises(backup.NasDataBackupError) as caught:
        backup.restore(**state.arguments)
    assert caught.value.code == "insufficient_space"
    assert caught.value.phase == "restore_transfer" and caught.value.committed is False
    assert not list(state.target.iterdir())
    first_staging = list(state.target.parent.glob(".storage.echo-nas-restore-*"))
    assert len(first_staging) == 1
    state.fail_restore = False
    result = backup.restore(**state.arguments)
    assert result["contentVerified"] is True
    assert (state.target / "photo.bin").read_bytes() == DATA
    # There is no transfer-resume contract: failed staging stays private for
    # operator inspection; a retry completes using a distinct staging tree.
    assert first_staging[0].is_dir()
    commands = [command for command in state.commands if "restore" in command]
    assert (
        commands[0][commands[0].index("--target") + 1]
        != commands[1][commands[1].index("--target") + 1]
    )


def test_receipt_failure_before_promotion_keeps_live_target_empty(restored_library, monkeypatch):
    state = restored_library

    def save(_receipts, _value):
        raise PermissionError(errno.EACCES, "sensitive private receipt storage")

    monkeypatch.setattr(support.RestoreReceipts, "save", save)
    with pytest.raises(backup.NasDataBackupError) as caught:
        backup.restore(**state.arguments)
    assert caught.value.code == "permission_denied"
    assert caught.value.phase == "prepared" and caught.value.committed is False
    assert not list(state.target.iterdir())


@pytest.mark.parametrize("field,value", [("snapshotId", "c" * 64), ("repositoryId", "d" * 64)])
def test_recovery_does_not_accept_a_receipt_for_another_snapshot_or_repository(
    restored_library, field, value
):
    state = restored_library
    backup.restore(**state.arguments)
    receipts = support.RestoreReceipts(state.receipts, state.target)
    receipt = receipts.load()
    receipts.save({**receipt, field: value})
    before = (state.target / "photo.bin").stat().st_mtime_ns
    with pytest.raises(backup.NasDataBackupError) as caught:
        backup.restore(**state.arguments, verify_only=True)
    assert caught.value.code == "receipt_mismatch"
    assert (state.target / "photo.bin").stat().st_mtime_ns == before
    assert len([command for command in state.commands if "restore" in command]) == 1


def test_corrupt_receipt_cannot_bypass_tree_verification_or_trigger_overwrite(restored_library):
    state = restored_library
    backup.restore(**state.arguments)
    receipts = support.RestoreReceipts(state.receipts, state.target)
    receipt = receipts.load()
    receipts.save({**receipt, "phase": ["verified"]})
    with pytest.raises(backup.NasDataBackupError) as caught:
        backup.restore(**state.arguments, verify_only=True)
    assert caught.value.code == "receipt_unavailable"
    assert (state.target / "photo.bin").read_bytes() == DATA
    assert len([command for command in state.commands if "restore" in command]) == 1


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership and modes require a POSIX host")
def test_receipts_require_private_root_owned_storage(tmp_path):
    directory = tmp_path / "receipts"
    directory.mkdir(mode=0o700)
    receipts = support.RestoreReceipts(directory, tmp_path / "target")
    if os.geteuid() != 0:
        # Ordinary users cannot create receipts trusted by the root-only CLI.
        with pytest.raises(backup.NasDataBackupError) as caught:
            receipts.load()
        assert caught.value.code == "receipt_unavailable"
        return
    assert receipts.load() is None
    directory.chmod(0o755)
    with pytest.raises(backup.NasDataBackupError) as caught:
        receipts.load()
    assert caught.value.code == "receipt_unavailable"
    directory.chmod(0o700)
    outside = tmp_path / "outside"
    outside.write_text("private file must not be read", encoding="utf-8")
    receipts.path.symlink_to(outside)
    with pytest.raises(backup.NasDataBackupError) as caught:
        receipts.load()
    assert caught.value.code == "receipt_unavailable"
    assert outside.read_text() == "private file must not be read"


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership and modes require a POSIX host")
def test_backup_set_receipts_are_private_durable_and_set_bound(tmp_path):
    directory = tmp_path / "set-receipts"
    directory.mkdir(mode=0o700)
    set_id = "99999999-aaaa-4bbb-8ccc-dddddddddddd"
    receipts = support.BackupSetRestoreReceipts(directory, set_id)
    if os.geteuid() != 0:
        with pytest.raises(backup.NasDataBackupError):
            receipts.load()
        return

    receipts.save({"phase": "preparing", "members": []})
    assert receipts.load()["setId"] == set_id
    assert receipts.load()["phase"] == "preparing"
    assert stat.S_IMODE(receipts.path.stat().st_mode) == 0o600

    directory.chmod(0o755)
    with pytest.raises(backup.NasDataBackupError) as caught:
        receipts.load()
    assert caught.value.code == "receipt_unavailable"


def test_tree_identity_tracks_paths_bytes_and_lexical_links(tmp_path):
    (tmp_path / "photo.bin").write_bytes(DATA)
    first = support.tree_identity(tmp_path)
    (tmp_path / "photo.bin").rename(tmp_path / "renamed.bin")
    assert support.tree_identity(tmp_path)["treeSha256"] != first["treeSha256"]
    link = tmp_path / "link"
    try:
        link.symlink_to("renamed.bin")
    except OSError:
        pytest.skip("host cannot create test symlinks")
    with_link = support.tree_identity(tmp_path)
    link.unlink()
    link.symlink_to("other.bin")
    assert support.tree_identity(tmp_path)["treeSha256"] != with_link["treeSha256"]
    link.unlink()
    link.symlink_to("../outside")
    with pytest.raises(backup.NasDataBackupError) as caught:
        support.tree_identity(tmp_path)
    assert caught.value.code == "unsafe_tree"
    assert (
        hashlib.sha256((tmp_path / "renamed.bin").read_bytes()).hexdigest()
        == hashlib.sha256(DATA).hexdigest()
    )


def test_cli_failure_is_structured_and_never_prints_raw_exception(tmp_path, monkeypatch, capsys):
    runtime = tmp_path / "restic"
    runtime.write_bytes(b"test runtime marker, never executed")
    monkeypatch.setattr(backup, "RESTIC", runtime)
    monkeypatch.setattr(backup.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(backup.os, "uname", lambda: SimpleNamespace(sysname="Linux"), raising=False)
    monkeypatch.setattr(backup, "_password_from_credential", lambda: b"not-logged-password")

    def fail(**_kwargs):
        raise backup.NasDataBackupError(
            "private password and /secret/path", code="permission_denied", phase="repository_check"
        )

    monkeypatch.setattr(backup, "check_repository", fail)
    assert (
        backup.main(
            [
                "check",
                "--repository",
                str(tmp_path),
                "--repository-mount",
                str(tmp_path),
                "--deployment-root",
                str(tmp_path),
            ]
        )
        == 1
    )
    output = capsys.readouterr()
    diagnostic = json.loads(output.err)
    assert diagnostic["code"] == "permission_denied"
    assert not output.out and "password" not in output.err and "/secret" not in output.err
