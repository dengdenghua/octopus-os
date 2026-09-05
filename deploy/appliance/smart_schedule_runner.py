"""Timer-driven SMART short self-tests for enumerated whole disks."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from appliance.native_smart import (
    apply_smart_self_test,
    plan_smart_self_test,
    smart_self_test_status,
)
from appliance.native_storage import block_devices
from appliance.smart_schedule_policy import read_policy

MAX_DISKS = 32


def run_schedule(
    *,
    policy_reader: Callable[[], tuple[bool, dict[str, Any]]] = read_policy,
    inventory_reader: Callable[[], list[dict[str, Any]]] = block_devices,
    status_reader: Callable[[str], dict[str, Any]] = smart_self_test_status,
    planner: Callable[[dict[str, Any]], dict[str, Any]] = plan_smart_self_test,
    applier: Callable[[dict[str, Any], str], dict[str, Any]] = apply_smart_self_test,
) -> dict[str, Any]:
    """Start a non-captive short test on each eligible local whole disk."""
    _configured, policy = policy_reader()
    if policy.get("enabled") is not True:
        return {"outcome": "disabled", "started": 0, "skipped": 0, "errors": 0}

    disks = sorted(
        (
            item
            for item in inventory_reader()
            if item.get("type") == "disk" and isinstance(item.get("devicefile"), str)
        ),
        key=lambda item: item["devicefile"],
    )
    if len(disks) > MAX_DISKS:
        raise OSError("SMART schedule disk inventory exceeded the safety limit")

    started = 0
    skipped = 0
    errors = 0
    for disk in disks:
        devicefile = disk["devicefile"]
        try:
            status = status_reader(devicefile)
            if status.get("supported") is not True or status.get("state") != "idle":
                skipped += 1
                continue
            desired = {
                "schema": "echo.omv.smart-self-test-desired.v1",
                "devicefile": devicefile,
                "test": "short",
            }
            plan = planner(desired)
            result = applier(desired, plan["planId"])
            if result.get("verified") is not True:
                raise OSError("SMART self-test start was not verified")
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
        print(f"SMART schedule refused to run: {exc}", file=__import__("sys").stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["errors"] == 0 else 2


if __name__ == "__main__":  # pragma: no cover - exercised by systemd
    raise SystemExit(main())
