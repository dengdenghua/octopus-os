from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.memory import user_store
from runtime.platform.process.paths import app_paths
from runtime.platform.ui.app import create_app
from runtime.safety.auth import Identity, IdentityStore
from runtime.safety.auth.scope import TenantScope


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


def test_authenticated_memory_search_and_assets_include_only_visible_shared_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    user_store.add_fact(
        "Team release calendar",
        category="ops",
        tenant_scope=TenantScope("tenant-a", "alice"),
        visibility="team",
        team_id="release-room",
    )
    private = user_store.add_fact(
        "Alice private launch secret",
        category="profile",
        tenant_scope=TenantScope("tenant-a", "alice"),
    )
    user_store.add_fact(
        "Foreign tenant release calendar",
        category="ops",
        tenant_scope=TenantScope("tenant-b", "carol"),
        visibility="team",
        team_id="release-room",
    )
    assert private is not None

    identities = IdentityStore()
    identities.add(
        Identity(
            actor_id="bob",
            roles=("member",),
            metadata={"tenant_id": "tenant-a", "team_ids": ["release-room"]},
        ),
        api_key_plaintext="sk-bob",
    )
    app = create_app(
        cocoloop_identity_store=identities,
        cocoloop_require_auth=True,
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer sk-bob"}

    results = client.get(
        "/api/memory/search",
        params={"q": "release calendar"},
        headers=headers,
    )
    assert results.status_code == 200, results.text
    contents = [item["content"] for item in results.json()]
    assert "Team release calendar" in contents
    assert "Foreign tenant release calendar" not in contents
    assert "Alice private launch secret" not in contents

    assets = client.get("/api/memory/assets", headers=headers)
    assert assets.status_code == 200, assets.text
    asset_contents = [item["content"] for item in assets.json()["items"]]
    assert "Team release calendar" in asset_contents
    assert "Foreign tenant release calendar" not in asset_contents
    assert "Alice private launch secret" not in asset_contents

    asset_id = next(
        item["id"]
        for item in assets.json()["items"]
        if item["content"] == "Team release calendar"
    )
    trace = client.get(f"/api/memory/assets/{asset_id}/trace", headers=headers)
    assert trace.status_code == 200, trace.text
