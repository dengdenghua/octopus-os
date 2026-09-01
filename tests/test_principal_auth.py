"""Regression tests for the shared Principal and operator boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from runtime.safety.auth import Identity, IdentityStore  # noqa: E402
from runtime.safety.auth.principal import (  # noqa: E402
    require_operator,
    resolve_principal,
)


def _request(
    token: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        headers=(
            headers
            if headers is not None
            else ({"Authorization": f"Bearer {token}"} if token is not None else {})
        ),
        cookies=cookies or {},
        query_params={},
        state=SimpleNamespace(),
    )


def _store() -> IdentityStore:
    store = IdentityStore()
    store.add(
        Identity(
            actor_id="alice",
            roles=("operator",),
            metadata={"tenant_id": "tenant-a", "scopes": ["mcp:write"]},
        ),
        api_key_plaintext="sk-alice",
    )
    store.add(
        Identity(actor_id="bob", roles=(), metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-bob",
    )
    return store


def test_principal_uses_verified_identity_metadata() -> None:
    request = _request("sk-alice")
    principal = resolve_principal(request, _store(), True)

    assert principal is not None
    assert principal.actor_id == "alice"
    assert principal.tenant_id == "tenant-a"
    assert principal.roles == frozenset({"operator"})
    assert principal.scopes == frozenset({"mcp:write"})
    assert request.state.principal == principal


def test_principal_accepts_websocket_bearer_subprotocol() -> None:
    request = _request(headers={"sec-websocket-protocol": "bearer, sk-alice"})

    principal = resolve_principal(request, _store(), True)

    assert principal is not None
    assert principal.actor_id == "alice"


def test_principal_accepts_http_only_browser_session_cookie() -> None:
    request = _request(cookies={"echo_session": "sk-alice"})

    principal = resolve_principal(request, _store(), True)

    assert principal is not None
    assert principal.actor_id == "alice"
    assert principal.authn_method == "api_key"


def test_regular_user_cannot_cross_operator_boundary() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        require_operator(_request("sk-bob"), _store(), True)
    assert exc_info.value.status_code == 403


def test_auth_without_identity_store_fails_closed() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        resolve_principal(_request(), None, True)
    assert exc_info.value.status_code == 401
    assert exc_info.value.headers == {"X-Echo-Auth-Expired": "1"}

