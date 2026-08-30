"""Security regression tests for two authz gates added after the full-stack audit.

S1 · observability router (``/api/journal``, ``/api/stream``,
     ``/api/files/stream`` …) previously had NO auth wiring at all — connecting
     replayed the whole journal (file diffs, absolute paths, task history) to
     any anonymous client (verified live: ``/api/files/stream`` streamed 44 KB
     of historical file_op events with absolute home/Windows paths). It now
     honours ``require_auth`` via a router-level dependency, mirroring
     ``create_browser_router``.

S3 · anthropic_compat per-session routes (``GET/POST /v1/sessions/{id}…``)
     authenticated the caller but never checked session ownership, so any
     authenticated actor could read or drive another actor's session by
     guessing its id. ``_owned_or_404`` now enforces ``creator_actor`` — and
     returns 404 (not 403) so a non-owner can't even confirm a session exists.

Both gates are no-ops when ``require_auth=False`` (single-user dev), so local
preview + the EventSource-based Observability panel are unchanged — pinned by
the ``*_dev_mode`` / ``*_open_in_dev`` tests.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI, WebSocketDisconnect  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from runtime.memory.threads import ThreadStateStore  # noqa: E402
from runtime.platform.config import AgentConfig, PlannerConfig, build_from_config  # noqa: E402
from runtime.platform.ui.app import create_app  # noqa: E402
from runtime.safety.auth.identity import (  # noqa: E402
    Identity,
    IdentityStore,
    encode_jwt_hs256,
    verify_jwt_hs256,
)
from runtime.sensing.gateway.anthropic_compat import create_anthropic_compat_router  # noqa: E402
from runtime.sensing.gateway.workspaces_router import create_workspaces_router  # noqa: E402

_BETA = {"anthropic-beta": "managed-agents-2026-04-01"}
_JWT_SECRET = "0123456789abcdef0123456789ABCDEF!"
_JWT_ISSUER = "good-idp"
_JWT_AUDIENCE = "echo-ui"
_SECURED_APP_ROUTE_SPECS = (
    ("GET", "/api/account/usage", {}),
    ("GET", "/api/agent-modes", {}),
    ("GET", "/api/teams", {}),
    ("GET", "/api/team-tasks", {}),
    ("GET", "/api/agents/parallel/status", {}),
    ("GET", "/api/dag/active", {}),
    ("GET", "/api/research/deep/jobs", {}),
    ("GET", "/api/subagents", {}),
    ("GET", "/api/tentacle/join-info", {}),
    ("POST", "/api/terminal/kill/s1", {}),
    ("GET", "/api/fs/roots", {}),
    ("GET", "/api/threads/search", {}),
    ("GET", "/api/threads/th-upload/uploads/list", {}),
    ("GET", "/api/wiki/status", {}),
    ("GET", "/api/local-brain/status", {}),
    ("GET", "/api/retrieve/backend", {}),
    ("POST", "/api/retrieve/rank", {"json": {"query": "x", "candidates": ["a"]}}),
    ("GET", "/api/index/status", {}),
    ("POST", "/api/lsp/diagnostics", {"json": {"path": ".", "workspace": "."}}),
    ("GET", "/api/ambient-suggestions", {"params": {"project": "."}}),
    (
        "POST",
        "/api/complete",
        {"json": {"prefix": "def x():\n    ", "suffix": "", "language": "python"}},
    ),
    ("POST", "/api/verify/detect", {"json": {"workspace": "."}}),
    ("GET", "/api/deployments", {}),
    ("GET", "/api/invariants", {}),
    ("GET", "/api/intelligence/subscriptions", {}),
    ("GET", "/api/agent-market/enterprise", {}),
    ("POST", "/api/teach-repeat/record/start", {"json": {"thread_id": "th-rec", "name": "demo"}}),
    ("GET", "/v1/models", {}),
    ("GET", "/api/journal", {}),
    ("GET", "/api/journal/stats", {}),
    ("POST", "/api/journal/reindex", {}),
)


def _store_with_actors() -> tuple[IdentityStore, dict[str, str]]:
    store = IdentityStore()
    keys: dict[str, str] = {}
    for actor in ("alice", "bob"):
        key = f"sk-test-{actor}"
        store.add(Identity(actor_id=actor), api_key_plaintext=key)
        keys[actor] = key
    return store, keys


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _jwt_headers(
    *,
    sub: str = "alice",
    iss: str = _JWT_ISSUER,
    aud: str = _JWT_AUDIENCE,
) -> dict[str, str]:
    token = encode_jwt_hs256(
        {
            "sub": sub,
            "exp": int(time.time()) + 60,
            "iss": iss,
            "aud": aud,
        },
        secret=_JWT_SECRET,
    )
    return {"Authorization": f"Bearer {token}"}


def _jwt_secured_app_client(
    obs_stack: object,
    *,
    require_auth: bool,
) -> TestClient:
    from runtime.adapters.integrations.local_auth.config import LocalAuthConfig

    store = IdentityStore()
    store.add(Identity(actor_id="alice", roles=("admin",)))
    app = create_app(
        journal=obs_stack.journal,  # type: ignore[attr-defined]
        registry=obs_stack.registry,  # type: ignore[attr-defined]
        stack=obs_stack,
        cocoloop_require_auth=require_auth,
        cocoloop_identity_store=store,
        local_auth_config=LocalAuthConfig(
            enabled=True,
            allow_any_username=True,
            jwt_secret=_JWT_SECRET,
            jwt_issuer=_JWT_ISSUER,
            jwt_audience=_JWT_AUDIENCE,
        ),
    )
    return TestClient(app)


# ── S1 · observability auth gate ─────────────────────────────────────


@pytest.fixture
def obs_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    monkeypatch.chdir(tmp_path)
    cfg = AgentConfig(
        planner=PlannerConfig(
            type="llm",
            model="mock/ob",
            mock_response='{"reasoning":"r","nodes":[]}',
        ),
    )
    yield build_from_config(cfg)


# These go through the real ``create_app`` factory so they exercise the exact
# app.py call-site wiring the fix added (create_observability_router now
# receives identity_store + require_auth). The dev-mode test proving 200 also
# rules out a global middleware confound: the 401 below can only come from the
# router-level gate that the fix made conditional on require_auth.


def test_observability_requires_auth_when_enabled(obs_stack: object) -> None:
    store, keys = _store_with_actors()
    app = create_app(
        journal=obs_stack.journal,  # type: ignore[attr-defined]
        registry=obs_stack.registry,  # type: ignore[attr-defined]
        stack=obs_stack,
        cocoloop_require_auth=True,
        cocoloop_identity_store=store,
    )
    client = TestClient(app)

    # Anonymous caller is rejected before the handler runs (was: 200 + the
    # whole journal). Pins the leak closed under auth-on.
    assert client.get("/api/journal").status_code == 401

    # Authenticated caller passes the gate (200 or any non-401 handler result).
    assert client.get("/api/journal", headers=_bearer(keys["alice"])).status_code != 401


def test_observability_open_in_dev_mode(obs_stack: object) -> None:
    app = create_app(
        journal=obs_stack.journal,  # type: ignore[attr-defined]
        registry=obs_stack.registry,  # type: ignore[attr-defined]
        stack=obs_stack,
    )
    client = TestClient(app)

    # No token, dev mode (require_auth defaults False) → router-level gate is a
    # no-op, endpoint reachable. This is the property that keeps the frontend
    # EventSource panel working, and rules out a global-auth confound.
    assert client.get("/api/journal").status_code == 200


@pytest.mark.parametrize(
    ("label", "bad_headers"),
    [
        ("issuer", _jwt_headers(iss="evil-idp")),
        ("audience", _jwt_headers(aud="wrong-ui")),
    ],
)
def test_app_wiring_enforces_jwt_claims_across_secured_routers(
    obs_stack: object,
    label: str,
    bad_headers: dict[str, str],
) -> None:
    client = _jwt_secured_app_client(obs_stack, require_auth=True)
    good_headers = _jwt_headers()

    for method, path, kwargs in _SECURED_APP_ROUTE_SPECS:
        ok = client.request(method, path, headers=good_headers, **kwargs)
        denied = client.request(method, path, headers=bad_headers, **kwargs)
        assert ok.status_code != 401, f"{path} should accept a valid jwt"
        assert denied.status_code == 401, f"{path} should reject jwt with mismatched {label}"


@pytest.mark.parametrize(
    ("label", "bad_headers"),
    [
        ("issuer", _jwt_headers(iss="evil-idp")),
        ("audience", _jwt_headers(aud="wrong-ui")),
    ],
)
def test_meta_install_requires_matching_jwt_claims_even_without_global_auth(
    obs_stack: object,
    label: str,
    bad_headers: dict[str, str],
) -> None:
    client = _jwt_secured_app_client(obs_stack, require_auth=False)
    good_headers = _jwt_headers()

    ok = client.post("/api/skills/install", json={}, headers=good_headers)
    denied = client.post("/api/skills/install", json={}, headers=bad_headers)

    assert ok.status_code == 400
    assert denied.status_code == 401, (
        f"/api/skills/install should reject jwt with mismatched {label}"
    )


@pytest.mark.parametrize(
    ("label", "bad_headers"),
    [
        ("issuer", _jwt_headers(iss="evil-idp")),
        ("audience", _jwt_headers(aud="wrong-ui")),
    ],
)
def test_stub_auth_me_only_resolves_identity_for_matching_jwt_claims(
    obs_stack: object,
    label: str,
    bad_headers: dict[str, str],
) -> None:
    client = _jwt_secured_app_client(obs_stack, require_auth=False)

    ok = client.get("/api/auth/me", headers=_jwt_headers())
    denied = client.get("/api/auth/me", headers=bad_headers)

    assert ok.status_code == 200
    assert ok.json()["user_id"] == "alice"
    assert denied.status_code == 200
    assert denied.json()["user_id"] == "anonymous", (
        f"/api/auth/me should ignore jwt with mismatched {label}"
    )


def test_workspace_routes_require_auth_and_respect_thread_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    store, keys = _store_with_actors()
    thread_store = ThreadStateStore()
    thread_store.ensure_thread(
        "owned-thread",
        metadata={"owner_actor_id": "alice"},
        values={"title": "Workspace owner"},
    )
    app = FastAPI()
    app.include_router(
        create_workspaces_router(
            workspace_root=tmp_path / "workspaces",
            thread_store=thread_store,
            identity_store=store,
            require_auth=True,
        )
    )
    client = TestClient(app)

    unauth = client.get("/api/workspaces/owned-thread")
    owner = client.get(
        "/api/workspaces/owned-thread",
        headers=_bearer(keys["alice"]),
    )
    other = client.get(
        "/api/workspaces/owned-thread",
        headers=_bearer(keys["bob"]),
    )

    assert unauth.status_code == 401
    assert owner.status_code == 200
    assert other.status_code == 404


def test_upload_routes_require_auth_and_respect_thread_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    store, keys = _store_with_actors()
    cfg = AgentConfig(
        planner=PlannerConfig(
            type="llm",
            model="mock/up-auth",
            mock_response='{"reasoning":"r","nodes":[]}',
        ),
    )
    stack = build_from_config(cfg)
    app = create_app(
        journal=stack.journal,
        registry=stack.registry,
        stack=stack,
        cocoloop_require_auth=True,
        cocoloop_identity_store=store,
    )
    app.state.thread_store.ensure_thread(
        "th-upload",
        metadata={"owner_actor_id": "alice"},
    )
    client = TestClient(app)

    assert (
        client.get(
            "/api/threads/th-upload/uploads/list",
            headers=_bearer(keys["alice"]),
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/threads/th-upload/uploads/list",
            headers=_bearer(keys["bob"]),
        ).status_code
        == 404
    )
    assert client.get("/api/threads/th-upload/uploads/list").status_code == 401


def test_legacy_control_plane_requires_auth_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    store, keys = _store_with_actors()
    app = create_app(
        cocoloop_require_auth=True,
        cocoloop_identity_store=store,
    )
    client = TestClient(app)

    for path in (
        "/api/apps",
        "/api/memory",
        "/api/mcp/config",
        "/api/meta-skills",
        "/api/permissions",
        "/api/plugins",
        "/api/plugin-hub/plugins",
        "/api/prompts",
        "/api/remote-backends",
        "/api/team/role-models",
        "/api/computer/status",
        "/api/skills/market/installed",
        "/api/agent-market/store",
        "/api/android/devices",
    ):
        assert client.get(path).status_code == 401
        assert client.get(path, headers=_bearer(keys["alice"])).status_code != 401


def test_legacy_control_plane_open_in_dev_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(create_app())

    assert client.get("/api/apps").status_code == 200
    assert client.get("/api/memory").status_code == 200
    assert client.get("/api/meta-skills").status_code == 200
    assert client.get("/api/plugins").status_code == 200
    assert client.get("/api/prompts").status_code == 200
    assert client.get("/api/remote-backends").status_code == 200
    assert client.get("/api/team/role-models").status_code == 200
    assert client.get("/api/computer/status").status_code == 200
    assert client.get("/api/skills/market/installed").status_code == 200
    assert client.get("/api/agent-market/store").status_code == 200


def test_local_auth_jwt_reaches_control_and_auth_aware_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.adapters.integrations.local_auth.config import LocalAuthConfig

    monkeypatch.chdir(tmp_path)
    app = create_app(
        cocoloop_require_auth=True,
        local_auth_config=LocalAuthConfig(
            enabled=True,
            allow_any_username=True,
            jwt_secret="0123456789abcdef0123456789ABCDEF!",
        ),
    )
    client = TestClient(app)

    login = client.post("/api/auth/local/login", json={"username": "alice"})
    assert login.status_code == 200
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/memory", headers=headers).status_code != 401
    assert client.get("/api/agents", headers=headers).status_code != 401


def test_local_auth_dev_mode_rejects_legacy_guest_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.adapters.integrations.local_auth.config import LocalAuthConfig

    monkeypatch.chdir(tmp_path)
    app = create_app(
        cocoloop_require_auth=True,
        local_auth_config=LocalAuthConfig(
            enabled=True,
            allow_any_username=True,
            jwt_secret="0123456789abcdef0123456789ABCDEF!",
        ),
    )
    client = TestClient(app)

    headers = {"Authorization": "Bearer __guest__"}
    assert client.get("/api/memory", headers=headers).status_code == 401
    assert client.get("/api/agents", headers=headers).status_code == 401


def test_local_auth_jwt_audience_is_issued_and_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.adapters.integrations.local_auth.config import LocalAuthConfig

    monkeypatch.chdir(tmp_path)
    app = create_app(
        cocoloop_require_auth=True,
        local_auth_config=LocalAuthConfig(
            enabled=True,
            allow_any_username=True,
            jwt_secret=_JWT_SECRET,
            jwt_issuer="local-idp",
            jwt_audience="echo-local-ui",
        ),
    )
    client = TestClient(app)

    login = client.post("/api/auth/local/login", json={"username": "alice"})
    assert login.status_code == 200
    token = login.json()["access_token"]
    claims = verify_jwt_hs256(
        token,
        secret=_JWT_SECRET,
        required_issuer="local-idp",
        required_audience="echo-local-ui",
    )
    assert claims["sub"] == "local:alice"

    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/auth/local/whoami", headers=headers).status_code == 200
    assert client.get("/api/memory", headers=headers).status_code != 401

    wrong_audience = encode_jwt_hs256(
        {
            "sub": "local:alice",
            "exp": int(time.time()) + 60,
            "iss": "local-idp",
            "aud": "wrong-ui",
        },
        secret=_JWT_SECRET,
    )
    denied_headers = {"Authorization": f"Bearer {wrong_audience}"}
    assert client.get("/api/auth/local/whoami", headers=denied_headers).status_code == 401
    assert client.get("/api/memory", headers=denied_headers).status_code == 401


def test_android_device_ws_requires_token_when_auth_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The device-registration WebSocket is gated like the HTTP routes.

    The control-plane auth middleware only sees HTTP scope, so the
    ``/api/android/ws/{id}`` handshake is gated inside the router itself.
    It must refuse an unauthenticated handshake when auth is on and accept
    a valid token.
    """
    monkeypatch.chdir(tmp_path)
    store, keys = _store_with_actors()
    store.add(
        Identity(actor_id="operator", roles=("operator",)),
        api_key_plaintext="sk-test-operator",
    )
    keys["operator"] = "sk-test-operator"
    app = create_app(cocoloop_require_auth=True, cocoloop_identity_store=store)
    client = TestClient(app)

    # No credentials → handshake refused (closed 4401 before accept).
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/api/android/ws/dev1") as ws,
    ):
        ws.receive_text()

    # A valid ordinary-user token is authenticated but lacks device-control
    # authority, so the handshake closes with the distinct role code.
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect(
            "/api/android/ws/dev1",
            headers=_bearer(keys["alice"]),
        ),
    ):
        pass
    assert exc_info.value.code == 4403

    # Valid operator token → handshake accepted.
    with client.websocket_connect(
        "/api/android/ws/dev1",
        headers=_bearer(keys["operator"]),
    ) as ws:
        ws.send_json({"model": "Pixel", "android_version": "14"})


def test_android_device_ws_open_in_dev_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(create_app())
    with client.websocket_connect("/api/android/ws/dev2") as ws:
        ws.send_json({"model": "Pixel"})


# ── S3 · anthropic_compat session ownership ──────────────────────────


def _session_client(store: IdentityStore) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_anthropic_compat_router(
            stack=None,
            identity_store=store,
            require_auth=True,
        )
    )
    return TestClient(app)


def test_session_ownership_blocks_other_actor() -> None:
    store, keys = _store_with_actors()
    client = _session_client(store)

    created = client.post(
        "/v1/sessions",
        json={"title": "alice's session"},
        headers={**_BETA, **_bearer(keys["alice"])},
    )
    assert created.status_code == 200, created.text
    sid = created.json()["id"]

    # bob is authenticated but is NOT the creator → 404 on every per-session
    # route (read, list-events, send-events, stream). 404 not 403 so bob can't
    # confirm the session exists.
    bob = {**_BETA, **_bearer(keys["bob"])}
    assert client.get(f"/v1/sessions/{sid}", headers=bob).status_code == 404
    assert client.get(f"/v1/sessions/{sid}/events", headers=bob).status_code == 404
    assert (
        client.post(
            f"/v1/sessions/{sid}/events",
            json={"events": []},
            headers=bob,
        ).status_code
        == 404
    )
    assert client.get(f"/v1/sessions/{sid}/events/stream", headers=bob).status_code == 404

    # alice (the creator) still has full access.
    assert (
        client.get(f"/v1/sessions/{sid}", headers={**_BETA, **_bearer(keys["alice"])}).status_code
        == 200
    )


def test_session_unauthenticated_rejected() -> None:
    store, _keys = _store_with_actors()
    client = _session_client(store)

    # Beta header present, no bearer token, require_auth=True → 401 (the beta
    # check passes, then actor resolution fails).
    assert client.get("/v1/sessions/sesn_whatever", headers=_BETA).status_code == 401

