"""Timer-driven scrubs for healthy Echo-managed Btrfs RAID1 filesystems."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from appliance.btrfs_scrub_schedule_policy import read_policy
from appliance.native_btrfs_scrub import (
    apply_btrfs_scrub,
    btrfs_scrub_maintenance,
    plan_btrfs_scrub,
)

MAX_FILESYSTEMS = 16


def _scheduled_plan(desired: dict[str, Any]) -> dict[str, Any]:
    return plan_btrfs_scrub(desired, wait_for_completion=True)


def _scheduled_apply(desired: dict[str, Any], plan_id: str) -> dict[str, Any]:
    return apply_btrfs_scrub(desired, plan_id, wait_for_completion=True)


def run_schedule(
    *,
    policy_reader: Callable[[], tuple[bool, dict[str, Any]]] = read_policy,
    inventory_reader: Callable[[], list[dict[str, Any]]] = btrfs_scrub_maintenance,
    planner: Callable[[dict[str, Any]], dict[str, Any]] = _scheduled_plan,
    applier: Callable[[dict[str, Any], str], dict[str, Any]] = _scheduled_apply,
    error_reporter: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Start scrub only for complete, idle, error-free Echo Btrfs RAID1."""
    configured, policy = policy_reader()
    if configured is not True or policy.get("enabled") is not True:
        return {"outcome": "disabled", "started": 0, "skipped": 0, "errors": 0}
    filesystems = inventory_reader()
    if len(filesystems) > MAX_FILESYSTEMS:
        raise OSError("Btrfs scrub schedule inventory exceeded the safety limit")

    def sort_key(item: object) -> str:
        if not isinstance(item, dict) or not isinstance(item.get("filesystem"), dict):
            return ""
        return str(item["filesystem"].get("uuid", ""))

    started = 0
    skipped = 0
    errors = 0
    for item in sorted(filesystems, key=sort_key):
        try:
            filesystem = item["filesystem"]
            eligible = (
                item.get("canStartScrub") is True
                and filesystem.get("status") == "healthy"
                and filesystem.get("readOnly") is False
                and filesystem.get("totalDevices") == 2
                and filesystem.get("activeDevices") == 2
                and filesystem.get("missingDevices") == 0
                and filesystem.get("dataProfile") == "raid1"
                and filesystem.get("metadataProfile") == "raid1"
                and filesystem.get("deviceErrorCount") == 0
            )
            if not eligible:
                skipped += 1
                continue
            desired = {
                "schema": "echo.omv.btrfs-scrub-desired.v1",
                "filesystemUuid": filesystem["uuid"],
                "operation": "start",
            }
            plan = planner(desired)
            result = applier(desired, plan["planId"])
            if (
                result.get("verified") is not True
                or result.get("maintenanceState") not in {"scrubbing", "completed"}
            ):
                raise OSError("Btrfs scrub start was not verified")
            started += 1
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors += 1
            if error_reporter is not None:
                detail = " ".join(str(exc).split())[:256]
                error_reporter(f"{type(exc).__name__}: {detail}")
    return {
        "outcome": "completed" if errors == 0 else "completedWithErrors",
        "started": started,
        "skipped": skipped,
        "errors": errors,
    }


def main() -> int:
    try:
        result = run_schedule(
            error_reporter=lambda detail: print(
                f"Btrfs scrub schedule skipped one candidate: {detail}",
                file=__import__("sys").stderr,
            )
        )
    except (OSError, ValueError) as exc:
        print(f"Btrfs scrub schedule refused to run: {exc}", file=__import__("sys").stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["errors"] == 0 else 2


if __name__ == "__main__":  # pragma: no cover - exercised by systemd
    raise SystemExit(main())
