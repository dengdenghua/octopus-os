"""Shared-deployment authorization contract for process-global control planes."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from runtime.platform.ui.app import create_app
from runtime.safety.auth import Identity, IdentityStore


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def control_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, dict[str, dict[str, str]], Path]]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECHO_HOME", str(tmp_path / "echo-home"))
    monkeypatch.setenv("ECHO_FF_UI_PROMPTS_HOT_RELOAD", "1")

    from runtime.platform import feature_flags

    feature_flags.reload()

    # Keep privileged success-path probes hermetic: the matrix verifies that
    # an administrator reaches the handler, not Docker/network/model side effects.
    monkeypatch.setattr(
        "runtime.platform.assets.asset_registry.sync_assets",
        lambda: {"ok": True, "count": 0},
    )
    monkeypatch.setattr(
        "runtime.sensing.model_router.hwfit.start_pull",
        lambda tag: {"ok": True, "tag": tag},
    )
    monkeypatch.setattr(
        "runtime.sensing.gateway.storage_supervisor.maybe_start_storage",
        lambda **_kwargs: {"status": "started"},
    )
    monkeypatch.setattr(
        "runtime.execution.suckers.storage_skills.storage_alive",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        "runtime.safety.organization.team_role_models.save_overrides",
        lambda value: value,
    )
    monkeypatch.setattr(
        "runtime.sensing.gateway.enterprise_assets_router._enterprise_get",
        lambda *_args, **_kwargs: {"available": False, "error": "not configured"},
    )
    monkeypatch.setattr(
        "echo_runtime.sync_skills",
        lambda *_args, **_kwargs: ([], [], [("missing", "not found")]),
    )

    identities = IdentityStore()
    identities.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    identities.add(
        Identity(actor_id="admin", roles=("admin",)),
        api_key_plaintext="sk-admin",
    )
    identities.add(
        Identity(actor_id="operator", roles=("operator",)),
        api_key_plaintext="sk-operator",
    )
    app = create_app(
        cocoloop_require_auth=True,
        cocoloop_identity_store=identities,
    )
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield (
            client,
            {
                "user": _headers("sk-alice"),
                "admin": _headers("sk-admin"),
                "operator": _headers("sk-operator"),
            },
            tmp_path,
        )
    finally:
        client.close()
        monkeypatch.delenv("ECHO_FF_UI_PROMPTS_HOT_RELOAD", raising=False)
        feature_flags.reload()


def test_global_control_plane_matrix_rejects_ordinary_users(
    control_client: tuple[TestClient, dict[str, dict[str, str]], Path],
) -> None:
    client, headers, tmp_path = control_client
    requests: list[tuple[str, str, dict[str, Any]]] = [
        ("PUT", "/api/config/identity-lock", {"json": {"locked": "invalid"}}),
        ("PUT", "/api/config/custom-models/demo", {"json": {}}),
        ("POST", "/api/config/custom-models/test", {"json": {}}),
        (
            "GET",
            "/api/config/local-models/scan",
            {"params": {"targets": "file:///etc/passwd"}},
        ),
        ("POST", "/api/config/local-models/import", {"json": {}}),
        ("POST", "/api/feature-flags/reload", {}),
        ("POST", "/api/ai-mode", {"json": {"mode": "invalid"}}),
        ("POST", "/api/connectors/missing/install", {}),
        ("GET", "/api/connectors/missing/headers", {}),
        ("POST", "/api/capabilities/missing/install", {}),
        ("GET", "/api/capabilities/missing/headers", {}),
        (
            "POST",
            "/api/permissions/rules",
            {"json": {"effect": "deny", "tool": "shell", "reason": "test"}},
        ),
        ("POST", "/api/skills/market/publish", {"json": {}}),
        ("POST", "/api/plugins/registry/install", {"json": {}}),
        ("POST", "/api/prompts/reload", {}),
        ("POST", "/api/registry/skills/missing/install", {}),
        ("POST", "/api/assets/sync", {}),
        ("POST", "/api/cookbook/pull", {"json": {"tag": "demo"}}),
        ("PUT", "/api/team/role-models", {"json": {"overrides": {}}}),
        (
            "GET",
            "/api/debug/session-info",
            {"params": {"workspace_path": "/etc", "thread_id": "other-user"}},
        ),
        ("GET", "/api/research/deep/jobs", {}),
        ("GET", "/api/android/devices", {}),
        (
            "GET",
            "/api/agent-modes/detect",
            {"params": {"workspace_path": str(tmp_path)}},
        ),
        (
            "GET",
            "/api/ambient-suggestions",
            {"params": {"project": str(tmp_path)}},
        ),
        ("GET", "/api/teach-repeat/templates", {}),
        ("POST", "/api/local-brain/storage/start", {}),
        ("POST", "/api/invariants/refresh", {}),
        ("GET", "/api/index/status", {}),
        ("GET", "/api/wiki/graph", {}),
        ("GET", "/api/intelligence/subscriptions", {}),
        ("POST", "/api/agent-market/enterprise/missing/install", {}),
        ("POST", "/api/organizations/topology-proposals/999/promote", {}),
        ("GET", "/api/journal", {}),
        (
            "POST",
            "/api/journal/reindex",
            {"json": {"jsonl_path": "/etc/passwd"}},
        ),
    ]

    for method, path, kwargs in requests:
        denied = client.request(method, path, headers=headers["user"], **kwargs)
        assert denied.status_code == 403, (path, denied.status_code, denied.text)

        # Admins must cross the role boundary and reach ordinary business
        # validation/404/success. A mistaken global auth gate shows up here as
        # another 401/403 even when the user-side assertion passes.
        elevated = client.request(method, path, headers=headers["admin"], **kwargs)
        assert elevated.status_code not in {401, 403}, (
            path,
            elevated.status_code,
            elevated.text,
        )


def test_android_websocket_rejects_authenticated_non_operator(
    control_client: tuple[TestClient, dict[str, dict[str, str]], Path],
) -> None:
    client, _headers_by_role, _tmp_path = control_client

    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            "/api/android/ws/device-a",
            headers={"Authorization": "Bearer sk-alice"},
        ),
    ):
        pass

    assert exc_info.value.code == 4403


def test_org_create_ignores_forged_owner_in_shared_mode(
    control_client: tuple[TestClient, dict[str, dict[str, str]], Path],
) -> None:
    client, headers, _tmp_path = control_client

    response = client.post(
        "/api/orgs",
        headers=headers["user"],
        json={"name": "Alice Org", "owner_id": "bob"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["owner_id"] == "alice"


def test_control_plane_stays_open_in_explicit_local_dev_mode(tmp_path: Path) -> None:
    from fastapi import FastAPI

    from runtime.sensing.gateway.agent_modes_router import create_agent_modes_router
    from runtime.sensing.gateway.ambient_suggestions_router import (
        create_ambient_suggestions_router,
    )
    from runtime.sensing.gateway.teach_repeat_router import create_teach_repeat_router

    app = FastAPI()
    app.include_router(create_agent_modes_router())
    app.include_router(create_ambient_suggestions_router(base_dir=tmp_path / "ambient"))
    app.include_router(create_teach_repeat_router())
    client = TestClient(app)

    assert (
        client.get(
            "/api/agent-modes/detect",
            params={"workspace_path": str(tmp_path)},
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/ambient-suggestions",
            params={"project": str(tmp_path)},
        ).status_code
        == 200
    )
    assert client.get("/api/teach-repeat/templates").status_code == 200
