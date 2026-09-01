"""Tests for realtime cerebrum file changes — diff emission, code verification, auto-verification, hunk decisions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    FastAPI = None  # type: ignore[assignment]
    TestClient = None  # type: ignore[assignment]

from runtime.protocol import (
    ItemStatus,
    JsonRpcRequest,
    JsonRpcResponse,
    VerificationItem,
    decode_message,
    encode_message,
)
from tests.realtime_cerebrum._helpers import (
    drive as _drive,
)
from tests.realtime_cerebrum._helpers import (
    set_script as _set_script,
)


def test_tool_end_with_diff_emits_file_change_item(gateway: Any) -> None:
    """When react_loop emits a ``tool_end`` carrying a unified diff,
    the bridge must promote it to a structured FileChangeItem so the
    UI can render hunk-level controls."""
    client, _ = gateway
    diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1,3 +1,3 @@\n x\n-old\n+new\n y\n"
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "edit_text_file",
                "tool_call_id": "call-edit",
            },
            {
                "type": "tool_end",
                "tool_name": "edit_text_file",
                "tool_call_id": "call-edit",
                "iteration": 1,
                "status": "success",
                "output_preview": "ok",
                "duration_ms": 1,
                "diff": diff,
                "verification": {
                    "command": "pytest tests/test_foo.py",
                    "kind": "test",
                    "exit_code": 0,
                    "success": True,
                    "stdout_tail": "1 passed",
                },
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_filechange",
                "input": [{"type": "text", "text": "edit"}],
                "approvalPolicy": "never",
            },
        )

    hunk_deltas = [n for n in out["notifications"] if n.method == "item/fileChange/hunkDelta"]
    assert len(hunk_deltas) == 1
    assert hunk_deltas[0].params["path"] == "src/foo.py"
    assert hunk_deltas[0].params["workspaceFocus"]["view"] == "diff"
    assert hunk_deltas[0].params["hunk"]["decision"] == "pending"

    turn = out["response"].result["turn"]
    file_items = [it for it in turn["items"] if it["type"] == "fileChange"]
    assert len(file_items) == 1
    fci = file_items[0]
    assert len(fci["changes"]) == 1
    change = fci["changes"][0]
    assert change["path"] == "src/foo.py"
    assert change["op"] == "update"
    assert len(change["hunks"]) == 1
    hunk = change["hunks"][0]
    assert hunk["oldStart"] == 1 and hunk["newStart"] == 1
    assert hunk["decision"] == "pending"
    # The promoted item must land as completed, not stay inProgress and get
    # swept to failed by _close_turn when the turn ends.
    assert fci["status"] == "completed"


def test_code_file_change_without_verification_fails_turn(gateway: Any) -> None:
    client, logs_root = gateway
    diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1,2 +1,2 @@\n-old\n+new\n"
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
            },
            {
                "type": "tool_end",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
                "iteration": 1,
                "status": "success",
                "output_preview": "ok",
                "duration_ms": 1,
                "diff": diff,
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_unverified_code_change",
                "input": [{"type": "text", "text": "edit"}],
                "approvalPolicy": "never",
            },
        )

    turn = out["response"].result["turn"]
    assert turn["status"] == "failed"
    # The bounded agent-verification follow-up reuses this fake script. A
    # provider may also reuse call ids across real rounds; public item ids
    # must remain unique for the entire turn.
    command_ids = [it["id"] for it in turn["items"] if it["type"] == "commandExecution"]
    assert command_ids == ["call-edit", "call-edit#2"]
    verification_items = [it for it in turn["items"] if it["type"] == "verification"]
    assert len(verification_items) == 1
    assert verification_items[0]["kind"] == "manual"
    assert verification_items[0]["status"] == "failed"
    assert verification_items[0]["relatedFiles"] == ["src/foo.py"]
    assert "Recommended verification commands:" in verification_items[0]["stdoutTail"]
    assert "python -m ruff check src/foo.py" in verification_items[0]["stdoutTail"]

    ledger_path = logs_root.parent / "proposal_ledger.jsonl"
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["kind"] == "turn_failure"
    assert entry["proposer"] == "realtime_cerebrum"
    assert entry["metadata"]["failure_source"] == "verification_required"
    assert entry["metadata"]["code_change_paths"] == ["src/foo.py"]
    assert entry["metadata"]["turn_id"] == turn["id"]
    assert entry["metadata"]["verification_plan"]["schema"] == "echo.verification_plan.v1"
    assert entry["metadata"]["verification_plan"]["commands"][0]["command"] == (
        "python -m ruff check src/foo.py"
    )


def test_code_file_change_auto_runs_safe_verification(
    gateway: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    client, _logs_root = gateway
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.py").write_text("value = 1\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_foo.py").write_text(
        "def test_foo():\n    assert True\n",
        encoding="utf-8",
    )
    diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1,1 +1,1 @@\n-value = 0\n+value = 1\n"
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
            },
            {
                "type": "tool_end",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
                "iteration": 1,
                "status": "success",
                "output_preview": "ok",
                "duration_ms": 1,
                "diff": diff,
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_auto_verified_code_change",
                "cwd": str(tmp_path),
                "input": [{"type": "text", "text": "edit"}],
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "workspaceWrite", "networkAccess": False},
            },
        )

    turn = out["response"].result["turn"]
    assert turn["status"] == "completed"
    verification_items = [it for it in turn["items"] if it["type"] == "verification"]
    assert len(verification_items) == 2
    assert verification_items[0]["command"] == "python -m ruff check src/foo.py"
    assert verification_items[0]["kind"] == "lint"
    assert verification_items[0]["status"] == "completed"
    assert verification_items[0]["relatedFiles"] == ["src/foo.py"]
    assert verification_items[1]["command"] == ("python -m pytest tests/test_foo.py -q")
    assert verification_items[1]["kind"] == "test"
    assert verification_items[1]["status"] == "completed"

    metrics = (data_dir / "auto_verifier_metrics.jsonl").read_text(encoding="utf-8")
    assert '"family": "ruff"' in metrics
    assert '"family": "pytest"' in metrics
    assert '"ok": true' in metrics
    decisions = (data_dir / "auto_verifier_decisions.jsonl").read_text(encoding="utf-8")
    assert '"selected_command": "python -m ruff check src/foo.py"' in decisions
    assert '"selected_command": "python -m pytest tests/test_foo.py -q"' in decisions
    assert "no history for ruff" in decisions


def test_failed_auto_verifier_gets_bounded_model_repair_and_fresh_evidence(
    gateway: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.safety.evolution import auto_verifier
    from tests.realtime_cerebrum import _helpers

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    calls = 0

    def fake_verification_plan(*_args: Any, **_kwargs: Any) -> list[VerificationItem]:
        nonlocal calls
        calls += 1
        passed = calls > 1
        return [
            VerificationItem(
                command="python -m pytest tests/test_foo.py -q",
                kind="test",
                status=ItemStatus.COMPLETED if passed else ItemStatus.FAILED,
                exit_code=0 if passed else 1,
                summary="fresh pass" if passed else "assertion failed",
                stdout_tail="1 passed" if passed else "1 failed",
                related_files=["src/foo.py"],
            )
        ]

    monkeypatch.setattr(auto_verifier, "run_verification_plan", fake_verification_plan)
    client, _logs_root = gateway
    diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
            },
            {
                "type": "tool_end",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
                "iteration": 1,
                "status": "success",
                "output_preview": "ok",
                "duration_ms": 1,
                "diff": diff,
            },
            {"type": "react_completed"},
        ]
    )

    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_bounded_verification_repair",
                "cwd": str(tmp_path),
                "input": [{"type": "text", "text": "edit and verify"}],
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "workspaceWrite", "networkAccess": False},
            },
        )

    turn = out["response"].result["turn"]
    verification_items = [item for item in turn["items"] if item["type"] == "verification"]
    assert turn["status"] == "completed"
    assert calls == 2
    assert [item["status"] for item in verification_items] == ["failed", "completed"]
    repair_intent = _helpers._LAST_STREAM_ARGS["args"][1]
    repair = repair_intent.user_context["verification_repair"]
    assert repair["schema"] == "echo.verification_repair_request.v1"
    assert repair["attempt"] == 1
    assert repair["fresh_evidence_required"] is True
    decisions = (data_dir / "auto_verifier_decisions.jsonl").read_text(encoding="utf-8")
    assert '"status": "requested"' in decisions
    assert '"status": "passed"' in decisions


def test_verification_repair_stops_after_two_failed_model_rounds(
    gateway: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.safety.evolution import auto_verifier

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("ECHO_DATA_DIR", str(data_dir))
    calls = 0

    def always_failing(*_args: Any, **_kwargs: Any) -> list[VerificationItem]:
        nonlocal calls
        calls += 1
        return [
            VerificationItem(
                command="python -m pytest tests/test_foo.py -q",
                kind="test",
                status=ItemStatus.FAILED,
                exit_code=1,
                summary="still failing",
                related_files=["src/foo.py"],
            )
        ]

    monkeypatch.setattr(auto_verifier, "run_verification_plan", always_failing)
    client, _logs_root = gateway
    diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
            },
            {
                "type": "tool_end",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
                "iteration": 1,
                "status": "success",
                "output_preview": "ok",
                "duration_ms": 1,
                "diff": diff,
            },
            {"type": "react_completed"},
        ]
    )

    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_exhausted_verification_repair",
                "cwd": str(tmp_path),
                "input": [{"type": "text", "text": "edit and verify"}],
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "workspaceWrite", "networkAccess": False},
            },
        )

    turn = out["response"].result["turn"]
    assert turn["status"] == "failed"
    assert calls == 3
    decisions = (data_dir / "auto_verifier_decisions.jsonl").read_text(encoding="utf-8")
    assert decisions.count('"status": "requested"') == 2
    assert decisions.count('"status": "failed"') == 2


def test_code_file_change_with_successful_verification_can_complete(gateway: Any) -> None:
    client, _ = gateway
    diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1,2 +1,2 @@\n-old\n+new\n"
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
            },
            {
                "type": "tool_end",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
                "iteration": 1,
                "status": "success",
                "output_preview": "ok",
                "duration_ms": 1,
                "diff": diff,
                "verification": {
                    "command": "pytest tests/test_foo.py",
                    "kind": "test",
                    "exit_code": 0,
                    "success": True,
                    "stdout_tail": "1 passed",
                },
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_verified_code_change",
                "input": [{"type": "text", "text": "edit"}],
                "approvalPolicy": "never",
            },
        )

    turn = out["response"].result["turn"]
    assert turn["status"] == "completed"
    file_items = [it for it in turn["items"] if it["type"] == "fileChange"]
    verification_items = [it for it in turn["items"] if it["type"] == "verification"]
    assert len(file_items) == 1
    assert len(verification_items) == 1
    assert verification_items[0]["status"] == "completed"
    assert verification_items[0]["relatedFiles"] == ["src/foo.py"]
    assert verification_items[0]["relatedChangeItemIds"] == [file_items[0]["id"]]


def test_code_file_change_with_failed_verification_fails_turn(gateway: Any) -> None:
    client, logs_root = gateway
    diff = "--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1,2 +1,2 @@\n-old\n+new\n"
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
            },
            {
                "type": "tool_end",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
                "iteration": 1,
                "status": "success",
                "output_preview": "tests failed",
                "duration_ms": 1,
                "diff": diff,
                "verification": {
                    "command": "pytest tests/test_foo.py",
                    "kind": "test",
                    "exit_code": 1,
                    "success": False,
                    "stdout_tail": "1 failed",
                },
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_failed_verified_code_change",
                "input": [{"type": "text", "text": "edit"}],
                "approvalPolicy": "never",
            },
        )

    turn = out["response"].result["turn"]
    assert turn["status"] == "failed"
    verification_items = [it for it in turn["items"] if it["type"] == "verification"]
    assert len(verification_items) == 1
    assert verification_items[0]["status"] == "failed"
    assert verification_items[0]["relatedFiles"] == ["src/foo.py"]

    ledger_path = logs_root.parent / "proposal_ledger.jsonl"
    entries = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    failures = [entry for entry in entries if entry["kind"] == "turn_failure"]
    assert failures[-1]["metadata"]["failure_source"] == "verification_failed"
    assert failures[-1]["metadata"]["failed_verifications"][0]["command"] == (
        "pytest tests/test_foo.py"
    )
    assert (
        failures[-1]["metadata"]["failed_verifications"][0]["diagnosis"]["category"]
        == "test_failure"
    )
    assert (
        failures[-1]["metadata"]["failed_verifications"][0]["diagnosis"]["action"]
        == "fix_code_or_test_expectation"
    )
    route = failures[-1]["metadata"]["failed_verifications"][0]["diagnosis"]["repair_route"]
    assert route["route"] == "test_driven_repair"
    assert route["strategy"] == "reproduce_and_patch_behavior"
    assert failures[-1]["metadata"]["primary_repair_route"] == "test_driven_repair"


def test_non_code_file_change_without_verification_can_complete(gateway: Any) -> None:
    client, logs_root = gateway
    diff = "--- a/notes.md\n+++ b/notes.md\n@@ -1,2 +1,2 @@\n-old\n+new\n"
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "write_text_file",
                "tool_call_id": "call-write",
            },
            {
                "type": "tool_end",
                "tool_name": "write_text_file",
                "tool_call_id": "call-write",
                "iteration": 1,
                "status": "success",
                "output_preview": "ok",
                "duration_ms": 1,
                "diff": diff,
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_unverified_note_change",
                "input": [{"type": "text", "text": "write notes"}],
                "approvalPolicy": "never",
            },
        )

    turn = out["response"].result["turn"]
    assert turn["status"] == "completed"
    ledger_path = logs_root.parent / "proposal_ledger.jsonl"
    entries = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    success = [entry for entry in entries if entry["kind"] == "turn_success"]
    assert len(success) == 1
    assert success[0]["metadata"]["goal"] == "write notes"

    turn = out["response"].result["turn"]
    assert turn["status"] == "completed"
    assert not [it for it in turn["items"] if it["type"] == "verification"]


def test_tool_end_with_post_write_diagnostics_emits_verification_item(gateway: Any) -> None:
    client, _ = gateway
    diagnostics = (
        "ok\n\n"
        "[post-write diagnostics]\n"
        "ruff diagnostics (foo.py):\n"
        "E999 SyntaxError: expected ':'\n"
    )
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "write_text_file",
                "tool_call_id": "call-write",
                "input_preview": {"path": "src/foo.py"},
            },
            {
                "type": "tool_end",
                "tool_name": "write_text_file",
                "tool_call_id": "call-write",
                "iteration": 1,
                "status": "success",
                "output_preview": diagnostics,
                "duration_ms": 1,
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_verify",
                "input": [{"type": "text", "text": "write"}],
                "approvalPolicy": "never",
            },
        )

    completed = [n.params["item"] for n in out["notifications"] if n.method == "item/completed"]
    verification_events = [it for it in completed if it["type"] == "verification"]
    assert len(verification_events) == 1
    assert verification_events[0]["kind"] == "diagnostic"
    assert verification_events[0]["status"] == "failed"
    assert verification_events[0]["exitCode"] == 1
    assert verification_events[0]["relatedFiles"] == ["src/foo.py"]

    turn = out["response"].result["turn"]
    verification_items = [it for it in turn["items"] if it["type"] == "verification"]
    assert len(verification_items) == 1
    assert "ruff diagnostics" in verification_items[0]["stdoutTail"]
    assert turn["status"] == "failed"


def test_tool_end_with_explicit_verification_metadata_emits_verification_item(gateway: Any) -> None:
    client, _ = gateway
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "run_tests",
                "tool_call_id": "call-test",
            },
            {
                "type": "tool_end",
                "tool_name": "run_tests",
                "tool_call_id": "call-test",
                "iteration": 1,
                "status": "success",
                "output_preview": "tests failed",
                "duration_ms": 1,
                "verification": {
                    "command": "pnpm test",
                    "kind": "test",
                    "exit_code": 1,
                    "success": False,
                    "stdout_tail": "1 failed",
                },
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_explicit_verify",
                "input": [{"type": "text", "text": "test"}],
                "approvalPolicy": "never",
            },
        )

    turn = out["response"].result["turn"]
    verification_items = [it for it in turn["items"] if it["type"] == "verification"]
    assert len(verification_items) == 1
    assert verification_items[0]["command"] == "pnpm test"
    assert verification_items[0]["kind"] == "test"
    assert verification_items[0]["status"] == "failed"
    assert verification_items[0]["exitCode"] == 1
    assert verification_items[0]["stdoutTail"] == "1 failed"


def test_tool_end_verification_without_success_uses_event_failure(gateway: Any) -> None:
    client, _ = gateway
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "exec_shell",
                "tool_call_id": "call-typecheck",
            },
            {
                "type": "tool_end",
                "tool_name": "exec_shell",
                "tool_call_id": "call-typecheck",
                "iteration": 1,
                "status": "error",
                "output_preview": "command failed",
                "duration_ms": 1,
                "verification": {
                    "command": "npx -y tsc --noEmit",
                    "kind": "typecheck",
                    "stderr_tail": "[WinError 2] file not found",
                },
            },
            {"type": "react_completed", "success": False},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_verify_unknown_failed",
                "input": [{"type": "text", "text": "test"}],
                "approvalPolicy": "never",
            },
        )

    turn = out["response"].result["turn"]
    verification_items = [it for it in turn["items"] if it["type"] == "verification"]
    assert len(verification_items) == 1
    assert verification_items[0]["command"] == "npx -y tsc --noEmit"
    assert verification_items[0]["kind"] == "typecheck"
    assert verification_items[0]["status"] == "failed"
    assert turn["status"] == "failed"


def test_failed_verification_metadata_classifies_missing_tool(
    gateway: Any,
) -> None:
    client, logs_root = gateway
    diff = "--- a/src/foo.ts\n+++ b/src/foo.ts\n@@ -1,2 +1,2 @@\n-old\n+new\n"
    _set_script(
        [
            {
                "type": "tool_start",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
            },
            {
                "type": "tool_end",
                "tool_name": "edit_file",
                "tool_call_id": "call-edit",
                "iteration": 1,
                "status": "success",
                "output_preview": "typecheck failed",
                "duration_ms": 1,
                "diff": diff,
                "verification": {
                    "command": "npx --no-install tsc --noEmit",
                    "kind": "typecheck",
                    "exit_code": 1,
                    "success": False,
                    "stderr_tail": "[WinError 2] file not found",
                },
            },
            {"type": "react_completed"},
        ]
    )
    with client.websocket_connect("/api/realtime") as ws:
        out = _drive(
            ws,
            {
                "threadId": "th_failed_missing_tool",
                "input": [{"type": "text", "text": "edit"}],
                "approvalPolicy": "never",
            },
        )

    turn = out["response"].result["turn"]
    assert turn["status"] == "failed"
    ledger_path = logs_root.parent / "proposal_ledger.jsonl"
    failures = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["kind"] == "turn_failure"
    ]
    diagnosis = failures[-1]["metadata"]["failed_verifications"][0]["diagnosis"]
    assert diagnosis["category"] == "environment_missing_tool"
    assert diagnosis["action"] == "install_or_select_available_verifier"
    assert diagnosis["retryable"] is True
    assert diagnosis["repair_route"]["route"] == "environment_repair"
    assert failures[-1]["metadata"]["primary_repair_route"] == "environment_repair"


def test_hunk_decide_rejected_reverts_file(gateway: Any, tmp_path: Path) -> None:
    """Client rejecting a hunk reverse-applies its diff to the file."""
    client, _ = gateway
    target = tmp_path / "sample.txt"
    target.write_text("x\nnew\ny\n", encoding="utf-8")
    diff = "--- a/sample.txt\n+++ b/sample.txt\n@@ -1,3 +1,3 @@\n x\n-old\n+new\n y\n"
    with client.websocket_connect("/api/realtime") as ws:
        ws.send_text(
            encode_message(
                JsonRpcRequest(
                    id=42,
                    method="item/fileChange/hunkDecide",
                    params={
                        "threadId": "th",
                        "turnId": "tn",
                        "itemId": "it",
                        "hunkId": "h1",
                        "path": str(target),
                        "decision": "rejected",
                        "diff": diff,
                    },
                )
            )
        )
        while True:
            msg = decode_message(ws.receive_text())
            if isinstance(msg, JsonRpcResponse) and msg.id == 42:
                break
    assert msg.error is None
    assert msg.result["decision"] == "rejected"
    assert target.read_text(encoding="utf-8") == "x\nold\ny\n"


def test_hunk_decide_accepted_does_not_touch_file(gateway: Any, tmp_path: Path) -> None:
    client, _ = gateway
    target = tmp_path / "kept.txt"
    target.write_text("after-edit\n", encoding="utf-8")
    with client.websocket_connect("/api/realtime") as ws:
        ws.send_text(
            encode_message(
                JsonRpcRequest(
                    id=43,
                    method="item/fileChange/hunkDecide",
                    params={
                        "path": str(target),
                        "decision": "accepted",
                    },
                )
            )
        )
        while True:
            msg = decode_message(ws.receive_text())
            if isinstance(msg, JsonRpcResponse) and msg.id == 43:
                break
    assert msg.error is None
    assert msg.result["decision"] == "accepted"
    assert target.read_text(encoding="utf-8") == "after-edit\n"
