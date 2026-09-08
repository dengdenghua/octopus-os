from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from appliance.vm_evidence_audit import VmEvidenceAuditError, audit_vm_evidence


def _write(path: Path, payload: dict[str, object]) -> str:
    data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _index(path: Path, entries: list[dict[str, object]]) -> Path:
    path.write_text(
        json.dumps(
            {"schemaVersion": 1, "kind": "echo.local-vm-evidence-index", "entries": entries}
        ),
        encoding="utf-8",
    )
    return path


def _entry(
    name: str,
    digest: str,
    *,
    bindings: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "sha256": digest,
        "discriminator": {"field": "kind", "value": f"echo-{name}"},
        "bindings": bindings or [],
    }


def test_audit_accepts_bound_private_vm_evidence(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    root.mkdir()
    provision = {
        "kind": "echo-provision.json",
        "pool": {"poolGuid": "123"},
        "payload": {"sha256": "a" * 64},
    }
    provision_hash = _write(root / "provision.json", provision)
    reboot = {
        "kind": "echo-reboot.json",
        "provisionEvidenceSha256": provision_hash,
        "pool": {"poolGuid": "123"},
        "payload": {"sha256": "a" * 64},
    }
    reboot_hash = _write(root / "reboot.json", reboot)
    bindings = [
        {"field": "provisionEvidenceSha256", "target": "provision.json", "targetField": "$sha256"},
        {"field": "pool.poolGuid", "target": "provision.json", "targetField": "pool.poolGuid"},
    ]
    index = _index(
        tmp_path / "index.json",
        [
            _entry("provision.json", provision_hash),
            _entry("reboot.json", reboot_hash, bindings=bindings),
        ],
    )

    report = audit_vm_evidence(index, root)

    assert report["ready"] is True
    assert report["blockers"] == []
    assert all(all(item["checks"].values()) for item in report["entries"])


def test_audit_rejects_tampering_host_paths_and_cross_run_bindings(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    root.mkdir()
    provision_hash = _write(
        root / "provision.json",
        {"kind": "echo-provision.json", "pool": {"poolGuid": "one"}},
    )
    reboot_hash = _write(
        root / "reboot.json",
        {
            "kind": "echo-reboot.json",
            "pool": {"poolGuid": "two"},
            "localState": r"C:\private\state.json",
        },
    )
    index = _index(
        tmp_path / "index.json",
        [
            _entry("provision.json", provision_hash),
            _entry(
                "reboot.json",
                reboot_hash,
                bindings=[
                    {
                        "field": "pool.poolGuid",
                        "target": "provision.json",
                        "targetField": "pool.poolGuid",
                    }
                ],
            ),
        ],
    )
    (root / "provision.json").write_text("{}", encoding="utf-8")

    report = audit_vm_evidence(index, root)

    assert report["ready"] is False
    assert "provision.json:sha256" in report["blockers"]
    assert "reboot.json:privacy" in report["blockers"]
    assert "reboot.json:binding:pool.poolGuid" in report["blockers"]


def test_audit_rejects_duplicate_manifest_keys(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    root.mkdir()
    index = tmp_path / "index.json"
    index.write_text(
        '{"schemaVersion":1,"schemaVersion":1,"kind":"echo.local-vm-evidence-index","entries":[]}',
        encoding="utf-8",
    )

    with pytest.raises(VmEvidenceAuditError, match="duplicate JSON key"):
        audit_vm_evidence(index, root)
