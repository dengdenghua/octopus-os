"""OS tools require live request credentials, never model-supplied identity."""

from __future__ import annotations

import asyncio
import base64
import contextvars
import copy
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from appliance.account_security import (
    ApplianceAccountSecurity,
    ApplianceSessionRevocationMiddleware,
    _scope_token,
    _strip_stale_credentials,
)
from appliance.agent_api.authorization import authorization_token, websocket_token
from appliance.agent_authorization import (
    ApplianceAgentAuthorizationError,
    ApplianceAgentAuthorizationMiddleware,
    require_appliance_actor,
)
from appliance.approval import HighRiskApprovalService
from appliance.audit import ApplianceAudit
from appliance.auth import load_or_bootstrap_auth
from appliance.security import ApplianceAuthenticator
from runtime.platform.capabilities.tenant_context import use_capability_scope
from runtime.platform.process.session import Session, session_scope
from runtime.platform.process.session_executor import SessionExecutor
from runtime.safety.auth.identity import encode_jwt_hs256
from runtime.safety.auth.scope import TenantScope

_SECRET = "agent-authorization-test-secret"


class _Security:
    def __init__(self):
        self.floor = 0
        self.active = True

    def claims_are_current(self, claims):
        return self.active and claims["iat"] >= self.floor


def _token(actor="local:alice", *, secret=_SECRET, **overrides):
    claims = {"sub": actor, "iat": int(time.time()), "exp": time.time() + 300, "iss": "echo-agent"}
    claims.update(overrides)
    return encode_jwt_hs256(claims, secret=secret)


def _encoded(token):
    return base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")


def _result():
    try:
        return {"actor": require_appliance_actor()}
    except ApplianceAgentAuthorizationError:
        return {"error": "appliance_authorization_required"}


def _app(security=None, *, secret=_SECRET):
    app = FastAPI()
    live = security or _Security()

    @app.get("/public")
    def public():
        return {"public": True}

    @app.post("/tool")
    def tool(_body: dict):
        result = _result()
        return JSONResponse(result, status_code=403 if "error" in result else 200)

    @app.websocket("/tool-ws")
    async def tool_ws(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                await ws.receive_text()
                await ws.send_json(_result())
        except WebSocketDisconnect:
            pass

    app.add_middleware(
        ApplianceAgentAuthorizationMiddleware,
        authenticator=ApplianceAuthenticator(secret),
        account_security=live,
    )
    return app, live


def test_http_uses_credentials_and_resets_between_requests():
    app, _security = _app()
    token = _token()
    with TestClient(app) as client:
        response = client.post(
            "/tool",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "actor": "local:admin",
                "tenant_id": "admin",
                "owner_actor_id": "local:admin",
                "_echo_authoritative_tenant_scope": {"actor_id": "local:admin"},
            },
        )
        assert response.json() == {"actor": "local:alice"}
        assert token not in response.text
        assert client.post("/tool", json={"actor": "local:admin"}).status_code == 403
        assert client.get("/public").json() == {"public": True}
    with pytest.raises(PermissionError):
        require_appliance_actor()


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": 1},
        {"exp": float("nan")},
        {"exp": True},
        {"iat": None},
        {"iat": True},
        {"iss": "other"},
    ],
)
def test_invalid_or_expired_signed_claims_do_not_authorize(overrides):
    app, _security = _app()
    with TestClient(app) as client:
        assert (
            client.post(
                "/tool", json={}, headers={"Authorization": f"Bearer {_token(**overrides)}"}
            ).status_code
            == 403
        )


def test_bad_bearer_never_falls_back_to_valid_cookie_or_query_identity():
    app, _security = _app()
    with TestClient(app) as client:
        client.cookies.set("echo_session", _token())
        assert client.post("/tool", json={}).json() == {"actor": "local:alice"}
        assert (
            client.post(
                "/tool", json={}, headers={"Authorization": f"Bearer {_token(secret='wrong')}"}
            ).status_code
            == 403
        )
        client.cookies.clear()
        assert client.post(f"/tool?token={_token()}", json={}).status_code == 403


@pytest.mark.parametrize(
    "session,scope,allowed",
    [
        (Session(actor="local:alice"), None, True),
        (Session(actor="local:admin"), None, False),
        (Session(actor="local:alice", metadata={"tenant_id": "incomplete"}), None, False),
        (
            Session(
                actor="local:alice", metadata={"tenant_id": "t", "owner_actor_id": "local:alice"}
            ),
            None,
            True,
        ),
        (None, TenantScope("t", "local:admin"), False),
        (None, TenantScope("t", "local:alice", allow_cross_tenant=True), False),
        (
            Session(
                actor="local:alice",
                metadata={
                    "_echo_authoritative_tenant_scope": {
                        "tenant_id": "t",
                        "actor_id": "local:admin",
                    }
                },
            ),
            None,
            False,
        ),
    ],
)
def test_active_runtime_actor_must_match_verified_appliance_identity(session, scope, allowed):
    app, _security = _app()

    @app.get("/scoped")
    def scoped():
        if session is not None:
            with session_scope(session):
                return _result()
        with use_capability_scope(scope):
            return _result()

    with TestClient(app) as client:
        response = client.get("/scoped", headers={"Authorization": f"Bearer {_token()}"})
        assert ("actor" in response.json()) is allowed


def test_restored_runtime_session_is_not_an_appliance_credential():
    session = Session(
        actor="local:admin",
        metadata={
            "tenant_id": "t",
            "owner_actor_id": "local:admin",
            "_echo_authoritative_tenant_scope": {"tenant_id": "t", "actor_id": "local:admin"},
        },
    )
    with session_scope(session), pytest.raises(ApplianceAgentAuthorizationError):
        require_appliance_actor()


def test_copied_worker_context_retains_live_checks_but_plain_thread_has_no_authority():
    app, security = _app()
    retained = []

    @app.get("/workers")
    async def workers():
        captured = contextvars.copy_context()
        retained.append(captured)
        assert await asyncio.to_thread(require_appliance_actor) == "local:alice"
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(_result).result() == {"error": "appliance_authorization_required"}
            assert pool.submit(captured.run, require_appliance_actor).result() == "local:alice"
        with SessionExecutor(max_workers=1) as pool:
            assert pool.submit(require_appliance_actor).result() == "local:alice"
        return {"actor": require_appliance_actor()}

    with TestClient(app) as client:
        assert client.get("/workers", headers={"Authorization": f"Bearer {_token()}"}).json() == {
            "actor": "local:alice"
        }
    security.floor = int(time.time()) + 1
    assert retained[0].run(_result) == {"error": "appliance_authorization_required"}
    assert _result() == {"error": "appliance_authorization_required"}


def test_expiry_is_rechecked_for_already_running_context(monkeypatch):
    app, _security = _app()
    captured = []
    expires = time.time() + 60

    @app.get("/capture")
    async def capture():
        captured.append(contextvars.copy_context())
        return _result()

    with TestClient(app) as client:
        assert (
            "actor"
            in client.get(
                "/capture", headers={"Authorization": f"Bearer {_token(exp=expires)}"}
            ).json()
        )
    monkeypatch.setattr("appliance.agent_authorization.time.time", lambda: expires + 1)
    assert captured[0].run(_result) == {"error": "appliance_authorization_required"}


def test_two_apps_with_same_actor_cannot_use_each_others_service_authority():
    app_a, security_a = _app()
    app_b, _security_b = _app()

    def read_a_library():
        try:
            actor = require_appliance_actor(expected_security=security_a)
        except ApplianceAgentAuthorizationError:
            return JSONResponse({"error": "wrong_service_authority"}, status_code=403)
        return {"actor": actor, "library": "a"}

    app_a.get("/a-library")(read_a_library)
    app_b.get("/a-library")(read_a_library)
    headers = {"Authorization": f"Bearer {_token('local:admin')}"}
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        assert client_a.get("/a-library", headers=headers).json() == {
            "actor": "local:admin",
            "library": "a",
        }
        assert client_b.post("/tool", json={}, headers=headers).json() == {"actor": "local:admin"}
        assert client_b.get("/a-library", headers=headers).status_code == 403


def test_concurrent_requests_keep_separate_authority_and_reset():
    observed = []
    security = _Security()

    async def exercise():
        entered = 0
        ready = asyncio.Event()

        async def downstream(scope, receive, send):
            nonlocal entered
            entered += 1
            if entered == 2:
                ready.set()
            await ready.wait()
            observed.append((scope["path"], require_appliance_actor(expected_security=security)))

        middleware = ApplianceAgentAuthorizationMiddleware(
            downstream, authenticator=ApplianceAuthenticator(_SECRET), account_security=security
        )
        await asyncio.gather(
            *[
                middleware(
                    {
                        "type": "http",
                        "path": actor,
                        "headers": [(b"authorization", f"Bearer {_token(actor)}".encode())],
                    },
                    None,
                    None,
                )
                for actor in ("local:alice", "local:bob")
            ]
        )
        assert _result() == {"error": "appliance_authorization_required"}

    asyncio.run(exercise())
    assert sorted(observed) == [("local:alice", "local:alice"), ("local:bob", "local:bob")]


def test_middleware_resets_context_even_when_downstream_raises():
    async def downstream(scope, receive, send):
        assert require_appliance_actor() == "local:alice"
        raise RuntimeError("downstream failed")

    middleware = ApplianceAgentAuthorizationMiddleware(
        downstream, authenticator=ApplianceAuthenticator(_SECRET), account_security=_Security()
    )

    async def exercise():
        scope = {"type": "http", "headers": [(b"authorization", f"Bearer {_token()}".encode())]}
        with pytest.raises(RuntimeError, match="downstream failed"):
            await middleware(scope, None, None)
        assert _result() == {"error": "appliance_authorization_required"}

    asyncio.run(exercise())


@pytest.mark.parametrize("protocol", ["bearer", "bearer.b64"])
def test_websocket_uses_strict_token_transport_and_rechecks_live_revocation(protocol):
    app, security = _app()
    token = _token()
    value = _encoded(token) if protocol == "bearer.b64" else token
    with (
        TestClient(app) as client,
        client.websocket_connect("/tool-ws", subprotocols=[protocol, value]) as ws,
    ):
        ws.send_text("read")
        assert ws.receive_json() == {"actor": "local:alice"}
        security.active = False
        ws.send_text("read after revocation")
        assert ws.receive_json() == {"error": "appliance_authorization_required"}


@pytest.mark.parametrize("encoded", ["not%%%base64", "_w"])
def test_malformed_base64_websocket_protocol_is_not_authority(encoded):
    app, _security = _app()
    with (
        TestClient(app) as client,
        client.websocket_connect("/tool-ws", subprotocols=["bearer.b64", encoded]) as ws,
    ):
        ws.send_text("read")
        assert ws.receive_json() == {"error": "appliance_authorization_required"}


def test_revocation_removes_encoded_token_from_header_and_parsed_asgi_protocols():
    token = _token()
    encoded = _encoded(token)
    scope = {
        "type": "websocket",
        "headers": [(b"sec-websocket-protocol", f"bearer.b64, {encoded}".encode())],
        "subprotocols": ["bearer.b64", encoded],
        "query_string": b"",
    }
    assert _scope_token(scope) == token
    security = SimpleNamespace(token_is_stale=lambda candidate: candidate == token)
    cleaned = _strip_stale_credentials(scope, security)
    assert cleaned["headers"] == []
    assert cleaned["subprotocols"] == []
    assert websocket_token(cleaned) is None
    assert authorization_token(cleaned) == ""
    assert scope["subprotocols"] == ["bearer.b64", encoded]


@pytest.mark.parametrize("disk_store", [False, True])
def test_real_account_revocation_closes_encoded_websocket_and_invalidates_copied_context(
    tmp_path, monkeypatch, disk_store
):
    monkeypatch.setenv("ECHO_APPLIANCE", "1")
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ECHO_ADMIN_PASSWORD", "test-device-passphrase")
    if disk_store:
        config, _generated = load_or_bootstrap_auth()
        audit = ApplianceAudit.from_data_dir(tmp_path, jwt_secret=config.jwt_secret)
        approval = HighRiskApprovalService(
            password_hash=config.users["admin"], jwt_secret=config.jwt_secret, audit=audit
        )
    else:
        # Exercise the real revocation service on every OS. Only persistence
        # and audit I/O are substituted; the disk variant exercises them too.
        store = {"username": "admin", "password_hash": "unused-test-hash"}
        monkeypatch.setattr(
            "appliance.account_security.read_auth_store", lambda _p: copy.deepcopy(store)
        )
        monkeypatch.setattr(
            "appliance.account_security.write_auth_store",
            lambda payload, _p: store.update(copy.deepcopy(payload)),
        )
        config = SimpleNamespace(jwt_secret=_SECRET, users={"admin": "unused-test-hash"})
        audit = SimpleNamespace(record=lambda **_kwargs: None)
        approval = SimpleNamespace(invalidate_tokens=lambda: None)
    security = ApplianceAccountSecurity(auth_config=config, approval=approval, audit=audit)
    app, _live = _app(security, secret=config.jwt_secret)
    app.add_middleware(ApplianceSessionRevocationMiddleware, account_security=security)
    captured = []

    @app.get("/capture")
    async def capture(request: Request):
        captured.append(contextvars.copy_context())
        # No credential is placed in public scope.state for tools or callers.
        assert not getattr(request.state, "appliance_authorization", None)
        return _result()

    token = _token("local:admin", secret=config.jwt_secret)
    with TestClient(app) as client:
        assert client.get("/capture", headers={"Authorization": f"Bearer {token}"}).json() == {
            "actor": "local:admin"
        }
        with client.websocket_connect(
            "/tool-ws", subprotocols=["bearer.b64", _encoded(token)]
        ) as ws:
            ws.send_text("read")
            assert ws.receive_json() == {"actor": "local:admin"}
            security.revoke_all(actor="local:admin")
            with pytest.raises(WebSocketDisconnect) as stopped:
                ws.receive_json()
            assert stopped.value.code == 4401
        assert captured[0].run(_result) == {"error": "appliance_authorization_required"}
        assert (
            client.post("/tool", json={}, headers={"Authorization": f"Bearer {token}"}).status_code
            == 403
        )


def test_explicit_development_never_becomes_admin_or_production_fallback(monkeypatch):
    monkeypatch.delenv("ECHO_APPLIANCE", raising=False)
    app, _security = _app(secret=None)
    with TestClient(app) as client:
        assert client.post("/tool", json={"actor": "local:admin"}).json() == {
            "actor": "local:development"
        }
    monkeypatch.setenv("ECHO_APPLIANCE", "1")
    app, _security = _app(secret=None)
    with TestClient(app) as client:
        assert client.post("/tool", json={}).status_code == 403
