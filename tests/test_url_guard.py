"""Implementation note."""

from __future__ import annotations

import socket

import pytest

from runtime.safety.auth import check_url, is_safe_url

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestScheme:
    def test_http_allowed(self):
        v = check_url("http://example.com/path")
        assert v.allow

    def test_https_allowed(self):
        v = check_url("https://example.com/path")
        assert v.allow

    @pytest.mark.parametrize(
        "scheme",
        [
            "file:///etc/passwd",
            "ftp://internal.srv/data",
            "gopher://localhost",
            "javascript:alert(1)",
            "data:text/html,<script>",
            "ssh://root@10.0.0.1",
        ],
    )
    def test_unsafe_scheme_blocked(self, scheme):
        v = check_url(scheme)
        assert not v.allow
        assert "scheme" in v.reason

    def test_empty_url_blocked(self):
        v = check_url("")
        assert not v.allow
        assert "empty" in v.reason

    def test_malformed_url_blocked(self):
        v = check_url("not a url at all")
        # Implementation note.
        assert not v.allow


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestDirectIP:
    @pytest.mark.parametrize(
        "url,reason_kw",
        [
            ("http://127.0.0.1/x", "private_ip"),
            ("http://127.0.0.1:8080/admin", "private_ip"),
            ("http://169.254.169.254/latest/meta-data/", "private_ip"),
            ("http://10.0.0.1/", "private_ip"),
            ("http://172.16.1.2/", "private_ip"),
            ("http://172.31.255.254/", "private_ip"),
            ("http://192.168.1.1/", "private_ip"),
            ("http://0.0.0.0/", "private_ip"),
            ("http://[::1]/", "private_ip"),
            ("http://[fe80::1]/", "private_ip"),
            ("http://[fd00:ec2::254]/", "blocked_host"),  # Implementation note.
        ],
    )
    def test_private_ip_blocked(self, url, reason_kw):
        v = check_url(url)
        assert not v.allow
        assert reason_kw in v.reason

    def test_public_ip_allowed(self):
        v = check_url("http://8.8.8.8/")
        assert v.allow

    def test_allow_private_flag_lets_through(self):
        """Implementation note."""
        v = check_url("http://10.0.0.1/internal-api", allow_private=True)
        assert v.allow


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBlockedHostnames:
    @pytest.mark.parametrize(
        "url,reason_kw",
        [
            ("http://localhost/x", "blocked_host"),
            ("http://LOCALHOST/x", "blocked_host"),
            ("http://metadata.google.internal/", "blocked_host"),
            ("http://metadata.azure.internal/", "blocked_host"),
        ],
    )
    def test_special_hosts_blocked(self, url, reason_kw):
        v = check_url(url)
        assert not v.allow
        assert reason_kw in v.reason

    @pytest.mark.parametrize(
        "url",
        [
            "http://my-app.internal/api",
            "http://foo.local/",
            "http://svc.svc.cluster.local/",
            "http://host.lan/",
        ],
    )
    def test_internal_suffixes_blocked(self, url):
        v = check_url(url)
        assert not v.allow
        assert "blocked_suffix" in v.reason

    def test_public_subdomain_of_internal_not_matched(self, monkeypatch):
        """Implementation note."""
        import runtime.safety.auth.url_guard as guard

        def fake_resolve(*a, **kw):
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("142.250.72.14", 0),  # Implementation note.
                )
            ]

        monkeypatch.setattr(guard.socket, "getaddrinfo", fake_resolve)

        v = check_url("http://internal.example.com/")
        assert v.allow


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestDNSRebinding:
    def test_hostname_resolving_to_private_blocked(self, monkeypatch):
        """Implementation note."""
        import runtime.safety.auth.url_guard as guard

        def fake_getaddrinfo(host, *a, **kw):
            # Implementation note.
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("10.0.0.5", 0),
                )
            ]

        monkeypatch.setattr(guard.socket, "getaddrinfo", fake_getaddrinfo)

        v = check_url("http://attacker-example.com/")
        assert not v.allow
        assert "dns_resolves_to_private" in v.reason

    def test_hostname_resolving_to_public_allowed(self, monkeypatch):
        import runtime.safety.auth.url_guard as guard

        def fake_getaddrinfo(host, *a, **kw):
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("142.250.72.14", 0),  # Implementation note.
                )
            ]

        monkeypatch.setattr(guard.socket, "getaddrinfo", fake_getaddrinfo)

        v = check_url("http://legit.example/")
        assert v.allow

    def test_dns_failure_fails_closed(self, monkeypatch):
        """Implementation note."""
        import runtime.safety.auth.url_guard as guard

        def fake_getaddrinfo(host, *a, **kw):
            raise socket.gaierror("name not known")

        monkeypatch.setattr(guard.socket, "getaddrinfo", fake_getaddrinfo)

        v = check_url("http://nonexistent.example/")
        assert not v.allow
        assert "dns_resolution_failed" in v.reason

    def test_resolve_dns_flag_skipped(self, monkeypatch):
        """Implementation note."""
        import runtime.safety.auth.url_guard as guard

        call_count = [0]

        def fake_getaddrinfo(*a, **kw):
            call_count[0] += 1
            raise socket.gaierror("should not be called")

        monkeypatch.setattr(guard.socket, "getaddrinfo", fake_getaddrinfo)

        v = check_url("http://example.com/", resolve_dns=False)
        assert v.allow
        assert call_count[0] == 0


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestIsSafeURL:
    def test_shortcut_matches_check_url_allow(self):
        assert is_safe_url("http://8.8.8.8/") == check_url("http://8.8.8.8/").allow
        assert is_safe_url("http://127.0.0.1/") == check_url("http://127.0.0.1/").allow

    def test_allow_private_shortcut(self):
        assert is_safe_url("http://10.0.0.1/", allow_private=True)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class _FakeHTTPX:
    """Implementation note."""

    def __init__(self):
        self.calls: list[str] = []

    def get(self, url):
        self.calls.append(url)
        resp = type("Resp", (), {})()
        resp.url = url
        resp.status_code = 200
        resp.text = "ok"
        resp.headers = {"content-type": "text/html"}
        return resp

    def close(self):
        pass


class TestFetchURLIntegration:
    def test_ssrf_url_blocked_before_httpx(self):
        from runtime.execution.suckers.web_skills import _fetch_url

        fake = _FakeHTTPX()
        result = _fetch_url(
            url="http://169.254.169.254/latest/meta-data/",
            client=fake,
        )
        assert "error" in result
        assert "ssrf_blocked" in result["error"]
        assert result.get("blocked") is True
        # Implementation note.
        assert fake.calls == []

    def test_public_url_passes_through(self, monkeypatch):
        """Implementation note."""
        import runtime.safety.auth.url_guard as guard

        def fake_resolve(host, *a, **kw):
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("142.250.72.14", 0),
                )
            ]

        monkeypatch.setattr(guard.socket, "getaddrinfo", fake_resolve)

        from runtime.execution.suckers.web_skills import _fetch_url

        fake = _FakeHTTPX()
        result = _fetch_url(url="https://example.com/", client=fake)
        assert "error" not in result
        assert fake.calls == ["https://example.com/"]

    def test_allow_private_flag_lets_internal_through(self):
        from runtime.execution.suckers.web_skills import _fetch_url

        fake = _FakeHTTPX()
        result = _fetch_url(
            url="http://10.0.0.1/internal-api",
            client=fake,
            allow_private=True,
        )
        assert "error" not in result
        assert fake.calls == ["http://10.0.0.1/internal-api"]

    def test_file_scheme_blocked(self):
        from runtime.execution.suckers.web_skills import _fetch_url

        fake = _FakeHTTPX()
        result = _fetch_url(url="file:///etc/passwd", client=fake)
        assert "error" in result
        assert "scheme" in result["error"].lower() or "ssrf" in result["error"].lower()
        assert fake.calls == []
