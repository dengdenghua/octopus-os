"""Tests for the ``paper_trading`` 盯盘(watch) feature.

盯盘 = 真实行情聚合面板:大盘指数 + 平台持仓 + 平台自选,全部来自平台只读接口,
带短 TTL 缓存,任一来源失败只降级对应字段。

安全红线:测试全部 mock,**绝不**真正连网。真实写操作仍然只在
``test_paper_trading_platform.py`` 里以 ``confirm:true`` + mock 覆盖。
"""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path
from unittest.mock import MagicMock

from fastapi import FastAPI
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


def _plugin(tmp_path: Path, live_mode: bool = False) -> tuple[PaperTradingPlugin, TestClient]:
    app = FastAPI()
    plugin = PaperTradingPlugin()
    ctx = ModuleContext(
        plugin_name="paper_trading",
        plugin_dir=str(PLUGIN_DIR),
        manifest=None,
        fastapi_app=app,
        config={
            "live_mode": live_mode,
            "initial_cash": 1_000_000,
            "data_dir": str(tmp_path / "pt"),
        },
    )
    plugin.on_load(ctx)
    return plugin, TestClient(app)


def _gzip_b64(obj) -> str:
    return base64.b64encode(gzip.compress(json.dumps(obj).encode())).decode()


def _jwt(member_id: str = "M1") -> str:
    import time

    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {"memberId": member_id, "account": "HL51550949", "exp": int(time.time()) + 3600}
        ).encode()
    ).rstrip(b"=")
    return f"{header.decode()}.{payload.decode()}.sig"


def _client_with_stub_request() -> tuple[PlatformClient, list]:
    """拦 _request,返回可配置的平台风格响应;token 带 memberId=M1。"""
    calls: list[tuple[str, str, dict]] = []
    responses: list[dict] = []

    def fake_request(method, path, payload=None, **kw):
        calls.append((method, path, payload))
        if responses:
            return responses.pop(0)
        return {"code": 1, "data": {"ok": True}}

    client = PlatformClient(base_url=DEFAULT_BASE_URL)
    client._request = fake_request  # type: ignore[method-assign]
    client._token = _jwt()
    client._stub_responses = responses  # type: ignore[attr-defined]
    return client, calls


# ── 1. PlatformClient 新方法 ─────────────────────────────


def test_client_fetch_stock_choose_payload_and_decode() -> None:
    client, calls = _client_with_stub_request()
    client._stub_responses.append(
        {
            "code": 1,
            "data": _gzip_b64(
                {"optionalVOList": [{"stockCode": "920169", "stockName": "七丰精工"}]}
            ),
        }
    )

    data = client.fetch_stock_choose()

    assert data == [{"stockCode": "920169", "stockName": "七丰精工"}]
    method, path, payload = calls[-1]
    assert method == "POST"
    assert path == "/stock/stockCodeV2"
    assert payload["memberId"] == "M1"
    assert payload["event"] == "subscribe"
    assert payload["isCompress"] is True


def test_client_fetch_stock_choose_raises_on_failure() -> None:
    client, calls = _client_with_stub_request()
    client._stub_responses.append({"code": 0, "message": "no"})
    try:
        client.fetch_stock_choose()
        raise AssertionError("should have raised")
    except Exception as exc:  # noqa: BLE001
        assert "自选失败" in str(exc)


def test_client_fetch_real_quotes_payload_and_decode() -> None:
    client, calls = _client_with_stub_request()
    client._stub_responses.append(
        {"code": 1, "data": _gzip_b64([{"stockCode": "605080", "currentPrice": 20.31}])}
    )

    data = client.fetch_real_quotes(["605080.sh", "003032.sz"])

    assert data == [{"stockCode": "605080", "currentPrice": 20.31}]
    _, path, payload = calls[-1]
    assert "event=kLineRealTime" in path
    assert payload["url"] == "kLineRealTime"
    assert payload["event"] == "subscribe"
    assert payload["params"] == ["605080.sh", "003032.sz"]


def test_client_fetch_real_quotes_wraps_single_quote_object() -> None:
    client, _calls = _client_with_stub_request()
    client._stub_responses.append(
        {"code": 1, "data": _gzip_b64({"stockCode": "600000", "currentPrice": 12.34})}
    )

    data = client.fetch_real_quotes(["600000.sh"])

    assert data == [{"stockCode": "600000", "currentPrice": 12.34}]


# ── 2. LiveDataSource.watch 聚合 + 缓存 + 降级 ─────────────


def _watch_live(client: MagicMock, ttl: float = 10.0) -> LiveDataSource:
    return LiveDataSource(client, ttl=ttl)


def test_watch_aggregates_overview_positions_watchlist() -> None:
    client = MagicMock(spec=PlatformClient)
    client.base_url = DEFAULT_BASE_URL
    client.login = MagicMock()
    client.positions.return_value = [{"code": "605080", "codeName": "浙江自然"}]
    client.fetch_stock_choose.return_value = [{"stockCode": "920169", "stockName": "七丰精工"}]
    # overview 依赖 _build_overview -> fetch_today_stock
    client.fetch_today_stock.return_value = {
        "stockStatus": "交易中",
        "stockVOS": [
            {
                "symbol": "000001.sh",
                "name": "上证指数",
                "price": 3894.42,
                "yClose": 3990.3,
                "risefall": -95.88,
                "increase": -2.4,
            }
        ],
        "up": 100,
        "down": 50,
        "unchanged": 10,
        "stop": 5,
    }

    live = _watch_live(client)
    data = live.watch(force=True)

    assert data["available"] is True
    assert data["status"] == "交易中"
    assert data["indices"][0]["symbol"] == "000001.sh"
    assert data["breadth"]["up"] == 100
    assert data["positions"] == [{"code": "605080", "codeName": "浙江自然"}]
    assert data["watchlist"] == [{"stockCode": "920169", "stockName": "七丰精工"}]


def test_watch_degrades_when_positions_fail() -> None:
    client = MagicMock(spec=PlatformClient)
    client.base_url = DEFAULT_BASE_URL
    client.login = MagicMock()
    client.positions.side_effect = RuntimeError("boom")
    client.fetch_stock_choose.return_value = [{"stockCode": "920169"}]
    client.fetch_today_stock.return_value = {"stockStatus": "未开盘", "stockVOS": []}

    live = _watch_live(client)
    data = live.watch(force=True)

    assert data["available"] is True
    assert data["positions"] == []
    assert data["watchlist"] == [{"stockCode": "920169"}]


def test_watch_degrades_when_login_fails() -> None:
    client = MagicMock(spec=PlatformClient)
    client.base_url = DEFAULT_BASE_URL
    client.login.side_effect = RuntimeError("auth failed")

    live = _watch_live(client)
    data = live.watch(force=True)

    assert data["available"] is False
    assert "auth failed" in (data.get("error") or "")
    assert data["positions"] == []
    assert data["watchlist"] == []


def test_watch_cache_within_ttl_skips_refetch() -> None:
    client = MagicMock(spec=PlatformClient)
    client.base_url = DEFAULT_BASE_URL
    client.login = MagicMock()
    client.positions.return_value = []
    client.fetch_stock_choose.return_value = []
    client.fetch_today_stock.return_value = {"stockStatus": "未开盘", "stockVOS": []}

    live = _watch_live(client, ttl=60.0)
    live.watch(force=True)
    client.positions.side_effect = AssertionError("should not refetch within ttl")
    data = live.watch()  # 命中缓存

    assert data["available"] is True
    assert client.positions.call_count == 1


# ── 3. API 路由 ──────────────────────────────────────────


def test_watch_route_returns_combined(tmp_path: Path) -> None:
    # 直接构造带 mock live 的插件实例
    app = FastAPI()
    plugin_instance = PaperTradingPlugin()
    ctx = ModuleContext(
        plugin_name="paper_trading",
        plugin_dir=str(PLUGIN_DIR),
        manifest=None,
        fastapi_app=app,
        config={"live_mode": True, "data_dir": str(tmp_path / "pt")},
    )
    plugin_instance.on_load(ctx)

    fake = MagicMock(spec=PlatformClient)
    fake.base_url = DEFAULT_BASE_URL
    fake.positions.return_value = [{"code": "605080"}]
    fake.fetch_stock_choose.return_value = [{"stockCode": "920169"}]
    fake.fetch_today_stock.return_value = {"stockStatus": "未开盘", "stockVOS": []}
    plugin_instance.live = MagicMock(wraps=LiveDataSource(fake, ttl=60.0))
    plugin_instance.live.watch = MagicMock(
        return_value={
            "available": True,
            "status": "未开盘",
            "indices": [],
            "breadth": {},
            "positions": [{"code": "605080"}],
            "watchlist": [{"stockCode": "920169"}],
        }
    )

    tc = TestClient(app)
    r = tc.get("/api/plugins/paper-trading/live/watch")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["positions"] == [{"code": "605080"}]
    assert body["watchlist"] == [{"stockCode": "920169"}]


def test_watch_route_degrades_when_live_disabled(tmp_path: Path) -> None:
    _, client = _plugin(tmp_path, live_mode=False)
    r = client.get("/api/plugins/paper-trading/live/watch")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["enabled"] is False


def test_watch_page_route_serves_html(tmp_path: Path) -> None:
    _, client = _plugin(tmp_path, live_mode=False)
    r = client.get("/api/plugins/paper-trading/watch")
    assert r.status_code == 200
    assert "盯盘" in r.text
    assert "watch.js" in r.text

    script = client.get("/api/plugins/paper-trading/watch.js")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("application/javascript")
    # 统一行情中心:带 Bearer 的 fetch-SSE 单连接 + 快照轮询兜底。
    assert 'QUOTE_URL + "/stream?codes="' in script.text
    assert 'QUOTE_URL + "/snapshot?codes="' in script.text
    assert "response.body.getReader()" in script.text
    assert 'headers.Authorization = "Bearer " + bearerToken' in script.text
    assert 'result.credentials = "omit"' in script.text
    assert "new EventSource" not in script.text
    assert "sessionStorage" not in script.text
    assert "localStorage" not in script.text
    assert 'type: "echo:quote-config-request"' in script.text
    assert 'message.type !== "echo:quote-config"' in script.text
    assert 'origin !== "null" && origin === window.location.origin' in script.text
    assert 'packet.event === "reauth"' in script.text
    assert "status === 401" in script.text
    assert "status === 429" in script.text
    assert '"https://quotes.echo-age.com"' in script.text
    assert "Array.isArray(payload.quotes)" in script.text
    assert 'window.addEventListener("pageshow"' in script.text
    assert 'requestQuoteConfig("pageshow")' in script.text
    assert "token=" not in script.text.lower()
    assert "/live/watch" in script.text
    assert "window.__quoteHubV2 = true" in script.text

