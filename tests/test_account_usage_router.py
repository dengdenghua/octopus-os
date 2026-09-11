from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import CostEntry
from runtime.sensing.gateway.account_usage_router import (
    create_account_usage_router,
)


def _client_with_usage_events() -> TestClient:
    journal = InMemoryJournal()
    chat_task_id = str(uuid4())
    tool_task_id = str(uuid4())
    journal.write_token_usage(
        chat_task_id,
        iteration=1,
        input_tokens=100,
        output_tokens=40,
        cost_usd=0.0123,
        model="kimi-k2.5",
    )
    journal.write_budget(
        "budget_commit",
        tool_task_id,
        cost=CostEntry(tokens_in=25, tokens_out=15, usd=0.004),
        reason="tool_run",
        actor="arms/general",
    )

    app = FastAPI()
    app.include_router(create_account_usage_router(journal=journal))
    return TestClient(app)


def test_account_usage_is_backed_by_journal_without_stub_tag() -> None:
    client = _client_with_usage_events()

    response = client.get("/api/account/usage")

    assert response.status_code == 200
    body = response.json()
    assert "_stub" not in body
    assert body["success"] is True
    usage = body["data"]
    assert usage["user_id"] == "local"
    assert usage["requests_used"] == 2
    assert usage["tokens_used"] == 180
    assert usage["cost_incurred"] == "0.0163"


def test_account_usage_summary_groups_real_events() -> None:
    client = _client_with_usage_events()

    response = client.get("/api/account/usage/summary")

    assert response.status_code == 200
    body = response.json()
    assert "_stub" not in body
    summary = body["data"]
    assert summary["total_requests"] == 2
    assert summary["total_tokens"] == 180
    assert summary["total_cost"] == "0.0163"
    assert summary["by_event_type"] == {
        "completion": 1,
        "agent_run": 1,
    }
    assert summary["by_model"]["kimi-k2.5"] == {
        "count": 1,
        "cost": "0.0123",
    }
    assert summary["most_used_model"] == "kimi-k2.5"


def test_account_usage_events_returns_real_rows_and_pagination() -> None:
    client = _client_with_usage_events()

    response = client.get("/api/account/usage/events?limit=1")

    assert response.status_code == 200
    body = response.json()
    assert "_stub" not in body
    payload = body["data"]
    assert len(payload["data"]) == 1
    assert payload["pagination"] == {
        "total": 2,
        "page": 1,
        "page_size": 1,
        "total_pages": 2,
    }
    event = payload["data"][0]
    assert event["event_type"] in {"completion", "agent_run"}
    assert event["user_id"] == "local"
    assert int(event["tokens_input"]) >= 0
    assert int(event["tokens_output"]) >= 0
