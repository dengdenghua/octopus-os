from __future__ import annotations

from fastapi.testclient import TestClient

from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import CostEntry
from runtime.platform.ui import create_app


def test_create_app_exposes_real_account_usage_routes() -> None:
    journal = InMemoryJournal()
    journal.write_budget(
        "budget_commit",
        "4f9b7ab8-7b1f-4c1b-9f06-5d8d7f1e4e32",
        cost=CostEntry(tokens_in=10, tokens_out=5, usd=0.0015),
        reason="ui_probe",
        actor="arms/ui",
    )

    app = create_app(journal=journal)
    client = TestClient(app)

    usage = client.get("/api/account/usage")
    summary = client.get("/api/account/usage/summary")
    events = client.get("/api/account/usage/events")

    assert usage.status_code == 200
    assert summary.status_code == 200
    assert events.status_code == 200

    usage_body = usage.json()
    summary_body = summary.json()
    events_body = events.json()

    assert "_stub" not in usage_body
    assert "_stub" not in summary_body
    assert "_stub" not in events_body
    assert usage_body["data"]["requests_used"] == 1
    assert summary_body["data"]["total_cost"] == "0.0015"
    assert events_body["data"]["pagination"]["total"] == 1
