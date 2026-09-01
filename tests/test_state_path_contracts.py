from __future__ import annotations

from pathlib import Path

import pytest
from runtime.memory.runtime_state.hub import MemoryHub
from runtime.memory.runtime_state.scope_paths import project_root_from_metadata
from runtime.platform.process.paths import app_paths


def test_pause_control_defaults_to_app_data_dir(monkeypatch, tmp_path: Path) -> None:
    from runtime.core.cerebrum import pause_control

    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "runtime-data"))
    monkeypatch.delenv("ECHO_HOME", raising=False)

    assert pause_control._default_store_path() == app_paths().data_dir / "pause_state.json"


def test_capabilities_store_defaults_to_app_data_dir(monkeypatch, tmp_path: Path) -> None:
    from runtime.platform import capabilities

    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "runtime-data"))
    monkeypatch.delenv("ECHO_HOME", raising=False)

    assert capabilities._store_path() == app_paths().data_dir / "capabilities.json"


def test_memory_hub_uses_project_root_when_repo_root_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='echo-agent'\n", encoding="utf-8")
    (repo / "runtime").mkdir()
    monkeypatch.chdir(repo / "runtime")
    monkeypatch.delenv("ECHO_DATA_DIR", raising=False)
    monkeypatch.delenv("ECHO_HOME", raising=False)

    hub = MemoryHub()

    assert hub.repo_root == repo.resolve()


def test_project_root_from_metadata_falls_back_to_discovered_project_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='echo-agent'\n", encoding="utf-8")
    (repo / "runtime").mkdir()
    monkeypatch.chdir(repo / "runtime")

    assert project_root_from_metadata({}) == repo.resolve()
