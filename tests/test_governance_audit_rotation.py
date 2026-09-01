from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from runtime.safety.evolution.governance_audit_rotation import (
    configure_governance_audit_rotation,
    governance_audit_rotation_status,
    register_governance_audit_rotation_task,
    run_due_governance_audit_rotation,
)


def test_scheduled_rotation_exports_only_when_due(tmp_path: Path) -> None:
    audit_path = tmp_path / "promotion_audit.json"
    configured_at = datetime(2026, 7, 18, 1, 0, tzinfo=UTC)
    configure_governance_audit_rotation(
        enabled=True,
        cron_expression="0 2 * * *",
        retention_count=3,
        actor="operator-a",
        audit_path=audit_path,
        now=configured_at,
    )

    early = run_due_governance_audit_rotation(
        audit_path=audit_path,
        now=datetime(2026, 7, 18, 1, 30, tzinfo=UTC),
    )
    due = run_due_governance_audit_rotation(
        audit_path=audit_path,
        now=datetime(2026, 7, 18, 2, 1, tzinfo=UTC),
    )

    assert early == {
        "schema": "echo.governance_audit_rotation_run.v1",
        "status": "skipped",
        "created": False,
        "reason": "not_due",
        "next_run_at": "2026-07-18T02:00:00Z",
    }
    assert due["status"] == "exported"
    assert due["created"] is True
    assert due["integrity"]["ok"] is True
    export_path = Path(due["export_path"])
    assert export_path.is_file()
    bundle = json.loads(export_path.read_text(encoding="utf-8"))
    assert bundle["schema"] == "echo.governance_audit_export.v1"
    assert bundle["audit_sha256"] == due["audit_sha256"]
    status = governance_audit_rotation_status(
        audit_path=audit_path,
        now=datetime(2026, 7, 18, 2, 2, tzinfo=UTC),
    )
    assert status["due"] is False
    assert status["export_count"] == 1
    assert status["next_run_at"] == "2026-07-19T02:00:00Z"


def test_forced_rotation_prunes_old_exports_and_records_receipts(tmp_path: Path) -> None:
    audit_path = tmp_path / "promotion_audit.json"
    configure_governance_audit_rotation(
        enabled=True,
        cron_expression="0 2 * * *",
        retention_count=2,
        audit_path=audit_path,
        now=datetime(2026, 7, 18, 0, 0, tzinfo=UTC),
    )

    runs = [
        run_due_governance_audit_rotation(
            force=True,
            actor="operator-a",
            audit_path=audit_path,
            now=datetime(2026, 7, day, 3, 0, tzinfo=UTC),
        )
        for day in (18, 19, 20)
    ]

    assert all(run["status"] == "exported" for run in runs)
    assert len(runs[-1]["pruned_paths"]) == 1
    export_dir = tmp_path / "governance_audit_exports"
    assert len(list(export_dir.glob("governance-audit-*.json"))) == 2
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    event_types = [record["event_type"] for record in audit["records"]]
    assert event_types.count("governance_audit_export_rotation") == 3
    assert runs[-1]["receipt_id"] == audit["records"][-1]["id"]


def test_rotation_fails_closed_when_audit_integrity_is_broken(tmp_path: Path) -> None:
    audit_path = tmp_path / "promotion_audit.json"
    configure_governance_audit_rotation(
        enabled=True,
        audit_path=audit_path,
        now=datetime(2026, 7, 18, 0, 0, tzinfo=UTC),
    )
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["records"][0]["status"] = "tampered"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")

    result = run_due_governance_audit_rotation(
        force=True,
        audit_path=audit_path,
        now=datetime(2026, 7, 18, 1, 0, tzinfo=UTC),
    )

    assert result["status"] == "failed"
    assert result["reason"] == "integrity_failed"
    assert result["created"] is False
    assert not (tmp_path / "governance_audit_exports").exists()
    state = json.loads(
        (tmp_path / "governance_audit_rotation_state.json").read_text(encoding="utf-8")
    )
    assert state["last_status"] == "integrity_failed"


def test_rotation_registers_shell_free_scheduler_tick() -> None:
    calls: list[tuple] = []

    class _Runner:
        def add_periodic(self, *args, **kwargs) -> None:
            calls.append((args, kwargs))

    assert register_governance_audit_rotation_task(_Runner()) == 1
    args, kwargs = calls[0]
    assert args[0] == "governance-audit-export-rotation"
    assert args[1] == 60.0
    assert args[2] is run_due_governance_audit_rotation
    assert kwargs == {"run_on_start": True}

