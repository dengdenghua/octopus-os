"""Crash-consistent immutable appliance upgrade transaction tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deploy.appliance import upgrade_transaction as transaction

PREVIOUS = f"registry.example/echo-os@sha256:{'a' * 64}"
TARGET = f"registry.example/echo-os@sha256:{'b' * 64}"


def _release(path: Path, image: str = PREVIOUS) -> None:
    path.write_text(f"ECHO_OS_IMAGE={image}\n")
    path.chmod(0o600)


def test_successful_switch_commits_only_the_selected_immutable_target(tmp_path: Path) -> None:
    release = tmp_path / "echo-release.env"
    journal = tmp_path / ".echo-upgrade-transaction.json"
    _release(release)

    begun = transaction.begin(
        journal,
        release,
        PREVIOUS,
        TARGET,
        previous_release_present=True,
    )
    selected = transaction.select(journal, release)
    committed = transaction.commit(journal, release)

    assert begun["phase"] == "prepared"
    assert selected["phase"] == "selected"
    assert committed == {"committed": True, "transactionId": begun["transactionId"]}
    assert release.read_text() == f"ECHO_OS_IMAGE={TARGET}\n"
    assert release.stat().st_mode & 0o777 == 0o600
    assert not journal.exists()


@pytest.mark.parametrize("phase", ["prepared", "switching", "selected", "recovering"])
def test_every_uncommitted_phase_recovers_the_previous_selection(
    tmp_path: Path, phase: str
) -> None:
    release = tmp_path / "echo-release.env"
    journal = tmp_path / ".echo-upgrade-transaction.json"
    _release(release)
    value = transaction.begin(
        journal,
        release,
        PREVIOUS,
        TARGET,
        previous_release_present=True,
    )
    if phase in {"switching", "selected", "recovering"}:
        value = transaction._set_phase(journal, value, phase)
    if phase in {"selected", "recovering"}:
        transaction._atomic_write(
            release,
            transaction._release_payload(TARGET),
            mode=0o600,
        )

    result = transaction.recover(journal, release)
    finished = transaction.finish_recovery(journal, release)

    assert result["previousImage"] == PREVIOUS
    assert result["targetImage"] == TARGET
    assert finished == {"recovered": True, "transactionId": value["transactionId"]}
    assert release.read_text() == f"ECHO_OS_IMAGE={PREVIOUS}\n"
    assert not journal.exists()


def test_first_install_without_a_release_file_still_records_a_recoverable_previous_image(
    tmp_path: Path,
) -> None:
    release = tmp_path / "echo-release.env"
    journal = tmp_path / ".echo-upgrade-transaction.json"

    transaction.begin(
        journal,
        release,
        PREVIOUS,
        TARGET,
        previous_release_present=False,
    )
    transaction.select(journal, release)
    transaction.recover(journal, release)

    assert release.read_text() == f"ECHO_OS_IMAGE={PREVIOUS}\n"


def test_tampered_or_duplicate_transaction_fields_fail_closed(tmp_path: Path) -> None:
    release = tmp_path / "echo-release.env"
    journal = tmp_path / ".echo-upgrade-transaction.json"
    _release(release)
    value = transaction.begin(
        journal,
        release,
        PREVIOUS,
        TARGET,
        previous_release_present=True,
    )

    value["targetImage"] = f"registry.example/echo-os@sha256:{'c' * 64}"
    journal.write_text(json.dumps(value) + "\n")
    journal.chmod(0o600)
    with pytest.raises(transaction.UpgradeTransactionError, match="schema"):
        transaction.recover(journal, release)

    journal.write_text('{"schemaVersion":1,"schemaVersion":1}\n')
    journal.chmod(0o600)
    with pytest.raises(transaction.UpgradeTransactionError, match="duplicate"):
        transaction.recover(journal, release)


def test_transaction_and_release_paths_reject_links_and_public_modes(tmp_path: Path) -> None:
    release = tmp_path / "echo-release.env"
    journal = tmp_path / ".echo-upgrade-transaction.json"
    _release(release)
    transaction.begin(
        journal,
        release,
        PREVIOUS,
        TARGET,
        previous_release_present=True,
    )
    journal.chmod(0o644)
    with pytest.raises(transaction.UpgradeTransactionError, match="ownership, mode, or size"):
        transaction.select(journal, release)

    journal.unlink()
    target = tmp_path / "real-release.env"
    _release(target)
    release.unlink()
    release.symlink_to(target)
    with pytest.raises(transaction.UpgradeTransactionError):
        transaction.begin(
            journal,
            release,
            PREVIOUS,
            TARGET,
            previous_release_present=True,
        )
