from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from appliance import agent_ui
from appliance.agent_ui import agent_ui_base, agent_workspace_url


def test_external_agent_ui_configuration_is_retired(monkeypatch):
    monkeypatch.setenv("ECHO_AGENT_UI_BASE_URL", "http://127.0.0.1:3001/#/old")

    assert agent_ui_base() is None
    assert agent_workspace_url() is None


def test_missing_agent_ui_keeps_legacy_fallback(monkeypatch):
    monkeypatch.delenv("ECHO_AGENT_UI_BASE_URL", raising=False)

    assert agent_ui_base() is None
    assert agent_workspace_url() is None


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def _configured_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    resources_root = tmp_path / "agent-resources"
    codex_root = tmp_path / "agent-codex"
    resources_root.mkdir()
    codex_executable = codex_root / "bin" / "codex"
    codex_executable.parent.mkdir(parents=True)
    codex_executable.write_bytes(b"codex-test")
    codex_executable.chmod(0o755)
    codex_manifest_path = codex_root / "echo-codex-bundle.json"
    _write_json(
        codex_manifest_path,
        {"schema": "echo.codex_bundle.v1", "version": "0.149.0"},
    )
    source = {
        "source_id": "a" * 40,
        "commit": "a" * 40,
        "dirty": False,
        "packaged_codex_version": "0.149.0",
    }
    resources = {"tree_sha256": "b" * 64, "file_count": 1, "size": 12}
    codex = {
        "tree_sha256": "c" * 64,
        "file_count": 2,
        "size": codex_executable.stat().st_size + codex_manifest_path.stat().st_size,
        "version": "0.149.0",
        "target": "x86_64-unknown-linux-musl",
        "manifest_sha256": hashlib.sha256(codex_manifest_path.read_bytes()).hexdigest(),
        "executable_sha256": hashlib.sha256(codex_executable.read_bytes()).hexdigest(),
    }
    wheel = {"distribution": "echo-agent-runtime", "version": "1.2.3"}
    _write_json(
        resources_root / "agent-build.json",
        {
            "schema_version": 2,
            "kind": "resources",
            "source": source,
            "artifact": resources,
        },
    )
    _write_json(
        codex_root / "agent-build.json",
        {"schema_version": 2, "kind": "codex", "source": source, "artifact": codex},
    )
    manifest = {
        "schema_version": 2,
        "source": source,
        "wheel": wheel,
        "resources": resources,
        "codex": codex,
    }
    manifest_path = tmp_path / "agent-bundle.json"
    _write_json(manifest_path, manifest)
    monkeypatch.setenv("ECHO_AGENT_BUNDLE_MANIFEST", str(manifest_path))
    monkeypatch.setenv("ECHO_RESOURCES_DIR", str(resources_root))
    monkeypatch.setenv("ECHO_CODEX_BUNDLE_DIR", str(codex_root))
    monkeypatch.setenv("ECHO_CODEX_EXECUTABLE", str(codex_executable))
    monkeypatch.delenv("ECHO_AGENT_UI_BASE_URL", raising=False)
    return manifest


def test_config_exposes_verified_agent_bundle(tmp_path, monkeypatch):
    _configured_bundle(tmp_path, monkeypatch)
    monkeypatch.setattr(agent_ui.importlib.metadata, "version", lambda _name: "1.2.3")
    app = FastAPI()
    app.state.echo_agent_api_contract = {
        "schema": "echo.agent_api_contract.v1",
        "compatible": True,
    }

    assert agent_ui.mount_agent_ui(app) is False
    response = TestClient(app).get("/api/appliance/config")

    assert response.status_code == 200
    assert response.json()["agent_bundle"] == {
        "verified": True,
        "source_id": "a" * 40,
        "commit": "a" * 40,
        "dirty": False,
        "distribution": "echo-agent-runtime",
        "version": "1.2.3",
        "packaged_codex_version": "0.149.0",
        "codex_target": "x86_64-unknown-linux-musl",
    }
    assert response.json()["agent_api"] == {
        "schema": "echo.agent_api_contract.v1",
        "compatible": True,
    }


def test_runtime_version_mismatch_fails_startup(tmp_path, monkeypatch):
    _configured_bundle(tmp_path, monkeypatch)
    monkeypatch.setattr(agent_ui.importlib.metadata, "version", lambda _name: "9.9.9")

    with pytest.raises(RuntimeError, match="does not match bundle"):
        agent_ui.mount_agent_ui(FastAPI())


def test_codex_executable_must_be_the_source_bound_bundle_entrypoint(tmp_path, monkeypatch):
    _configured_bundle(tmp_path, monkeypatch)
    monkeypatch.setattr(agent_ui.importlib.metadata, "version", lambda _name: "1.2.3")
    monkeypatch.setenv("ECHO_CODEX_EXECUTABLE", str(tmp_path / "different-codex"))

    with pytest.raises(RuntimeError, match="Codex engine"):
        agent_ui.mount_agent_ui(FastAPI())
