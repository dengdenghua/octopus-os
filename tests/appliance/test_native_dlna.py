from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from appliance import native_dlna
from appliance.native_storage_routes import create_omv_alias_router
from appliance.omv_models import DlnaDesiredState
from appliance.omv_protocol import DLNA_DESIRED_SCHEMA, validate_dlna_desired

FOLDER_REF = "11111111-2222-4333-8444-555555555555"


def _desired(*, enabled: bool = True, media_type: str = "all") -> dict[str, Any]:
    return {
        "schema": DLNA_DESIRED_SCHEMA,
        "sharedFolderRef": FOLDER_REF,
        "enabled": enabled,
        "mediaType": media_type,
    }


def _fake_storage() -> SimpleNamespace:
    folder = {
        "uuid": FOLDER_REF,
        "name": "Media",
        "relativePath": "Media",
        "mountPointRef": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        "volumePath": "/srv/pool",
    }

    def digest(value: Any) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def save(path: Path, content: str, *, mode: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(mode)

    def restore(path: Path, content: str | None, *, mode: int) -> None:
        if content is None:
            path.unlink(missing_ok=True)
        else:
            save(path, content, mode=mode)

    return SimpleNamespace(
        _resolve_shared_folder=lambda ref: folder if ref == FOLDER_REF else None,
        _native_registered_path=lambda _folder: PurePosixPath("/srv/pool/Media"),
        _native_folder_path=lambda _folder: PurePosixPath("/srv/pool/Media"),
        _registered_relative_name=lambda _folder, strict=True: "Media",
        _native_registered_folder_status=lambda _folder: "MOUNTED",
        _canonical_hash=digest,
        _registry_transaction=contextlib.nullcontext,
        _atomic_text_save=save,
        _restore_managed_text=restore,
    )


@pytest.fixture
def managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = tmp_path / "etc" / "echo-os" / "minidlna.conf"
    drop_in = tmp_path / "etc" / "systemd" / "echo-dlna.service.d" / "shares.conf"
    unit = tmp_path / "echo-dlna.service"
    unit.write_text("[Service]\nUser=minidlna\n", encoding="utf-8")
    monkeypatch.setattr(native_dlna, "MANAGED_CONFIG", config)
    monkeypatch.setattr(native_dlna, "MANAGED_DROP_IN", drop_in)
    monkeypatch.setattr(native_dlna, "SERVICE_UNIT", unit)
    monkeypatch.setattr(native_dlna, "_storage", _fake_storage)
    return config, drop_in


def test_dlna_desired_contract_is_exact_and_normalized() -> None:
    assert validate_dlna_desired(_desired(media_type="video"))["mediaType"] == "video"
    with pytest.raises(ValueError, match="mediaType"):
        validate_dlna_desired(_desired(media_type="documents"))
    with pytest.raises(ValidationError):
        DlnaDesiredState.model_validate({**_desired(), "path": "/srv/private"})


def test_generated_config_and_mount_namespace_are_canonical(managed, monkeypatch) -> None:
    config_path, drop_in_path = managed
    entry = native_dlna._entry_for_desired(_desired(media_type="pictures"))
    config = native_dlna.render_config([entry])
    drop_in = native_dlna.render_drop_in([entry])

    assert "media_dir=P,/run/echo-dlna/media/11111111-2222-4333-8444-555555555555" in config
    assert "wide_links=no" in config
    assert "BindReadOnlyPaths=/srv/pool/Media:/run/echo-dlna/media/" in drop_in
    assert "BindReadOnlyPaths=\n" in drop_in
    config_path.parent.mkdir(parents=True)
    drop_in_path.parent.mkdir(parents=True)
    config_path.write_text(config, encoding="utf-8")
    drop_in_path.write_text(drop_in, encoding="utf-8")
    monkeypatch.setattr(native_dlna, "_service_state", lambda: {"enabled": True, "active": True})
    monkeypatch.setattr(native_dlna, "capability_available", lambda: True)

    share = native_dlna.status()["shares"][0]
    assert share == {
        "sharedFolderRef": FOLDER_REF,
        "name": "Media",
        "mediaType": "pictures",
        "status": "MOUNTED",
    }


def test_managed_files_are_tamper_evident(managed) -> None:
    config_path, drop_in_path = managed
    entry = native_dlna._entry_for_desired(_desired())
    config_path.parent.mkdir(parents=True)
    drop_in_path.parent.mkdir(parents=True)
    config_path.write_text(native_dlna.render_config([entry]), encoding="utf-8")
    drop_in_path.write_text(native_dlna.render_drop_in([entry]), encoding="utf-8")

    assert native_dlna._load_entries(strict=True) == [entry]
    drop_in_path.write_text(
        drop_in_path.read_text(encoding="utf-8").replace("BindReadOnlyPaths=\n", ""),
        encoding="utf-8",
    )
    with pytest.raises(OSError, match="modified outside"):
        native_dlna._load_entries(strict=True)


def test_unsafe_systemd_source_path_is_rejected() -> None:
    with pytest.raises(ValueError, match="systemd bind mount"):
        native_dlna._safe_unit_path(PurePosixPath("/srv/My Media"))
    with pytest.raises(ValueError, match="systemd bind mount"):
        native_dlna._safe_unit_path(PurePosixPath("/srv/media%u"))


def test_plan_apply_and_remove_preserve_data_and_order_firewall(managed, monkeypatch) -> None:
    service = {"enabled": False, "active": False}
    events: list[str] = []

    monkeypatch.setattr(native_dlna, "_require_ready", lambda: None)
    monkeypatch.setattr(native_dlna, "_service_state", lambda: dict(service))

    def command(*args: str, **_kwargs: Any) -> SimpleNamespace:
        if args[:2] == ("systemctl", "enable"):
            service["enabled"] = True
        elif args[:2] == ("systemctl", "disable"):
            service["enabled"] = False
        elif args[:2] == ("systemctl", "restart"):
            events.append("start")
            service["active"] = True
        elif args[:2] == ("systemctl", "stop"):
            events.append("stop")
            service["active"] = False
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def sync(*, enabled: bool) -> None:
        events.append(f"firewall:{enabled}")

    monkeypatch.setattr(native_dlna, "_run", command)
    monkeypatch.setattr(native_dlna.native_firewall, "sync_dlna", sync)
    monkeypatch.setattr(native_dlna.native_firewall, "verify_dlna", lambda **_kwargs: {})

    create_plan = native_dlna.plan_dlna(_desired(media_type="video"))
    created = native_dlna.apply_dlna(_desired(media_type="video"), create_plan["planId"])
    assert created["verified"] is True
    assert created["dataPreserved"] is True
    assert "sourcePath" not in created["share"]
    assert events.index("firewall:True") < events.index("start")

    remove = _desired(enabled=False, media_type="video")
    remove_plan = native_dlna.plan_dlna(remove)
    native_dlna.apply_dlna(remove, remove_plan["planId"])
    assert events.index("stop") < events.index("firewall:False")
    assert native_dlna._load_entries(strict=True) == []


def test_apply_rolls_back_config_service_and_firewall(managed, monkeypatch) -> None:
    config_path, drop_in_path = managed
    service = {"enabled": False, "active": False}
    firewall: list[bool] = []
    monkeypatch.setattr(native_dlna, "_require_ready", lambda: None)
    monkeypatch.setattr(native_dlna, "_service_state", lambda: dict(service))
    plan = native_dlna.plan_dlna(_desired())

    def command(*args: str, **_kwargs: Any) -> SimpleNamespace:
        if args[:2] == ("systemctl", "enable"):
            service["enabled"] = True
        elif args[:2] == ("systemctl", "disable"):
            service["enabled"] = False
        elif args[:2] == ("systemctl", "restart"):
            if not service["active"] and service["enabled"]:
                service["active"] = True
                raise OSError("start failed")
            service["active"] = True
        elif args[:2] == ("systemctl", "stop"):
            service["active"] = False
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(native_dlna, "_run", command)
    monkeypatch.setattr(
        native_dlna.native_firewall,
        "sync_dlna",
        lambda *, enabled: firewall.append(enabled),
    )
    monkeypatch.setattr(native_dlna.native_firewall, "verify_dlna", lambda **_kwargs: {})

    with pytest.raises(OSError, match="start failed"):
        native_dlna.apply_dlna(_desired(), plan["planId"])
    assert not config_path.exists()
    assert not drop_in_path.exists()
    assert service == {"enabled": False, "active": False}
    assert firewall == [True, False]


def test_service_start_preflight_revalidates_live_mount_and_firewall(managed, monkeypatch) -> None:
    config_path, drop_in_path = managed
    entry = native_dlna._entry_for_desired(_desired())
    config_path.parent.mkdir(parents=True)
    drop_in_path.parent.mkdir(parents=True)
    config_path.write_text(native_dlna.render_config([entry]), encoding="utf-8")
    drop_in_path.write_text(native_dlna.render_drop_in([entry]), encoding="utf-8")
    verified: list[bool] = []
    monkeypatch.setattr(native_dlna, "_require_ready", lambda: None)
    monkeypatch.setattr(
        native_dlna.native_firewall,
        "verify_dlna",
        lambda *, enabled: verified.append(enabled),
    )

    assert native_dlna.verify_start() == {"shares": 1, "firewall": "verified"}
    assert verified == [True]

    storage = _fake_storage()
    storage._native_folder_path = lambda _folder: PurePosixPath("/srv/pool/Other")
    monkeypatch.setattr(native_dlna, "_storage", lambda: storage)
    with pytest.raises(OSError, match="approved mounted directory"):
        native_dlna.verify_start()


def test_service_unit_hides_all_nas_roots_and_has_no_write_capability() -> None:
    text = Path("deploy/media/echo-dlna.service").read_text(encoding="utf-8")
    assert "User=minidlna" in text
    assert "SupplementaryGroups=users" in text
    assert "CapabilityBoundingSet=\n" in text
    assert "Environment=PYTHONPATH=/opt/echo-agent/site-packages" in text
    assert "ExecStartPre=+/usr/bin/python3 -m appliance.native_dlna verify-start" in text
    assert "ProtectSystem=strict" in text
    assert "PrivateDevices=yes" in text
    assert "InaccessiblePaths=-/data -/mnt -/srv -/fs -/volume" in text
    assert "ReadWritePaths=/var/lib/echo-dlna" in text


def test_native_routes_expose_dlna_status_plan_and_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = {
        "schema": "echo.storage.dlna-plan.v1",
        "planId": "a" * 64,
        "operation": "none",
        "requiresApproval": False,
    }
    result = {**plan, "applied": False, "verified": True, "dataPreserved": True}
    monkeypatch.setattr(
        native_dlna,
        "status",
        lambda: {"schema": "echo.storage.dlna-status.v1", "shares": []},
    )
    monkeypatch.setattr(native_dlna, "plan_dlna", lambda _desired: plan)
    monkeypatch.setattr(native_dlna, "apply_dlna", lambda _desired, _plan_id: result)
    app = FastAPI()
    app.include_router(create_omv_alias_router())
    client = TestClient(app)

    assert client.get("/api/appliance/omv/sharing/dlna").status_code == 200
    preview = client.post("/api/appliance/omv/sharing/dlna/plan", json=_desired(enabled=False))
    applied = client.post(
        "/api/appliance/omv/sharing/dlna/apply",
        json={"desired": _desired(enabled=False), "planId": "a" * 64},
    )
    assert preview.json()["planId"] == "a" * 64
    assert applied.json() == result
