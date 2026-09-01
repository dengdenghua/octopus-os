"""Bounded, SSRF-resistant downloads for remote plugin catalogs.

Marketplace metadata and archives are operator-facing supply-chain inputs, not
ordinary web browsing.  Keep the policy deliberately narrow: every hop must be
public HTTPS, redirects are revalidated, credentials in URLs are forbidden,
and the complete response is capped before a caller persists or parses it.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit

from runtime.safety.auth.url_guard import check_url, safe_httpx_request

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5


def _validate_public_https_url(url: str) -> str:
    value = str(url or "").strip()
    try:
        parsed = urlsplit(value)
        # Accessing ``port`` performs urllib's strict numeric/range validation.
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid marketplace URL: {exc}") from exc
    if parsed.scheme.lower() != "https":
        raise ValueError("marketplace URL must use https")
    if not parsed.hostname:
        raise ValueError("marketplace URL must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("marketplace URL must not contain credentials")
    verdict = check_url(value, allow_private=False)
    if not verdict.allow:
        raise ValueError(f"marketplace URL rejected: {verdict.reason}")
    return value


def fetch_public_https_bytes(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
) -> bytes:
    """Fetch a public HTTPS resource with a hard response-size ceiling.

    ``safe_httpx_request`` pins the approved DNS result for each connection.
    Redirect handling stays here so an HTTPS catalog cannot redirect a token or
    archive request to plaintext HTTP or an internal address.
    """

    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    current = _validate_public_https_url(url)
    for _hop in range(_MAX_REDIRECTS + 1):
        response = safe_httpx_request(
            "GET",
            current,
            headers={"User-Agent": "echo-agent/1.0"},
            timeout=timeout,
            allow_private=False,
            follow_redirects=False,
            read_cap_bytes=max_bytes,
        )
        if response.status_code in _REDIRECT_STATUSES:
            location = response.headers.get("Location")
            if not location:
                response.raise_for_status()
            current = _validate_public_https_url(urljoin(current, location))
            continue
        response.raise_for_status()
        declared = response.headers.get("Content-Length")
        if declared:
            try:
                declared_size = int(declared)
            except ValueError as exc:
                raise ValueError("invalid Content-Length from marketplace") from exc
            if declared_size < 0 or declared_size > max_bytes:
                raise ValueError(f"marketplace response exceeds {max_bytes} bytes")
        body = bytes(response.content)
        if len(body) > max_bytes:
            raise ValueError(f"marketplace response exceeds {max_bytes} bytes")
        return body
    raise ValueError("too many marketplace redirects")


__all__ = ["fetch_public_https_bytes"]
