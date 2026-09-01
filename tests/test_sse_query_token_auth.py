"""SSE credentials stay out of URLs.

The frontend uses fetch + ReadableStream, which can carry the session cookie or
Authorization header.  Query-string tokens leak into browser history, access
logs, and referrers, so the shared principal resolver deliberately ignores
them.  Header/cookie authentication remains the supported transport.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from runtime.adapters.web_auth import _resolve_actor
from runtime.safety.auth import Identity, IdentityStore


def _store() -> IdentityStore:
    store = IdentityStore()
    store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    return store


def _req(headers: dict | None = None, query: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(headers=headers or {}, query_params=query or {})


def test_query_token_is_rejected_when_auth_is_required() -> None:
    with pytest.raises(HTTPException):
        _resolve_actor(_req(query={"token": "sk-alice"}), _store(), True)


def test_header_takes_precedence_over_query() -> None:
    actor = _resolve_actor(
        _req(headers={"Authorization": "Bearer sk-alice"}, query={"token": "garbage"}),
        _store(),
        True,
    )
    assert actor == "alice"


def test_missing_token_anywhere_raises_when_required() -> None:
    with pytest.raises(HTTPException):
        _resolve_actor(_req(), _store(), True)


def test_invalid_query_token_raises_when_required() -> None:
    with pytest.raises(HTTPException):
        _resolve_actor(_req(query={"token": "sk-nope"}), _store(), True)


def test_query_token_is_ignored_when_auth_is_optional() -> None:
    assert _resolve_actor(_req(query={"token": "sk-alice"}), _store(), False) is None
    assert _resolve_actor(_req(query={"token": "sk-nope"}), _store(), False) is None

