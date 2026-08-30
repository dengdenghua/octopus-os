from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runtime.memory import user_store
from runtime.platform.process.paths import app_paths
from runtime.platform.ui.app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.chdir(tmp_path)
    return TestClient(create_app())


def test_app_paths_are_cwd_relative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    paths = app_paths()
    assert paths.data_dir == tmp_path / "data"
    assert paths.custom_models_path == tmp_path / "data" / "custom_models.json"
    assert paths.user_memory_path == tmp_path / "data" / "user_memory.json"
    assert paths.user_memory_config_path == tmp_path / "data" / "user_memory_config.json"
    assert paths.threads_path == tmp_path / "data" / "threads.jsonl"
    assert paths.cron_jobs_path == tmp_path / "data" / "cron_jobs.json"


def test_user_store_resolves_paths_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    stored = user_store.add_fact("Remember the blue deployment", source="test")

    assert stored is not None
    persisted = tmp_path / "data" / "user_memory.json"
    assert persisted.exists()
    raw = json.loads(persisted.read_text(encoding="utf-8"))
    assert raw["facts"][0]["content"] == "Remember the blue deployment"


def test_memory_api_uses_real_store_before_stub_router(client: TestClient, tmp_path: Path) -> None:
    created = client.post(
        "/api/memory/facts",
        json={
            "content": "Deploys use blue green rollout",
            "category": "ops",
            "source": "manual",
            "scope": "project",
            "project": "echo",
        },
    )

    assert created.status_code == 200, created.text
    body = created.json()
    assert "_stub" not in body
    assert body["facts"][0]["scope"] == "project"
    assert body["facts"][0]["project"] == "echo"
    assert (tmp_path / "data" / "user_memory.json").exists()

    results = client.get("/api/memory/search", params={"q": "blue green"}).json()
    assert results[0]["content"] == "Deploys use blue green rollout"
    assert results[0]["relevance"] > 0


def test_memory_config_uses_same_app_paths(client: TestClient, tmp_path: Path) -> None:
    config = client.put(
        "/api/memory/config",
        json={"enabled": False, "max_facts": 12},
    ).json()

    assert config["enabled"] is False
    assert config["max_facts"] == 12
    assert config["storage_path"] == str(tmp_path / "data" / "user_memory.json")
    assert (tmp_path / "data" / "user_memory_config.json").exists()
