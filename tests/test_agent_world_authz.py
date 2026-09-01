"""Authorization regression coverage for shared Agent World installs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway import agent_world_router
from runtime.sensing.gateway.agent_world_router import create_agent_world_router


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _secured_client(
    tmp_path: Path,
    monkeypatch: Any,
    *,
    allow_local_user_plugin_lifecycle: bool = False,
) -> tuple[TestClient, dict[str, str]]:
    agents_root = tmp_path / "agents"
    resources = tmp_path / "resources"
    monkeypatch.setattr(agent_world_router, "_INSTALL_STATE", tmp_path / "installed.json")
    monkeypatch.setattr(agent_world_router, "default_agents_root", lambda: agents_root)
    monkeypatch.setattr(agent_world_router, "resources_root", lambda: resources)

    template = {"id": "demo", "author": "test", "available_skills": []}
    monkeypatch.setattr(agent_world_router, "_template_by_id", lambda agent_id: template)
    monkeypatch.setattr(agent_world_router, "_template_skill_catalog", lambda _template: [])
    monkeypatch.setattr(agent_world_router, "_read_agent_private_skills", lambda _root: [])
    monkeypatch.setattr(agent_world_router, "_is_market_managed_agent", lambda *_a, **_k: True)

    def _install_local(_agent_id: str, root: Path, **_kwargs: Any) -> Path:
        target = root / "demo"
        (target / "agent-core").mkdir(parents=True, exist_ok=True)
        return target

    monkeypatch.setattr(agent_world_router, "_install_template_agent", _install_local)

    import runtime.platform.plugins.cloud_catalog as cloud_catalog
    import runtime.platform.plugins.cloud_expert_store as cloud_expert_store

    class _CloudExpertStore:
        def install_expert(self, expert_id: str, **_kwargs: Any) -> dict[str, Any]:
            return {"installed": True, "agent_id": expert_id}

    class _CloudCatalog:
        def __init__(self, kind: str) -> None:
            self.kind = kind

        def install_skill(self, name: str) -> dict[str, Any]:
            return {"installed": True, "name": name}

        def items(self) -> list[dict[str, Any]]:
            return [{"id": "demo-plugin", "kind": "plugin", "plugin": "demo"}]

        def install_plugin(self, name: str, *, plugin_kind: str) -> dict[str, Any]:
            return {"installed": True, "name": name, "kind": plugin_kind}

        def uninstall_plugin(self, name: str, *, plugin_kind: str) -> dict[str, Any]:
            return {"uninstalled": True, "name": name, "kind": plugin_kind}

    monkeypatch.setattr(cloud_expert_store, "CloudExpertStore", _CloudExpertStore)
    monkeypatch.setattr(cloud_catalog, "CloudCatalog", _CloudCatalog)

    identities = IdentityStore()
    identities.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    identities.add(
        Identity(actor_id="admin", roles=("admin",)),
        api_key_plaintext="sk-admin",
    )
    app = FastAPI()
    app.include_router(
        create_agent_world_router(
            identity_store=identities,
            require_auth=True,
            allow_local_user_plugin_lifecycle=allow_local_user_plugin_lifecycle,
        )
    )
    return TestClient(app), {
        "alice": "sk-alice",
        "admin": "sk-admin",
    }


def test_agent_world_lists_remain_available_to_authenticated_users(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    client, keys = _secured_client(tmp_path, monkeypatch)

    response = client.get(
        "/api/agent-market/store",
        headers=_headers(keys["alice"]),
    )

    assert response.status_code == 200


def test_agent_world_shared_content_mutations_reject_non_admin(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    client, keys = _secured_client(tmp_path, monkeypatch)
    requests = (
        ("POST", "/api/agent-market/store/demo/install", {}),
        ("DELETE", "/api/agent-market/store/demo/install", {}),
        ("POST", "/api/agent-market/cloud/store/demo/install", {}),
        ("POST", "/api/agent-market/cloud/skills/demo/install", {}),
        ("POST", "/api/agent-market/cloud/plugins/demo-plugin/install", {}),
        ("DELETE", "/api/agent-market/cloud/plugins/demo-plugin/install", {}),
    )

    for method, path, kwargs in requests:
        response = client.request(
            method,
            path,
            headers=_headers(keys["alice"]),
            **kwargs,
        )
        assert response.status_code == 403, path


def test_agent_world_admin_can_install_and_uninstall_shared_content(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    client, keys = _secured_client(tmp_path, monkeypatch)
    headers = _headers(keys["admin"])

    assert client.post("/api/agent-market/store/demo/install", headers=headers).status_code == 200
    assert (
        client.post(
            "/api/agent-market/cloud/store/demo/install",
            headers=headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/agent-market/cloud/skills/demo/install",
            headers=headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/agent-market/cloud/plugins/demo-plugin/install",
            headers=headers,
        ).status_code
        == 200
    )
    assert (
        client.delete(
            "/api/agent-market/cloud/plugins/demo-plugin/install",
            headers=headers,
        ).status_code
        == 200
    )
    assert client.delete("/api/agent-market/store/demo/install", headers=headers).status_code == 200


def test_agent_world_local_desktop_allows_authenticated_plugin_lifecycle(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """The loopback desktop may manage its own cloud plugin catalog."""

    client, keys = _secured_client(
        tmp_path,
        monkeypatch,
        allow_local_user_plugin_lifecycle=True,
    )
    headers = _headers(keys["alice"])

    installed = client.post(
        "/api/agent-market/cloud/plugins/demo-plugin/install",
        headers=headers,
    )
    removed = client.delete(
        "/api/agent-market/cloud/plugins/demo-plugin/install",
        headers=headers,
    )

    assert installed.status_code == 200
    assert removed.status_code == 200


def test_production_agent_install_cannot_hot_register_mutable_prompt_directory(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from runtime.execution.suckers import SkillRegistry
    from runtime.sensing.gateway._agent_world_helpers import _register_public_prompt_skills

    skill_dir = tmp_path / "mutable" / "remote"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: remote\ndescription: mutable\n---\nMUTABLE REMOTE PROMPT\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", "server")
    registry = SkillRegistry()

    assert _register_public_prompt_skills(registry, tmp_path / "mutable") == 0
    assert not registry.has("remote")


def test_production_rejects_unsigned_cloud_content_installs(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    client, keys = _secured_client(tmp_path, monkeypatch)
    monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", "production")
    headers = _headers(keys["admin"])

    responses = [
        client.post("/api/agent-market/cloud/store/demo/install", headers=headers),
        client.post("/api/agent-market/cloud/skills/demo/install", headers=headers),
        client.post("/api/agent-market/cloud/plugins/demo-plugin/install", headers=headers),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]
    assert all("shared/commercial" in response.json()["detail"] for response in responses)


def test_reviewed_factory_workbench_delegates_to_live_plugin_hub(
    monkeypatch: Any,
) -> None:
    import runtime.platform.plugins.cloud_catalog as cloud_catalog

    class FactoryCatalog:
        def __init__(self, _kind: str) -> None:
            pass

        @staticmethod
        def is_factory_plugin(plugin_id: str) -> bool:
            return plugin_id == "narrative_studio"

        def items(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": "workbench_narrative",
                    "plugin": "narrative_studio",
                    "kind": "workbench",
                    "factory_seed": True,
                }
            ]

    class Hub:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def install_plugin(self, name: str, **kwargs: Any) -> dict[str, Any]:
            self.calls.append((name, kwargs))
            return {"ok": True, "installed": True, "restart_required": False}

        def uninstall_plugin(self, name: str, **kwargs: Any) -> dict[str, Any]:
            self.calls.append((name, kwargs))
            return {"ok": True, "installed": False, "restart_required": False}

    monkeypatch.setattr(cloud_catalog, "CloudCatalog", FactoryCatalog)
    monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", "production")
    identities = IdentityStore()
    identities.add(Identity(actor_id="admin", roles=("admin",)), api_key_plaintext="sk-admin")
    app = FastAPI()
    hub = Hub()
    app.state.plugin_hub = hub
    app.include_router(create_agent_world_router(identity_store=identities, require_auth=True))
    client = TestClient(app)
    headers = _headers("sk-admin")

    installed = client.post(
        "/api/agent-market/cloud/plugins/workbench_narrative/install",
        headers=headers,
        json={"enabled": False},
    )
    removed = client.delete(
        "/api/agent-market/cloud/plugins/workbench_narrative/install",
        headers=headers,
        params={"data_policy": "trash", "confirm_data_move": "true"},
    )

    assert installed.status_code == 200
    assert removed.status_code == 200
    assert hub.calls == [
        (
            "narrative_studio",
            {"enabled": False, "restore_data": False, "recovery_id": None},
        ),
        (
            "narrative_studio",
            {"data_policy": "trash", "confirm_data_move": True},
        ),
    ]

