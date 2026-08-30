"""oct 账号网关集成测试。

覆盖:config / OctLinkStore / client helpers / 邮箱登录路由 / 账号路由 /
OctModelRouter(网关计费)/ OctFallbackRouter(actor 感知,保 P0 guest 路径)。
所有上游网关调用用注入的 fake http_client,不打真网。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from runtime.adapters.integrations.oct import (  # noqa: E402
    OctConfig,
    OctLink,
    OctLinkStore,
    load_oct_config_from_dict,
)
from runtime.adapters.integrations.oct.client import (  # noqa: E402
    OctClientError,
    get_auth,
    get_public,
    is_dead_token,
    is_insufficient_credits,
    mask_email,
    post_public,
)
from runtime.adapters.integrations.oct.router_account import create_account_router  # noqa: E402
from runtime.adapters.integrations.oct.router_auth import (  # noqa: E402
    actor_from_email,
    create_auth_router,
)
from runtime.platform.ui._app_auth_routers import _restore_oct_identities  # noqa: E402
from runtime.safety.auth import IdentityStore  # noqa: E402
from runtime.sensing.model_router.actor_context import current_actor  # noqa: E402
from runtime.sensing.model_router.models import (  # noqa: E402
    CostEntry,
    Message,
    ModelRequest,
    ModelResponse,
    ModelRouter,
)
from runtime.sensing.model_router.oct_router import (  # noqa: E402
    OctCredentialsRequired,
    OctFallbackRouter,
    OctModelRouter,
)

_SECRET = "x" * 32


class _Resp:
    def __init__(self, body: Any, status: int = 200) -> None:
        self._body = body
        self.status_code = status
        self._text = json.dumps(body) if isinstance(body, (dict, list)) else str(body or "")
        self.content = self._text.encode()

    @property
    def text(self) -> str:
        return self._text

    def json(self) -> Any:
        if isinstance(self._body, (dict, list)):
            return self._body
        raise ValueError("not json")


class _FakeHttp:
    """按 URL 末段路由的 fake httpx。routes: {'send'|'login'|'balance'|...: _Resp}。"""

    def __init__(self, routes: dict[str, _Resp]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict | None]] = []

    def _key(self, url: str) -> str:
        return url.rstrip("/").rsplit("/", 1)[-1]

    def post(
        self, url: str, json: dict | None = None, headers: dict | None = None, timeout: Any = None
    ) -> _Resp:
        self.calls.append(("POST", url, headers))
        return self.routes[self._key(url)]

    def get(
        self, url: str, headers: dict | None = None, params: dict | None = None, timeout: Any = None
    ) -> _Resp:
        self.calls.append(("GET", url, headers))
        return self.routes[self._key(url)]


# ─── config ─────────────────────────────────────────────


def test_config_defaults() -> None:
    c = OctConfig()
    assert c.enabled is False
    assert c.base_url == "https://api.echo-age.com"
    assert c.default_model == "qwen3.5-flash"


def test_load_config_from_dict() -> None:
    c = load_oct_config_from_dict({"enabled": True, "base_url": "https://x"})
    assert c.enabled is True and c.base_url == "https://x"


# ─── client helpers ─────────────────────────────────────


def test_mask_email() -> None:
    assert mask_email("alice@example.com") == "a***@example.com"
    assert mask_email("nope") == "***"


def test_dead_token_and_credits_codes() -> None:
    assert is_dead_token(401) is True
    assert is_dead_token(200) is False
    assert is_insufficient_credits(402) is True


def test_post_public_parses() -> None:
    fake = _FakeHttp({"login": _Resp({"token": "jwt", "userId": "u1"})})
    out = post_public(
        "https://x/auth/email/login", {"email": "a@b.com"}, timeout=5, http_client=fake
    )
    assert out["token"] == "jwt"


def test_get_auth_sends_bearer_and_raises_on_4xx() -> None:
    fake = _FakeHttp({"balance": _Resp({"detail": "nope"}, status=401)})
    with pytest.raises(OctClientError) as ei:
        get_auth("https://x/account/balance", token="jwt", timeout=5, http_client=fake)
    assert ei.value.status_code == 401
    assert fake.calls[0][2]["Authorization"] == "Bearer jwt"


def test_get_public_never_sends_an_empty_bearer_header() -> None:
    fake = _FakeHttp({"models": _Resp({"data": []})})

    assert get_public("https://x/v1/models", timeout=5, http_client=fake) == {"data": []}
    assert fake.calls[0][2] is None


def test_get_auth_rejects_empty_token_before_network() -> None:
    fake = _FakeHttp({})

    with pytest.raises(OctClientError) as exc:
        get_auth("https://x/account/balance", token="  ", timeout=5, http_client=fake)

    assert exc.value.status_code == 401
    assert fake.calls == []


# ─── links store ────────────────────────────────────────


def test_link_store_roundtrip(tmp_path: Any) -> None:
    store = OctLinkStore(path=tmp_path / "l.json")
    store.put(
        OctLink(echo_user_id="oct:a@b.com", oct_user_id="u1", oct_token="jwt", email="a@b.com")
    )
    assert store.get("oct:a@b.com").oct_token == "jwt"
    assert store.all_actor_ids() == ["oct:a@b.com"]
    store.update_credits("oct:a@b.com", {"credits": 100}, now=1.0)
    assert store.get("oct:a@b.com").credits_snapshot == {"credits": 100}
    store.mark_token_invalid("oct:a@b.com", "EXPIRED")
    assert store.get("oct:a@b.com").token_invalid is True
    assert store.delete("oct:a@b.com") is True
    assert store.get("oct:a@b.com") is None


def test_link_store_restores_oct_identity_after_restart(tmp_path: Any) -> None:
    links = OctLinkStore(path=tmp_path / "l.json")
    links.put(
        OctLink(
            echo_user_id="oct:a@b.com",
            oct_user_id="u1",
            oct_token="jwt",
            email="a@b.com",
        )
    )
    identities = IdentityStore()

    restored = _restore_oct_identities(identities, links)

    identity = identities.get("oct:a@b.com")
    assert restored == 1
    assert identity is not None
    assert identity.roles == ("user", "oct")
    assert identity.metadata["email"] == "a@b.com"
    assert _restore_oct_identities(identities, links) == 0


def test_actor_from_email_lowercases() -> None:
    assert actor_from_email("  Alice@Example.COM ") == "oct:alice@example.com"


# ─── auth router (TestClient + fake gateway) ─────────────


def _auth_app(routes: dict[str, _Resp], *, store: OctLinkStore, enabled: bool = True) -> TestClient:
    cfg = OctConfig(enabled=enabled)
    router = create_auth_router(
        config=cfg, link_store=store, jwt_secret=_SECRET, http_client=_FakeHttp(routes)
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_email_login_stores_link_and_issues_jwt(tmp_path: Any) -> None:
    store = OctLinkStore(path=tmp_path / "l.json")
    client = _auth_app(
        {
            "send": _Resp({"ok": True, "ttlSeconds": 300}),
            "login": _Resp(
                {"token": "gw-jwt", "userId": "u1", "email": "a@b.com", "isNewUser": True}
            ),
        },
        store=store,
    )
    assert client.post("/api/auth/oct/email/send", json={"email": "a@b.com"}).status_code == 200
    r = client.post("/api/auth/oct/email/login", json={"email": "a@b.com", "code": "123456"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["actor_id"] == "oct:a@b.com"
    assert body["access_token"]  # agent 自有 JWT 已签
    cookie = r.headers["set-cookie"]
    assert "echo_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Max-Age=" in cookie
    # 网关 JWT 存进 link
    assert store.get("oct:a@b.com").oct_token == "gw-jwt"


def test_email_login_bad_code_is_401(tmp_path: Any) -> None:
    store = OctLinkStore(path=tmp_path / "l.json")
    client = _auth_app({"login": _Resp({"detail": "bad code"}, status=400)}, store=store)
    r = client.post("/api/auth/oct/email/login", json={"email": "a@b.com", "code": "000000"})
    assert r.status_code == 401


def test_email_bad_format_is_400(tmp_path: Any) -> None:
    store = OctLinkStore(path=tmp_path / "l.json")
    client = _auth_app({"send": _Resp({"ok": True})}, store=store)
    assert (
        client.post("/api/auth/oct/email/send", json={"email": "not-an-email"}).status_code == 400
    )


def test_disabled_returns_503(tmp_path: Any) -> None:
    store = OctLinkStore(path=tmp_path / "l.json")
    client = _auth_app({"send": _Resp({"ok": True})}, store=store, enabled=False)
    assert client.post("/api/auth/oct/email/send", json={"email": "a@b.com"}).status_code == 503


# ─── account router ─────────────────────────────────────


def _bearer(actor: str = "oct:a@b.com") -> dict[str, str]:
    import time

    from runtime.safety.auth.identity import encode_jwt_hs256

    now = int(time.time())
    token = encode_jwt_hs256(
        {"sub": actor, "iat": now, "exp": now + 3600, "iss": "echo-agent"},
        secret=_SECRET,
    )
    return {"Authorization": f"Bearer {token}"}


def _account_app(
    routes: dict[str, _Resp], *, store: OctLinkStore, actor: str = "oct:a@b.com"
) -> TestClient:
    from runtime.safety.auth.identity import Identity, IdentityStore

    ids = IdentityStore()
    ids.add(Identity(actor_id=actor, roles=("user", "oct"), metadata={"provider": "oct"}))
    cfg = OctConfig(enabled=True)
    router = create_account_router(
        config=cfg,
        link_store=store,
        identity_store=ids,
        require_auth=False,
        jwt_secret=_SECRET,
        http_client=_FakeHttp(routes),
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_account_refresh_pulls_balance_membership(tmp_path: Any) -> None:
    store = OctLinkStore(path=tmp_path / "l.json")
    store.put(
        OctLink(echo_user_id="oct:a@b.com", oct_user_id="u1", oct_token="jwt", email="a@b.com")
    )
    client = _account_app(
        {
            "balance": _Resp({"credits": 500, "paidCredits": 500}),
            "membership": _Resp({"active": True, "remainingDays": 10}),
        },
        store=store,
    )
    r = client.post("/api/account/oct/refresh", headers=_bearer())
    assert r.status_code == 200, r.text
    snap = r.json()["credits"]
    assert snap["credits"] == 500 and snap["membership"]["active"] is True


def test_account_returns_empty_state_when_no_link(tmp_path: Any) -> None:
    store = OctLinkStore(path=tmp_path / "l.json")
    client = _account_app({}, store=store)
    response = client.get("/api/account/oct", headers=_bearer())
    assert response.status_code == 200
    assert response.json() is None


# ─── model router ───────────────────────────────────────


def test_model_router_calls_gateway_and_meters(tmp_path: Any) -> None:
    store = OctLinkStore(path=tmp_path / "l.json")
    store.put(
        OctLink(echo_user_id="oct:a@b.com", oct_user_id="u1", oct_token="gw-jwt", email="a@b.com")
    )
    chat = _Resp(
        {
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7},
        }
    )
    mr = OctModelRouter(link_store=store, http_client=_FakeHttp({"completions": chat}))
    tok = current_actor.set("oct:a@b.com")
    try:
        resp = mr.call(
            ModelRequest(model="qwen3.5-flash", messages=[Message(role="user", content="hi")])
        )
    finally:
        current_actor.reset(tok)
    assert resp.text == "hi" and resp.input_tokens == 12 and resp.output_tokens == 7
    assert resp.provider == "oct"


def test_model_router_401_raises_credentials(tmp_path: Any) -> None:
    store = OctLinkStore(path=tmp_path / "l.json")
    store.put(OctLink(echo_user_id="oct:a@b.com", oct_user_id="u1", oct_token="gw-jwt"))
    mr = OctModelRouter(
        link_store=store, http_client=_FakeHttp({"completions": _Resp({"d": 1}, status=401)})
    )
    tok = current_actor.set("oct:a@b.com")
    try:
        with pytest.raises(OctCredentialsRequired):
            mr.call(ModelRequest(model="x", messages=[Message(role="user", content="hi")]))
    finally:
        current_actor.reset(tok)


def test_model_router_402_raises_runtime(tmp_path: Any) -> None:
    store = OctLinkStore(path=tmp_path / "l.json")
    store.put(OctLink(echo_user_id="oct:a@b.com", oct_user_id="u1", oct_token="gw-jwt"))
    mr = OctModelRouter(
        link_store=store, http_client=_FakeHttp({"completions": _Resp({"d": 1}, status=402)})
    )
    tok = current_actor.set("oct:a@b.com")
    try:
        with pytest.raises(RuntimeError, match="积分"):
            mr.call(ModelRequest(model="x", messages=[Message(role="user", content="hi")]))
    finally:
        current_actor.reset(tok)


def test_model_router_no_actor_raises(tmp_path: Any) -> None:
    store = OctLinkStore(path=tmp_path / "l.json")
    mr = OctModelRouter(link_store=store, http_client=_FakeHttp({}))
    with pytest.raises(OctCredentialsRequired):
        mr.call(ModelRequest(model="x", messages=[Message(role="user", content="hi")]))


# ─── fallback router (actor 感知,保 P0) ──────────────────


class _Tag(ModelRouter):
    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.default_model = "m-" + tag

    def call(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            text=self.tag,
            input_tokens=1,
            output_tokens=1,
            cost=CostEntry(tokens_in=1, tokens_out=1, usd=0.0),
            model="x",
            provider=self.tag,
        )


def _fb(store: OctLinkStore) -> OctFallbackRouter:
    return OctFallbackRouter(oct_router=_Tag("OCT"), self_router=_Tag("SELF"), link_store=store)


def _call_fb(fb: OctFallbackRouter, actor: str | None) -> str:
    req = ModelRequest(model="x", messages=[Message(role="user", content="hi")])
    if actor is None:
        return fb.call(req).text
    tok = current_actor.set(actor)
    try:
        return fb.call(req).text
    finally:
        current_actor.reset(tok)


def test_fallback_guest_uses_self(tmp_path: Any) -> None:
    # guest / 无 actor → 自配模型(不回退 P0)
    assert _call_fb(_fb(OctLinkStore(path=tmp_path / "l.json")), None) == "SELF"


def test_fallback_actor_no_link_uses_self(tmp_path: Any) -> None:
    assert _call_fb(_fb(OctLinkStore(path=tmp_path / "l.json")), "oct:nobody@x.com") == "SELF"


def test_fallback_actor_with_link_uses_oct(tmp_path: Any) -> None:
    store = OctLinkStore(path=tmp_path / "l.json")
    store.put(
        OctLink(echo_user_id="oct:a@b.com", oct_user_id="u1", oct_token="jwt", email="a@b.com")
    )
    assert _call_fb(_fb(store), "oct:a@b.com") == "OCT"


def test_fallback_invalid_token_uses_self(tmp_path: Any) -> None:
    store = OctLinkStore(path=tmp_path / "l.json")
    store.put(OctLink(echo_user_id="oct:a@b.com", oct_user_id="u1", oct_token="jwt"))
    store.mark_token_invalid("oct:a@b.com", "EXPIRED")
    assert _call_fb(_fb(store), "oct:a@b.com") == "SELF"


# ─── 审查后加固(评审 #1/#2/#9/#6)───────────────────────


def _tools_request() -> ModelRequest:
    from runtime.sensing.model_router.models import ToolSpec

    return ModelRequest(
        model="qwen3.5-flash",
        messages=[Message(role="user", content="hi")],
        tools=[ToolSpec(name="t1", description="d", input_schema={"type": "object"})],
    )


def test_build_payload_includes_tools_both_modes(tmp_path: Any) -> None:
    # 评审 #2:call_stream 曾丢 tools → 共享 _build_payload 后两条路径都带 tools
    mr = OctModelRouter(link_store=OctLinkStore(path=tmp_path / "l.json"))
    for stream in (False, True):
        p = mr._build_payload(_tools_request(), stream=stream)
        assert p["stream"] is stream
        assert "tools" in p and p["tools"][0]["function"]["name"] == "t1"
        assert p["tool_choice"] == "auto"


def test_model_router_401_marks_token_invalid(tmp_path: Any) -> None:
    # 评审 #9:401 时标记 link 失效 → 下回合 OctFallbackRouter 降级自配
    store = OctLinkStore(path=tmp_path / "l.json")
    store.put(OctLink(echo_user_id="oct:a@b.com", oct_user_id="u1", oct_token="gw-jwt"))
    mr = OctModelRouter(
        link_store=store, http_client=_FakeHttp({"completions": _Resp({"d": 1}, status=401)})
    )
    tok = current_actor.set("oct:a@b.com")
    try:
        with pytest.raises(OctCredentialsRequired):
            mr.call(ModelRequest(model="x", messages=[Message(role="user", content="hi")]))
    finally:
        current_actor.reset(tok)
    assert store.get("oct:a@b.com").token_invalid is True


def test_schema_oct_requires_secret_when_enabled() -> None:
    # 评审 #1:enabled 必须有 jwt_secret(否则运行期锁死)
    from runtime.platform.config.schema import OctConfig as SchemaOct

    SchemaOct(enabled=False)  # ok
    # Audit R-03: a "valid-looking" long secret must still clear the entropy
    # gate — use a genuinely strong secret for the positive case.
    _strong = "V8!xQ#z9mK2@Lp4%Yw7^Nc1&Fd3*Gh5!Tq7#Rp9"
    SchemaOct(enabled=True, jwt_secret=_strong)  # ok
    with pytest.raises(ValueError, match="jwt_secret"):
        SchemaOct(enabled=True)
