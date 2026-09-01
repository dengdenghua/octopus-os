from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.plugins.bundled.paper_trading import PaperTradingPlugin
from runtime.platform.plugins.plugin_base import ModuleContext

PLUGIN_DIR = (
    Path(__file__).resolve().parents[1]
    / "runtime"
    / "platform"
    / "plugins"
    / "bundled"
    / "paper_trading"
)


def _plugin(tmp_path: Path) -> tuple[PaperTradingPlugin, FastAPI, TestClient]:
    app = FastAPI()
    plugin = PaperTradingPlugin()
    plugin.on_load(
        ModuleContext(
            plugin_name="paper_trading",
            plugin_dir=str(PLUGIN_DIR),
            manifest=None,
            fastapi_app=app,
            config={
                "data_dir": str(tmp_path / "pt"),
                "quote_hub_enabled": True,
                "quote_hub_max_codes_per_client": 2,
                "quote_hub_max_clients": 2,
                "quote_hub_max_union_codes": 3,
            },
        )
    )
    return plugin, app, TestClient(app)


def test_quote_status_is_idle_and_does_not_create_push(tmp_path: Path) -> None:
    plugin, _app, client = _plugin(tmp_path)

    response = client.get("/api/plugins/paper-trading/quotes/status")

    assert response.status_code == 200
    assert response.json()["state"] == "idle"
    assert response.json()["limits"]["max_codes_per_subscriber"] == 2
    assert "subscribers" not in response.json()
    assert "subscribed_codes" not in response.json()
    assert "ref_counts" not in response.json()
    assert all("last_error" not in source for source in response.json()["sources"].values())
    assert plugin.push is None


def test_snapshot_returns_normalized_quote_and_rejects_too_many_codes(
    tmp_path: Path,
) -> None:
    plugin, _app, client = _plugin(tmp_path)
    assert plugin.quote_hub is not None
    plugin.quote_hub.ingest(
        "kLineRealTime",
        {
            "data": [
                {
                    "stockCode": "600000",
                    "stockName": "浦发银行",
                    "exchangeType": "SH",
                    "currentPrice": 10.5,
                }
            ]
        },
        source="platform_ws",
    )

    response = client.get("/api/plugins/paper-trading/quotes/snapshot", params={"codes": "600000"})

    assert response.status_code == 200
    assert response.json()["quotes"][0]["code"] == "600000.sh"
    assert response.json()["quotes"][0]["price"] == 10.5
    assert response.json()["quotes"][0]["source"] == "platform_ws"
    too_many = client.get(
        "/api/plugins/paper-trading/quotes/snapshot",
        params={"codes": "600000,000001,300001"},
    )
    assert too_many.status_code == 400


def test_legacy_quote_subscription_cannot_overwrite_modern_union(tmp_path: Path) -> None:
    plugin, _app, client = _plugin(tmp_path)
    assert plugin.quote_hub is not None
    modern = plugin.quote_hub.subscribe(["600000.sh"], subscriber_id="modern", replay=False)

    response = client.get(
        "/api/plugins/paper-trading/live/push/subscribe",
        params={"event": "kLineRealTime", "codes": "000001"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert plugin.quote_hub.status()["ref_counts"] == {
        "000001.sz": 1,
        "600000.sh": 1,
    }
    modern.close()
    plugin.on_unload(plugin.ctx)


def test_stream_close_releases_subscription(tmp_path: Path) -> None:
    plugin, app, _client = _plugin(tmp_path)
    assert plugin.quote_hub is not None
    routes = []
    for route in app.routes:
        original = getattr(route, "original_router", None)
        routes.extend(original.routes if original is not None else [route])
    endpoint = next(
        route.endpoint
        for route in routes
        if getattr(route, "path", "") == "/api/plugins/paper-trading/quotes/stream"
    )

    class _Request:
        async def is_disconnected(self) -> bool:
            return False

    response = endpoint(_Request(), codes="600000")
    assert plugin.quote_hub.status()["subscriber_count"] == 1

    async def consume_and_close() -> str:
        iterator = response.body_iterator
        first = await iterator.__anext__()
        await iterator.aclose()
        return first

    first = asyncio.run(consume_and_close())

    assert first == "retry: 3000\n\n"
    assert plugin.quote_hub.status()["subscriber_count"] == 0
    plugin.on_unload(plugin.ctx)

