#!/usr/bin/env python3
"""Validate the inert interrupted-task fixture used by the raw-image smoke."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

MAX_BYTES = 1024 * 1024
TASK_ID = "2dc98bd4-caa4-4f4b-a6db-218689039473"
FIXTURE_MARKER = "echo-os-cold-boot-recovery-v1"
DISABLED_CAPABILITY_GROUPS = {
    "builtin",
    "web",
    "browser",
    "computer",
    "fs_write",
    "git",
    "shell",
    "memory",
}
FORBIDDEN_METADATA_KEYS = {
    "restart",
    "restart_at",
    "restart_events",
    "takeover_at",
    "takeover_events",
    "resume_execution",
    "resume_request_id",
    "resume_turn_id",
}


def _read(path: Path) -> tuple[bytes, dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"fixture is not a regular non-symlink file: {path}")
    raw = path.read_bytes()
    if not 1 <= len(raw) <= MAX_BYTES:
        raise ValueError(f"fixture size is unsafe: {path}")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"fixture is not valid UTF-8 JSON: {path}") from error
    if not isinstance(value, dict):
        raise ValueError("fixture root must be an object")
    return raw, value


def validate(path: Path) -> dict[str, Any]:
    _, payload = _read(path)
    if payload.get("schema") != "echo.task_supervisor.v1":
        raise ValueError("fixture has the wrong task-supervisor schema")
    if payload.get("version") != 1 or payload.get("leaseCounter") != 1:
        raise ValueError("fixture version or lease counter is invalid")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], dict):
        raise ValueError("fixture must contain exactly one task object")

    task = tasks[0]
    if task.get("task_id") != TASK_ID:
        raise ValueError("fixture task identity is invalid")
    if task.get("kind") != "realtime_objective" or task.get("status") != "running":
        raise ValueError("fixture must model one interrupted running objective")
    if task.get("owner_id") != "local:admin" or not str(task.get("thread_id") or ""):
        raise ValueError("fixture task owner or thread identity is invalid")
    if task.get("latest_checkpoint_id") != 88:
        raise ValueError("fixture must retain its pre-power-loss checkpoint")
    if task.get("completed_at") is not None or task.get("terminal_reason") not in (None, ""):
        raise ValueError("fixture must remain non-terminal")

    lease = task.get("lease")
    if not isinstance(lease, dict):
        raise ValueError("fixture task must retain its previous lease")
    expires_at = lease.get("expires_at")
    if (
        lease.get("holder_id") != "worker-before-power-loss"
        or lease.get("token") != 1
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, (int, float))
        or float(expires_at) <= 0
        or float(expires_at) >= time.time()
    ):
        raise ValueError("fixture lease is not an expired pre-power-loss lease")

    capabilities = task.get("capabilities")
    groups = capabilities.get("groups") if isinstance(capabilities, dict) else None
    if not isinstance(groups, dict) or set(groups) != DISABLED_CAPABILITY_GROUPS:
        raise ValueError("fixture capability groups are incomplete")
    if any(value is not False for value in groups.values()):
        raise ValueError("fixture must not grant executable capability groups")
    if capabilities.get("workspace_paths") != []:
        raise ValueError("fixture must not grant workspace access")

    metadata = task.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("fixture") != FIXTURE_MARKER:
        raise ValueError("fixture marker is missing")
    forbidden = sorted(FORBIDDEN_METADATA_KEYS.intersection(metadata))
    if forbidden:
        raise ValueError(f"fixture already contains recovery mutations: {forbidden}")
    return task


def verify_unchanged(expected_path: Path, observed_path: Path) -> dict[str, Any]:
    expected_raw, _ = _read(expected_path)
    observed_raw, _ = _read(observed_path)
    if observed_raw != expected_raw:
        raise ValueError("persisted task store changed during cold-boot discovery")
    return validate(observed_path)


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "verify":
        task = validate(Path(argv[2]))
    elif len(argv) == 4 and argv[1] == "unchanged":
        task = verify_unchanged(Path(argv[2]), Path(argv[3]))
    else:
        print(
            f"usage: {argv[0]} verify FIXTURE | {argv[0]} unchanged EXPECTED OBSERVED",
            file=sys.stderr,
        )
        return 2
    print(
        "ECHO_AGENT_RECOVERY_FIXTURE_OK "
        f"task={task['task_id']} checkpoint={task['latest_checkpoint_id']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except (OSError, ValueError) as error:
        print(f"Echo Agent recovery fixture rejected: {error}", file=sys.stderr)
        raise SystemExit(1) from error
