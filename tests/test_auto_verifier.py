from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from runtime.protocol import ItemStatus, VerificationItem
from runtime.safety.evolution import auto_verifier


def _plan(workspace: Path) -> dict[str, Any]:
    target = workspace / "src.py"
    target.write_text("value = 1\n", encoding="utf-8")
    return {
        "workspace": str(workspace),
        "commands": [
            {
                "command": "python -m ruff check src.py",
                "kind": "lint",
                "target": "src.py",
                "priority": 1,
            },
            {
                "command": "python -m pytest src.py -q",
                "kind": "test",
                "target": "src.py",
                "priority": 2,
            },
            {
                "command": "python -m pytest src.py -q",
                "kind": "test",
                "target": "src.py",
                "priority": 3,
            },
        ],
    }


def _item(command: dict[str, Any], status: ItemStatus) -> VerificationItem:
    return VerificationItem(
        command=str(command["command"]),
        kind=str(command["kind"]),
        status=status,
        exit_code=0 if status == ItemStatus.COMPLETED else 1,
        summary="passed" if status == ItemStatus.COMPLETED else "failed",
        related_files=[str(command["target"])],
    )


def test_verification_plan_runs_distinct_checks_within_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[str] = []
    batches: list[dict[str, Any]] = []

    def fake_run(
        command: dict[str, Any],
        _workspace: Path,
        _sandbox_policy: dict[str, Any],
    ) -> VerificationItem:
        executed.append(str(command["command"]))
        return _item(command, ItemStatus.COMPLETED)

    monkeypatch.setattr(auto_verifier, "_run_command", fake_run)
    monkeypatch.setattr(
        auto_verifier,
        "rank_verification_commands",
        lambda candidates: candidates,
    )
    monkeypatch.setattr(
        auto_verifier,
        "record_auto_verifier_batch",
        lambda **payload: batches.append(payload),
    )

    items = auto_verifier.run_verification_plan(
        _plan(tmp_path),
        sandbox_policy={"type": "workspaceWrite", "networkAccess": False},
        max_commands=3,
    )

    assert [item.kind for item in items] == ["lint", "test"]
    assert executed == [
        "python -m ruff check src.py",
        "python -m pytest src.py -q",
    ]
    assert batches == [
        {
            "candidate_count": 3,
            "commands": executed,
            "passed_count": 2,
            "stop_reason": "exhausted",
        }
    ]


def test_verification_plan_stops_at_first_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[str] = []
    batches: list[dict[str, Any]] = []

    def fake_run(
        command: dict[str, Any],
        _workspace: Path,
        _sandbox_policy: dict[str, Any],
    ) -> VerificationItem:
        executed.append(str(command["command"]))
        status = ItemStatus.FAILED if command["kind"] == "lint" else ItemStatus.COMPLETED
        return _item(command, status)

    monkeypatch.setattr(auto_verifier, "_run_command", fake_run)
    monkeypatch.setattr(
        auto_verifier,
        "rank_verification_commands",
        lambda candidates: candidates,
    )
    monkeypatch.setattr(
        auto_verifier,
        "record_auto_verifier_batch",
        lambda **payload: batches.append(payload),
    )

    items = auto_verifier.run_verification_plan(
        _plan(tmp_path),
        sandbox_policy={"type": "workspaceWrite", "networkAccess": False},
    )

    assert len(items) == 1
    assert items[0].status == ItemStatus.FAILED
    assert executed == ["python -m ruff check src.py"]
    assert batches[0]["stop_reason"] == "failed"
    assert batches[0]["passed_count"] == 0


def test_verification_plan_requires_workspace_write_sandbox(tmp_path: Path) -> None:
    assert (
        auto_verifier.run_verification_plan(
            _plan(tmp_path),
            sandbox_policy={"type": "readOnly"},
        )
        == []
    )


def test_verification_repair_request_is_bounded_and_carries_failure_evidence(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    failed = VerificationItem(
        command="python -m pytest src.py -q",
        kind="test",
        status=ItemStatus.FAILED,
        exit_code=1,
        summary="Auto verification failed.",
        stdout_tail="assert 1 == 2",
        related_files=["src.py"],
    )

    request = auto_verifier.build_verification_repair_request(
        plan,
        [failed],
        attempt=99,
        max_attempts=99,
    )

    assert request["schema"] == "echo.verification_repair_request.v1"
    assert request["attempt"] == 2
    assert request["max_attempts"] == 2
    assert request["fresh_evidence_required"] is True
    assert request["failures"][0]["command"] == failed.command
    assert "assert 1 == 2" in request["prompt"]
    assert "runtime will rerun" in request["prompt"]


def test_agent_verification_request_is_bounded_and_carries_commands(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    request = auto_verifier.build_agent_verification_request(
        plan,
        attempt=99,
        max_attempts=99,
    )

    assert request["schema"] == "echo.verification_request.v1"
    assert request["attempt"] == 2
    assert request["max_attempts"] == 2
    assert request["fresh_evidence_required"] is True
    assert len(request["commands"]) == 3
    assert request["commands"][0]["command"] == "python -m ruff check src.py"
    assert "no verification step was recorded" in request["prompt"]
    assert "python -m ruff check src.py" in request["prompt"]
    assert "Do not claim success without fresh passing evidence" in request["prompt"]


def test_agent_verification_request_handles_empty_plan(tmp_path: Path) -> None:
    request = auto_verifier.build_agent_verification_request(
        {"workspace": str(tmp_path), "targets": ["src.py"], "commands": []},
        attempt=1,
    )

    assert request["commands"] == []
    assert "pick the repository's test / lint / build" in request["prompt"]


