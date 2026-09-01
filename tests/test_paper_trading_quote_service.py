from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from runtime.platform.plugins.bundled.paper_trading.quote_hub import QuoteHub
from runtime.platform.plugins.bundled.paper_trading.quote_service import (
    API_PREFIX,
    QuoteService,
    QuoteServiceSettings,
    _ManagedPushEventClient,
    create_app,
    create_quote_service,
)


class FakeSource:
    def __init__(self) -> None:
        self.callback = None
        self.codes: set[str] = set()

    def subscribe(self, codes, callback) -> None:
        self.codes.update(codes)
        self.callback = callback

    def unsubscribe(self, codes, callback) -> None:
        self.codes.difference_update(codes)
        if callback == self.callback and not self.codes:
            self.callback = None

    def push(self, rows: list[dict[str, Any]]) -> None:
        assert self.callback is not None
        self.callback("kLineRealTime", {"code": 1, "data": rows})


def _settings(**overrides: Any) -> QuoteServiceSettings:
    values: dict[str, Any] = {
        "upstream_url": "https://market-origin.example.com/api",
        "phone": "13800000000",
        "password": "secret-password",
        "state_dir": "/tmp/quote-hub-test",
        "queue_size": 3,
        "max_clients": 2,
        "max_codes_per_client": 2,
        "max_union_codes": 3,
    }
    values.update(overrides)
    return QuoteServiceSettings(**values)


def _fake_service(**settings_overrides: Any) -> tuple[QuoteService, FakeSource]:
    settings = _settings(**settings_overrides)
    source = FakeSource()
    hub = QuoteHub(
        {"platform_ws": source},
        subscriber_queue_size=settings.queue_size,
        max_subscribers=settings.max_clients,
        max_codes_per_subscriber=settings.max_codes_per_client,
        max_union_codes=settings.max_union_codes,
    )
    return QuoteService(settings, hub), source


def _route_endpoint(app, path: str):
    return next(route.endpoint for route in app.routes if getattr(route, "path", "") == path)


def test_settings_load_only_explicit_strict_secret_and_allow_env_override(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "quote-hub.json"
    secret.write_text(
        '{"upstream_url":"https://secret.example/api",'
        '"phone":"13800000000","password":"from-file"}',
        encoding="utf-8",
    )
    secret.chmod(0o600)

    settings = QuoteServiceSettings.from_env(
        {
            "QUOTE_HUB_SECRET_FILE": str(secret),
            "QUOTE_HUB_UPSTREAM_URL": "https://override.example/api",
            "QUOTE_HUB_PLATFORM_PASSWORD": " from-env ",
        }
    )

    assert settings.upstream_url == "https://override.example/api"
    assert settings.phone == "13800000000"
    assert settings.password == " from-env "
    assert "from-env" not in repr(settings)
    assert settings.configured is True


def test_settings_reject_relative_or_permissive_secret_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute path"):
        QuoteServiceSettings.from_env({"QUOTE_HUB_SECRET_FILE": "credentials.json"})

    secret = tmp_path / "quote-hub.json"
    secret.write_text('{"phone":"1","password":"2"}', encoding="utf-8")
    secret.chmod(0o640)
    with pytest.raises(ValueError, match="group or others"):
        QuoteServiceSettings.from_env({"QUOTE_HUB_SECRET_FILE": str(secret)})


def test_standalone_service_builds_lazy_ws_and_rest_sources_without_starting_network() -> None:
    service = create_quote_service(_settings())

    status = service.hub.status()

    assert set(status["sources"]) == {"platform_ws", "platform_rest"}
    assert service.push is not None
    assert service.push.running is False
    assert service.configured is True
    assert service.ready is False
    service.close()


def test_managed_push_registers_before_start_and_rolls_back_failed_start() -> None:
    class PushStub:
        def __init__(self, start_result: bool) -> None:
            self.start_result = start_result
            self.calls: list[tuple[Any, ...]] = []

        def subscribe(self, event, params, callback) -> None:
            self.calls.append(("subscribe", event, params, callback))

        def start(self) -> bool:
            self.calls.append(("start",))
            return self.start_result

        def unsubscribe(self, event, callback) -> None:
            self.calls.append(("unsubscribe", event, callback))

    callback = object()
    working = PushStub(True)
    _ManagedPushEventClient(working).subscribe("kLineRealTime", ["600000.sh"], callback)
    assert [call[0] for call in working.calls] == ["subscribe", "start"]

    unavailable = PushStub(False)
    with pytest.raises(RuntimeError, match="unavailable"):
        _ManagedPushEventClient(unavailable).subscribe("kLineRealTime", ["600000.sh"], callback)
    assert [call[0] for call in unavailable.calls] == ["subscribe", "start", "unsubscribe"]


def test_health_is_live_but_readiness_rejects_missing_upstream_configuration() -> None:
    service = create_quote_service(QuoteServiceSettings())
    with TestClient(create_app(service=service)) as client:
        health = client.get("/health")
        live = client.get("/livez")
        ready = client.get("/readyz")

    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert health.json()["configured"] is False
    assert live.status_code == 200
    assert ready.status_code == 503
    assert "missing trusted HTTPS upstream" in ready.json()["detail"]["configuration_issues"]


def test_readiness_requires_a_real_successful_upstream_probe() -> None:
    successful, _source = _fake_service()
    successful._upstream_probe = lambda: [{"stockCode": "600000", "currentPrice": 10.0}]
    with TestClient(create_app(service=successful)) as client:
        ready = client.get("/readyz")
        health = client.get("/health")
    assert ready.status_code == 200
    assert ready.json()["upstream_probe"]["ok"] is True
    assert health.json()["ready"] is True

    failed, _source = _fake_service()

    def unavailable():
        raise OSError("unreachable")

    failed._upstream_probe = unavailable
    with TestClient(create_app(service=failed)) as client:
        not_ready = client.get("/readyz")
    assert not_ready.status_code == 503
    assert not_ready.json()["detail"]["upstream_probe"]["ok"] is False


def test_first_snapshot_fetches_rest_once_and_reuses_short_cache() -> None:
    service, _source = _fake_service()
    calls: list[list[str]] = []

    def fetch(codes: list[str]) -> list[dict[str, Any]]:
        calls.append(codes)
        return [
            {
                "stockCode": "600000",
                "stockName": "浦发银行",
                "exchangeType": "SH",
                "currentPrice": 10.8,
            }
        ]

    service._snapshot_fetcher = fetch
    with TestClient(create_app(service=service)) as client:
        first = client.get(f"{API_PREFIX}/snapshot", params={"codes": "600000"})
        second = client.get(f"{API_PREFIX}/snapshot", params={"codes": "600000.sh"})

    assert first.status_code == 200
    assert first.json()["quotes"][0]["price"] == 10.8
    assert first.json()["quotes"][0]["source"] == "platform_rest"
    assert first.json()["quotes"][0]["seq"] == 0
    assert second.status_code == 200
    assert calls == [["600000.sh"]]


def test_quote_routes_expose_only_sanitized_status_and_explicit_snapshots() -> None:
    service, source = _fake_service()
    subscription = service.hub.subscribe(["600000.sh"], subscriber_id="tenant-a", replay=False)
    source.push(
        [
            {
                "stockCode": "600000",
                "stockName": "浦发银行",
                "exchangeType": "SH",
                "currentPrice": 10.5,
            }
        ]
    )
    app = create_app(service=service)
    with TestClient(app) as client:
        status = client.get(f"{API_PREFIX}/status")
        snapshot = client.get(f"{API_PREFIX}/snapshot", params={"codes": "600000"})
        empty = client.get(f"{API_PREFIX}/snapshot")
        invalid = client.get(f"{API_PREFIX}/snapshot", params={"codes": "not-a-code"})
        unrelated = client.get("/api/plugins/paper-trading/account")
        docs = client.get("/docs")

    payload = status.json()
    assert status.status_code == 200
    assert "subscribers" not in payload
    assert "subscribed_codes" not in payload
    assert "ref_counts" not in payload
    assert all("last_error" not in item for item in payload["sources"].values())
    assert all("subscribed_codes" not in item for item in payload["sources"].values())
    assert snapshot.status_code == 200
    assert snapshot.json()["quotes"][0]["code"] == "600000.sh"
    assert snapshot.json()["quotes"][0]["price"] == 10.5
    assert empty.json()["quotes"] == []
    assert invalid.status_code == 400
    assert unrelated.status_code == 404
    assert docs.status_code == 404
    subscription.close()


def test_sse_is_bounded_and_releases_subscription_when_client_closes() -> None:
    service, _source = _fake_service(queue_size=2)
    app = create_app(service=service)
    endpoint = _route_endpoint(app, f"{API_PREFIX}/stream")

    class RequestStub:
        async def is_disconnected(self) -> bool:
            return False

    response = endpoint(RequestStub(), codes="600000")
    assert service.hub.status()["subscriber_count"] == 1
    assert service.hub.status()["limits"]["subscriber_queue_size"] == 2

    async def consume_and_close() -> str:
        iterator = response.body_iterator
        first = await iterator.__anext__()
        await iterator.aclose()
        return first

    first = asyncio.run(consume_and_close())

    assert first == "retry: 3000\n\n"
    assert service.hub.status()["subscriber_count"] == 0
    service.close()


def test_sse_forces_bounded_reauthentication_and_then_releases_subscription() -> None:
    service, _source = _fake_service(sse_keepalive=0.01, sse_max_lifetime=0.02)
    app = create_app(service=service)
    endpoint = _route_endpoint(app, f"{API_PREFIX}/stream")

    class RequestStub:
        async def is_disconnected(self) -> bool:
            return False

    response = endpoint(RequestStub(), codes="600000")

    async def consume_all() -> list[str]:
        frames: list[str] = []
        async for frame in response.body_iterator:
            frames.append(frame)
        return frames

    frames = asyncio.run(consume_all())

    assert any("event: reauth" in frame for frame in frames)
    assert service.hub.status()["subscriber_count"] == 0
    service.close()


def test_stream_requires_configuration_codes_and_enforces_capacity() -> None:
    unconfigured = create_quote_service(QuoteServiceSettings())
    unconfigured_endpoint = _route_endpoint(
        create_app(service=unconfigured), f"{API_PREFIX}/stream"
    )

    class RequestStub:
        async def is_disconnected(self) -> bool:
            return False

    with pytest.raises(HTTPException) as unavailable:
        unconfigured_endpoint(RequestStub(), codes="600000")
    assert unavailable.value.status_code == 503
    unconfigured.close()

    service, _source = _fake_service(max_codes_per_client=1, max_union_codes=1)
    endpoint = _route_endpoint(create_app(service=service), f"{API_PREFIX}/stream")
    with pytest.raises(HTTPException) as missing:
        endpoint(RequestStub(), codes="")
    assert missing.value.status_code == 400
    with pytest.raises(HTTPException) as over_limit:
        endpoint(RequestStub(), codes="600000,000001")
    assert over_limit.value.status_code == 400
    service.close()

