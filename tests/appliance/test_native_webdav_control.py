from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import native_webdav_control as control
from appliance.native_storage_routes import create_omv_alias_router


def _service_state(enabled: bool) -> dict[str, object]:
    units = {
        unit: {"enabled": enabled, "active": enabled}
        for unit in (*control.CORE_SERVICES, *control.REFRESH_UNITS)
    }
    return {"enabled": enabled, "active": enabled, "units": units}


@pytest.fixture
def policy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "native-webdav.json"
    monkeypatch.setattr(control, "POLICY_PATH", path)
    monkeypatch.setattr(control, "capability_available", lambda: True)
    monkeypatch.setattr(control.shutil, "which", lambda _name: "C:/test/tool")
    monkeypatch.setattr(
        control,
        "_registered_shares",
        lambda: [
            {
                "sharedFolderRef": "11111111-1111-4111-8111-111111111111",
                "name": "photos",
                "status": "MOUNTED",
            }
        ],
    )
    return path


def test_missing_policy_is_fail_closed(policy_path: Path) -> None:
    assert control.publication_enabled(strict=True) is False
    assert not policy_path.exists()


def test_plan_requires_an_explicit_transition_and_mounted_share(
    policy_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(control, "_service_state", lambda: _service_state(False))
    desired = {"schema": "echo.storage.webdav-desired.v1", "enabled": True}

    plan = control.plan_webdav(desired)

    assert plan["operation"] == "enable"
    assert plan["requiresApproval"] is True
    assert plan["publishedShareCount"] == 1
    assert len(plan["planId"]) == 64
    assert not policy_path.exists()


def test_plan_rejects_enablement_without_a_mounted_share(
    policy_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(control, "_service_state", lambda: _service_state(False))
    monkeypatch.setattr(
        control,
        "_registered_shares",
        lambda: [
            {
                "sharedFolderRef": "11111111-1111-4111-8111-111111111111",
                "name": "offline",
                "status": "UNAVAILABLE",
            }
        ],
    )
    with pytest.raises(ValueError, match="mounted registered"):
        control.plan_webdav({"schema": "echo.storage.webdav-desired.v1", "enabled": True})
    assert not policy_path.exists()


def test_apply_publishes_policy_then_converges_runtime(
    policy_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = {"enabled": False}
    monkeypatch.setattr(
        control,
        "_service_state",
        lambda: _service_state(runtime["enabled"]),
    )
    monkeypatch.setattr(
        control,
        "_set_runtime",
        lambda enabled: runtime.__setitem__("enabled", enabled),
    )
    desired = {"schema": "echo.storage.webdav-desired.v1", "enabled": True}
    plan = control.plan_webdav(desired)

    result = control.apply_webdav(desired, plan["planId"])

    assert result["applied"] is True
    assert result["status"]["enabled"] is True
    assert json.loads(policy_path.read_text(encoding="utf-8")) == {
        "enabled": True,
        "schema": control.POLICY_SCHEMA,
    }


def test_apply_restores_policy_and_runtime_after_transition_failure(
    policy_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control._write_policy(False)
    monkeypatch.setattr(control, "_service_state", lambda: _service_state(False))
    calls: list[bool] = []

    def transition(enabled: bool) -> None:
        calls.append(enabled)
        if enabled:
            raise OSError("injected start failure")

    monkeypatch.setattr(control, "_set_runtime", transition)
    desired = {"schema": "echo.storage.webdav-desired.v1", "enabled": True}
    plan = control.plan_webdav(desired)

    with pytest.raises(OSError, match="injected start failure"):
        control.apply_webdav(desired, plan["planId"])

    assert calls == [True, False]
    assert control.publication_enabled(strict=True) is False
    assert policy_path.read_bytes() == control._policy_bytes(False)


def test_apply_rejects_a_stale_plan_without_mutation(
    policy_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(control, "_service_state", lambda: _service_state(False))
    with pytest.raises(ValueError, match="stale"):
        control.apply_webdav(
            {"schema": "echo.storage.webdav-desired.v1", "enabled": True},
            "0" * 64,
        )
    assert not policy_path.exists()


def test_image_and_provisioning_keep_webdav_disabled_until_approval() -> None:
    repository = Path(__file__).resolve().parents[2]
    preset = (
        repository / "packaging/image/mkosi.extra/usr/lib/systemd/system-preset/80-echo-os.preset"
    ).read_text(encoding="utf-8")
    provision = (repository / "deploy/provision/base/provision-lib.sh").read_text(encoding="utf-8")
    refresh_path = (repository / "deploy/webdav/echo-webdav-refresh.path").read_text(
        encoding="utf-8"
    )

    for unit in (*control.CORE_SERVICES, *control.REFRESH_UNITS):
        assert f"disable {unit}" in preset
    assert "systemctl disable --now echo-webdav-refresh.path" in provision
    assert "systemctl enable echo-webdav-jails.service" not in provision
    assert "PathChanged=/var/lib/echo-os/native-webdav.json" in refresh_path


def test_storage_broker_exposes_only_the_paired_webdav_plan_and_apply() -> None:
    from appliance import native_storage_broker as broker

    assert broker.operation_for_callable(control.plan_webdav) == (
        "native_webdav_control.plan_webdav"
    )
    assert broker.operation_for_callable(control.apply_webdav) == (
        "native_webdav_control.apply_webdav"
    )


def test_native_alias_exposes_webdav_status_and_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    monkeypatch.setattr(
        control,
        "status",
        lambda: {
            "schema": "echo.storage.webdav-status.v1",
            "available": True,
            "enabled": False,
            "active": False,
            "endpoint": "/webdav/",
            "certificateEndpoint": "/api/appliance/tls/certificate",
            "publishedShares": [],
            "registeredShareCount": 1,
            "source": "native",
            "tlsRequired": True,
        },
    )
    monkeypatch.setattr(
        control,
        "plan_webdav",
        lambda desired: {
            "schema": "echo.storage.webdav-plan.v1",
            "planId": "a" * 64,
            "operation": "enable" if desired["enabled"] else "none",
        },
    )
    app = FastAPI()
    app.include_router(create_omv_alias_router())
    client = TestClient(app)

    status = client.get("/api/appliance/omv/sharing/webdav")
    plan = client.post(
        "/api/appliance/omv/sharing/webdav/plan",
        json={"schema": "echo.storage.webdav-desired.v1", "enabled": True},
    )

    assert status.status_code == 200
    assert status.json()["enabled"] is False
    assert plan.status_code == 200
    assert plan.json()["operation"] == "enable"
