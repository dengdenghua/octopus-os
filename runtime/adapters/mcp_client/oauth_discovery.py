"""OAuth metadata discovery + dynamic client registration for MCP (step 2).

Turns a bare MCP server URL into the endpoints step-1 needs, so the UI can offer
"authorize" without anyone hand-entering authorize/token/client_id. Follows the
MCP auth spec (RFC 9728 protected-resource metadata → RFC 8414 auth-server
metadata → RFC 7591 dynamic client registration):

  1. GET {origin}/.well-known/oauth-protected-resource → authorization_servers[0]
     (fallback: treat the server origin itself as the auth server).
  2. GET {auth_server}/.well-known/oauth-authorization-server (fallback
     openid-configuration) → authorization_endpoint / token_endpoint /
     registration_endpoint / scopes_supported.
  3. If no client_id is cached for the issuer and a registration endpoint exists,
     register a public (PKCE, token_endpoint_auth_method=none) client.

All network calls are best-effort and never raise — callers fall back to the
manual /authorize path when discovery isn't available.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from typing import Any
from urllib import request as urllib_request

from runtime.safety.auth.url_guard import check_url, safe_httpx_request

_DEFAULT_URLOPEN = urllib_request.urlopen


def _guard_discovery_url(url: str) -> None:
    resolve_dns = urllib_request.urlopen is _DEFAULT_URLOPEN
    verdict = check_url(url, allow_private=False, resolve_dns=resolve_dns)
    if not verdict.allow:
        raise ValueError(f"url_guard rejected: {verdict.reason}")


@dataclass(frozen=True)
class OAuthEndpoints:
    issuer: str
    authorize_url: str
    token_url: str
    registration_url: str = ""
    scopes: tuple[str, ...] = ()


def _get_json(url: str, timeout: float = 15.0) -> dict[str, Any] | None:
    try:
        _guard_discovery_url(url)
        if urllib_request.urlopen is _DEFAULT_URLOPEN:
            resp = safe_httpx_request(
                "GET",
                url,
                headers={"Accept": "application/json"},
                timeout=timeout,
            )
            resp.raise_for_status()
            data = json.loads(resp.content.decode("utf-8"))
            return data if isinstance(data, dict) else None
        req = urllib_request.Request(url, headers={"Accept": "application/json"})
        with urllib_request.urlopen(req, timeout=timeout) as resp:  # nosec B310  # noqa: S310 — guarded hermetic test seam; production uses pinned helper
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 — discovery is best-effort
        return None
    return data if isinstance(data, dict) else None


def _origin(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def discover(server_url: str, *, timeout: float = 15.0) -> OAuthEndpoints | None:
    origin = _origin(server_url)
    if not origin:
        return None

    # 1. protected-resource metadata → authorization server (fallback: origin)
    auth_server = origin
    prm = _get_json(f"{origin}/.well-known/oauth-protected-resource", timeout=timeout)
    if prm:
        servers = prm.get("authorization_servers")
        if isinstance(servers, list) and servers:
            auth_server = str(servers[0]).rstrip("/")

    # 2. authorization-server metadata
    meta = _get_json(
        f"{auth_server}/.well-known/oauth-authorization-server",
        timeout=timeout,
    ) or _get_json(
        f"{auth_server}/.well-known/openid-configuration",
        timeout=timeout,
    )
    if not meta:
        return None

    authorize_url = str(meta.get("authorization_endpoint") or "")
    token_url = str(meta.get("token_endpoint") or "")
    if not (authorize_url and token_url):
        return None
    try:
        _guard_discovery_url(authorize_url)
        _guard_discovery_url(token_url)
    except ValueError:
        return None
    registration_url = str(meta.get("registration_endpoint") or "")
    if registration_url:
        try:
            _guard_discovery_url(registration_url)
        except ValueError:
            registration_url = ""

    raw_scopes = meta.get("scopes_supported")
    scopes = tuple(str(s) for s in raw_scopes) if isinstance(raw_scopes, list) else ()
    return OAuthEndpoints(
        issuer=str(meta.get("issuer") or auth_server),
        authorize_url=authorize_url,
        token_url=token_url,
        registration_url=registration_url,
        scopes=scopes,
    )


def register_client(
    registration_url: str,
    *,
    redirect_uri: str,
    client_name: str = "Echo Agent",
    timeout: float = 15.0,
) -> str | None:
    """Dynamic client registration (RFC 7591) for a public PKCE client."""
    body = json.dumps(
        {
            "client_name": client_name,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
    ).encode("utf-8")
    try:
        _guard_discovery_url(registration_url)
        if urllib_request.urlopen is _DEFAULT_URLOPEN:
            resp = safe_httpx_request(
                "POST",
                registration_url,
                data=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=timeout,
            )
            resp.raise_for_status()
            data = json.loads(resp.content.decode("utf-8"))
            client_id = data.get("client_id") if isinstance(data, dict) else None
            return str(client_id) if client_id else None
        req = urllib_request.Request(
            registration_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib_request.urlopen(req, timeout=timeout) as resp:  # nosec B310  # noqa: S310 — guarded hermetic test seam; production uses pinned helper
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 — DCR is best-effort
        return None
    client_id = data.get("client_id") if isinstance(data, dict) else None
    return str(client_id) if client_id else None


__all__ = ["OAuthEndpoints", "discover", "register_client"]
