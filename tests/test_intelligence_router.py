from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.sensing.gateway.intelligence_router import (
    _subscription_due,
    create_intelligence_router,
)


def test_intelligence_subscriptions_persist(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(create_intelligence_router(tmp_path / "intelligence.json"))
    client = TestClient(app)

    created = client.post(
        "/api/intelligence/subscriptions",
        json={
            "topic": "AI 行业简报",
            "display_name": "AI 行业简报",
            "keywords": ["AI", "Agent"],
        },
    )

    assert created.status_code == 200
    subscription = created.json()
    assert subscription["topic"] == "AI 行业简报"
    assert subscription["enabled"] is True

    listed = client.get("/api/intelligence/subscriptions")

    assert listed.status_code == 200
    assert listed.json()["subscriptions"][0]["id"] == subscription["id"]

    updated = client.patch(
        f"/api/intelligence/subscriptions/{subscription['id']}",
        json={"enabled": False},
    )

    assert updated.status_code == 200
    assert updated.json()["enabled"] is False

    deleted = client.delete(f"/api/intelligence/subscriptions/{subscription['id']}")

    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True
    assert client.get("/api/intelligence/subscriptions").json()["subscriptions"] == []


def test_intelligence_subscription_draft_from_goal(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(create_intelligence_router(tmp_path / "intelligence.json"))
    client = TestClient(app)

    response = client.post(
        "/api/intelligence/subscriptions/draft",
        json={"goal": "每天关注 Echo Agent 的 GitHub release 和 issue 变化"},
    )

    assert response.status_code == 200
    draft = response.json()["draft"]
    assert draft["topic"]
    assert draft["cadence"] == "每天"
    assert draft["schedule_time"] == "09:00"
    assert draft["schedule_day"] == "1"
    assert draft["timezone"] == "Asia/Shanghai"
    assert "Echo" in draft["keywords"]


def test_intelligence_subscription_schedule_fields_persist(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(create_intelligence_router(tmp_path / "intelligence.json"))
    client = TestClient(app)

    created = client.post(
        "/api/intelligence/subscriptions",
        json={
            "topic": "weekly agent brief",
            "cadence": "每周",
            "schedule_time": "10:30",
            "schedule_day": "3",
            "timezone": "Asia/Shanghai",
        },
    )

    assert created.status_code == 200
    subscription = created.json()
    assert subscription["cadence"] == "每周"
    assert subscription["schedule_time"] == "10:30"
    assert subscription["schedule_day"] == "3"
    assert subscription["timezone"] == "Asia/Shanghai"


def test_subscription_due_respects_schedule_time_and_day() -> None:
    subscription = {
        "enabled": True,
        "cadence": "每周",
        "schedule_time": "10:30",
        "schedule_day": "3",
        "timezone": "Asia/Shanghai",
        "last_run": None,
    }

    before_time = datetime(2026, 6, 3, 2, 20, tzinfo=UTC)  # Wed 10:20 Asia/Shanghai
    at_time = datetime(2026, 6, 3, 2, 30, tzinfo=UTC)  # Wed 10:30 Asia/Shanghai
    wrong_day = datetime(2026, 6, 4, 2, 30, tzinfo=UTC)  # Thu 10:30 Asia/Shanghai

    assert _subscription_due(subscription, now=before_time) is False
    assert _subscription_due(subscription, now=at_time) is True
    assert _subscription_due(subscription, now=wrong_day) is False


def test_subscription_due_uses_fixed_shanghai_fallback_without_tzdata(monkeypatch) -> None:
    from zoneinfo import ZoneInfoNotFoundError

    import runtime.sensing.gateway.intelligence_router as router

    def _missing_zoneinfo(_name: str):
        raise ZoneInfoNotFoundError("tzdata unavailable")

    monkeypatch.setattr(router, "ZoneInfo", _missing_zoneinfo)
    subscription = {
        "enabled": True,
        "cadence": "weekly",
        "schedule_time": "10:30",
        "schedule_day": "3",
        "timezone": "Asia/Shanghai",
        "last_run": None,
    }

    assert (
        router._subscription_due(
            subscription,
            now=datetime(2026, 6, 3, 2, 30, tzinfo=UTC),
        )
        is True
    )


def test_intelligence_subscription_run_creates_report(tmp_path: Path) -> None:
    def fake_search(query: str, *, max_results: int = 5, **_kwargs):
        return {
            "query": query,
            "backend": "fake",
            "results": [
                {
                    "title": f"{query} release",
                    "url": "https://example.com/echo-release",
                    "snippet": "Echo agent release adds browser automation.",
                },
                {
                    "title": f"{query} paper",
                    "url": "https://arxiv.org/abs/2604.00001",
                    "snippet": "A paper about multi-agent research workflows.",
                },
            ][:max_results],
        }

    app = FastAPI()
    app.include_router(
        create_intelligence_router(
            tmp_path / "intelligence.json",
            search_fn=fake_search,
            remember_reports=False,
        )
    )
    client = TestClient(app)

    created = client.post(
        "/api/intelligence/subscriptions",
        json={
            "topic": "AI Agent",
            "display_name": "AI Agent",
            "keywords": ["AI", "Agent"],
            "sources": ["github", "arxiv"],
        },
    ).json()

    response = client.post(
        f"/api/intelligence/subscriptions/{created['id']}/run",
        json={"max_results_per_query": 2},
    )

    assert response.status_code == 200
    payload = response.json()
    report = payload["report"]
    assert payload["ok"] is True
    assert report["items_analyzed"] >= 1
    assert "## 证据与来源" in report["markdown"]
    assert report["memory_written"] is False
    assert payload["subscription"]["last_run"] == report["created_at"]

    reports = client.get("/api/intelligence/reports").json()["reports"]
    assert reports[0]["id"] == report["id"]
    assert client.get(f"/api/intelligence/reports/{report['id']}").json()["id"] == report["id"]


def test_intelligence_run_all_skips_disabled(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_search(query: str, *, max_results: int = 5, **_kwargs):
        calls.append(query)
        return {
            "query": query,
            "backend": "fake",
            "results": [
                {
                    "title": "OpenAI release notes",
                    "url": f"https://example.com/{len(calls)}",
                    "snippet": "New model update.",
                },
            ],
        }

    app = FastAPI()
    app.include_router(
        create_intelligence_router(
            tmp_path / "intelligence.json",
            search_fn=fake_search,
            remember_reports=False,
        )
    )
    client = TestClient(app)

    enabled = client.post(
        "/api/intelligence/subscriptions",
        json={"topic": "enabled", "keywords": ["OpenAI"]},
    ).json()
    disabled = client.post(
        "/api/intelligence/subscriptions",
        json={"topic": "disabled", "keywords": ["Anthropic"], "enabled": False},
    ).json()

    result = client.post("/api/intelligence/run").json()

    assert result["reports_count"] == 1
    assert result["due_only"] is False
    subs = client.get("/api/intelligence/subscriptions").json()["subscriptions"]
    by_id = {item["id"]: item for item in subs}
    assert by_id[enabled["id"]]["last_run"] is not None
    assert by_id[disabled["id"]]["last_run"] is None
    assert calls


def test_intelligence_due_only_skips_recent_subscription(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_search(query: str, *, max_results: int = 5, **_kwargs):
        calls.append(query)
        return {
            "query": query,
            "backend": "fake",
            "results": [
                {
                    "title": "Model release",
                    "url": f"https://example.com/{len(calls)}",
                    "snippet": "A useful model update.",
                },
            ],
        }

    app = FastAPI()
    app.include_router(
        create_intelligence_router(
            tmp_path / "intelligence.json",
            search_fn=fake_search,
            remember_reports=False,
        )
    )
    client = TestClient(app)

    client.post(
        "/api/intelligence/subscriptions",
        json={"topic": "model updates", "keywords": ["model"]},
    )

    first = client.post("/api/intelligence/run", json={"due_only": True}).json()
    assert first["reports_count"] == 1

    calls.clear()
    second = client.post("/api/intelligence/run", json={"due_only": True}).json()

    assert second["reports_count"] == 0
    assert second["due_only"] is True
    assert calls == []
