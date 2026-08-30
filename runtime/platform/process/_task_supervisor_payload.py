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
    payload = _empty_payload()
    if not isinstance(raw, dict):
        return payload
    payload["lastUpdated"] = str(raw.get("lastUpdated") or "")
    try:
        payload["leaseCounter"] = max(0, int(raw.get("leaseCounter") or 0))
    except (TypeError, ValueError):
        payload["leaseCounter"] = 0
    rows: list[dict[str, Any]] = []
    for item in raw.get("tasks") or []:
        if not isinstance(item, dict):
            continue
        try:
            record = TaskRunRecord.model_validate(item)
        except Exception:
            continue
        rows.append(record.model_dump(mode="json"))
    payload["tasks"] = rows
    return payload
