from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.memory.diagnostics.trace_store import AgentTraceStore
from runtime.sensing.gateway.agent_trace_router import create_agent_trace_router


def _client_with_trace(tmp_path: Path) -> TestClient:
    store = AgentTraceStore(tmp_path / "agent_trace.sqlite")
    store.record_event(
        thread_id="thread-1",
        task_id="task-1",
        agent_id="agent-a",
        event_type="RUN_STARTED",
        payload={"phase": "start"},
    )
    store.record_event(
        thread_id="thread-2",
        task_id="task-2",
        agent_id="agent-b",
        event_type="RUN_FINISHED",
        payload={"phase": "end"},
    )
    store.record_token_usage(
        task_id="task-2",
        thread_id="thread-2",
        agent_id="agent-b",
        model="gpt-other",
        input_tokens=90,
        output_tokens=10,
    )
    store.record_approval(
        thread_id="thread-1",
        tool_name="exec_shell",
        tool_call_id="call-1",
        decision="approved",
    )
    store.record_checkpoint(
        task_id="task-1",
        checkpoint_type="react",
        state={
            "current_phase": "implementation",
            "messages_snapshot": [{"content": "secret message body"}],
            "steps_snapshot": [{"iteration": 1}],
            "working_set_snapshot": [{"path": "runtime/memory/trace_store.py"}],
        },
        iteration=2,
        summary="resume here",
    )
    store.record_resume_request(
        thread_id="thread-1",
        task_id="task-1",
        checkpoint_id=1,
        status="pending",
        intent={
            "checkpoint_id": 1,
            "requires_confirmation": True,
            "messages_snapshot": ["secret message body"],
        },
    )
    store.record_token_usage(
        task_id="task-1",
        thread_id="thread-1",
        agent_id="agent-a",
        model="gpt-test",
        input_tokens=10,
        output_tokens=5,
    )
    store.record_task_run_started(
        task_id="turn-1",
        thread_id="thread-1",
        turn_id="turn-1",
        agent_id="agent-a",
        title="Build report",
        mode="code",
    )
    store.record_event(
        thread_id="thread-1",
        turn_id="turn-1",
        task_id="turn-1",
        agent_id="agent-a",
        event_type="TOOL_CALL_START",
        item_id="call-read-1",
        payload={"tool": "read_file", "tool_call_id": "call-read-1"},
    )
    store.record_approval(
        thread_id="thread-1",
        turn_id="turn-1",
        task_id="turn-1",
        agent_id="agent-a",
        tool_name="read_file",
        tool_call_id="call-read-1",
        decision="approved",
        reason="safe read",
        metadata={
            "trust_gateway": {
                "schema": "echo.trust_decision.v1",
                "source": "risk_policy",
                "action": "allow",
                "risk": {
                    "level": "low",
                    "categories": ["local_read"],
                    "reason": "local_read",
                    "requires_approval": False,
                },
                "risk_policy": {
                    "low": "allow",
                    "medium": "ask",
                    "high": "ask",
                    "critical": "confirm",
                },
            }
        },
    )
    store.record_event(
        thread_id="thread-1",
        turn_id="turn-1",
        task_id="turn-1",
        agent_id="agent-a",
        event_type="TOOL_CALL_END",
        item_id="call-read-1",
        payload={"tool": "read_file", "tool_call_id": "call-read-1", "status": "success"},
    )
    store.record_task_run_finished(
        task_id="turn-1",
        thread_id="thread-1",
        turn_id="turn-1",
        agent_id="agent-a",
        status="completed",
        summary="done",
    )
    app = FastAPI()
    app.include_router(
        create_agent_trace_router(
            store=store,
            experience_ledger_path=tmp_path / "experience_ledger.json",
            review_queue_path=tmp_path / "review_queue.json",
            promotion_audit_path=tmp_path / "promotion_audit.json",
            proposal_ledger_path=tmp_path / "proposal_ledger.jsonl",
        )
    )
    return TestClient(app)


def test_trace_stats_exposes_ledger_totals(tmp_path: Path) -> None:
    client = _client_with_trace(tmp_path)

    response = client.get("/api/agent-trace/stats")

    assert response.status_code == 200
    data = response.json()
    assert data["events"] == 6
    assert data["approvals"] == 2
    assert data["checkpoints"] == 1
    assert data["token_totals"]["input_tokens"] == 100


def test_trace_stats_supports_thread_scope(tmp_path: Path) -> None:
    client = _client_with_trace(tmp_path)

    response = client.get("/api/agent-trace/stats", params={"thread_id": "thread-1"})

    assert response.status_code == 200
    data = response.json()
    assert data["events"] == 5
    assert data["approvals"] == 2
    assert data["checkpoints"] == 0
    assert data["token_totals"]["input_tokens"] == 10


def test_trace_events_support_runtime_filters(tmp_path: Path) -> None:
    client = _client_with_trace(tmp_path)

    response = client.get(
        "/api/agent-trace/events",
        params={"thread_id": "thread-1", "event_type": "RUN_STARTED"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["limit"] == 100
    assert len(data["events"]) == 1
    assert data["events"][0]["task_id"] == "task-1"
    assert data["events"][0]["payload"] == {"phase": "start"}


def test_trace_task_runs_are_readable_as_run_summaries(tmp_path: Path) -> None:
    client = _client_with_trace(tmp_path)

    listing = client.get(
        "/api/agent-trace/task-runs",
        params={"thread_id": "thread-1", "status": "completed"},
    )
    detail = client.get("/api/agent-trace/task-runs/turn-1")
    missing = client.get("/api/agent-trace/task-runs/missing")

    assert listing.status_code == 200
    runs = listing.json()["task_runs"]
    assert [run["task_id"] for run in runs] == ["turn-1"]
    assert runs[0]["status"] == "completed"
    assert runs[0]["title"] == "Build report"
    assert runs[0]["tool_calls_started"] == 1
    assert detail.status_code == 200
    assert detail.json()["task_run"]["summary"] == "done"
    assert len(detail.json()["task_run"]["events"]) == 4
    assert missing.status_code == 404


def test_trace_task_run_review_endpoint_exposes_replay_and_candidates(
    tmp_path: Path,
) -> None:
    client = _client_with_trace(tmp_path)

    response = client.get("/api/agent-trace/task-runs/turn-1/review")
    missing = client.get("/api/agent-trace/task-runs/missing/review")

    assert response.status_code == 200
    review = response.json()["review"]
    assert review["schema"] == "echo.task_run_review.v1"
    assert review["task_id"] == "turn-1"
    assert review["status"] == "completed"
    assert review["replay"]["replayable"] is True
    assert review["summary"]["tool_calls_started"] == 1
    assert any(finding["type"] == "success_pattern" for finding in review["findings"])
    assert any(item["kind"] == "success_pattern" for item in review["learning_candidates"])
    assert missing.status_code == 404


def test_trace_task_run_review_can_commit_to_experience_ledger(
    tmp_path: Path,
) -> None:
    client = _client_with_trace(tmp_path)

    committed = client.post("/api/agent-trace/task-runs/turn-1/review/commit")
    listed = client.get("/api/agent-trace/experience-ledger")
    # Use today's date as the week anchor so commit timestamps
    # (== "now") fall inside the half-open [week_start, week_start+7)
    # window regardless of which day of the week the test runs.
    from datetime import UTC, datetime

    today_iso = datetime.now(UTC).date().isoformat()
    summary = client.get(
        "/api/agent-trace/experience-ledger/weekly-summary",
        params={"week_start": today_iso},
    )
    missing = client.post("/api/agent-trace/task-runs/missing/review/commit")

    assert committed.status_code == 200
    assert committed.json()["commit"]["created"] == 2
    assert listed.status_code == 200
    records = listed.json()["records"]
    assert {record["kind"] for record in records} == {
        "success_pattern",
        "backlog_candidate",
    }
    assert summary.status_code == 200
    assert summary.json()["record_count"] == 2
    assert missing.status_code == 404


def test_trace_task_run_review_can_enter_review_queue(
    tmp_path: Path,
) -> None:
    client = _client_with_trace(tmp_path)

    queued = client.post("/api/agent-trace/task-runs/turn-1/review/queue")
    listed = client.get("/api/agent-trace/review-queue", params={"status": "pending"})
    summary = client.get("/api/agent-trace/review-queue/summary")
    missing = client.post("/api/agent-trace/task-runs/missing/review/queue")

    assert queued.status_code == 200
    assert queued.json()["queue"]["created"] == 2
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 2
    assert {item["target_bucket"] for item in items} == {
        "experience",
        "experiment_backlog",
    }
    assert summary.status_code == 200
    assert summary.json()["pending_count"] == 2
    assert missing.status_code == 404


def test_trace_review_queue_item_can_be_decided(tmp_path: Path) -> None:
    client = _client_with_trace(tmp_path)
    client.post("/api/agent-trace/task-runs/turn-1/review/queue")
    items = client.get("/api/agent-trace/review-queue").json()["items"]
    item_id = items[0]["id"]

    decided = client.post(
        f"/api/agent-trace/review-queue/{item_id}/decision",
        json={
            "action": "promoted",
            "reason": "Useful enough to keep.",
            "promoted_to": "experience",
        },
    )
    summary = client.get("/api/agent-trace/review-queue/summary")
    missing = client.post(
        "/api/agent-trace/review-queue/missing/decision",
        json={"action": "archived"},
    )
    invalid = client.post(
        f"/api/agent-trace/review-queue/{item_id}/decision",
        json={"action": "unknown"},
    )

    assert decided.status_code == 200
    assert decided.json()["item"]["status"] == "promoted"
    assert decided.json()["item"]["promoted_to"] == "experience"
    assert summary.status_code == 200
    assert summary.json()["pending_count"] == 1
    assert summary.json()["by_status"] == {"pending": 1, "promoted": 1}
    assert missing.status_code == 404
    assert invalid.status_code == 400


def test_trace_review_queue_promotions_can_plan_apply_and_audit(
    tmp_path: Path,
) -> None:
    client = _client_with_trace(tmp_path)
    client.post("/api/agent-trace/task-runs/turn-1/review/queue")
    items = client.get("/api/agent-trace/review-queue").json()["items"]
    item_id = items[0]["id"]
    client.post(
        f"/api/agent-trace/review-queue/{item_id}/decision",
        json={
            "action": "promoted",
            "reason": "Ready to apply.",
            "promoted_to": "experience",
        },
    )

    plan = client.post("/api/agent-trace/review-queue/promotions/plan")
    applied = client.post("/api/agent-trace/review-queue/promotions/apply")
    audit = client.get("/api/agent-trace/review-queue/promotions/audit")
    second_plan = client.post("/api/agent-trace/review-queue/promotions/plan")
    ledger = client.get("/api/agent-trace/experience-ledger")

    assert plan.status_code == 200
    assert plan.json()["dry_run"] is True
    assert plan.json()["applicable"] == 1
    assert applied.status_code == 200
    assert applied.json()["applied"] == 1
    assert audit.status_code == 200
    assert audit.json()["total"] == 1
    assert audit.json()["records"][0]["review_queue_item_id"] == item_id
    assert second_plan.status_code == 200
    assert second_plan.json()["skipped"] == 1
    assert ledger.status_code == 200
    assert ledger.json()["total"] == 1


def test_trace_task_run_process_timeline_merges_review_and_ledger(
    tmp_path: Path,
) -> None:
    client = _client_with_trace(tmp_path)

    client.post("/api/agent-trace/task-runs/turn-1/review/commit")
    response = client.get("/api/agent-trace/task-runs/turn-1/process-timeline")
    missing = client.get("/api/agent-trace/task-runs/missing/process-timeline")

    assert response.status_code == 200
    timeline = response.json()["timeline"]
    assert timeline["schema"] == "echo.process_timeline.v1"
    assert timeline["task_id"] == "turn-1"
    assert timeline["overview"]["status"] == "completed"
    assert timeline["overview"]["approval_count"] == 1
    assert timeline["overview"]["experience_record_count"] == 2
    lanes = {node["lane"] for node in timeline["timeline"]}
    assert {"execution", "permission", "review", "learning"}.issubset(lanes)
    kinds = {node["kind"] for node in timeline["timeline"]}
    assert "approval" in kinds
    assert "success_pattern" in kinds
    assert "experience_record" in kinds
    read_file = next(item for item in timeline["capabilities"] if item["tool"] == "read_file")
    assert read_file["risk"]["level"] == "low"
    assert timeline["safety"]["raw_messages_included"] is False
    assert missing.status_code == 404


def test_trace_approvals_tokens_and_latest_checkpoint_are_readable(tmp_path: Path) -> None:
    client = _client_with_trace(tmp_path)

    approvals = client.get("/api/agent-trace/approvals", params={"thread_id": "thread-1"})
    tokens = client.get("/api/agent-trace/token-usage", params={"task_id": "task-1"})
    checkpoints = client.get("/api/agent-trace/checkpoints", params={"task_id": "task-1"})
    checkpoint = client.get(
        "/api/agent-trace/checkpoints/latest",
        params={"task_id": "task-1", "checkpoint_type": "react"},
    )
    missing = client.get(
        "/api/agent-trace/checkpoints/latest",
        params={"task_id": "unknown"},
    )

    assert approvals.json()["approvals"][0]["decision"] == "approved"
    assert tokens.json()["usage"][0]["model"] == "gpt-test"
    assert checkpoints.json()["checkpoints"][0]["checkpoint_type"] == "react"
    assert checkpoint.status_code == 200
    assert checkpoint.json()["checkpoint"]["state"]["current_phase"] == "implementation"
    assert missing.status_code == 404


def test_trace_resume_proposal_is_sanitized(tmp_path: Path) -> None:
    client = _client_with_trace(tmp_path)
    checkpoints = client.get("/api/agent-trace/checkpoints", params={"task_id": "task-1"})
    checkpoint_id = checkpoints.json()["checkpoints"][0]["id"]

    response = client.get(f"/api/agent-trace/checkpoints/{checkpoint_id}/resume-proposal")
    missing = client.get("/api/agent-trace/checkpoints/99999/resume-proposal")

    assert response.status_code == 200
    proposal = response.json()["proposal"]
    assert proposal["checkpoint"]["id"] == checkpoint_id
    assert proposal["recovery_hints"]["phase"] == "implementation"
    assert proposal["resume_plan"]["steps"][1] == "Continue from iteration 3."
    assert proposal["safety"]["raw_state_included"] is False
    assert "secret message body" not in str(proposal)
    assert missing.status_code == 404


def test_trace_resume_proposals_supports_thread_scope(tmp_path: Path) -> None:
    client = _client_with_trace(tmp_path)

    response = client.get("/api/agent-trace/resume-proposals", params={"task_id": "task-1"})

    assert response.status_code == 200
    data = response.json()
    assert data["limit"] == 5
    assert len(data["proposals"]) == 1
    proposal = data["proposals"][0]
    assert proposal["checkpoint"]["type"] == "react"
    assert proposal["recovery_hints"]["phase"] == "implementation"
    assert "secret message body" not in str(data)


def test_trace_resume_requests_are_readable_and_sanitized(tmp_path: Path) -> None:
    client = _client_with_trace(tmp_path)

    response = client.get(
        "/api/agent-trace/resume-requests",
        params={"thread_id": "thread-1", "status": "pending"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["limit"] == 100
    assert len(data["requests"]) == 1
    request = data["requests"][0]
    assert request["status"] == "pending"
    assert request["intent"]["checkpoint_id"] == 1
    assert request["intent"]["requires_confirmation"] is True
    assert "secret message body" not in str(data)


def test_create_app_mounts_agent_trace_router(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from runtime.platform.ui import create_app

    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    app = create_app(journal_path=tmp_path / "data" / "events.jsonl")
    client = TestClient(app)

    response = client.get("/api/agent-trace/stats")

    assert response.status_code == 200
    assert response.json()["events"] == 0
