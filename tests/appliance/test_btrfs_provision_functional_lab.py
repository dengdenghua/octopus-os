from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from deploy.appliance import btrfs_provision_functional_lab as lab


def _candidates(**overrides: Any) -> dict[str, Any]:
    devices = [
        {
            "devicefile": device,
            "sizeBytes": 2 * 1024**3,
            "serial": serial,
            "wwn": None,
            "model": None,
        }
        for device, serial in zip(lab.DEVICES, lab.SERIALS, strict=True)
    ]
    devices[0].update(overrides)
    return {"devices": devices}


def test_bound_candidates_requires_two_fixed_blank_vm_disk_identities() -> None:
    selected = lab._bound_candidates(_candidates())

    assert [item["devicefile"] for item in selected] == list(lab.DEVICES)
    assert [item["serial"] for item in selected] == list(lab.SERIALS)

    with pytest.raises(lab.BtrfsProvisionFunctionalLabError, match="uniquely offered"):
        lab._bound_candidates(_candidates(serial="unexpected"))
    with pytest.raises(lab.BtrfsProvisionFunctionalLabError, match="uniquely offered"):
        lab._bound_candidates({"devices": _candidates()["devices"][:1]})


def test_profiles_requires_exact_raid1_for_data_and_metadata() -> None:
    output = "Data, RAID1: total=1, used=1\nMetadata,RAID1: total=1, used=1\n"
    assert lab._profiles(output) == {"Data": {"raid1"}, "Metadata": {"raid1"}}
    assert lab._profiles(output.replace("Data, RAID1", "Data, single")) != {
        "Data": {"raid1"},
        "Metadata": {"raid1"},
    }


def test_restore_replays_saved_files_in_reverse_order(monkeypatch: pytest.MonkeyPatch) -> None:
    restored: list[Path] = []
    first = lab.base.SavedFile(Path("/one"), False, None, None)
    second = lab.base.SavedFile(Path("/two"), False, None, None)
    monkeypatch.setattr(lab.base, "_atomic_restore", lambda saved: restored.append(saved.path))

    lab._restore([first, second])

    assert restored == [Path("/two"), Path("/one")]
