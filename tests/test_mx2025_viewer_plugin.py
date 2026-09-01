from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from runtime.platform.plugins.bundled.mx2025_viewer import (
    MX2025ViewerPlugin,
)
from runtime.platform.plugins.bundled.mx2025_viewer.groups import ConversationGroupStore
from runtime.platform.plugins.bundled.mx2025_viewer.proxy import (
    _REQUEST_BODY_LIMIT,
    _rewrite_login_host,
    _rewrite_proxy_root_paths,
    _safe_upstream_path,
    register_origin_proxy,
    secure_upstream_origin,
)
from runtime.platform.plugins.plugin_base import ModuleContext
from runtime.platform.plugins.plugin_hub import PluginHub

PLUGIN_ID = "mx2025_viewer"
PLUGIN_DIR = (
    Path(__file__).resolve().parents[1] / "runtime" / "platform" / "plugins" / "bundled" / PLUGIN_ID
)


def _load_plugin(
    config: dict[str, object] | None = None,
    *,
    authenticated_host: bool = False,
) -> tuple[MX2025ViewerPlugin, FastAPI]:
    app = FastAPI()
    app.state.echo_require_auth = authenticated_host
    plugin = MX2025ViewerPlugin()
    plugin.on_load(
        ModuleContext(
            plugin_name=PLUGIN_ID,
            plugin_dir=str(PLUGIN_DIR),
            manifest=None,
            fastapi_app=app,
            config=dict(config or {}),
        )
    )
    return plugin, app


def _proxy_app(client: httpx.AsyncClient) -> FastAPI:
    router = APIRouter(prefix=f"/api/plugins/{PLUGIN_ID}")
    assert register_origin_proxy(
        router,
        base_url="https://up.test/base",
        http_client=client,
    )
    app = FastAPI()
    app.include_router(router)
    return app


def _mounted_routes(app: FastAPI):
    def _walk(routes):
        for route in routes:
            nested = getattr(getattr(route, "original_router", None), "routes", None)
            if nested is not None:
                yield from _walk(nested)
            else:
                yield route

    return list(_walk(app.routes))


def test_bundled_manifest_defaults_origin_proxy_on(tmp_path: Path) -> None:
    hub = PluginHub(
        plugin_dir=tmp_path,
        bundled_plugin_dir=PLUGIN_DIR.parent,
    )
    discovered = [item for item in hub.discover() if item["id"] == PLUGIN_ID]

    assert len(discovered) == 1
    assert discovered[0]["bundled"] is True
    plugin = hub.load(PLUGIN_ID)
    assert plugin is not None
    assert plugin.version == MX2025ViewerPlugin.version
    assert plugin.proxy_origin is True
    assert hub.get_plugin_config(PLUGIN_ID) == {
        "base_url": "https://mx2025.hhhuu.com",
        "inactive_days": 7,
        "proxy_origin": True,
        "allow_same_origin_third_party_scripts": True,
        "isolated_bridge_url": "http://127.0.0.1:18082",
    }
    listed = next(item for item in hub.list_plugins() if item["id"] == PLUGIN_ID)
    schema = listed["config_schema"]["properties"]
    assert schema["proxy_origin"]["type"] == "boolean"
    assert schema["proxy_origin"]["default"] is True
    assert schema["inactive_days"]["default"] == 7
    assert schema["allow_same_origin_third_party_scripts"]["default"] is True
    assert schema["isolated_bridge_url"]["default"] == "http://127.0.0.1:18082"
    assert [cap["name"] for cap in listed["capabilities"]] == [
        "mx2025_viewer.skills",
        "mx2025_viewer.api",
    ]


def test_proxy_is_mounted_by_default_without_environment(monkeypatch) -> None:
    monkeypatch.delenv("ECHO_MX2025_PROXY_ORIGIN", raising=False)
    monkeypatch.delenv("ECHO_MX2025_ALLOW_SAME_ORIGIN_SCRIPTS", raising=False)
    plugin, app = _load_plugin()
    client = TestClient(app)

    assert plugin.proxy_origin is True
    assert any("/origin/" in str(getattr(route, "path", "")) for route in _mounted_routes(app))
    page = client.get(f"/api/plugins/{PLUGIN_ID}/page")
    assert page.status_code == 200
    assert '<iframe id="frame" title="MX技术小筑">' in page.text
    assert "frame-src 'self'" in page.headers["content-security-policy"]


@pytest.mark.parametrize(
    "config",
    [
        {"proxy_origin": False, "base_url": "https://up.test"},
        {
            "proxy_origin": True,
            "allow_same_origin_third_party_scripts": False,
            "base_url": "https://up.test",
        },
        {
            "proxy_origin": True,
            "allow_same_origin_third_party_scripts": True,
            "base_url": "http://up.test",
        },
        {
            "proxy_origin": "true",
            "allow_same_origin_third_party_scripts": True,
            "base_url": "https://up.test",
        },
        {
            "proxy_origin": True,
            "allow_same_origin_third_party_scripts": "true",
            "base_url": "https://up.test",
        },
    ],
)
def test_proxy_rejects_explicit_disable_strings_and_non_https(
    config: dict[str, object],
    monkeypatch,
) -> None:
    monkeypatch.delenv("ECHO_MX2025_PROXY_ORIGIN", raising=False)
    monkeypatch.delenv("ECHO_MX2025_ALLOW_SAME_ORIGIN_SCRIPTS", raising=False)
    plugin, app = _load_plugin(config)

    assert plugin.proxy_origin is False
    assert not any("/origin/" in str(getattr(route, "path", "")) for route in _mounted_routes(app))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("proxy_origin", False),
        ("proxy_origin", "true"),
        ("allow_same_origin_third_party_scripts", False),
        ("allow_same_origin_third_party_scripts", "true"),
    ],
)
def test_explicit_config_wins_over_enable_environment(
    key: str,
    value: object,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ECHO_MX2025_PROXY_ORIGIN", "true")
    monkeypatch.setenv("ECHO_MX2025_ALLOW_SAME_ORIGIN_SCRIPTS", "true")
    config: dict[str, object] = {
        "proxy_origin": True,
        "allow_same_origin_third_party_scripts": True,
        "base_url": "https://up.test",
    }
    config[key] = value

    plugin, app = _load_plugin(config)

    assert plugin.proxy_origin is False
    assert not any("/origin/" in str(getattr(route, "path", "")) for route in _mounted_routes(app))


def test_local_explicit_double_switch_keeps_viewer_compatible() -> None:
    plugin, app = _load_plugin(
        {
            "proxy_origin": True,
            "allow_same_origin_third_party_scripts": True,
            "base_url": "https://up.test",
        }
    )

    assert plugin.proxy_origin is True
    assert any("/origin/" in str(getattr(route, "path", "")) for route in _mounted_routes(app))
    page = TestClient(app).get(f"/api/plugins/{PLUGIN_ID}/page")
    assert page.status_code == 200
    assert "<iframe" in page.text
    assert "Number('7')" in page.text
    assert "data-mx-inactive-hidden" in page.text
    assert "data-mx-empty-hidden" in page.text
    assert "hasVisibleText" in page.text
    assert "width:calc(100% - 92px)" in page.text
    assert "overflow:clip!important" in page.text
    assert 'id="groupFilter"' in page.text
    assert 'id="groupModal"' in page.text
    assert 'id="syncModal"' in page.text
    assert 'id="syncManage"' in page.text
    assert "/sync/capture" in page.text
    assert "captureVisibleMessages" in page.text
    assert "group-assignments" in page.text
    assert "data-mx-group-hidden" in page.text
    assert "hideUpstreamPromotionModals" in page.text
    assert "每日签到" in page.text
    assert 'data-mx-promotion-hidden="1"' in page.text
    assert "MutationObserver" in page.text
    assert "location.protocol==='http:'" in page.text
    assert "frameLocation.pathname==='/'" in page.text
    assert "?echo_proxy=7#/" in page.text
    assert "var viewerMode='local'" in page.text
    assert f"var groupApi='/api/plugins/{PLUGIN_ID}'" in page.text
    assert "__MX_" not in page.text
    assert 'iframe id="frame" title="MX技术小筑"' in page.text
    assert "frame-src 'self'" in page.headers["content-security-policy"]
    assert "connect-src 'self'" in page.headers["content-security-policy"]


def test_local_conversation_groups_persist_and_assign(tmp_path: Path) -> None:
    store = ConversationGroupStore(tmp_path)
    created = store.create("工作")
    group_id = created["id"]

    assert store.assign("3436", group_id) == {"room_id": "3436", "group_id": group_id}
    assert ConversationGroupStore(tmp_path).snapshot() == {
        "groups": [{"id": group_id, "name": "工作"}],
        "assignments": {"3436": group_id},
    }
    assert store.rename(group_id, "重点关注") == {"id": group_id, "name": "重点关注"}
    assert store.delete(group_id) is True
    assert store.snapshot() == {"groups": [], "assignments": {}}


def test_local_group_api_crud_is_independent_from_upstream(tmp_path: Path) -> None:
    plugin, app = _load_plugin()
    plugin.groups = ConversationGroupStore(tmp_path)
    client = TestClient(app)

    created = client.post(f"/api/plugins/{PLUGIN_ID}/groups", json={"name": "收藏"})
    assert created.status_code == 200
    group_id = created.json()["group"]["id"]
    assigned = client.put(
        f"/api/plugins/{PLUGIN_ID}/group-assignments/9001",
        json={"group_id": group_id},
    )
    assert assigned.status_code == 200
    assert client.get(f"/api/plugins/{PLUGIN_ID}/groups").json() == {
        "groups": [{"id": group_id, "name": "收藏"}],
        "assignments": {"9001": group_id},
    }
    assert (
        client.patch(
            f"/api/plugins/{PLUGIN_ID}/groups/{group_id}", json={"name": "稍后看"}
        ).status_code
        == 200
    )
    assert client.delete(f"/api/plugins/{PLUGIN_ID}/groups/{group_id}").status_code == 200


def test_authenticated_host_does_not_expose_group_state() -> None:
    _, app = _load_plugin(authenticated_host=True)
    client = TestClient(app)

    assert client.get(f"/api/plugins/{PLUGIN_ID}/groups").status_code == 404


def test_inactive_days_is_configurable_and_clamped() -> None:
    _, app = _load_plugin(
        {
            "proxy_origin": True,
            "allow_same_origin_third_party_scripts": True,
            "base_url": "https://up.test",
            "inactive_days": 999,
        }
    )

    page = TestClient(app).get(f"/api/plugins/{PLUGIN_ID}/page")
    assert page.status_code == 200
    assert "Number('365')" in page.text


def test_authenticated_host_mounts_only_public_static_notice() -> None:
    from runtime.platform.ui._app_auth import _install_legacy_control_plane_auth
    from runtime.safety.auth import Identity, IdentityStore

    plugin, app = _load_plugin(
        {
            "proxy_origin": True,
            "allow_same_origin_third_party_scripts": True,
            "base_url": "https://up.test",
        },
        authenticated_host=True,
    )
    identities = IdentityStore()
    identities.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    _install_legacy_control_plane_auth(
        app,
        identity_store=identities,
        require_auth=True,
        jwt_secret=None,
        jwt_issuer=None,
        jwt_audience=None,
    )
    client = TestClient(app)

    assert plugin.proxy_origin is False
    page = client.get(f"/api/plugins/{PLUGIN_ID}/page")
    assert page.status_code == 200
    assert "当前实例已开启身份认证" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]
    routes = _mounted_routes(app)
    assert not any("/origin/" in str(getattr(route, "path", "")) for route in routes)
    assert not any(type(route).__name__ == "APIWebSocketRoute" for route in routes)
    origin = client.get(
        f"/api/plugins/{PLUGIN_ID}/origin/api/me",
        headers={"Authorization": "Bearer sk-alice"},
    )
    assert origin.status_code == 404
    assert client.get(f"/api/plugins/{PLUGIN_ID}/page/child").status_code == 401


def test_authenticated_host_can_embed_separate_loopback_bridge() -> None:
    plugin, app = _load_plugin(
        {
            "proxy_origin": True,
            "allow_same_origin_third_party_scripts": True,
            "base_url": "https://up.test",
            "isolated_bridge_url": "http://127.0.0.1:18082",
        },
        authenticated_host=True,
    )
    page = TestClient(app).get(f"/api/plugins/{PLUGIN_ID}/page")

    assert plugin.proxy_origin is False
    assert page.status_code == 200
    assert '<iframe src="http://127.0.0.1:18082/viewer"' in page.text
    assert "frame-src http://127.0.0.1:18082" in page.headers["content-security-policy"]
    assert not any("/origin/" in str(getattr(route, "path", "")) for route in _mounted_routes(app))


def test_proxy_strips_host_credentials_and_upstream_cookie_and_sets_csp() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            content=b"ok",
            headers={
                "Content-Type": "text/plain",
                "Set-Cookie": "upstream_session=secret; Path=/",
                "Content-Security-Policy": "default-src *",
            },
        )

    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer client-default"},
        cookies={"client_cookie": "secret"},
    )
    response = TestClient(_proxy_app(upstream)).get(
        f"/api/plugins/{PLUGIN_ID}/origin/api/profile?view=full",
        headers={
            "Authorization": "Bearer echo-host-token",
            "Cookie": "echo_session=host-secret",
            "Proxy-Authorization": "Basic host-secret",
            "Token": "mx-upstream-token",
            "Version": "4.2.3",
            "AD": "true",
            "I": "qq",
        },
    )

    assert response.status_code == 200
    assert len(seen) == 1
    sent = seen[0]
    assert "authorization" not in sent.headers
    assert "proxy-authorization" not in sent.headers
    assert "cookie" not in sent.headers
    assert sent.headers["token"] == "mx-upstream-token"
    assert sent.headers["version"] == "4.2.3"
    assert sent.headers["ad"] == "true"
    assert sent.headers["i"] == "qq"
    assert sent.headers["origin"] == "https://up.test"
    assert sent.url.params["view"] == "full"
    assert "set-cookie" not in response.headers
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "default-src *" not in csp
    assert response.headers["x-content-type-options"] == "nosniff"


def test_proxy_rejects_oversized_request_before_upstream() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=b"unexpected")

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    response = TestClient(_proxy_app(upstream)).post(
        f"/api/plugins/{PLUGIN_ID}/origin/api/upload",
        content=b"x" * (_REQUEST_BODY_LIMIT + 1),
    )

    assert response.status_code == 413
    assert called is False


def test_proxy_error_response_does_not_expose_upstream_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret.internal.example:9443 refused token=abc", request=request)

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    response = TestClient(_proxy_app(upstream)).get(f"/api/plugins/{PLUGIN_ID}/origin/api/profile")

    assert response.status_code == 503
    assert response.json() == {"detail": "MX upstream temporarily unavailable"}
    assert "secret.internal" not in response.text
    assert "default-src 'self'" in response.headers["content-security-policy"]


@pytest.mark.parametrize(
    "base_url",
    [
        "http://up.test",
        "https://user:pass@up.test",
        "https://up.test/path?token=secret",
        "https://up.test/path#fragment",
        "https://up.test:bad",
        "https://bad host.test",
        "https://bad_host.test",
        "https://%65vil.test",
        "https://127.0.0.1%2f.evil.test",
        "https://123",
        "https://up.test\\@evil.test",
        "not-a-url",
    ],
)
def test_strict_https_validator_rejects_ambiguous_urls(base_url: str) -> None:
    assert secure_upstream_origin(base_url) is None
    router = APIRouter(prefix=f"/api/plugins/{PLUGIN_ID}")
    assert register_origin_proxy(router, base_url=base_url) is False
    assert router.routes == []


def test_https_validator_and_path_allowlist_normalize_valid_inputs() -> None:
    assert secure_upstream_origin("https://UP.TEST:8443/base/") == "https://up.test:8443/base"
    assert _safe_upstream_path("") == ""
    assert _safe_upstream_path("pages/login/login") == "pages/login/login"
    assert _safe_upstream_path("assets/app.js") == "assets/app.js"
    assert _safe_upstream_path("3/api/msg/tip") == "3/api/msg/tip"
    assert _safe_upstream_path("5/api/room/list") == "5/api/room/list"
    assert _safe_upstream_path("socket.io/") == "socket.io/"
    assert _safe_upstream_path("evil/admin") is None
    assert _safe_upstream_path("pages/%2e%2e/admin") is None
    assert _safe_upstream_path("pages/%252e%252e/admin") is None


def test_rewrites_root_relative_spa_assets_and_api_calls() -> None:
    source = b'<script src="/assets/app.js"></script><link href="/static/app.css">'

    rewritten = _rewrite_proxy_root_paths(source, "text/html; charset=utf-8").decode()

    prefix = "/api/plugins/mx2025_viewer/origin"
    assert f"{prefix}/assets/app.js" in rewritten
    assert f"{prefix}/static/app.css" in rewritten
    assert f"{prefix}/assets/app.js?echo_proxy=7" in rewritten

    script = (
        b'const master="/3"; const room="/5"; '
        b'const chunk="assets/login.js"; const ws="/socket.io/"; '
        b'fetch("/api/msg/tip"); go("/pages/login"); import("./pages-login.js")'
    )
    rewritten_script = _rewrite_proxy_root_paths(script, "application/javascript").decode()
    assert f'"{prefix}/3"' in rewritten_script
    assert f'"{prefix}/5"' in rewritten_script
    assert f'"{prefix.lstrip("/")}/assets/login.js?echo_proxy=7"' in rewritten_script
    assert '"./pages-login.js?echo_proxy=7"' in rewritten_script
    assert f'"{prefix}/socket.io/"' in rewritten_script
    assert '"/api/msg/tip"' in rewritten_script
    assert '"/pages/login"' in rewritten_script


def test_does_not_rewrite_api_payloads_or_external_urls() -> None:
    payload = b'{"avatar":"/img/user.png","link":"https://example.test/assets/x.js"}'

    assert _rewrite_proxy_root_paths(payload, "application/json") == payload


def test_rewrites_login_shard_without_changing_other_fields() -> None:
    body = b'{"code":200,"hosturl":"/5","token":"secret-token"}'

    rewritten = json.loads(_rewrite_login_host(body))

    assert rewritten == {
        "code": 200,
        "hosturl": "/api/plugins/mx2025_viewer/origin/5",
        "token": "secret-token",
    }
