from __future__ import annotations

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from appliance.web_security import ApplianceWebSecurityMiddleware


def _app(
    *,
    trusted_hosts: list[str] | None = None,
    trusted_origins: list[str] | None = None,
    frame_origins: list[str] | None = None,
    connect_origins: list[str] | None = None,
    storage_url: str | None = None,
) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        ApplianceWebSecurityMiddleware,
        trusted_hosts=trusted_hosts,
        trusted_origins=trusted_origins,
        frame_origins=frame_origins,
        connect_origins=connect_origins,
        storage_url=storage_url,
    )

    @app.get("/api/state")
    def read_state():
        return {"ok": True}

    @app.post("/api/state")
    def change_state():
        return {"ok": True}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("ready")
        await websocket.close()

    return app


def test_same_origin_browser_and_nonbrowser_client_are_allowed() -> None:
    with TestClient(_app()) as client:
        browser = client.post(
            "/api/state",
            headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
        )
        cli = client.post("/api/state")

    assert browser.status_code == 200
    assert cli.status_code == 200


def test_every_http_response_gets_a_strict_browser_policy() -> None:
    with TestClient(_app()) as client:
        response = client.get("/api/state")

    policy = response.headers["content-security-policy"]
    assert "default-src 'self'" in policy
    assert "script-src 'self' 'wasm-unsafe-eval'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'self'" in policy
    assert "frame-src 'self'" in policy
    assert "frame-src 'self' http:" not in policy
    assert "frame-src 'self' https:" not in policy
    assert "connect-src 'self' ws://testserver" in policy
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_explicit_frame_and_connect_origins_are_exactly_allowlisted() -> None:
    with TestClient(
        _app(
            frame_origins=["https://media.home.example:8443"],
            connect_origins=[
                "https://storage.home.example",
                "wss://events.home.example:9443",
            ],
        )
    ) as client:
        response = client.get("/api/state")

    policy = response.headers["content-security-policy"]
    assert "frame-src 'self' https://media.home.example:8443" in policy
    assert "https://storage.home.example" in policy
    assert "wss://events.home.example:9443" in policy


def test_configured_storage_service_url_extends_only_connect_directive() -> None:
    with TestClient(
        _app(
            storage_url="https://storage.home.example:9443/api",
        )
    ) as client:
        response = client.get("/api/state")

    policy = response.headers["content-security-policy"]
    frame_directive = next(item for item in policy.split("; ") if item.startswith("frame-src "))
    connect_directive = next(item for item in policy.split("; ") if item.startswith("connect-src "))
    assert frame_directive == "frame-src 'self'"
    assert "https://storage.home.example:9443" in connect_directive
    assert "storage.home.example" not in frame_directive


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "file://local"])
def test_cross_or_invalid_origin_is_rejected(origin: str) -> None:
    with TestClient(_app()) as client:
        response = client.post("/api/state", headers={"Origin": origin})

    assert response.status_code == 403
    assert response.json() == {"detail": "cross-origin request rejected"}
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]


@pytest.mark.parametrize("fetch_site", ["cross-site", "same-site"])
def test_browser_state_change_without_origin_fails_closed(fetch_site: str) -> None:
    with TestClient(_app()) as client:
        response = client.post(
            "/api/state",
            headers={"Sec-Fetch-Site": fetch_site},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "browser Origin header required"}


def test_cross_origin_preflight_for_state_change_is_rejected() -> None:
    with TestClient(_app()) as client:
        response = client.options(
            "/api/state",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert response.status_code == 403


def test_untrusted_fqdn_host_is_rejected_but_private_ip_is_allowed() -> None:
    with TestClient(_app()) as client:
        rebound = client.get("/api/state", headers={"Host": "rebind.example:8000"})
    with TestClient(_app(), base_url="http://192.168.50.20:8000") as private_client:
        private = private_client.post(
            "/api/state",
            headers={"Origin": "http://192.168.50.20:8000"},
        )

    assert rebound.status_code == 400
    assert rebound.json() == {"detail": "untrusted Host header"}
    assert private.status_code == 200


def test_explicit_reverse_proxy_host_and_https_origin_are_allowed() -> None:
    app = _app(
        trusted_hosts=["echo.home.example"],
        trusted_origins=["https://echo.home.example"],
    )
    with TestClient(app, base_url="http://echo.home.example") as client:
        response = client.post(
            "/api/state",
            headers={"Origin": "https://echo.home.example"},
        )

    assert response.status_code == 200


def test_configured_remote_access_origin_is_automatically_trusted(monkeypatch) -> None:
    monkeypatch.setenv(
        "ECHO_REMOTE_ACCESS_URL",
        "https://echo-os.example.ts.net",
    )
    with TestClient(_app(), base_url="http://echo-os.example.ts.net") as client:
        response = client.post(
            "/api/state",
            headers={"Origin": "https://echo-os.example.ts.net"},
        )

    assert response.status_code == 200


def test_invalid_remote_access_origin_stops_browser_boundary(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_REMOTE_ACCESS_URL", "https://user:pw@example.test")

    with (
        pytest.raises(RuntimeError, match="invalid Echo appliance browser trust"),
        TestClient(_app()),
    ):
        pass


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "file:///tmp/echo",
        "https://user:password@echo.example",
        "https://echo.example/unsafe/path",
    ],
)
def test_invalid_trusted_origin_configuration_stops_startup(origin: str) -> None:
    with pytest.raises(RuntimeError, match="invalid Echo appliance browser trust"):
        ApplianceWebSecurityMiddleware(lambda *_args: None, trusted_origins=[origin])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("frame_origins", "https://frames.example/path"),
        ("frame_origins", "data:text/html,unsafe"),
        ("connect_origins", "https://user:password@api.example"),
        ("connect_origins", "wss://events.example/socket"),
    ],
)
def test_invalid_csp_source_configuration_stops_startup(field: str, value: str) -> None:
    with pytest.raises(RuntimeError, match="invalid Echo appliance browser trust"):
        ApplianceWebSecurityMiddleware(lambda *_args: None, **{field: [value]})


def test_websocket_origin_is_enforced_before_accept() -> None:
    with TestClient(_app()) as client:
        with client.websocket_connect(
            "/ws",
            headers={"Origin": "http://testserver"},
        ) as websocket:
            assert websocket.receive_text() == "ready"

        with (
            pytest.raises(WebSocketDisconnect) as rejected,
            client.websocket_connect(
                "/ws",
                headers={"Origin": "https://evil.example"},
            ),
        ):
            pass

    assert rejected.value.code == 1008
