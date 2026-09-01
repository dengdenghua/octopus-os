from __future__ import annotations

from dataclasses import dataclass

import pytest

from runtime.platform.plugins import _secure_fetch
from runtime.safety.auth.url_guard import URLVerdict


@dataclass
class _Response:
    status_code: int
    headers: dict[str, str]
    content: bytes = b""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _allow_public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _secure_fetch,
        "check_url",
        lambda url, **kwargs: URLVerdict(True, url, resolved_ip="203.0.113.10"),
    )


def test_rejects_plain_http_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def _unexpected(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must not run")

    monkeypatch.setattr(_secure_fetch, "safe_httpx_request", _unexpected)

    with pytest.raises(ValueError, match="must use https"):
        _secure_fetch.fetch_public_https_bytes(
            "http://example.com/catalog.json",
            timeout=1,
            max_bytes=100,
        )
    assert called is False


def test_revalidates_redirect_and_rejects_https_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_public_dns(monkeypatch)
    monkeypatch.setattr(
        _secure_fetch,
        "safe_httpx_request",
        lambda *a, **kw: _Response(302, {"Location": "http://evil.example/steal"}),
    )

    with pytest.raises(ValueError, match="must use https"):
        _secure_fetch.fetch_public_https_bytes(
            "https://market.example/archive.tar.gz",
            timeout=1,
            max_bytes=100,
        )


def test_rejects_declared_or_actual_oversize_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_public_dns(monkeypatch)
    responses = iter(
        [
            _Response(200, {"Content-Length": "101"}, b"x"),
            _Response(200, {}, b"x" * 101),
        ]
    )
    monkeypatch.setattr(
        _secure_fetch,
        "safe_httpx_request",
        lambda *a, **kw: next(responses),
    )

    for _ in range(2):
        with pytest.raises(ValueError, match="exceeds 100 bytes"):
            _secure_fetch.fetch_public_https_bytes(
                "https://market.example/catalog.json",
                timeout=1,
                max_bytes=100,
            )

