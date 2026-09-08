"""Actual WebSocket recovery rejection with persistent checkpoints and pauses."""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.core.cerebrum import pause_control
from runtime.memory.diagnostics.trace_store import AgentTraceStore
from runtime.memory.journal import JSONLJournal
from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime
from runtime.sensing.gateway.realtime_gateway import RealtimeGateway
from tests.realtime_cerebrum._helpers import _LAST_STREAM_KWARGS, drive, set_script


@pytest.mark.parametrize("kind", ["missing", "bad_shape", "future_version", "unknown_task"])
def test_continue_rejects_in_ui_without_dispatch_or_destroying_pause(tmp_path, monkeypatch, kind):
    task_id, thread = str(uuid4()), "thread-recovery-rejection"
    trace = AgentTraceStore(tmp_path / "trace.sqlite")
    pause = pause_control.PauseController(store_path=tmp_path / "pauses.json")
    monkeypatch.setattr(pause_control, "get_pause_controller", lambda: pause)
    pause.request_pause(task_id, reason="user_request", thread_id=thread)
    pause.mark_paused(task_id)
    pause.set_grant(task_id, extra_iterations=5)
    pause.set_pending_resume(thread, task_id)
    if kind in {"bad_shape", "future_version"}:
        trace.record_checkpoint(
            task_id=task_id,
            thread_id=thread,
            checkpoint_type="react",
            iteration=2,
            state={
                "schema_version": 999 if kind == "future_version" else 1,
                "messages_snapshot": "bad" if kind == "bad_shape" else [],
                "steps_snapshot": [],
            },
        )
    if kind == "unknown_task":
        # A banner's selected task disappeared. It cannot become a new task.
        pause.clear(task_id)
    runtime = CerebrumRuntime(
        stack=SimpleNamespace(journal=JSONLJournal(tmp_path / "journal.jsonl")),
        agent=None,
        logs_root=str(tmp_path / "threads"),
        trace_store=trace,
    )
    app = FastAPI()
    app.include_router(RealtimeGateway(runtime=runtime, approval_timeout=5).router)
    before = (tmp_path / "pauses.json").read_bytes()
    set_script(
        [{"type": "text_delta", "delta": "UNEXPECTED MODEL EXECUTION"}, {"type": "react_completed"}]
    )
    try:
        with TestClient(app) as client, client.websocket_connect("/api/realtime") as ws:
            out = drive(ws, {"threadId": thread, "input": [{"type": "text", "text": "继续"}]})
        assert _LAST_STREAM_KWARGS == {}
        terminal = next(
            n.params["turn"] for n in out["notifications"] if n.method == "turn/completed"
        )
        assert terminal["status"] == "failed"
        assert terminal["error"]["restarted"] is False
        assert terminal["error"]["resume_task_id"] == task_id
        assert terminal["error"]["code"].startswith("resume_checkpoint_")
        error_items = [item for item in terminal["items"] if item["type"] == "error"]
        assert error_items and error_items[0]["message"]
        assert "UNEXPECTED MODEL EXECUTION" not in json.dumps(terminal)
        assert (tmp_path / "pauses.json").read_bytes() == before
        assert pause.consume_pending_resume(thread) == task_id
        if kind != "unknown_task":
            assert pause.consume_grant(task_id)["extra_iterations"] == 5
            reopened = pause_control.PauseController(store_path=tmp_path / "pauses.json")
            assert reopened.get_request(task_id) is not None
    finally:
        trace.close()
