"""Bounded SMART self-test inventory and approval-bound start operation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from appliance.omv_protocol import (
    SMART_SELF_TEST_PLAN_SCHEMA,
    validate_smart_self_test_desired,
)

_MAX_OUTPUT_BYTES = 1024 * 1024
_COMMAND_FAILURE_BITS = 0b00000111


class SmartSelfTestError(ValueError):
    """A SMART self-test request is unsafe or cannot be proven."""


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _run_smartctl(
    devicefile: str,
    *arguments: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[dict[str, Any], int]:
    try:
        completed = runner(
            ["smartctl", "-j", *arguments, devicefile],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30.0,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise OSError("SMART self-test query could not be completed") from exc
    output = completed.stdout or ""
    if len(output.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        raise OSError("SMART self-test response exceeded the safety limit")
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise OSError("SMART self-test response was not JSON") from exc
    if not isinstance(value, dict):
        raise OSError("SMART self-test response was malformed")
    return value, completed.returncode


def _integer(value: Any, *, minimum: int = 0, maximum: int = 100) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if minimum <= value <= maximum else None


def _status(payload: Mapping[str, Any]) -> dict[str, Any]:
    ata_data = payload.get("ata_smart_data")
    ata_self_test = ata_data.get("self_test") if isinstance(ata_data, dict) else None
    ata_status = ata_self_test.get("status") if isinstance(ata_self_test, dict) else None
    if isinstance(ata_status, dict):
        value = _integer(ata_status.get("value"), maximum=255)
        remaining = _integer(ata_status.get("remaining_percent"))
        text = ata_status.get("string")
        text = text.casefold() if isinstance(text, str) else ""
        in_progress = value is not None and value >> 4 == 0xF
        if not in_progress and "in progress" in text:
            in_progress = True
        return {
            "supported": True,
            "state": "inProgress" if in_progress else "idle",
            "kind": "unknown" if in_progress else None,
            "progressPercent": (100 - remaining) if in_progress and remaining is not None else None,
        }

    nvme_log = payload.get("nvme_self_test_log")
    if isinstance(nvme_log, dict):
        operation = nvme_log.get("current_self_test_operation")
        operation_value = (
            _integer(operation.get("value"), maximum=15) if isinstance(operation, dict) else None
        )
        if operation_value is None:
            return {
                "supported": False,
                "state": "unknown",
                "kind": None,
                "progressPercent": None,
            }
        progress = _integer(nvme_log.get("current_self_test_completion_percent"))
        return {
            "supported": True,
            "state": "idle" if operation_value == 0 else "inProgress",
            "kind": "short"
            if operation_value == 1
            else "long"
            if operation_value == 2
            else "unknown"
            if operation_value
            else None,
            "progressPercent": progress if operation_value else None,
        }

    return {
        "supported": False,
        "state": "unknown",
        "kind": None,
        "progressPercent": None,
    }


def _inventory_device(
    devicefile: str,
    *,
    inventory_reader: Callable[[], list[dict[str, Any]]],
) -> tuple[dict[str, Any], str]:
    matches = [item for item in inventory_reader() if item.get("devicefile") == devicefile]
    if len(matches) != 1 or matches[0].get("type") != "disk":
        raise SmartSelfTestError("SMART self-test target is not an enumerated whole disk")
    device = matches[0]
    identity = {
        "devicefile": devicefile,
        "sizeBytes": device.get("sizeBytes"),
        "model": device.get("model"),
        "serial": device.get("serial"),
    }
    return device, hashlib.sha256(_canonical(identity)).hexdigest()


def _default_inventory() -> list[dict[str, Any]]:
    from appliance.native_storage import block_devices

    return block_devices()


def smart_self_test_status(
    devicefile: str,
    *,
    inventory_reader: Callable[[], list[dict[str, Any]]] = _default_inventory,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    desired = validate_smart_self_test_desired(
        {
            "schema": "echo.omv.smart-self-test-desired.v1",
            "devicefile": devicefile,
            "test": "short",
        }
    )
    device, identity_hash = _inventory_device(
        desired["devicefile"], inventory_reader=inventory_reader
    )
    payload, returncode = _run_smartctl(devicefile, "-c", "-l", "selftest", runner=runner)
    if returncode < 0 or returncode & _COMMAND_FAILURE_BITS:
        raise OSError("SMART self-test status command failed")
    return {
        "devicefile": devicefile,
        "model": device.get("model"),
        "identityHash": identity_hash,
        **_status(payload),
        "readOnly": True,
        "source": "smartctl",
    }


def plan_smart_self_test(
    desired_state: Mapping[str, Any],
    *,
    inventory_reader: Callable[[], list[dict[str, Any]]] = _default_inventory,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    desired = validate_smart_self_test_desired(dict(desired_state))
    before = smart_self_test_status(
        desired["devicefile"], inventory_reader=inventory_reader, runner=runner
    )
    if not before["supported"] or before["state"] == "unknown":
        raise SmartSelfTestError("the selected disk does not expose a supported self-test status")
    if before["state"] == "inProgress":
        raise SmartSelfTestError("the selected disk already has a self-test in progress")
    binding = {
        "schema": SMART_SELF_TEST_PLAN_SCHEMA,
        "operation": "start",
        "desired": desired,
        "identityHash": before["identityHash"],
        "before": {
            "state": before["state"],
            "kind": before["kind"],
            "progressPercent": before["progressPercent"],
        },
    }
    return {
        **binding,
        "planId": hashlib.sha256(_canonical(binding)).hexdigest(),
        "requiresApproval": True,
        "safety": {
            "target": "enumeratedWholeDisk",
            "allowedTests": ["short", "long"],
            "activeTest": "mustBeAbsent",
            "captive": False,
            "abort": False,
        },
    }


def apply_smart_self_test(
    desired_state: Mapping[str, Any],
    plan_id: str,
    *,
    inventory_reader: Callable[[], list[dict[str, Any]]] = _default_inventory,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if shutil.which("smartctl") is None:
        raise OSError("smartctl is unavailable")
    plan = plan_smart_self_test(desired_state, inventory_reader=inventory_reader, runner=runner)
    if plan["planId"] != plan_id:
        raise SmartSelfTestError("SMART self-test plan is stale; preview again")
    desired = plan["desired"]
    _payload, returncode = _run_smartctl(
        desired["devicefile"], "-t", desired["test"], runner=runner
    )
    if returncode < 0 or returncode & _COMMAND_FAILURE_BITS:
        raise OSError("SMART self-test start command failed")
    after = smart_self_test_status(
        desired["devicefile"], inventory_reader=inventory_reader, runner=runner
    )
    if after["identityHash"] != plan["identityHash"]:
        raise OSError("SMART self-test target changed after the command")
    if after["state"] != "inProgress":
        raise OSError("SMART self-test start could not be verified")
    return {
        **plan,
        "applied": True,
        "verified": True,
        "selfTest": after,
    }


__all__ = [
    "SmartSelfTestError",
    "apply_smart_self_test",
    "plan_smart_self_test",
    "smart_self_test_status",
]
