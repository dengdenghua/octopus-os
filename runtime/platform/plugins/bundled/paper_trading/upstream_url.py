"""Strict parsing for operator-configured paper-trading upstream URLs."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

_DOMAIN_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def _canonical_host(hostname: str) -> str:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            ascii_host = hostname.rstrip(".").encode("idna").decode("ascii")
        except UnicodeError:
            return ""
        labels = ascii_host.split(".")
        if (
            not ascii_host
            or len(ascii_host) > 253
            or not all(_DOMAIN_LABEL.fullmatch(label) for label in labels)
        ):
            return ""
        return ascii_host.lower()
    return f"[{address.compressed}]" if address.version == 6 else address.compressed


def upstream_origin(base_url: str, *, https_only: bool = False) -> str:
    """Return a canonical HTTP(S) origin or ``""`` for an unsafe URL.

    Userinfo, malformed ports, query/fragment components, and invalid hostnames
    are rejected.  Canonical reconstruction also prevents HTML contexts from
    accidentally reusing attacker-controlled ``netloc`` text.
    """
    try:
        parts = urlsplit(str(base_url or "").strip())
        port = parts.port
    except (TypeError, ValueError):
        return ""
    scheme = parts.scheme.lower()
    allowed_schemes = {"https"} if https_only else {"http", "https"}
    if (
        scheme not in allowed_schemes
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        return ""
    host = _canonical_host(parts.hostname)
    if not host:
        return ""
    return f"{scheme}://{host}{f':{port}' if port is not None else ''}"


def secure_upstream_origin(base_url: str) -> str:
    """Return a canonical HTTPS origin, rejecting every other input."""
    return upstream_origin(base_url, https_only=True)


__all__ = ["secure_upstream_origin", "upstream_origin"]
