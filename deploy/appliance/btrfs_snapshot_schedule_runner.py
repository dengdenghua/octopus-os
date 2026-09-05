"""Create daily read-only share snapshots and prune scheduler-owned history."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from appliance.btrfs_snapshot_schedule_policy import read_policy
from appliance.native_btrfs_snapshot import (
    apply_automatic_snapshot,
    apply_snapshot_delete,
    list_snapshots,
    plan_automatic_snapshot,
    plan_snapshot_delete,
)


def run_schedule(
    *,
    now: datetime | None = None,
    policy_reader: Callable[[], tuple[bool, dict[str, Any]]] = read_policy,
    inventory_reader: Callable[[str], dict[str, Any]] = list_snapshots,
    create_planner: Callable[[str, str], dict[str, Any]] = plan_automatic_snapshot,
    create_applier: Callable[[str, str, str], dict[str, Any]] = apply_automatic_snapshot,
    delete_planner: Callable[[dict[str, Any]], dict[str, Any]] = plan_snapshot_delete,
    delete_applier: Callable[[dict[str, Any], str], dict[str, Any]] = apply_snapshot_delete,
) -> dict[str, Any]:
    configured, policy = policy_reader()
    shares = policy.get("shares")
    if configured is not True or not isinstance(shares, list) or not shares:
        return {"outcome": "disabled", "created": 0, "pruned": 0, "errors": 0}
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    name = timestamp.strftime("auto-%Y%m%dt%H%M%Sz")
    created = 0
    pruned = 0
    errors = 0
    for rule in shares:
        try:
            reference = rule["sharedFolderRef"]
            keep_latest = rule["keepLatest"]
            before = inventory_reader(reference)
            before_snapshots = before.get("snapshots", [])
            if not isinstance(before_snapshots, list):
                raise OSError("scheduled Btrfs snapshot inventory is invalid")
            before_automatic = sorted(
                (
                    item
                    for item in before_snapshots
                    if isinstance(item, dict)
                    and item.get("kind") == "automatic"
                    and item.get("locked") is not True
                ),
                key=lambda item: item.get("name", ""),
            )

            def prune(snapshot: dict[str, Any], shared_folder_ref: str) -> None:
                nonlocal pruned
                desired = {
                    "schema": "echo.omv.btrfs-snapshot-delete-desired.v1",
                    "sharedFolderRef": shared_folder_ref,
                    "snapshotId": snapshot["snapshotId"],
                }
                delete_plan = delete_planner(desired)
                delete_result = delete_applier(desired, delete_plan["planId"])
                if (
                    delete_result.get("verified") is not True
                    or delete_result.get("snapshotDeleted") is not True
                ):
                    raise OSError("scheduled Btrfs snapshot pruning was not verified")
                pruned += 1

            # Make one slot before creation at the hard 256-snapshot limit.
            # Only a scheduler-owned snapshot may be sacrificed, and it is
            # always the oldest. Manual snapshots remain untouchable.
            if len(before_snapshots) >= 256:
                if not before_automatic:
                    raise OSError("manual snapshots occupy the complete snapshot limit")
                prune(before_automatic[0], reference)

            create_plan = create_planner(reference, name)
            create_result = create_applier(reference, name, create_plan["planId"])
            if create_result.get("verified") is not True:
                raise OSError("scheduled Btrfs snapshot was not verified")
            if create_plan.get("operation") == "create":
                snapshot = create_result.get("snapshot")
                if not isinstance(snapshot, dict) or snapshot.get("kind") != "automatic":
                    raise OSError("scheduled Btrfs snapshot identity is invalid")
                created += 1
            inventory = inventory_reader(reference)
            automatic = sorted(
                (
                    item
                    for item in inventory.get("snapshots", [])
                    if isinstance(item, dict)
                    and item.get("kind") == "automatic"
                    and item.get("locked") is not True
                ),
                key=lambda item: item.get("name", ""),
            )
            for snapshot in automatic[: max(0, len(automatic) - keep_latest)]:
                prune(snapshot, reference)
        except (OSError, ValueError, KeyError, TypeError):
            errors += 1
    return {
        "outcome": "completed" if errors == 0 else "completedWithErrors",
        "created": created,
        "pruned": pruned,
        "errors": errors,
    }


def main() -> int:
    try:
        result = run_schedule()
    except (OSError, ValueError) as exc:
        print(f"Btrfs snapshot schedule refused to run: {exc}", file=__import__("sys").stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["errors"] == 0 else 2


if __name__ == "__main__":  # pragma: no cover - exercised by systemd
    raise SystemExit(main())
