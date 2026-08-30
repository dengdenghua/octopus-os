"""Tests for the ``paper_trading`` plugin's optional live (平台只读行情) source.

关键点:所有网络调用都被 mock,测试**不真连网**、不真登录。
覆盖:

  1. 无凭证 → 优雅降级 ``available: False``,不影响本地功能
  2. 有 mock 客户端 → 正常解析出真实指数 / 涨跌家数 / 市场状态
  3. TTL 缓存:ttl 内重复拉取不重复请求
  4. 网络异常 → 降级,不向外抛异常
  5. ``/live/overview`` 路由:未启用/启用两条路径
  6. gzip 解压工具:base64 → gzip → JSON
"""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from runtime.platform.plugins.bundled.paper_trading import PaperTradingPlugin
from runtime.platform.plugins.bundled.paper_trading.live import (
    DEFAULT_BASE_URL,
    LiveDataSource,
    PlatformClient,
)
from runtime.platform.plugins.plugin_base import ModuleContext

PLUGIN_DIR = (
    Path(__file__).resolve().parents[1]
    / "runtime"
    / "platform"
    / "plugins"
    / "bundled"
    / "paper_trading"
)


def _fake_today() -> dict:
    return {
        "up": 518,
        "down": 4905,
        "unchanged": 35,
        "stop": 80,
        "stockStatus": "交易中",
        "stockVOS": [
            {
                "symbol": "000001.sh",
                "name": "上证指数",
                "price": 3894.42,
                "risefall": -95.88,
                "increase": -2.4,
                "yClose": 3990.3,
                "increases": [-1.0, -1.2, -1.4, -1.6],
            },
            {
                "symbol": "399001.sz",
                "name": "深证指数",
                "price": 13890.15,
                "risefall": -732.34,
                "increase": -5.01,
                "yClose": 14622.5,
                "increases": [-2.0, -2.1, -2.2],
            },
            {"symbol": "999999.zz", "name": "坏数据", "price": None},
        ],
    }


def test_platform_http_client_rejects_redirects_with_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trusted HTTPS URL cannot redirect platform tokens to another origin."""
    import io
    import urllib.error

    from runtime.platform.plugins.bundled.paper_trading import live as live_module

    requests = []

    class _RedirectingOpener:
        def open(self, request, timeout):  # noqa: ANN001
            requests.append(request)
            raise urllib.error.HTTPError(
                request.full_url,
                302,
                "Found",
                {"Location": "http://evil.test/collect"},
                io.BytesIO(b"redirect refused"),
            )

    monkeypatch.setattr(live_module, "_NO_REDIRECT_OPENER", _RedirectingOpener())
    client = PlatformClient("https://trusted.test/api")
    client._token = "platform-secret"

    with pytest.raises(live_module.PlatformClientError, match="HTTP 302"):
        client._request_once("GET", "/market")

    assert len(requests) == 1
    assert requests[0].full_url == "https://trusted.test/api/market"
    assert requests[0].get_header("Authorization") == "Bearer platform-secret"
    assert requests[0].get_header("Token") == "platform-secret"


def test_no_redirect_handler_never_builds_followup_request() -> None:
    from runtime.platform.plugins.bundled.paper_trading.live import _NoRedirectHandler

    handler = _NoRedirectHandler()
    assert (
        handler.redirect_request(
            None,
            None,
            302,
            "Found",
            {"Location": "http://evil.test/collect"},
            "http://evil.test/collect",
        )
        is None
    )


def test_platform_http_client_rejects_plain_http_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.platform.plugins.bundled.paper_trading import live as live_module

    opener = MagicMock()
    monkeypatch.setattr(live_module, "_NO_REDIRECT_OPENER", opener)
    client = PlatformClient("http://up.test/api")
    client._token = "platform-secret"

    with pytest.raises(live_module.PlatformClientError, match="HTTPS"):
        client._request_once("GET", "/market")

    opener.open.assert_not_called()


def _mock_client() -> MagicMock:
    client = MagicMock(spec=PlatformClient)
    client.has_credentials = True
    client.base_url = DEFAULT_BASE_URL
    client.login.return_value = "fake-jwt-token"
    client.fetch_today_stock.return_value = _fake_today()
    return client


# ── 1. 无凭证 → 降级 ─────────────────────────────────────


def test_live_without_credentials_degrades(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PAPER_TRADING_PHONE", raising=False)
    monkeypatch.delenv("PAPER_TRADING_PASSWORD", raising=False)
    client = PlatformClient(
        base_url=DEFAULT_BASE_URL, state_dir=str(tmp_path / "pt")
    )  # 无 phone/password
    src = LiveDataSource(client, ttl=5)

    ov = src.overview()

    assert ov["available"] is False
    assert "凭证" in ov["error"] or "PAPER_TRADING" in ov["error"]
    assert ov["indices"] == []
    assert ov["breadth"] == {"up": 0, "down": 0, "unchanged": 0, "stop": 0}


# ── 2. 正常解析 ──────────────────────────────────────────


def test_live_overview_parses_indices_and_breadth() -> None:
    src = LiveDataSource(_mock_client(), ttl=5)

    ov = src.overview()

    assert ov["available"] is True
    assert ov["status"] == "交易中"
    assert ov["source"] == DEFAULT_BASE_URL
    assert ov["breadth"] == {"up": 518, "down": 4905, "unchanged": 35, "stop": 80}
    names = [ix["name"] for ix in ov["indices"]]
    assert names == ["上证指数", "深证指数"]  # 无 price 的坏数据被跳过
    sh = ov["indices"][0]
    assert sh["price"] == 3894.42
    assert sh["change"] == -95.88
    assert sh["change_pct"] == -2.4
    assert sh["spark"] == [-1.0, -1.2, -1.4, -1.6]


# ── 3. TTL 缓存 ──────────────────────────────────────────


def test_live_overview_caches_within_ttl() -> None:
    client = _mock_client()
    src = LiveDataSource(client, ttl=60)

    src.overview()
    src.overview()

    assert client.fetch_today_stock.call_count == 1
    # 强制刷新才会再拉
    src.overview(force=True)
    assert client.fetch_today_stock.call_count == 2


# ── 4. 网络异常 → 降级 ───────────────────────────────────


def test_live_overview_degrades_on_error() -> None:
    client = _mock_client()
    client.fetch_today_stock.side_effect = RuntimeError("连接超时")
    src = LiveDataSource(client, ttl=5)

    ov = src.overview()

    assert ov["available"] is False
    assert "连接超时" in ov["error"]
    # 不向外抛异常
    assert isinstance(ov, dict)


# ── 5. 路由两条路径 ──────────────────────────────────────


def _plugin(tmp_path: Path, live_mode: bool) -> tuple[PaperTradingPlugin, TestClient]:
    from fastapi import FastAPI

    app = FastAPI()
    plugin = PaperTradingPlugin()
    ctx = ModuleContext(
        plugin_name="paper_trading",
        plugin_dir=str(PLUGIN_DIR),
        manifest=None,
        fastapi_app=app,
        config={
            "initial_cash": 1_000_000,
            "data_dir": str(tmp_path / "pt"),
            "live_mode": live_mode,
        },
    )
    plugin.on_load(ctx)
    return plugin, TestClient(app)


def test_live_route_when_disabled(tmp_path: Path) -> None:
    plugin, client = _plugin(tmp_path, live_mode=False)
    assert plugin.live is None

    r = client.get("/api/plugins/paper-trading/live/overview").json()
    assert r["available"] is False
    assert r["enabled"] is False
    assert "未启用" in r["message"]


def test_live_route_with_source(tmp_path: Path) -> None:
    plugin, client = _plugin(tmp_path, live_mode=False)
    live = MagicMock(spec=LiveDataSource)
    live.overview.return_value = {
        "available": True,
        "source": DEFAULT_BASE_URL,
        "status": "交易中",
        "indices": [{"symbol": "000001.sh", "name": "上证指数", "price": 3894.42}],
        "breadth": {"up": 1, "down": 2, "unchanged": 0, "stop": 0},
    }
    plugin.live = live

    r = client.get("/api/plugins/paper-trading/live/overview").json()
    assert r["available"] is True
    assert r["indices"][0]["name"] == "上证指数"


# ── 6. gzip 解压工具 ─────────────────────────────────────


def test_gunzip_b64_roundtrip() -> None:
    raw = json.dumps({"up": 1, "down": 2}).encode()
    payload = base64.b64encode(gzip.compress(raw)).decode()

    parsed = PlatformClient._gunzip_b64(payload)

    assert parsed == {"up": 1, "down": 2}


def test_gunzip_b64_bad_payload() -> None:
    from runtime.platform.plugins.bundled.paper_trading.live import PlatformClientError

    with pytest.raises(PlatformClientError):
        PlatformClient._gunzip_b64(base64.b64encode(b"not-gzip-json").decode())


# ── 登录界面(凭证)路由 ─────────────────────────────────


def test_live_status_when_disabled(tmp_path: Path) -> None:
    plugin, client = _plugin(tmp_path, live_mode=False)

    r = client.get("/api/plugins/paper-trading/live/status").json()
    assert r["enabled"] is False
    assert r["configured"] is False


def test_live_credentials_save_and_clear_route(tmp_path: Path) -> None:
    plugin, client = _plugin(tmp_path, live_mode=False)
    live = MagicMock(spec=LiveDataSource)
    live.save_credentials.return_value = {
        "ok": True,
        "saved": True,
        "verified": True,
        "account": "HL51550949",
    }
    live.clear_credentials.return_value = {"ok": True, "message": "已清除平台凭证"}
    live.configured = False
    live.available = False
    live.account = ""
    live.client = MagicMock()
    live.client.base_url = DEFAULT_BASE_URL
    plugin.live = live

    r = client.post(
        "/api/plugins/paper-trading/live/credentials",
        json={"phone": "18685323548", "password": "secret"},
    ).json()
    assert r["saved"] is True
    assert r["account"] == "HL51550949"
    live.save_credentials.assert_called_once_with("18685323548", "secret")

    c = client.post("/api/plugins/paper-trading/live/credentials/clear").json()
    assert c["ok"] is True
    live.clear_credentials.assert_called_once()


def test_live_credentials_route_rejects_empty(tmp_path: Path) -> None:
    plugin, client = _plugin(tmp_path, live_mode=False)
    live = MagicMock(spec=LiveDataSource)
    live.save_credentials.return_value = {
        "saved": False,
        "ok": False,
        "error": "手机号和密码不能为空",
    }
    plugin.live = live

    r = client.post(
        "/api/plugins/paper-trading/live/credentials",
        json={"phone": "", "password": ""},
    ).json()
    assert r["saved"] is False


# ── PlatformClient 凭证文件(chmod 600) ──────────────────


def test_save_credentials_writes_0600_file(tmp_path: Path) -> None:
    client = PlatformClient(base_url=DEFAULT_BASE_URL, state_dir=str(tmp_path / "pt"))
    creds = str(tmp_path / "pt" / "credentials.json")

    path = client.save_credentials("13800000000", "p@ss", creds)

    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600
    import json as _json

    assert _json.loads(path.read_text(encoding="utf-8")) == {
        "phone": "13800000000",
        "password": "p@ss",
    }
    # 内存同步
    assert client.phone == "13800000000"
    assert client.password == "p@ss"


def test_clear_credentials_removes_file(tmp_path: Path) -> None:
    client = PlatformClient(base_url=DEFAULT_BASE_URL, state_dir=str(tmp_path / "pt"))
    creds = str(tmp_path / "pt" / "credentials.json")
    client.save_credentials("13800000000", "p@ss", creds)
    client._token = "jwt"

    client.clear_credentials(creds)

    assert not Path(creds).exists()
    assert client.phone == ""
    assert client.password == ""
    assert client._token is None


def test_mask_phone() -> None:
    from runtime.platform.plugins.bundled.paper_trading.live import _mask_phone

    assert _mask_phone("18685323548") == "186****3548"
    assert _mask_phone("123") == "***"
    assert _mask_phone("") == ""


# ── 委托/成交与资金流水解析 ──────────────────────────────


def _client_with_request(response_data) -> PlatformClient:
    client = PlatformClient(base_url=DEFAULT_BASE_URL, state_dir="/tmp/pt-test")
    client._token = "test-token"
    client._request = MagicMock(return_value={"code": 1, "data": response_data})
    return client


def test_orders_normalizes_wrapped_stock_order_vos() -> None:
    """非空响应:data 是 gzip 的 {stockOrderVOList, pages},应归一化为 {list,pages}。"""
    raw = {
        "stockOrderVOList": [
            {
                "stockCode": "600519",
                "stockName": "贵州茅台",
                "buySell": "buy",
                "entrustType": "1",
                "entrustPrice": 1700.5,
                "dealNumber": 100,
                "createTime": "2026-08-19 09:30:00",
                "stateText": "已成交",
            },
            {
                "stockCode": "000858",
                "stockName": "五粮液",
                "buySell": "sell",
                "entrustType": "0",
                "entrustPrice": 130.2,
                "entrustNumber": 200,
                "createTime": "2026-08-19 09:31:00",
                "stateName": "已成交",
            },
        ],
        "pages": 1,
    }
    gz = base64.b64encode(gzip.compress(json.dumps(raw).encode())).decode()
    client = _client_with_request(gz)
    out = client.orders(type_=1)
    assert out["type"] == 1
    assert out["pages"] == 1
    assert len(out["list"]) == 2
    assert out["list"][0]["stockCode"] == "600519"
    assert out["list"][1]["stockName"] == "五粮液"


def test_orders_normalizes_empty_list() -> None:
    """空记录:data 直接是 [] → {list:[], pages:1}。"""
    client = _client_with_request([])
    out = client.orders(type_=1)
    assert out == {"list": [], "pages": 1, "total": 0, "type": 1}


def test_orders_passes_contract_id() -> None:
    client = _client_with_request([])
    client.orders(contract_id="abc123", type_=2, size=50)
    payload = client._request.call_args.args[2]
    assert payload["contractId"] == "abc123"
    assert payload["type"] == 2
    assert payload["size"] == 50
    assert payload["memberId"] == client.member_id


def test_money_records_parses_list() -> None:
    client = _client_with_request(
        [
            {
                "createTime": "2026-08-19 10:00:00",
                "typeName": "买入成功",
                "money": 170050.0,
                "balance": 776.77,
                "remark": "买入 600519",
            },
            {
                "createTime": "2026-08-19 10:05:00",
                "typeName": "卖出成功",
                "money": -100.0,
                "balance": 876.77,
                "remark": "卖出 000858",
            },
        ]
    )
    out = client.money_records()
    assert len(out) == 2
    assert out[0]["typeName"] == "买入成功"
    assert out[1]["money"] == -100.0


def test_money_records_empty() -> None:
    client = _client_with_request([])
    assert client.money_records() == []


def test_orders_raises_on_non_ok_code() -> None:
    client = _client_with_request({"err": 1})
    client._request.return_value = {"code": 40013, "message": "登录过期"}
    try:
        client.orders()
    except Exception as exc:  # noqa: BLE001
        assert "委托记录失败" in str(exc)
    else:
        raise AssertionError("should have raised")


# ── 会话失效自动重登 ─────────────────────────────────────


def test_auth_expired_auto_relogin_and_retry(monkeypatch) -> None:
    """平台返回 code=20040(登录信息已过期)时,自动强制重登并重试一次。"""
    client = PlatformClient(base_url=DEFAULT_BASE_URL, state_dir="/tmp/pt-test")
    client._token = "stale-token"
    ok = {
        "code": 1,
        "data": {"optionalVOList": [{"stockCode": "920169", "stockName": "七丰精工"}]},
    }
    expired = {"code": 20040, "message": "您的登录信息已过期, 请重新登录~~"}

    calls = {"n": 0}

    def fake_once(method, path, payload=None, *, auth=True):
        calls["n"] += 1
        if calls["n"] == 1:
            return expired
        return ok

    client._request_once = fake_once
    relogin_called = {"n": 0}

    def fake_login(force=False):
        relogin_called["n"] += 1
        client._token = "fresh-token"
        return "fresh-token"

    client.login = fake_login

    out = client.fetch_stock_choose()
    assert out == [{"stockCode": "920169", "stockName": "七丰精工"}]
    assert calls["n"] == 2  # 失败一次 + 重试一次
    assert relogin_called["n"] == 1  # 强制重登恰好一次


def test_auth_expired_no_token_no_relogin() -> None:
    """未带鉴权(auth=False)的请求即使命中失效 code 也不触发重登。"""
    client = PlatformClient(base_url=DEFAULT_BASE_URL, state_dir="/tmp/pt-test")
    client._token = "x"
    expired = {"code": 20040, "message": "过期"}

    def fake_once(method, path, payload=None, *, auth=True):
        assert auth is False  # 内部请求(如拿公钥)不应重试
        return expired

    client._request_once = fake_once
    relogin = []

    def fake_login(force=False):
        relogin.append(force)
        return "t"

    client.login = fake_login
    resp = client._request("POST", "/x", {}, auth=False)
    assert resp["code"] == 20040
    assert relogin == []

