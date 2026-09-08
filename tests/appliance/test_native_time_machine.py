from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from appliance import native_storage, native_time_machine
from appliance.native_storage_routes import create_omv_alias_router
from appliance.omv_models import TimeMachineDesiredState
from appliance.omv_protocol import (
    TIME_MACHINE_DESIRED_SCHEMA,
    validate_time_machine_desired,
)

FOLDER_REF = "11111111-2222-4333-8444-555555555555"
GIB = 1024**3


def _desired(*, enabled: bool = True, owner: str = "alice", maximum: int = 256 * GIB):
    return {
        "schema": TIME_MACHINE_DESIRED_SCHEMA,
        "sharedFolderRef": FOLDER_REF,
        "enabled": enabled,
        "owner": owner,
        "maximumBytes": maximum,
    }


def _fake_storage(tmp_path: Path, *, empty: bool = True, smb: Any = None, nfs=None):
    folder_path = tmp_path / "TimeMachine"
    folder_path.mkdir(exist_ok=True)
    folder = {"uuid": FOLDER_REF, "name": "TimeMachine", "relativePath": "TimeMachine"}

    def canonical(value: Any) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    return SimpleNamespace(
        _resolve_shared_folder=lambda reference: (
            folder
            if reference == FOLDER_REF
            else (_ for _ in ()).throw(ValueError("unknown folder"))
        ),
        _native_registered_path=lambda _folder: folder_path,
        _native_folder_path=lambda _folder: folder_path,
        _native_registered_folder_status=lambda _folder: "MOUNTED",
        _registered_relative_name=lambda _folder, strict=False: "TimeMachine",
        _constrained_user_snapshot=lambda owner: {"name": owner} if owner == "alice" else None,
        _smb_usershare_info=lambda _name: smb,
        _nfs_exports_load=lambda **_kwargs: list(nfs or []),
        _directory_is_empty=lambda _path: empty,
        _canonical_hash=canonical,
        _registry_transaction=contextlib.nullcontext,
        _atomic_text_save=lambda path, content, mode: path.write_text(content, encoding="utf-8"),
    )


@pytest.fixture
def managed_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    managed = tmp_path / "echo-os-time-machine.conf"
    samba = tmp_path / "smb.conf"
    monkeypatch.setattr(native_time_machine, "MANAGED_CONFIG", managed)
    monkeypatch.setattr(native_time_machine, "SAMBA_CONFIG", samba)
    monkeypatch.setattr(
        native_time_machine,
        "_INCLUDE_LINE",
        f"include = {managed}",
    )
    samba.write_text(f"[global]\n  include = {managed}\n", encoding="utf-8")
    return managed, samba


def test_time_machine_desired_contract_rejects_unsafe_owner_and_unaligned_limit() -> None:
    with pytest.raises(ValueError, match="owner name"):
        validate_time_machine_desired(_desired(owner="Alice;admin"))
    with pytest.raises(ValueError, match="whole GiB"):
        validate_time_machine_desired(_desired(maximum=64 * GIB + 1))
    with pytest.raises(ValidationError):
        TimeMachineDesiredState.model_validate(_desired(maximum=32 * GIB))


def test_empty_config_matches_provisioning_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(native_time_machine, "_storage", lambda: _fake_storage(tmp_path))
    expected = (
        Path("deploy/provision/base/echo-os-time-machine.conf")
        .read_text(encoding="utf-8")
        .replace("\r\n", "\n")
    )
    assert native_time_machine.render_config([]) == expected


def test_config_round_trip_is_canonical_and_tamper_evident(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, managed_paths
) -> None:
    managed, _samba = managed_paths
    monkeypatch.setattr(native_time_machine, "_storage", lambda: _fake_storage(tmp_path))
    entry = native_time_machine._entry_for_desired(_desired())
    rendered = native_time_machine.render_config([entry])
    managed.write_text(rendered, encoding="utf-8")

    assert native_time_machine._load_entries(strict=True) == [entry]
    assert "fruit:time machine = yes" in rendered
    assert "fruit:time machine max size = 256G" in rendered
    assert "valid users = alice" in rendered

    managed.write_text(rendered.replace("guest ok = no", "guest ok = yes"), encoding="utf-8")
    with pytest.raises(OSError, match="modified outside Echo OS"):
        native_time_machine._load_entries(strict=True)


def test_plan_requires_dedicated_empty_folder_and_no_protocol_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, managed_paths
) -> None:
    managed, _samba = managed_paths
    monkeypatch.setattr(native_time_machine, "_require_ready", lambda: None)

    monkeypatch.setattr(
        native_time_machine, "_storage", lambda: _fake_storage(tmp_path, empty=False)
    )
    managed.write_text(native_time_machine.render_config([]), encoding="utf-8")
    with pytest.raises(ValueError, match="dedicated empty folder"):
        native_time_machine.plan_time_machine(_desired())

    monkeypatch.setattr(native_time_machine, "_storage", lambda: _fake_storage(tmp_path, smb={}))
    with pytest.raises(ValueError, match="ordinary SMB"):
        native_time_machine.plan_time_machine(_desired())

    monkeypatch.setattr(
        native_time_machine,
        "_storage",
        lambda: _fake_storage(tmp_path, nfs=[{"sharedFolderRef": FOLDER_REF}]),
    )
    with pytest.raises(ValueError, match="remove NFS"):
        native_time_machine.plan_time_machine(_desired())


def test_plan_and_apply_create_then_disable_preserves_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, managed_paths
) -> None:
    managed, _samba = managed_paths
    storage = _fake_storage(tmp_path)
    monkeypatch.setattr(native_time_machine, "_storage", lambda: storage)
    monkeypatch.setattr(native_time_machine, "_require_ready", lambda: None)
    monkeypatch.setattr(native_time_machine, "_run_checked", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(native_time_machine, "_verify_live_entry", lambda _entry: None)
    monkeypatch.setattr(native_time_machine, "_verify_live_absent", lambda _name: None)
    managed.write_text(native_time_machine.render_config([]), encoding="utf-8")

    create_plan = native_time_machine.plan_time_machine(_desired())
    assert create_plan["operation"] == "create"
    assert create_plan["requiresApproval"] is True
    created = native_time_machine.apply_time_machine(_desired(), create_plan["planId"])
    assert created["applied"] is True
    assert created["verified"] is True
    assert created["dataPreserved"] is True
    assert "path" not in created["share"]
    assert native_time_machine._load_entries(strict=True)[0]["owner"] == "alice"
    public_status = native_time_machine.status()
    assert public_status["shares"][0]["status"] == "MOUNTED"
    assert "path" not in public_status["shares"][0]

    disable = _desired(enabled=False)
    remove_plan = native_time_machine.plan_time_machine(disable)
    assert remove_plan["operation"] == "remove"
    removed = native_time_machine.apply_time_machine(disable, remove_plan["planId"])
    assert removed["dataPreserved"] is True
    assert native_time_machine._load_entries(strict=True) == []


def test_apply_rolls_back_exact_config_when_reload_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, managed_paths
) -> None:
    managed, _samba = managed_paths
    storage = _fake_storage(tmp_path)
    monkeypatch.setattr(native_time_machine, "_storage", lambda: storage)
    monkeypatch.setattr(native_time_machine, "_require_ready", lambda: None)
    monkeypatch.setattr(native_time_machine, "_verify_live_entry", lambda _entry: None)
    old = native_time_machine.render_config([])
    managed.write_text(old, encoding="utf-8")
    plan = native_time_machine.plan_time_machine(_desired())
    reloads = 0

    def command(*args: str, **_kwargs: Any) -> str:
        nonlocal reloads
        if args[:3] == ("systemctl", "reload", "smbd.service"):
            reloads += 1
            if reloads == 1:
                raise OSError("reload failed")
        return ""

    monkeypatch.setattr(native_time_machine, "_run_checked", command)
    with pytest.raises(OSError, match="reload failed"):
        native_time_machine.apply_time_machine(_desired(), plan["planId"])
    assert managed.read_text(encoding="utf-8") == old
    assert reloads == 2


def test_managed_include_must_be_unique_and_inside_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, managed_paths
) -> None:
    managed, samba = managed_paths
    managed.write_text(native_time_machine.render_config([]), encoding="utf-8")
    assert native_time_machine._config_has_managed_include() is True

    samba.write_text(f"[share]\n include = {managed}\n", encoding="utf-8")
    assert native_time_machine._config_has_managed_include() is False
    samba.write_text(
        f"[global]\n include = {managed}\n include = {managed}\n",
        encoding="utf-8",
    )
    assert native_time_machine._config_has_managed_include() is False


def test_existing_time_machine_share_blocks_reverse_smb_nfs_and_folder_detach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dependency = {"sharedFolderRef": FOLDER_REF, "name": "TimeMachine"}
    monkeypatch.setattr(
        native_storage.native_time_machine, "dependency_for", lambda _ref: dependency
    )
    monkeypatch.setattr(
        native_storage.native_time_machine,
        "dependency_for_name",
        lambda _name: dependency,
    )
    monkeypatch.setattr(
        native_storage,
        "_resolve_shared_folder",
        lambda _ref: {"uuid": FOLDER_REF, "name": "TimeMachine"},
    )
    monkeypatch.setattr(native_storage, "_native_registered_path", lambda _entry: tmp_path)
    monkeypatch.setattr(
        native_storage, "_native_registered_folder_status", lambda _entry: "MOUNTED"
    )
    monkeypatch.setattr(native_storage, "_smb_usershare_info", lambda _name: None)
    monkeypatch.setattr(native_storage, "_nfs_exports_load", lambda **_kwargs: [])

    with pytest.raises(ValueError, match="Time Machine"):
        native_storage._build_smb_plan(
            {
                "schema": "echo.omv.smb-share-desired.v1",
                "sharedFolderRef": FOLDER_REF,
                "enabled": True,
                "readOnly": False,
                "browseable": True,
                "recycleBin": False,
                "comment": "",
            }
        )
    with pytest.raises(ValueError, match="Time Machine"):
        native_storage._build_nfs_plan(
            {
                "schema": "echo.omv.nfs-share-desired.v1",
                "sharedFolderRef": FOLDER_REF,
                "clientCidr": "192.168.1.0/24",
                "readOnly": False,
                "comment": "",
            }
        )
    assert native_storage._shared_folder_detach_dependencies(
        {"uuid": FOLDER_REF, "name": "TimeMachine"}
    ) == (False, [], dependency)


def test_native_routes_expose_time_machine_status_plan_and_noop_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = {
        "schema": "echo.storage.time-machine-plan.v1",
        "planId": "a" * 64,
        "operation": "none",
        "requiresApproval": False,
    }
    result = {**plan, "applied": False, "verified": True, "dataPreserved": True}
    monkeypatch.setattr(
        native_time_machine,
        "status",
        lambda: {"schema": "echo.storage.time-machine-status.v1", "shares": []},
    )
    monkeypatch.setattr(native_time_machine, "plan_time_machine", lambda _desired: plan)
    monkeypatch.setattr(
        native_time_machine,
        "apply_time_machine",
        lambda _desired, _plan_id: result,
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router())
    client = TestClient(app)

    status = client.get("/api/appliance/omv/sharing/time-machine")
    preview = client.post(
        "/api/appliance/omv/sharing/time-machine/plan", json=_desired(enabled=False)
    )
    applied = client.post(
        "/api/appliance/omv/sharing/time-machine/apply",
        json={"desired": _desired(enabled=False), "planId": "a" * 64},
    )

    assert status.status_code == 200
    assert preview.json() == plan
    assert applied.json() == result
