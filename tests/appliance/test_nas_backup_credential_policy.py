from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

from appliance import nas_backup_credential_policy as policy

OWNER = os.getuid() if hasattr(os, "getuid") else 0
GROUP = os.getgid() if hasattr(os, "getgid") else 0


def _desired(password: str = "correct-horse-battery") -> dict[str, Any]:
    return {
        "schema": policy.DESIRED_SCHEMA,
        "mode": "initialize",
        "repository": "/mnt/off-device/echo-nas-data",
        "repositoryMount": "/mnt/off-device",
        "password": password,
    }


def _runtime(tmp_path: Path) -> tuple[Path, Path, Path]:
    credential = tmp_path / "credstore" / policy.CREDENTIAL_NAME
    credential.parent.mkdir(mode=0o700)
    systemd_creds = tmp_path / "systemd-creds"
    restic = tmp_path / "restic"
    for path in (systemd_creds, restic):
        path.write_bytes(b"runtime")
        path.chmod(0o755)
    return credential, systemd_creds, restic


def _binding(_desired: dict[str, Any]) -> dict[str, Any]:
    return {
        "filesystem": "ext4",
        "sourceSha256": "a" * 64,
        "device": 10,
        "inode": 20,
        "empty": True,
    }


def _rotation_desired(
    *,
    current_password: str = "correct-horse-battery",
    new_password: str = "new-correct-horse-battery",
) -> dict[str, Any]:
    return {
        "schema": policy.ROTATION_DESIRED_SCHEMA,
        "repository": "/mnt/off-device/echo-nas-data",
        "repositoryMount": "/mnt/off-device",
        "currentPassword": current_password,
        "newPassword": new_password,
    }


def _rotation_runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
    payload = kwargs["input"]
    if "encrypt" in command:
        assert payload == b"new-correct-horse-battery"
        output = b"encrypted-new-credential"
    elif payload == b"encrypted-old-credential":
        output = b"correct-horse-battery"
    else:
        assert payload == b"encrypted-new-credential"
        output = b"new-correct-horse-battery"
    return subprocess.CompletedProcess(command, 0, stdout=output, stderr=b"")


def test_plan_binds_secret_without_exposing_secret_or_paths(tmp_path: Path, monkeypatch) -> None:
    credential, systemd_creds, restic = _runtime(tmp_path)
    monkeypatch.setattr(policy.nas_data_backup, "RESTIC", restic)

    first = policy.plan_credential(
        _desired(),
        credential_path=credential,
        systemd_creds=systemd_creds,
        trusted_uid=OWNER,
        repository_binding_reader=_binding,
    )
    repeated = policy.plan_credential(
        _desired(),
        credential_path=credential,
        systemd_creds=systemd_creds,
        trusted_uid=OWNER,
        repository_binding_reader=_binding,
    )
    changed = policy.plan_credential(
        _desired("different-safe-password"),
        credential_path=credential,
        systemd_creds=systemd_creds,
        trusted_uid=OWNER,
        repository_binding_reader=_binding,
    )
    different_worker_key = policy.plan_credential(
        _desired(),
        credential_path=credential,
        systemd_creds=systemd_creds,
        trusted_uid=OWNER,
        binding_key=b"different-worker-binding-key-0001",
        repository_binding_reader=_binding,
    )

    assert first == repeated
    assert first["planId"] != changed["planId"]
    assert first["planId"] != different_worker_key["planId"]
    assert first["pathsRedacted"] is True
    assert first["desired"] == {
        "mode": "initialize",
        "repositoryConfigured": True,
        "passwordBound": True,
    }
    serialized = str(first)
    assert "correct-horse-battery" not in serialized
    assert "/mnt/" not in serialized


def test_apply_initializes_repository_and_commits_verified_encrypted_credential(
    tmp_path: Path, monkeypatch
) -> None:
    credential, systemd_creds, restic = _runtime(tmp_path)
    monkeypatch.setattr(policy.nas_data_backup, "RESTIC", restic)
    desired = _desired()
    plan = policy.plan_credential(
        desired,
        credential_path=credential,
        systemd_creds=systemd_creds,
        trusted_uid=OWNER,
        repository_binding_reader=_binding,
    )
    observed: dict[str, Any] = {}

    def prepare(_desired_state: dict[str, Any], password: bytes) -> dict[str, Any]:
        observed["passwordSha256"] = hashlib.sha256(password).hexdigest()
        return {
            "repositoryId": "b" * 64,
            "encrypted": True,
            "fullReadVerified": True,
        }

    def runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if "encrypt" in command:
            assert kwargs["input"] == b"correct-horse-battery"
            output = b"encrypted-host-bound-credential\n"
        else:
            assert kwargs["input"] == b"encrypted-host-bound-credential\n"
            output = b"correct-horse-battery"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr=b"")

    result = policy.apply_credential(
        desired,
        plan["planId"],
        credential_path=credential,
        systemd_creds=systemd_creds,
        trusted_uid=OWNER,
        trusted_gid=GROUP,
        repository_binding_reader=_binding,
        repository_preparer=prepare,
        credential_runner=runner,
        credential_lock_path=tmp_path / "locks" / "credential.lock",
    )

    assert result["verified"] is True
    assert result["repositoryId"] == "b" * 64
    assert credential.read_bytes() == b"encrypted-host-bound-credential\n"
    assert hashlib.sha256(b"correct-horse-battery").hexdigest() == observed["passwordSha256"]
    if os.name == "posix":
        assert stat.S_IMODE(credential.stat().st_mode) == 0o600


def test_apply_rejects_password_changed_after_preview(tmp_path: Path, monkeypatch) -> None:
    credential, systemd_creds, restic = _runtime(tmp_path)
    monkeypatch.setattr(policy.nas_data_backup, "RESTIC", restic)
    plan = policy.plan_credential(
        _desired(),
        credential_path=credential,
        systemd_creds=systemd_creds,
        trusted_uid=OWNER,
        repository_binding_reader=_binding,
    )

    with pytest.raises(policy.NasBackupCredentialPolicyError, match="stale"):
        policy.apply_credential(
            _desired("different-safe-password"),
            plan["planId"],
            credential_path=credential,
            systemd_creds=systemd_creds,
            trusted_uid=OWNER,
            trusted_gid=GROUP,
            repository_binding_reader=_binding,
            credential_lock_path=tmp_path / "locks" / "credential.lock",
        )

    assert not credential.exists()


def test_existing_credential_cannot_be_blindly_rotated(tmp_path: Path, monkeypatch) -> None:
    credential, systemd_creds, restic = _runtime(tmp_path)
    monkeypatch.setattr(policy.nas_data_backup, "RESTIC", restic)
    credential.write_bytes(b"existing-encrypted-credential")
    credential.chmod(0o600)

    with pytest.raises(policy.NasBackupCredentialPolicyError, match="already configured"):
        policy.plan_credential(
            _desired(),
            credential_path=credential,
            systemd_creds=systemd_creds,
            trusted_uid=OWNER,
            repository_binding_reader=_binding,
        )


def test_failed_encryption_never_writes_plaintext_credential(tmp_path: Path, monkeypatch) -> None:
    credential, systemd_creds, restic = _runtime(tmp_path)
    monkeypatch.setattr(policy.nas_data_backup, "RESTIC", restic)
    desired = _desired()
    plan = policy.plan_credential(
        desired,
        credential_path=credential,
        systemd_creds=systemd_creds,
        trusted_uid=OWNER,
        repository_binding_reader=_binding,
    )

    def failed(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"failed")

    with pytest.raises(OSError, match="encryption tool failed"):
        policy.apply_credential(
            desired,
            plan["planId"],
            credential_path=credential,
            systemd_creds=systemd_creds,
            trusted_uid=OWNER,
            trusted_gid=GROUP,
            repository_binding_reader=_binding,
            repository_preparer=lambda _desired_state, _password: {"repositoryId": "b" * 64},
            credential_runner=failed,
            credential_lock_path=tmp_path / "locks" / "credential.lock",
        )

    assert not credential.exists()


def test_atomic_commit_never_overwrites_concurrent_credential(tmp_path: Path) -> None:
    credential = tmp_path / "credstore" / policy.CREDENTIAL_NAME
    credential.parent.mkdir(mode=0o700)
    credential.write_bytes(b"credential-from-other-worker")

    with pytest.raises(policy.NasBackupCredentialPolicyError, match="configured concurrently"):
        policy._atomic_write_new(
            credential,
            b"new-encrypted-credential",
            uid=OWNER,
            gid=GROUP,
        )

    assert credential.read_bytes() == b"credential-from-other-worker"


def test_rotation_adds_verifies_switches_and_revokes_without_exposing_secrets(
    tmp_path: Path, monkeypatch
) -> None:
    credential, systemd_creds, restic = _runtime(tmp_path)
    monkeypatch.setattr(policy.nas_data_backup, "RESTIC", restic)
    credential.write_bytes(b"encrypted-old-credential")
    credential.chmod(0o600)
    desired = _rotation_desired()
    plan = policy.plan_rotation(
        desired,
        credential_path=credential,
        systemd_creds=systemd_creds,
        trusted_uid=OWNER,
        repository_binding_reader=_binding,
    )
    calls: list[tuple[str, str]] = []

    def verify(_desired_state: dict[str, Any], password: bytes) -> dict[str, Any]:
        calls.append(("verify", password.decode()))
        return {"repositoryId": "b" * 64, "repositoryVerified": True}

    def add(_desired_state: dict[str, Any], current: bytes, new: bytes) -> dict[str, Any]:
        calls.append(("add", f"{current.decode()}->{new.decode()}"))
        return {
            "repository": tmp_path / "repository",
            "oldKeyId": "1" * 64,
            "newKeyId": "2" * 64,
        }

    def revoke(
        _desired_state: dict[str, Any],
        authentication: bytes,
        rejected: bytes,
        preserve: str,
    ) -> int:
        calls.append(
            (
                "revoke",
                f"{authentication.decode()}:{rejected.decode()}:{preserve}",
            )
        )
        return 1

    result = policy.apply_rotation(
        desired,
        plan["planId"],
        credential_path=credential,
        systemd_creds=systemd_creds,
        trusted_uid=OWNER,
        trusted_gid=GROUP,
        repository_binding_reader=_binding,
        credential_runner=_rotation_runner,
        operation_lock=nullcontext,
        receipt_path=credential.with_suffix(".rotation"),
        repository_path_resolver=lambda _desired_state: tmp_path / "repository",
        repository_key_reader=lambda _repository, _password: [{"id": "1" * 64, "current": True}],
        repository_key_adder=add,
        repository_password_verifier=verify,
        repository_password_revoker=revoke,
        credential_lock_path=tmp_path / "locks" / "credential.lock",
    )

    assert result["verified"] is True
    assert result["oldPasswordRevoked"] is True
    assert result["removedKeyCount"] == 1
    assert result["recoveryReceiptCleared"] is True
    assert credential.read_bytes() == b"encrypted-new-credential"
    assert not credential.with_suffix(".rotation").exists()
    assert calls[0] == ("verify", "correct-horse-battery")
    assert calls[-1][0] == "revoke"
    serialized = str(plan)
    assert "correct-horse-battery" not in serialized
    assert "/mnt/" not in serialized


def test_rotation_refuses_to_add_a_key_when_repository_key_limit_is_reached(
    monkeypatch,
) -> None:
    keys = [
        {"id": f"{index:064x}", "current": index == 0}
        for index in range(policy.MAX_REPOSITORY_KEYS)
    ]
    monkeypatch.setattr(policy, "_repository_path", lambda _desired: Path("/repo"))
    monkeypatch.setattr(policy, "_repository_keys", lambda *_args, **_kwargs: keys)

    with pytest.raises(OSError, match="too many keys"):
        policy._add_repository_key(
            _rotation_desired(),
            b"correct-horse-battery",
            b"new-correct-horse-battery",
        )


def test_rotation_rejects_current_password_that_does_not_match_credential(
    tmp_path: Path, monkeypatch
) -> None:
    credential, systemd_creds, restic = _runtime(tmp_path)
    monkeypatch.setattr(policy.nas_data_backup, "RESTIC", restic)
    credential.write_bytes(b"encrypted-old-credential")
    credential.chmod(0o600)
    desired = _rotation_desired(current_password="plausible-but-wrong-password")
    plan = policy.plan_rotation(
        desired,
        credential_path=credential,
        systemd_creds=systemd_creds,
        trusted_uid=OWNER,
        repository_binding_reader=_binding,
    )
    added = False

    def add(*_args: Any) -> dict[str, Any]:
        nonlocal added
        added = True
        return {}

    with pytest.raises(policy.NasBackupCredentialPolicyError, match="does not match"):
        policy.apply_rotation(
            desired,
            plan["planId"],
            credential_path=credential,
            systemd_creds=systemd_creds,
            trusted_uid=OWNER,
            trusted_gid=GROUP,
            repository_binding_reader=_binding,
            credential_runner=_rotation_runner,
            operation_lock=nullcontext,
            receipt_path=credential.with_suffix(".rotation"),
            repository_path_resolver=lambda _desired_state: tmp_path / "repository",
            repository_key_reader=lambda _repository, _password: [
                {"id": "1" * 64, "current": True}
            ],
            repository_key_adder=add,
            credential_lock_path=tmp_path / "locks" / "credential.lock",
        )

    assert added is False
    assert credential.read_bytes() == b"encrypted-old-credential"


def test_rotation_cleans_new_key_when_verification_fails_before_switch(
    tmp_path: Path, monkeypatch
) -> None:
    credential, systemd_creds, restic = _runtime(tmp_path)
    monkeypatch.setattr(policy.nas_data_backup, "RESTIC", restic)
    credential.write_bytes(b"encrypted-old-credential")
    credential.chmod(0o600)
    desired = _rotation_desired()
    plan = policy.plan_rotation(
        desired,
        credential_path=credential,
        systemd_creds=systemd_creds,
        trusted_uid=OWNER,
        repository_binding_reader=_binding,
    )
    removed: list[tuple[bytes, str]] = []
    verification_count = 0

    def verify(_desired_state: dict[str, Any], _password: bytes) -> dict[str, Any]:
        nonlocal verification_count
        verification_count += 1
        if verification_count == 2:
            raise OSError("new key check failed")
        return {"repositoryId": "b" * 64}

    transition = {
        "repository": tmp_path / "repository",
        "oldKeyId": "1" * 64,
        "newKeyId": "2" * 64,
    }
    with pytest.raises(OSError, match="new key check failed"):
        policy.apply_rotation(
            desired,
            plan["planId"],
            credential_path=credential,
            systemd_creds=systemd_creds,
            trusted_uid=OWNER,
            trusted_gid=GROUP,
            repository_binding_reader=_binding,
            credential_runner=_rotation_runner,
            operation_lock=nullcontext,
            receipt_path=credential.with_suffix(".rotation"),
            repository_path_resolver=lambda _desired_state: tmp_path / "repository",
            repository_key_reader=lambda _repository, _password: [
                {"id": "1" * 64, "current": True}
            ],
            repository_key_adder=lambda *_args: transition,
            repository_password_verifier=verify,
            repository_key_remover=lambda _repository, password, key_id: removed.append(
                (password, key_id)
            ),
            credential_lock_path=tmp_path / "locks" / "credential.lock",
        )

    assert removed == [(b"correct-horse-battery", "2" * 64)]
    assert credential.read_bytes() == b"encrypted-old-credential"


def test_rotation_failure_after_switch_keeps_verified_new_access(
    tmp_path: Path, monkeypatch
) -> None:
    credential, systemd_creds, restic = _runtime(tmp_path)
    monkeypatch.setattr(policy.nas_data_backup, "RESTIC", restic)
    credential.write_bytes(b"encrypted-old-credential")
    credential.chmod(0o600)
    desired = _rotation_desired()
    plan = policy.plan_rotation(
        desired,
        credential_path=credential,
        systemd_creds=systemd_creds,
        trusted_uid=OWNER,
        repository_binding_reader=_binding,
    )
    remover_called = False

    def remove(*_args: Any) -> None:
        nonlocal remover_called
        remover_called = True

    with pytest.raises(OSError, match="revocation interrupted"):
        policy.apply_rotation(
            desired,
            plan["planId"],
            credential_path=credential,
            systemd_creds=systemd_creds,
            trusted_uid=OWNER,
            trusted_gid=GROUP,
            repository_binding_reader=_binding,
            credential_runner=_rotation_runner,
            operation_lock=nullcontext,
            receipt_path=credential.with_suffix(".rotation"),
            repository_path_resolver=lambda _desired_state: tmp_path / "repository",
            repository_key_reader=lambda _repository, _password: [
                {"id": "1" * 64, "current": True}
            ],
            repository_key_adder=lambda *_args: {
                "repository": tmp_path / "repository",
                "oldKeyId": "1" * 64,
                "newKeyId": "2" * 64,
            },
            repository_password_verifier=lambda *_args: {"repositoryId": "b" * 64},
            repository_password_revoker=lambda *_args: (_ for _ in ()).throw(
                OSError("revocation interrupted")
            ),
            repository_key_remover=remove,
            credential_lock_path=tmp_path / "locks" / "credential.lock",
        )

    assert credential.read_bytes() == b"encrypted-new-credential"
    assert remover_called is False
    assert credential.with_suffix(".rotation").exists()


def test_pending_rotation_receipt_completes_forward_after_credential_switch(
    tmp_path: Path, monkeypatch
) -> None:
    credential, systemd_creds, restic = _runtime(tmp_path)
    monkeypatch.setattr(policy.nas_data_backup, "RESTIC", restic)
    credential.write_bytes(b"encrypted-old-credential")
    credential.chmod(0o600)
    receipt = credential.with_suffix(".rotation")
    repository = tmp_path / "repository"
    desired = _rotation_desired()
    plan = policy.plan_rotation(
        desired,
        credential_path=credential,
        systemd_creds=systemd_creds,
        trusted_uid=OWNER,
        repository_binding_reader=_binding,
    )
    key_state = {"old": True, "new": False}

    def keys(_repository: Path, password: bytes) -> list[dict[str, Any]] | None:
        if password == b"correct-horse-battery":
            if not key_state["old"]:
                return None
            return [
                {"id": "1" * 64, "current": True},
                *([{"id": "2" * 64, "current": False}] if key_state["new"] else []),
            ]
        if password == b"new-correct-horse-battery" and key_state["new"]:
            return [
                *([{"id": "1" * 64, "current": False}] if key_state["old"] else []),
                {"id": "2" * 64, "current": True},
            ]
        return None

    def add(*_args: Any) -> dict[str, Any]:
        key_state["new"] = True
        return {
            "repository": repository,
            "oldKeyId": "1" * 64,
            "newKeyId": "2" * 64,
        }

    with pytest.raises(OSError, match="revocation interrupted"):
        policy.apply_rotation(
            desired,
            plan["planId"],
            credential_path=credential,
            systemd_creds=systemd_creds,
            trusted_uid=OWNER,
            trusted_gid=GROUP,
            repository_binding_reader=_binding,
            credential_runner=_rotation_runner,
            operation_lock=nullcontext,
            receipt_path=receipt,
            repository_path_resolver=lambda _desired_state: repository,
            repository_key_reader=keys,
            repository_key_adder=add,
            repository_password_verifier=lambda *_args: {"repositoryId": "b" * 64},
            repository_password_revoker=lambda *_args: (_ for _ in ()).throw(
                OSError("revocation interrupted")
            ),
            credential_lock_path=tmp_path / "locks" / "credential.lock",
        )

    assert receipt.exists()
    serialized = receipt.read_text(encoding="utf-8")
    assert "correct-horse-battery" not in serialized
    assert "new-correct-horse-battery" not in serialized

    def revoke(*_args: Any) -> int:
        key_state["old"] = False
        return 1

    recovered = policy.recover_rotation(
        receipt_path=receipt,
        credential_path=credential,
        systemd_creds=systemd_creds,
        trusted_uid=OWNER,
        credential_lock_path=tmp_path / "locks" / "credential.lock",
        credential_runner=_rotation_runner,
        operation_lock=nullcontext,
        repository_path_resolver=lambda _desired_state: repository,
        repository_key_reader=keys,
        repository_password_verifier=lambda *_args: {"repositoryId": "b" * 64},
        repository_password_revoker=revoke,
    )

    assert recovered["direction"] == "forward"
    assert recovered["removedKeyCount"] == 1
    assert key_state == {"old": False, "new": True}
    assert credential.read_bytes() == b"encrypted-new-credential"
    assert not receipt.exists()


def test_pending_rotation_receipt_rolls_back_key_added_before_credential_switch(
    tmp_path: Path, monkeypatch
) -> None:
    credential, systemd_creds, restic = _runtime(tmp_path)
    monkeypatch.setattr(policy.nas_data_backup, "RESTIC", restic)
    credential.write_bytes(b"encrypted-old-credential")
    credential.chmod(0o600)
    receipt = credential.with_suffix(".rotation")
    repository = tmp_path / "repository"
    desired = _rotation_desired()
    plan = policy.plan_rotation(
        desired,
        credential_path=credential,
        systemd_creds=systemd_creds,
        trusted_uid=OWNER,
        repository_binding_reader=_binding,
    )
    key_ids = {"1" * 64}

    def keys(_repository: Path, password: bytes) -> list[dict[str, Any]] | None:
        if password != b"correct-horse-battery":
            return None
        return [{"id": key_id, "current": key_id == "1" * 64} for key_id in sorted(key_ids)]

    def interrupted_add(*_args: Any) -> dict[str, Any]:
        key_ids.add("2" * 64)
        raise OSError("simulated process interruption")

    with pytest.raises(OSError, match="simulated process interruption"):
        policy.apply_rotation(
            desired,
            plan["planId"],
            credential_path=credential,
            systemd_creds=systemd_creds,
            trusted_uid=OWNER,
            trusted_gid=GROUP,
            repository_binding_reader=_binding,
            credential_runner=_rotation_runner,
            operation_lock=nullcontext,
            receipt_path=receipt,
            repository_path_resolver=lambda _desired_state: repository,
            repository_key_reader=keys,
            repository_key_adder=interrupted_add,
            repository_password_verifier=lambda *_args: {"repositoryId": "b" * 64},
            credential_lock_path=tmp_path / "locks" / "credential.lock",
        )

    assert receipt.exists()
    assert key_ids == {"1" * 64, "2" * 64}

    recovered = policy.recover_rotation(
        receipt_path=receipt,
        credential_path=credential,
        systemd_creds=systemd_creds,
        trusted_uid=OWNER,
        credential_lock_path=tmp_path / "locks" / "credential.lock",
        credential_runner=_rotation_runner,
        operation_lock=nullcontext,
        repository_path_resolver=lambda _desired_state: repository,
        repository_key_reader=keys,
        repository_password_verifier=lambda *_args: {"repositoryId": "b" * 64},
        repository_key_remover=lambda _repository, _password, key_id: key_ids.remove(key_id),
    )

    assert recovered["direction"] == "rollback"
    assert recovered["removedKeyCount"] == 1
    assert key_ids == {"1" * 64}
    assert credential.read_bytes() == b"encrypted-old-credential"
    assert not receipt.exists()
