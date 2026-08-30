"""Guard the isolated Android acceptance lab against protocol drift."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from scripts.android_device_sync_lab import (
    _safe_empty_state_dir,
    _write_pairing_file,
    build_lab,
)


def test_lab_uses_the_real_managed_device_auth_and_grants(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    device_id = "android-acceptance-1"
    app, _link, invitation = build_lab(
        state_dir=state_dir,
        device_id=device_id,
        public_host="10.0.2.2",
        port=8011,
        ws_port=8765,
    )
    token = parse_qs(urlparse(invitation["connectString"]).query)["token"][0]

    with TestClient(app) as client:
        status = client.get(
            "/api/appliance/device-sync",
            headers={
                "Authorization": f"EchoDevice {token}",
                "X-Echo-Device-ID": device_id,
                "X-Echo-Sync-Version": "1",
            },
        )

    assert status.status_code == 200
    assert status.json()["deviceId"] == device_id
    assert status.json()["grantedScopes"] == ["files", "photos"]


def test_lab_credential_file_is_private_and_never_overwritten(tmp_path: Path) -> None:
    pairing_file = tmp_path / "pairing.json"
    payload = {"schema": "test", "connectString": "echo://join?token=secret"}

    _write_pairing_file(pairing_file, payload)

    assert json.loads(pairing_file.read_text(encoding="utf-8")) == payload
    assert stat.S_IMODE(pairing_file.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        _write_pairing_file(pairing_file, payload)


def test_lab_requires_an_empty_non_symlink_state_directory(tmp_path: Path) -> None:
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep").write_text("user data", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        _safe_empty_state_dir(str(occupied))

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        _safe_empty_state_dir(str(link))
