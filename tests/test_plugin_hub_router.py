from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway.plugin_hub_router import create_plugin_hub_router


class _FakeHub:
    def __init__(self):
        self.calls = []

    def list_plugins(self):
        return []

    def discover(self):
        return []

    def load(self, _name: str):
        return True

    def start(self, _name: str):
        return True

    def stop(self, _name: str):
        return True

    def unload(self, _name: str):
        return True

    def get_plugin_config(self, _name: str):
        return {}

    def update_plugin_config(self, _name: str, _body):
        return True

    def install_plugin(self, name: str, **kwargs):
        self.calls.append(("install", name, kwargs))
        return {"ok": True, "plugin_id": name, "installed": True, **kwargs}

    def enable_plugin(self, name: str):
        self.calls.append(("enable", name, {}))
        return {"ok": True, "plugin_id": name, "enabled": True}

    def disable_plugin(self, name: str):
        self.calls.append(("disable", name, {}))
        return {"ok": True, "plugin_id": name, "enabled": False}

    def uninstall_plugin(self, name: str, **kwargs):
        self.calls.append(("uninstall", name, kwargs))
        return {"ok": True, "plugin_id": name, "installed": False, **kwargs}


def test_plugin_hub_router_requires_auth_when_enabled() -> None:
    store = IdentityStore()
    store.add(Identity(actor_id="alice", roles=("operator",)), api_key_plaintext="sk-alice")
    app = FastAPI()
    app.include_router(
        create_plugin_hub_router(
            hub=_FakeHub(),
            identity_store=store,
            require_auth=True,
        )
    )
    client = TestClient(app)

    assert client.get("/api/plugin-hub/plugins").status_code == 401
    assert (
        client.get(
            "/api/plugin-hub/plugins",
            headers={"Authorization": "Bearer sk-alice"},
        ).status_code
        == 200
    )


def test_plugin_hub_mutation_requires_operator_role() -> None:
    store = IdentityStore()
    store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    app = FastAPI()
    app.include_router(
        create_plugin_hub_router(
            hub=_FakeHub(),
            identity_store=store,
            require_auth=True,
        )
    )
    client = TestClient(app)

    response = client.post(
        "/api/plugin-hub/plugins/demo/load",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert response.status_code == 403


def test_persistent_lifecycle_route_contract() -> None:
    hub = _FakeHub()
    app = FastAPI()
    app.include_router(create_plugin_hub_router(hub=hub))
    client = TestClient(app)

    installed = client.post(
        "/api/plugin-hub/plugins/narrative_studio/install",
        json={"enabled": False, "restore_data": True, "recovery_id": "recovery-1"},
    )
    assert installed.status_code == 200
    assert hub.calls[-1] == (
        "install",
        "narrative_studio",
        {"enabled": False, "restore_data": True, "recovery_id": "recovery-1"},
    )

    assert client.post("/api/plugin-hub/plugins/narrative_studio/enable").status_code == 200
    assert client.post("/api/plugin-hub/plugins/narrative_studio/disable").status_code == 200

    removed = client.delete(
        "/api/plugin-hub/plugins/narrative_studio/install",
        params={"data_policy": "trash", "confirm_data_move": "true"},
    )
    assert removed.status_code == 200
    assert hub.calls[-1] == (
        "uninstall",
        "narrative_studio",
        {"data_policy": "trash", "confirm_data_move": True},
    )

