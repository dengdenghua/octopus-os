from __future__ import annotations

import base64
from pathlib import Path

import pytest

from deploy.appliance import btrfs_reboot_functional_lab as lab


def _saved() -> list[lab.base.SavedFile]:
    return [
        lab.base.SavedFile(lab.base.AUTH_PATH, True, b"auth", 0o600),
        lab.base.SavedFile(lab.provision.FSTAB_PATH, False, None, None),
    ]


def test_saved_file_recovery_round_trip_is_exact_and_ordered() -> None:
    records = [lab._saved_record(saved) for saved in _saved()]

    assert lab._saved_files(records) == _saved()
    assert records[0]["payload"] == base64.b64encode(b"auth").decode("ascii")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda records: records.reverse(),
        lambda records: records[0].update(path="/tmp/other"),
        lambda records: records[0].update(mode=0o666),
        lambda records: records[0].update(payload="not-base64!"),
        lambda records: records.pop(),
    ],
)
def test_saved_file_recovery_rejects_tampered_state(mutation) -> None:  # type: ignore[no-untyped-def]
    records = [lab._saved_record(saved) for saved in _saved()]
    mutation(records)

    with pytest.raises(lab.BtrfsRebootFunctionalLabError):
        lab._saved_files(records)


def test_boot_id_requires_a_canonical_uuid(tmp_path: Path) -> None:
    path = tmp_path / "boot-id"
    path.write_text("11111111-2222-3333-4444-555555555555\n", encoding="ascii")
    assert lab._boot_id(path) == "11111111-2222-3333-4444-555555555555"

    path.write_text("NOT-A-UUID\n", encoding="ascii")
    with pytest.raises(lab.BtrfsRebootFunctionalLabError, match="boot ID"):
        lab._boot_id(path)


def test_state_material_separates_prepared_and_provisioned_identity() -> None:
    prepared = lab._state_material(
        saved_files=_saved(),
        baseline_boot_id="11111111-2222-3333-4444-555555555555",
        status="prepared",
    )
    provisioned = lab._state_material(
        saved_files=_saved(),
        baseline_boot_id="11111111-2222-3333-4444-555555555555",
        status="provisioned",
        filesystem_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        payload_sha256="a" * 64,
        plan_id="b" * 64,
    )

    assert prepared["filesystemUuid"] is None
    assert provisioned["filesystemUuid"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
