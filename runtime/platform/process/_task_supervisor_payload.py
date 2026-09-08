from __future__ import annotations

from typing import Any

from runtime.platform.process._task_supervisor_models import TaskRunRecord

_SCHEMA = "echo.task_supervisor.v1"


def _empty_payload() -> dict[str, Any]:
    return {
        "schema": _SCHEMA,
        "version": 1,
        "lastUpdated": "",
        "leaseCounter": 0,
        "tasks": [],
    }


def _normalize_payload(raw: Any) -> dict[str, Any]:
    """Validate a complete authority snapshot; never repair by dropping rows."""
    payload = _empty_payload()
    if not isinstance(raw, dict) or raw.get("schema") != _SCHEMA:
        raise ValueError("invalid task authority schema")
    if type(raw.get("version")) is not int or raw["version"] != 1:
        raise ValueError("invalid task authority version")
    counter = raw.get("leaseCounter")
    if type(counter) is not int or counter < 0:
        raise ValueError("invalid task authority lease counter")
    if not isinstance(raw.get("tasks"), list):
        raise ValueError("invalid task authority rows")
    payload["lastUpdated"] = str(raw.get("lastUpdated") or "")
    payload["leaseCounter"] = counter
    rows: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    for item in raw["tasks"]:
        if not isinstance(item, dict):
            raise ValueError("invalid task authority row")
        record = TaskRunRecord.model_validate(item)
        if record.task_id in task_ids:
            raise ValueError("duplicate task authority row")
        task_ids.add(record.task_id)
        if record.lease is not None and record.lease.token > counter:
            raise ValueError("task authority lease counter is behind its records")
        rows.append(record.model_dump(mode="json"))
    payload["tasks"] = rows
    return payload
