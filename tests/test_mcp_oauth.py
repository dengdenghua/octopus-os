"""MCP OAuth (PKCE) core: PKCE, authorize URL, token store, refresh.

Hermetic — ECHO_HOME points at tmp_path and the token endpoint is mocked, so
nothing touches the real ~/.echo or the network.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from runtime.adapters.mcp_client import oauth


def _fake_urlopen(payload: bytes):
    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_a: object) -> bool:
            return False

        def read(self) -> bytes:
            return payload

    def _open(_req: object, timeout: float | None = None) -> _Resp:
        return _Resp()

    return _open


def test_pkce_is_valid_s256() -> None:
    verifier, challenge = oauth.new_pkce()
    assert len(verifier) >= 43
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert challenge == expected


def test_build_authorize_url_has_required_params() -> None:
    url = oauth.build_authorize_url(
        authorize_url="https://p/auth",
        client_id="cid",
        redirect_uri="http://cb",
        scopes=["a", "b"],
        state="ST",
        code_challenge="CH",
    )
    for fragment in (
        "client_id=cid",
        "state=ST",
        "code_challenge=CH",
        "code_challenge_method=S256",
        "scope=a+b",
        "response_type=code",
    ):
        assert fragment in url


def test_pending_is_single_use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    oauth.reset_oauth_store_for_tests()
    store = oauth.get_oauth_store()
    state = store.start_pending(
        server="cf",
        code_verifier="v",
        redirect_uri="http://cb",
        token_url="https://p/token",
        client_id="cid",
    )
    first = store.pop_pending(state)
    assert first is not None and first.server == "cf"
    assert store.pop_pending(state) is None  # consumed → CSRF-safe
    oauth.reset_oauth_store_for_tests()


def test_save_and_bearer_and_forget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    oauth.reset_oauth_store_for_tests()
    store = oauth.get_oauth_store()
    store.save_tokens(
        "cf",
        {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600},
        token_url="https://p/token",
        client_id="cid",
    )
    assert store.bearer("cf") == "AT"
    assert store.has_tokens("cf")
    # token file is restricted
    mode = (tmp_path / "mcp_oauth.json").stat().st_mode & 0o777
    assert mode == 0o600
    assert store.forget("cf") and not store.has_tokens("cf")
    oauth.reset_oauth_store_for_tests()


def test_tokens_encrypted_at_rest_when_key_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setenv("ECHO_MCP_TOKEN_KEY", Fernet.generate_key().decode())
    path = tmp_path / "mcp_oauth.json"
    store = oauth.MCPOAuthStore(path=path)
    store.save_tokens(
        "cf",
        {"access_token": "AT-secret", "refresh_token": "RT", "expires_in": 3600},
        token_url="https://p/token",
        client_id="cid",
    )
    blob = path.read_bytes()
    assert blob[:1] != b"{"  # encrypted, not plaintext JSON
    assert b"AT-secret" not in blob  # token unreadable on disk
    assert (path.stat().st_mode & 0o777) == 0o600
    # A fresh store with the same key round-trips the tokens.
    assert oauth.MCPOAuthStore(path=path).bearer("cf") == "AT-secret"


def test_plaintext_store_still_readable_after_key_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A store written plaintext (no key) stays readable once a key is later
    # configured — enabling encryption must not force a re-auth on upgrade.
    from cryptography.fernet import Fernet

    monkeypatch.delenv("ECHO_MCP_TOKEN_KEY", raising=False)
    path = tmp_path / "mcp_oauth.json"
    oauth.MCPOAuthStore(path=path).save_tokens(
        "cf", {"access_token": "AT", "expires_in": 3600}, token_url="u", client_id="c"
    )
    assert path.read_bytes()[:1] == b"{"  # plaintext
    monkeypatch.setenv("ECHO_MCP_TOKEN_KEY", Fernet.generate_key().decode())
    assert oauth.MCPOAuthStore(path=path).bearer("cf") == "AT"


def test_wrong_key_yields_empty_store_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setenv("ECHO_MCP_TOKEN_KEY", Fernet.generate_key().decode())
    path = tmp_path / "mcp_oauth.json"
    oauth.MCPOAuthStore(path=path).save_tokens(
        "cf", {"access_token": "AT", "expires_in": 3600}, token_url="u", client_id="c"
    )
    # A different key can't decrypt the store → start empty (re-auth), no crash.
    monkeypatch.setenv("ECHO_MCP_TOKEN_KEY", Fernet.generate_key().decode())
    assert not oauth.MCPOAuthStore(path=path).has_tokens("cf")


def test_bearer_refreshes_near_expiry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    oauth.reset_oauth_store_for_tests()
    store = oauth.get_oauth_store()
    store.save_tokens(
        "cf",
        {"access_token": "OLD", "refresh_token": "RT", "expires_in": 1},
        token_url="https://p/token",
        client_id="cid",
    )
    monkeypatch.setattr(
        oauth.urllib_request,
        "urlopen",
        _fake_urlopen(json.dumps({"access_token": "NEW", "expires_in": 3600}).encode()),
    )
    assert store.bearer("cf") == "NEW"  # refreshed (was within skew)
    assert store.bearer("cf") == "NEW"  # now fresh, no second refresh
    oauth.reset_oauth_store_for_tests()


def test_exchange_code_posts_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        oauth.urllib_request,
        "urlopen",
        _fake_urlopen(json.dumps({"access_token": "AT", "expires_in": 3600}).encode()),
    )
    resp = oauth.exchange_code(
        token_url="https://p/token",
        code="C",
        code_verifier="V",
        client_id="cid",
        redirect_uri="http://cb",
    )
    assert resp["access_token"] == "AT"


def test_token_endpoint_ssrf_is_rejected_before_exchange() -> None:
    with pytest.raises(ValueError, match="url_guard rejected"):
        oauth.exchange_code(
            token_url="http://127.0.0.1:8000/token",
            code="C",
            code_verifier="V",
            client_id="cid",
            redirect_uri="http://cb",
        )


def test_bearer_for_server_none_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    oauth.reset_oauth_store_for_tests()
    assert oauth.bearer_for_server("nope") is None
    oauth.reset_oauth_store_for_tests()

