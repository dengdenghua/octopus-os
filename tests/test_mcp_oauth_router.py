"""Tests for the MCP OAuth endpoints (POST authorize / GET callback /
GET status / DELETE tokens) and the HttpMCPClient transport wiring that
consumes the resulting token.

runtime/adapters/mcp_client/oauth.py and oauth_discovery.py were complete,
independently-tested modules with zero production callers — these tests
cover the router wiring that closes that gap.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from runtime.adapters.mcp_client import oauth
from runtime.adapters.mcp_client.oauth_discovery import OAuthEndpoints
from runtime.platform.ui.app import create_app


@pytest.fixture(autouse=True)
def _isolated_oauth_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Hermetic: point the singleton at a tmp dir so tests never touch the
    # real ~/.echo store, and reset it before/after so state doesn't
    # leak between tests (mirrors tests/test_mcp_oauth.py's pattern).
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    oauth.reset_oauth_store_for_tests()
    yield
    oauth.reset_oauth_store_for_tests()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.chdir(tmp_path)
    return TestClient(create_app())


@pytest.fixture
def secured_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, dict[str, str]]:
    from runtime.safety.auth import Identity, IdentityStore

    monkeypatch.chdir(tmp_path)
    store = IdentityStore()
    store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    app = create_app(cocoloop_require_auth=True, cocoloop_identity_store=store)
    return TestClient(app), {"Authorization": "Bearer sk-alice"}


_ENDPOINTS = OAuthEndpoints(
    issuer="https://auth.example.com",
    authorize_url="https://auth.example.com/authorize",
    token_url="https://auth.example.com/token",
    registration_url="https://auth.example.com/register",
    scopes=("tools:read", "tools:write"),
)


class TestOAuthAuthorize:
    def test_happy_path_discovers_and_registers_client(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "runtime.adapters.mcp_client.oauth_discovery.discover",
            lambda url, **kw: _ENDPOINTS,
        )
        monkeypatch.setattr(
            "runtime.adapters.mcp_client.oauth_discovery.register_client",
            lambda reg_url, *, redirect_uri, **kw: "dcr-client-id",
        )

        r = client.post(
            "/api/mcp/oauth/authorize",
            json={"server": "acme", "url": "https://mcp.acme.example/v1"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["callback_transport"] == "standard"
        url = body["authorize_url"]
        assert url.startswith("https://auth.example.com/authorize?")
        assert "client_id=dcr-client-id" in url
        assert "code_challenge_method=S256" in url
        assert "scope=tools%3Aread+tools%3Awrite" in url

        # The dynamically-registered client_id is cached per-issuer.
        store = oauth.get_oauth_store()
        assert store.get_client(_ENDPOINTS.issuer) == "dcr-client-id"

    def test_reuses_cached_client_id_without_reregistering(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "runtime.adapters.mcp_client.oauth_discovery.discover",
            lambda url, **kw: _ENDPOINTS,
        )
        calls = {"n": 0}

        def _register(*a, **kw):
            calls["n"] += 1
            return "should-not-be-used"

        monkeypatch.setattr(
            "runtime.adapters.mcp_client.oauth_discovery.register_client",
            _register,
        )
        oauth.get_oauth_store().save_client(_ENDPOINTS.issuer, "cached-client-id")

        r = client.post(
            "/api/mcp/oauth/authorize",
            json={"server": "acme", "url": "https://mcp.acme.example/v1"},
        )
        assert r.status_code == 200
        assert "client_id=cached-client-id" in r.json()["authorize_url"]
        assert calls["n"] == 0

    def test_tdx_requires_desktop_deep_link_callback(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tdx = OAuthEndpoints(
            issuer="https://auth.tdx.com.cn",
            authorize_url=("https://auth.tdx.com.cn/tdx-oauth/page_workbuddy_oauth.html"),
            token_url="https://auth.tdx.com.cn/token",
            registration_url="https://auth.tdx.com.cn/register",
            scopes=("mcp.read", "mcp.write"),
        )
        monkeypatch.setattr(
            "runtime.adapters.mcp_client.oauth_discovery.discover",
            lambda url, **kw: tdx,
        )
        monkeypatch.setattr(
            "runtime.adapters.mcp_client.oauth_discovery.register_client",
            lambda *args, **kwargs: "tdx-client-id",
        )

        body = client.post(
            "/api/mcp/oauth/authorize",
            json={"server": "tdx-finance", "url": "https://tdx.example/mcp"},
        ).json()

        assert body["ok"] is True
        assert body["callback_transport"] == "desktop-deep-link"

    def test_discovery_failure_returns_400(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "runtime.adapters.mcp_client.oauth_discovery.discover",
            lambda url, **kw: None,
        )
        r = client.post(
            "/api/mcp/oauth/authorize",
            json={"server": "acme", "url": "https://mcp.acme.example/v1"},
        )
        assert r.status_code == 400

    def test_no_client_id_and_no_registration_endpoint_returns_400(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        no_dcr = OAuthEndpoints(
            issuer="https://auth.example.com",
            authorize_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
            registration_url="",  # no DCR support
        )
        monkeypatch.setattr(
            "runtime.adapters.mcp_client.oauth_discovery.discover",
            lambda url, **kw: no_dcr,
        )
        r = client.post(
            "/api/mcp/oauth/authorize",
            json={"server": "acme", "url": "https://mcp.acme.example/v1"},
        )
        assert r.status_code == 400

    def test_requires_server_and_url(self, client: TestClient) -> None:
        assert client.post("/api/mcp/oauth/authorize", json={"url": "https://x"}).status_code == 400
        assert client.post("/api/mcp/oauth/authorize", json={"server": "s"}).status_code == 400


class TestOAuthCallback:
    def _start_pending(self) -> str:
        store = oauth.get_oauth_store()
        return store.start_pending(
            server="acme",
            code_verifier="verifier",
            redirect_uri="http://testserver/api/mcp/oauth/callback",
            token_url="https://auth.example.com/token",
            client_id="cid",
        )

    def test_exchanges_code_and_saves_tokens(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state = self._start_pending()
        monkeypatch.setattr(
            "runtime.adapters.mcp_client.oauth.exchange_code",
            lambda **kw: {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600},
        )

        r = client.get(f"/api/mcp/oauth/callback?code=C&state={state}")
        assert r.status_code == 200
        assert "Authorized acme" in r.text

        store = oauth.get_oauth_store()
        assert store.has_tokens("acme")
        assert store.bearer("acme") == "AT"

    def test_state_is_single_use_csrf_protection(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state = self._start_pending()
        monkeypatch.setattr(
            "runtime.adapters.mcp_client.oauth.exchange_code",
            lambda **kw: {"access_token": "AT", "expires_in": 3600},
        )
        r1 = client.get(f"/api/mcp/oauth/callback?code=C&state={state}")
        assert "Authorized" in r1.text

        # Replaying the same state (e.g. a captured redirect URL) must
        # not exchange a second time — the pending record is consumed.
        r2 = client.get(f"/api/mcp/oauth/callback?code=C&state={state}")
        assert "Invalid or expired" in r2.text

    def test_unknown_state_shows_error_page(self, client: TestClient) -> None:
        r = client.get("/api/mcp/oauth/callback?code=C&state=bogus")
        assert r.status_code == 200
        assert "Invalid or expired" in r.text

    def test_provider_error_param_shows_error_page(self, client: TestClient) -> None:
        state = self._start_pending()
        r = client.get(
            f"/api/mcp/oauth/callback?error=access_denied&state={state}",
        )
        assert r.status_code == 200
        assert "Authorization failed" in r.text
        replay = client.get(f"/api/mcp/oauth/callback?code=C&state={state}")
        assert "Invalid or expired" in replay.text

    def test_provider_error_without_state_is_rejected(self, client: TestClient) -> None:
        r = client.get("/api/mcp/oauth/callback?error=access_denied")
        assert r.status_code == 200
        assert "Missing code/state" in r.text

    def test_token_exchange_exception_shows_error_page_not_500(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        state = self._start_pending()

        def _boom(**kw):
            raise RuntimeError("token endpoint unreachable")

        monkeypatch.setattr("runtime.adapters.mcp_client.oauth.exchange_code", _boom)
        r = client.get(f"/api/mcp/oauth/callback?code=C&state={state}")
        assert r.status_code == 200
        assert "Token exchange failed" in r.text

    def test_tdx_desktop_deep_link_completes_full_callback_chain(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        node = shutil.which("node")
        if node is None:
            pytest.skip("Node.js is required for the desktop bridge contract")
        state = self._start_pending()
        root = Path(__file__).resolve().parents[1]
        bridge = root / "frontend" / "electron" / "mcp-oauth-deep-link.cjs"
        fixture = {
            "sourceURL": (
                "https://auth.tdx.com.cn/tdx-oauth/page_workbuddy_oauth.html?client_id=test"
            ),
            "deepLinkURL": ("workbuddy://mcp/oauth/callback?code=TDX-CODE&state=" + state),
            "backendBaseURL": "http://127.0.0.1:8000",
        }
        script = (
            "const h=require(process.argv[1]);"
            "const input=JSON.parse(process.argv[2]);"
            "process.stdout.write(h.buildMcpOAuthCallbackURL(input)||'');"
        )
        completed = subprocess.run(
            [node, "-e", script, str(bridge), json.dumps(fixture)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        parsed = urlsplit(completed)
        assert parsed.path == "/api/mcp/oauth/callback"

        monkeypatch.setattr(
            "runtime.adapters.mcp_client.oauth.exchange_code",
            lambda **kw: {
                "access_token": "TDX-ACCESS",
                "refresh_token": "TDX-REFRESH",
                "expires_in": 3600,
            },
        )
        response = client.get(f"{parsed.path}?{parsed.query}")

        assert response.status_code == 200
        assert "Authorized acme" in response.text
        assert oauth.get_oauth_store().bearer("acme") == "TDX-ACCESS"
        assert client.get("/api/mcp/oauth/status?server=acme").json() == {
            "server": "acme",
            "authorized": True,
        }


class TestOAuthStatusAndForget:
    def test_status_reflects_token_presence(self, client: TestClient) -> None:
        assert client.get("/api/mcp/oauth/status?server=acme").json() == {
            "server": "acme",
            "authorized": False,
        }
        oauth.get_oauth_store().save_tokens(
            "acme",
            {"access_token": "AT", "expires_in": 3600},
            token_url="https://t",
            client_id="cid",
        )
        assert client.get("/api/mcp/oauth/status?server=acme").json()["authorized"] is True

    def test_forget_revokes_tokens(self, client: TestClient) -> None:
        oauth.get_oauth_store().save_tokens(
            "acme",
            {"access_token": "AT", "expires_in": 3600},
            token_url="https://t",
            client_id="cid",
        )
        r = client.delete("/api/mcp/oauth/acme")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert client.get("/api/mcp/oauth/status?server=acme").json()["authorized"] is False


class TestHttpMCPClientBearerInjection:
    @pytest.fixture(autouse=True)
    def _needs_mcp_sdk(self) -> None:
        # HttpMCPClient imports the mcp package; the cross-platform CI
        # matrix installs only [dev,web,serve].
        pytest.importorskip("mcp")

    def test_injects_bearer_token_from_oauth_store(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.adapters.mcp_client.client import HttpMCPClient
        from runtime.adapters.mcp_client.types import MCPServerConfig

        oauth.get_oauth_store().save_tokens(
            "acme",
            {"access_token": "OAUTH-TOKEN", "expires_in": 3600},
            token_url="https://t",
            client_id="cid",
        )
        config = MCPServerConfig(
            name="acme",
            transport="http",
            url="https://mcp.acme.example/v1",
        )
        client_obj = HttpMCPClient.__new__(HttpMCPClient)
        client_obj.config = config

        captured: dict[str, object] = {}

        def _fake_streamable(url, *, http_client=None, **kw):
            captured["http_client"] = http_client
            raise RuntimeError("not actually connecting")

        monkeypatch.setattr(
            "mcp.client.streamable_http.streamable_http_client",
            _fake_streamable,
        )
        with pytest.raises(RuntimeError):
            client_obj._transport()
        assert captured["http_client"] is not None
        assert captured["http_client"].headers.get("Authorization") == "Bearer OAUTH-TOKEN"

    def test_explicit_authorization_header_wins_over_oauth(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.adapters.mcp_client.client import HttpMCPClient
        from runtime.adapters.mcp_client.types import MCPServerConfig

        oauth.get_oauth_store().save_tokens(
            "acme",
            {"access_token": "OAUTH-TOKEN", "expires_in": 3600},
            token_url="https://t",
            client_id="cid",
        )
        config = MCPServerConfig(
            name="acme",
            transport="http",
            url="https://mcp.acme.example/v1",
            headers={"Authorization": "Bearer MANUAL-TOKEN"},
        )
        client_obj = HttpMCPClient.__new__(HttpMCPClient)
        client_obj.config = config

        captured: dict[str, object] = {}

        def _fake_streamable(url, *, http_client=None, **kw):
            captured["http_client"] = http_client
            raise RuntimeError("not actually connecting")

        monkeypatch.setattr(
            "mcp.client.streamable_http.streamable_http_client",
            _fake_streamable,
        )
        with pytest.raises(RuntimeError):
            client_obj._transport()
        assert captured["http_client"] is not None
        assert captured["http_client"].headers.get("Authorization") == "Bearer MANUAL-TOKEN"

    def test_no_header_when_server_never_authorized(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.adapters.mcp_client.client import HttpMCPClient
        from runtime.adapters.mcp_client.types import MCPServerConfig

        config = MCPServerConfig(
            name="never-authorized",
            transport="http",
            url="https://mcp.example/v1",
        )
        client_obj = HttpMCPClient.__new__(HttpMCPClient)
        client_obj.config = config

        captured: dict[str, object] = {}

        def _fake_streamable(url, *, http_client=None, **kw):
            captured["http_client"] = http_client
            raise RuntimeError("not actually connecting")

        monkeypatch.setattr(
            "mcp.client.streamable_http.streamable_http_client",
            _fake_streamable,
        )
        with pytest.raises(RuntimeError):
            client_obj._transport()
        # 服务器从未授权:即使 2.x 总是注入一个 httpx2 client,也不该带
        # Authorization 头(也不该带任何认证编排层的头)。
        assert captured["http_client"] is not None
        assert captured["http_client"].headers.get("Authorization") is None


class TestOAuthEndpointsRequireAuthWhenEnabled:
    """The new routes are declared on the same router as /api/mcp/config
    (dependencies=[Depends(_auth_dep)] at the APIRouter level), so they
    should inherit auth gating automatically — pin that explicitly. The
    callback route is the deliberate exception (see test_callback_is_
    exempt_from_auth below)."""

    def test_authorize_and_status_require_auth(
        self,
        secured_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        client, headers = secured_client
        assert (
            client.post(
                "/api/mcp/oauth/authorize",
                json={"server": "acme", "url": "https://mcp.acme.example"},
            ).status_code
            == 401
        )
        assert client.get("/api/mcp/oauth/status?server=acme").status_code == 401
        assert client.delete("/api/mcp/oauth/acme").status_code == 401

        # With credentials the request proceeds past auth (status/forget
        # don't touch the network, so they're safe to check end-to-end).
        assert (
            client.get(
                "/api/mcp/oauth/status?server=acme",
                headers=headers,
            ).status_code
            == 200
        )

    def test_callback_is_exempt_from_auth(
        self,
        secured_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        """The callback is reached by the OAuth *provider* redirecting the
        user's own browser — a top-level navigation that cannot carry our
        Authorization header. Gating it behind auth would deadlock the
        entire flow in auth-on deployments (the callback can never be
        called, so the user can never finish authorizing). Its actual
        credential is the single-use, TTL-bounded ``state`` param, checked
        in the handler itself — verified separately in TestOAuthCallback.
        """
        client, _headers = secured_client
        r = client.get("/api/mcp/oauth/callback?code=C&state=bogus")
        assert r.status_code == 200  # not 401 — reaches the handler
        assert "Invalid or expired" in r.text  # ...and the handler's own check still applies
        assert "echo-mcp-oauth" in r.text  # popup postMessage page, not a JSON error

