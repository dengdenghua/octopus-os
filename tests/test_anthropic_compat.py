"""Smoke tests for the Anthropic Managed Agents compatibility layer."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from runtime.sensing.gateway.anthropic_compat import create_anthropic_compat_router  # noqa: E402

_BETA = {"anthropic-beta": "managed-agents-2026-04-01"}


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    # ``stack`` may be None for the auth/session-management surface;
    # turn execution is exercised through smoke tests that wire a real
    # stack in fixtures (out of scope for the unit suite).
    app.include_router(create_anthropic_compat_router(stack=None))
    return TestClient(app)


def test_missing_beta_header_rejected(client: TestClient) -> None:
    r = client.post("/v1/sessions", json={"title": "x"})
    assert r.status_code == 400
    assert "managed-agents-2026-04-01" in r.json()["detail"]


def test_create_session_returns_id_and_idle(client: TestClient) -> None:
    r = client.post("/v1/sessions", json={"title": "hello"}, headers=_BETA)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["id"].startswith("sesn_")
    assert data["status"] == "idle"
    assert data["title"] == "hello"
    assert data["usage"]["input_tokens"] == 0


def test_get_session_404_when_unknown(client: TestClient) -> None:
    r = client.get("/v1/sessions/sesn_nope", headers=_BETA)
    assert r.status_code == 404


def test_list_events_empty_for_new_session(client: TestClient) -> None:
    r = client.post("/v1/sessions", json={}, headers=_BETA)
    sid = r.json()["id"]
    events = client.get(f"/v1/sessions/{sid}/events", headers=_BETA)
    assert events.status_code == 200
    assert events.json() == []


def test_event_adapter_maps_text_delta() -> None:
    from runtime.sensing.gateway.anthropic_compat.event_adapter import adapt_react_event

    out = adapt_react_event({"kind": "text_delta", "delta": "hello"})
    assert len(out) == 1
    assert out[0].type == "agent.message"
    assert out[0].content == [{"type": "text", "text": "hello"}]


def test_event_adapter_maps_tool_start_and_end() -> None:
    from runtime.sensing.gateway.anthropic_compat.event_adapter import adapt_react_event

    start = adapt_react_event(
        {
            "kind": "tool_start",
            "tool_name": "exec_shell",
            "tool_call_id": "tc_1",
            "input_preview": {"command": "ls"},
        }
    )
    assert len(start) == 1
    assert start[0].type == "agent.tool_use"
    assert start[0].tool_name == "exec_shell"
    assert start[0].tool_use_id == "tc_1"

    end = adapt_react_event(
        {
            "kind": "tool_end",
            "tool_call_id": "tc_1",
            "status": "success",
            "output_preview": "file1.txt\n",
        }
    )
    assert len(end) == 1
    assert end[0].type == "agent.tool_result"
    assert end[0].tool_use_id == "tc_1"


def test_event_adapter_approval_emits_requires_action() -> None:
    from runtime.sensing.gateway.anthropic_compat.event_adapter import adapt_react_event

    out = adapt_react_event(
        {
            "kind": "tool_approval_request",
            "tool_name": "delete_file",
            "tool_call_id": "tc_2",
            "args_preview": "path=/etc/passwd",
        }
    )
    # custom_tool_use + session.status_idle with requires_action
    assert len(out) == 2
    assert out[0].type == "agent.custom_tool_use"
    assert out[1].type == "session.status_idle"
    assert out[1].stop_reason.type == "requires_action"
    assert out[0].id in out[1].stop_reason.event_ids
