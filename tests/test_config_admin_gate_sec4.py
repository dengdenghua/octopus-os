"""SEC-4 regression: mutating the path-denylist requires the admin role.

`config_router` only had a router-level authentication gate (`_auth_dep`), no
role check — so any authenticated non-admin user could `POST`/`DELETE`
`/api/path-denylist`, an override of a security control. The mutating
path-denylist endpoints now carry `_require_admin` (mirroring system_router);
dev mode (`require_auth=False`) stays a no-op.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from runtime.safety.auth import Identity, IdentityStore  # noqa: E402
from runtime.sensing.gateway.config_router import create_config_router  # noqa: E402


def _client(*, require_auth: bool) -> TestClient:
    store = IdentityStore()
    store.add(Identity(actor_id="boss", roles=("admin",)), api_key_plaintext="sk-admin")
    store.add(Identity(actor_id="user", roles=()), api_key_plaintext="sk-user")
    cr = create_config_router(require_auth=require_auth, identity_store=store)
    app = FastAPI()
    app.include_router(cr.router)
    return TestClient(app)


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_non_admin_cannot_mutate_path_denylist() -> None:
    client = _client(require_auth=True)
    r = client.post("/api/path-denylist", json={"path": "/tmp/x"}, headers=_bearer("sk-user"))
    assert r.status_code == 403


def test_admin_can_mutate_path_denylist(monkeypatch) -> None:
    monkeypatch.setattr(
        "runtime.safety.auth.path_denylist.add_user_denylist_entry",
        lambda p: [p],
    )
    client = _client(require_auth=True)
    r = client.post("/api/path-denylist", json={"path": "/tmp/x"}, headers=_bearer("sk-admin"))
    assert r.status_code == 200


def test_delete_path_denylist_also_admin_gated() -> None:
    client = _client(require_auth=True)
    r = client.request(
        "DELETE", "/api/path-denylist", json={"path": "/tmp/x"}, headers=_bearer("sk-user")
    )
    assert r.status_code == 403


def test_dev_mode_does_not_require_admin(monkeypatch) -> None:
    monkeypatch.setattr(
        "runtime.safety.auth.path_denylist.add_user_denylist_entry",
        lambda p: [p],
    )
    client = _client(require_auth=False)
    r = client.post("/api/path-denylist", json={"path": "/tmp/x"})
    assert r.status_code == 200

