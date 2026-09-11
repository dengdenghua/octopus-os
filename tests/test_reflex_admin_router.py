from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.ui.reflex_admin_router import mount_reflex_admin_routes


class _Journal:
    def read_by_type(self, _event_type: str) -> list[object]:
        return []


class _Stack:
    journal = _Journal()


class _ReflexRouter:
    try_count = 3
    hit_count = 2
    hit_rate = 2 / 3

    def stats_by_rule(self) -> dict[str, int]:
        return {"ping": 2}

    def coverage_summary(self, *, stale_hours: float) -> dict[str, float]:
        return {"stale_hours": stale_hours}

    def list_rules(self) -> list[dict[str, str]]:
        return [{"rule_id": "ping"}]


def test_reflex_admin_router_mounts_core_endpoints() -> None:
    app = FastAPI()
    mount_reflex_admin_routes(
        app,
        stack=_Stack(),
        reflex_router=_ReflexRouter(),
        panel_html="<html>panel</html>",
        editor_html="<html>editor</html>",
    )
    client = TestClient(app)

    stats = client.get("/api/reflex/stats")
    rules = client.get("/api/reflex/rules")
    timeseries = client.get("/api/reflex/timeseries")
    panel = client.get("/admin/reflex")
    editor = client.get("/admin/reflex/edit")

    assert stats.status_code == 200
    assert stats.json()["try_count"] == 3
    assert rules.status_code == 200
    assert rules.json()["rules"] == [{"rule_id": "ping"}]
    assert timeseries.status_code == 200
    assert timeseries.json()["total_events"] == 0
    assert panel.status_code == 200
    assert "panel" in panel.text
    assert editor.status_code == 200
    assert "editor" in editor.text


def test_reflex_admin_router_skips_when_disabled() -> None:
    app = FastAPI()
    mount_reflex_admin_routes(
        app,
        stack=_Stack(),
        reflex_router=None,
        panel_html="",
        editor_html="",
    )

    assert TestClient(app).get("/api/reflex/stats").status_code == 404
