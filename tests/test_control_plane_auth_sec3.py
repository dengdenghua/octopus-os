"""SEC-3 regression: /api/reflex and /api/gene-locks are auth-gated control plane.

The reflex-admin router mounts `/api/reflex/*` (incl. auto-pr / reload) and
`/api/gene-locks/*` (incl. panic / mode) with no per-route auth. They were
missing from `_LEGACY_CONTROL_PLANE_PREFIXES`, so the legacy control-plane auth
middleware did not gate them even when `require_auth=True`. They're now in the
tuple, so the middleware requires a valid actor.
"""

from __future__ import annotations

import pytest

from runtime.platform.ui.app import (
    _LEGACY_CONTROL_PLANE_PREFIXES,
    _path_matches_prefix,
)


def _gated(path: str) -> bool:
    return any(_path_matches_prefix(path, p) for p in _LEGACY_CONTROL_PLANE_PREFIXES)


@pytest.mark.parametrize(
    "path",
    [
        "/api/reflex/stats",
        "/api/reflex/auto-pr",
        "/api/reflex/reload",
        "/api/gene-locks/status",
        "/api/gene-locks/panic",
        "/api/gene-locks/mode",
    ],
)
def test_reflex_and_gene_locks_paths_are_in_control_plane(path: str) -> None:
    assert _gated(path), f"{path} is not gated by the control-plane auth middleware"


def test_middleware_401s_unauthenticated_reflex_when_auth_on() -> None:
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from runtime.platform.ui.app import _install_legacy_control_plane_auth
    from runtime.safety.auth import Identity, IdentityStore

    store = IdentityStore()
    store.add(Identity(actor_id="boss"), api_key_plaintext="sk-boss")

    app = fastapi.FastAPI()

    @app.post("/api/reflex/auto-pr")
    def _auto_pr() -> dict:
        return {"ok": True}

    @app.post("/api/gene-locks/panic")
    def _panic() -> dict:
        return {"ok": True}

    _install_legacy_control_plane_auth(
        app,
        identity_store=store,
        require_auth=True,
        jwt_secret=None,
        jwt_issuer=None,
        jwt_audience=None,
    )
    client = TestClient(app)

    # No credentials -> blocked by the control-plane middleware.
    assert client.post("/api/reflex/auto-pr").status_code == 401
    assert client.post("/api/gene-locks/panic").status_code == 401
    # Valid credentials -> middleware lets it through to the handler.
    ok = client.post("/api/reflex/auto-pr", headers={"Authorization": "Bearer sk-boss"})
    assert ok.status_code == 200

