"""Timer-driven consistency checks for healthy Echo-managed md RAID1 arrays."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from appliance.mdraid_check_schedule_policy import read_policy
from appliance.native_mdraid_check import (
    apply_mdraid_check,
    mdraid_maintenance,
    plan_mdraid_check,
)

MAX_ARRAYS = 16


def run_schedule(
    *,
    policy_reader: Callable[[], tuple[bool, dict[str, Any]]] = read_policy,
    inventory_reader: Callable[[], list[dict[str, Any]]] = mdraid_maintenance,
    planner: Callable[[dict[str, Any]], dict[str, Any]] = plan_mdraid_check,
    applier: Callable[[dict[str, Any], str], dict[str, Any]] = apply_mdraid_check,
) -> dict[str, Any]:
    """Start checks only for idle, healthy arrays owned by Echo's mdadm block."""
    _configured, policy = policy_reader()
    if policy.get("enabled") is not True:
        return {"outcome": "disabled", "started": 0, "skipped": 0, "errors": 0}

    arrays = inventory_reader()
    if len(arrays) > MAX_ARRAYS:
        raise OSError("md RAID1 schedule inventory exceeded the safety limit")

    def sort_key(item: object) -> str:
        if not isinstance(item, dict) or not isinstance(item.get("array"), dict):
            return ""
        return str(item["array"].get("name", ""))

    arrays = sorted(arrays, key=sort_key)

    started = 0
    skipped = 0
    errors = 0
    for item in arrays:
        try:
            identity = item["array"]
            if item.get("canStartCheck") is not True:
                skipped += 1
                continue
            desired = {
                "schema": "echo.omv.mdraid-check-desired.v1",
                "name": identity["name"],
                "arrayUuid": identity["uuid"],
                "operation": "start",
            }
            plan = planner(desired)
            result = applier(desired, plan["planId"])
            if result.get("verified") is not True:
                raise OSError("md RAID1 consistency check start was not verified")
            started += 1
        except (OSError, ValueError, KeyError, TypeError):
            errors += 1
    return {
        "outcome": "completed" if errors == 0 else "completedWithErrors",
        "started": started,
        "skipped": skipped,
        "errors": errors,
    }


def main() -> int:
    try:
        result = run_schedule()
    except (OSError, ValueError) as exc:
        print(f"md RAID1 schedule refused to run: {exc}", file=__import__("sys").stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["errors"] == 0 else 2


if __name__ == "__main__":  # pragma: no cover - exercised by systemd
    raise SystemExit(main())
