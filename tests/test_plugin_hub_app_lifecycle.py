from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.ui._app_routers_extra import _register_plugin_hub_lifecycle


class _LifecycleHub:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def start_all(self) -> list[str]:
        self.calls.append("start")
        return ["paper_trading"]

    def stop_all(self) -> list[str]:
        self.calls.append("stop")
        return ["paper_trading"]


def test_plugin_hub_background_lifecycle_follows_fastapi_app() -> None:
    app = FastAPI()
    hub = _LifecycleHub()
    _register_plugin_hub_lifecycle(app, hub)

    assert hub.calls == []
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 200
        assert hub.calls == ["start"]
    assert hub.calls == ["start", "stop"]

