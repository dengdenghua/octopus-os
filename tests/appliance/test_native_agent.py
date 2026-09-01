from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import agent_ui, native_entrypoint, native_extension


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
