from __future__ import annotations

import ipaddress
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

_SAFE_SCHEMES = frozenset({"http", "https"})

_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata.azure.internal",
        "instance-data",  # AWS SSM
        "instance-data.ec2.internal",
        "fd00:ec2::254",  # AWS IPv6 metadata
    }
)

_BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".svc.cluster.local",
    ".lan",
)


@dataclass(frozen=True)
class URLVerdict:
    allow: bool
    url: str
    reason: str = ""
    resolved_ip: str | None = None


def check_url(
    url: str,
    *,
    allow_private: bool = False,
    resolve_dns: bool = True,
) -> URLVerdict:
    if not url or not isinstance(url, str):
        return URLVerdict(False, str(url), "empty_or_non_string_url")

    try:
        parsed = urlparse(url)
    except Exception as e:  # noqa: BLE001
        return URLVerdict(False, url, f"unparseable: {e}")

    scheme = (parsed.scheme or "").lower()
    if scheme not in _SAFE_SCHEMES:
        return URLVerdict(False, url, f"disallowed_scheme: {scheme!r}")

    try:
        port = parsed.port
    except ValueError as exc:
        return URLVerdict(False, url, f"invalid_port: {exc}")
    if port == 0:
        return URLVerdict(False, url, "invalid_port: 0")

    host = parsed.hostname
    if not host:
        return URLVerdict(False, url, "missing_host")

    # IDN normalisation.  ``urllib.parse.urlparse`` returns hostnames
    # without IDNA-normalising them, so ``xn--bad-host.example``-style
    # punycode and raw Unicode look distinct to our suffix / exact-match
    # blocklists. Canonicalise to punycode (ASCII-compatible encoding)
    # before the string comparisons below.
    try:
        host_ascii = host.encode("idna").decode("ascii")
    except UnicodeError:
        # Catches raw U+2024/U+3002/U+FF0E full-width dots, homoglyph
        # attempts with invalid IDNA, and empty labels.  Refuse.
        return URLVerdict(False, url, "invalid_idn_host")
    host_lc = host_ascii.lower()

    if not allow_private:
        if host_lc in _BLOCKED_HOSTS:
            return URLVerdict(False, url, f"blocked_host: {host_lc}")
        for suf in _BLOCKED_SUFFIXES:
            if host_lc.endswith(suf):
                return URLVerdict(False, url, f"blocked_suffix: {suf}")

    ip_obj = _as_ip(host)
    if ip_obj is not None:
        if not allow_private and _is_private_ip(ip_obj):
            return URLVerdict(False, url, f"private_ip: {ip_obj}", str(ip_obj))
        return URLVerdict(True, url, resolved_ip=str(ip_obj))

    if not resolve_dns:
        return URLVerdict(True, url)

    resolved = _resolve_all(host)
    if not resolved:
        return URLVerdict(False, url, "dns_resolution_failed")

    if not allow_private:
        for ip_obj in resolved:
            if _is_private_ip(ip_obj):
                return URLVerdict(
                    False,
                    url,
                    f"dns_resolves_to_private: {host} → {ip_obj}",
                    resolved_ip=str(ip_obj),
                )

    return URLVerdict(True, url, resolved_ip=str(resolved[0]))


def is_safe_url(url: str, *, allow_private: bool = False) -> bool:
    return check_url(url, allow_private=allow_private).allow


# ═══════════════════════════════════════════════════════════
# DNS-rebinding-proof fetch helpers.
#
# ``check_url`` resolves a host to an IP before the request runs; the
# underlying fetch then re-resolves on connect. A rebinding attacker
# can serve a public IP on the first resolve and an internal IP on the
# second, tunnelling through the guard.
#
# To close the TOCTOU we (a) resolve once, (b) hand the fetcher the
# literal IP as the connect target, and (c) put the original hostname
# in the ``Host:`` header so TLS SNI + vhost routing still work.
# ═══════════════════════════════════════════════════════════


def safe_urlopen(
    url: str,
    *,
    timeout: float = 10.0,
    read_cap_bytes: int = 1_000_000,
    allow_private: bool = False,
) -> tuple[bytes, dict[str, str]]:
    """Fetch ``url`` with rebinding-proof host pinning.

    Returns ``(body, headers)`` on success. Raises ``ValueError``
    (from ``check_url``) or the usual urllib exceptions otherwise.

    Only supports HTTP(S) GET — the call sites we need to protect
    (page-title probes, text extraction, skill-archive downloads)
    are all GETs.
    """
    import urllib.error

    try:
        # Read one byte beyond the public contract so callers retain the
        # historical truncation signal without ever buffering an unbounded
        # response. Redirects are revalidated at every hop.
        response = safe_httpx_request(
            "GET",
            url,
            timeout=timeout,
            allow_private=allow_private,
            follow_redirects=True,
            read_cap_bytes=read_cap_bytes + 1,
        )
        response.raise_for_status()
    except ValueError:
        raise
    except Exception as exc:
        # Preserve the legacy urllib-facing error contract used by the
        # browser helpers while routing the actual connection through httpx.
        try:
            import httpx
        except ImportError:  # pragma: no cover - safe_httpx_request already reports it
            raise
        if isinstance(exc, httpx.HTTPError):
            raise urllib.error.URLError(str(exc)) from exc
        raise

    data = bytes(response.content)
    headers = {key.title(): value for key, value in response.headers.items()}
    if len(data) > read_cap_bytes:
        data = data[:read_cap_bytes]
        headers["X-Echo-Truncated"] = "true"
    return data, headers


def safe_httpx_get(
    url: str,
    *,
    timeout: float = 30.0,
    allow_private: bool = False,
    follow_redirects: bool = False,
    read_cap_bytes: int | None = None,
):
    """Rebinding-proof GET via httpx when the dep is available.

    Unlike ``safe_urlopen`` this version preserves TLS SNI: httpx's
    ``transport`` hook lets us rewrite the connect target while keeping
    the URL (and therefore SNI) unchanged.

    Returns the ``httpx.Response`` on success.

    When ``follow_redirects`` is true the 30x Location is re-validated
    through ``check_url`` on each hop, so a hostile server can't 302
    us to ``http://10.x.y.z``.
    """
    return safe_httpx_request(
        "GET",
        url,
        timeout=timeout,
        allow_private=allow_private,
        follow_redirects=follow_redirects,
        read_cap_bytes=read_cap_bytes,
    )


def safe_httpx_request(
    method: str,
    url: str,
    *,
    json: object | None = None,
    data: Mapping[str, object] | bytes | str | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    allow_private: bool = False,
    follow_redirects: bool = False,
    read_cap_bytes: int | None = None,
):
    """Make one rebinding-resistant HTTP request.

    The hostname is resolved and validated once, then the transport connects
    to that exact approved IP. Redirects are disabled by default; callers that
    opt in get the same validation and IP pinning on every hop. This helper is
    intentionally the common path for control-plane proxying and OAuth
    discovery/token calls. When ``read_cap_bytes`` is set, the response is
    consumed as a stream and the connection is aborted as soon as the decoded
    body crosses the limit; callers therefore get a real memory bound rather
    than a post-buffering length check.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - caller should check
        raise RuntimeError("httpx is required for safe_httpx_request") from exc

    if read_cap_bytes is not None and read_cap_bytes <= 0:
        raise ValueError("read_cap_bytes must be positive")
    if headers and any(key.lower() == "accept-encoding" for key in headers):
        raise ValueError("Accept-Encoding is managed by url_guard")

    visited: set[str] = set()
    current_url = url
    for _hop in range(5):
        if current_url in visited:
            raise RuntimeError("redirect loop detected")
        visited.add(current_url)

        verdict = check_url(current_url, allow_private=allow_private)
        if not verdict.allow:
            raise ValueError(f"url_guard rejected: {verdict.reason}")

        parsed = urlparse(current_url)
        host = parsed.hostname or ""
        resolved_ip = verdict.resolved_ip or host
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        host_literal = f"[{host}]" if ":" in host else host
        host_header = (
            f"{host_literal}:{parsed.port}"
            if parsed.port is not None and parsed.port != default_port
            else host_literal
        )

        # httpx ``transport`` takes a resolver hook. Pin every host
        # seen in this request to the single IP we approved.
        class _PinnedResolver(httpx.HTTPTransport):
            def __init__(self_inner, pinned_host: str, pinned_ip: str) -> None:  # noqa: N805
                super().__init__()
                self_inner._pinned_host = pinned_host
                self_inner._pinned_ip = pinned_ip

            def handle_request(self_inner, request):  # type: ignore[override]  # noqa: N805
                target_host = request.url.host
                if target_host == self_inner._pinned_host:
                    # Fake-ip proxy pool (198.18/15, see _FAKE_IP_NETWORK):
                    # the proxy restores the real host from the TLS SNI, so
                    # rewriting the URL host to the fake IP would drop SNI and
                    # fail the handshake. Keep the hostname URL in that case.
                    if not _is_fake_ip(self_inner._pinned_ip):
                        # httpcore uses this extension as the TLS
                        # ``server_hostname`` while the rewritten URL controls
                        # only the TCP destination. Without it, a normal public
                        # DNS result would pin the socket correctly but validate
                        # the certificate against the IP address.
                        request.extensions["sni_hostname"] = target_host
                        new_url = request.url.copy_with(host=self_inner._pinned_ip)
                        request.url = new_url
                    request.headers.setdefault("Host", target_host)
                return super().handle_request(request)

        transport = _PinnedResolver(host, resolved_ip)
        request_headers = {
            key: value for key, value in (headers or {}).items() if key.lower() != "host"
        }
        # Pin the HTTP authority to the validated URL instead of trusting a
        # caller-supplied Host header. Preserve non-default ports and IPv6
        # brackets so virtual-host routing remains standards-compliant.
        request_headers["Host"] = host_header
        with httpx.Client(transport=transport, timeout=timeout, follow_redirects=False) as client:
            request_kwargs: dict[str, Any] = {
                "headers": request_headers,
                "json": json,
            }
            if isinstance(data, (bytes, str)):
                request_kwargs["content"] = data
            else:
                request_kwargs["data"] = data

            if read_cap_bytes is None:
                resp = client.request(
                    method.upper(),
                    current_url,
                    **request_kwargs,
                )
            else:
                request = client.build_request(
                    method.upper(),
                    current_url,
                    **request_kwargs,
                )
                streamed = client.send(request, stream=True)
                try:
                    advertised_encodings = {
                        part.split(";", 1)[0].strip().lower()
                        for part in request.headers.get("Accept-Encoding", "").split(",")
                        if part.strip()
                    }
                    response_encodings = {
                        part.strip().lower()
                        for part in streamed.headers.get("Content-Encoding", "").split(",")
                        if part.strip() and part.strip().lower() != "identity"
                    }
                    unsupported_encodings = response_encodings - advertised_encodings
                    if unsupported_encodings and "*" not in advertised_encodings:
                        unsupported = ", ".join(sorted(unsupported_encodings))
                        raise httpx.DecodingError(
                            f"response uses unadvertised content encoding: {unsupported}",
                            request=streamed.request,
                        )
                    body = bytearray()
                    for chunk in streamed.iter_bytes():
                        if len(chunk) > read_cap_bytes - len(body):
                            raise ValueError(f"response exceeds {read_cap_bytes} bytes")
                        body.extend(chunk)
                    # ``iter_bytes()`` yields decoded representation bytes.
                    # Reusing the upstream Content-Encoding would make the
                    # detached response try to decode the already-decoded body
                    # a second time (and can raise httpx.DecodingError).  The
                    # original length/transfer framing is stale for the same
                    # reason, so expose headers that describe the detached
                    # in-memory response instead.
                    detached_headers = [
                        (key, value)
                        for key, value in streamed.headers.multi_items()
                        if key.lower()
                        not in {"content-encoding", "content-length", "transfer-encoding"}
                    ]
                    # Return a regular, fully-readable response whose content
                    # no longer depends on the client/transport context.
                    resp = httpx.Response(
                        status_code=streamed.status_code,
                        headers=detached_headers,
                        content=bytes(body),
                        request=streamed.request,
                    )
                finally:
                    streamed.close()
            if not follow_redirects:
                return resp
            if resp.status_code not in (301, 302, 303, 307, 308):
                return resp
            loc = resp.headers.get("Location")
            if not loc:
                return resp
            current_url = str(httpx.URL(loc, base=resp.url))
    raise RuntimeError("too many redirects")


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _as_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    stripped = host.strip("[]")
    try:
        return ipaddress.ip_address(stripped)
    except ValueError:
        return None


# 198.18.0.0/15 (RFC 2544 benchmarking) is reserved, but Clash/Surge-style
# proxies run in "fake-ip" mode where every public hostname resolves into this
# pool and the proxy forwards the real traffic onward. It never stands for an
# actual LAN/private resource, so it must be treated as external — otherwise a
# fake-ip proxy environment can never reach any external MCP/OAuth endpoint.
_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


def _is_fake_ip(value: str | None) -> bool:
    """True when ``value`` falls in the fake-ip proxy pool (198.18/15)."""
    if not value:
        return False
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return isinstance(ip, ipaddress.IPv4Address) and ip in _FAKE_IP_NETWORK


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Fake-ip proxy pool (see _FAKE_IP_NETWORK above) is not private.
    if isinstance(ip, ipaddress.IPv4Address) and ip in _FAKE_IP_NETWORK:
        return False
    # IPv4-mapped IPv6 (``::ffff:10.0.0.1``) and IPv4-compatible
    # (``::10.0.0.1``) both look like public IPv6 to Python's
    # ``is_private`` but route to the embedded IPv4 address.  Unwrap
    # and re-check on the embedded v4.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        v4 = ip.ipv4_mapped
        return _is_private_ip(v4)
    # Some ipaddress builds expose ``.sixtofour`` on 2002::/16
    # (6to4) and ``.teredo`` on 2001::/32 (Teredo); both tunnel an
    # arbitrary v4 target through a v6 address.  Refuse them.
    if isinstance(ip, ipaddress.IPv6Address):
        sixto4 = getattr(ip, "sixtofour", None)
        if sixto4 is not None and _is_private_ip(sixto4):
            return True
        teredo = getattr(ip, "teredo", None)
        if teredo is not None:
            client = teredo[1] if isinstance(teredo, tuple) else None
            if client is not None and _is_private_ip(client):
                return True
    return bool(
        ip.is_private  # 10.* / 172.16-31.* / 192.168.*
        or ip.is_loopback  # 127.* / ::1
        or ip.is_link_local  # 169.254.* / fe80::
        or ip.is_multicast  # 224+ / ff00::
        or ip.is_reserved
        or ip.is_unspecified  # 0.0.0.0 / ::
    )


def _resolve_all(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return []
    out = []
    seen: set[str] = set()
    for info in infos:
        # sockaddr[0] is the host address; typed str | int because the
        # sockaddr tuple shape differs for IPv4/IPv6, but element 0 is
        # always the address string. Coerce so the set / _as_ip see str.
        addr = str(info[4][0])
        if addr in seen:
            continue
        seen.add(addr)
        ip = _as_ip(addr)
        if ip is not None:
            out.append(ip)
    return out
