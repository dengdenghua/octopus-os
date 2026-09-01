"""MCP OAuth 2.0 (PKCE) client — authorize-on-enable for remote MCP servers.

Step 1 covers **known** authorization endpoints (the caller supplies
``authorize_url`` / ``token_url`` / ``client_id``; auto-discovery via
``.well-known`` + dynamic client registration is step 2). The flow:

1. **authorize** — mint a PKCE verifier + CSRF ``state``, persist a pending
   record, and return the provider authorize URL for the UI to open.
2. **callback** — the provider redirects back with ``?code&state``; we match the
   pending record by ``state``, exchange the code (+ verifier) for tokens, and
   persist them per server.
3. **transport** — :func:`bearer_for_server` returns a valid access token
   (refreshing via ``refresh_token`` near expiry), which the remote MCP client
   attaches as ``Authorization: Bearer``.

Tokens live in ``~/.echo/mcp_oauth.json`` (created 0600). At rest they are
plaintext by default (matching how local CLIs store OAuth tokens); set
``ECHO_MCP_TOKEN_KEY`` (a Fernet key kept in your secret store, not on the
token disk) to encrypt them — see ``_token_cipher``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import request as urllib_request

from runtime.platform.io import atomic_write_bytes, atomic_write_json
from runtime.safety.auth.url_guard import check_url, safe_httpx_request

_logger = logging.getLogger(__name__)

_PENDING_TTL = 600.0  # authorize→callback round-trip window (10 min)
_REFRESH_SKEW = 60.0  # refresh when within 60s of expiry
_DEFAULT_URLOPEN = urllib_request.urlopen


def _guard_oauth_url(url: str) -> None:
    # Unit tests replace urlopen with a hermetic fake and use non-resolvable
    # placeholder hosts. Production always performs DNS resolution here; the
    # actual production request is then made through the pinned-IP helper.
    resolve_dns = urllib_request.urlopen is _DEFAULT_URLOPEN
    verdict = check_url(url, allow_private=False, resolve_dns=resolve_dns)
    if not verdict.allow:
        raise ValueError(f"url_guard rejected: {verdict.reason}")


# ── PKCE + URL ───────────────────────────────────────────


def new_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for PKCE S256."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(
    *,
    authorize_url: str,
    client_id: str,
    redirect_uri: str,
    scopes: list[str] | None,
    state: str,
    code_challenge: str | None = None,
    code_challenge_method: str | None = "S256",
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    # 部分服务商(GitHub 等 OAuth App)不支持 PKCE:不传 code_challenge 即走纯
    # authorization_code + client_secret 流。
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = code_challenge_method or "S256"
    if scopes:
        params["scope"] = " ".join(scopes)
    sep = "&" if "?" in authorize_url else "?"
    return f"{authorize_url}{sep}{urllib.parse.urlencode(params)}"


def _post_form(url: str, data: dict[str, str], timeout: float = 30.0) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    _guard_oauth_url(url)
    if urllib_request.urlopen is _DEFAULT_URLOPEN:
        response = safe_httpx_request(
            "POST",
            url,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return json.loads(response.content.decode("utf-8"))
    req = urllib_request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    with urllib_request.urlopen(req, timeout=timeout) as resp:  # nosec B310  # noqa: S310 — guarded hermetic test seam; production uses pinned helper
        return json.loads(resp.read().decode("utf-8"))


def exchange_code(
    *,
    token_url: str,
    code: str,
    code_verifier: str | None = None,
    client_id: str,
    client_secret: str | None = None,
    redirect_uri: str,
) -> dict[str, Any]:
    data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier
    if client_secret:
        data["client_secret"] = client_secret
    return _post_form(token_url, data)


def refresh_access(*, token_url: str, refresh_token: str, client_id: str) -> dict[str, Any]:
    return _post_form(
        token_url,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
    )


# ── Store ────────────────────────────────────────────────


@dataclass
class _Pending:
    server: str
    code_verifier: str
    redirect_uri: str
    token_url: str
    client_id: str
    created_ts: float
    client_secret: str = ""
    use_pkce: bool = True


@dataclass
class _Tokens:
    access_token: str
    refresh_token: str = ""
    expires_at: float = 0.0
    token_url: str = ""
    client_id: str = ""


def _store_path(tenant_id: str | None = None) -> Path:
    home = os.environ.get("ECHO_HOME")
    base = Path(home) if home else (Path.home() / ".echo")
    if tenant_id:
        digest = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:32]
        base = base / "tenants" / digest
    base.mkdir(parents=True, exist_ok=True)
    return base / "mcp_oauth.json"


_TOKEN_KEY_ENV = "ECHO_MCP_TOKEN_KEY"
_TOKEN_KEY_NAME = "mcp-oauth-token-key"


def _token_cipher() -> Any:
    """Fernet cipher for at-rest token encryption, or None when disabled.

    The key is resolved in two steps, first match wins:

    1. ``ECHO_MCP_TOKEN_KEY`` — a urlsafe-base64 32-byte key
       (``cryptography.fernet.Fernet.generate_key()``). Deployments that
       inject secrets from their own vault keep full control this way.
    2. The OS keychain (macOS Keychain / Secret Service / DPAPI), minting
       and storing a key on first use. This is what makes encryption the
       default on a normal desktop install instead of something the
       operator has to remember to turn on.

    When neither is available the store keeps its plaintext 0600 file — a
    machine with no keychain must still be able to hold tokens, so this
    degrades rather than failing. A malformed key or a missing
    ``cryptography`` install degrades the same way.
    """
    from runtime.platform.credentials.secret_store import get_or_create_fernet_key

    try:
        key = get_or_create_fernet_key(_TOKEN_KEY_NAME, env_var=_TOKEN_KEY_ENV)
    except Exception:  # noqa: BLE001 — keychain trouble must never break token storage
        key = os.environ.get(_TOKEN_KEY_ENV)
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet

        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except Exception:  # noqa: BLE001 — bad key / missing dep → degrade to plaintext
        _logger.warning(
            "MCP OAuth token key is set but unusable (need a valid Fernet key "
            "and the cryptography package); tokens stored unencrypted."
        )
        return None


class MCPOAuthStore:
    """Thread-safe, JSON-backed per-server OAuth token + pending-flow store."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        tenant_id: str | None = None,
        create_parent: bool = True,
    ) -> None:
        self.tenant_id = str(tenant_id).strip() if tenant_id else None
        self._path = Path(path) if path else _store_path(self.tenant_id)
        if create_parent:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._pending: dict[str, _Pending] = {}
        self._tokens: dict[str, _Tokens] = {}
        self._clients: dict[str, str] = {}  # issuer → registered client_id (DCR)
        self._app_clients: dict[str, dict[str, str]] = {}  # provider → {client_id, client_secret}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            blob = self._path.read_bytes()
        except OSError:
            return
        # Back-compat + opt-in encryption: a plaintext store parses as JSON
        # directly; an encrypted store (Fernet ciphertext) does not, so we
        # decrypt with the configured key. A store we can't read (encrypted
        # but no/wrong key) is ignored, forcing a clean re-auth rather than
        # a crash.
        try:
            raw = json.loads(blob.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            cipher = _token_cipher()
            if cipher is None:
                return
            try:
                raw = json.loads(cipher.decrypt(blob).decode("utf-8"))
            except Exception:  # noqa: BLE001 — undecryptable store → start empty
                _logger.warning(
                    "MCP OAuth token store could not be decrypted (the key in "
                    "%s or the OS keychain does not match the one it was "
                    "written with); ignoring — re-auth required.",
                    _TOKEN_KEY_ENV,
                )
                return
        for srv, tok in (raw.get("tokens") or {}).items():
            try:
                self._tokens[str(srv)] = _Tokens(
                    access_token=str(tok["access_token"]),
                    refresh_token=str(tok.get("refresh_token", "")),
                    expires_at=float(tok.get("expires_at", 0.0)),
                    token_url=str(tok.get("token_url", "")),
                    client_id=str(tok.get("client_id", "")),
                )
            except (KeyError, TypeError, ValueError):
                continue
        for state, pend in (raw.get("pending") or {}).items():
            try:
                self._pending[str(state)] = _Pending(
                    server=str(pend["server"]),
                    code_verifier=str(pend["code_verifier"]),
                    redirect_uri=str(pend["redirect_uri"]),
                    token_url=str(pend["token_url"]),
                    client_id=str(pend["client_id"]),
                    created_ts=float(pend.get("created_ts", 0.0)),
                    client_secret=str(pend.get("client_secret", "")),
                    use_pkce=bool(pend.get("use_pkce", True)),
                )
            except (KeyError, TypeError, ValueError):
                continue
        for issuer, cid in (raw.get("clients") or {}).items():
            if isinstance(cid, str):
                self._clients[str(issuer)] = cid
        for prov, app in (raw.get("app_clients") or {}).items():
            if isinstance(app, dict) and app.get("client_id"):
                self._app_clients[str(prov)] = {
                    "client_id": str(app["client_id"]),
                    "client_secret": str(app.get("client_secret", "")),
                }

    def _save(self) -> None:
        now = time.time()
        self._pending = {
            s: p for s, p in self._pending.items() if now - p.created_ts < _PENDING_TTL
        }
        payload = {
            "version": 1,
            "tokens": {
                s: {
                    "access_token": t.access_token,
                    "refresh_token": t.refresh_token,
                    "expires_at": t.expires_at,
                    "token_url": t.token_url,
                    "client_id": t.client_id,
                }
                for s, t in self._tokens.items()
            },
            "pending": {
                s: {
                    "server": p.server,
                    "code_verifier": p.code_verifier,
                    "redirect_uri": p.redirect_uri,
                    "token_url": p.token_url,
                    "client_id": p.client_id,
                    "created_ts": p.created_ts,
                    "client_secret": p.client_secret,
                    "use_pkce": p.use_pkce,
                }
                for s, p in self._pending.items()
            },
            "clients": dict(self._clients),
            "app_clients": dict(self._app_clients),
        }
        # 0o600 from creation — the token file holds access/refresh tokens
        # and must never be even briefly group/world-readable, so we set
        # the mode on the temp file before writing rather than chmod'ing
        # after (which left a TOCTOU window at the default 0o644). When a
        # key is configured the payload is also encrypted at rest (defense
        # in depth against backup/disk leaks); otherwise it's plaintext JSON.
        cipher = _token_cipher()
        if cipher is not None:
            token = cipher.encrypt(json.dumps(payload).encode("utf-8"))
            atomic_write_bytes(self._path, token, mode=0o600)
        else:
            atomic_write_json(self._path, payload, mode=0o600)

    def start_pending(
        self,
        *,
        server: str,
        code_verifier: str,
        redirect_uri: str,
        token_url: str,
        client_id: str,
        client_secret: str = "",
        use_pkce: bool = True,
    ) -> str:
        state = secrets.token_urlsafe(32)
        # The callback is intentionally unauthenticated.  Bind the opaque
        # state to the tenant store so it cannot fall back to another
        # tenant's OAuth token namespace after the browser redirect.
        if self.tenant_id:
            tenant_digest = hashlib.sha256(
                self.tenant_id.encode("utf-8"),
            ).hexdigest()[:32]
            state = f"{tenant_digest}.{state}"
        with self._lock:
            self._pending[state] = _Pending(
                server,
                code_verifier,
                redirect_uri,
                token_url,
                client_id,
                time.time(),
                client_secret=client_secret,
                use_pkce=use_pkce,
            )
            self._save()
        return state

    def pop_pending(self, state: str) -> _Pending | None:
        with self._lock:
            pend = self._pending.pop(state, None)
            if pend is None:
                return None
            self._save()
            if time.time() - pend.created_ts >= _PENDING_TTL:
                return None
            return pend

    def save_tokens(
        self,
        server: str,
        token_response: dict[str, Any],
        *,
        token_url: str,
        client_id: str,
    ) -> None:
        with self._lock:
            prior = self._tokens.get(server)
            refresh = str(
                token_response.get("refresh_token") or (prior.refresh_token if prior else "")
            )
            self._tokens[server] = _Tokens(
                access_token=str(token_response.get("access_token", "")),
                refresh_token=refresh,
                expires_at=time.time() + float(token_response.get("expires_in", 3600)),
                token_url=token_url,
                client_id=client_id,
            )
            self._save()

    def bearer(self, server: str) -> str | None:
        with self._lock:
            tok = self._tokens.get(server)
            if tok is None or not tok.access_token:
                return None
            stale = (
                tok.expires_at
                and tok.expires_at - time.time() < _REFRESH_SKEW
                and tok.refresh_token
                and tok.token_url
            )
            if stale:
                try:
                    resp = refresh_access(
                        token_url=tok.token_url,
                        refresh_token=tok.refresh_token,
                        client_id=tok.client_id,
                    )
                except Exception:  # noqa: BLE001 — fall back to existing token on refresh failure
                    return tok.access_token
                self.save_tokens(server, resp, token_url=tok.token_url, client_id=tok.client_id)
                tok = self._tokens.get(server)
            return tok.access_token if tok else None

    def get_client(self, issuer: str) -> str | None:
        with self._lock:
            return self._clients.get(issuer)

    def save_client(self, issuer: str, client_id: str) -> None:
        with self._lock:
            self._clients[issuer] = client_id
            self._save()

    # ── 服务商 OAuth App 凭据(BYO OAuth:用户自己注册的 client_id/secret)─
    def save_app_client(
        self,
        provider: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        with self._lock:
            self._app_clients[provider] = {
                "client_id": client_id,
                "client_secret": client_secret,
            }
            self._save()

    def get_app_client(self, provider: str) -> dict[str, str] | None:
        with self._lock:
            app = self._app_clients.get(provider)
            return dict(app) if app else None

    def forget_app_client(self, provider: str) -> bool:
        with self._lock:
            if provider not in self._app_clients:
                return False
            del self._app_clients[provider]
            self._save()
            return True

    def has_tokens(self, server: str) -> bool:
        with self._lock:
            return server in self._tokens

    def forget(self, server: str) -> bool:
        with self._lock:
            if server not in self._tokens:
                return False
            del self._tokens[server]
            self._save()
            return True


# ── Module singleton ─────────────────────────────────────

_GLOBAL_STORES: dict[str, MCPOAuthStore] = {}
_GLOBAL_LOCK = threading.Lock()


def get_oauth_store(tenant_id: str | None = None) -> MCPOAuthStore:
    """Return the OAuth store for one tenant; no arg is the legacy store."""
    key = str(tenant_id).strip() if tenant_id else "__legacy__"
    if key not in _GLOBAL_STORES:
        with _GLOBAL_LOCK:
            if key not in _GLOBAL_STORES:
                _GLOBAL_STORES[key] = MCPOAuthStore(
                    tenant_id=None if key == "__legacy__" else key,
                )
    return _GLOBAL_STORES[key]


def get_oauth_store_for_state(state: str) -> MCPOAuthStore:
    """Resolve a callback state to its tenant-partitioned store.

    The state contains only a non-reversible tenant digest, never the tenant
    identifier itself.  Unknown/malformed states use the legacy store and
    will fail the normal single-use lookup.
    """
    prefix, separator, _opaque = str(state).partition(".")
    if (
        not separator
        or len(prefix) != 32
        or any(char not in "0123456789abcdef" for char in prefix.lower())
    ):
        return get_oauth_store()
    home = os.environ.get("ECHO_HOME")
    base = Path(home) if home else (Path.home() / ".echo")
    path = base / "tenants" / prefix / "mcp_oauth.json"
    return MCPOAuthStore(path=path, create_parent=False)


def bearer_for_server(name: str, tenant_id: str | None = None) -> str | None:
    """Valid access token for ``name`` (refreshing if needed), or ``None``.

    Never raises — the MCP transport calls this on every connect and must
    degrade to no-auth when there's no token / the store is unavailable.
    """
    try:
        return get_oauth_store(tenant_id).bearer(name)
    except Exception:  # noqa: BLE001
        return None


def reset_oauth_store_for_tests() -> None:
    with _GLOBAL_LOCK:
        _GLOBAL_STORES.clear()


__all__ = [
    "MCPOAuthStore",
    "bearer_for_server",
    "build_authorize_url",
    "exchange_code",
    "get_oauth_store",
    "get_oauth_store_for_state",
    "new_pkce",
    "refresh_access",
    "reset_oauth_store_for_tests",
]
