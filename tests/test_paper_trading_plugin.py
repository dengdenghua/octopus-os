"""Tests for the bundled ``paper_trading`` plugin (模拟炒股练习模块).

Covers:
  1. 插件可发现、可加载,中文名展示为「模拟炒股」
  2. ``paper_trading.quote`` skill 注册进 SkillRegistry
  3. 自含引擎:市价买入 / T+1 锁定卖出 / 整手校验 / 限价未成交 / 账户盈亏 / 持久化
  4. 路由可挂载:页面 200,报价/下单/账户/成交 API 打通
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from runtime.execution.suckers.registry import Skill
from runtime.platform.plugins.bundled.paper_trading import PaperTradingPlugin
from runtime.platform.plugins.bundled.paper_trading import service as pt_service
from runtime.platform.plugins.bundled.paper_trading.service import (
    PaperTradingEngine,
    WatchlistStore,
)
from runtime.platform.plugins.plugin_base import ModuleContext
from runtime.platform.plugins.plugin_hub import PluginHub

PLUGIN_ID = "paper_trading"
PLUGIN_DIR = (
    Path(__file__).resolve().parents[1]
    / "runtime"
    / "platform"
    / "plugins"
    / "bundled"
    / "paper_trading"
)


def test_remote_paper_trading_is_discoverable_and_loadable(tmp_path: Path) -> None:
    # ``delivery: remote`` keeps the factory source inert.  Exercise the same
    # directory layout produced by the workbench installer without depending
    # on whatever happens to be installed in the developer's home directory.
    shutil.copytree(PLUGIN_DIR, tmp_path / "workbench" / "paper-trading")
    hub = PluginHub(plugin_dir=tmp_path, bundled_plugin_dir=PLUGIN_DIR.parent)
    matches = [item for item in hub.discover() if item["id"] == PLUGIN_ID]

    assert len(matches) == 1
    assert matches[0]["bundled"] is False
    assert matches[0]["name"] == "模拟炒股"  # display_name 折进 name,id 保持 ASCII
    assert matches[0]["version"] == PaperTradingPlugin.version
    plugin = hub.load(PLUGIN_ID)
    assert plugin is not None
    assert plugin.live is None
    assert plugin.proxy_origin is True
    assert hub.get_plugin_config(PLUGIN_ID)["live_mode"] is False
    assert hub.get_plugin_config(PLUGIN_ID)["proxy_origin"] is True
    assert hub.get_plugin_config(PLUGIN_ID)["allow_same_origin_third_party_scripts"] is True
    assert {capability.name for capability in plugin.capabilities} == {
        "paper_trading.api",
        "paper_trading.page",
        "paper_trading.quote_hub",
        "paper_trading.skills",
    }


def test_plugin_registers_skill_into_registry(tmp_path: Path) -> None:
    plugin = PaperTradingPlugin()
    plugin.ctx = MagicMock()
    plugin.engine = PaperTradingEngine(initial_cash=100_000, data_dir=str(tmp_path / "pt"))

    plugin.register_skills()

    assert plugin.ctx.register_skill.call_count == 1
    skill: Skill = plugin.ctx.register_skill.call_args[0][0]
    assert skill.name == "paper_trading.quote"
    assert skill.trusted_source == "plugin://paper_trading"
    assert callable(skill.handler)

    result = skill.handler(code="600519")
    assert result["code"] == "600519"
    assert result["name"] == "贵州茅台"


def _engine(tmp_path: Path) -> PaperTradingEngine:
    engine = PaperTradingEngine(initial_cash=1_000_000, data_dir=str(tmp_path / "pt"))
    engine.load()
    return engine


def test_engine_buy_and_t_plus_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """交易时段内:买入成交后 T+1 锁定,当日卖出被拒;整手校验生效。"""
    monkeypatch.setattr(pt_service, "is_trading_time", lambda now=None: True)
    engine = _engine(tmp_path)

    # 数量非整手 -> 拒绝
    res = engine.place_order(code="600519", side="buy", qty=150)
    assert res["ok"] is False
    assert "100 的整数倍" in res["message"]

    # 市价买入 100 股
    res = engine.place_order(code="600519", side="buy", order_type="market", qty=100)
    assert res["ok"] is True
    buy = res["order"]
    assert buy["side"] == "buy" and buy["qty"] == 100

    # T+1:当日买入的持仓 locked,不可卖
    acc = engine.account()
    pos = acc["positions"][0]
    assert pos["qty"] == 100
    assert pos["locked"] == 100
    assert pos["sellable"] == 0
    sell = engine.place_order(code="600519", side="sell", order_type="market", qty=100)
    assert sell["ok"] is False
    assert "T+1" in sell["message"]

    # 资金守恒:成交那一刻 现金 + 成交金额 + 买入费用 == 初始资金
    # (之后行情 tick 会让市值/盈亏变化,不影响该守恒)
    assert abs((acc["cash"] + buy["price"] * buy["qty"] + buy["fee"]) - 1_000_000) < 1.0


def test_engine_limit_order_not_filled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """限价卖高于现价 -> 未成交。"""
    monkeypatch.setattr(pt_service, "is_trading_time", lambda now=None: True)
    engine = _engine(tmp_path)

    engine.place_order(code="600519", side="buy", order_type="market", qty=100)
    price = engine.quote("600519")["price"]
    res = engine.place_order(
        code="600519", side="sell", order_type="limit", price=price * 1.05, qty=100
    )
    assert res["ok"] is False
    assert "未成交" in res["message"]


def test_engine_persistence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """状态持久化到 JSON:重启后账户/持仓/成交不丢。"""
    monkeypatch.setattr(pt_service, "is_trading_time", lambda now=None: True)
    engine = _engine(tmp_path)
    engine.place_order(code="600519", side="buy", order_type="market", qty=100)
    assert (tmp_path / "pt" / "state.json").exists()

    engine2 = _engine(tmp_path)
    assert len(engine2.state.positions) == 1
    assert len(engine2.state.orders) == 1


def test_routes_via_module_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """on_load 挂路由:页面 200,报价/下单/账户/成交 API 打通。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(pt_service, "is_trading_time", lambda now=None: True)

    app = FastAPI()
    plugin = PaperTradingPlugin()
    ctx = ModuleContext(
        plugin_name=PLUGIN_ID,
        plugin_dir=str(PLUGIN_DIR),
        manifest=None,
        fastapi_app=app,
        config={"initial_cash": 1_000_000, "data_dir": str(tmp_path / "pt")},
    )
    plugin.on_load(ctx)

    client = TestClient(app)
    assert client.get("/api/plugins/paper-trading/page").status_code == 200

    quotes = client.get("/api/plugins/paper-trading/quotes").json()
    assert len(quotes["quotes"]) == 30
    assert quotes["trading"] in (True, False)

    buy = client.post(
        "/api/plugins/paper-trading/orders",
        json={"code": "600519", "side": "buy", "order_type": "market", "qty": 100},
    ).json()
    assert buy["ok"] is True

    acc = client.get("/api/plugins/paper-trading/account").json()
    assert len(acc["positions"]) == 1

    orders = client.get("/api/plugins/paper-trading/orders").json()
    assert len(orders["orders"]) == 1

    assert client.get("/api/plugins/paper-trading/quote/999999").status_code == 404


def test_authenticated_host_only_mounts_static_landing_pages(tmp_path: Path) -> None:
    """A process-wide portfolio/account must never be shared between users."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.platform.ui._app_auth import _install_legacy_control_plane_auth
    from runtime.safety.auth import Identity, IdentityStore

    app = FastAPI()
    app.state.echo_require_auth = True
    app.state.echo_allow_local_workspace_access = True
    plugin = PaperTradingPlugin()
    plugin.on_load(
        ModuleContext(
            plugin_name=PLUGIN_ID,
            plugin_dir=str(PLUGIN_DIR),
            manifest=None,
            fastapi_app=app,
            config={
                "data_dir": str(tmp_path / "pt"),
                "base_url": "https://up.test/api",
                "live_mode": True,
                "auto_trade": True,
                "proxy_origin": True,
                "allow_same_origin_third_party_scripts": True,
            },
        )
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
    page = client.get("/api/plugins/paper-trading/page")
    assert page.status_code == 200
    assert "当前实例已开启身份认证" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert client.get("/api/plugins/paper-trading/watch").status_code == 200
    assert client.get("/api/plugins/paper-trading/quotes/status").status_code == 401
    assert (
        client.get(
            "/api/plugins/paper-trading/quotes/snapshot",
            params={"codes": "600000"},
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/api/plugins/paper-trading/quotes/stream",
            params={"codes": "600000"},
        ).status_code
        == 401
    )
    assert client.get("/api/plugins/paper-trading/account").status_code == 401
    assert (
        client.get(
            "/api/plugins/paper-trading/account",
            headers={"Authorization": "Bearer sk-alice"},
        ).status_code
        == 404
    )
    assert plugin.live is None
    assert plugin.auto_trade is False
    assert plugin.proxy_origin is False
    assert app.state.paper_trading_trusted_single_user_local_proxy is False
    assert not any(type(route).__name__ == "APIWebSocketRoute" for route in app.routes)


def test_authenticated_loopback_requires_explicit_trusted_proxy_flag(tmp_path: Path) -> None:
    """The opt-in mounts only the proxy/check-in slice and never enables agent trading."""
    from fastapi import FastAPI

    app = FastAPI()
    app.state.echo_require_auth = True
    app.state.echo_allow_local_workspace_access = True
    plugin = PaperTradingPlugin()
    plugin.on_load(
        ModuleContext(
            plugin_name=PLUGIN_ID,
            plugin_dir=str(PLUGIN_DIR),
            manifest=None,
            fastapi_app=app,
            config={
                "data_dir": str(tmp_path / "pt"),
                "base_url": "https://up.test/api",
                "live_mode": True,
                "auto_trade": True,
                "proxy_origin": True,
                "allow_same_origin_third_party_scripts": True,
                "trusted_single_user_local_proxy": True,
            },
        )
    )

    from tests.route_utils import route_paths

    paths = route_paths(app)
    assert plugin.proxy_origin is True
    assert plugin.sign_in_service is not None
    assert plugin.sign_in_scheduler is not None
    assert plugin.live is None
    assert plugin.auto_trade is False
    assert app.state.paper_trading_trusted_single_user_local_proxy is True
    assert "/api/plugins/paper-trading/page" in paths
    assert "/api/plugins/paper-trading/watch" in paths
    assert "/api/plugins/paper-trading/check-in/status" in paths
    assert "/api/plugins/paper-trading/check-in" in paths
    assert "/api/plugins/paper-trading/origin/{upstream_path:path}" in paths
    assert "/api/plugins/paper-trading/account" not in paths
    assert "/api/plugins/paper-trading/orders" not in paths
    assert not any(path.startswith("/api/plugins/paper-trading/platform/") for path in paths)

    plugin.on_stop(plugin.ctx)
    assert app.state.paper_trading_trusted_single_user_local_proxy is False
    plugin.on_start(plugin.ctx)
    assert app.state.paper_trading_trusted_single_user_local_proxy is True
    plugin.on_unload(plugin.ctx)
    assert app.state.paper_trading_trusted_single_user_local_proxy is False


def test_trusted_proxy_flag_is_rejected_without_local_loopback_posture(tmp_path: Path) -> None:
    from fastapi import FastAPI

    app = FastAPI()
    app.state.echo_require_auth = True
    app.state.echo_allow_local_workspace_access = False
    plugin = PaperTradingPlugin()
    plugin.on_load(
        ModuleContext(
            plugin_name=PLUGIN_ID,
            plugin_dir=str(PLUGIN_DIR),
            manifest=None,
            fastapi_app=app,
            config={
                "data_dir": str(tmp_path / "pt"),
                "base_url": "https://up.test/api",
                "proxy_origin": True,
                "allow_same_origin_third_party_scripts": True,
                "trusted_single_user_local_proxy": True,
            },
        )
    )

    assert plugin.proxy_origin is False
    assert plugin.sign_in_service is None
    assert app.state.paper_trading_trusted_single_user_local_proxy is False


# ── 自选股分组(watchlist) ───────────────────────────────


def _watch(tmp_path: Path) -> WatchlistStore:
    return WatchlistStore(data_dir=str(tmp_path / "wl"))


def test_watchlist_default_group_and_persistence(tmp_path: Path) -> None:
    store = _watch(tmp_path)
    groups = store.list()
    assert len(groups) == 1
    assert groups[0]["name"] == "默认自选"
    assert (tmp_path / "wl" / "watchlists.json").exists()

    # 重开一次,默认组仍在
    store2 = _watch(tmp_path)
    assert store2.list()[0]["name"] == "默认自选"


def test_watchlist_add_remove_and_groups(tmp_path: Path) -> None:
    store = _watch(tmp_path)
    default_id = store.default_group().id

    r = store.add_stock(default_id, "600519")
    assert r["ok"] and r["in_watchlist"]
    assert store.has_code("600519")

    g = store.create_group("白酒")
    gid = g["group"]["id"]
    store.add_stock(gid, "000858")
    assert store.add_stock(gid, "000858")["ok"]  # 幂等

    by_id = {x["id"]: x for x in store.list()}
    assert by_id[gid]["codes"] == ["000858"]
    assert by_id[default_id]["codes"] == ["600519"]

    # 删除分组后股票从自选移除
    store.delete_group(gid)
    assert not store.has_code("000858")


def test_watchlist_rename_and_delete_group(tmp_path: Path) -> None:
    store = _watch(tmp_path)
    g = store.create_group("A")["group"]
    assert store.rename_group(g["id"], "B")["group"]["name"] == "B"
    assert store.rename_group(g["id"], "  ")["ok"] is False
    assert store.delete_group(g["id"])["ok"] is True
    assert store.delete_group("nope")["ok"] is False


def test_watchlist_add_unknown_code_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """路由层:未知代码被拒;已存在的分组/股票走完整 CRUD。"""
    monkeypatch.setattr(pt_service, "is_trading_time", lambda now=None: True)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    plugin = PaperTradingPlugin()
    ctx = ModuleContext(
        plugin_name=PLUGIN_ID,
        plugin_dir=str(PLUGIN_DIR),
        manifest=None,
        fastapi_app=app,
        config={"initial_cash": 1_000_000, "data_dir": str(tmp_path / "pt")},
    )
    plugin.on_load(ctx)
    client = TestClient(app)

    bad = client.post(
        "/api/plugins/paper-trading/watchlists/default/stocks", json={"code": "999999"}
    ).json()
    assert bad["ok"] is False

    fav = client.post("/api/plugins/paper-trading/watchlists/fav", json={"code": "600519"}).json()
    assert fav["in_watchlist"] is True
    unfav = client.post("/api/plugins/paper-trading/watchlists/fav", json={"code": "600519"}).json()
    assert unfav["in_watchlist"] is False

    wl = client.get("/api/plugins/paper-trading/watchlists").json()
    assert "默认自选" in [g["name"] for g in wl["groups"]]
    assert "600519" in wl["universe"][0]["code"]


# ── 交易终端:十档盘口 ───────────────────────────────────


def test_engine_order_book(tmp_path: Path) -> None:
    """十档盘口:买/卖各 10 档,围绕现价步进,价格递增排序。"""
    engine = _engine(tmp_path)
    book = engine.order_book("600519")

    assert book is not None
    assert book["code"] == "600519"
    assert len(book["bids"]) == 10
    assert len(book["asks"]) == 10
    # 卖档价从近到远递增、买档价从近到远递减;卖一价 > 买一价
    assert book["asks"][0]["price"] > book["price"]
    assert book["bids"][0]["price"] < book["price"]
    for i in range(1, 10):
        assert book["asks"][i]["price"] > book["asks"][i - 1]["price"]
        assert book["bids"][i]["price"] < book["bids"][i - 1]["price"]
    # 未知代码
    assert engine.order_book("999999") is None


def test_orderbook_route(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    plugin = PaperTradingPlugin()
    ctx = ModuleContext(
        plugin_name=PLUGIN_ID,
        plugin_dir=str(PLUGIN_DIR),
        manifest=None,
        fastapi_app=app,
        config={"initial_cash": 1_000_000, "data_dir": str(tmp_path / "pt")},
    )
    plugin.on_load(ctx)
    client = TestClient(app)

    d = client.get("/api/plugins/paper-trading/orderbook/600519").json()
    assert len(d["bids"]) == 10 and len(d["asks"]) == 10
    assert client.get("/api/plugins/paper-trading/orderbook/999999").status_code == 404


# ── 平台原站同源反代(proxy_origin) ──────────────────────


def _proxy_plugin(tmp_path: Path, **cfg: object) -> PaperTradingPlugin:
    from fastapi import FastAPI

    app = FastAPI()
    plugin = PaperTradingPlugin()
    ctx = ModuleContext(
        plugin_name=PLUGIN_ID,
        plugin_dir=str(PLUGIN_DIR),
        manifest=None,
        fastapi_app=app,
        config={"data_dir": str(tmp_path / "pt"), **cfg},
    )
    plugin.on_load(ctx)
    plugin._test_app = app  # type: ignore[attr-defined]
    return plugin


def test_proxy_origin_enabled_by_default(tmp_path: Path) -> None:
    """缺省配置直接挂载原站代理并展示 iframe 页面。"""
    from fastapi.testclient import TestClient

    plugin = _proxy_plugin(tmp_path)
    assert plugin.proxy_origin is True
    client = TestClient(plugin._test_app)  # type: ignore[attr-defined]

    page = client.get("/api/plugins/paper-trading/page")
    assert page.status_code == 200
    assert "<iframe" in page.text
    assert 'src="origin/trade/#/transaction"' in page.text


def test_proxy_origin_rewrites_html_and_injects_session(tmp_path: Path) -> None:
    """开启后:中和 electron 崩溃行 + 注入已缓存登录态。"""
    import base64
    import json

    import httpx
    from fastapi import APIRouter
    from fastapi.testclient import TestClient

    from runtime.platform.plugins.bundled.paper_trading.proxy import register_origin_proxy

    # 造一个带 memberId 的假 JWT(仅 payload 段需可解)
    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"memberId": "42", "account": "HL1", "phone": "138"}).encode()
        )
        .decode()
        .rstrip("=")
    )
    state = tmp_path / "pt"
    state.mkdir(parents=True, exist_ok=True)
    (state / "token.json").write_text(json.dumps({"token": f"h.{payload}.s"}), encoding="utf-8")

    upstream_html = (
        "<html><head></head><body>"
        "<script>var shell = window.require('electron').shell</script>"
        '<script>function P(){return"browser"===window.PLATFORM?"/api":0}</script>'
        "</body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html=upstream_html)

    router = APIRouter(prefix="/api/plugins/paper-trading")
    assert register_origin_proxy(
        router,
        base_url="https://up.test:9/api",
        state_dir=str(state),
        credentials_file=str(state / "credentials.json"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    resp = TestClient(app).get("/api/plugins/paper-trading/origin/trade/")

    assert resp.status_code == 200
    # electron 崩溃行已中和
    assert "window.require('electron')" not in resp.text
    assert "var shell = null" in resp.text
    # 登录态已注入,且 userInfo 是字符串形式(上游用 JSON.parse 读)
    assert "if(!localStorage.getItem('userInfo'))" in resp.text
    assert "localStorage.setItem('userInfo'" in resp.text
    assert '\\"memberId\\": \\"42\\"' in resp.text or '\\"memberId\\":\\"42\\"' in resp.text
    # API 基址已指向代理前缀,否则页面内请求会打到本机 /api 上
    assert '"/api/plugins/paper-trading/origin/api"' in resp.text


def test_proxy_never_forwards_echo_authorization_to_upstream(tmp_path: Path) -> None:
    import httpx
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    from runtime.platform.plugins.bundled.paper_trading.proxy import register_origin_proxy

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        # A text response exercises the proxy's buffered rewrite branch; a
        # MockTransport JSON response is already consumed before raw streaming.
        return httpx.Response(
            200,
            text="window.ok=true",
            headers={"content-type": "application/javascript"},
        )

    router = APIRouter(prefix="/api/plugins/paper-trading")
    assert register_origin_proxy(
        router,
        base_url="https://up.test/api",
        state_dir=str(tmp_path),
        credentials_file=str(tmp_path / "credentials.json"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    app = FastAPI()
    app.include_router(router)

    response = TestClient(app).get(
        "/api/plugins/paper-trading/origin/api/market",
        headers={
            "Authorization": "Bearer echo-host-token",
            "token": "platform-token",
        },
    )

    assert response.status_code == 200
    assert len(seen) == 1
    assert "authorization" not in seen[0].headers
    assert seen[0].headers["token"] == "platform-token"


def test_proxy_origin_rejects_paths_outside_allowlist(tmp_path: Path) -> None:
    """只允许 trade//api//static/ 三个前缀,避免退化成任意 URL 转发器。"""
    import httpx
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    from runtime.platform.plugins.bundled.paper_trading.proxy import register_origin_proxy

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("上游不应被访问")

    router = APIRouter(prefix="/api/plugins/paper-trading")
    register_origin_proxy(
        router,
        base_url="https://up.test:9/api",
        state_dir=str(tmp_path / "pt"),
        credentials_file=str(tmp_path / "pt" / "credentials.json"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    for bad in ("evil.com/x", "../etc/passwd", "admin/panel"):
        assert client.get(f"/api/plugins/paper-trading/origin/{bad}").status_code == 404


def test_proxy_origin_not_mounted_when_base_url_unusable(tmp_path: Path) -> None:
    """base_url 解不出 origin 时不挂路由,而不是挂一个必然 500 的路由。"""
    from fastapi import APIRouter

    from runtime.platform.plugins.bundled.paper_trading.proxy import register_origin_proxy

    router = APIRouter(prefix="/api/plugins/paper-trading")
    assert register_origin_proxy(router, base_url="") is False
    assert register_origin_proxy(router, base_url="not-a-url") is False


def test_proxy_origin_accepts_plain_http_upstream() -> None:
    import httpx
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    from runtime.platform.plugins.bundled.paper_trading.proxy import register_origin_proxy

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            text="window.ok=true",
            headers={"content-type": "application/javascript"},
        )

    router = APIRouter(prefix="/api/plugins/paper-trading")
    assert register_origin_proxy(
        router,
        base_url="http://up.test:9/api",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    app = FastAPI()
    app.include_router(router)

    response = TestClient(app).get("/api/plugins/paper-trading/origin/api/market")

    assert response.status_code == 200
    assert str(seen[0].url) == "http://up.test:9/api/market"


def test_proxy_origin_can_be_explicitly_disabled(tmp_path: Path) -> None:
    proxy_disabled = _proxy_plugin(
        tmp_path,
        proxy_origin=False,
        base_url="https://up.test:9/api",
    )
    assert proxy_disabled.proxy_origin is False

    risk_acceptance_disabled = _proxy_plugin(
        tmp_path,
        proxy_origin=True,
        allow_same_origin_third_party_scripts=False,
        base_url="https://up.test:9/api",
    )
    assert risk_acceptance_disabled.proxy_origin is False


@pytest.mark.parametrize("string_value", ["false", "true"])
def test_security_switches_reject_string_booleans(
    tmp_path: Path,
    string_value: str,
) -> None:
    from fastapi import FastAPI

    registry = MagicMock()
    plugin = PaperTradingPlugin()
    plugin.on_load(
        ModuleContext(
            plugin_name=PLUGIN_ID,
            plugin_dir=str(PLUGIN_DIR),
            manifest=None,
            fastapi_app=FastAPI(),
            skill_registry=registry,
            config={
                "data_dir": str(tmp_path / "pt"),
                "base_url": "https://up.test/api",
                "live_mode": string_value,
                "auto_trade": string_value,
                "proxy_origin": string_value,
                "allow_same_origin_third_party_scripts": string_value,
            },
        )
    )

    assert plugin.live is None
    assert plugin.auto_trade is False
    assert plugin.proxy_origin is False
    registered = [call.args[0].name for call in registry.register.call_args_list]
    assert registered == ["paper_trading.quote", "paper_trading.live_quotes"]


def test_live_and_auto_trade_reject_plain_http_upstream(tmp_path: Path) -> None:
    from fastapi import FastAPI

    registry = MagicMock()
    plugin = PaperTradingPlugin()
    plugin.on_load(
        ModuleContext(
            plugin_name=PLUGIN_ID,
            plugin_dir=str(PLUGIN_DIR),
            manifest=None,
            fastapi_app=FastAPI(),
            skill_registry=registry,
            config={
                "data_dir": str(tmp_path / "pt"),
                "base_url": "http://up.test/api",
                "live_mode": True,
                "auto_trade": True,
            },
        )
    )

    assert plugin.live is None
    assert plugin.auto_trade is False
    assert plugin.proxy_origin is True
    registered = [call.args[0].name for call in registry.register.call_args_list]
    assert registered == ["paper_trading.quote"]


def test_reload_and_unload_stop_existing_push(tmp_path: Path) -> None:
    from fastapi import FastAPI

    plugin = PaperTradingPlugin()
    old_push = MagicMock()
    plugin.push = old_push
    plugin.on_load(
        ModuleContext(
            plugin_name=PLUGIN_ID,
            plugin_dir=str(PLUGIN_DIR),
            manifest=None,
            fastapi_app=FastAPI(),
            config={"data_dir": str(tmp_path / "pt")},
        )
    )
    old_push.stop.assert_called_once_with()
    assert plugin.push is None

    active_push = MagicMock()
    plugin.push = active_push
    plugin.on_unload(plugin.ctx)
    active_push.stop.assert_called_once_with()
    assert plugin.push is None


@pytest.mark.parametrize(
    "base_url",
    [
        "https://[::1",
        "https://example.com:bad/api",
        "https://user:password@example.com/api",
        "https://bad host.example/api",
    ],
)
def test_proxy_origin_rejects_malformed_or_credentialed_urls(
    tmp_path: Path,
    base_url: str,
) -> None:
    plugin = _proxy_plugin(
        tmp_path,
        proxy_origin=True,
        allow_same_origin_third_party_scripts=True,
        base_url=base_url,
    )

    assert plugin.proxy_origin is False


def test_proxy_disabled_page_escapes_configured_origin(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    plugin = _proxy_plugin(
        tmp_path,
        base_url='https://safe.example"><svg onload=alert(document.domain)>/api',
    )

    response = TestClient(plugin._test_app).get(  # type: ignore[attr-defined]
        "/api/plugins/paper-trading/page"
    )

    assert response.status_code == 200
    assert "<svg" not in response.text
    assert "default-src 'none'" in response.headers["content-security-policy"]


def test_proxy_origin_rewrites_both_api_bases() -> None:
    """原站有两处独立的 API 基址计算,必须都改写。

    实测教训:只改 ``P()`` 时页面能加载、少数接口(getS3Configs)也通,
    但绝大多数业务接口走 ``global.getUrl("/api")``,会绕过代理打到本机
    ``/api/*`` 上全部 404,页面表现为"暂无合约 + 网络线路不佳"。
    """
    from runtime.platform.plugins.bundled.paper_trading.proxy import rewrite_js

    src = (
        'function P(){return"browser"===window.PLATFORM?"/api":x}'
        ';g["global"].getUrl("/api"),g["global"].getSocketIoUrl(h)'
    )
    out = rewrite_js(src)

    prefix = "/api/plugins/paper-trading/origin/api"
    assert f'window.PLATFORM?"{prefix}"' in out
    assert f'.getUrl("{prefix}")' in out
    # 不留任何裸的绝对基址
    assert '"/api"' not in out


def test_proxy_origin_rewrites_socket_io_path() -> None:
    """socket.io 也得改写:持仓列表和合约详情是 WS 推送的,不是 HTTP。

    改的是 vendors chunk 里真正建连的 ``io()`` 调用(加 ``path`` 选项);
    app.js 里的 ``getSocketIoUrl(e)`` 只接一个参数,在调用点塞第二个参数会被忽略。
    """
    from runtime.platform.plugins.bundled.paper_trading.proxy import rewrite_js

    src = 't.socketIo=o()(t.getSocketIoUrlPath,{forceNew:!1,transports:["websocket"]})'
    out = rewrite_js(src)

    assert 'path:"/api/plugins/paper-trading/origin/socket.io"' in out


def test_proxy_origin_registers_websocket_route(tmp_path: Path) -> None:
    """WS 路由必须带 /origin 前缀 —— 漏了它客户端会收到 403(Starlette 对未匹配 WS 的响应)。"""
    from fastapi import APIRouter

    from runtime.platform.plugins.bundled.paper_trading.proxy import register_origin_proxy

    router = APIRouter(prefix="/api/plugins/paper-trading")
    register_origin_proxy(
        router,
        base_url="https://up.test:9/api",
        state_dir=str(tmp_path),
        credentials_file=str(tmp_path / "credentials.json"),
    )

    ws_paths = [r.path for r in router.routes if type(r).__name__ == "APIWebSocketRoute"]
    assert ws_paths == ["/api/plugins/paper-trading/origin/socket.io/{ws_path:path}"]


def test_proxy_origin_allows_socket_io_prefix() -> None:
    """socket.io 要进路径白名单,否则 HTTP polling 回退会被 404 拦掉。"""
    from runtime.platform.plugins.bundled.paper_trading.proxy import _safe_upstream_path

    assert _safe_upstream_path("socket.io/") == "socket.io/"


@pytest.mark.parametrize(
    "path",
    [
        "trade/%2e%2e/admin",
        "trade/%252e%252e/admin",
        "trade/%255c..%255cadmin",
        "trade/%25invalid",
    ],
)
def test_proxy_origin_rejects_nested_encoded_path_ambiguity(path: str) -> None:
    from fastapi import HTTPException

    from runtime.platform.plugins.bundled.paper_trading.proxy import _safe_upstream_path

    with pytest.raises(HTTPException) as exc_info:
        _safe_upstream_path(path)
    assert exc_info.value.status_code == 404

