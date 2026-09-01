"""Narrow server-to-server client for the public share relay.

The local gateway performs the privacy projection first.  When an operator
configures a relay, only that bounded snapshot is uploaded; thread state,
workspace paths, tool calls and provider credentials never leave the gateway.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


class ThreadShareRelayError(RuntimeError):
    """A safe, non-credential-bearing relay failure."""


def _relay_origin(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlparse(raw)
    loopback_http = parsed.scheme == "http" and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    if (parsed.scheme != "https" and not loopback_http) or not parsed.netloc:
        raise ValueError("public share relay must use HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("public share relay URL is invalid")
    return raw


class ThreadShareRelayClient:
    def __init__(
        self,
        origin: str,
        *,
        api_key: str | None = None,
        bearer_token: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.origin = _relay_origin(origin)
        self.api_key = str(api_key or "").strip()
        self.bearer_token = str(bearer_token or "").strip()
        if not self.api_key and not self.bearer_token:
            raise ValueError("public share relay authentication is missing")
        if self.api_key and len(self.api_key) < 32:
            raise ValueError("public share relay API key must contain at least 32 characters")
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 30.0))

    @staticmethod
    def _owner_scope(*, actor_id: str, tenant_id: str) -> str:
        """Create a stable, non-identifying scope for relay-side ownership."""

        digest = hashlib.sha256(f"{tenant_id}\0{actor_id}".encode()).hexdigest()
        return f"relay_{digest}"

    @property
    def _management_prefix(self) -> str:
        # A configured device token is already owner-bound by Cloud Edge, while
        # the narrow service key uses the account-compatible relay route.
        return "/api/cloud-edge/thread-shares" if self.api_key else "/edge/v1/thread-shares"

    @classmethod
    def from_env(cls) -> ThreadShareRelayClient | None:
        origin = str(os.environ.get("ECHO_PUBLIC_SHARE_RELAY_URL") or "").strip()
        if not origin:
            return None
        return cls(
            origin,
            api_key=os.environ.get("ECHO_PUBLIC_SHARE_RELAY_API_KEY"),
            bearer_token=os.environ.get("ECHO_PUBLIC_SHARE_RELAY_BEARER_TOKEN"),
            timeout_seconds=float(os.environ.get("ECHO_PUBLIC_SHARE_RELAY_TIMEOUT_SECONDS") or 10),
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        owner_scope: str | None = None,
    ) -> dict[str, Any] | None:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
            if owner_scope:
                headers["X-Echo-Share-Owner-Scope"] = owner_scope
        else:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        request = Request(f"{self.origin}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ThreadShareRelayError("public share relay response is too large")
                if not raw:
                    return None
                value = json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 401 or exc.code == 403:
                raise ThreadShareRelayError("public share relay authentication failed") from exc
            if exc.code == 404:
                raise ThreadShareRelayError("public share was not found") from exc
            if exc.code == 413:
                raise ThreadShareRelayError("public share is too large") from exc
            if exc.code in {409, 429}:
                raise ThreadShareRelayError("public share quota exceeded") from exc
            if exc.code == 507:
                raise ThreadShareRelayError("public share storage is full") from exc
            raise ThreadShareRelayError(f"public share relay failed ({exc.code})") from exc
        except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            raise ThreadShareRelayError("public share relay is unavailable") from exc
        if not isinstance(value, dict):
            raise ThreadShareRelayError("public share relay returned an invalid response")
        return value

    def create(
        self,
        *,
        source_thread_id: str,
        snapshot: dict[str, Any],
        actor_id: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        result = self._request(
            "POST",
            self._management_prefix,
            body={"source_thread_id": source_thread_id, "snapshot": snapshot},
            owner_scope=self._owner_scope(actor_id=actor_id, tenant_id=tenant_id),
        )
        if (
            not isinstance(result, dict)
            or not result.get("share_id")
            or not result.get("share_url")
        ):
            raise ThreadShareRelayError("public share relay returned an invalid response")
        return result

    def list_for_thread(
        self,
        *,
        source_thread_id: str,
        actor_id: str,
        tenant_id: str,
    ) -> list[dict[str, Any]]:
        query = urlencode({"source_thread_id": source_thread_id})
        result = (
            self._request(
                "GET",
                f"{self._management_prefix}?{query}",
                owner_scope=self._owner_scope(actor_id=actor_id, tenant_id=tenant_id),
            )
            or {}
        )
        shares = result.get("shares")
        return (
            [item for item in shares if isinstance(item, dict)] if isinstance(shares, list) else []
        )

    def revoke(self, share_id: str, *, actor_id: str, tenant_id: str) -> None:
        clean_id = str(share_id or "").strip()
        if not clean_id:
            raise ThreadShareRelayError("public share id is missing")
        self._request(
            "DELETE",
            f"{self._management_prefix}/{quote(clean_id, safe='')}",
            owner_scope=self._owner_scope(actor_id=actor_id, tenant_id=tenant_id),
        )


__all__ = ["ThreadShareRelayClient", "ThreadShareRelayError"]
