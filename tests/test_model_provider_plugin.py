from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.capabilities.capability_registry import CapabilityRegistry
from runtime.platform.capabilities.permission_grants import CapabilityPermissionStore
from runtime.platform.connectors.auth_orchestrator import AuthOrchestrator
from runtime.platform.connectors.connector_registry import ConnectorRegistry
from runtime.platform.connectors.credential_store import CredentialStore
from runtime.platform.models.model_provider_plugin import (
    ModelProviderPluginManager,
    model_provider_entry_has_key,
    resolve_model_provider_api_key,
)
from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway.capability_router import create_capability_router
from runtime.sensing.gateway.config_router import create_config_router


class _Credentials:
    def __init__(self) -> None:
        self.values = {
            "opencode-zen": {"api_key": "zen-secret"},
            "freebuff2api-community": {
                "api_key": "sk-fb-secret",
                "base_url": "https://gateway.example/v1",
            },
        }

    def get_secret(self, connector_id: str, key: str) -> str | None:
        return self.values.get(connector_id, {}).get(key)

    def list_secrets(self, connector_id: str) -> list[str]:
        return list(self.values.get(connector_id, {}))


def _item() -> dict[str, Any]:
    return {
        "id": "opencode-zen",
        "name": "OpenCode Zen Models",
        "name_zh": "OpenCode Zen 模型适配器",
        "model_provider": {
            "entry_id": "opencode-zen",
            "display_name": "OpenCode Zen",
            "display_name_zh": "OpenCode Zen 免费模型",
            "base_url": "https://opencode.ai/zen/v1",
            "models_endpoint": "https://opencode.ai/zen/v1/models",
            "free_models": [
                "big-pickle",
                "muse-spark-1.2-contributor-free",
                "mimo-v2.5-free",
            ],
            "excluded_models": [],
            "responses_models": ["muse-spark-1.2-contributor-free"],
            "compat_profile": "opencode_zen",
            "supports_tool_use": True,
        },
    }


def test_credential_reference_resolves_without_persisting_secret() -> None:
    entry = {
        "credential_ref": "connector:opencode-zen:api_key",
        "api_key": "",
    }
    credentials = _Credentials()

    assert resolve_model_provider_api_key(entry, credential_store=credentials) == "zen-secret"
    assert model_provider_entry_has_key(entry, credential_store=credentials) is True
    assert "zen-secret" not in repr(entry)


def test_validate_discovers_only_current_free_models(monkeypatch) -> None:
    class _Response:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "data": [
                    {"id": "big-pickle"},
                    {"id": "mimo-v2.5-free"},
                    {"id": "kimi-k3"},
                    {"id": "future-coder-free"},
                    {"id": "muse-spark-1.2-contributor-free"},
                ]
            }

    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: _Response())
    manager = ModelProviderPluginManager(
        custom_models={},
        lock=threading.RLock(),
        save=lambda *_ids: None,
        unregister_entry=lambda *_args, **_kwargs: False,
        rebuild_routes=lambda: {},
        credential_store=_Credentials(),
    )

    result = manager.validate(_item(), tokens={"api_key": "zen-secret"})

    assert result["models"] == [
        "big-pickle",
        "muse-spark-1.2-contributor-free",
        "mimo-v2.5-free",
        "future-coder-free",
    ]
    assert "kimi-k3" not in result["models"]


def test_community_provider_discovers_all_models_and_uses_custom_base_url(monkeypatch) -> None:
    requested: list[str] = []

    class _Response:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, Any]:
            return {"data": [{"id": "freebuff/model-a"}, {"id": "vendor/model-b"}]}

    def fake_get(url: str, **_kwargs: Any) -> _Response:
        requested.append(url)
        return _Response()

    monkeypatch.setattr(httpx, "get", fake_get)
    manager = ModelProviderPluginManager(
        custom_models={},
        lock=threading.RLock(),
        save=lambda *_ids: None,
        unregister_entry=lambda *_args, **_kwargs: False,
        rebuild_routes=lambda: {},
        credential_store=_Credentials(),
    )
    item = {
        "id": "freebuff2api-community",
        "name_zh": "Freebuff2API 社区适配器",
        "model_provider": {
            "entry_id": "freebuff2api-community",
            "display_name_zh": "Freebuff2API 社区适配器",
            "base_url": "https://open.freebuff.app/v1",
            "configurable_base_url": True,
            "discover_all_models": True,
            "free_models": [],
        },
    }

    result = manager.validate(item, tokens=None)

    assert requested == ["https://gateway.example/v1/models"]
    assert result["models"] == ["freebuff/model-a", "vendor/model-b"]
    assert result["base_url"] == "https://gateway.example/v1"


def test_configurable_provider_rejects_insecure_remote_http() -> None:
    manager = ModelProviderPluginManager(
        custom_models={},
        lock=threading.RLock(),
        save=lambda *_ids: None,
        unregister_entry=lambda *_args, **_kwargs: False,
        rebuild_routes=lambda: {},
        credential_store=_Credentials(),
    )
    item = {
        "id": "freebuff2api-community",
        "name_zh": "Freebuff2API 社区适配器",
        "model_provider": {
            "entry_id": "freebuff2api-community",
            "base_url": "https://open.freebuff.app/v1",
            "configurable_base_url": True,
        },
    }

    try:
        manager.validate(
            item,
            tokens={"api_key": "sk-fb-secret", "base_url": "http://remote.example/v1"},
        )
    except ValueError as exc:
        assert "必须使用 HTTPS" in str(exc)
    else:  # pragma: no cover - documents the security boundary
        raise AssertionError("insecure remote URL should be rejected")


def test_configure_and_remove_hot_model_routes() -> None:
    state: dict[str, dict[str, Any]] = {}
    saved: list[str] = []
    unregistered: list[str] = []

    def unregister(entry: dict[str, Any], *, fallback_id: str = "") -> bool:
        unregistered.append(str(entry.get("id") or fallback_id))
        return True

    def rebuild() -> dict[str, dict[str, Any]]:
        return {model_id: {"ok": True, "model_id": model_id} for model_id in state}

    manager = ModelProviderPluginManager(
        custom_models=state,
        lock=threading.RLock(),
        save=lambda *ids: saved.extend(ids),
        unregister_entry=unregister,
        rebuild_routes=rebuild,
        credential_store=_Credentials(),
    )

    configured = manager.configure(
        _item(),
        models=["big-pickle", "muse-spark-1.2-contributor-free"],
    )

    assert configured == {
        "configured": True,
        "entry_id": "opencode-zen",
        "models": ["big-pickle", "muse-spark-1.2-contributor-free"],
    }
    entry = state["opencode-zen"]
    assert entry["api_key"] == ""
    assert entry["credential_ref"] == "connector:opencode-zen:api_key"
    assert entry["supports_tool_use"] is True
    assert entry["responses_models"] == ["muse-spark-1.2-contributor-free"]
    assert "zen-secret" not in repr(entry)

    removed = manager.remove(_item())

    assert removed == {"removed": True, "entry_id": "opencode-zen"}
    assert state == {}
    assert unregistered == ["opencode-zen"]
    assert saved == ["opencode-zen", "opencode-zen"]


def test_plugin_connect_hot_registers_and_disconnect_removes_routes(
    tmp_path,
    monkeypatch,
) -> None:
    class _Response:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, Any]:
            return {"data": [{"id": "big-pickle"}, {"id": "mimo-v2.5-free"}]}

    class _Dispatcher:
        def __init__(self) -> None:
            self.routes: dict[str, Any] = {}

        def register(self, model_id: str, router: Any) -> None:
            self.routes[model_id] = router

        def unregister(self, model_id: str) -> bool:
            return self.routes.pop(model_id, None) is not None

    class _Planner:
        def __init__(self, router: Any) -> None:
            self.router = router

    class _Stack:
        def __init__(self, router: Any) -> None:
            self.planner = _Planner(router)

    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: _Response())
    credentials = CredentialStore(root=tmp_path / "credentials")
    connector_registry = ConnectorRegistry(
        marketplace_root=(
            Path(__file__).resolve().parents[1] / "extensions" / "workbuddy-connectors"
        ),
        skills_root=tmp_path / "skills",
        state_file=tmp_path / "connectors.json",
    )
    permission_store = CapabilityPermissionStore(tmp_path / "permission-grants.json")
    capability_registry = CapabilityRegistry(
        connector_registry=connector_registry,
        auth_orchestrator=AuthOrchestrator(credentials=credentials),
        codex_cache=tmp_path / "codex-plugins",
        capability_state_file=tmp_path / "capabilities.json",
        skills_root=tmp_path / "skills",
        permission_store=permission_store,
    )
    dispatcher = _Dispatcher()
    config = create_config_router(
        stack=_Stack(dispatcher),
        custom_models_path=tmp_path / "custom-models.json",
        credential_store=credentials,
    )
    app = FastAPI()
    app.include_router(config.router)
    identities = IdentityStore()
    identities.add(
        Identity(actor_id="oct:user@example.com", roles=("user", "oct")),
        api_key_plaintext="sk-user",
    )
    app.include_router(
        create_capability_router(
            registry=capability_registry,
            model_provider_plugins=config.model_provider_plugins,
            identity_store=identities,
            require_auth=True,
            allow_local_user_plugin_lifecycle=True,
        )
    )
    client = TestClient(app)
    client.headers.update({"Authorization": "Bearer sk-user"})

    installed = client.post("/api/capabilities/opencode-zen/install")
    assert installed.status_code == 200
    connected = client.post(
        "/api/capabilities/opencode-zen/connect",
        json={
            "tokens": {"api_key": "zen-secret"},
            "grant_permissions": ["account.credentials", "network.remote"],
        },
    )

    assert connected.status_code == 200
    assert connected.json()["model_provider"]["models"] == [
        "big-pickle",
        "mimo-v2.5-free",
    ]
    persisted = (tmp_path / "custom-models.json").read_text(encoding="utf-8")
    assert "zen-secret" not in persisted
    assert "connector:opencode-zen:api_key" in persisted
    assert "opencode-zen" in dispatcher.routes
    assert "big-pickle" in dispatcher.routes
    listed = client.get("/api/config/custom-models").json()["models"][0]
    assert listed["has_api_key"] is True
    assert "credential_ref" not in listed

    disabled = client.post("/api/capabilities/opencode-zen/disable")
    assert disabled.status_code == 200
    assert config.custom_models == {}

    enabled = client.post("/api/capabilities/opencode-zen/enable")
    assert enabled.status_code == 200
    assert config.custom_models["opencode-zen"]["models"] == [
        "big-pickle",
        "mimo-v2.5-free",
    ]

    disconnected = client.post("/api/capabilities/opencode-zen/disconnect")

    assert disconnected.status_code == 200
    assert config.custom_models == {}
    assert "opencode-zen" not in dispatcher.routes

