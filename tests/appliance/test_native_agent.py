from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import agent_ui, native_entrypoint, native_extension
from runtime.safety.auth.identity import encode_jwt_hs256


def test_native_entrypoint_executes_official_cli_on_loopback(tmp_path, monkeypatch) -> None:
    observed: dict[str, object] = {}

    def _execv(program: str, argv: list[str]) -> None:
        observed["program"] = program
        observed["argv"] = argv
        raise OSError("exec captured")

    monkeypatch.setenv("ECHO_NATIVE_OS", "1")
    monkeypatch.setenv("ECHO_NATIVE_AGENT_PORT", "8123")
    config = tmp_path / "config.yaml"
    config.write_text("preset: personal\n")
    monkeypatch.setenv("ECHO_NATIVE_AGENT_CONFIG", str(config))
    monkeypatch.delenv("ECHO_PACKAGED_CODEX_VERSION", raising=False)
    monkeypatch.delenv("ECHO_RUNTIME_SOURCE_ID", raising=False)
    monkeypatch.delenv("ECHO_RUNTIME_BUNDLE_VERIFIED", raising=False)
    monkeypatch.setattr(
        agent_ui,
        "agent_bundle_status",
        lambda: {
            "verified": True,
            "source_id": "a" * 40,
            "version": "1.2.3",
            "packaged_codex_version": "0.149.0",
        },
    )
    monkeypatch.setattr(os, "execv", _execv)

    with pytest.raises(OSError, match="exec captured"):
        native_entrypoint.main()

    assert observed["program"] == os.sys.executable
    assert observed["argv"] == [
        os.sys.executable,
        "-m",
        "runtime.cli",
        "serve",
        "--config",
        str(config),
        "--host",
        "127.0.0.1",
        "--port",
        "8123",
    ]
    assert os.environ["ECHO_PACKAGED_CODEX_VERSION"] == "0.149.0"
    assert os.environ["ECHO_RUNTIME_SOURCE_ID"] == "a" * 40
    assert os.environ["ECHO_RUNTIME_BUNDLE_VERIFIED"] == "1"


def test_native_entrypoint_rejects_an_unversioned_agent_bundle(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_NATIVE_OS", "1")
    monkeypatch.setattr(
        agent_ui,
        "agent_bundle_status",
        lambda: {
            "verified": True,
            "source_id": "dirty-development-tree",
            "version": "1.2.3",
        },
    )

    with pytest.raises(RuntimeError, match="clean source revision"):
        native_entrypoint.main()


def test_native_entrypoint_provisions_opaque_auth_only_for_ci_credential(
    tmp_path, monkeypatch
) -> None:
    data = tmp_path / "state"
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    (credentials / native_entrypoint.CI_SESSION_CREDENTIAL).write_bytes(b"1")
    monkeypatch.setenv("ECHO_DATA_DIR", str(data))
    monkeypatch.setenv("ECHO_APPLIANCE_REQUIRE_PROVISIONED_AUTH", "1")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials))

    assert native_entrypoint._provision_ci_auth() is True
    payload = (data / "appliance-auth.json").read_text(encoding="utf-8")
    assert '"username":"admin"' in payload
    assert "ECHO_ADMIN_PASSWORD" not in os.environ
    assert native_entrypoint._provision_ci_auth() is False


def test_native_entrypoint_rejects_invalid_ci_credential(tmp_path, monkeypatch) -> None:
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    (credentials / native_entrypoint.CI_SESSION_CREDENTIAL).write_bytes(b"0")
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ECHO_APPLIANCE_REQUIRE_PROVISIONED_AUTH", "1")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials))

    with pytest.raises(RuntimeError, match="CI credential is invalid"):
        native_entrypoint._provision_ci_auth()


@pytest.mark.parametrize("value", ["0", "65536", "http", "8000 extra"])
def test_native_entrypoint_rejects_invalid_ports(value: str, monkeypatch) -> None:
    monkeypatch.setenv("ECHO_NATIVE_AGENT_PORT", value)

    with pytest.raises(RuntimeError, match="valid TCP port"):
        native_entrypoint._port()


def test_native_extension_does_not_require_a_second_agent_webui(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_NATIVE_OS", "1")
    called = False

    def _mount(_app) -> bool:
        nonlocal called
        called = True
        return False

    monkeypatch.setattr(agent_ui, "mount_agent_ui", _mount)
    app = FastAPI()
    app.state.task_supervisor = None

    native_extension.register_app(app, object())

    assert called is True


def test_native_extension_projects_the_live_agent_task_supervisor(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_NATIVE_OS", "1")
    monkeypatch.setattr(agent_ui, "mount_agent_ui", lambda _app: True)
    app = FastAPI()
    app.state.task_supervisor = None

    native_extension.register_app(app, object())

    response = TestClient(app).get("/api/appliance/tasks")
    body = response.json()
    assert response.status_code == 200
    assert body == {
        "schema": "echo.task_projection.v1",
        "available": False,
        "generatedAt": body["generatedAt"],
        "counts": {
            "total": 0,
            "active": 0,
            "waitingApproval": 0,
            "paused": 0,
            "recoveryNeeded": 0,
            "failed": 0,
            "completed": 0,
        },
        "auditIntegrity": {
            "available": False,
            "ok": None,
            "entriesChecked": 0,
        },
        "tasks": [],
    }


def test_native_extension_mounts_desktop_file_fallback_with_host_auth(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ECHO_NATIVE_OS", "1")
    monkeypatch.setenv("ECHO_DESKTOP", "1")
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(agent_ui, "mount_agent_ui", lambda _app: True)
    app = FastAPI()
    app.state.task_supervisor = None
    (tmp_path / "desktop-files" / "notes").mkdir(parents=True)
    (tmp_path / "desktop-files" / "notes" / "readme.txt").write_text("hello")

    class Context:
        jwt_secret = "desktop-secret"

    native_extension.register_app(app, Context())

    response = TestClient(app).get("/api/appliance/files/list?path=")
    assert response.status_code == 401
    assert response.json() == {"detail": "authentication required"}
    token = encode_jwt_hs256(
        {"sub": "local:desktop", "iat": 0, "exp": 9_999_999_999},
        secret="desktop-secret",
    )
    listed = TestClient(app).get(
        "/api/appliance/files/list?path=notes",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert listed.status_code == 200
    assert [entry["name"] for entry in listed.json()["entries"]] == ["readme.txt"]

    manifest = TestClient(app).get("/api/storage/v1/manifest")
    assert manifest.status_code == 200
    assert manifest.json()["role"] == "embedded"
    files = TestClient(app).get("/api/storage/v1/files?kind=document")
    assert files.status_code == 200
    assert files.json()[0]["resource_id"].startswith("storage-file:v1:")


def test_native_extension_is_inert_outside_native_os(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_NATIVE_OS", raising=False)
    called = False

    def _mount(_app) -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(agent_ui, "mount_agent_ui", _mount)
    native_extension.register_app(object(), object())

    assert called is False
