from __future__ import annotations

from runtime.memory.runtime_state.process_timeline import build_task_run_process_timeline


def test_process_timeline_merges_execution_approval_review_and_learning() -> None:
    timeline = build_task_run_process_timeline(
        task_run={
            "task_id": "turn-1",
            "thread_id": "thread-1",
            "turn_id": "turn-1",
            "agent_id": "agent-a",
            "status": "failed",
            "title": "Run tests",
            "tool_names": ["exec_shell"],
            "tool_calls_started": 1,
            "tool_calls_finished": 1,
            "tool_errors": 1,
            "started_at": "2026-06-07T00:00:00+00:00",
            "completed_at": "2026-06-07T00:00:03+00:00",
            "updated_at": "2026-06-07T00:00:03+00:00",
            "token_totals": {"input_tokens": 10, "output_tokens": 5},
        },
        review={
            "task_id": "turn-1",
            "status": "failed",
            "score": 0.2,
            "score_reasons": ["status:failed"],
            "replay": {
                "steps": [
                    {"kind": "task_start", "ts": "2026-06-07T00:00:00+00:00"},
                    {
                        "kind": "tool_start",
                        "ts": "2026-06-07T00:00:01+00:00",
                        "tool": "exec_shell",
                        "input_preview": "pytest",
                    },
                    {
                        "kind": "tool_end",
                        "ts": "2026-06-07T00:00:02+00:00",
                        "tool": "exec_shell",
                        "status": "error",
                        "is_error": True,
                        "output_preview": "AssertionError",
                    },
                ]
            },
            "findings": [
                {
                    "type": "tool_error",
                    "severity": "high",
                    "title": "Tool failed: exec_shell",
                    "evidence": {"tool": "exec_shell"},
                    "recommendation": "Create replay fixture.",
                }
            ],
            "learning_candidates": [
                {
                    "kind": "failure_pattern",
                    "priority": "P0",
                    "memory_bucket": "experience",
                    "title": "Tool failure pattern: exec_shell",
                    "text": "Add preflight validation.",
                }
            ],
            "backlog_candidates": [
                {
                    "priority": "P0",
                    "experiment": "Create deterministic replay case",
                    "hypothesis": "Replay prevents recurrence.",
                }
            ],
        },
        approvals=[
            {
                "id": 1,
                "requested_at": "2026-06-07T00:00:01+00:00",
                "decided_at": "2026-06-07T00:00:01+00:00",
                "tool_name": "exec_shell",
                "tool_call_id": "call-1",
                "decision": "approved",
                "metadata": {
                    "trust_gateway": {
                        "source": "risk_policy",
                        "action": "ask",
                        "risk": {
                            "level": "high",
                            "categories": ["shell_execution"],
                            "reason": "shell_execution",
                            "requires_approval": True,
                        },
                    }
                },
            }
        ],
        experience_records=[
            {
                "id": "exp_1",
                "title": "Tool failure pattern: exec_shell",
                "text": "Add preflight validation.",
                "priority": "P0",
                "memory_bucket": "experience",
                "status": "active",
                "occurrences": 1,
                "last_seen_at": "2026-06-07T00:00:04+00:00",
            }
        ],
    )

    assert timeline["schema"] == "echo.process_timeline.v1"
    assert timeline["overview"]["score"] == 0.2
    assert timeline["overview"]["approval_count"] == 1
    assert timeline["overview"]["experience_record_count"] == 1
    lanes = {node["lane"] for node in timeline["timeline"]}
    assert lanes == {"execution", "permission", "review", "learning"}
    kinds = {node["kind"] for node in timeline["timeline"]}
    assert "approval" in kinds
    assert "tool_error" in kinds
    assert "experience_record" in kinds
    assert timeline["capabilities"][0]["tool"] == "exec_shell"
    assert timeline["capabilities"][0]["risk"]["level"] == "high"
    assert timeline["safety"]["raw_messages_included"] is False
